"""Shared typed execution surface for plan, sync, verify, and apply lifecycles.

Four entry paths use this module: the CLI ``diff``, ``sync``, and ``apply``
commands, plus the packaged Prefect flow. The CLI ``diff`` path selects either
plan generation or saved-plan verification, and ``sync`` selects serial or
tier-parallel execution. This module imports no Prefect symbol and stays
importable in a base install.

Failure semantics: :func:`execute_run` preserves lifecycle exception types,
apart from its typed concurrency refusal; :func:`run_remote_request` is the one
sanitize-and-wrap boundary that converts failures into
:class:`RunValidationError` / :class:`RunExecutionError`.
"""
# pylint: disable=too-many-lines

from __future__ import annotations

import logging
import os
import re
from collections.abc import Mapping
from contextlib import AbstractContextManager, ExitStack, contextmanager, nullcontext
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Literal, Protocol, cast, overload

import pydantic
import yaml
from filelock import Timeout

from infrahub_sync import SyncConfig, SyncInstance
from infrahub_sync.cache.locks import pipeline_lock
from infrahub_sync.cache.paths import cache_root_for
from infrahub_sync.cache.sidecars import RunFile
from infrahub_sync.plan.config_version import resolve_config_version
from infrahub_sync.plan.errors import (
    ApplyRecordInvariantError,
    OperationApplyFailedError,
    PlanArtifactError,
    PlanVerificationError,
)
from infrahub_sync.plan.models import ApplyRecord
from infrahub_sync.plan.reader import read_plan_artifact_bytes, require_plan_directory
from infrahub_sync.plan.review import (
    expected_checksum_refusal,
    read_saved_plan,
    require_stored_run,
    resolve_run_directory,
)
from infrahub_sync.plan.verify import verify_plan
from infrahub_sync.plan.writer import require_uncommitted_plan
from infrahub_sync.utils import PlanApplier, get_potenda_from_instance

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator, Sequence

    from infrahub_sync.plan.ownership import WriteOwnership
    from infrahub_sync.plan.review import SavedPlan
    from infrahub_sync.potenda import Potenda

logger = logging.getLogger(__name__)

Operation = Literal["plan", "sync", "verify", "apply"]
Status = Literal["planned", "applied", "no-change"]
ActionKey = Literal["create", "update", "delete"]


class PotendaFactory(Protocol):
    """The engine-factory call shape, injected wherever an engine is built.

    Spelled as a `Protocol` rather than `Callable[..., Potenda]` so the pinned
    seven-keyword call shape is part of the type: `Callable[..., ...]` erases the
    parameter names, and a rename in `utils.get_potenda_from_instance` then
    survives type checking and fails at run time inside the remote boundary
    instead. Keyword-only, because every caller passes all seven by keyword.

    Injection exists so the CLI can pass its own thin wrapper over the module
    global (which keeps existing patches on
    `infrahub_sync.cli.get_potenda_from_instance` effective).
    """

    def __call__(
        self,
        *,
        sync_instance: SyncInstance,
        branch: str | None = ...,
        show_progress: bool | None = ...,
        verbosity: int = ...,
        run_id: str | None = ...,
        continue_on_error: bool = ...,
        concurrent_load: bool = ...,
    ) -> Potenda: ...


OPERATIONS: tuple[Operation, ...] = ("plan", "sync", "verify", "apply")
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
# when its name CONTAINS one of the substrings, ENDS WITH one of the suffixes, or
# EQUALS one of the exact names — the same three boundary rules the environment
# half above uses, for the same reason. `key` and `auth` are matched at a name
# boundary rather than as bare substrings because the bare forms sweep in the
# shipped non-secret keys `response_key_pattern` and `auth_method`
# (adapters/genericrestapi.py:71,190) whose ordinary values (`objects`, `api-key`,
# `x-auth-token`) then shred the operator diagnostics this boundary exists to keep
# readable — `Authentication method '***' requires a valid API token!`. The
# boundary rules still cover every credential-shaped key the adapters read:
# `api_key` and `secret_key` by suffix, ipfabric's bare `auth` by exact name, a
# nested `headers.authorization`, `store.settings.password`, and every `token`
# variant by substring. Values are coerced by :func:`_coerce_secret_text`, so a
# non-string credential is collected too.
SECRET_SETTING_KEY_SUBSTRINGS = (
    "token",
    "password",
    "passwd",
    "secret",
    "credential",
    "apikey",
    "authorization",
)
SECRET_SETTING_KEY_SUFFIXES = ("_key", "_auth")
SECRET_SETTING_KEY_NAMES = ("key", "auth")
# A `<something>_env_vars` key names environment variables the adapter reads
# INSTEAD of an inline value (adapters/genericrestapi.py:59-92,
# adapters/peeringmanager.py:31-34). The names are not secrets; the values they
# point at are, when the key itself is credential-shaped.
ENV_VAR_LIST_SUFFIX = "_env_vars"
# Userinfo of a URL-shaped value: `scheme://user:password@host/...` hides a
# credential in plain sight, typically next to an already-redacted sibling token.
_URL_USERINFO = re.compile(r"://(?P<userinfo>[^/\s@]+)@")
# Depth ceiling for the recursive settings walk. `yaml.safe_load` builds
# self-referential structures from aliases (`token: &A\n  nested: *A`) and
# arbitrarily deep nesting from ordinary input, and an unbounded walk turns either
# into a `RecursionError` that fails EVERY run of that configuration with no hint
# that the config's own shape is the cause. No real adapter settings tree comes
# close to this depth; hitting it means the walk stops descending, never raises.
MAX_SETTINGS_DEPTH = 64

# Private composition channel used only by the packaged direct Prefect flow.
# Keeping this out of ``run_remote_request``'s parameters preserves that
# function's remote-shaped call contract while allowing the bridge's mutable
# collection to learn configuration-derived values after resolution.
_REMOTE_SECRET_VALUES: ContextVar[list[str] | None] = ContextVar("remote_secret_values", default=None)

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


class RunConcurrencyError(Exception):
    """A same-configuration run could not acquire the bounded pipeline lock."""


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


def _is_secret_setting_key(name: str) -> bool:
    """Return whether a `settings` key's NAME is credential-shaped (lower-cased in)."""
    return (
        name in SECRET_SETTING_KEY_NAMES
        or name.endswith(SECRET_SETTING_KEY_SUFFIXES)
        or any(part in name for part in SECRET_SETTING_KEY_SUBSTRINGS)
    )


def _coerce_secret_text(value: object) -> str | None:
    """Return `value` as text, or `None` when it is not a coercible scalar.

    Coercion is restricted to the scalar types a credential can actually be, so a
    hostile or merely unusual `__str__` on some object reachable from `settings`
    cannot raise out of the public :func:`collect_secret_values` and fail a run
    that was otherwise fine. `settings` is `yaml.safe_load` output in every real
    configuration, whose scalars are exactly `str`/`int`/`float` (`bool` and `None`
    are filtered earlier as ordinary words, and `Decimal` is accepted for a
    programmatically constructed `SyncInstance`).
    """
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, Decimal)):
        return str(value)
    logger.debug("Skipping a %s settings value during secret collection: not a coercible scalar", type(value).__name__)
    return None


def _add_secret(value: object, values: set[str]) -> None:
    """Add one candidate value, coercing scalars and dropping short ones."""
    text = _coerce_secret_text(value)
    if text is not None and len(text) >= MIN_SECRET_LENGTH:
        values.add(text)


def _add_url_userinfo(text: str, values: set[str]) -> None:
    """Add the userinfo of every URL-shaped value (`http://admin:pw@host/api`)."""
    for match in _URL_USERINFO.finditer(text):
        userinfo = match.group("userinfo")
        _add_secret(userinfo, values)
        _, separator, password = userinfo.partition(":")
        if separator:
            _add_secret(password, values)


def _add_named_env_values(names: Any, values: set[str], environ: Mapping[str, str]) -> None:
    """Collect the environment VALUES a `*_env_vars` list names, never the names.

    A name as a redaction target (`ADDRESS`) would shred unrelated messages.
    """
    if not isinstance(names, (list, tuple)):
        return
    for env_name in names:
        name = _coerce_secret_text(env_name)
        if name is not None:
            _add_secret(environ.get(name) or "", values)


def _may_descend(node: Any, *, secret_context: bool, seen: set[tuple[int, bool]], depth: int) -> bool:
    """Return whether this container may be walked, recording it as visited if so.

    Two independent bounds, both of which stop descending rather than raising.
    `seen` holds `(id(container), secret_context)` pairs: keying on the context as
    well as the identity keeps an alias shared between a plain and a
    credential-shaped key collected under BOTH contexts while still terminating on a
    self-referential structure, and bounds the walk at twice the container count.
    The depth cap backs it for an acyclic but absurdly deep tree.
    """
    if depth >= MAX_SETTINGS_DEPTH:
        logger.debug("Stopped secret collection below depth %d: the settings tree nests deeper", MAX_SETTINGS_DEPTH)
        return False
    marker = (id(node), secret_context)
    if marker in seen:
        logger.debug("Stopped secret collection at an already-walked settings container (alias or cycle)")
        return False
    seen.add(marker)
    return True


def _collect_from_leaf(node: Any, values: set[str], *, secret_context: bool) -> None:
    """Collect one non-container settings value."""
    if node is None or isinstance(node, bool):
        # `str(None)`/`str(False)` are ordinary words, never credentials.
        return
    if secret_context:
        _add_secret(node, values)
    if isinstance(node, str):
        _add_url_userinfo(node, values)


def _collect_from_settings(
    node: Any,
    values: set[str],
    *,
    secret_context: bool,
    environ: Mapping[str, str],
    seen: set[tuple[int, bool]],
    depth: int = 0,
) -> None:
    """Walk one `settings` subtree, collecting every credential-shaped value.

    `secret_context` is inherited: once a credential-shaped key is entered, every
    value beneath it counts (a `credentials:` block of plain-named entries, say).
    Descent through containers is bounded by :func:`_may_descend`.
    """
    if isinstance(node, (dict, list, tuple, set)) and not _may_descend(
        node, secret_context=secret_context, seen=seen, depth=depth
    ):
        return
    child_depth = depth + 1
    if isinstance(node, dict):
        for key, value in node.items():
            lowered = str(key).lower()
            is_secret_key = secret_context or _is_secret_setting_key(lowered)
            if lowered.endswith(ENV_VAR_LIST_SUFFIX):
                if is_secret_key:
                    _add_named_env_values(value, values, environ)
                continue
            _collect_from_settings(
                value,
                values,
                secret_context=is_secret_key,
                environ=environ,
                seen=seen,
                depth=child_depth,
            )
    elif isinstance(node, (list, tuple, set)):
        for item in node:
            _collect_from_settings(
                item,
                values,
                secret_context=secret_context,
                environ=environ,
                seen=seen,
                depth=child_depth,
            )
    else:
        _collect_from_leaf(node, values, secret_context=secret_context)


def collect_secret_values(
    sync_instance: SyncInstance | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> tuple[str, ...]:
    """Collect the secret VALUES that must never appear in a surface message.

    Sources:

    - the runner environment — the value of every variable whose NAME is
      credential-shaped (:func:`_is_secret_env_name`), PLUS the userinfo of every
      URL-shaped value regardless of its variable name;
    - the resolved configuration's `source`, `destination`, AND `store` settings,
      walked recursively: the value of every key whose name is credential-shaped
      (:func:`_is_secret_setting_key`; nested `headers`/`params` included,
      non-string scalars coerced), the userinfo of every URL-shaped value, and
      the environment values named by the configuration's own `*_env_vars` lists.

    The environment userinfo scan is deliberately name-blind: the runner-side
    endpoint variables (`NETBOX_ADDRESS`, `PROM_URL`, `CISCO_APIC_URL`, …) are how
    every adapter learns where to connect (`adapters/netbox.py:42`,
    `adapters/prometheus.py:388`, `adapters/aci.py:208`), their names are not
    credential-shaped, and a password embedded in one would otherwise reach a
    remote caller verbatim in the first connection-refused message. Userinfo still
    has to clear :data:`MIN_SECRET_LENGTH`, so a name-blind scan over-collects
    almost nothing.

    Values shorter than :data:`MIN_SECRET_LENGTH` are dropped. Longest values
    first, so overlapping secrets redact completely.
    """
    env = os.environ if environ is None else environ
    values: set[str] = set()
    for name, value in env.items():
        if not value:
            continue
        if _is_secret_env_name(name):
            _add_secret(value, values)
        _add_url_userinfo(value, values)
    if sync_instance is not None:
        settings_blocks = [sync_instance.source.settings, sync_instance.destination.settings]
        if sync_instance.store is not None:
            settings_blocks.append(sync_instance.store.settings)
        for settings in settings_blocks:
            _collect_from_settings(settings or {}, values, secret_context=False, environ=env, seen=set())
    return tuple(sorted(values, key=lambda value: (-len(value), value)))


@contextmanager
def _bind_remote_secret_values(values: list[str]) -> Iterator[None]:
    """Bind a flow-owned mutable secret collection for one remote request."""
    token = _REMOTE_SECRET_VALUES.set(values)
    try:
        yield
    finally:
        _REMOTE_SECRET_VALUES.reset(token)


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
    """Immutable success result — exactly these seven fields.

    `changed` and `summary` describe the MATERIALIZED PLAN ROWS, not whether the
    destination was written. `Potenda._diff_to_rows` walks only the diff root's
    direct children while `Diff.has_diffs()` — which gates the sync — is
    recursive, so a difference confined to nested child elements runs the sync
    and materializes zero rows. Such a run reports `status="no-change"` and
    `changed=False` even though it may have written. Pinned by
    `tests/test_execution_cli_parity.py::test_nested_only_diff_reports_no_change_even_though_the_sync_ran`
    and documented for remote callers in `docs/docs/reference/prefect-remote-run.mdx`.
    """

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
        if self.status == "applied" and self.operation not in ("sync", "apply"):
            msg = f"status='applied' requires operation='sync' or 'apply', got operation={self.operation!r}"
            raise ValueError(msg)
        artifact = Path(self.artifact_path)
        if not artifact.is_absolute():
            # The field crosses a process boundary — it is one of the seven returned
            # fields and one of the five on the flow's contractual summary line — and
            # a remote caller cannot recover the serving process's cwd, so a relative
            # value is unusable rather than merely untidy. `cache.paths.cache_root_for`
            # absolutizes at the single derivation point; this makes the contract
            # ("absolute runner-local run directory") self-enforcing.
            msg = f"artifact_path must be absolute, got {self.artifact_path!r}"
            raise ValueError(msg)
        if self.run_id != artifact.name:
            msg = f"run_id={self.run_id!r} must equal the final segment of artifact_path={self.artifact_path!r}"
            raise ValueError(msg)
        object.__setattr__(self, "summary", MappingProxyType(dict(self.summary)))


# --------------------------------------------------------------------------- #
# Configuration resolution
# --------------------------------------------------------------------------- #


def _load_config_name(config_file: Path) -> tuple[bool, Any]:
    """Return `(determinable, document)` for one discovered `config.yml`.

    The document is the whole parsed mapping; the caller reads its top-level
    `name` key. It is
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


def _validated_plan_write_summary(write_result: object) -> dict[ActionKey, int] | None:
    """Validate supplied saved-operation counts; return `None` for a legacy writer."""
    if not isinstance(write_result, Mapping):
        return None
    if set(write_result) != set(ACTION_KEYS):
        msg = f"plan write summary must carry exactly the keys {ACTION_KEYS!r}, got {sorted(write_result, key=str)!r}"
        raise ValueError(msg)
    typed_result = cast("Mapping[str, object]", write_result)
    summary: dict[ActionKey, int] = {}
    for action in ACTION_KEYS:
        value = typed_result[action]
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            msg = f"plan write summary value for {action!r} must be a non-negative integer, got {value!r}"
            raise ValueError(msg)
        summary[action] = value
    return summary


def _summary_from_plan_write(
    write_result: object,
    fallback_rows: Callable[[], Iterable[Mapping[str, Any]]],
) -> dict[ActionKey, int]:
    """Use saved-operation counts when supplied, with legacy row fallback for test engines."""
    summary = _validated_plan_write_summary(write_result)
    return _summarize_rows(fallback_rows()) if summary is None else summary


def _run_plan_lifecycle(*, ptd: Potenda, run_file: RunFile, print_diff: bool) -> dict[ActionKey, int]:
    """Reproduce the CLI `diff` lifecycle and return authoritative operation counts."""
    ptd.load_both_sides()
    mydiff = ptd.diff()
    write_result = ptd.write_plan(mydiff)
    if print_diff:
        logger.info("\n%s", mydiff.str())
    run_file.status = "dry-run"
    run_file.summary = {"resources": len(ptd.top_level)}
    return _summary_from_plan_write(write_result, lambda: ptd._diff_to_rows(mydiff))


def _require_a_free_run_id(*, sync_name: str, run_id: str | None) -> None:
    """Refuse a plan generation that would overwrite a committed saved plan."""
    if run_id is None:
        return
    directory = resolve_run_directory(sync_name, run_id)
    require_uncommitted_plan(directory, run_id=run_id)


def _latest_running_run_id(sync_name: str) -> str | None:
    """Return the newest run still marked running, which may be stale."""
    root = cache_root_for(sync_name)
    try:
        candidates = sorted((path for path in root.iterdir() if path.is_dir()), reverse=True)
    except OSError:
        return None
    for directory in candidates[:20]:
        try:
            run_file = RunFile.load_or_default(directory / "run.json")
        except (OSError, ValueError):
            continue
        if run_file.status == "running":
            return directory.name
    return None


@contextmanager
def _run_lock(sync_name: str, *, timeout: float) -> Iterator[None]:
    """Acquire the core-owned lock and report advisory sidecar context while waiting."""
    lock_stack = ExitStack()
    try:
        lock_stack.enter_context(pipeline_lock(sync_name, timeout=0))
    except Timeout:
        latest_running = _latest_running_run_id(sync_name)
        detail = (
            f"the latest run sidecar still marked running is {latest_running!r} (it may be stale)"
            if latest_running is not None
            else "no run sidecar currently identifies the lock owner"
        )
        logger.warning("Sync %r is locked; %s; waiting up to %s seconds", sync_name, detail, timeout)
        try:
            lock_stack.enter_context(pipeline_lock(sync_name, timeout=timeout))
        except Timeout as exc:
            lock_stack.close()
            msg = f"Sync {sync_name!r} could not acquire its pipeline lock within {timeout} seconds; {detail}."
            raise RunConcurrencyError(msg) from exc
    with lock_stack:
        yield


def _require_applicable_plan(*, sync_name: str, run_id: str) -> Path:
    """Locate an existing saved plan before constructing an apply destination."""
    directory = require_stored_run(sync_name, run_id)
    require_plan_directory(directory)
    return directory


def _require_expected_checksum(*, run_directory: Path, run_id: str, expected: str | None) -> None:
    """Perform the apply command's no-destination checksum fast path."""
    if expected is None:
        return
    artifact = read_plan_artifact_bytes(run_directory)
    refusal = expected_checksum_refusal(artifact=artifact, run_id=run_id, expected=expected)
    if refusal is not None:
        raise PlanVerificationError(refusal.reason, next_action=refusal.next_action)


def _apply_summary(counts: Mapping[str, int]) -> dict[ActionKey, int]:
    """Return action counts parsed from the exact artifact consumed by apply."""
    return {action: counts.get(action, 0) for action in ACTION_KEYS}


def _save_run_transition(
    run_directory: Path,
    *,
    mode: Literal["diff", "sync", "apply"],
    status: Literal["running", "applied", "failed"],
    record: ApplyRecord | None = None,
    run_file: RunFile | None = None,
) -> None:
    """Persist one core-owned transition without discarding earlier summary data."""
    run_file = run_file or RunFile.load_or_default(run_directory / "run.json")
    run_file.mode = mode
    run_file.status = status
    if record is not None:
        run_file.summary.update(record.as_summary_keys())
    run_file.finished_at = None if status == "running" else datetime.now(timezone.utc).isoformat()
    run_file.save()


def _save_failed_run(
    run_directory: Path,
    *,
    mode: Literal["diff", "sync", "apply"],
    record: ApplyRecord | None = None,
    run_file: RunFile | None = None,
) -> None:
    """Best-effort a terminal failure without replacing the lifecycle exception."""
    final_error: BaseException | None = None
    for _attempt in range(2):
        try:
            _save_run_transition(
                run_directory,
                mode=mode,
                status="failed",
                record=record,
                run_file=run_file,
            )
        except BaseException as exc:  # noqa: BLE001  # pylint: disable=broad-exception-caught
            final_error = exc
            continue
        else:
            return
    error_type = type(final_error).__name__ if final_error is not None else "unknown error"
    logger.warning(
        "Unable to persist failed run state after two attempts (%s)",
        error_type,
    )


def _read_verified_plan(*, sync_instance: SyncInstance, run_id: str) -> SavedPlan:
    """Read and independently verify one saved plan without constructing adapters."""
    saved = read_saved_plan(sync_name=sync_instance.name, run_id=run_id, config=sync_instance)
    run_directory = resolve_run_directory(sync_instance.name, run_id)
    failures = verify_plan(
        artifact=read_plan_artifact_bytes(run_directory),
        run_id=run_id,
        config_version=resolve_config_version(sync_instance),
    )
    if failures:
        checks = ", ".join(failure.check for failure in failures)
        detail = "; ".join(
            f"{failure.check}: expected {failure.expected}; found {failure.found}" for failure in failures
        )
        next_actions = tuple(dict.fromkeys(failure.next_action for failure in failures))
        msg = f"Saved plan {run_id!r} failed verification checks: {checks}. {detail}"
        raise PlanVerificationError(msg, next_action=" ".join(next_actions))
    return saved


def _run_apply_lifecycle(  # pylint: disable=too-many-arguments
    *,
    sync_instance: SyncInstance,
    run_id: str,
    branch: str | None,
    verbosity: int,
    allow_destination_change: bool,
    expected_checksum: str | None,
    ownership: WriteOwnership,
    record_applied: Callable[[ApplyRecord], None],
    _plan_applier_factory: Callable[..., PlanApplier] | None,
    run_directory: Path,
    sidecar_mode: Literal["sync", "apply"],
) -> RunResult:
    """Apply one saved plan while the shared core owns its sidecar transitions."""
    factory = _plan_applier_factory if _plan_applier_factory is not None else PlanApplier.open_existing
    try:
        applier = factory(sync_instance, run_id=run_id, branch=branch, verbosity=verbosity)
    except BaseException:
        if sidecar_mode == "sync":
            _save_failed_run(run_directory, mode="sync")
        raise

    run_file = RunFile.load_or_default(run_directory / "run.json")
    _save_run_transition(run_directory, mode=sidecar_mode, status="running", run_file=run_file)
    try:
        record = applier.apply_plan(
            ownership=ownership,
            allow_destination_change=allow_destination_change,
            expected_checksum=expected_checksum,
        )
    except (OperationApplyFailedError, ApplyRecordInvariantError) as exc:
        _save_failed_run(run_directory, mode=sidecar_mode, record=exc.apply_record, run_file=run_file)
        raise
    except PlanArtifactError:
        _save_failed_run(run_directory, mode=sidecar_mode, record=ApplyRecord(), run_file=run_file)
        raise
    except BaseException as exc:
        carried = getattr(exc, "apply_record", None)
        partial = carried if isinstance(carried, ApplyRecord) else ApplyRecord()
        _save_failed_run(run_directory, mode=sidecar_mode, record=partial, run_file=run_file)
        raise

    # Reported to the write scope before anything else can fail: the sidecar write, the
    # guard's release, and the product success commit all happen after this point, and each
    # of them is a failure after a dispatch that still has to say what was written. The sink
    # is separate from `ownership` on purpose — that boundary authorizes the write, and
    # saying what the write did is not its question to answer.
    record_applied(record)
    try:
        _save_run_transition(run_directory, mode=sidecar_mode, status="applied", record=record, run_file=run_file)
    except BaseException as exc:
        # Everything the loop applied is already written; a sidecar that could not record
        # that must not make the completed record unreadable to the failure boundary.
        _save_failed_run(run_directory, mode=sidecar_mode, record=record, run_file=run_file)
        exc.apply_record = record  # ty: ignore[unresolved-attribute]
        raise
    return _build_result(
        sync_instance=sync_instance,
        operation="apply",
        ptd=applier.engine,
        summary=_apply_summary(applier.applied_plan_action_counts),
    )


def _build_result(
    *,
    sync_instance: SyncInstance,
    operation: Operation,
    ptd: Potenda,
    summary: Mapping[ActionKey, int],
) -> RunResult:
    """Derive the result from the in-memory operation summary."""
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


def _execute_verify_operation(
    *,
    sync_instance: SyncInstance,
    run_id: str,
    require_verified: bool,
    run_file_mode: Literal["diff", "sync"] | None,
) -> SavedPlan:
    """Run one read-only verification, terminalizing only a composed run failure."""
    try:
        if require_verified:
            return _read_verified_plan(sync_instance=sync_instance, run_id=run_id)
        return read_saved_plan(sync_name=sync_instance.name, run_id=run_id, config=sync_instance)
    except BaseException:
        if run_file_mode is not None:
            _save_failed_run(resolve_run_directory(sync_instance.name, run_id), mode=run_file_mode)
        raise


def _execute_apply_operation(
    *,
    sync_instance: SyncInstance,
    run_id: str,
    branch: str | None,
    verbosity: int,
    allow_destination_change: bool,
    expected_checksum: str | None,
    ownership: WriteOwnership,
    record_applied: Callable[[ApplyRecord], None],
    plan_applier_factory: Callable[..., PlanApplier] | None,
    lock_timeout: float,
    lock_already_held: bool,
    run_file_mode: Literal["diff", "sync"] | None,
) -> RunResult:
    """Apply one reviewed plan with core-owned lock and sidecar transitions."""
    run_directory = _require_applicable_plan(sync_name=sync_instance.name, run_id=run_id)
    sidecar_mode: Literal["sync", "apply"] = "sync" if run_file_mode == "sync" else "apply"
    try:
        _require_expected_checksum(run_directory=run_directory, run_id=run_id, expected=expected_checksum)
    except BaseException:
        if sidecar_mode == "sync":
            _save_failed_run(run_directory, mode="sync")
        raise
    apply_lock: AbstractContextManager[None] = (
        nullcontext() if lock_already_held else _run_lock(sync_instance.name, timeout=lock_timeout)
    )
    with apply_lock:
        return _run_apply_lifecycle(
            sync_instance=sync_instance,
            run_id=run_id,
            branch=branch,
            verbosity=verbosity,
            allow_destination_change=allow_destination_change,
            expected_checksum=expected_checksum,
            ownership=ownership,
            record_applied=record_applied,
            _plan_applier_factory=plan_applier_factory,
            run_directory=run_directory,
            sidecar_mode=sidecar_mode,
        )


def _validate_operation_request(
    *,
    operation: Operation,
    confirm_writes: bool,
    run_id: str | None,
    ownership: WriteOwnership | None,
    record_applied: Callable[[ApplyRecord], None] | None,
) -> None:
    """Refuse invalid operation inputs before any adapter or run state is constructed."""
    if operation not in OPERATIONS:
        msg = f"Unsupported operation {operation!r} — expected one of {OPERATIONS!r}"
        raise RunValidationError(msg)
    if operation == "sync":
        # The composed sync writer is not a supported entry point: it produces its own plan
        # and applies it in one call, so it can offer no per-operation ownership proof over
        # a reviewed artifact. The Sync HTTP API composes plan, verify, and apply instead,
        # holding one configuration write guard across them.
        msg = "operation=sync is not supported here; compose plan, verify, and apply through the Sync API"
        raise RunValidationError(msg)
    if operation == "apply" and not confirm_writes:
        msg = f"confirm_writes=true is required to run operation={operation}"
        raise RunValidationError(msg)
    if operation in ("verify", "apply") and run_id is None:
        msg = f"run_id is required to run operation={operation}"
        raise RunValidationError(msg)
    if operation == "apply" and ownership is None:
        msg = "an explicit write-ownership boundary is required to run operation=apply"
        raise RunValidationError(msg)
    if operation == "apply" and record_applied is None:
        msg = "a completion sink is required to run operation=apply"
        raise RunValidationError(msg)


# Pylint applies this check to the implementation's first line, below the overloads.
# pylint: disable=too-many-branches
@overload
def execute_run(sync_instance: SyncInstance, *, operation: Literal["verify"], **kwargs: Any) -> SavedPlan: ...


@overload
def execute_run(
    sync_instance: SyncInstance,
    *,
    operation: Literal["plan"],
    _return_saved_plan: Literal[True],
    **kwargs: Any,
) -> SavedPlan: ...


@overload
def execute_run(
    sync_instance: SyncInstance,
    *,
    operation: Literal["apply"],
    ownership: WriteOwnership,
    record_applied: Callable[[ApplyRecord], None],
    **kwargs: Any,
) -> RunResult: ...


# `apply` is deliberately absent from the remaining overloads: it is the only operation that
# writes, so the type checker — not just the runtime refusal below — is what rejects an apply
# with no write-ownership boundary.
@overload
def execute_run(sync_instance: SyncInstance, *, operation: Literal["plan"], **kwargs: Any) -> RunResult: ...


@overload
def execute_run(sync_instance: SyncInstance, *, operation: Literal["plan", "sync"], **kwargs: Any) -> RunResult: ...


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
    continue_on_error: bool = False,
    print_diff: bool = True,
    potenda_factory: PotendaFactory | None = None,
    allow_destination_change: bool = False,
    expected_checksum: str | None = None,
    ownership: WriteOwnership | None = None,
    record_applied: Callable[[ApplyRecord], None] | None = None,
    # Private seams — not part of the remote contract; run_remote_request never sets them.
    _plan_applier_factory: Callable[..., PlanApplier] | None = None,
    _lock_timeout: float = 60.0,
    _lock_already_held: bool = False,
    _run_file_mode: Literal["diff", "sync"] | None = None,
    _require_verified: bool = False,
    _return_saved_plan: bool = False,
) -> RunResult | SavedPlan:
    """Run one product operation against a resolved instance.

    The core owns all run-sidecar transitions.  The CLI remains responsible only
    for argument parsing and rendering, so the existing command-specific failure
    shapes stay at that boundary.

    An apply writes to a destination, so it requires `ownership`: the boundary that
    proves this process still holds the right to write this configuration. The
    parameter is optional in the signature only because plan and verify do not write;
    an apply without one is refused before anything is constructed.

    Raises:
        RunConcurrencyError: the same synchronization remains locked after the
            bounded wait, with advisory context from the latest running sidecar.
        RunValidationError: an unsupported operation, a composed sync write, a
            missing saved-plan id, an unconfirmed write, or an apply with no
            write-ownership boundary (all refused before an adapter is built).
    """
    _validate_operation_request(
        operation=operation,
        confirm_writes=confirm_writes,
        run_id=run_id,
        ownership=ownership,
        record_applied=record_applied,
    )

    if operation == "verify":
        assert run_id is not None  # narrowed above; verification never allocates a run.
        return _execute_verify_operation(
            sync_instance=sync_instance,
            run_id=run_id,
            require_verified=_require_verified,
            run_file_mode=_run_file_mode,
        )

    if operation == "apply":
        assert run_id is not None  # narrowed above; keeps the saved-plan boundary explicit.
        assert ownership is not None  # narrowed above; an apply never runs unguarded.
        assert record_applied is not None  # narrowed above; an apply always reports what it did.
        return _execute_apply_operation(
            sync_instance=sync_instance,
            run_id=run_id,
            branch=branch,
            verbosity=verbosity,
            allow_destination_change=allow_destination_change,
            expected_checksum=expected_checksum,
            ownership=ownership,
            record_applied=record_applied,
            plan_applier_factory=_plan_applier_factory,
            lock_timeout=_lock_timeout,
            lock_already_held=_lock_already_held,
            run_file_mode=_run_file_mode,
        )

    _require_a_free_run_id(sync_name=sync_instance.name, run_id=run_id)

    run_lock: AbstractContextManager[None] = (
        nullcontext() if _lock_already_held else _run_lock(sync_instance.name, timeout=_lock_timeout)
    )
    with run_lock:
        # A plan may have been committed while this command waited for the lock.
        _require_a_free_run_id(sync_name=sync_instance.name, run_id=run_id)
        factory: PotendaFactory = potenda_factory if potenda_factory is not None else get_potenda_from_instance
        # Pinned call shape: all seven keyword arguments of
        # utils.get_potenda_from_instance, always explicitly.
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
        run_mode: Literal["diff", "sync"] = _run_file_mode or "diff"
        run_file = RunFile(
            path=ptd.run_dir / "run.json",
            status="running",
            mode=run_mode,
        )
        run_file.save()

        saved_plan: SavedPlan | None = None
        try:
            summary = _run_plan_lifecycle(ptd=ptd, run_file=run_file, print_diff=print_diff)
            if _return_saved_plan:
                saved_plan = read_saved_plan(sync_name=sync_instance.name, run_id=str(ptd.run_id), config=sync_instance)

            run_file.finished_at = datetime.now(timezone.utc).isoformat()
            run_file.save()
        except BaseException:
            # Preserved CLI pattern (cli.py:156-159 / 285-288), verbatim: mark
            # run.json failed, then bare re-raise of the ORIGINAL exception, so a
            # lifecycle failure can never leave run.json at status="running".
            # This broad except is that existing pattern, not new looseness.
            _save_failed_run(ptd.run_dir, mode=run_mode, run_file=run_file)
            raise
        logger.info("Cached run %s at %s", ptd.run_id, ptd.run_dir)
        if saved_plan is not None:
            return saved_plan
        return _build_result(sync_instance=sync_instance, operation=operation, ptd=ptd, summary=summary)


# pylint: enable=too-many-branches
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
    operation: Literal["plan", "sync"] = "plan",
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
    shared_secrets = _REMOTE_SECRET_VALUES.get()
    if shared_secrets is not None:
        shared_secrets[:] = secrets
    try:
        sync_instance = resolve_sync_instance(sync_name, directory=config_directory)
        secrets = collect_secret_values(sync_instance)
        if shared_secrets is not None:
            shared_secrets[:] = secrets
        return cast(
            "RunResult",
            execute_run(
                sync_instance,
                operation=operation,
                confirm_writes=confirm_writes,
                branch=branch,
                show_progress=False,
                potenda_factory=factory,
                _lock_timeout=_lock_timeout,
            ),
        )
    except RunValidationError:
        raise
    except RunConcurrencyError as exc:
        msg = redact(str(exc), secrets)
        raise RunExecutionError(msg) from sanitize_exception_chain(exc, secrets)
    except _FactoryValueError as exc:
        # Factory stage only. A load-stage `ValueError` (potenda wraps every load
        # failure into one) and a `RunResult` invariant violation fall through to
        # the stage-naming clause below instead of being mislabeled a credential
        # problem.
        # The hint is computed from the RAW detail and appended to the REDACTED one:
        # it emits only environment-variable NAMES, never any detail text, while the
        # `Error initializing <Name>Adapter:` prefix and the marker phrases it matches
        # are themselves redaction targets — one collected substring away from
        # silently dropping the hint the operator needs.
        raw_detail = str(exc)
        detail = redact(raw_detail, secrets)
        msg = f"Failed to initialize the Sync Instance: {detail}{_missing_credential_hint(raw_detail)}"
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
