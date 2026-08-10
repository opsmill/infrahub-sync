"""Prefect Extras translation and live-state access for the managed API."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

import httpx
from opsmill_prefect_extras.executors import (
    IdempotentWorkflowExecutor,
    RemoteExecutionClient,
    RemoteWorkflowExecutor,
)
from opsmill_prefect_extras.workflows import WorkflowDefinition
from prefect.exceptions import ObjectNotFound
from prefect.states import Cancelling

MANAGED_FLOW_NAME = "infrahub-sync-managed"
MANAGED_DEPLOYMENT_NAME = "run"
MANAGED_DEFINITION = WorkflowDefinition(
    flow_name=MANAGED_FLOW_NAME,
    deployment_name=MANAGED_DEPLOYMENT_NAME,
    module="infrahub_sync.managed.flow",
    function="managed_sync_run",
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


class ManagedOrchestration(Protocol):
    """Small orchestration boundary consumed by the HTTP service."""

    async def submit(self, parameters: dict[str, object], *, idempotency_key: str) -> Submission: ...

    async def observe(self, flow_run_id: str) -> Observation: ...

    async def cancel(self, flow_run_id: str) -> Observation: ...


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

    async def cancel(self, flow_run_id: str) -> Observation:
        observed = await self.observe(flow_run_id)
        if not observed.available or observed.state in {"completed", "failed", "crashed", "cancelled"}:
            return observed
        try:
            await self._client.set_flow_run_state(UUID(flow_run_id), Cancelling())
        except httpx.HTTPError:
            return Observation(available=False, state=observed.state, reason="prefect-cancellation-unavailable")
        return await self.observe(flow_run_id)
