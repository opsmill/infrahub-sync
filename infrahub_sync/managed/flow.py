"""Separate Prefect flow for API-created durable Sync runs."""

# Deliberately no ``from __future__ import annotations``. Prefect parameter
# validation must receive the concrete Literal at runtime; the direct Prefect
# flow (``infrahub_sync/orchestration/flow.py``) documents the affected
# Prefect/Pydantic versions.

import logging
import os
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

import httpx
from prefect import flow, get_run_logger
from prefect.client.orchestration import get_client
from prefect.client.schemas.objects import WorkerStatus
from prefect.exceptions import MissingContextError, ObjectNotFound

from infrahub_sync.configuration import ConfigurationPackageParseError, parse_configuration_package
from infrahub_sync.configuration.runtime import resolve_runtime_instance
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
from infrahub_sync.orchestration.flow import (
    _REMOTE_LOGGER_OWNERSHIP_LOCK,
    BRIDGED_LEVEL,
    SOURCE_LOGGER_NAME,
    RunLogger,
    RunLoggerBridge,
)
from infrahub_sync.plan.config_version import resolve_config_version
from infrahub_sync.plan.errors import PlanSchemaChangedError
from infrahub_sync.plan.models import ApplyRecord, PlanManifest
from infrahub_sync.plan.reader import parse_plan_artifact, read_plan_artifact_bytes
from infrahub_sync.plan.review import SavedPlan, resolve_run_directory
from infrahub_sync.plan.verify import verify_plan
from infrahub_sync.product_store import (
    ExecutionFinishWriteback,
    ExecutionMergeWriteback,
    ExecutionWriteback,
    ProductProjection,
)
from infrahub_sync.runtime_schema import STAGE_RUNTIME_MODEL_SCOPE, RuntimeModelPlan, build_runtime_model_plan

from .liveness import LivenessPolicy
from .models import PlanResource
from .orchestration import MANAGED_FLOW_NAME
from .service import PLAN_ARTIFACT_ID
from .storage import managed_product_projection

CONFIG_DIR_ENV = "INFRAHUB_SYNC_CONFIG_DIRECTORY"
RUN_CACHE_ENV = "INFRAHUB_SYNC_CACHE_DIR"
logger = logging.getLogger(__name__)
_REGISTERED_VERSION_UNAVAILABLE = "registered configuration version is unavailable"
_REGISTERED_VERSION_INVALID = "registered configuration version is invalid"
_REGISTERED_CHECKSUM_MISMATCH = "registered configuration checksum does not match run binding"
_REGISTERED_PLAN_BINDING_MISMATCH = "registered saved plan binding does not match run binding"
_REGISTERED_PLAN_VERIFICATION_FAILED = "registered saved plan verification failed"
_REGISTERED_PLAN_CHECKSUM_MISMATCH = "registered saved plan checksum does not match the approved expected_checksum"
_WORKER_BINDING_PARAMETERS_INVALID = "managed worker configuration binding parameters must be all absent or all present"
_LEGACY_RUN_IDENTITY_UNAVAILABLE = "legacy managed run identity is unavailable"
_LEGACY_RUN_CONFIGURATION_MISMATCH = "legacy managed run configuration version does not match durable run"
_WORKER_EXECUTION_REFUSED = "managed worker execution claim was refused"
_WORKER_EXECUTION_ID_INVALID = "managed worker execution identity is invalid"
_WORKER_EXECUTION_IDENTITY_UNAVAILABLE = "managed worker execution identity is unavailable"
_WORKER_EXECUTION_WRITEBACK_REFUSED = "managed worker execution writeback was refused"
_WORKER_PAGE_SIZE = 200


def _raise_writeback_refused() -> None:
    raise RuntimeError(_WORKER_EXECUTION_WRITEBACK_REFUSED)


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
    with _REMOTE_LOGGER_OWNERSHIP_LOCK:
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


def _runtime(*, projection_factory: Any = managed_product_projection) -> tuple[str, ProductProjection]:
    config_directory = os.environ.get(CONFIG_DIR_ENV)
    run_cache = os.environ.get(RUN_CACHE_ENV)
    if not config_directory or not Path(config_directory).is_dir():
        msg = f"{CONFIG_DIR_ENV} must name the worker's configuration directory"
        raise RuntimeError(msg)
    if not run_cache or not Path(run_cache).expanduser().is_absolute():
        msg = f"{RUN_CACHE_ENV} must name an absolute shared saved-plan cache"
        raise RuntimeError(msg)
    return config_directory, projection_factory()


def _review_document(run_id: str, saved: SavedPlan) -> PlanResource:
    summary = saved.summary()
    return PlanResource(
        run_id=run_id,
        checksum=saved.manifest.plan_checksum,
        checksum_ok=saved.checksum_ok,
        verification_notes=tuple(saved.verification_notes),
        summary=summary.model_dump(mode="json"),
        operations=tuple(operation.model_dump(mode="json") for operation in saved.operations()),
        schema_fingerprint=saved.manifest.registered_schema_fingerprint,
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


def _verify_registered_apply(
    *, instance: Any, run_id: str, binding: tuple[str, int, str] | None, expected_checksum: str | None
) -> PlanManifest:
    """Verify a retained artifact before the apply path can construct a destination.

    Returns the verified manifest. Its `plan_checksum` must already be the operator's
    approved value, which is the same value the later `PlanApplier` read is given: one
    approval gates both reads, so bytes swapped between them cannot reach the write loop.
    """
    artifact = read_plan_artifact_bytes(resolve_run_directory(instance.name, run_id))
    if verify_plan(artifact=artifact, run_id=run_id, config_version=resolve_config_version(instance)):
        raise ValueError(_REGISTERED_PLAN_VERIFICATION_FAILED)
    manifest = parse_plan_artifact(artifact, run_id=run_id).manifest
    if manifest.configuration_binding != binding:
        raise ValueError(_REGISTERED_PLAN_BINDING_MISMATCH)
    if manifest.plan_checksum != expected_checksum:
        raise ValueError(_REGISTERED_PLAN_CHECKSUM_MISMATCH)
    return manifest


def _require_planned_schema(*, run_id: str, manifest: PlanManifest, models: RuntimeModelPlan | None) -> None:
    """Refuse a retained registered plan whose consumed schema semantics have changed.

    Both fingerprints come from the one canonical projection: the manifest's was written
    from the snapshot that built the plan's models, and `models` carries the one this
    stage's single live schema read produced. Compared before any adapter is constructed,
    so a refusal has read no source and written nothing. Absent values never compare equal,
    so the comparison fails closed rather than skipping.
    """
    recorded = manifest.registered_schema_fingerprint
    live = None if models is None else models.schema_fingerprint
    if recorded is not None and recorded == live:
        return
    msg = (
        f"The saved plan of run {run_id!r} was computed against destination schema semantics "
        f"{recorded!r} and this destination now reports {live!r}, so the plan's recorded "
        f"operations may no longer mean what they did when it was reviewed. Nothing was "
        f"written to the destination and no source was read."
    )
    raise PlanSchemaChangedError(msg)


def _worker_binding(
    config_id: str | None, registry_version: int | None, package_checksum: str | None
) -> tuple[str, int, str] | None:
    """Return the closed Prefect tuple carrier or refuse a partial carrier."""
    values = (config_id, registry_version, package_checksum)
    if all(value is None for value in values):
        return None
    if any(value is None for value in values):
        raise ValueError(_WORKER_BINDING_PARAMETERS_INVALID)
    assert config_id is not None
    assert registry_version is not None
    assert package_checksum is not None
    return config_id, registry_version, package_checksum


def _canonical_uuid(value: object) -> str | None:
    """Return the canonical text of a UUID object or already-canonical UUID string."""
    if isinstance(value, UUID):
        return str(value)
    if not isinstance(value, str):
        return None
    try:
        return value if str(UUID(value)) == value else None
    except ValueError:
        return None


def _require_worker_page(value: object) -> list[Any]:
    if not isinstance(value, list):
        raise TypeError
    return value


def _require_work_pool_binding(flow_run: object) -> tuple[str, str]:
    work_pool_name = getattr(flow_run, "work_pool_name", None)
    work_pool_id = _canonical_uuid(getattr(flow_run, "work_pool_id", None))
    if not isinstance(work_pool_name, str) or not work_pool_name or work_pool_id is None:
        raise ValueError
    return work_pool_name, work_pool_id


def _require_current_worker_identity(flow_run_id: str, worker_id: str) -> None:
    """Require the child's worker UUID to remain canonical immediately before claim."""
    worker_name = os.environ.get("PREFECT__WORKER_NAME")
    if not worker_name:
        raise RuntimeError(_WORKER_EXECUTION_IDENTITY_UNAVAILABLE)
    try:
        client = get_client(sync_client=True)
        with client:
            flow_run = client.read_flow_run(UUID(flow_run_id))
            work_pool_name, work_pool_id = _require_work_pool_binding(flow_run)
            records: list[Any] = []
            offset = 0
            while True:
                page = _require_worker_page(
                    client.read_workers_for_work_pool(
                        work_pool_name,
                        offset=offset,
                        limit=_WORKER_PAGE_SIZE,
                    )
                )
                records.extend(page)
                if len(page) < _WORKER_PAGE_SIZE:
                    break
                offset += _WORKER_PAGE_SIZE
    except (httpx.HTTPError, ObjectNotFound, AttributeError, RuntimeError, TypeError, ValueError):
        raise RuntimeError(_WORKER_EXECUTION_IDENTITY_UNAVAILABLE) from None

    matches = [record for record in records if record.name == worker_name]
    if len(matches) != 1:
        raise RuntimeError(_WORKER_EXECUTION_IDENTITY_UNAVAILABLE)
    record = matches[0]
    if (
        _canonical_uuid(record.id) != worker_id
        or _canonical_uuid(record.work_pool_id) != work_pool_id
        or record.status != WorkerStatus.ONLINE
    ):
        raise RuntimeError(_WORKER_EXECUTION_IDENTITY_UNAVAILABLE)


def _prefect_flow_run_id() -> str:
    """Read the flow-run UUID from Prefect's process-local runtime context."""
    from prefect.runtime import flow_run  # pylint: disable=import-outside-toplevel

    flow_run_id = _canonical_uuid(flow_run.id)
    if flow_run_id is None:
        raise RuntimeError(_WORKER_EXECUTION_ID_INVALID)
    return flow_run_id


def _claim_current_execution(projection: ProductProjection, run_id: str) -> tuple[str, str]:
    """Claim this Prefect execution before any configuration or adapter work begins."""
    worker_id = _canonical_uuid(os.environ.get("PREFECT__WORKER_ID"))
    flow_run_id = _prefect_flow_run_id()
    if worker_id is None:
        raise RuntimeError(_WORKER_EXECUTION_ID_INVALID)
    _require_current_worker_identity(flow_run_id, worker_id)
    admission_ttl_seconds = LivenessPolicy.from_environment().admission_ttl_seconds
    if not projection.claim_execution(
        run_id,
        flow_run_id,
        worker_id=worker_id,
        admission_ttl_seconds=admission_ttl_seconds,
    ):
        raise RuntimeError(_WORKER_EXECUTION_REFUSED)
    return flow_run_id, worker_id


def _worker_execution_context(
    run_id: str,
    binding: tuple[str, int, str] | None,
    *,
    config_directory: str,
    projection: ProductProjection,
    run_branch: str | None,
    stage: str,
    build_models: bool = True,
) -> tuple[ProductProjection, Any, str]:
    """Load the durable run and resolve its registered or legacy runtime.

    A registered run builds its runtime model plan here, from one destination schema read,
    before any adapter is constructed or any source is extracted. A saved-plan apply defers
    that build until its artifact binding is verified. What the plan covers follows the stage:
    both sides for plan and sync, the destination only for a saved-plan apply, and nothing at
    all for verify, which constructs no adapter. The legacy path keeps its generated-code
    resolution and builds no plan.
    """
    stored = projection.lookup_run(run_id)
    if stored.value is None:
        msg = f"API-created Sync run {run_id!r} is unavailable"
        raise RuntimeError(msg)
    if stored.value.configuration_binding != binding:
        msg = "managed run binding does not match worker parameters"
        raise ValueError(msg)
    if binding is None:
        sync_name = stored.value.summary.get("sync_name")
        if not isinstance(sync_name, str) or not sync_name:
            raise ValueError(_LEGACY_RUN_IDENTITY_UNAVAILABLE)
        instance = resolve_sync_instance(sync_name, directory=config_directory)
        if resolve_config_version(instance) != stored.value.configuration_reference:
            raise ValueError(_LEGACY_RUN_CONFIGURATION_MISMATCH)
        return projection, instance, sync_name

    config_id, registry_version, package_checksum = binding
    registered = projection.lookup_configuration_version(config_id, registry_version).value
    if registered is None:
        raise ValueError(_REGISTERED_VERSION_UNAVAILABLE)
    try:
        package = parse_configuration_package(registered.declared_content)
    except ConfigurationPackageParseError:
        raise ValueError(_REGISTERED_VERSION_INVALID) from None
    if registered.package_checksum != package_checksum or package.checksum() != package_checksum:
        raise ValueError(_REGISTERED_CHECKSUM_MISMATCH)
    instance = resolve_runtime_instance(package, directory=config_directory)
    instance._configuration_binding = binding
    scope = STAGE_RUNTIME_MODEL_SCOPE.get(stage)
    if scope is not None and build_models:
        instance._runtime_models = build_runtime_model_plan(
            package=package, instance=instance, run_branch=run_branch, scope=scope
        )
    return projection, instance, package.configuration.name


def _execute_stage(  # pylint: disable=too-many-arguments,too-many-positional-arguments,too-many-statements
    run_id: str,
    stage: Literal["plan", "verify", "apply", "sync"],
    config_id: str | None,
    registry_version: int | None,
    package_checksum: str | None,
    branch: str | None,
    expected_checksum: str | None,
    *,
    confirm_writes: bool,
    run_logger: RunLogger,
    secrets: list[str],
    config_directory: str,
    projection: ProductProjection,
) -> tuple[dict[str, Any], ExecutionWriteback]:
    """Resolve and execute one managed stage within the sanitized worker boundary."""
    parameter_binding = _worker_binding(config_id, registry_version, package_checksum)
    projection, instance, sync_name = _worker_execution_context(
        run_id,
        parameter_binding,
        config_directory=config_directory,
        projection=projection,
        run_branch=branch,
        stage=stage,
        build_models=stage != "apply",
    )
    if stage in ("apply", "sync") and not confirm_writes:
        msg = f"confirm_writes=true is required for managed stage={stage}"
        raise ValueError(msg)
    if stage == "apply" and expected_checksum is None:
        msg = "expected_checksum is required for managed stage=apply"
        raise ValueError(msg)

    secrets[:] = collect_secret_values(instance)
    result: dict[str, Any]
    if stage == "plan":
        saved = _plan(instance, run_id=run_id, branch=branch, composed_sync=False)
        _publish_plan(projection, run_id, saved, secrets)
        summary = saved.summary()
        outcome = "no-change" if summary.total == 0 else "planned"
        result = {"run_id": run_id, "stage": stage, "outcome": outcome, "summary": summary.model_dump(mode="json")}
        writeback: ExecutionWriteback = ExecutionFinishWriteback(
            phase="planned",
            outcome=outcome,
            finished_at=datetime.now(timezone.utc),
            summary={"sync_name": sync_name, **summary.model_dump(mode="json")},
            results=result,
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
        writeback = ExecutionMergeWriteback(results={"verification": result})
    elif stage == "apply":
        manifest = _verify_registered_apply(
            instance=instance,
            run_id=run_id,
            binding=parameter_binding,
            expected_checksum=expected_checksum,
        )
        if parameter_binding is not None:
            # The live schema read, and the comparison it exists for, both before any
            # adapter is constructed: the registered pre-write gate.
            _, instance, sync_name = _worker_execution_context(
                run_id,
                parameter_binding,
                config_directory=config_directory,
                projection=projection,
                run_branch=branch,
                stage=stage,
            )
            _require_planned_schema(run_id=run_id, manifest=manifest, models=instance._runtime_models)
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
        writeback = ExecutionFinishWriteback(
            phase="applied",
            outcome=applied.status,
            finished_at=datetime.now(timezone.utc),
            summary={"sync_name": sync_name, **{key: applied.summary[key] for key in ACTION_KEYS}},
            results=result,
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
            manifest = _verify_registered_apply(
                instance=instance,
                run_id=run_id,
                binding=parameter_binding,
                expected_checksum=verified.manifest.plan_checksum,
            )
            if parameter_binding is not None:
                _require_planned_schema(run_id=run_id, manifest=manifest, models=instance._runtime_models)
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
        writeback = ExecutionFinishWriteback(
            phase="applied",
            outcome=applied.status,
            finished_at=datetime.now(timezone.utc),
            summary={"sync_name": sync_name, **{key: applied.summary[key] for key in ACTION_KEYS}},
            results=result,
        )
    run_logger.info(redact(f"managed Sync run {run_id} stage={stage} outcome={result['outcome']}", secrets))
    return result, writeback


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
    projection: ProductProjection,
    flow_run_id: str,
    worker_id: str,
) -> None:
    """Best-effort durable product evidence without masking the worker failure."""
    try:
        stored = projection.lookup_run(run_id).value
        if stored is None:
            return
        evidence = _failure_evidence(stage, exc)
        results: dict[str, Any] = {f"{stage}_failure": evidence}
        if stage == "verify":
            writeback: ExecutionWriteback = ExecutionMergeWriteback(results=results)
        else:
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
            writeback = ExecutionFinishWriteback(
                phase=f"{stage}-failed",
                outcome="failed",
                finished_at=datetime.now(timezone.utc),
                summary={**stored.summary, "failed_stage": stage, **partial},
                results={**stored.results, **results},
            )
        projection.commit_claimed_execution(
            run_id,
            flow_run_id,
            worker_id=worker_id,
            terminal_at=datetime.now(timezone.utc),
            terminal_state="failed",
            terminal_outcome="failed",
            writeback=writeback,
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
    stage: Literal["plan", "verify", "apply", "sync"],
    config_id: str | None = None,
    registry_version: int | None = None,
    package_checksum: str | None = None,
    branch: str | None = None,
    expected_checksum: str | None = None,
    confirm_writes: bool = False,
) -> dict[str, Any]:
    """Execute one API-reserved stage and publish its durable product data."""
    run_logger, prefect_context = _run_logger()
    secrets: list[str] = []
    failure: Exception | None = None
    config_directory, projection = _runtime()
    flow_run_id, worker_id = _claim_current_execution(projection, run_id)
    secrets[:] = collect_secret_values()
    try:
        with _remote_log_bridge(run_logger, prefect_context=prefect_context, secrets=secrets):
            result, writeback = _execute_stage(
                run_id,
                stage,
                config_id,
                registry_version,
                package_checksum,
                branch,
                expected_checksum,
                confirm_writes=confirm_writes,
                run_logger=run_logger,
                secrets=secrets,
                config_directory=config_directory,
                projection=projection,
            )
    except Exception as exc:  # noqa: BLE001  # pylint: disable=broad-exception-caught
        # Rebuilt after the original exception context exits.
        _record_failure(run_id, stage, exc, run_logger, secrets, projection, flow_run_id, worker_id)
        failure = sanitize_exception_chain(exc, secrets)
    if failure is not None:
        secrets.clear()
        raise failure

    persistence_failure: Exception | None = None
    try:
        if not projection.commit_claimed_execution(
            run_id,
            flow_run_id,
            worker_id=worker_id,
            terminal_at=datetime.now(timezone.utc),
            terminal_state="completed",
            terminal_outcome="succeeded",
            writeback=writeback,
            secrets=secrets,
        ):
            _raise_writeback_refused()
    except Exception as exc:  # noqa: BLE001  # pylint: disable=broad-exception-caught
        try:
            stored = projection.lookup_run(run_id).value
            link = (
                None
                if stored is None
                else next(
                    (candidate for candidate in stored.prefect_executions if candidate.flow_run_id == flow_run_id),
                    None,
                )
            )
        except Exception:  # noqa: BLE001  # pylint: disable=broad-exception-caught
            link = None
        if link is None or (link.terminal_state, link.terminal_outcome) != ("completed", "succeeded"):
            persistence_failure = sanitize_exception_chain(exc, secrets)
    if persistence_failure is not None:
        secrets.clear()
        raise persistence_failure
    return result
