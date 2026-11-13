"""Prefect Extras translation and live-state access for the Sync API."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from math import isfinite
from typing import TYPE_CHECKING, Any, Final, Protocol, cast
from uuid import UUID

import httpx
from opsmill_prefect_extras.executors import (
    IdempotentWorkflowExecutor,
    RemoteExecutionClient,
    RemoteWorkflowExecutor,
)
from opsmill_prefect_extras.workflows import WorkflowDefinition
from prefect.client.schemas.filters import LogFilter, LogFilterFlowRunId
from prefect.client.schemas.objects import State, StateType, WorkerStatus
from prefect.client.schemas.responses import (
    OrchestrationResult,
    SetStateStatus,
    StateAcceptDetails,
    T,
)
from prefect.client.schemas.sorting import LogSort
from prefect.exceptions import ObjectNotFound
from prefect.states import Cancelling

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

SERVICE_FLOW_NAME = "infrahub-sync-service"
SERVICE_DEPLOYMENT_NAME = "run"
_TERMINAL_STATE_TYPES = frozenset({StateType.COMPLETED, StateType.FAILED, StateType.CRASHED, StateType.CANCELLED})
# Freshness uses three intervals; cap it at the largest age two datetimes can express.
_MAX_DATETIME_AGE = datetime.max.replace(tzinfo=timezone.utc) - datetime.min.replace(tzinfo=timezone.utc)
_MAX_HEARTBEAT_INTERVAL_SECONDS = (_MAX_DATETIME_AGE.days * 24 * 60 * 60 + _MAX_DATETIME_AGE.seconds) // 3
# The installed dotted identity of the flow, and deliberately colon-free. Prefect
# routes an entrypoint containing a colon through its source-file branch, which
# executes a script at a recorded path -- a path that belongs to whichever
# installation applied the deployment and need not exist on a worker at all. The
# module form resolves through the installed distribution instead, so a worker
# needs the package installed and nothing else. An entrypoint is still required:
# without one a process worker refuses the flow run with "does not have an
# entrypoint and can not be run".
_SERVICE_FLOW_ENTRYPOINT = "infrahub_sync.service.flow.service_sync_run"

SERVICE_DEFINITION = WorkflowDefinition(
    flow_name=SERVICE_FLOW_NAME,
    deployment_name=SERVICE_DEPLOYMENT_NAME,
    module="infrahub_sync.service.flow",
    function="service_sync_run",
    entrypoint=_SERVICE_FLOW_ENTRYPOINT,
    tags=("infrahub-sync", "service"),
)


# The title Prefect's UI shows for a run, instead of the name Prefect generates for a
# flow run. It follows the rule Infrahub applies to its own flow runs (`dev/guides/
# backend/creating-async-tasks.md` in Infrahub): say what the run does, and leave out
# the branch and any ID, which the run's page already shows.
_RUN_NAMES: Final = {
    "plan": "Plan sync of {configuration}",
    "verify": "Verify sync plan of {configuration}",
    "apply": "Apply sync plan of {configuration}",
    "sync": "Sync {configuration}",
    "resume": "Resume sync plan of {configuration}",
}
# Lines read per Prefect log request (spec 017, research R4). Prefect's own default page size;
# a page shorter than this is the last one.
LOG_PAGE_SIZE: Final = 200


class OrchestrationUnavailableError(RuntimeError):
    """The orchestrator could not be reached or did not answer in time.

    The message names no provider and no address: it can reach an API error. ``error_class``
    is the class name of the client error behind it, for the server log only.
    """

    def __init__(self, message: str, *, error_class: str | None = None) -> None:
        super().__init__(message)
        self.error_class = error_class


@dataclass(frozen=True)
class FlowRunLog:
    """One log line of a flow run, as the orchestrator stores it."""

    log_id: str
    flow_run_id: str
    timestamp: datetime
    level: int
    message: str


@dataclass(frozen=True)
class RunLogRead:
    """The log lines of a run's flow runs, or that one of them is no longer held."""

    expired: bool
    logs: tuple[FlowRunLog, ...] = ()


_UNNAMED_RUNS: Final = {
    "plan": "Plan sync",
    "verify": "Verify sync plan",
    "apply": "Apply sync plan",
    "sync": "Sync",
    "resume": "Resume sync plan",
}


def run_name(stage: object, configuration_name: object) -> str:
    """Return the title of one stage's run: what it does, and to which configuration.

    The configuration is named by its readable name, never by its ID.
    """
    if not isinstance(stage, str) or stage not in _RUN_NAMES:
        return "Sync run"
    if isinstance(configuration_name, str) and configuration_name:
        return _RUN_NAMES[stage].format(configuration=configuration_name)
    return _UNNAMED_RUNS[stage]


@dataclass(frozen=True, slots=True)
class Submission:
    """One Prefect-accepted service execution."""

    flow_run_id: str
    state: str


@dataclass(frozen=True, slots=True)
class Observation:
    """Available Prefect state or explicit missing detail."""

    available: bool
    state: str | None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class CancellationResult:
    """Remote cancellation acknowledgement; never a fabricated terminal state."""

    acknowledged: bool
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class PoolWorker:
    """Internal, non-public worker liveness evidence from Prefect."""

    worker_id: str
    status: str
    last_heartbeat: datetime | None
    heartbeat_interval_seconds: float | None

    def __post_init__(self) -> None:
        """Reject worker evidence outside the documented value domain."""
        _validate_pool_worker(self)


@dataclass(frozen=True, slots=True)
class PoolStatus:
    """One value-free work-pool observation."""

    detail_available: bool
    queue_depth: int | None
    observed_at: datetime | None
    workers: tuple[PoolWorker, ...] = ()

    def __post_init__(self) -> None:
        """Keep unavailable and available snapshots structurally disjoint."""
        if not self.detail_available:
            if self.queue_depth is not None or self.observed_at is not None or self.workers:
                raise ValueError
            return
        if self.queue_depth is None or self.queue_depth < 0:
            raise ValueError
        if not isinstance(self.observed_at, datetime) or self.observed_at.utcoffset() is None:
            raise ValueError
        if not all(isinstance(worker, PoolWorker) for worker in self.workers):
            raise ValueError
        if len({worker.worker_id for worker in self.workers}) != len(self.workers):
            raise ValueError


class ServiceOrchestration(Protocol):
    """Small orchestration boundary consumed by the HTTP service."""

    async def submit(self, parameters: dict[str, object], *, idempotency_key: str) -> Submission: ...

    async def observe(self, flow_run_id: str) -> Observation: ...

    async def pool_status(self, work_pool_name: str, now: datetime) -> PoolStatus: ...

    async def cancel(self, flow_run_id: str) -> CancellationResult: ...

    async def read_logs(self, flow_run_ids: Sequence[str]) -> RunLogRead: ...


def run_tags(parameters: dict[str, object]) -> frozenset[str]:
    """Return the Prefect tags that identify one run, its stage and its configuration version.

    Built only from identifiers the run resource already publishes: never the actor, the
    branch, the work pool or a secret. A legacy run has no registry version and gets no
    configuration tags.
    """
    tags: set[str] = set()
    run_id = parameters.get("run_id")
    if isinstance(run_id, str) and run_id:
        tags.add(f"run:{run_id}")
    stage = parameters.get("stage")
    if isinstance(stage, str) and stage:
        tags.add(f"stage:{stage}")
    config_id = parameters.get("config_id")
    registry_version = parameters.get("registry_version")
    registered = isinstance(registry_version, int) and not isinstance(registry_version, bool)
    if isinstance(config_id, str) and config_id and registered:
        tags.add(f"config:{config_id}")
        tags.add(f"config-version:{config_id}@{registry_version}")
    return frozenset(tags)


class _LabellingClient(Protocol):
    """The Prefect flow-run creation call with the title and tags Prefect accepts at creation."""

    async def create_flow_run_from_deployment(
        self,
        deployment_id: UUID,
        *,
        parameters: dict[str, Any],
        idempotency_key: str | None = None,
        name: str | None = None,
        tags: list[str] | None = None,
    ) -> Any: ...


class _LabelledCreationClient:
    """Pass the executor's Prefect calls through, adding one run's title and tags at creation.

    The vendored executor creates the flow run without a name or tags, and its copy must stay
    identical to upstream. Prefect's ``create_flow_run_from_deployment`` accepts both; the
    server gives the run the union of the deployment's tags and these, so the deployment's
    tags, including any an operator added to the deployment, are kept.
    """

    def __init__(self, client: RemoteExecutionClient, *, name: str, tags: list[str]) -> None:
        self._client = client
        self._name = name
        self._tags = tags

    async def read_deployment_by_name(self, name: str) -> Any:
        """Read the deployment unchanged."""
        return await self._client.read_deployment_by_name(name)

    async def create_flow_run_from_deployment(
        self,
        deployment_id: UUID,
        *,
        parameters: dict[str, Any],
        idempotency_key: str | None = None,
    ) -> Any:
        """Create the flow run with this run's title and tags."""
        client = cast("_LabellingClient", self._client)
        return await client.create_flow_run_from_deployment(
            deployment_id,
            parameters=parameters,
            idempotency_key=idempotency_key,
            name=self._name,
            tags=self._tags,
        )

    async def read_flow_run(self, flow_run_id: UUID) -> Any:
        """Read the flow run unchanged."""
        return await self._client.read_flow_run(flow_run_id)

    async def set_flow_run_state(self, flow_run_id: UUID, state: State[Any]) -> object:
        """Request the state transition unchanged."""
        return await self._client.set_flow_run_state(flow_run_id, state)


class _LogClient(Protocol):
    """Pinned Prefect client methods used only by the log reader."""

    async def read_flow_run(self, flow_run_id: UUID) -> Any: ...

    async def read_logs(
        self,
        log_filter: LogFilter | None = None,
        limit: int | None = None,
        offset: int | None = None,
        sort: LogSort | None = None,
    ) -> list[Any]: ...


class _PoolClient(Protocol):
    """Pinned Prefect client methods used only by the service liveness adapter."""

    async def read_workers_for_work_pool(self, work_pool_name: str) -> list[Any]: ...

    async def get_scheduled_flow_runs_for_work_pool(self, work_pool_name: str) -> list[Any]: ...


class PrefectOrchestration:
    """Use Prefect Extras for submission and Prefect as live-state authority."""

    def __init__(
        self,
        client: RemoteExecutionClient,
        executor_factory: Callable[[RemoteExecutionClient], IdempotentWorkflowExecutor] = RemoteWorkflowExecutor,
    ) -> None:
        self._client = client
        self._executor_factory = executor_factory

    async def submit(self, parameters: dict[str, object], *, idempotency_key: str) -> Submission:
        """Create the flow run with its title and run tags, so a waiting run is already labelled.

        The flow sets the same title again when it starts. The run tags are ``run:``,
        ``stage:`` and, for a registered run, ``config:`` and ``config-version:``; Prefect adds
        the deployment's tags. A replay with the same idempotency key returns the flow run
        Prefect created first, with the title and tags it was created with.
        """
        labelled = _LabelledCreationClient(
            self._client,
            name=run_name(parameters.get("stage"), parameters.get("configuration_name")),
            tags=sorted(run_tags(parameters)),
        )
        executor = self._executor_factory(labelled)
        handle = await executor.submit(SERVICE_DEFINITION, parameters, idempotency_key=idempotency_key)
        return Submission(flow_run_id=handle.id, state=await handle.status())

    async def observe(self, flow_run_id: str) -> Observation:
        try:
            run = await self._client.read_flow_run(UUID(flow_run_id))
        except ObjectNotFound:
            return Observation(available=False, state=None, reason="prefect-execution-unavailable")
        except httpx.HTTPError:
            return Observation(available=False, state=None, reason="prefect-read-unavailable")
        state = run.state
        if state is None:
            return Observation(available=True, state="pending")
        state_type = state.type
        state_name = state_type.value if state_type in _TERMINAL_STATE_TYPES else state.name or state_type.value
        return Observation(available=True, state=state_name.lower())

    async def pool_status(self, work_pool_name: str, now: datetime) -> PoolStatus:
        """Read worker heartbeats and scheduled queue depth without exposing provider detail."""
        try:
            client = cast("_PoolClient", self._client)
            workers = _pinned_list(await client.read_workers_for_work_pool(work_pool_name))
            scheduled = _pinned_list(await client.get_scheduled_flow_runs_for_work_pool(work_pool_name))
            parsed = tuple(_pool_worker(worker) for worker in workers)
            return PoolStatus(detail_available=True, queue_depth=len(scheduled), observed_at=now, workers=parsed)
        except (ObjectNotFound, httpx.HTTPError, AttributeError, TypeError, ValueError):
            return PoolStatus(detail_available=False, queue_depth=None, observed_at=None)

    async def cancel(self, flow_run_id: str) -> CancellationResult:
        """Cancel one flow run and report only what Prefect acknowledged."""
        requested_state = Cancelling()
        try:
            result = await self._client.set_flow_run_state(UUID(flow_run_id), requested_state)
        except (ObjectNotFound, httpx.HTTPError):
            return CancellationResult(acknowledged=False, reason="prefect-cancellation-unavailable")
        if not _cancellation_acknowledged(result, requested_state):
            return CancellationResult(acknowledged=False, reason="prefect-cancellation-unavailable")
        return CancellationResult(acknowledged=True)

    async def read_logs(self, flow_run_ids: Sequence[str]) -> RunLogRead:
        """Read every log line of the given flow runs, oldest first.

        Each flow run is read first, so a flow run the orchestrator no longer holds is told
        apart from one that has written no line yet. Lines are read in pages of
        ``LOG_PAGE_SIZE`` until a short page.

        Raises:
            OrchestrationUnavailableError: a request failed or timed out.
        """
        if not flow_run_ids:
            return RunLogRead(expired=False)
        client = cast("_LogClient", self._client)
        identifiers = [UUID(flow_run_id) for flow_run_id in flow_run_ids]
        try:
            for identifier in identifiers:
                try:
                    await client.read_flow_run(identifier)
                except ObjectNotFound:
                    return RunLogRead(expired=True)
            log_filter = LogFilter(flow_run_id=LogFilterFlowRunId(any_=identifiers))
            logs: list[FlowRunLog] = []
            while True:
                batch = await client.read_logs(
                    log_filter, limit=LOG_PAGE_SIZE, offset=len(logs), sort=LogSort.TIMESTAMP_ASC
                )
                logs.extend(_flow_run_log(entry) for entry in batch)
                if len(batch) < LOG_PAGE_SIZE:
                    break
        except (httpx.HTTPError, TimeoutError) as exc:
            msg = "the orchestrator could not be reached"
            raise OrchestrationUnavailableError(msg, error_class=type(exc).__name__) from None
        return RunLogRead(expired=False, logs=tuple(logs))


def _flow_run_log(entry: Any) -> FlowRunLog:
    """Copy the fields the log reader uses from one orchestrator log line."""
    return FlowRunLog(
        log_id=str(entry.id),
        flow_run_id=str(entry.flow_run_id),
        timestamp=entry.timestamp,
        level=int(entry.level),
        message=str(entry.message),
    )


def _pinned_list(value: object) -> list[Any]:
    """Require the pinned Prefect list response; every failure here means unavailable."""
    if not isinstance(value, list):
        raise ValueError  # noqa: TRY004 - pool_status contains ValueError, not TypeError.
    return cast("list[Any]", value)


def _canonical_uuid(value: object) -> str:
    """Return the canonical form of the UUID Prefect assigned to the worker."""
    if not isinstance(value, UUID):
        raise ValueError  # noqa: TRY004 - pool_status contains ValueError, not TypeError.
    return str(value)


def _validate_pool_worker(worker: PoolWorker) -> None:
    """Validate one internal worker observation against its documented domain."""
    try:
        canonical_worker_id = str(UUID(worker.worker_id))
    except ValueError:
        raise ValueError from None
    if canonical_worker_id != worker.worker_id:
        raise ValueError
    if worker.status not in {member.value.lower() for member in WorkerStatus}:
        raise ValueError
    heartbeat = worker.last_heartbeat
    if heartbeat is not None and heartbeat.utcoffset() is None:
        raise ValueError
    interval = worker.heartbeat_interval_seconds
    if interval is not None and (interval <= 0 or not isfinite(interval) or interval > _MAX_HEARTBEAT_INTERVAL_SECONDS):
        raise ValueError


def _cancellation_acknowledged(result: object, requested_state: State[T]) -> bool:
    """Accept only Prefect's acknowledgement of the requested cancelling transition."""
    if not isinstance(result, OrchestrationResult):
        return False
    state = result.state
    return (
        result.status is SetStateStatus.ACCEPT
        and isinstance(result.details, StateAcceptDetails)
        and state is not None
        and state.type is StateType.CANCELLING
        and state.name == requested_state.name
    )


def normalized_pool_status(snapshot: object) -> PoolStatus:
    """Map any malformed orchestration snapshot to the fixed unavailable value."""
    if not isinstance(snapshot, PoolStatus):
        return PoolStatus(detail_available=False, queue_depth=None, observed_at=None)
    try:
        return PoolStatus(
            detail_available=snapshot.detail_available,
            queue_depth=snapshot.queue_depth,
            observed_at=snapshot.observed_at,
            workers=snapshot.workers,
        )
    except (AttributeError, TypeError, ValueError):
        return PoolStatus(detail_available=False, queue_depth=None, observed_at=None)


def _status_value(value: object) -> str:
    """Normalize one Prefect worker status into the documented vocabulary."""
    if isinstance(value, WorkerStatus):
        return value.value.lower()
    if not isinstance(value, str):
        raise ValueError  # noqa: TRY004 - pool_status contains ValueError, not TypeError.
    status = value.lower()
    if status not in {member.value.lower() for member in WorkerStatus}:
        raise ValueError
    return status


def _pool_worker(worker: Any) -> PoolWorker:
    """Parse one Prefect worker record into the internal liveness evidence."""
    heartbeat = worker.last_heartbeat_time
    interval = worker.heartbeat_interval_seconds
    if heartbeat is not None and (not isinstance(heartbeat, datetime) or heartbeat.utcoffset() is None):
        raise ValueError
    if not isinstance(interval, int) or interval <= 0 or interval > _MAX_HEARTBEAT_INTERVAL_SECONDS:
        raise ValueError
    return PoolWorker(
        worker_id=_canonical_uuid(worker.id),
        status=_status_value(worker.status),
        last_heartbeat=heartbeat,
        heartbeat_interval_seconds=float(interval),
    )
