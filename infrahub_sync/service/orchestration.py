"""Prefect Extras translation and live-state access for the Sync API."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from math import isfinite
from pathlib import Path
from typing import Any, Protocol, cast
from uuid import UUID

import httpx
from opsmill_prefect_extras.executors import (
    IdempotentWorkflowExecutor,
    RemoteExecutionClient,
    RemoteWorkflowExecutor,
)
from opsmill_prefect_extras.workflows import WorkflowDefinition
from prefect.client.schemas.objects import State, StateType, WorkerStatus
from prefect.client.schemas.responses import (
    OrchestrationResult,
    SetStateStatus,
    StateAcceptDetails,
    T,
)
from prefect.exceptions import ObjectNotFound
from prefect.states import Cancelling

SERVICE_FLOW_NAME = "infrahub-sync-service"
SERVICE_DEPLOYMENT_NAME = "run"
_TERMINAL_STATE_TYPES = frozenset({StateType.COMPLETED, StateType.FAILED, StateType.CRASHED, StateType.CANCELLED})
# Freshness uses three intervals; cap it at the largest age two datetimes can express.
_MAX_DATETIME_AGE = datetime.max.replace(tzinfo=timezone.utc) - datetime.min.replace(tzinfo=timezone.utc)
_MAX_HEARTBEAT_INTERVAL_SECONDS = (_MAX_DATETIME_AGE.days * 24 * 60 * 60 + _MAX_DATETIME_AGE.seconds) // 3
# The entrypoint is the absolute path of the flow file in THIS installation,
# resolved at import time. Without it the applied deployment carries no
# entrypoint at all (the deployment library invents no defaults), and a Prefect
# process worker refuses the flow run with "does not have an entrypoint and can
# not be run". An absolute path also encodes the documented contract that the
# API, the deployment apply, and the workers share one installation's
# filesystem view; re-applying from a different installation reconciles it.
_SERVICE_FLOW_ENTRYPOINT = f"{Path(__file__).with_name('flow.py')}:service_sync_run"

SERVICE_DEFINITION = WorkflowDefinition(
    flow_name=SERVICE_FLOW_NAME,
    deployment_name=SERVICE_DEPLOYMENT_NAME,
    module="infrahub_sync.service.flow",
    function="service_sync_run",
    entrypoint=_SERVICE_FLOW_ENTRYPOINT,
    tags=("infrahub-sync", "service"),
)


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


class _PoolClient(Protocol):
    """Pinned Prefect client methods used only by the service liveness adapter."""

    async def read_workers_for_work_pool(self, work_pool_name: str) -> list[Any]: ...

    async def get_scheduled_flow_runs_for_work_pool(self, work_pool_name: str) -> list[Any]: ...


class PrefectOrchestration:
    """Use Prefect Extras for submission and Prefect as live-state authority."""

    def __init__(self, client: RemoteExecutionClient, executor: IdempotentWorkflowExecutor | None = None) -> None:
        self._client = client
        self._executor = executor or RemoteWorkflowExecutor(client)

    async def submit(self, parameters: dict[str, object], *, idempotency_key: str) -> Submission:
        handle = await self._executor.submit(SERVICE_DEFINITION, parameters, idempotency_key=idempotency_key)
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
