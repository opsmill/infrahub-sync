"""Local compositions for the version 1 public Python API."""

from __future__ import annotations

import logging
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from infrahub_sync.cache.locks import pipeline_lock
from infrahub_sync.cache.paths import generate_run_id
from infrahub_sync.cache.sidecars import RunFile
from infrahub_sync.execution import (
    RunValidationError as CoreRunValidationError,
)
from infrahub_sync.execution import (
    collect_secret_values,
    execute_run,
    redact,
    resolve_sync_instance,
    sanitize_exception_chain,
)
from infrahub_sync.plan.config_version import resolve_config_version
from infrahub_sync.plan.errors import (
    ApplyRecordInvariantError,
    OperationApplyFailedError,
    PlanArtifactError,
    PlanVerificationError,
)
from infrahub_sync.plan.models import ApplyRecord
from infrahub_sync.plan.reader import RawPlanArtifact, parse_plan_artifact, read_plan_artifact_bytes
from infrahub_sync.plan.review import (
    SavedPlan,
    expected_checksum_refusal,
    read_saved_plan,
    require_stored_run,
)
from infrahub_sync.plan.verify import verify_plan
from infrahub_sync.plan.writer import MANIFEST_FILE_NAME, OPERATIONS_FILE_NAME, PLAN_DIR_NAME
from infrahub_sync.utils import PlanApplier

from ._models import (
    ApplyRequest,
    LifecycleEvent,
    Operation,
    PlanRequest,
    RunError,
    RunExecutionError,
    RunResult,
    RunValidationError,
    SyncRequest,
    VerifyRequest,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from infrahub_sync import SyncInstance

logger = logging.getLogger(__name__)


def _log_lifecycle(
    *,
    run_id: str,
    operation: Operation,
    stage: str,
    outcome: str,
    secrets: Sequence[str] = (),
) -> None:
    """Emit one lifecycle boundary through standard-library logging."""
    run_id = redact(run_id, secrets)
    event = LifecycleEvent(run_id=run_id, operation=operation, stage=stage, outcome=outcome)
    logger.info(
        "Sync run %s operation=%s stage=%s outcome=%s",
        run_id,
        operation,
        stage,
        outcome,
        extra=event.model_dump(),
    )


def _translate_error(
    exc: Exception,
    *,
    operation: Operation,
    stage: str,
    run_id: str | None,
    secrets: Sequence[str],
) -> RunError:
    """Build one typed, secret-safe public error with a sanitized cause chain."""
    safe_run_id = None if run_id is None else redact(run_id, secrets)
    execution_failure = isinstance(exc, (OperationApplyFailedError, ApplyRecordInvariantError))
    error_type = (
        RunValidationError
        if isinstance(exc, (CoreRunValidationError, PlanArtifactError)) and not execution_failure
        else RunExecutionError
    )
    public = error_type(
        f"{type(exc).__name__}: {exc}",
        operation=operation,
        stage=stage,
        run_id=safe_run_id,
        secrets=secrets,
    )
    if safe_run_id is not None:
        _log_lifecycle(run_id=safe_run_id, operation=operation, stage=stage, outcome="failed", secrets=secrets)
    public.__cause__ = sanitize_exception_chain(exc, secrets)
    public.__suppress_context__ = True
    return public


def _load_instance(sync_name: str, config_directory: str) -> tuple[SyncInstance, tuple[str, ...]]:
    """Load one configuration and collect its public-boundary redaction values."""
    instance = resolve_sync_instance(sync_name, directory=config_directory)
    return instance, collect_secret_values(instance)


def _result(
    *,
    saved: SavedPlan,
    operation: Operation,
    outcome: str,
    run_directory: Path,
    secrets: Sequence[str],
) -> RunResult:
    """Build the common public result from the authoritative saved plan."""
    summary = saved.summary()
    plan_directory = run_directory / PLAN_DIR_NAME
    redacted = {
        "run_id": saved.manifest.run_id,
        "operation": operation,
        "phase": "completed",
        "outcome": outcome,
        "counts": {
            "create": summary.by_action.get("create", 0),
            "update": summary.by_action.get("update", 0),
            "delete": summary.by_action.get("delete", 0),
        },
        "domain_summary": dict(summary.by_kind),
        "artifacts": (
            {"kind": "run-directory", "path": str(run_directory)},
            {"kind": "plan-manifest", "path": str(plan_directory / MANIFEST_FILE_NAME)},
            {"kind": "plan-operations", "path": str(plan_directory / OPERATIONS_FILE_NAME)},
        ),
    }
    return RunResult.model_validate(redacted)._with_secret_values(secrets)


def _transition_run_sidecar(
    run_directory: Path,
    *,
    operation: Literal["apply", "sync"],
    status: Literal["running", "applied", "failed"],
    record: ApplyRecord | None = None,
) -> None:
    """Persist one apply-capable run transition without replacing prior summary data."""
    run_file = RunFile.load_or_default(run_directory / "run.json")
    run_file.mode = operation
    run_file.status = status
    if record is not None:
        run_file.summary.update(record.as_summary_keys())
    run_file.finished_at = None if status == "running" else datetime.now(timezone.utc).isoformat()
    run_file.save()


def _ensure_failed_run_sidecar(
    run_directory: Path,
    *,
    operation: Literal["apply", "sync"],
    record: ApplyRecord | None = None,
) -> None:
    """Persist a terminal failure, retrying one transient sidecar transition error."""
    first_error: Exception | None = None
    for _attempt in range(2):
        try:
            _transition_run_sidecar(run_directory, operation=operation, status="failed", record=record)
        except Exception as exc:  # noqa: BLE001  # pylint: disable=broad-exception-caught
            first_error = exc
        else:
            return
    assert first_error is not None
    raise first_error


def _plan_instance(
    instance: SyncInstance,
    *,
    branch: str | None,
    run_id: str,
    operation: Operation,
    secrets: Sequence[str],
    lock_already_held: bool = False,
) -> tuple[SavedPlan, Path]:
    """Create and retrieve one saved plan through the shared execution core."""
    _log_lifecycle(run_id=run_id, operation=operation, stage="plan", outcome="running", secrets=secrets)
    core_result = execute_run(
        instance,
        operation="plan",
        branch=branch,
        run_id=run_id,
        show_progress=False,
        print_diff=False,
        _lock_already_held=lock_already_held,
        _run_file_mode="sync" if operation == "sync" else None,
    )
    run_directory = Path(core_result.artifact_path)
    saved = read_saved_plan(sync_name=instance.name, run_id=core_result.run_id, config=instance)
    outcome = "no-change" if saved.summary().total == 0 else "planned"
    _log_lifecycle(run_id=run_id, operation=operation, stage="plan", outcome=outcome, secrets=secrets)
    return saved, run_directory


def _verify_instance(
    instance: SyncInstance,
    *,
    run_id: str,
    operation: Operation,
    secrets: Sequence[str],
) -> tuple[SavedPlan, Path]:
    """Independently verify and retrieve a saved plan without constructing adapters."""
    _log_lifecycle(run_id=run_id, operation=operation, stage="verify", outcome="running", secrets=secrets)
    saved = read_saved_plan(sync_name=instance.name, run_id=run_id, config=instance)
    run_directory = require_stored_run(instance.name, run_id)
    artifact = read_plan_artifact_bytes(run_directory)
    failures = verify_plan(
        artifact=artifact,
        run_id=run_id,
        config_version=resolve_config_version(instance),
    )
    if failures:
        checks = ", ".join(failure.check for failure in failures)
        detail = "; ".join(
            f"{failure.check}: expected {failure.expected}; found {failure.found}" for failure in failures
        )
        next_actions = tuple(dict.fromkeys(failure.next_action for failure in failures))
        msg = f"Saved plan {run_id!r} failed verification checks: {checks}. {detail}"
        raise PlanVerificationError(msg, next_action=" ".join(next_actions))
    _log_lifecycle(run_id=run_id, operation=operation, stage="verify", outcome="verified", secrets=secrets)
    return saved, run_directory


def _apply_instance(
    instance: SyncInstance,
    *,
    run_directory: Path,
    run_id: str,
    branch: str | None,
    expected_checksum: str,
    operation: Operation,
    secrets: Sequence[str],
    lock_already_held: bool = False,
) -> tuple[ApplyRecord, SavedPlan]:
    """Apply the reviewed artifact, including its immediate mandatory verification."""
    _log_lifecycle(run_id=run_id, operation=operation, stage="apply", outcome="running", secrets=secrets)
    lock_scope = nullcontext() if lock_already_held else pipeline_lock(instance.name)
    with lock_scope:
        sidecar_operation: Literal["apply", "sync"] = "sync" if operation == "sync" else "apply"
        artifact: RawPlanArtifact = read_plan_artifact_bytes(run_directory)
        refusal = expected_checksum_refusal(
            artifact=artifact,
            run_id=run_id,
            expected=expected_checksum,
        )
        if refusal is not None:
            raise PlanVerificationError(
                refusal.reason,
                next_action=refusal.next_action,
            )
        loaded = parse_plan_artifact(artifact, run_id=run_id)
        saved = SavedPlan(
            manifest=loaded.manifest,
            operations=loaded.operations,
            checksum_ok=True,
            verification_notes=(),
            declared_kinds=(entry.name for entry in instance.schema_mapping),
        )
        applier = PlanApplier.open_existing(instance, run_id=run_id, branch=branch)
        _transition_run_sidecar(run_directory, operation=sidecar_operation, status="running")
        try:
            record = applier.apply_plan(expected_checksum=expected_checksum, artifact=artifact)
            _transition_run_sidecar(
                run_directory,
                operation=sidecar_operation,
                status="applied",
                record=record,
            )
        except BaseException as exc:
            carried = getattr(exc, "apply_record", None)
            partial = carried if isinstance(carried, ApplyRecord) else ApplyRecord()
            _ensure_failed_run_sidecar(
                run_directory,
                operation=sidecar_operation,
                record=partial,
            )
            raise
    _log_lifecycle(run_id=run_id, operation=operation, stage="apply", outcome="applied", secrets=secrets)
    return record, saved


def plan(request: PlanRequest) -> RunResult:
    """Create, retrieve, and return one saved local plan."""
    run_id = generate_run_id()
    stage = "configuration"
    secrets = collect_secret_values()
    try:
        instance, secrets = _load_instance(request.sync_name, request.config_directory)
        stage = "plan"
        saved, run_directory = _plan_instance(
            instance,
            branch=request.branch,
            run_id=run_id,
            operation="plan",
            secrets=secrets,
        )
        outcome = "no-change" if saved.summary().total == 0 else "planned"
        return _result(
            saved=saved,
            operation="plan",
            outcome=outcome,
            run_directory=run_directory,
            secrets=secrets,
        )
    except Exception as exc:  # noqa: BLE001  # pylint: disable=broad-exception-caught
        # Public boundary: every adapter/engine failure is re-raised typed with a sanitized cause.
        error = _translate_error(exc, operation="plan", stage=stage, run_id=run_id, secrets=secrets)
    raise error from error.__cause__


def verify(request: VerifyRequest) -> RunResult:
    """Verify an existing saved plan without constructing or writing through adapters."""
    stage = "configuration"
    secrets = collect_secret_values()
    try:
        instance, secrets = _load_instance(request.sync_name, request.config_directory)
        stage = "verify"
        saved, run_directory = _verify_instance(
            instance,
            run_id=request.run_id,
            operation="verify",
            secrets=secrets,
        )
        return _result(
            saved=saved,
            operation="verify",
            outcome="verified",
            run_directory=run_directory,
            secrets=secrets,
        )
    except Exception as exc:  # noqa: BLE001  # pylint: disable=broad-exception-caught
        # Public boundary: every adapter/engine failure is re-raised typed with a sanitized cause.
        error = _translate_error(exc, operation="verify", stage=stage, run_id=request.run_id, secrets=secrets)
    raise error from error.__cause__


def apply(request: ApplyRequest) -> RunResult:
    """Apply exactly the saved plan identified by a reviewed checksum."""
    stage = "configuration"
    secrets = collect_secret_values()
    try:
        instance, secrets = _load_instance(request.sync_name, request.config_directory)
        stage = "read-plan"
        run_directory = require_stored_run(instance.name, request.run_id)
        stage = "apply"
        _record, saved = _apply_instance(
            instance,
            run_directory=run_directory,
            run_id=request.run_id,
            branch=request.branch,
            expected_checksum=request.expected_checksum,
            operation="apply",
            secrets=secrets,
        )
        outcome = "no-change" if saved.summary().total == 0 else "applied"
        return _result(
            saved=saved,
            operation="apply",
            outcome=outcome,
            run_directory=run_directory,
            secrets=secrets,
        )
    except Exception as exc:  # noqa: BLE001  # pylint: disable=broad-exception-caught
        # Public boundary: every adapter/engine failure is re-raised typed with a sanitized cause.
        error = _translate_error(exc, operation="apply", stage=stage, run_id=request.run_id, secrets=secrets)
    raise error from error.__cause__


def sync(request: SyncRequest) -> RunResult:
    """Create, verify, and apply one plan after explicit write confirmation."""
    if not request.confirm_writes:
        msg = "confirm_writes=true is required to run operation=sync"
        raise RunValidationError(msg, operation="sync", stage="confirmation", run_id=None)

    run_id = generate_run_id()
    stage = "configuration"
    secrets = collect_secret_values()
    run_directory: Path | None = None
    try:
        instance, secrets = _load_instance(request.sync_name, request.config_directory)
        stage = "plan"
        with pipeline_lock(instance.name):
            try:
                saved, run_directory = _plan_instance(
                    instance,
                    branch=request.branch,
                    run_id=run_id,
                    operation="sync",
                    secrets=secrets,
                    lock_already_held=True,
                )
                _transition_run_sidecar(run_directory, operation="sync", status="running")
                stage = "verify"
                saved, run_directory = _verify_instance(instance, run_id=run_id, operation="sync", secrets=secrets)
                stage = "apply"
                _record, saved = _apply_instance(
                    instance,
                    run_directory=run_directory,
                    run_id=run_id,
                    branch=request.branch,
                    expected_checksum=saved.manifest.plan_checksum,
                    operation="sync",
                    secrets=secrets,
                    lock_already_held=True,
                )
            except Exception:
                if run_directory is not None:
                    _ensure_failed_run_sidecar(run_directory, operation="sync")
                raise
        outcome = "no-change" if saved.summary().total == 0 else "applied"
        _log_lifecycle(run_id=run_id, operation="sync", stage="completed", outcome=outcome, secrets=secrets)
        return _result(
            saved=saved,
            operation="sync",
            outcome=outcome,
            run_directory=run_directory,
            secrets=secrets,
        )
    except Exception as exc:  # noqa: BLE001  # pylint: disable=broad-exception-caught
        # Public boundary: every adapter/engine failure is re-raised typed with a sanitized cause.
        error = _translate_error(exc, operation="sync", stage=stage, run_id=run_id, secrets=secrets)
    raise error from error.__cause__
