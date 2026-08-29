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
from prefect.client.schemas.objects import WorkerStatus
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
class PoolWorker:
    """Internal, non-public worker liveness evidence from Prefect."""

    worker_id: str
    status: str
    last_heartbeat: datetime | None
    heartbeat_interval_seconds: float | None


@dataclass(frozen=True, slots=True)
class PoolStatus:
    """One value-free work-pool observation."""

    detail_available: bool
    queue_depth: int | None
    observed_at: datetime | None
    workers: tuple[PoolWorker, ...] = ()


class ManagedOrchestration(Protocol):
    """Small orchestration boundary consumed by the HTTP service."""

    async def submit(self, parameters: dict[str, object], *, idempotency_key: str) -> Submission: ...

    async def observe(self, flow_run_id: str) -> Observation: ...

    async def pool_status(self, work_pool_name: str, now: datetime) -> PoolStatus: ...

    async def cancel(self, flow_run_id: str) -> Observation: ...


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
            workers = await client.read_workers_for_work_pool(work_pool_name)
            scheduled = await client.get_scheduled_flow_runs_for_work_pool(work_pool_name)
            parsed = tuple(_pool_worker(worker) for worker in workers)
            queue_depth = len(scheduled)
        except (ObjectNotFound, httpx.HTTPError, AttributeError, TypeError, ValueError):
            return PoolStatus(detail_available=False, queue_depth=None, observed_at=None)
        return PoolStatus(detail_available=True, queue_depth=queue_depth, observed_at=now, workers=parsed)

    async def cancel(self, flow_run_id: str) -> Observation:
        observed = await self.observe(flow_run_id)
        if not observed.available or observed.state in {"completed", "failed", "crashed", "cancelled"}:
            return observed
        try:
            await self._client.set_flow_run_state(UUID(flow_run_id), Cancelling())
        except (ObjectNotFound, httpx.HTTPError):
            return Observation(available=False, state=observed.state, reason="prefect-cancellation-unavailable")
        return await self.observe(flow_run_id)


def _canonical_uuid(value: object) -> str:
    """Require the exact canonical UUID that Prefect assigned to the worker."""
    if type(value) is not UUID:
        raise ValueError
    return str(value)


def _status_value(value: object) -> str:
    """Accept only Prefect's status enum or an exact string test double."""
    if type(value) is WorkerStatus:
        return value.value.lower()
    if type(value) is not str:
        raise ValueError
    return value.lower()


def _pool_worker(worker: Any) -> PoolWorker:
    """Parse one pinned Prefect worker record without coercing hostile values."""
    heartbeat = worker.last_heartbeat_time
    interval = worker.heartbeat_interval_seconds
    if heartbeat is not None and (type(heartbeat) is not datetime or heartbeat.utcoffset() is None):
        raise ValueError
    if type(interval) is not int or isinstance(interval, bool) or interval <= 0 or not isfinite(interval):
        raise ValueError
    return PoolWorker(
        worker_id=_canonical_uuid(worker.id),
        status=_status_value(worker.status),
        last_heartbeat=heartbeat,
        heartbeat_interval_seconds=float(interval),
    )
