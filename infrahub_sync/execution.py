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
# Value-based secret collection. Names matched exactly, plus every environment
# variable whose NAME ends with one of the suffixes — adapter credentials such as
# NETBOX_TOKEN reach the runner through the environment, outside the resolved
# configuration's settings.
SECRET_ENV_NAMES = ("INFRAHUB_API_TOKEN",)
SECRET_ENV_NAME_SUFFIXES = ("_TOKEN", "_PASSWORD", "_SECRET", "_API_KEY")
SECRET_SETTING_KEYS = ("token", "password", "secret", "api_key")

# Runner-environment variables named in the missing-credential wrap message for
# the infrahub adapter. Named here, at the remote boundary, so
# `infrahub_sync/adapters/infrahub.py` stays untouched and the CLI's own wording
# is unchanged.
INFRAHUB_CREDENTIAL_ENV_VARS = ("INFRAHUB_ADDRESS", "INFRAHUB_API_TOKEN")
_MISSING_CREDENTIAL_MARKERS = ("must be specified", "must be set", "no token", "missing credential")


class RunValidationError(Exception):
    """Input-boundary refusal: unconfirmed write, or an unresolvable/invalid configuration."""


class RunExecutionError(Exception):
    """Adapter or engine failure after validation passed."""


# --------------------------------------------------------------------------- #
# Secret redaction
# --------------------------------------------------------------------------- #


def collect_secret_values(
    sync_instance: SyncInstance | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> tuple[str, ...]:
    """Collect the secret VALUES that must never appear in a surface message.

    Sources: the runner environment (names in :data:`SECRET_ENV_NAMES` plus any
    name ending with a :data:`SECRET_ENV_NAME_SUFFIXES` suffix) and the resolved
    configuration's source/destination settings (keys in
    :data:`SECRET_SETTING_KEYS`). Longest values first so overlapping secrets
    redact completely.
    """
    env = os.environ if environ is None else environ
    values: set[str] = set()
    for name, value in env.items():
        upper = name.upper()
        if (upper in SECRET_ENV_NAMES or upper.endswith(SECRET_ENV_NAME_SUFFIXES)) and value:
            values.add(value)
    if sync_instance is not None:
        for adapter in (sync_instance.source, sync_instance.destination):
            for key, value in (adapter.settings or {}).items():
                if key.lower() in SECRET_SETTING_KEYS and isinstance(value, str) and value:
                    values.add(value)
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
    UNDETERMINABLE when the read raises `OSError`, the parse raises
    `yaml.YAMLError`, or the loaded document is not a mapping.
    """
    try:
        data = yaml.safe_load(config_file.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
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


def _missing_credential_hint(sync_instance: SyncInstance, detail: str) -> str:
    """Return the env-var naming suffix for an adapter missing-credential refusal."""
    lowered = detail.lower()
    names_infrahub = any(
        "infrahub" in adapter.name.lower() for adapter in (sync_instance.source, sync_instance.destination)
    )
    if names_infrahub and any(marker in lowered for marker in _MISSING_CREDENTIAL_MARKERS):
        joined = " and ".join(INFRAHUB_CREDENTIAL_ENV_VARS)
        return f" Set the runner-environment variables {joined}."
    return ""


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

    Raises:
        RunValidationError: request or configuration refusal.
        RunExecutionError: any adapter or engine failure, sanitized.
    """
    sync_instance = resolve_sync_instance(sync_name, directory=config_directory)
    secrets = collect_secret_values(sync_instance)
    try:
        return execute_run(
            sync_instance,
            operation=operation,
            confirm_writes=confirm_writes,
            branch=branch,
            show_progress=False,
            potenda_factory=_potenda_factory,
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
    except ValueError as exc:
        detail = redact(str(exc), secrets)
        msg = f"Failed to initialize the Sync Instance: {detail}{_missing_credential_hint(sync_instance, detail)}"
        raise RunExecutionError(msg) from sanitize_exception_chain(exc, secrets)
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
