"""Separate Prefect flow for API-created durable Sync runs."""

# Deliberately no ``from __future__ import annotations``. Prefect parameter
# validation must receive the concrete Literal at runtime; the direct Prefect
# flow (``infrahub_sync/orchestration/flow.py``) documents the affected
# Prefect/Pydantic versions.

import logging
import os
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Literal

from prefect import flow, get_run_logger
from prefect.exceptions import MissingContextError

from infrahub_sync.execution import (
    ACTION_KEYS,
    RunResult,
    bounded_run_lock,
    collect_secret_values,
    execute_run,
    redact,
    resolve_sync_instance,
    sanitize_exception_chain,
)
from infrahub_sync.orchestration.flow import BRIDGED_LEVEL, SOURCE_LOGGER_NAME, RunLogger, RunLoggerBridge
from infrahub_sync.plan.models import ApplyRecord
from infrahub_sync.plan.review import SavedPlan
from infrahub_sync.product_store import ProductProjection, local_product_projection

from ._settings import PRODUCT_CACHE_ENV
from .models import PlanResource
from .orchestration import MANAGED_FLOW_NAME
from .service import PLAN_ARTIFACT_ID

CONFIG_DIR_ENV = "INFRAHUB_SYNC_CONFIG_DIRECTORY"
RUN_CACHE_ENV = "INFRAHUB_SYNC_CACHE_DIR"
logger = logging.getLogger(__name__)


def _run_logger() -> tuple[RunLogger, bool]:
    """Use Prefect logging in a run and a local logger in executor-only tests."""
    try:
        return get_run_logger(), True
    except MissingContextError:
        return logger, False


@contextmanager
def _remote_log_bridge(
    run_logger: RunLogger,
    *,
    prefect_context: bool,
    secrets: Sequence[str] = (),
) -> Iterator[None]:
    """Bridge shared logs only when a real Prefect run context owns the logger."""
    if not prefect_context:
        yield
        return
    source_logger = logging.getLogger(SOURCE_LOGGER_NAME)
    bridge = RunLoggerBridge(run_logger, secrets=secrets)
    previous_level = source_logger.level
    previous_propagate = source_logger.propagate
    source_logger.addHandler(bridge)
    source_logger.setLevel(BRIDGED_LEVEL)
    source_logger.propagate = False
    try:
        yield
    finally:
        source_logger.removeHandler(bridge)
        source_logger.setLevel(previous_level)
        source_logger.propagate = previous_propagate


def _runtime() -> tuple[str, ProductProjection]:
    config_directory = os.environ.get(CONFIG_DIR_ENV)
    product_cache = os.environ.get(PRODUCT_CACHE_ENV)
    run_cache = os.environ.get(RUN_CACHE_ENV)
    if not config_directory or not Path(config_directory).is_dir():
        msg = f"{CONFIG_DIR_ENV} must name the worker's configuration directory"
        raise RuntimeError(msg)
    if not product_cache:
        msg = f"{PRODUCT_CACHE_ENV} must name the worker's durable product cache"
        raise RuntimeError(msg)
    if not run_cache or not Path(run_cache).expanduser().is_absolute():
        msg = f"{RUN_CACHE_ENV} must name an absolute shared saved-plan cache"
        raise RuntimeError(msg)
    return config_directory, local_product_projection(Path(product_cache).expanduser())


def _review_document(run_id: str, saved: SavedPlan) -> PlanResource:
    summary = saved.summary()
    return PlanResource(
        run_id=run_id,
        checksum=saved.manifest.plan_checksum,
        checksum_ok=saved.checksum_ok,
        verification_notes=tuple(saved.verification_notes),
        summary=summary.model_dump(mode="json"),
        operations=tuple(operation.model_dump(mode="json") for operation in saved.operations()),
    )


def _publish_plan(projection: ProductProjection, run_id: str, saved: SavedPlan, secrets: Sequence[str]) -> None:
    document = _review_document(run_id, saved)
    projection.publish_artifact(
        run_id,
        artifact_id=PLAN_ARTIFACT_ID,
        kind="saved-plan-review",
        media_type="application/json",
        data=document.model_dump_json().encode(),
        secrets=secrets,
    )


def _result_data(result: RunResult) -> dict[str, Any]:
    return {
        "run_id": result.run_id,
        "operation": result.operation,
        "outcome": result.status,
        "changed": result.changed,
        "summary": {key: result.summary[key] for key in ACTION_KEYS},
    }


def _plan(
    instance: Any,
    *,
    run_id: str,
    branch: str | None,
    composed_sync: bool,
) -> SavedPlan:
    saved = execute_run(
        instance,
        operation="plan",
        branch=branch,
        run_id=run_id,
        show_progress=False,
        print_diff=False,
        _lock_already_held=composed_sync,
        _run_file_mode="sync" if composed_sync else None,
        _return_saved_plan=True,
    )
    assert isinstance(saved, SavedPlan)
    return saved


def _execute_stage(  # pylint: disable=too-many-arguments,too-many-positional-arguments,too-many-statements
    run_id: str,
    sync_name: str,
    stage: Literal["plan", "verify", "apply", "sync"],
    configuration_reference: str,
    branch: str | None,
    expected_checksum: str | None,
    confirm_writes: bool,
    run_logger: RunLogger,
    secrets: list[str],
) -> dict[str, Any]:
    """Resolve and execute one managed stage within the sanitized worker boundary."""
    config_directory, projection = _runtime()
    stored = projection.lookup_run(run_id)
    if stored.value is None:
        msg = f"API-created Sync run {run_id!r} is unavailable"
        raise RuntimeError(msg)
    if stored.value.configuration_reference != configuration_reference:
        msg = f"configuration_reference does not match Sync run {run_id!r}"
        raise ValueError(msg)
    recorded_sync_name = stored.value.summary.get("sync_name")
    if recorded_sync_name is not None and recorded_sync_name != sync_name:
        msg = f"sync_name does not match Sync run {run_id!r}"
        raise ValueError(msg)
    if stage in ("apply", "sync") and not confirm_writes:
        msg = f"confirm_writes=true is required for managed stage={stage}"
        raise ValueError(msg)
    if stage == "apply" and expected_checksum is None:
        msg = "expected_checksum is required for managed stage=apply"
        raise ValueError(msg)

    instance = resolve_sync_instance(sync_name, directory=config_directory)
    secrets[:] = collect_secret_values(instance)
    result: dict[str, Any]
    if stage == "plan":
        saved = _plan(instance, run_id=run_id, branch=branch, composed_sync=False)
        _publish_plan(projection, run_id, saved, secrets)
        summary = saved.summary()
        outcome = "no-change" if summary.total == 0 else "planned"
        result = {"run_id": run_id, "stage": stage, "outcome": outcome, "summary": summary.model_dump(mode="json")}
        projection.finish_run(
            run_id,
            phase="planned",
            outcome=outcome,
            summary={"sync_name": sync_name, **summary.model_dump(mode="json")},
            results=result,
            secrets=secrets,
        )
    elif stage == "verify":
        saved = execute_run(instance, operation="verify", run_id=run_id, _require_verified=True)
        assert isinstance(saved, SavedPlan)
        result = {
            "run_id": run_id,
            "stage": stage,
            "outcome": "verified",
            "checksum": saved.manifest.plan_checksum,
            "checksum_ok": saved.checksum_ok,
            "verification_notes": list(saved.verification_notes),
        }
        projection.merge_results(
            run_id,
            {"verification": result},
            secrets=secrets,
        )
    elif stage == "apply":
        applied = execute_run(
            instance,
            operation="apply",
            run_id=run_id,
            branch=branch,
            expected_checksum=expected_checksum,
            confirm_writes=True,
        )
        assert isinstance(applied, RunResult)
        result = _result_data(applied)
        projection.finish_run(
            run_id,
            phase="applied",
            outcome=applied.status,
            summary={"sync_name": sync_name, **{key: applied.summary[key] for key in ACTION_KEYS}},
            results=result,
            secrets=secrets,
        )
    else:
        with bounded_run_lock(instance.name, timeout=60.0):
            saved = _plan(instance, run_id=run_id, branch=branch, composed_sync=True)
            _publish_plan(projection, run_id, saved, secrets)
            verified = execute_run(
                instance,
                operation="verify",
                run_id=run_id,
                _lock_already_held=True,
                _run_file_mode="sync",
                _require_verified=True,
            )
            assert isinstance(verified, SavedPlan)
            applied = execute_run(
                instance,
                operation="apply",
                run_id=run_id,
                branch=branch,
                expected_checksum=verified.manifest.plan_checksum,
                confirm_writes=True,
                _lock_already_held=True,
                _run_file_mode="sync",
            )
            assert isinstance(applied, RunResult)
        result = {**_result_data(applied), "operation": "sync"}
        projection.finish_run(
            run_id,
            phase="applied",
            outcome=applied.status,
            summary={"sync_name": sync_name, **{key: applied.summary[key] for key in ACTION_KEYS}},
            results=result,
            secrets=secrets,
        )
    run_logger.info(redact(f"managed Sync run {run_id} stage={stage} outcome={result['outcome']}", secrets))
    return result


def _failure_evidence(
    stage: Literal["plan", "verify", "apply", "sync"],
    exc: Exception,
) -> dict[str, Any]:
    evidence: dict[str, Any] = {
        "stage": stage,
        "outcome": "failed",
        "error_type": type(exc).__name__,
    }
    apply_record = getattr(exc, "apply_record", None)
    if isinstance(apply_record, ApplyRecord):
        evidence.update(apply_record.as_summary_keys())
    return evidence


def _record_failure(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    run_id: str,
    stage: Literal["plan", "verify", "apply", "sync"],
    exc: Exception,
    run_logger: RunLogger,
    secrets: Sequence[str],
) -> None:
    """Best-effort durable product evidence without masking the worker failure."""
    try:
        _config_directory, projection = _runtime()
        stored = projection.lookup_run(run_id).value
        if stored is None:
            return
        evidence = _failure_evidence(stage, exc)
        projection.merge_results(run_id, {f"{stage}_failure": evidence}, secrets=secrets)
        if stage == "verify":
            return
        refreshed = projection.lookup_run(run_id).value
        if refreshed is None:
            return
        partial = {
            key: evidence[key]
            for key in (
                "applied_operations",
                "skipped_delete_operations",
                "skipped_delete_count",
                "failed_operation",
                "may_have_partially_written",
            )
            if key in evidence
        }
        projection.finish_run(
            run_id,
            phase=f"{stage}-failed",
            outcome="failed",
            summary={**refreshed.summary, "failed_stage": stage, **partial},
            results=refreshed.results,
            secrets=secrets,
        )
    except Exception as persistence_error:  # noqa: BLE001  # pylint: disable=broad-exception-caught
        run_logger.log(
            logging.WARNING,
            "managed Sync failure evidence could not be persisted (%s)",
            type(persistence_error).__name__,
        )


@flow(name=MANAGED_FLOW_NAME)
def managed_sync_run(  # pylint: disable=too-many-positional-arguments
    run_id: str,
    sync_name: str,
    stage: Literal["plan", "verify", "apply", "sync"],
    configuration_reference: str,
    branch: str | None = None,
    expected_checksum: str | None = None,
    confirm_writes: bool = False,
) -> dict[str, Any]:
    """Execute one API-reserved stage and publish its durable product data."""
    run_logger, prefect_context = _run_logger()
    secrets = list(collect_secret_values())
    failure: Exception | None = None
    try:
        with _remote_log_bridge(run_logger, prefect_context=prefect_context, secrets=secrets):
            return _execute_stage(
                run_id,
                sync_name,
                stage,
                configuration_reference,
                branch,
                expected_checksum,
                confirm_writes,
                run_logger,
                secrets,
            )
    except Exception as exc:  # noqa: BLE001  # pylint: disable=broad-exception-caught
        # Rebuilt after the original exception context exits.
        _record_failure(run_id, stage, exc, run_logger, secrets)
        failure = sanitize_exception_chain(exc, secrets)
    assert failure is not None
    secrets.clear()
    raise failure
