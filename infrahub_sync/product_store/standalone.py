"""Opt-in DB-003 product projection for standalone core callers."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, overload

from pydantic import BaseModel, ConfigDict

from infrahub_sync.cache.paths import generate_run_id
from infrahub_sync.execution import ACTION_KEYS, Operation, RunResult, collect_secret_values, execute_run
from infrahub_sync.plan.config_version import resolve_config_version
from infrahub_sync.plan.models import ApplyRecord
from infrahub_sync.plan.review import SavedPlan, read_saved_plan
from infrahub_sync.product_store.models import ProductRun
from infrahub_sync.product_store.store import DuplicateRunError, ProductProjection, local_product_projection

if TYPE_CHECKING:
    from collections.abc import Sequence

    from infrahub_sync import SyncInstance

PLAN_REVIEW_ARTIFACT_ID = "plan-review"
logger = logging.getLogger(__name__)


class SavedPlanReviewArtifact(BaseModel):
    """Transport-neutral form of the managed ``plan-review`` artifact."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    checksum: str
    checksum_ok: bool
    verification_notes: tuple[str, ...]
    summary: dict[str, Any]
    operations: tuple[dict[str, Any], ...]


class StandaloneProductRecordError(Exception):
    """A configured standalone projection cannot continue the requested run."""


def _review_document(run_id: str, saved: SavedPlan) -> SavedPlanReviewArtifact:
    return SavedPlanReviewArtifact(
        run_id=run_id,
        checksum=saved.manifest.plan_checksum,
        checksum_ok=saved.checksum_ok,
        verification_notes=tuple(saved.verification_notes),
        summary=saved.summary().model_dump(mode="json"),
        operations=tuple(operation.model_dump(mode="json") for operation in saved.operations()),
    )


def _plan_result(run_id: str, saved: SavedPlan) -> dict[str, Any]:
    summary = saved.summary()
    return {
        "run_id": run_id,
        "stage": "plan",
        "outcome": "no-change" if summary.total == 0 else "planned",
        "summary": summary.model_dump(mode="json"),
    }


def _verification_result(run_id: str, saved: SavedPlan) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "stage": "verify",
        "outcome": "verified",
        "checksum": saved.manifest.plan_checksum,
        "checksum_ok": saved.checksum_ok,
        "verification_notes": list(saved.verification_notes),
    }


def _execution_result(result: RunResult, *, operation: Operation) -> dict[str, Any]:
    return {
        "run_id": result.run_id,
        "operation": operation,
        "outcome": result.status,
        "changed": result.changed,
        "summary": {key: result.summary[key] for key in ACTION_KEYS},
    }


def _finish_execution(
    projection: ProductProjection,
    *,
    run_id: str,
    result: RunResult,
    operation: Operation,
    sync_name: str,
    secrets: Sequence[str],
) -> None:
    projection.finish_run(
        run_id,
        phase="applied",
        outcome=result.status,
        summary={"sync_name": sync_name, **{key: result.summary[key] for key in ACTION_KEYS}},
        results=_execution_result(result, operation=operation),
        secrets=secrets,
    )


def _publish_plan(
    projection: ProductProjection,
    run_id: str,
    saved: SavedPlan,
    secrets: Sequence[str],
) -> None:
    document = _review_document(run_id, saved)
    projection.publish_artifact(
        run_id,
        artifact_id=PLAN_REVIEW_ARTIFACT_ID,
        kind="saved-plan-review",
        media_type="application/json",
        data=document.model_dump_json().encode(),
        secrets=secrets,
    )


def _require_existing_run(
    projection: ProductProjection,
    *,
    run_id: str,
    sync_instance: SyncInstance,
    configuration_reference: str,
) -> ProductRun:
    stored = projection.lookup_run(run_id).value
    if stored is None:
        msg = (
            f"Configured product storage has no Sync run {run_id!r}; use the same "
            "product-cache location that recorded the plan."
        )
        raise StandaloneProductRecordError(msg)
    if stored.configuration_reference != configuration_reference:
        msg = f"Configured product record {run_id!r} does not match the current configuration fingerprint."
        raise StandaloneProductRecordError(msg)
    if stored.summary.get("sync_name") != sync_instance.name:
        msg = f"Configured product record {run_id!r} belongs to a different synchronization."
        raise StandaloneProductRecordError(msg)
    return stored


def _record_failure(
    projection: ProductProjection,
    *,
    run_id: str,
    operation: Operation,
    exc: BaseException,
    secrets: Sequence[str],
) -> None:
    evidence: dict[str, Any] = {
        "stage": operation,
        "outcome": "failed",
        "error_type": type(exc).__name__,
    }
    apply_record = getattr(exc, "apply_record", None)
    if isinstance(apply_record, ApplyRecord):
        evidence.update(apply_record.as_summary_keys())
    try:
        projection.merge_results(run_id, {f"{operation}_failure": evidence}, secrets=secrets)
        if operation == "verify":
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
            phase=f"{operation}-failed",
            outcome="failed",
            summary={**refreshed.summary, "failed_stage": operation, **partial},
            results=refreshed.results,
            secrets=secrets,
        )
    except Exception as persistence_error:  # noqa: BLE001  # pylint: disable=broad-exception-caught
        logger.warning(
            "Standalone Sync failure evidence could not be persisted (%s)",
            type(persistence_error).__name__,
        )


def _prepare_projection(
    sync_instance: SyncInstance,
    *,
    operation: Operation,
    semantic_operation: Operation,
    product_cache_location: str | Path,
    kwargs: dict[str, Any],
) -> tuple[ProductProjection, str, Sequence[str]]:
    supplied_run_id = kwargs.get("run_id")
    if supplied_run_id is None and operation in ("plan", "sync"):
        supplied_run_id = generate_run_id()
        kwargs["run_id"] = supplied_run_id
    if not isinstance(supplied_run_id, str):
        msg = f"run_id is required for configured standalone operation={operation}"
        raise StandaloneProductRecordError(msg)

    try:
        cache_location = Path(product_cache_location).expanduser()
    except RuntimeError:
        msg = f"product cache path has an unresolvable user home: {str(product_cache_location)!r}"
        raise StandaloneProductRecordError(msg) from None
    try:
        projection = local_product_projection(cache_location)
    except ValueError as exc:
        raise StandaloneProductRecordError(str(exc)) from None
    secrets = collect_secret_values(sync_instance)
    configuration_reference = resolve_config_version(sync_instance)
    if operation in ("plan", "sync"):
        try:
            projection.create_run(
                ProductRun(
                    run_id=supplied_run_id,
                    operation=semantic_operation,
                    configuration_reference=configuration_reference,
                    started_at=datetime.now(timezone.utc),
                    phase="accepted",
                    summary={"sync_name": sync_instance.name},
                ),
                secrets=secrets,
            )
        except DuplicateRunError as exc:
            msg = f"Configured product run {supplied_run_id!r} already exists; use a fresh run ID."
            raise StandaloneProductRecordError(msg) from exc
    else:
        _require_existing_run(
            projection,
            run_id=supplied_run_id,
            sync_instance=sync_instance,
            configuration_reference=configuration_reference,
        )
    return projection, supplied_run_id, secrets


@overload
def execute_standalone(
    sync_instance: SyncInstance,
    *,
    operation: Literal["verify"],
    product_cache_location: str | Path | None = ...,
    product_operation: Operation | None = ...,
    complete_plan: bool = ...,
    _core_executor: Any = ...,
    **kwargs: Any,
) -> SavedPlan: ...


@overload
def execute_standalone(
    sync_instance: SyncInstance,
    *,
    operation: Literal["plan"],
    product_cache_location: str | Path | None = ...,
    product_operation: Operation | None = ...,
    complete_plan: bool = ...,
    _core_executor: Any = ...,
    _return_saved_plan: Literal[True],
    **kwargs: Any,
) -> SavedPlan: ...


@overload
def execute_standalone(
    sync_instance: SyncInstance,
    *,
    operation: Literal["plan", "sync", "apply"],
    product_cache_location: str | Path | None = ...,
    product_operation: Operation | None = ...,
    complete_plan: bool = ...,
    _core_executor: Any = ...,
    **kwargs: Any,
) -> RunResult: ...


def execute_standalone(  # pylint: disable=too-many-branches
    sync_instance: SyncInstance,
    *,
    operation: Operation,
    product_cache_location: str | Path | None = None,
    product_operation: Operation | None = None,
    complete_plan: bool = True,
    _core_executor: Any = None,
    **kwargs: Any,
) -> RunResult | SavedPlan:
    """Run the shared core and project its lifecycle when local storage is configured.

    ``product_cache_location=None`` preserves the legacy standalone cache-only
    behavior. Managed callers do not use this adapter and retain their existing
    HTTP/Prefect lifecycle owner.
    """
    core_executor = execute_run if _core_executor is None else _core_executor
    if product_cache_location is None:
        return core_executor(sync_instance, operation=operation, **kwargs)

    semantic_operation = product_operation or operation
    projection, supplied_run_id, secrets = _prepare_projection(
        sync_instance,
        operation=operation,
        semantic_operation=semantic_operation,
        product_cache_location=product_cache_location,
        kwargs=kwargs,
    )

    plan_published = False

    def publish_committed_plan() -> None:
        nonlocal plan_published
        saved_plan = read_saved_plan(sync_name=sync_instance.name, run_id=supplied_run_id, config=sync_instance)
        _publish_plan(projection, supplied_run_id, saved_plan, secrets)
        plan_published = True

    if operation == "sync":
        kwargs["_plan_committed"] = publish_committed_plan

    try:
        result = core_executor(sync_instance, operation=operation, **kwargs)
        if operation == "plan":
            saved = (
                result
                if isinstance(result, SavedPlan)
                else read_saved_plan(sync_name=sync_instance.name, run_id=supplied_run_id, config=sync_instance)
            )
            _publish_plan(projection, supplied_run_id, saved, secrets)
            if complete_plan:
                plan_result = _plan_result(supplied_run_id, saved)
                projection.finish_run(
                    supplied_run_id,
                    phase="planned",
                    outcome=plan_result["outcome"],
                    summary={"sync_name": sync_instance.name, **saved.summary().model_dump(mode="json")},
                    results=plan_result,
                    secrets=secrets,
                )
        elif operation == "verify":
            assert isinstance(result, SavedPlan)
            if kwargs.get("_require_verified"):
                projection.merge_results(
                    supplied_run_id,
                    {"verification": _verification_result(supplied_run_id, result)},
                    secrets=secrets,
                )
        elif operation == "apply":
            assert isinstance(result, RunResult)
            _finish_execution(
                projection,
                run_id=supplied_run_id,
                result=result,
                operation=semantic_operation,
                sync_name=sync_instance.name,
                secrets=secrets,
            )
        else:
            assert isinstance(result, RunResult)
            if not plan_published:
                publish_committed_plan()
            _finish_execution(
                projection,
                run_id=supplied_run_id,
                result=result,
                operation=semantic_operation,
                sync_name=sync_instance.name,
                secrets=secrets,
            )
    except BaseException as exc:
        _record_failure(
            projection,
            run_id=supplied_run_id,
            operation=semantic_operation,
            exc=exc,
            secrets=secrets,
        )
        raise
    return result
