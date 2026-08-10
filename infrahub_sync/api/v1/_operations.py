"""Local compositions for the version 1 public Python API."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from infrahub_sync.cache.paths import generate_run_id
from infrahub_sync.execution import (
    RunValidationError as CoreRunValidationError,
)
from infrahub_sync.execution import (
    bounded_run_lock,
    collect_secret_values,
    execute_run,
    redact,
    resolve_sync_instance,
    sanitize_exception_chain,
)
from infrahub_sync.plan.errors import (
    ApplyRecordInvariantError,
    OperationApplyFailedError,
    PlanArtifactError,
)
from infrahub_sync.plan.review import (
    SavedPlan,
    read_saved_plan,
    resolve_run_directory,
)
from infrahub_sync.plan.writer import MANIFEST_FILE_NAME, OPERATIONS_FILE_NAME, PLAN_DIR_NAME
from infrahub_sync.product_store.standalone import execute_standalone

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
    from pathlib import Path

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


def _plan_instance(
    instance: SyncInstance,
    *,
    branch: str | None,
    run_id: str,
    operation: Operation,
    secrets: Sequence[str],
    product_cache_location: str | None = None,
    lock_already_held: bool = False,
) -> tuple[SavedPlan, Path]:
    """Create and retrieve one saved plan through the shared execution core."""
    _log_lifecycle(run_id=run_id, operation=operation, stage="plan", outcome="running", secrets=secrets)
    saved = execute_standalone(
        instance,
        operation="plan",
        product_cache_location=product_cache_location,
        product_operation=operation,
        complete_plan=operation == "plan",
        _core_executor=execute_run,
        branch=branch,
        run_id=run_id,
        show_progress=False,
        print_diff=False,
        _lock_already_held=lock_already_held,
        _run_file_mode="sync" if operation == "sync" else None,
        _return_saved_plan=True,
    )
    assert isinstance(saved, SavedPlan)
    run_directory = resolve_run_directory(instance.name, run_id)
    outcome = "no-change" if saved.summary().total == 0 else "planned"
    _log_lifecycle(run_id=run_id, operation=operation, stage="plan", outcome=outcome, secrets=secrets)
    return saved, run_directory


def _verify_instance(
    instance: SyncInstance,
    *,
    run_id: str,
    operation: Operation,
    secrets: Sequence[str],
    product_cache_location: str | None = None,
    lock_already_held: bool = False,
) -> tuple[SavedPlan, Path]:
    """Independently verify and retrieve a saved plan without constructing adapters."""
    _log_lifecycle(run_id=run_id, operation=operation, stage="verify", outcome="running", secrets=secrets)
    saved = execute_standalone(
        instance,
        operation="verify",
        product_cache_location=product_cache_location,
        product_operation=operation,
        _core_executor=execute_run,
        run_id=run_id,
        _lock_already_held=lock_already_held,
        _run_file_mode="sync" if operation == "sync" else None,
        _require_verified=True,
    )
    run_directory = resolve_run_directory(instance.name, run_id)
    _log_lifecycle(run_id=run_id, operation=operation, stage="verify", outcome="verified", secrets=secrets)
    return saved, run_directory


def _apply_instance(
    instance: SyncInstance,
    *,
    run_id: str,
    branch: str | None,
    expected_checksum: str,
    operation: Operation,
    secrets: Sequence[str],
    saved: SavedPlan,
    product_cache_location: str | None = None,
    lock_already_held: bool = False,
) -> SavedPlan:
    """Apply the reviewed artifact, including its immediate mandatory verification."""
    _log_lifecycle(run_id=run_id, operation=operation, stage="apply", outcome="running", secrets=secrets)
    execute_standalone(
        instance,
        operation="apply",
        product_cache_location=product_cache_location,
        product_operation=operation,
        _core_executor=execute_run,
        confirm_writes=True,
        run_id=run_id,
        branch=branch,
        expected_checksum=expected_checksum,
        _lock_already_held=lock_already_held,
        _run_file_mode="sync" if operation == "sync" else None,
    )
    _log_lifecycle(run_id=run_id, operation=operation, stage="apply", outcome="applied", secrets=secrets)
    return saved


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
            product_cache_location=request.product_cache_location,
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
            product_cache_location=request.product_cache_location,
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
        saved = read_saved_plan(sync_name=instance.name, run_id=request.run_id, config=instance)
        run_directory = resolve_run_directory(instance.name, request.run_id)
        stage = "apply"
        saved = _apply_instance(
            instance,
            run_id=request.run_id,
            branch=request.branch,
            expected_checksum=request.expected_checksum,
            operation="apply",
            secrets=secrets,
            saved=saved,
            product_cache_location=request.product_cache_location,
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
        stage = "lock"
        with bounded_run_lock(instance.name, timeout=60.0):
            stage = "plan"
            saved, run_directory = _plan_instance(
                instance,
                branch=request.branch,
                run_id=run_id,
                operation="sync",
                secrets=secrets,
                product_cache_location=request.product_cache_location,
                lock_already_held=True,
            )
            stage = "verify"
            saved, run_directory = _verify_instance(
                instance,
                run_id=run_id,
                operation="sync",
                secrets=secrets,
                product_cache_location=request.product_cache_location,
                lock_already_held=True,
            )
            stage = "apply"
            saved = _apply_instance(
                instance,
                run_id=run_id,
                branch=request.branch,
                expected_checksum=saved.manifest.plan_checksum,
                operation="sync",
                secrets=secrets,
                saved=saved,
                product_cache_location=request.product_cache_location,
                lock_already_held=True,
            )
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
