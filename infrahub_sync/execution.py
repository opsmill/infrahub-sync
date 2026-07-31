"""Shared typed execution surface for the diff/plan and serial-sync lifecycles.

Exactly three callers use this module: the CLI ``diff`` command, the serial
branch of the CLI ``sync`` command, and the packaged Prefect flow. It imports no
Prefect symbol and stays importable in a base install.

Failure semantics: :func:`execute_run` raises ORIGINAL exception types and never
wraps them; :func:`run_remote_request` is the one sanitize-and-wrap boundary that
converts failures into :class:`RunValidationError` / :class:`RunExecutionError`.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Callable  # runtime use: the PotendaFactory alias below
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from timeit import default_timer as timer
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Literal

import pydantic
import yaml
from filelock import Timeout

from infrahub_sync import SyncConfig, SyncInstance
from infrahub_sync.cache.locks import pipeline_lock
from infrahub_sync.cache.sidecars import RunFile
from infrahub_sync.utils import get_potenda_from_instance

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence
    from contextlib import AbstractContextManager
    from typing import NoReturn

    from infrahub_sync.potenda import Potenda

logger = logging.getLogger(__name__)

Operation = Literal["plan", "sync"]
Status = Literal["planned", "applied", "no-change"]
ActionKey = Literal["create", "update", "delete"]

# Factory signature identical to utils.get_potenda_from_instance — injected so the
# CLI can pass its own thin wrapper over the module global (which keeps existing
# patches on `infrahub_sync.cli.get_potenda_from_instance` effective).
PotendaFactory = Callable[..., "Potenda"]

OPERATIONS: tuple[Operation, ...] = ("plan", "sync")
ACTION_KEYS: tuple[ActionKey, ...] = ("create", "update", "delete")

REDACTED = "***"
# Shortest collected value that is redacted. A short value — the `1` of a
# `SKIP_TOKEN=1` feature flag, say — would turn redaction into a substring
# shredder over every message ("within 6***.0 seconds"), and no real credential
# is that short, so short values are dropped rather than applied.
MIN_SECRET_LENGTH = 6

# Value-based secret collection, environment half. A variable's NAME is
# credential-shaped when it CONTAINS one of the substrings, ENDS WITH one of the
# suffixes, or equals one of the exact names. Substring matching is what catches
# the bare `TOKEN`/`PASSWORD` names the genericrestapi adapter reads by default
# (adapters/genericrestapi.py:72,90) and `AWS_SECRET_ACCESS_KEY`; the two
# suffixes carry the names — `*_KEY`, `*_AUTH` — whose bare substrings would
# match unrelated variables (`KEYCHAIN`, `SSH_AUTH_SOCK`). Adapter credentials
# such as NETBOX_TOKEN reach the runner through the environment, outside the
# resolved configuration's settings.
SECRET_ENV_NAMES = ("INFRAHUB_API_TOKEN", "KEY", "AUTH")
SECRET_ENV_NAME_SUBSTRINGS = ("TOKEN", "PASSWORD", "PASSWD", "SECRET", "CREDENTIAL", "APIKEY")
SECRET_ENV_NAME_SUFFIXES = ("_KEY", "_AUTH")

# Settings half. Every `settings` mapping of the resolved configuration — source,
# destination, AND store — is walked RECURSIVELY, and a key is credential-shaped
# when its name CONTAINS one of these substrings: `api_key`, ipfabric's `auth`, a
# nested `headers.authorization`, and `store.settings.password` all match. Values
# are `str()`-ed, so a non-string credential is collected too.
SECRET_SETTING_KEY_SUBSTRINGS = ("token", "password", "secret", "key", "auth", "credential")
# A `<something>_env_vars` key names environment variables the adapter reads
# INSTEAD of an inline value (adapters/genericrestapi.py:59-92,
# adapters/peeringmanager.py:31-34). The names are not secrets; the values they
# point at are, when the key itself is credential-shaped.
ENV_VAR_LIST_SUFFIX = "_env_vars"
# Userinfo of a URL-shaped value: `scheme://user:password@host/...` hides a
# credential in plain sight, typically next to an already-redacted sibling token.
_URL_USERINFO = re.compile(r"://(?P<userinfo>[^/\s@]+)@")

# Runner-environment variables each adapter reads its credentials from, keyed by
# the adapter's normalized name. Named here, at the remote boundary, so the
# adapter modules stay untouched and the CLI's own wording is unchanged. An
# adapter absent from this map gets NO hint: naming the wrong system's variables
# is worse than naming none.
INFRAHUB_CREDENTIAL_ENV_VARS = ("INFRAHUB_ADDRESS", "INFRAHUB_API_TOKEN")
ADAPTER_CREDENTIAL_ENV_VARS: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "infrahub": INFRAHUB_CREDENTIAL_ENV_VARS,
        "netbox": ("NETBOX_ADDRESS", "NETBOX_TOKEN"),
        "nautobot": ("NAUTOBOT_ADDRESS", "NAUTOBOT_TOKEN"),
        "prometheus": ("PROM_URL", "PROM_TOKEN"),
        "ipfabricsync": ("IPF_URL", "IPF_TOKEN"),
        "aci": ("CISCO_APIC_URL", "CISCO_APIC_USERNAME", "CISCO_APIC_PASSWORD"),
        "peeringmanager": ("PEERING_MANAGER_ADDRESS", "PEERING_MANAGER_TOKEN"),
    }
)
_MISSING_CREDENTIAL_MARKERS = (
    "must be specified",
    "must be set",
    "no token",
    "missing credential",
    "requires a valid api token",
    "requires both username and password",
)
# `utils.get_potenda_from_instance` wraps EVERY adapter construction failure with
# this prefix (utils.py:219,233), and it is the only place the FAILING adapter is
# named — the adapter's own message never names itself.
_ADAPTER_INIT_PREFIX = re.compile(r"^Error initializing (?P<adapter>.+?)Adapter:\s*(?P<detail>.*)$", re.DOTALL)


class RunValidationError(Exception):
    """Input-boundary refusal: unconfirmed write, or an unresolvable/invalid configuration."""


class RunExecutionError(Exception):
    """Adapter or engine failure after validation passed."""


class _FactoryValueError(Exception):
    """Internal marker: the ENGINE FACTORY raised `ValueError`, not a later stage.

    `potenda` wraps every LOAD-stage failure into `ValueError` as well
    (`potenda/__init__.py:234-250`), and `RunResult.__post_init__` raises it for an
    invariant violation, so the exception type alone cannot tell the stages apart.
    `run_remote_request`'s own wrapper factory (mirroring `cli._cli_potenda_factory`)
    raises this marker with the original `ValueError` as its `__cause__`, so only
    factory-stage failures get the "Failed to initialize the Sync Instance" wording.
    """


# --------------------------------------------------------------------------- #
# Secret redaction
# --------------------------------------------------------------------------- #


def _is_secret_env_name(name: str) -> bool:
    """Return whether an environment variable's NAME is credential-shaped."""
    upper = name.upper()
    return (
        upper in SECRET_ENV_NAMES
        or upper.endswith(SECRET_ENV_NAME_SUFFIXES)
        or any(part in upper for part in SECRET_ENV_NAME_SUBSTRINGS)
    )


def _add_secret(value: object, values: set[str]) -> None:
    """Add one candidate value, `str()`-ing non-strings and dropping short ones."""
    text = value if isinstance(value, str) else str(value)
    if len(text) >= MIN_SECRET_LENGTH:
        values.add(text)


def _add_url_userinfo(text: str, values: set[str]) -> None:
    """Add the userinfo of every URL-shaped value (`http://admin:pw@host/api`)."""
    for match in _URL_USERINFO.finditer(text):
        userinfo = match.group("userinfo")
        _add_secret(userinfo, values)
        _, separator, password = userinfo.partition(":")
        if separator:
            _add_secret(password, values)


def _collect_from_settings(
    node: Any,
    values: set[str],
    *,
    secret_context: bool,
    environ: Mapping[str, str],
) -> None:
    """Walk one `settings` subtree, collecting every credential-shaped value.

    `secret_context` is inherited: once a credential-shaped key is entered, every
    value beneath it counts (a `credentials:` block of plain-named entries, say).
    """
    if isinstance(node, dict):
        for key, value in node.items():
            lowered = str(key).lower()
            is_secret_key = secret_context or any(part in lowered for part in SECRET_SETTING_KEY_SUBSTRINGS)
            if lowered.endswith(ENV_VAR_LIST_SUFFIX):
                # The list holds NAMES, never values: collect what they point at,
                # and never the names themselves (`ADDRESS` as a redaction target
                # would shred unrelated messages).
                if is_secret_key and isinstance(value, (list, tuple)):
                    for env_name in value:
                        _add_secret(environ.get(str(env_name)) or "", values)
                continue
            _collect_from_settings(value, values, secret_context=is_secret_key, environ=environ)
        return
    if isinstance(node, (list, tuple, set)):
        for item in node:
            _collect_from_settings(item, values, secret_context=secret_context, environ=environ)
        return
    if node is None or isinstance(node, bool):
        # `str(None)`/`str(False)` are ordinary words, never credentials.
        return
    if secret_context:
        _add_secret(node, values)
    if isinstance(node, str):
        _add_url_userinfo(node, values)


def collect_secret_values(
    sync_instance: SyncInstance | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> tuple[str, ...]:
    """Collect the secret VALUES that must never appear in a surface message.

    Sources:

    - the runner environment — every variable whose NAME is credential-shaped
      (:func:`_is_secret_env_name`);
    - the resolved configuration's `source`, `destination`, AND `store` settings,
      walked recursively: the value of every key whose name contains one of
      :data:`SECRET_SETTING_KEY_SUBSTRINGS` (nested `headers`/`params` included,
      non-string values `str()`-ed), the userinfo of every URL-shaped value, and
      the environment values named by the configuration's own `*_env_vars` lists.

    Values shorter than :data:`MIN_SECRET_LENGTH` are dropped. Longest values
    first, so overlapping secrets redact completely.
    """
    env = os.environ if environ is None else environ
    values: set[str] = set()
    for name, value in env.items():
        if value and _is_secret_env_name(name):
            _add_secret(value, values)
    if sync_instance is not None:
        settings_blocks = [sync_instance.source.settings, sync_instance.destination.settings]
        if sync_instance.store is not None:
            settings_blocks.append(sync_instance.store.settings)
        for settings in settings_blocks:
            _collect_from_settings(settings or {}, values, secret_context=False, environ=env)
    return tuple(sorted(values, key=lambda value: (-len(value), value)))


def redact(message: str, secrets: Sequence[str]) -> str:
    """Replace every occurrence of a collected secret value with ``***``."""
    for secret in secrets:
        message = message.replace(secret, REDACTED)
    return message


def sanitize_exception_chain(exc: BaseException, secrets: Sequence[str]) -> Exception:
    """Rebuild `exc`'s whole cause chain as redacted, context-suppressed copies.

    Prefect logs a failed flow's exception WITH its traceback, and a traceback
    renders every ``__cause__``/``__context__`` message — so redacting only the
    wrapper message is not enough. Each link becomes a ``RuntimeError`` carrying
    the original type name and the redacted original text, with
    ``__suppress_context__`` set so no unredacted original can be reached from
    the returned exception.
    """
    chain: list[BaseException] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        chain.append(current)
        current = current.__cause__ or (None if current.__suppress_context__ else current.__context__)

    sanitized: Exception = RuntimeError("unknown cause")
    for original in reversed(chain):
        rebuilt = RuntimeError(f"{type(original).__name__}: {redact(str(original), secrets)}")
        if original is not chain[-1]:
            rebuilt.__cause__ = sanitized
        rebuilt.__suppress_context__ = True
        sanitized = rebuilt
    return sanitized


# --------------------------------------------------------------------------- #
# Result
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class RunResult:
    """Immutable success result — exactly these seven fields."""

    sync_name: str
    operation: Operation
    run_id: str
    status: Status
    changed: bool
    summary: Mapping[ActionKey, int]  # read-only after validation — see __post_init__
    artifact_path: str

    def __post_init__(self) -> None:
        """Enforce the cross-field invariants and freeze `summary`.

        `frozen=True` prevents only field rebinding: a plain dict passed in as
        `summary` could still be mutated after validation, silently breaking the
        invariants below. So the validated mapping is re-bound, through
        `object.__setattr__`, to a `types.MappingProxyType` copy.
        """
        if set(self.summary) != set(ACTION_KEYS):
            msg = f"summary must carry exactly the keys {ACTION_KEYS!r}, got {sorted(self.summary)!r}"
            raise ValueError(msg)
        total = sum(self.summary.values())
        if self.changed is not (self.status != "no-change") or self.changed is not (total > 0):
            msg = (
                f"inconsistent result: changed={self.changed!r}, status={self.status!r}, "
                f"summary total={total!r} — changed, a non-'no-change' status, and a "
                "non-zero summary total must agree"
            )
            raise ValueError(msg)
        if self.status == "planned" and self.operation != "plan":
            msg = f"status='planned' requires operation='plan', got operation={self.operation!r}"
            raise ValueError(msg)
        if self.status == "applied" and self.operation != "sync":
            msg = f"status='applied' requires operation='sync', got operation={self.operation!r}"
            raise ValueError(msg)
        if self.run_id != Path(self.artifact_path).name:
            msg = f"run_id={self.run_id!r} must equal the final segment of artifact_path={self.artifact_path!r}"
            raise ValueError(msg)
        object.__setattr__(self, "summary", MappingProxyType(dict(self.summary)))


# --------------------------------------------------------------------------- #
# Configuration resolution
# --------------------------------------------------------------------------- #


def _load_config_name(config_file: Path) -> tuple[bool, Any]:
    """Return `(determinable, name)` for one discovered `config.yml`.

    The name is the top-level `name` key of the parsed mapping. It is
    UNDETERMINABLE when the read raises `OSError` or `UnicodeDecodeError` (a
    non-UTF-8 file — `read_text(encoding="utf-8")` raises it, and it is a
    `ValueError`, so neither of the other two clauses would catch it), the parse
    raises `yaml.YAMLError`, or the loaded document is not a mapping.
    """
    try:
        data = yaml.safe_load(config_file.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        return False, None
    if not isinstance(data, dict):
        return False, None
    return True, data


def resolve_sync_instance(sync_name: str, *, directory: str) -> SyncInstance:
    """Resolve `sync_name` by exact string equality within `directory`.

    A tolerant per-file walk: the same recursive `**/config.yml` discovery glob
    and exact-`name` match the CLI lookup uses, but every discovered file is read
    and validated individually rather than through `utils.get_all_sync`'s eager
    validate-everything pass — one broken neighbor must not block resolution of
    every other name, and its parse error must not leak that file's contents.

    `sync_name` is never used to construct a filesystem path, so traversal-shaped
    and command-like values fail exactly like unknown names do.

    Raises:
        RunValidationError: no configuration with that logical name exists under
            `directory` (the message names the logical name and, when
            applicable, the COUNT of unreadable files), or the matched
            `config.yml` failed `SyncConfig` validation (the message names the
            logical name and the file path, never file contents).
    """
    secrets = collect_secret_values()
    undeterminable = 0
    for config_file in sorted(Path(directory).glob("**/config.yml")):
        determinable, data = _load_config_name(config_file)
        if not determinable:
            # Rule (c): no determinable name, so this file can never be the
            # matched one. Path only — never contents, never the parse detail.
            undeterminable += 1
            logger.warning("Skipping sync configuration %s: it could not be read", config_file)
            continue
        if data.get("name") != sync_name:
            # Rule (b): determinable and different — the ordinary case.
            logger.debug("Sync configuration %s does not match the requested name", config_file)
            continue
        # Rule (a): the matched file.
        try:
            SyncConfig(**data)
            return SyncInstance(**data, directory=str(config_file.parent))
        except (pydantic.ValidationError, TypeError, ValueError):
            # The parse detail is deliberately NOT chained: pydantic's
            # `input_value=...` echo can carry this file's contents, including
            # inline secrets the value-based redactor never collected.
            msg = redact(
                f"Sync configuration {sync_name!r} at {config_file} is not a valid configuration",
                secrets,
            )
            raise RunValidationError(msg) from None

    if undeterminable:
        msg = (
            f"No sync configuration named {sync_name!r} was found in the configured directory; "
            f"{undeterminable} file(s) could not be read"
        )
    else:
        msg = f"No sync configuration named {sync_name!r} was found in the configured directory"
    raise RunValidationError(redact(msg, secrets))


# --------------------------------------------------------------------------- #
# Lifecycles
# --------------------------------------------------------------------------- #


def _summarize_rows(rows: Iterable[Mapping[str, Any]]) -> dict[ActionKey, int]:
    """Count plan rows per action, zero-filling every action key."""
    counts: dict[ActionKey, int] = dict.fromkeys(ACTION_KEYS, 0)
    for row in rows:
        action = row.get("action")
        if action in counts:
            counts[action] += 1
    return counts


def _run_plan_lifecycle(*, ptd: Potenda, run_file: RunFile) -> list[dict[str, str]]:
    """Reproduce the CLI `diff` lifecycle (cli.py:149-159) and return the plan rows."""
    ptd.load_both_sides()
    mydiff = ptd.diff()
    ptd.write_plan(mydiff)
    logger.info("\n%s", mydiff.str())
    run_file.status = "dry-run"
    run_file.summary = {"resources": len(ptd.top_level)}
    return list(ptd._diff_to_rows(mydiff))


def _run_sync_lifecycle(
    *,
    ptd: Potenda,
    run_file: RunFile,
    print_diff: bool,
    allow_rowcount_drop: bool,
    serial_load_error: Callable[[ValueError], NoReturn] | None,
) -> list[dict[str, str]]:
    """Reproduce the CLI serial `sync` lifecycle (cli.py:263-284) and return the plan rows."""
    try:
        ptd.load_both_sides()
    except ValueError as exc:
        run_file.status = "failed"
        run_file.save()
        if serial_load_error is not None:
            # CLI-only seam: the unprefixed abort fires at the site, exactly as
            # cli.py:265-268 does today.
            serial_load_error(exc)
        raise
    ptd.check_rowcount_guardrail(allow_drop=allow_rowcount_drop)
    mydiff = ptd.diff()
    ptd.write_plan(mydiff)
    if mydiff.has_diffs():
        if print_diff:
            logger.info("\n%s", mydiff.str())
        start_synctime = timer()
        ptd.sync(diff=mydiff)
        end_synctime = timer()
        logger.info("Sync: Completed in %s sec", end_synctime - start_synctime)
    else:
        logger.info("No difference found. Nothing to sync")
    ptd.persist_baseline_counts()
    run_file.summary = {"resources": len(ptd.top_level), "mode": "serial"}
    run_file.status = "applied"
    return list(ptd._diff_to_rows(mydiff))


def _build_result(
    *,
    sync_instance: SyncInstance,
    operation: Operation,
    ptd: Potenda,
    rows: Iterable[Mapping[str, Any]],
) -> RunResult:
    """Derive the result from the in-memory plan rows — never by re-reading plan.parquet."""
    summary = _summarize_rows(rows)
    changed = sum(summary.values()) > 0
    if not changed:
        status: Status = "no-change"
    elif operation == "plan":
        status = "planned"
    else:
        status = "applied"
    return RunResult(
        sync_name=sync_instance.name,
        operation=operation,
        run_id=str(ptd.run_id),
        status=status,
        changed=changed,
        summary=summary,
        artifact_path=str(ptd.run_dir),
    )


def execute_run(
    sync_instance: SyncInstance,
    *,
    operation: Operation,
    confirm_writes: bool = False,
    branch: str | None = None,
    # Engine options — defaults are the CLI option defaults at commit 9edc1bc.
    show_progress: bool | None = None,
    verbosity: int = logging.INFO,
    run_id: str | None = None,
    concurrent_load: bool = True,
    full_extract: bool = True,
    allow_rowcount_drop: bool = False,
    continue_on_error: bool = False,
    print_diff: bool = True,
    potenda_factory: PotendaFactory | None = None,
    # Private seams — not part of the remote contract; run_remote_request never sets
    # _serial_load_error or _lock_already_held.
    _lock_timeout: float = 60.0,
    _serial_load_error: Callable[[ValueError], NoReturn] | None = None,
    _lock_already_held: bool = False,
) -> RunResult:
    """Run one plan (== the diff lifecycle) or serial sync against a resolved instance.

    Raises the ORIGINAL exception types of every engine failure — no
    sanitize-and-wrap happens here. The only surface-typed raise is the
    validation refusal below, which is unreachable from the CLI callers.

    Raises:
        RunValidationError: unknown `operation`, or `operation="sync"` without
            `confirm_writes` (refused BEFORE any adapter is constructed).
    """
    if operation not in OPERATIONS:
        msg = f"Unsupported operation {operation!r} — expected one of {OPERATIONS!r}"
        raise RunValidationError(msg)
    if operation == "sync" and not confirm_writes:
        msg = "confirm_writes=true is required to run operation=sync"
        raise RunValidationError(msg)

    # A second same-process FileLock on the same path does not re-enter: it
    # blocks for the full timeout and then raises filelock.Timeout. The CLI
    # serial-sync caller therefore keeps its own outer `with pipeline_lock(...)`
    # (the parallel branch it shares a command body with still needs it) and
    # passes _lock_already_held=True so this function does not self-deadlock.
    lock_scope: AbstractContextManager[None] = (
        nullcontext() if _lock_already_held else pipeline_lock(sync_instance.name, timeout=_lock_timeout)
    )
    with lock_scope:
        factory: PotendaFactory = potenda_factory if potenda_factory is not None else get_potenda_from_instance
        # Pinned call shape: all seven keyword arguments of
        # utils.get_potenda_from_instance, always explicitly, for both operations.
        ptd = factory(
            sync_instance=sync_instance,
            branch=branch,
            show_progress=show_progress,
            verbosity=verbosity,
            run_id=run_id,
            continue_on_error=continue_on_error,
            concurrent_load=concurrent_load,
        )
        ptd.force_full_extract = full_extract
        if ptd.run_dir is None:  # get_potenda_from_instance always allocates one
            msg = "get_potenda_from_instance did not allocate a run_dir"
            raise RuntimeError(msg)
        run_file = RunFile(
            path=ptd.run_dir / "run.json",
            status="running",
            mode="diff" if operation == "plan" else "sync",
        )
        run_file.save()

        try:
            if operation == "plan":
                rows = _run_plan_lifecycle(ptd=ptd, run_file=run_file)
            else:
                rows = _run_sync_lifecycle(
                    ptd=ptd,
                    run_file=run_file,
                    print_diff=print_diff,
                    allow_rowcount_drop=allow_rowcount_drop,
                    serial_load_error=_serial_load_error,
                )
        except Exception:
            # Preserved CLI pattern (cli.py:156-159 / 285-288), verbatim: mark
            # run.json failed, then bare re-raise of the ORIGINAL exception, so a
            # lifecycle failure can never leave run.json at status="running".
            # This broad except is that existing pattern, not new looseness.
            run_file.status = "failed"
            run_file.save()
            raise

        run_file.finished_at = datetime.now(timezone.utc).isoformat()
        run_file.save()
        if operation == "plan":
            logger.info("Cached run %s at %s", ptd.run_id, ptd.run_dir)
        else:
            logger.info("Sync run %s at %s", ptd.run_id, ptd.run_dir)
        return _build_result(sync_instance=sync_instance, operation=operation, ptd=ptd, rows=rows)


# --------------------------------------------------------------------------- #
# Remote composition — THE sanitize-and-wrap boundary
# --------------------------------------------------------------------------- #


def _missing_credential_hint(detail: str) -> str:
    """Return the env-var naming suffix for an adapter missing-credential refusal.

    Attribution comes from the `Error initializing <Name>Adapter:` prefix
    `utils.get_potenda_from_instance` always emits, so the variables named are the
    FAILING adapter's — not those of whichever system the configuration happens to
    mention (Infrahub is almost always one side, which made every adapter's missing
    `url` read as an Infrahub credential problem). Returns `""` — no hint at all —
    when the message carries no such prefix, when the named adapter is absent from
    :data:`ADAPTER_CREDENTIAL_ENV_VARS`, or when the detail is not a
    missing-credential refusal.
    """
    match = _ADAPTER_INIT_PREFIX.match(detail)
    if match is None:
        return ""
    adapter_key = re.sub(r"[^a-z0-9]", "", match.group("adapter").lower())
    env_vars = ADAPTER_CREDENTIAL_ENV_VARS.get(adapter_key)
    if not env_vars:
        return ""
    lowered = match.group("detail").lower()
    if not any(marker in lowered for marker in _MISSING_CREDENTIAL_MARKERS):
        return ""
    joined = " and ".join(env_vars) if len(env_vars) < 3 else ", ".join(env_vars[:-1]) + f", and {env_vars[-1]}"
    return f" Set the runner-environment variables {joined}."


def run_remote_request(
    sync_name: str,
    operation: Operation = "plan",
    confirm_writes: bool = False,
    branch: str | None = None,
    *,
    config_directory: str,
    # Private test seams, mirroring execute_run's. NOT part of the remote contract:
    # the Prefect flow never sets them, no remote caller can reach them, and they
    # default to exactly the production values. They exist so tests can drive the
    # REAL sanitize-and-wrap boundary without improvising a monkeypatch target.
    _potenda_factory: PotendaFactory | None = None,
    _lock_timeout: float = 60.0,
) -> RunResult:
    """Resolve `sync_name` under `config_directory` and run it with the remote defaults.

    Every engine option stays at its CLI default except `show_progress=False`.
    No public parameter accepts paths, CLI fragments, credentials, or environment
    overrides.

    This is THE sanitize-and-wrap boundary: `RunValidationError` passes through
    unchanged (already sanitized at raise) and every other failure becomes a
    `RunExecutionError` whose message — and whose whole cause chain — is redacted.
    Configuration resolution runs INSIDE that boundary, so a failure the tolerant
    per-file walk does not turn into a `RunValidationError` cannot bypass it.

    Raises:
        RunValidationError: request or configuration refusal.
        RunExecutionError: any adapter or engine failure, sanitized.
    """

    def factory(**kwargs: Any) -> Potenda:
        """Mark factory-stage `ValueError`s so only they are labeled "initialize".

        Mirrors `cli._cli_potenda_factory`: the CLI keeps its prefixed abort at the
        construction site, and this keeps the equivalent wording tied to the same
        stage. The module global is resolved at call time so patches on
        `infrahub_sync.execution.get_potenda_from_instance` still intercept it.
        """
        inner: PotendaFactory = _potenda_factory if _potenda_factory is not None else get_potenda_from_instance
        try:
            return inner(**kwargs)
        except ValueError as exc:
            raise _FactoryValueError(str(exc)) from exc

    secrets = collect_secret_values()
    try:
        sync_instance = resolve_sync_instance(sync_name, directory=config_directory)
        secrets = collect_secret_values(sync_instance)
        return execute_run(
            sync_instance,
            operation=operation,
            confirm_writes=confirm_writes,
            branch=branch,
            show_progress=False,
            potenda_factory=factory,
            _lock_timeout=_lock_timeout,
        )
    except RunValidationError:
        raise
    except Timeout as exc:
        msg = redact(
            f"Sync {sync_name!r} could not acquire its pipeline lock within {_lock_timeout} seconds — "
            "another run is in progress",
            secrets,
        )
        raise RunExecutionError(msg) from sanitize_exception_chain(exc, secrets)
    except _FactoryValueError as exc:
        # Factory stage only. A load-stage `ValueError` (potenda wraps every load
        # failure into one) and a `RunResult` invariant violation fall through to
        # the stage-naming clause below instead of being mislabeled a credential
        # problem.
        detail = redact(str(exc), secrets)
        msg = f"Failed to initialize the Sync Instance: {detail}{_missing_credential_hint(detail)}"
        raise RunExecutionError(msg) from sanitize_exception_chain(exc.__cause__ or exc, secrets)
    except ImportError as exc:
        msg = redact(f"Failed to import an adapter for sync {sync_name!r}: {exc}", secrets)
        raise RunExecutionError(msg) from sanitize_exception_chain(exc, secrets)
    except Exception as exc:  # noqa: BLE001 - boundary translation, always re-raised typed
        # Caught broadly, ALWAYS re-raised typed, never swallowed. The BLE001
        # directive is unavoidable here: the two cause-chain mechanisms this
        # boundary is allowed to use (a rebuilt sanitized cause, or
        # __suppress_context__ with the redacted text inlined) both fire BLE001,
        # and the one BLE001-clean form — plain `from exc` — is precisely the
        # unredacted-cause traceback leak this boundary exists to prevent.
        msg = redact(
            f"Sync {sync_name!r} failed during operation={operation}: {type(exc).__name__}: {exc}",
            secrets,
        )
        raise RunExecutionError(msg) from sanitize_exception_chain(exc, secrets)
