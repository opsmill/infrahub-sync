"""Execute declared Prefect workflows locally or through a deployment.

Applications choose an executor at their composition root.  Both
:class:`LocalWorkflowExecutor` and :class:`RemoteWorkflowExecutor` accept the
same :class:`~opsmill_prefect_extras.workflows.definitions.WorkflowDefinition`
and a mapping of parameters.  :meth:`WorkflowExecutor.run` waits for and
returns the flow result; :meth:`WorkflowExecutor.submit` returns an execution
handle as soon as the execution was accepted.

The modes intentionally have different operational properties:

* Local runs execute ``Flow.fn`` in this Python process after Prefect validates
  the parameters.  They have a ``local:`` identity, are not Prefect flow-run
  IDs, create no server history or UI entry, and disappear on process restart.
  Cancelling a local handle cancels its asyncio task; it cannot stop synchronous
  work that is already running in its worker thread.
* Remote runs are addressed as ``flow_name/deployment_name`` and have the
  server-assigned Prefect flow-run ID.  Their durability, restart survival, and
  UI/history are supplied by the configured Prefect server.  Cancellation is a
  request sent to that server; its worker decides when it takes effect.  The
  :class:`IdempotentWorkflowExecutor` capability additionally accepts Prefect's
  optional ``idempotency_key``; consumers own its stable derivation and must
  not put private data in it. :class:`RemoteWorkflowExecutor` satisfies that
  capability; local execution deliberately does not.

Local execution intentionally has no Prefect flow-run context or engine.
Calling :func:`prefect.get_run_logger` inside a local ``Flow.fn`` therefore
raises :class:`prefect.exceptions.MissingContextError` unless the consumer flow
provides its own fallback. Prefect retries, hooks, timeouts, runtime logging,
state, and history are bypassed. Consumers needing full engine semantics must
use remote mode. A generic consumer-owned fallback catches *only*
``MissingContextError`` around ``get_run_logger`` and uses a normal module
logger instead::

    import logging

    from prefect import get_run_logger
    from prefect.exceptions import MissingContextError

    def workflow_logger() -> logging.Logger | logging.LoggerAdapter[logging.Logger]:
        try:
            return get_run_logger()
        except MissingContextError:
            return logging.getLogger(__name__)

The local executor deliberately retains only active tasks.  A caller-held
handle retains its completed result or exception, while the executor keeps no
completed-run registry, persistence, or TTL cache.  Call :meth:`shutdown` at
application shutdown to reject new submissions, drain active tasks for a
bounded period, and cancel any work still pending.

Example:
    An application chooses local execution for a test or single-process mode::

        from opsmill_prefect_extras.executors import LocalWorkflowExecutor

        executor = LocalWorkflowExecutor()
        result = await executor.run(INVENTORY_REFRESH, {"full": False})
        await executor.shutdown()

    Production wiring owns the Prefect client lifecycle and supplies it to the
    remote executor::

        from prefect.client.orchestration import get_client

        from opsmill_prefect_extras.executors import RemoteWorkflowExecutor

        async with get_client() as client:
            executor = RemoteWorkflowExecutor(client)
            handle = await executor.submit(INVENTORY_REFRESH, {"full": False})
            result = await handle.result()
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, Protocol
from uuid import UUID, uuid4

from prefect.client.schemas.objects import State
from prefect.exceptions import MissingResult, ObjectNotFound
from prefect.flows import Flow
from prefect.states import Cancelling

from opsmill_prefect_extras.workflows.definitions import WorkflowDefinition

ExecutionStatus = Literal[
    "pending",
    "running",
    "cancelling",
    "paused",
    "suspended",
    "completed",
    "failed",
    "crashed",
    "cancelled",
]
"""The portable execution state exposed by a run handle."""

__all__: list[str] = [
    "CancelledFlowRunError",
    "CrashedFlowRunError",
    "ExecutionStatus",
    "ExecutorClosedError",
    "FailedFlowRunError",
    "FlowRunError",
    "IdempotentWorkflowExecutor",
    "LocalWorkflowExecutor",
    "MissingFlowRunResultError",
    "RemoteExecutionClient",
    "RemoteWorkflowExecutor",
    "WorkflowExecutor",
    "WorkflowNotFoundError",
    "WorkflowRunHandle",
]


class WorkflowRunHandle(Protocol):
    """A retained reference to one accepted workflow execution.

    ``id`` is a ``local:`` identity for local handles and the real Prefect
    flow-run ID for remote handles.  ``result`` waits for terminal completion
    and returns the flow result, or raises the documented mode-specific error.
    """

    @property
    def id(self) -> str:
        """Return this execution's identity."""

    async def status(self) -> ExecutionStatus:
        """Inspect the current portable execution status."""

    async def wait(self) -> ExecutionStatus:
        """Wait until terminal status and return it."""

    async def result(self) -> object:
        """Wait for terminal completion and return the flow result."""

    async def cancel(self) -> ExecutionStatus:
        """Request cancellation and return the status observed afterward."""


class WorkflowExecutor(Protocol):
    """The application-facing execution contract.

    Implementations use Prefect's ``Flow.validate_parameters`` before they
    accept work, so a definition rejects the same parameter shape in both
    modes.
    """

    async def run(
        self, definition: WorkflowDefinition, parameters: Mapping[str, object]
    ) -> object:
        """Execute one definition, wait for it, and return its flow result."""

    async def submit(
        self, definition: WorkflowDefinition, parameters: Mapping[str, object]
    ) -> WorkflowRunHandle:
        """Accept one definition and immediately return its retained handle."""


class IdempotentWorkflowExecutor(WorkflowExecutor, Protocol):
    """Execution contract with server-backed run-creation idempotency.

    Consumers that require deduplicated submission can depend on this capability
    without naming a concrete executor. Implementations pass the optional key to
    their durable run-creation boundary; they do not derive or store it.
    """

    async def run(
        self,
        definition: WorkflowDefinition,
        parameters: Mapping[str, object],
        *,
        idempotency_key: str | None = None,
    ) -> object:
        """Execute one definition, optionally idempotently, and return its result."""

    async def submit(
        self,
        definition: WorkflowDefinition,
        parameters: Mapping[str, object],
        *,
        idempotency_key: str | None = None,
    ) -> WorkflowRunHandle:
        """Accept one definition with an optional run-creation key."""


class ExecutorClosedError(RuntimeError):
    """A local executor was shut down and cannot accept further submissions."""


class WorkflowNotFoundError(LookupError):
    """The remote deployment named by a definition does not exist."""

    target: str

    def __init__(self, definition: WorkflowDefinition) -> None:
        """Describe the missing ``flow_name/deployment_name`` target."""
        self.target = definition.key
        super().__init__(f"Prefect deployment {self.target!r} was not found")


class FlowRunError(RuntimeError):
    """A terminal local or remote workflow run cannot produce a normal result."""

    run_id: str

    def __init__(self, run_id: str, detail: str) -> None:
        """Keep the execution identity with a narrowly classified failure."""
        self.run_id = run_id
        super().__init__(f"workflow run {run_id} {detail}")


class FailedFlowRunError(FlowRunError):
    """A workflow run failed, preserving its original local cause when present."""

    def __init__(self, run_id: str) -> None:
        """Describe a failed workflow run."""
        super().__init__(run_id, "failed")


class CrashedFlowRunError(FlowRunError):
    """A remote run reached Prefect's crashed terminal state."""

    def __init__(self, run_id: str) -> None:
        """Describe a crashed workflow run."""
        super().__init__(run_id, "crashed")


class CancelledFlowRunError(FlowRunError):
    """A local or remote workflow run reached a cancelled terminal state."""

    def __init__(self, run_id: str) -> None:
        """Describe a cancelled workflow run."""
        super().__init__(run_id, "was cancelled")


class MissingFlowRunResultError(FlowRunError):
    """A remote run has no retrievable Prefect state or result."""

    def __init__(self, run_id: str) -> None:
        """Describe a remote run whose state or result is unavailable."""
        super().__init__(run_id, "has no retrievable state or result")


class _RemoteDeployment(Protocol):
    """The deployment identity required to create a remote flow run."""

    id: UUID


class _RemoteFlowRun(Protocol):
    """The subset of a Prefect flow-run response used by this feature."""

    id: UUID
    # Any: a Prefect state carries the consumer flow's arbitrary result type.
    state: State[Any] | None


class RemoteExecutionClient(Protocol):
    """Injectable, offline-testable seam over the Prefect execution calls."""

    async def read_deployment_by_name(self, name: str) -> _RemoteDeployment:
        """Read a deployment by its ``flow_name/deployment_name`` target."""

    async def create_flow_run_from_deployment(
        self,
        deployment_id: UUID,
        *,
        parameters: dict[str, Any],
        idempotency_key: str | None = None,
    ) -> _RemoteFlowRun:
        """Submit parameters and an optional server idempotency key."""

    async def read_flow_run(self, flow_run_id: UUID) -> _RemoteFlowRun:
        """Read the current remote flow-run state."""

    async def set_flow_run_state(self, flow_run_id: UUID, state: State[Any]) -> object:
        """Request a state transition for one remote flow run."""


if TYPE_CHECKING:
    from prefect.client.orchestration import PrefectClient

    def _prefect_client_is_a_remote_execution_client(
        client: PrefectClient,
    ) -> RemoteWorkflowExecutor:
        """Keep the real Prefect client structurally checked without connecting."""
        return RemoteWorkflowExecutor(client)


def _validated_flow_and_parameters(
    definition: WorkflowDefinition, parameters: Mapping[str, object]
) -> tuple[Flow[Any, Any], dict[str, object]]:
    """Resolve a real flow and ask Prefect to validate its parameters.

    Returns:
        The resolved Prefect flow and its Prefect-normalized parameters.

    Raises:
        TypeError: If the definition does not resolve to a Prefect flow.
        prefect.exceptions.ParameterTypeError: If Prefect rejects parameters.
    """
    flow = definition.load()
    # Flow parameters are dynamic application values; Prefect owns their types.
    validated: dict[str, Any] = flow.validate_parameters(dict(parameters))
    return flow, dict(validated)


async def _execute_local_flow(
    flow: Flow[Any, Any], parameters: dict[str, object]
) -> object:
    """Call a validated Prefect flow's underlying function without an engine.

    ``Flow.fn`` is intentionally used instead of invoking the ``Flow`` object:
    calling a Flow can start Prefect's temporary server.  The object was still
    resolved and parameter-validated by Prefect, while this execution remains
    in-process and needs no server, worker, queue, child process, or history.
    """
    # Flow is intentionally dynamic after definition resolution; its fn has the
    # consumer application's precise signature rather than a library signature.
    flow_function = flow.fn
    if inspect.iscoroutinefunction(flow_function):
        return await flow_function(**parameters)
    return await asyncio.to_thread(flow_function, **parameters)


@dataclass(slots=True)
class _LocalFlowRunHandle:
    """A caller-owned reference to one local background asyncio task."""

    _id: str
    _task: asyncio.Task[object]

    @property
    def id(self) -> str:
        """Return a local-only identity, never a Prefect flow-run ID."""
        return self._id

    async def status(self) -> ExecutionStatus:
        """Return the task's current portable status."""
        if not self._task.done():
            return "running"
        if self._task.cancelled():
            return "cancelled"
        if self._task.exception() is not None:
            return "failed"
        return "completed"

    async def wait(self) -> ExecutionStatus:
        """Wait for this task to finish without consuming its exception."""
        try:
            await asyncio.shield(self._task)
        except asyncio.CancelledError:
            if not self._task.cancelled():
                raise
        except Exception:
            pass
        return await self.status()

    async def result(self) -> object:
        """Return the retained result or raise the shared terminal error type."""
        try:
            return await asyncio.shield(self._task)
        except asyncio.CancelledError as exc:
            if self._task.cancelled():
                raise CancelledFlowRunError(self.id) from exc
            raise
        except Exception as exc:
            raise FailedFlowRunError(self.id) from exc

    async def cancel(self) -> ExecutionStatus:
        """Cancel local asyncio work unless it is already terminal."""
        if not self._task.done():
            self._task.cancel()
            await self.wait()
        return await self.status()


class LocalWorkflowExecutor:
    """Run flow functions locally without a Prefect server, context, or engine."""

    _active_tasks: set[asyncio.Task[object]]
    _closed: bool

    def __init__(self) -> None:
        """Create an executor with no active tasks and open submissions."""
        self._active_tasks = set()
        self._closed = False

    async def run(
        self, definition: WorkflowDefinition, parameters: Mapping[str, object]
    ) -> object:
        """Execute a flow and wait for its result.

        Raises:
            ExecutorClosedError: If shutdown has started.
            prefect.exceptions.ParameterTypeError: If Prefect rejects parameters.
            FailedFlowRunError: If the flow fails, with its original exception
                chained as the cause.
            CancelledFlowRunError: If a concurrent shutdown cancels the local
                task before it completes.
        """
        handle = await self.submit(definition, parameters)
        return await handle.result()

    async def submit(
        self, definition: WorkflowDefinition, parameters: Mapping[str, object]
    ) -> WorkflowRunHandle:
        """Validate and start a local background flow task.

        The returned handle owns its task after completion.  This executor
        removes completed tasks from its active set and retains no history.

        Raises:
            ExecutorClosedError: If shutdown has started.
            prefect.exceptions.ParameterTypeError: If Prefect rejects parameters.
        """
        if self._closed:
            raise ExecutorClosedError("local executor is shut down")
        flow, validated = _validated_flow_and_parameters(definition, parameters)
        task = asyncio.create_task(_execute_local_flow(flow, validated))
        self._active_tasks.add(task)
        task.add_done_callback(self._complete_task)
        return _LocalFlowRunHandle(f"local:{uuid4()}", task)

    def _complete_task(self, task: asyncio.Task[object]) -> None:
        """Drop active tracking and consume discarded task exceptions safely."""
        self._active_tasks.discard(task)
        if not task.cancelled():
            task.exception()

    async def shutdown(self, *, timeout: float = 5.0) -> None:
        """Stop accepting work, drain it briefly, then cancel pending tasks.

        Args:
            timeout: Maximum seconds to wait for active tasks before cancelling
                the ones still pending.  Must be non-negative.

        Raises:
            ValueError: If ``timeout`` is negative.
        """
        if timeout < 0:
            raise ValueError("shutdown timeout must be non-negative")
        self._closed = True
        tasks = tuple(self._active_tasks)
        if not tasks:
            return
        _, pending = await asyncio.wait(tasks, timeout=timeout)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)


def _status_for_state(state: State[Any] | None) -> ExecutionStatus:
    """Map Prefect state, including absent API state, into portable statuses."""
    if state is None:
        return "failed"
    if state.is_completed():
        return "completed"
    if state.is_failed():
        return "failed"
    if state.is_crashed():
        return "crashed"
    if state.is_cancelled():
        return "cancelled"
    if state.is_cancelling():
        return "cancelling"
    if state.is_paused():
        if getattr(state.state_details, "pause_reschedule", False):
            return "suspended"
        return "paused"
    if state.is_final():
        return "failed"
    if state.is_running():
        return "running"
    return "pending"


@dataclass(slots=True)
class _RemoteFlowRunHandle:
    """A handle for a real Prefect server flow run."""

    _client: RemoteExecutionClient
    _flow_run_id: UUID
    _poll_interval: float

    @property
    def id(self) -> str:
        """Return the server-assigned Prefect flow-run ID."""
        return str(self._flow_run_id)

    async def _state(self) -> State[Any] | None:
        """Read the latest state; ``None`` is an unrecoverable missing state."""
        return (await self._client.read_flow_run(self._flow_run_id)).state

    async def status(self) -> ExecutionStatus:
        """Read and map the current server state."""
        return _status_for_state(await self._state())

    async def _wait_for_terminal_state(self) -> State[Any] | None:
        """Poll until final, stopping immediately when Prefect omits state."""
        while True:
            state = await self._state()
            if state is None or state.is_final():
                return state
            await asyncio.sleep(self._poll_interval)

    async def wait(self) -> ExecutionStatus:
        """Wait for a terminal server state and return its portable status."""
        return _status_for_state(await self._wait_for_terminal_state())

    async def result(self) -> object:
        """Return the completed result or raise a typed terminal-state error."""
        state = await self._wait_for_terminal_state()
        if state is None:
            raise MissingFlowRunResultError(self.id)
        if state.is_crashed():
            raise CrashedFlowRunError(self.id)
        if state.is_failed():
            raise FailedFlowRunError(self.id)
        if state.is_cancelled():
            raise CancelledFlowRunError(self.id)
        if not state.is_completed():
            raise FailedFlowRunError(self.id)
        try:
            result = state.result()
            if inspect.isawaitable(result):
                return await result
            return result
        except MissingResult as exc:
            raise MissingFlowRunResultError(self.id) from exc

    async def cancel(self) -> ExecutionStatus:
        """Request server cancellation unless the flow run is already terminal."""
        state = await self._state()
        if state is None or state.is_final():
            return await self.status()
        await self._client.set_flow_run_state(self._flow_run_id, Cancelling())
        return await self.status()


class RemoteWorkflowExecutor:
    """Submit declared flows to already-created Prefect deployments."""

    _client: RemoteExecutionClient
    _poll_interval: float

    def __init__(
        self, client: RemoteExecutionClient, *, poll_interval: float = 1.0
    ) -> None:
        """Use an application-owned Prefect client to execute deployments.

        Args:
            client: An open Prefect orchestration client.  The application owns
                its context-manager lifetime for every returned handle.
            poll_interval: Seconds between remote state reads while waiting.

        Raises:
            ValueError: If ``poll_interval`` is negative.
        """
        if poll_interval < 0:
            raise ValueError("poll interval must be non-negative")
        self._client = client
        self._poll_interval = poll_interval

    async def run(
        self,
        definition: WorkflowDefinition,
        parameters: Mapping[str, object],
        *,
        idempotency_key: str | None = None,
    ) -> object:
        """Submit a deployment, optionally idempotently, and return its result.

        The key is passed unchanged to Prefect. Consumers own its derivation,
        stability, and privacy.
        """
        handle = await self.submit(
            definition, parameters, idempotency_key=idempotency_key
        )
        return await handle.result()

    async def submit(
        self,
        definition: WorkflowDefinition,
        parameters: Mapping[str, object],
        *,
        idempotency_key: str | None = None,
    ) -> WorkflowRunHandle:
        """Validate parameters and submit to the named remote deployment.

        ``idempotency_key`` is passed unchanged to Prefect. Consumers own its
        derivation, stability, and privacy; the executor stores no key registry.

        Raises:
            WorkflowNotFoundError: If the named deployment does not exist.
            prefect.exceptions.ParameterTypeError: If Prefect rejects parameters.
        """
        _, validated = _validated_flow_and_parameters(definition, parameters)
        try:
            deployment = await self._client.read_deployment_by_name(definition.key)
            flow_run = await self._client.create_flow_run_from_deployment(
                deployment.id,
                parameters=validated,
                idempotency_key=idempotency_key,
            )
        except ObjectNotFound:
            pass
        else:
            return _RemoteFlowRunHandle(self._client, flow_run.id, self._poll_interval)
        raise WorkflowNotFoundError(definition)
