"""Separate Prefect flow for API-created durable Sync runs."""

# pylint: disable=too-many-lines  # one module owns the worker's claim, stage execution and run reporting

# Deliberately no ``from __future__ import annotations``. Prefect parameter
# validation must receive the concrete Literal at runtime; the direct Prefect
# flow (``infrahub_sync/orchestration/flow.py``) documents the affected
# Prefect/Pydantic versions.

import logging
import os
import re
from collections.abc import Iterator, Mapping, Sequence
from contextlib import AbstractContextManager, contextmanager, suppress
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, NoReturn
from uuid import UUID

import httpx
from prefect import flow, get_run_logger
from prefect.artifacts import create_markdown_artifact
from prefect.client.orchestration import get_client
from prefect.client.schemas.objects import WorkerStatus
from prefect.exceptions import MissingContextError, ObjectNotFound
from prefect.runtime import flow_run as current_flow_run
from prefect.states import Failed, State
from pydantic_core import to_json

from infrahub_sync.client.models import RECOVERY_NEXT_ACTIONS, RecoveryAction
from infrahub_sync.configuration import ConfigurationPackageParseError, parse_configuration_package
from infrahub_sync.configuration.runtime import resolve_runtime_instance
from infrahub_sync.configuration.storage import (
    UNSUPPORTED_STORE_MESSAGE,
    UNSUPPORTED_STORE_REASON,
    UnsupportedSyncStoreError,
)
from infrahub_sync.execution import (
    ACTION_KEYS,
    RunResult,
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
from infrahub_sync.plan.errors import PlanSchemaChangedError, PlanVerificationError
from infrahub_sync.plan.models import ApplyRecord, PlanManifest
from infrahub_sync.plan.ownership import ProvenWriteOwnership, WriteDispatchTracker
from infrahub_sync.plan.reader import parse_plan_artifact, read_plan_artifact_bytes
from infrahub_sync.plan.review import SavedPlan, resolve_run_directory
from infrahub_sync.plan.verify import (
    RE_PLAN_NEXT_ACTION,
    UNSUPPORTED_FORMAT_NEXT_ACTION,
    failure_guidance,
    verify_plan,
)
from infrahub_sync.product_store import (
    AuditEvent,
    BaselineWriteback,
    ExecutionFinishWriteback,
    ExecutionMergeWriteback,
    ExecutionWriteback,
    ProductProjection,
    ProductRun,
)
from infrahub_sync.runtime_schema import STAGE_RUNTIME_MODEL_SCOPE, RuntimeModelPlan, build_runtime_model_plan

from .apply_guard import ApplyGuard, hold_apply_guard
from .checkpoints import publish_final_checkpoint, publish_plan_checkpoint, rehydrate_plan_checkpoint
from .events import run_event, stage_result_type
from .failure_cause import FAILURE_RECORD_KEYS, apply_progress, failure_record, operator_cause
from .liveness import LivenessPolicy
from .models import PlanResource
from .orchestration import SERVICE_FLOW_NAME, run_name
from .read_progress import StoreSideReadObserver, record_waiting
from .run_artifact import publish_run_artifact
from .scratch import StageScratch, stage_scratch
from .service import PLAN_ARTIFACT_ID, PLAN_REPORT_KEY
from .storage import service_guard_secrets, service_guard_session, service_product_projection

CONFIG_DIR_ENV = "INFRAHUB_SYNC_CONFIG_DIRECTORY"
logger = logging.getLogger(__name__)
_REGISTERED_VERSION_UNAVAILABLE = "registered configuration version is unavailable"
_REGISTERED_VERSION_INVALID = "registered configuration version is invalid"
_REGISTERED_CHECKSUM_MISMATCH = "registered configuration checksum does not match run binding"
_REGISTERED_PLAN_BINDING_MISMATCH = "registered saved plan binding does not match run binding"
_REGISTERED_PLAN_VERIFICATION_FAILED = "registered saved plan verification failed"
# The outcome of a `sync` that stopped after a plan that leaves out records (spec 019 FR-012).
LEFT_OUT_STOP_OUTCOME = "left-out-review-required"
_REGISTERED_PLAN_CHECKSUM_MISMATCH = "registered saved plan checksum does not match the approved expected_checksum"
_WORKER_BINDING_PARAMETERS_INVALID = "service worker configuration binding parameters must be all absent or all present"
_LEGACY_RUN_IDENTITY_UNAVAILABLE = "legacy service run identity is unavailable"
_LEGACY_CONFIGURATION_DIRECTORY_UNAVAILABLE = (
    f"{CONFIG_DIR_ENV} must name the directory an unregistered service run resolves its configuration from"
)
_LEGACY_RUN_CONFIGURATION_MISMATCH = "legacy service run configuration version does not match durable run"
_WORKER_EXECUTION_REFUSED = "service worker execution claim was refused"
# Fixed text with the run ID as its only value, so the refusal echoes nothing else.
_WORKER_EXECUTION_REFUSED_GUIDANCE = (
    "this Prefect flow run is not an execution the Sync API submitted for run {run_id} and is waiting for. "
    "A Prefect Retry, a run started by hand from the deployment, and an execution whose admission expired "
    "are refused. Nothing was read or written. A Sync run is not replayed from Prefect: check it with "
    "`infrahub-sync runs show {run_id}`, then start a new run with `infrahub-sync plan` or "
    "`infrahub-sync sync`, or apply a reviewed plan with "
    "`infrahub-sync apply {run_id} --expected-checksum <checksum>`."
)
# The form of a product run ID (`ProductRun.run_id`). A Prefect run started by hand can pass any
# text as `run_id`, so the refusal names only an ID of this form and a placeholder otherwise.
_RUN_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_UNNAMED_RUN_ID = "<run-id>"
_WORKER_EXECUTION_ID_INVALID = "service worker execution identity is invalid"
_WORKER_EXECUTION_IDENTITY_UNAVAILABLE = "service worker execution identity is unavailable"
_WORKER_EXECUTION_WRITEBACK_REFUSED = "service worker execution writeback was refused"
_UNREGISTERED_WRITE_REFUSED = (
    "a managed write requires a complete registered configuration binding, because the write "
    "guard serializes writers per registered configuration"
)
_WORKER_PAGE_SIZE = 200
# A registered run resolves both adapters from installed code and reads nothing from a
# filesystem, so its runtime instance names no generated-code root. Empty is the value
# `import_adapter` already reads as "there is no such root", not a placeholder path.
_NO_CONFIGURATION_DIRECTORY = ""
# The stages the managed write scope covers. A read stage has nothing to be uncertain
# about, so it keeps its own deliberate handling where the two differ.
_WRITE_STAGES = ("apply", "sync")


class RegisteredPlanVerificationError(PlanVerificationError, ValueError):
    """A managed plan refusal with a fixed, safe recovery signal for the CLI.

    `next_action` is the fixed text for `recovery_action`. Without a recovery signal it is
    `guidance`: each failed check's name and its fixed next action, the text the message is
    built from, which echoes no manifest value.
    """

    def __init__(self, message: str, recovery_action: RecoveryAction | None, *, guidance: str) -> None:
        super().__init__(
            message, next_action=guidance if recovery_action is None else RECOVERY_NEXT_ACTIONS[recovery_action]
        )
        self.recovery_action = recovery_action
        # The message already carries the guidance, so the exception's text stays the message
        # alone rather than the base class's message followed by its next action.
        self.args = (message,)


class ExecutionClaimRefusedError(RuntimeError):
    """The worker refused to claim a Prefect flow run the Sync API is not waiting for.

    The message is built from fixed text and the run ID only, and tells the operator how to
    recover the Sync run without replaying it from Prefect. A run ID that is not of the product
    run ID form, such as one with a newline or a terminal control sequence, is replaced by a
    fixed placeholder, so the message cannot add lines of its own to the flow run log.
    """

    def __init__(self, run_id: str) -> None:
        shown = run_id if _RUN_ID_PATTERN.fullmatch(run_id) else _UNNAMED_RUN_ID
        guidance = _WORKER_EXECUTION_REFUSED_GUIDANCE.format(run_id=shown)
        super().__init__(f"{_WORKER_EXECUTION_REFUSED}: {guidance}")
        self.run_id = run_id


def _raise_writeback_refused() -> NoReturn:
    raise RuntimeError(_WORKER_EXECUTION_WRITEBACK_REFUSED)


def _run_logger() -> tuple[RunLogger, bool]:
    """Use Prefect logging in a run and a local logger in executor-only tests."""
    try:
        return get_run_logger(), True
    except MissingContextError:
        return logger, False


class SyncFlowLines:
    """Write the service flow's own lines through the run logger, as Sync lines.

    Each line starts with `infrahub_sync.service.flow | `, the form `RunLoggerBridge` gives every
    bridged line, so the log reader can tell every line Sync writes from Prefect's own lines. Each
    line is redacted with the stage's secrets as they are when the line is written.
    """

    def __init__(self, run_logger: RunLogger, secrets: Sequence[str]) -> None:
        self._run_logger = run_logger
        self._secrets = secrets

    def _text(self, msg: str, args: tuple[object, ...]) -> str:
        return redact(msg % args if args else msg, self._secrets)

    def log(self, level: int, msg: str, *args: object) -> None:
        """Write one line at `level`."""
        self._run_logger.log(level, "%s | %s", __name__, self._text(msg, args))

    def info(self, msg: str, *args: object) -> None:
        """Write one INFO line."""
        self._run_logger.info("%s | %s", __name__, self._text(msg, args))


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


def _runtime(*, projection_factory: Any = service_product_projection) -> tuple[str | None, ProductProjection]:
    """Resolve the worker's product store, and a configuration directory if it has one.

    A registered run reads its declared configuration out of the registry and resolves
    both adapter classes out of installed code, so it opens no directory at all. Only an
    unregistered run resolves a `SyncConfig` file, and that path is where the setting is
    required -- checked here instead, every registered deployment would have to satisfy
    it with a directory nothing ever reads. No cache location is read either: every stage
    creates its own private scratch.
    """
    return os.environ.get(CONFIG_DIR_ENV) or None, projection_factory()


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
        destination_branch=saved.manifest.destination_binding.branch if saved.manifest.destination_binding else None,
        lineage=saved.manifest.lineage.model_dump(mode="json") if saved.manifest.lineage is not None else None,
        last_changes=(
            None
            if saved.last_changes is None
            else tuple(record.model_dump(mode="json") for record in saved.last_changes)
        ),
    )


def _publish_plan(projection: ProductProjection, run_id: str, saved: SavedPlan, secrets: Sequence[str]) -> None:
    document = _review_document(run_id, saved)
    data = document.model_dump_json().encode()
    if saved.report:
        # The plan report's lines travel in the same document, under a key `GET /runs/{run_id}/plan`
        # does not return; the paged report routes read them (spec 019 contracts/sync-api.md). The
        # same serializer as `model_dump_json`, so secret redaction sees the same value text.
        data = to_json({**document.model_dump(mode="json"), PLAN_REPORT_KEY: list(saved.report)})
    projection.publish_artifact(
        run_id,
        artifact_id=PLAN_ARTIFACT_ID,
        kind="saved-plan-review",
        media_type="application/json",
        data=data,
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
    base_directory: Path | None = None,
    observer: StoreSideReadObserver | None = None,
) -> SavedPlan:
    saved = execute_run(
        instance,
        operation="plan",
        branch=branch,
        run_id=run_id,
        show_progress=False,
        print_diff=False,
        base_directory=base_directory,
        _run_file_mode="sync" if composed_sync else None,
        _return_saved_plan=True,
        _read_observer=observer,
    )
    assert isinstance(saved, SavedPlan)
    return saved


def _verify_registered_apply(
    *,
    instance: Any,
    run_id: str,
    binding: tuple[str, int, str] | None,
    expected_checksum: str | None,
    base_directory: Path | None = None,
) -> PlanManifest:
    """Verify a retained artifact before the apply path can construct a destination.

    Returns the verified manifest. Its `plan_checksum` must already be the operator's
    approved value, which is the same value the later `PlanApplier` read is given: one
    approval gates both reads, so bytes swapped between them cannot reach the write loop.
    """
    artifact = read_plan_artifact_bytes(resolve_run_directory(instance.name, run_id, base_directory=base_directory))
    failures = verify_plan(artifact=artifact, run_id=run_id, config_version=resolve_config_version(instance))
    if failures:
        guidance = failure_guidance(failures)
        message = f"{_REGISTERED_PLAN_VERIFICATION_FAILED}: {guidance}"
        format_failure = next((failure for failure in failures if failure.check == "format_version"), None)
        recovery_action: RecoveryAction | None = None
        if format_failure is not None:
            if format_failure.next_action.endswith(RE_PLAN_NEXT_ACTION):
                recovery_action = "rebuild"
            elif format_failure.next_action.endswith(UNSUPPORTED_FORMAT_NEXT_ACTION):
                recovery_action = "compatible_version"
        raise RegisteredPlanVerificationError(message, recovery_action, guidance=guidance)
    manifest = parse_plan_artifact(artifact, run_id=run_id, parse_last_changes=False).manifest
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


def _baseline_writeback(manifest: PlanManifest) -> BaselineWriteback:
    """Return the durable safety reference this successful write contributes.

    The counts are the plan manifest's own source snapshots: the write applied that
    plan, so those are the source counts its success corresponds to. Each snapshot is
    one resource's `A/<resource>.parquet`, and the supported service path always extracts
    in full, so the cadence fact is stated rather than counted.

    It records what this run observed. Nothing in this unit reads it back: there is no
    managed row-count refusal, no pre-dispatch lookup, and no override.
    """
    return BaselineWriteback(
        source_row_counts={Path(record.path).stem: record.row_count for record in manifest.source_snapshot},
        full_extract=True,
    )


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
    flow_run_id = _canonical_uuid(current_flow_run.id)
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
        raise ExecutionClaimRefusedError(run_id)
    return flow_run_id, worker_id


def _worker_execution_context(
    run_id: str,
    binding: tuple[str, int, str] | None,
    *,
    config_directory: str | None,
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
        msg = "service run binding does not match worker parameters"
        raise ValueError(msg)
    if binding is None:
        sync_name = stored.value.summary.get("sync_name")
        if not isinstance(sync_name, str) or not sync_name:
            raise ValueError(_LEGACY_RUN_IDENTITY_UNAVAILABLE)
        if config_directory is None or not Path(config_directory).is_dir():
            raise ValueError(_LEGACY_CONFIGURATION_DIRECTORY_UNAVAILABLE)
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
    # A saved-plan apply constructs the destination only, so it resolves no source credential.
    instance = resolve_runtime_instance(
        package, directory=_NO_CONFIGURATION_DIRECTORY, resolve_source_credentials=stage != "apply"
    )
    instance._configuration_binding = binding
    scope = STAGE_RUNTIME_MODEL_SCOPE.get(stage)
    if scope is not None and build_models:
        instance._runtime_models = build_runtime_model_plan(
            package=package, instance=instance, run_branch=run_branch, scope=scope
        )
    return projection, instance, package.configuration.name


def _configuration_write_guard(configuration_id: str) -> AbstractContextManager[ApplyGuard]:
    """Return the hold of one registered configuration's write guard.

    Everything destination-sensitive belongs inside that hold: the live schema read, the
    runtime models built from it, and every operation dispatched against them. A snapshot
    taken before a contended wait describes a destination another writer may still have
    been changing, so it cannot gate a write that happens after the wait.

    The dedicated session it opens is the guard's alone. It is never handed to an adapter
    or to the product store.
    """
    return hold_apply_guard(configuration_id, connect=service_guard_session, secrets=service_guard_secrets())


def _require_registered_write(binding: tuple[str, int, str] | None) -> str:
    """Return the configuration ID a managed write is guarded by, or refuse the write."""
    if binding is None:
        raise ValueError(_UNREGISTERED_WRITE_REFUSED)
    return binding[0]


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
    secrets: list[str],
    config_directory: str | None,
    projection: ProductProjection,
    tracker: WriteDispatchTracker,
    scratch: StageScratch,
    flow_run_id: str,
    confirm_left_out: bool = False,
) -> tuple[dict[str, Any], ExecutionWriteback]:
    """Resolve and execute one service stage within the sanitized worker boundary.

    A `plan` or `sync` stage records both sides as `waiting` as soon as it starts, before the
    live schema read and before a `sync` waits for its write guard, so a stage that fails
    before either read returns both sides as `not_started`. It then gives the engine an
    observer that stores each side's read on the execution `flow_run_id`.

    A `sync` whose plan leaves out records stops after the plan is saved, before any write,
    and gives back its write admission (spec 019 FR-012, research R11): see `_left_out_stop`.
    """
    if stage in ("plan", "sync"):
        record_waiting(projection, run_id, flow_run_id)
    parameter_binding = _worker_binding(config_id, registry_version, package_checksum)
    if stage in ("apply", "sync"):
        if not confirm_writes:
            msg = f"confirm_writes=true is required for service stage={stage}"
            raise ValueError(msg)
        # Before anything is resolved: the guard serializes writers per registered
        # configuration, so a run that names none cannot be serialized against them.
        _require_registered_write(parameter_binding)
    if stage == "apply" and expected_checksum is None:
        msg = "expected_checksum is required for service stage=apply"
        raise ValueError(msg)
    # A write defers its live schema read until the guard is held, so no destination
    # snapshot it validates against can predate a contended wait.
    projection, instance, sync_name = _worker_execution_context(
        run_id,
        parameter_binding,
        config_directory=config_directory,
        projection=projection,
        run_branch=branch,
        stage=stage,
        build_models=stage not in ("apply", "sync"),
    )

    secrets[:] = collect_secret_values(instance)
    result: dict[str, Any]
    if stage == "plan":
        saved = _plan(
            instance,
            run_id=run_id,
            branch=branch,
            composed_sync=False,
            base_directory=scratch.root,
            observer=StoreSideReadObserver(projection, run_id, flow_run_id),
        )
        # The internal handoff first: a plan that is publicly reviewable but has no
        # durable checkpoint would invite an apply on another worker that cannot read it.
        publish_plan_checkpoint(
            projection,
            run_id,
            run_directory=scratch.run_directory(instance.name, run_id),
            manifest=saved.manifest,
            secrets=secrets,
        )
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
        # Rehydrated and validated whole before the engine reads any of it. Verification
        # constructs no adapter, so a refusal here has contacted nothing at all.
        rehydrate_plan_checkpoint(projection, run_id, destination=scratch.run_directory(instance.name, run_id))
        saved = execute_run(
            instance,
            operation="verify",
            run_id=run_id,
            base_directory=scratch.root,
            _require_verified=True,
        )
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
        run_directory = scratch.run_directory(instance.name, run_id)
        # The plan this apply was approved for, resolved and validated whole before any
        # adapter factory exists. A checkpoint that is absent, oversized, corrupt,
        # digest-mismatched, or another run's refuses here, having contacted nothing.
        rehydrate_plan_checkpoint(projection, run_id, destination=run_directory)
        # Deterministic next, and still outside the guard: a run whose rehydrated artifact
        # does not match its binding or the operator's approved checksum is refused without
        # making another writer wait.
        manifest = _verify_registered_apply(
            instance=instance,
            run_id=run_id,
            binding=parameter_binding,
            expected_checksum=expected_checksum,
            base_directory=scratch.root,
        )
        with _configuration_write_guard(_require_registered_write(parameter_binding)) as guard:
            ownership = ProvenWriteOwnership(prove=guard.require_ownership, tracker=tracker)
            # The live schema read, and the comparison it exists for, both under the guard
            # and both before any adapter is constructed: the registered pre-write gate.
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
                confirm_left_out=confirm_left_out,
                ownership=ownership,
                record_applied=tracker.record_applied,
                base_directory=scratch.root,
                _lock_already_held=True,
            )
        # Outside the guard, so the release this stage reports is already confirmed, and
        # before the product commit, so the run's evidence is durable before its verdict.
        assert isinstance(applied, RunResult)
        publish_final_checkpoint(projection, run_id, run_directory=run_directory, secrets=secrets)
        result = _result_data(applied)
        writeback = ExecutionFinishWriteback(
            phase="applied",
            outcome=applied.status,
            finished_at=datetime.now(timezone.utc),
            summary={"sync_name": sync_name, **{key: applied.summary[key] for key in ACTION_KEYS}},
            results=result,
            baseline=_baseline_writeback(manifest),
        )
    else:
        # One guard across planning, verification, and the write it approves: a plan
        # generated outside it could go stale behind another writer before being applied.
        with _configuration_write_guard(_require_registered_write(parameter_binding)) as guard:
            ownership = ProvenWriteOwnership(prove=guard.require_ownership, tracker=tracker)
            _, instance, sync_name = _worker_execution_context(
                run_id,
                parameter_binding,
                config_directory=config_directory,
                projection=projection,
                run_branch=branch,
                stage=stage,
            )
            run_directory = scratch.run_directory(instance.name, run_id)
            saved = _plan(
                instance,
                run_id=run_id,
                branch=branch,
                composed_sync=True,
                base_directory=scratch.root,
                observer=StoreSideReadObserver(projection, run_id, flow_run_id),
            )
            # Durable before the first destination operation: a sync interrupted
            # mid-write leaves the plan an operator reconciles what happened against.
            publish_plan_checkpoint(
                projection,
                run_id,
                run_directory=run_directory,
                manifest=saved.manifest,
                secrets=secrets,
            )
            _publish_plan(projection, run_id, saved, secrets)
            # After the plan is durable and reviewable, before the verification and the
            # write: the team reviews the records left out and applies this run itself.
            stop = _left_out_stop(run_id, saved, sync_name)
            if stop is not None:
                return stop
            verified = execute_run(
                instance,
                operation="verify",
                run_id=run_id,
                base_directory=scratch.root,
                _run_file_mode="sync",
                _require_verified=True,
            )
            assert isinstance(verified, SavedPlan)
            manifest = _verify_registered_apply(
                instance=instance,
                run_id=run_id,
                binding=parameter_binding,
                expected_checksum=verified.manifest.plan_checksum,
                base_directory=scratch.root,
            )
            _require_planned_schema(run_id=run_id, manifest=manifest, models=instance._runtime_models)
            applied = execute_run(
                instance,
                operation="apply",
                run_id=run_id,
                branch=branch,
                expected_checksum=verified.manifest.plan_checksum,
                confirm_writes=True,
                ownership=ownership,
                record_applied=tracker.record_applied,
                base_directory=scratch.root,
                _lock_already_held=True,
                _run_file_mode="sync",
            )
            assert isinstance(applied, RunResult)
        # Same order the apply stage publishes in, for the same reason.
        publish_final_checkpoint(projection, run_id, run_directory=run_directory, secrets=secrets)
        result = {**_result_data(applied), "operation": "sync"}
        writeback = ExecutionFinishWriteback(
            phase="applied",
            outcome=applied.status,
            finished_at=datetime.now(timezone.utc),
            summary={"sync_name": sync_name, **{key: applied.summary[key] for key in ACTION_KEYS}},
            results=result,
            baseline=_baseline_writeback(verified.manifest),
        )
    return result, writeback


def left_out_stop_next_action(run_id: str, checksum: str) -> str:
    """The next action of a `sync` that stopped after a plan that leaves out records."""
    return (
        f"Review the plan with `infrahub-sync runs plan {run_id} --problems` and `--left-out`, then apply it "
        f"with `infrahub-sync apply {run_id} --expected-checksum {checksum} --confirm-left-out`."
    )


def _left_out_stop(
    run_id: str, saved: SavedPlan, sync_name: str
) -> tuple[dict[str, Any], ExecutionFinishWriteback] | None:
    """The result and writeback of a `sync` whose plan leaves out records, else `None`.

    The stage ends with phase `planned` and outcome `left-out-review-required`, and its
    result carries `failure`, whose next action the last log line, the run artifact and the
    CLI show (spec 019 FR-012, data-model.md "Stage result `failure` for a stopped `sync`").
    The writeback asks the store to give back the run's write admission in the same commit,
    so the team can apply the reviewed plan once with the confirmation. It carries no
    baseline: nothing was written.
    """
    summary = saved.summary()
    if summary.left_out == 0:
        return None
    failure = {
        "stage": "sync",
        "failure_type": "left_out_review_required",
        "left_out": summary.left_out,
        "next_action": left_out_stop_next_action(run_id, saved.manifest.plan_checksum),
    }
    summary_data = summary.model_dump(mode="json")
    result = {
        "run_id": run_id,
        "stage": "sync",
        "outcome": LEFT_OUT_STOP_OUTCOME,
        "summary": summary_data,
        "failure": failure,
    }
    writeback = ExecutionFinishWriteback(
        phase="planned",
        outcome=LEFT_OUT_STOP_OUTCOME,
        finished_at=datetime.now(timezone.utc),
        summary={"sync_name": sync_name, **summary_data},
        results=result,
        release_write_admission=True,
    )
    return result, writeback


def _failure_evidence(
    stage: Literal["plan", "verify", "apply", "sync"],
    exc: Exception,
    *,
    outcome: str,
    record: ApplyRecord | None,
    secrets: Sequence[str],
) -> dict[str, Any]:
    """Describe one failure, with whatever is known about what it wrote.

    The failure's operator cause is recorded too, when it has one: see `operator_cause`. Every
    record carries the spec 019 fields of `failure_record` (ADR 0021): the failure type, the
    system involved and the allowlisted values the failure names. Where `operator_cause`
    records no cause or next action, the fixed template of the failure type supplies it.

    A failure raised by the engine carries its own partial record; a failure raised after
    the engine returned — a sidecar write, the guard's release, the product commit — carries
    none, and the scope's record is what that one wrote. The engine's record also holds the
    plan's operation count and the failed operation's action, kind and identity.
    """
    evidence: dict[str, Any] = {
        "stage": stage,
        "outcome": outcome,
        "error_type": type(exc).__name__,
        # The class the wrapper was raised from. Every designed refusal the apply path
        # recognises is reported as one `OperationApplyFailedError`, so the wrapper alone
        # cannot tell an unkeyed render from a peer that matched nothing or from a
        # destination's own rejection; the immediate cause only, because the apply path wraps
        # exactly once.
        #
        # ADR 0021: the record holds the class name of the immediate cause and the allowlisted
        # fields of `failure_record` (the failure type, the system, Infrahub's error code, node
        # kind, attribute and own message of a rejected write, the kind, record and field a
        # typed Sync error names, the apply progress) and fixed template text filled with them.
        # It never holds the text of any other error: an adapter's, an HTTP library's or a
        # parser's text can carry an address, a header or a credential.
        "cause_type": None if exc.__cause__ is None else type(exc.__cause__).__name__,
    }
    if isinstance(exc, UnsupportedSyncStoreError):
        evidence.update(reason=UNSUPPORTED_STORE_REASON, message=UNSUPPORTED_STORE_MESSAGE)
    evidence.update(operator_cause(exc, secrets))
    evidence.update(_structured_cause(stage, exc, secrets, evidence))
    if stage == "apply" and isinstance(exc, RegisteredPlanVerificationError) and exc.recovery_action is not None:
        evidence["recovery_action"] = exc.recovery_action
    written = effective_apply_record(exc, record)
    if written is not None:
        evidence.update(written.as_summary_keys())
        evidence.update(apply_progress(written))
    return evidence


def _structured_cause(
    stage: str, exc: Exception, secrets: Sequence[str], recorded: Mapping[str, Any]
) -> dict[str, Any]:
    """The `failure_record` fields, keeping the cause and next action `operator_cause` recorded.

    `failure_record` reads typed values only, but an unexpected shape must not cost the run its
    whole failure record (`_record_failure` stores nothing when building it raises), so any
    error here records `internal_error`.
    """
    try:
        fields = failure_record(stage, exc, secrets=secrets)
    except Exception as unexpected:  # noqa: BLE001  # pylint: disable=broad-exception-caught
        logger.warning("failure record fields could not be built (%s)", type(unexpected).__name__)
        fields = dict.fromkeys(FAILURE_RECORD_KEYS)
        fields["failure_type"] = "internal_error"
    for key in ("cause", "next_action"):
        if recorded.get(key):
            fields.pop(key, None)
    return fields


def effective_apply_record(exc: Exception, record: ApplyRecord | None) -> ApplyRecord | None:
    """The record that describes this failure: the one the error carries, else the scope's.

    A failure raised by the engine carries its own partial record; one raised after the
    engine returned carries none, and the scope's record is what that one wrote. Resolved in
    one place because the ambiguity verdict and the recorded evidence must agree about what
    was written — reading different records is how a run gets an outcome its own evidence
    contradicts.
    """
    carried = getattr(exc, "apply_record", None)
    return carried if isinstance(carried, ApplyRecord) else record


@dataclass(frozen=True)
class RecordedFailure:
    """What `_record_failure` stored: the run's terminal outcome and the failure's next action.

    `cause` is the cause `operator_cause` recorded, for a known error class only; a cause from
    a `failure_record` template is not repeated here.
    """

    outcome: Literal["failed", "ambiguous"]
    next_action: str | None
    cause: str | None = None


def _next_step(run_id: str, next_action: str | None) -> str:
    """The operator's next step: the failure's next action, else reading the run's record."""
    return next_action or f"`infrahub-sync runs show {run_id}`"


def final_log_line(run_id: str, stage: str, outcome: str, next_action: str | None) -> str:
    """The last log line of a stage: its outcome and the operator's next step.

    Without a next action from the failure, the next step is to read the run's record.
    """
    return f"service run {run_id} stage={stage} outcome={outcome}; next step: {_next_step(run_id, next_action)}"


def result_next_action(result: dict[str, Any]) -> str | None:
    """The next action a successful stage's result carries for its failure, if any.

    The CLI reads the same `failure.next_action` field from the stored result.
    """
    failure = result.get("failure")
    if not isinstance(failure, dict):
        return None
    next_action = failure.get("next_action")
    return next_action if isinstance(next_action, str) and next_action else None


def _record_failure(  # pylint: disable=too-many-arguments,too-many-positional-arguments,too-many-locals
    run_id: str,
    stage: Literal["plan", "verify", "apply", "sync"],
    exc: Exception,
    run_logger: RunLogger,
    secrets: Sequence[str],
    projection: ProductProjection,
    flow_run_id: str,
    worker_id: str,
    *,
    dispatch_started: bool,
    record: ApplyRecord | None,
) -> RecordedFailure | None:
    """Best-effort durable product evidence without masking the worker failure.

    Returns the stored outcome and next action, or `None` when nothing was stored.

    `dispatch_started` is the one thing this boundary cannot re-derive: only the stage
    that ran knows whether it proved its hold and began a destination operation. Before
    that point a failure is an ordinary failed run; after it, nothing can claim the
    destination was left untouched, so the verdict is interrupted/ambiguous and the store
    records that the run requires reconciliation in the same transaction.

    A record that **names a failing operation** is the sharper account, and it overrides
    `dispatch_started` for exactly that case: the write surface knows whether that operation
    reached a mutation, and a refusal it raised before the write leaves nothing to
    reconcile. So a named failing operation is ambiguous only where its reach is unknown
    (S6). Everything else keeps the `dispatch_started` rule — including a **completed**
    record attached to a post-write failure, where the operations did write and the
    interruption came afterwards, in `record_applied`, the sidecar transition, the product
    commit or the final ownership proof.
    """
    try:
        stored = projection.lookup_run(run_id).value
        if stored is None:
            return None
        described = effective_apply_record(exc, record)
        if described is not None and described.failed_operation is not None:
            ambiguous = described.failed_operation_wrote is not False
        else:
            ambiguous = dispatch_started
        terminal_state, terminal_outcome = ("interrupted", "ambiguous") if ambiguous else ("failed", "failed")
        evidence = _failure_evidence(stage, exc, outcome=terminal_outcome, record=record, secrets=secrets)
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
                    "failed_operation_wrote",
                    "may_have_partially_written",
                )
                if key in evidence
            }
            writeback = ExecutionFinishWriteback(
                phase=f"{stage}-interrupted" if ambiguous else f"{stage}-failed",
                outcome=terminal_outcome,
                finished_at=datetime.now(timezone.utc),
                summary={**stored.summary, "failed_stage": stage, **partial},
                results={**stored.results, **results},
            )
        # Built before the commit: a receipt lookup error falls back to the run's actor
        # rather than losing the failure evidence.
        event = _stage_event(projection, stored, flow_run_id, stage, terminal_outcome, run_logger)
        committed = projection.commit_claimed_execution(
            run_id,
            flow_run_id,
            worker_id=worker_id,
            terminal_at=datetime.now(timezone.utc),
            terminal_state=terminal_state,
            terminal_outcome=terminal_outcome,
            writeback=writeback,
            event=event,
            secrets=secrets,
        )
    except Exception as persistence_error:  # noqa: BLE001  # pylint: disable=broad-exception-caught
        run_logger.log(
            logging.WARNING,
            "service failure evidence could not be persisted (%s)",
            type(persistence_error).__name__,
        )
        return None
    if not committed:
        # The store refused the commit because this worker no longer holds the execution, so
        # nothing was recorded and the failure takes the unrecorded path.
        run_logger.log(
            logging.WARNING, "service failure evidence was not recorded: this worker no longer holds the execution"
        )
        return None
    next_action = evidence.get("next_action")
    # The worker's ERROR line and its returned `Failed` state repeat only a cause that
    # `operator_cause` recorded (spec 017); a cause from a `failure_record` template stays in
    # the record, and the failure keeps the sanitized raise.
    cause = operator_cause(exc, secrets).get("cause")
    return RecordedFailure(
        outcome=terminal_outcome,
        next_action=next_action if isinstance(next_action, str) and next_action else None,
        cause=cause if isinstance(cause, str) and cause else None,
    )


def known_failure_line(run_id: str, recorded: RecordedFailure | None) -> str | None:
    """The one ERROR line for a failure whose recorded evidence has a cause, else `None`.

    The same text is the message of the `Failed` state the flow returns. Without a next action
    from the failure, the next step is to read the run's record, as in the last log line.
    """
    if recorded is None or recorded.cause is None:
        return None
    return f"{recorded.cause} Next step: {_next_step(run_id, recorded.next_action)}"


def _publish_run_summary(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    projection: ProductProjection,
    run_id: str,
    stage: str,
    outcome: str,
    next_action: str | None,
    secrets: Sequence[str],
    run_logger: RunLogger,
    *,
    prefect_context: bool,
) -> None:
    """Publish the stage's run summary artifact, with the outcome and next action of its last line.

    Only a real Prefect flow run has a place to publish it; elsewhere this does nothing.
    """
    if not prefect_context:
        return
    publish_run_artifact(
        projection,
        run_id,
        stage,
        outcome=outcome,
        next_action=next_action,
        secrets=secrets,
        run_logger=run_logger,
        create=create_markdown_artifact,
    )


def _failure_exit(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    run_id: str,
    stage: str,
    failure: Exception,
    recorded: RecordedFailure | None,
    lines: SyncFlowLines,
    secrets: list[str],
    projection: ProductProjection,
    *,
    prefect_context: bool,
) -> State[Any]:
    """End a failed stage: write its lines and artifact, clear the secrets, then fail the flow run.

    A failure whose recorded evidence has a cause writes one ERROR line, `<cause> Next step:
    <next_action>`, and returns `Failed` with the same text, so no traceback passes through
    Prefect's engine. Any other failure writes its class name as the ERROR line and raises its
    sanitized copy.

    The lines and the artifact are written before `secrets.clear()`, so they are redacted with
    this stage's secrets. A recorded failure reports the outcome the store holds. An unrecorded
    failure reports `failed` and points at `runs show`, while the store keeps the run
    nonterminal for reconciliation to decide.
    """
    if recorded is None:
        outcome, next_action = "failed", None
    else:
        outcome, next_action = recorded.outcome, recorded.next_action
    known = known_failure_line(run_id, recorded)
    try:
        # Best-effort: neither the lines nor the artifact may replace the stage's failure.
        with suppress(Exception):
            lines.log(logging.ERROR, "%s", type(failure).__name__ if known is None else known)
        with suppress(Exception):
            lines.info(final_log_line(run_id, stage, outcome, next_action))
            _publish_run_summary(
                projection, run_id, stage, outcome, next_action, secrets, lines, prefect_context=prefect_context
            )
        if known is not None:
            return Failed(message=redact(known, secrets))
        # Rebuilt after the original exception context exits.
        sanitized = sanitize_exception_chain(failure, secrets)
    finally:
        secrets.clear()
    raise sanitized


def _service_run_name() -> str:
    """The run's title, set by Prefect when the run starts."""
    parameters = current_flow_run.parameters
    return run_name(parameters.get("stage"), parameters.get("configuration_name"))


@flow(name=SERVICE_FLOW_NAME, flow_run_name=_service_run_name)
def service_sync_run(  # pylint: disable=too-many-positional-arguments
    run_id: str,
    stage: Literal["plan", "verify", "apply", "sync"],
    config_id: str | None = None,
    registry_version: int | None = None,
    package_checksum: str | None = None,
    branch: str | None = None,
    expected_checksum: str | None = None,
    confirm_writes: bool = False,
    configuration_name: str | None = None,
    confirm_left_out: bool = False,
) -> dict[str, Any] | State[Any]:
    """Execute one API-reserved stage and publish its durable product data.

    `configuration_name` is the configuration's readable name, used only for the run's
    title; the configuration is identified by `config_id`. `confirm_left_out` is the apply
    request's confirmation that the records the plan leaves out were reviewed; the engine
    checks it again (spec 019 research R10).

    A refused execution claim, and a failure whose recorded evidence has a cause, end the flow
    run with a returned `Failed` state, so no traceback passes through Prefect's engine; any
    other failure raises its sanitized copy (plan.md, "Failure exit for known error classes").
    """
    del configuration_name
    run_logger, prefect_context = _run_logger()
    secrets: list[str] = []
    # Every line this function writes itself goes through `lines`; the bridge below forwards
    # the `infrahub_sync` logger tree with the same prefix.
    lines = SyncFlowLines(run_logger, secrets)
    failure: Exception | None = None
    config_directory, projection = _runtime()
    try:
        flow_run_id, worker_id = _claim_current_execution(projection, run_id)
    except ExecutionClaimRefusedError as refused:
        # Put the recovery guidance in the flow run log, where an operator who
        # pressed Retry looks for it, then fail the flow run with a returned state,
        # so Prefect's engine does not log the refusal a second time with a traceback.
        lines.log(logging.ERROR, "%s", refused)
        return Failed(message=str(refused))
    secrets[:] = collect_secret_values()
    # Owned here rather than by the stage, so the failure boundary can read it however the
    # stage ended.
    tracker = WriteDispatchTracker()
    try:
        # One new private root per stage, released before this worker reports anything.
        # Everything the stage needs from its predecessor arrives through product
        # storage, so nothing here has to survive the stage that created it.
        with (
            stage_scratch(stage) as scratch,
            _remote_log_bridge(run_logger, prefect_context=prefect_context, secrets=secrets),
        ):
            result, writeback = _execute_stage(
                run_id,
                stage,
                config_id,
                registry_version,
                package_checksum,
                branch,
                expected_checksum,
                confirm_writes=confirm_writes,
                secrets=secrets,
                config_directory=config_directory,
                projection=projection,
                tracker=tracker,
                scratch=scratch,
                flow_run_id=flow_run_id,
                confirm_left_out=confirm_left_out,
            )
    except Exception as exc:  # noqa: BLE001  # pylint: disable=broad-exception-caught
        failure = exc
    record_terminal = True
    if failure is None:
        try:
            _commit_success(
                projection,
                run_id,
                flow_run_id,
                stage=stage,
                worker_id=worker_id,
                writeback=writeback,
                secrets=secrets,
                run_logger=lines,
            )
        except Exception as exc:  # noqa: BLE001  # pylint: disable=broad-exception-caught
            failure = exc
            # A read stage whose success did not store stays nonterminal on purpose: it
            # wrote nothing, so reconciliation decides it rather than this worker.
            record_terminal = stage in _WRITE_STAGES
    if failure is not None:
        # The one exit for the whole managed write scope. Everything after the first proven
        # dispatch reaches it — a failed operation, a lost hold, a sidecar that could not
        # record the applied transition, a release that could not be confirmed, and a
        # product commit that did not store its success — so all of them commit the same
        # ambiguous verdict, the same evidence, the record the scope kept, and
        # `reconciliation_required` together.
        recorded = None
        if record_terminal:
            recorded = _record_failure(
                run_id,
                stage,
                failure,
                lines,
                secrets,
                projection,
                flow_run_id,
                worker_id,
                dispatch_started=tracker.dispatch_started,
                record=tracker.applied_record,
            )
        return _failure_exit(
            run_id,
            stage,
            failure,
            recorded,
            lines,
            secrets,
            projection,
            prefect_context=prefect_context,
        )
    outcome, next_action = str(result["outcome"]), result_next_action(result)
    lines.info(final_log_line(run_id, stage, outcome, next_action))
    _publish_run_summary(
        projection, run_id, stage, outcome, next_action, secrets, lines, prefect_context=prefect_context
    )
    return result


def _stage_event(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    projection: ProductProjection,
    run: ProductRun,
    flow_run_id: str,
    stage: str,
    outcome: str,
    run_logger: RunLogger,
) -> AuditEvent:
    """Build the event of one stage result (spec 017 data model).

    The actor and reason are those of the request whose receipt holds this flow run ID, so an
    apply an administrator requested names the administrator; when no receipt holds it, the
    event names the run's actor. When the receipt cannot be read, the event names the run's
    actor too and a WARNING line names the error class, so a lookup error neither loses a
    failure's evidence nor turns a success into a failure.
    """
    try:
        request = projection.lookup_mutation_for_execution(run.run_id, flow_run_id, stage).value
    except Exception as lookup_error:  # noqa: BLE001  # pylint: disable=broad-exception-caught
        run_logger.log(
            logging.WARNING,
            "the request that started this execution could not be read (%s)",
            type(lookup_error).__name__,
        )
        request = None
    actor = request.actor if request is not None else run.actor
    return run_event(
        run.run_id,
        run,
        event_type=stage_result_type(stage, outcome),
        actor=actor or "unknown",
        operation=stage,
        reason=None if request is None else request.reason,
        outcome=outcome,
    )


def _commit_success(  # pylint: disable=too-many-arguments
    projection: ProductProjection,
    run_id: str,
    flow_run_id: str,
    *,
    stage: str,
    worker_id: str,
    writeback: ExecutionWriteback,
    secrets: Sequence[str],
    run_logger: RunLogger,
) -> None:
    """Commit this stage's success, tolerating only a commit that actually landed.

    A commit can succeed and still fail to answer. Rereading the link is what tells those
    apart: a stored success is this stage's success whatever the call reported, and
    anything else is a failure the scope's one exit path has to handle.
    """
    try:
        stored = projection.lookup_run(run_id).value
        if stored is None:
            _raise_writeback_refused()
        event = _stage_event(projection, stored, flow_run_id, stage, "succeeded", run_logger)
        if not projection.commit_claimed_execution(
            run_id,
            flow_run_id,
            worker_id=worker_id,
            terminal_at=datetime.now(timezone.utc),
            terminal_state="completed",
            terminal_outcome="succeeded",
            writeback=writeback,
            event=event,
            secrets=secrets,
        ):
            _raise_writeback_refused()
    except Exception:
        if _stored_success(projection, run_id, flow_run_id):
            return
        raise


def _stored_success(projection: ProductProjection, run_id: str, flow_run_id: str) -> bool:
    """Answer whether this execution's success is already durable."""
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
        return False
    return link is not None and (link.terminal_state, link.terminal_outcome) == ("completed", "succeeded")
