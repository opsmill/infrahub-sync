"""Prefect Extras translation and live-state access for the managed API."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
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

MANAGED_FLOW_NAME = "infrahub-sync-managed"
MANAGED_DEPLOYMENT_NAME = "run"
# The entrypoint is the absolute path of the flow file in THIS installation,
# resolved at import time. Without it the applied deployment carries no
# entrypoint at all (the deployment library invents no defaults), and a Prefect
# process worker refuses the flow run with "does not have an entrypoint and can
# not be run". An absolute path also encodes the documented contract that the
# API, the deployment apply, and the workers share one installation's
# filesystem view; re-applying from a different installation reconciles it.
_MANAGED_FLOW_ENTRYPOINT = f"{Path(__file__).with_name('flow.py')}:managed_sync_run"

MANAGED_DEFINITION = WorkflowDefinition(
    flow_name=MANAGED_FLOW_NAME,
    deployment_name=MANAGED_DEPLOYMENT_NAME,
    module="infrahub_sync.managed.flow",
    function="managed_sync_run",
    entrypoint=_MANAGED_FLOW_ENTRYPOINT,
    tags=("infrahub-sync", "managed"),
)


@dataclass(frozen=True, slots=True)
class Submission:
    """One Prefect-accepted managed execution."""

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
    """Exact-flow remote cancellation acknowledgement without fabricated terminal state."""

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
        """Reject worker evidence outside the exact internal value domain."""
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
        if type(self.detail_available) is not bool:  # pylint: disable=unidiomatic-typecheck
            raise ValueError
        if not self.detail_available:
            if self.queue_depth is not None or self.observed_at is not None or self.workers:
                raise ValueError
            return
        if type(self.queue_depth) is not int or self.queue_depth < 0:  # pylint: disable=unidiomatic-typecheck
            raise ValueError
        if (  # pylint: disable=unidiomatic-typecheck
            type(self.observed_at) is not datetime or self.observed_at.utcoffset() is None
        ):
            raise ValueError
        if type(self.workers) is not tuple:  # pylint: disable=unidiomatic-typecheck
            raise ValueError
        for worker in self.workers:
            if type(worker) is not PoolWorker:  # pylint: disable=unidiomatic-typecheck
                raise ValueError
            _validate_pool_worker(worker)
        if len({worker.worker_id for worker in self.workers}) != len(self.workers):
            raise ValueError


class ManagedOrchestration(Protocol):
    """Small orchestration boundary consumed by the HTTP service."""

    async def submit(self, parameters: dict[str, object], *, idempotency_key: str) -> Submission: ...

    async def observe(self, flow_run_id: str) -> Observation: ...

    async def pool_status(self, work_pool_name: str, now: datetime) -> PoolStatus: ...

    async def cancel(self, flow_run_id: str) -> CancellationResult: ...


class _PoolClient(Protocol):
    """Pinned Prefect client methods used only by the managed liveness adapter."""

    async def read_workers_for_work_pool(self, work_pool_name: str) -> list[Any]: ...

    async def get_scheduled_flow_runs_for_work_pool(self, work_pool_name: str) -> list[Any]: ...


class PrefectOrchestration:
    """Use Prefect Extras for submission and Prefect as live-state authority."""

    def __init__(self, client: RemoteExecutionClient, executor: IdempotentWorkflowExecutor | None = None) -> None:
        self._client = client
        self._executor = executor or RemoteWorkflowExecutor(client)

    async def submit(self, parameters: dict[str, object], *, idempotency_key: str) -> Submission:
        handle = await self._executor.submit(MANAGED_DEFINITION, parameters, idempotency_key=idempotency_key)
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
        state_name = state.name or state.type.value
        return Observation(available=True, state=state_name.lower())

    async def pool_status(self, work_pool_name: str, now: datetime) -> PoolStatus:
        """Read worker heartbeats and scheduled queue depth without exposing provider detail."""
        try:
            client = cast("_PoolClient", self._client)
            workers = _exact_list(await client.read_workers_for_work_pool(work_pool_name))
            scheduled = _exact_list(await client.get_scheduled_flow_runs_for_work_pool(work_pool_name))
            parsed = tuple(_pool_worker(worker) for worker in workers)
            queue_depth = len(scheduled)
            return PoolStatus(detail_available=True, queue_depth=queue_depth, observed_at=now, workers=parsed)
        except (ObjectNotFound, httpx.HTTPError, AttributeError, TypeError, ValueError):
            return PoolStatus(detail_available=False, queue_depth=None, observed_at=None)

    async def cancel(self, flow_run_id: str) -> CancellationResult:
        """Request exact-flow cancellation and report only Prefect acknowledgement."""
        try:
            result = await self._client.set_flow_run_state(UUID(flow_run_id), Cancelling())
        except (ObjectNotFound, httpx.HTTPError):
            return CancellationResult(acknowledged=False, reason="prefect-cancellation-unavailable")
        if not _cancellation_acknowledged(result):
            return CancellationResult(acknowledged=False, reason="prefect-cancellation-unavailable")
        return CancellationResult(acknowledged=True)


def _canonical_uuid(value: object) -> str:
    """Require the exact canonical UUID that Prefect assigned to the worker."""
    if type(value) is not UUID:  # pylint: disable=unidiomatic-typecheck
        raise ValueError
    return str(value)


def _validate_pool_worker(worker: PoolWorker) -> None:
    """Validate every exact field of one internal worker observation."""
    if type(worker.worker_id) is not str:  # pylint: disable=unidiomatic-typecheck
        raise ValueError
    try:
        canonical_worker_id = str(UUID(worker.worker_id))
    except ValueError:
        raise ValueError from None
    if canonical_worker_id != worker.worker_id:
        raise ValueError
    if type(worker.status) is not str or worker.status not in {  # pylint: disable=unidiomatic-typecheck
        member.value.lower() for member in WorkerStatus
    }:
        raise ValueError
    heartbeat = worker.last_heartbeat
    if heartbeat is not None and (
        type(heartbeat) is not datetime  # pylint: disable=unidiomatic-typecheck
        or heartbeat.utcoffset() is None
    ):
        raise ValueError
    interval = worker.heartbeat_interval_seconds
    if interval is not None and (
        type(interval) is not float  # pylint: disable=unidiomatic-typecheck
        or interval <= 0
        or not isfinite(interval)
    ):
        raise ValueError


def _cancellation_acknowledged(result: object) -> bool:
    """Accept only Prefect's exact acknowledgement of the requested transition."""
    if type(result) is not OrchestrationResult:  # pylint: disable=unidiomatic-typecheck
        return False
    state = result.state
    return (
        result.status is SetStateStatus.ACCEPT
        and type(result.details) is StateAcceptDetails  # pylint: disable=unidiomatic-typecheck
        and type(state) is State[T]  # pylint: disable=unidiomatic-typecheck
        and type(state.type) is StateType  # pylint: disable=unidiomatic-typecheck
        and state.type is StateType.CANCELLING
    )


def normalized_pool_status(snapshot: object) -> PoolStatus:
    """Map any malformed orchestration snapshot to the fixed unavailable value."""
    if type(snapshot) is not PoolStatus:  # pylint: disable=unidiomatic-typecheck
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


def _exact_list(value: object) -> list[Any]:
    """Require the pinned Prefect collection response without generic ``len`` coercion."""
    if type(value) is not list:  # pylint: disable=unidiomatic-typecheck
        raise ValueError
    return cast("list[Any]", value)


def _status_value(value: object) -> str:
    """Accept only Prefect's status enum or an exact string test double."""
    if type(value) is WorkerStatus:  # pylint: disable=unidiomatic-typecheck
        return value.value.lower()
    if type(value) is not str:  # pylint: disable=unidiomatic-typecheck
        raise ValueError
    status = value.lower()
    if status not in {member.value.lower() for member in WorkerStatus}:
        raise ValueError
    return status


def _pool_worker(worker: Any) -> PoolWorker:
    """Parse one pinned Prefect worker record without coercing hostile values."""
    heartbeat = worker.last_heartbeat_time
    interval = worker.heartbeat_interval_seconds
    if heartbeat is not None and (
        type(heartbeat) is not datetime  # pylint: disable=unidiomatic-typecheck
        or heartbeat.utcoffset() is None
    ):
        raise ValueError
    if (
        type(interval) is not int  # pylint: disable=unidiomatic-typecheck
        or isinstance(interval, bool)
        or interval <= 0
        or not isfinite(interval)
    ):
        raise ValueError
    return PoolWorker(
        worker_id=_canonical_uuid(worker.id),
        status=_status_value(worker.status),
        last_heartbeat=heartbeat,
        heartbeat_interval_seconds=float(interval),
    )
