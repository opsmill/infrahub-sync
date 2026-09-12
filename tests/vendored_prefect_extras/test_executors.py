"""Offline contract tests for local and remote workflow executors."""

from __future__ import annotations

import asyncio
import inspect
import logging
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any
from uuid import UUID, uuid4

import pytest
from prefect import flow, get_run_logger
from prefect.client.schemas.objects import FlowRun, State
from prefect.exceptions import MissingContextError, ObjectNotFound, ParameterTypeError
from prefect.flows import Flow
from prefect.states import (
    Cancelled,
    Cancelling,
    Completed,
    Crashed,
    Failed,
    Paused,
    Pending,
    Running,
    Scheduled,
    Suspended,
)

import opsmill_prefect_extras.executors as executors_module
from opsmill_prefect_extras.executors import (
    CancelledFlowRunError,
    CrashedFlowRunError,
    ExecutorClosedError,
    FailedFlowRunError,
    FlowRunError,
    IdempotentWorkflowExecutor,
    LocalWorkflowExecutor,
    MissingFlowRunResultError,
    RemoteWorkflowExecutor,
    WorkflowExecutor,
    WorkflowNotFoundError,
    WorkflowRunHandle,
)
from opsmill_prefect_extras.workflows.definitions import WorkflowDefinition


def test_public_surface_keeps_concrete_handles_private() -> None:
    """Concrete handle types remain private implementation details."""
    assert "WorkflowRunHandle" in executors_module.__all__
    assert "IdempotentWorkflowExecutor" in executors_module.__all__
    assert "RemoteFlowRunHandle" not in executors_module.__all__
    assert not hasattr(executors_module, "RemoteFlowRunHandle")


def test_idempotency_capability_is_explicit_and_remote_only() -> None:
    """The extension and remote implementation expose keys; base and local do not."""
    for method_name in ("run", "submit"):
        assert (
            "idempotency_key"
            in inspect.signature(
                getattr(RemoteWorkflowExecutor, method_name)
            ).parameters
        )
        assert (
            "idempotency_key"
            in inspect.signature(
                getattr(IdempotentWorkflowExecutor, method_name)
            ).parameters
        )
        assert (
            "idempotency_key"
            not in inspect.signature(
                getattr(LocalWorkflowExecutor, method_name)
            ).parameters
        )
        assert (
            "idempotency_key"
            not in inspect.signature(getattr(WorkflowExecutor, method_name)).parameters
        )


def _definition(
    # Any: test fixture accommodates Prefect flows with varied user signatures.
    monkeypatch: pytest.MonkeyPatch,
    flow_object: Flow[..., Any],
) -> WorkflowDefinition:
    """Expose one real Prefect flow through a temporary definition module."""
    module_name = f"executor_fixture_{uuid4().hex}"
    module = ModuleType(module_name)
    module.__dict__["workflow"] = flow_object
    monkeypatch.setitem(sys.modules, module_name, module)
    return WorkflowDefinition(
        flow_name=flow_object.name,
        deployment_name="run",
        module=module_name,
        function="workflow",
    )


@dataclass
class _Deployment:
    """Minimal offline Prefect deployment response."""

    id: UUID


@dataclass
class _FlowRun:
    """Minimal offline Prefect flow-run response."""

    id: UUID
    state: State[Any] | None


class _Client:
    """A deterministic in-memory seam, never a Prefect server client."""

    def __init__(self, state: State[Any] | None = None) -> None:
        self.deployment = _Deployment(uuid4())
        self.flow_run: _FlowRun | FlowRun = _FlowRun(
            uuid4(), state or Completed(data="remote result")
        )
        self.deployment_names: list[str] = []
        # Any: Prefect validates arbitrary application parameter value types.
        self.submissions: list[tuple[UUID, dict[str, Any], str | None]] = []
        self.reads: int = 0

    async def read_deployment_by_name(self, name: str) -> _Deployment:
        """Record the requested deployment target."""
        self.deployment_names.append(name)
        return self.deployment

    async def create_flow_run_from_deployment(
        self,
        deployment_id: UUID,
        *,
        parameters: dict[str, Any],
        idempotency_key: str | None = None,
    ) -> _FlowRun | FlowRun:
        """Record one accepted in-memory remote submission."""
        self.submissions.append((deployment_id, parameters, idempotency_key))
        return self.flow_run

    async def read_flow_run(self, flow_run_id: UUID) -> _FlowRun | FlowRun:
        """Return the configured state for the real-looking run identity."""
        assert flow_run_id == self.flow_run.id
        self.reads += 1
        return self.flow_run

    async def set_flow_run_state(self, flow_run_id: UUID, state: State[Any]) -> object:
        """Apply a requested fake state transition."""
        assert flow_run_id == self.flow_run.id
        self.flow_run.state = state
        return object()


class _DeduplicatingClient(_Client):
    """A fake server that applies Prefect-style creation-key deduplication."""

    def __init__(self) -> None:
        super().__init__()
        self._runs_by_key: dict[str, _FlowRun] = {}
        self.creation_count = 0

    async def create_flow_run_from_deployment(
        self,
        deployment_id: UUID,
        *,
        parameters: dict[str, Any],
        idempotency_key: str | None = None,
    ) -> _FlowRun:
        """Return a prior run for a key, otherwise create one server-side."""
        self.submissions.append((deployment_id, parameters, idempotency_key))
        if idempotency_key is not None and idempotency_key in self._runs_by_key:
            return self._runs_by_key[idempotency_key]
        flow_run = _FlowRun(uuid4(), Completed(data="remote result"))
        self.creation_count += 1
        if idempotency_key is not None:
            self._runs_by_key[idempotency_key] = flow_run
        return flow_run


def test_remote_executor_structurally_satisfies_idempotency_capability() -> None:
    """Static checking makes this call fail if the remote surface drifts."""

    def accept(executor: IdempotentWorkflowExecutor) -> IdempotentWorkflowExecutor:
        return executor

    assert isinstance(accept(RemoteWorkflowExecutor(_Client())), RemoteWorkflowExecutor)


@pytest.mark.parametrize("mode", ["local", "remote"])
def test_shared_executor_contract_returns_results_and_handles(
    monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    """Both implementations satisfy the run/submit/result/status contract."""

    @flow(name="contract-flow")
    def contract_flow(value: int) -> str:
        return f"value={value}"

    definition = _definition(monkeypatch, contract_flow)

    async def assert_contract(
        executor: LocalWorkflowExecutor | RemoteWorkflowExecutor,
    ) -> None:
        assert await executor.run(definition, {"value": 7}) == "value=7"
        handle = await executor.submit(definition, {"value": 7})
        assert await handle.wait() == "completed"
        assert await handle.status() == "completed"
        assert await handle.result() == "value=7"

    async def scenario() -> None:
        if mode == "local":
            local_executor = LocalWorkflowExecutor()
            await assert_contract(local_executor)
            await local_executor.shutdown()
            return
        remote_executor = RemoteWorkflowExecutor(_Client(Completed(data="value=7")))
        await assert_contract(remote_executor)

    asyncio.run(scenario())


def test_local_submit_is_immediate_event_gated_and_never_invokes_flow_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Local submit starts a managed task without server, process, or queue use."""
    started = asyncio.Event()
    release = asyncio.Event()

    @flow(name="gated-local-flow")
    async def gated_local_flow() -> str:
        started.set()
        await release.wait()
        return "released"

    definition = _definition(monkeypatch, gated_local_flow)

    def forbidden_flow_engine(*args: object, **kwargs: object) -> object:
        raise AssertionError("local executor must not invoke Flow.__call__")

    monkeypatch.setattr(Flow, "__call__", forbidden_flow_engine)

    async def scenario() -> None:
        executor = LocalWorkflowExecutor()
        handle = await executor.submit(definition, {})
        assert handle.id.startswith("local:")
        assert await handle.status() == "running"
        assert not started.is_set()
        await started.wait()
        release.set()
        assert await handle.result() == "released"
        await executor.shutdown()

    asyncio.run(scenario())


def test_local_flow_logger_requires_a_consumer_owned_context_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Flow.fn does not fabricate a Prefect logger context or start its engine."""

    @flow(name="logger-context-flow")
    def logger_context_flow() -> None:
        get_run_logger()

    definition = _definition(monkeypatch, logger_context_flow)

    def forbidden_flow_engine(*args: object, **kwargs: object) -> object:
        raise AssertionError("local executor must not invoke Flow.__call__")

    monkeypatch.setattr(Flow, "__call__", forbidden_flow_engine)

    async def scenario() -> None:
        executor = LocalWorkflowExecutor()
        with pytest.raises(FailedFlowRunError) as raised:
            await executor.run(definition, {})
        assert isinstance(raised.value.__cause__, MissingContextError)
        await executor.shutdown()

    asyncio.run(scenario())


def test_executor_docs_disclose_local_engine_limitations() -> None:
    """The public docs name the unavailable local-engine capabilities."""
    readme = Path(__file__).parents[1] / "README.md"
    module_documentation = executors_module.__doc__ or ""
    readme_documentation = readme.read_text()
    documentation = f"{module_documentation}\n{readme_documentation}"
    for required_concept in (
        "flow-run context",
        "MissingContextError",
        "retries",
        "hooks",
        "timeouts",
        "runtime logging",
        "consumer-owned fallback",
        "remote mode",
    ):
        assert required_concept in documentation
    logger_annotation = "logging.Logger | logging.LoggerAdapter[logging.Logger]"
    assert logger_annotation in module_documentation
    assert logger_annotation in readme_documentation


def test_local_async_flow_leaves_the_callers_event_loop_responsive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An async flow yields while its caller can make independent progress."""
    started = asyncio.Event()
    release = asyncio.Event()
    caller_progressed = asyncio.Event()

    @flow(name="async-progress-flow")
    async def async_progress_flow() -> str:
        started.set()
        await release.wait()
        return "done"

    definition = _definition(monkeypatch, async_progress_flow)

    async def scenario() -> None:
        executor = LocalWorkflowExecutor()
        handle = await executor.submit(definition, {})
        await started.wait()
        caller_progressed.set()
        assert caller_progressed.is_set()
        release.set()
        assert await handle.result() == "done"
        await executor.shutdown()

    asyncio.run(scenario())


def test_local_sync_flow_runs_off_the_callers_event_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A blocking synchronous flow runs in a thread while the loop progresses."""
    started = threading.Event()
    release = threading.Event()
    caller_progressed = asyncio.Event()

    @flow(name="sync-progress-flow")
    def sync_progress_flow() -> str:
        started.set()
        release.wait()
        return "done"

    definition = _definition(monkeypatch, sync_progress_flow)

    async def scenario() -> None:
        executor = LocalWorkflowExecutor()
        handle = await executor.submit(definition, {})
        await asyncio.to_thread(started.wait)
        caller_progressed.set()
        assert caller_progressed.is_set()
        release.set()
        assert await handle.result() == "done"
        await executor.shutdown()

    asyncio.run(scenario())


def test_local_shutdown_drains_completed_work_and_cancels_pending_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Shutdown rejects new work, drains ready work, and cancels the rest."""
    started = asyncio.Event()
    release = asyncio.Event()

    @flow(name="shutdown-flow")
    async def shutdown_flow() -> str:
        started.set()
        await release.wait()
        return "done"

    definition = _definition(monkeypatch, shutdown_flow)

    async def scenario() -> None:
        draining_executor = LocalWorkflowExecutor()
        draining_handle = await draining_executor.submit(definition, {})
        await started.wait()
        release.set()
        await draining_executor.shutdown(timeout=1)
        assert await draining_handle.result() == "done"

        never_finishes = asyncio.Event()
        second_started = asyncio.Event()

        @flow(name="cancelled-by-shutdown")
        async def blocked_flow() -> None:
            second_started.set()
            await never_finishes.wait()

        blocked_definition = _definition(monkeypatch, blocked_flow)
        cancelling_executor = LocalWorkflowExecutor()
        cancelled_handle = await cancelling_executor.submit(blocked_definition, {})
        await second_started.wait()
        await cancelling_executor.shutdown(timeout=0)
        assert await cancelled_handle.wait() == "cancelled"
        with pytest.raises(ExecutorClosedError):
            await cancelling_executor.submit(blocked_definition, {})

    asyncio.run(scenario())


def test_cancelled_local_handle_is_terminal_and_caller_retains_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A caller-held local handle retains cancellation after active tracking."""
    started = asyncio.Event()
    never_finishes = asyncio.Event()

    @flow(name="cancel-local-flow")
    async def cancel_local_flow() -> None:
        started.set()
        await never_finishes.wait()

    definition = _definition(monkeypatch, cancel_local_flow)

    async def scenario() -> None:
        executor = LocalWorkflowExecutor()
        handle = await executor.submit(definition, {})
        await started.wait()
        assert await handle.cancel() == "cancelled"
        assert await handle.status() == "cancelled"
        with pytest.raises(CancelledFlowRunError) as raised:
            await handle.result()
        assert isinstance(raised.value.__cause__, asyncio.CancelledError)
        await executor.shutdown()

    asyncio.run(scenario())


def test_cancelling_a_handle_waiter_does_not_cancel_the_local_flow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Caller cancellation propagates while the shielded local task continues."""
    started = asyncio.Event()
    release = asyncio.Event()

    @flow(name="caller-cancellation-flow")
    async def caller_cancellation_flow() -> str:
        started.set()
        await release.wait()
        return "finished"

    definition = _definition(monkeypatch, caller_cancellation_flow)

    async def scenario() -> None:
        executor = LocalWorkflowExecutor()
        handle = await executor.submit(definition, {})
        await started.wait()
        result_waiting = asyncio.Event()
        wait_waiting = asyncio.Event()

        async def await_result() -> object:
            result_waiting.set()
            return await handle.result()

        async def await_status() -> str:
            wait_waiting.set()
            return await handle.wait()

        result_waiter = asyncio.create_task(await_result())
        wait_waiter = asyncio.create_task(await_status())
        await result_waiting.wait()
        await wait_waiting.wait()
        result_waiter.cancel()
        wait_waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await result_waiter
        with pytest.raises(asyncio.CancelledError):
            await wait_waiter
        assert await handle.status() == "running"
        release.set()
        assert await handle.result() == "finished"
        await executor.shutdown()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("mode", "error_type"),
    [
        ("local-failure", FailedFlowRunError),
        ("remote-failure", FailedFlowRunError),
        ("local-cancellation", CancelledFlowRunError),
        ("remote-cancellation", CancelledFlowRunError),
    ],
)
def test_local_and_remote_terminal_failures_share_the_error_taxonomy(
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    error_type: type[FlowRunError],
) -> None:
    """Both handle types classify failures identically and preserve local causes."""
    started = asyncio.Event()
    release = asyncio.Event()

    @flow(name="shared-terminal-flow")
    async def shared_terminal_flow() -> None:
        started.set()
        await release.wait()
        if mode == "local-failure":
            raise ValueError("local failure")

    definition = _definition(monkeypatch, shared_terminal_flow)

    async def scenario() -> None:
        if mode.startswith("local"):
            executor = LocalWorkflowExecutor()
            handle = await executor.submit(definition, {})
            await started.wait()
            if mode == "local-cancellation":
                await handle.cancel()
            else:
                release.set()
            with pytest.raises(error_type) as raised:
                await handle.result()
            if mode == "local-failure":
                assert isinstance(raised.value.__cause__, ValueError)
            if mode == "local-cancellation":
                assert isinstance(raised.value.__cause__, asyncio.CancelledError)
            await executor.shutdown()
            return

        remote_state = (
            Failed(data=RuntimeError("remote failure"))
            if mode == "remote-failure"
            else Cancelled()
        )
        handle = await RemoteWorkflowExecutor(_Client(remote_state)).submit(
            definition, {}
        )
        with pytest.raises(error_type):
            await handle.result()

    asyncio.run(scenario())


def test_parameter_rejection_is_identical_before_execution_or_submission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both modes use Prefect's parameter validator before accepting work."""

    @flow(name="parameter-flow")
    def parameter_flow(required_number: int) -> int:
        return required_number

    definition = _definition(monkeypatch, parameter_flow)

    async def scenario() -> None:
        local = LocalWorkflowExecutor()
        remote_client = _Client()
        remote = RemoteWorkflowExecutor(remote_client)
        with pytest.raises(ParameterTypeError) as local_error:
            await local.submit(definition, {"required_number": "not-a-number"})
        with pytest.raises(ParameterTypeError) as remote_error:
            await remote.submit(definition, {"required_number": "not-a-number"})
        assert type(local_error.value) is type(remote_error.value)
        assert not remote_client.deployment_names
        await local.shutdown()

    asyncio.run(scenario())


def test_remote_submit_targets_definition_and_returns_real_server_run_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Remote submission speaks only the definition's deployment identity."""

    @flow(name="inventory-refresh")
    def remote_flow(full: bool) -> str:
        return "not executed remotely"

    definition = _definition(monkeypatch, remote_flow)
    client = _Client()

    async def scenario() -> None:
        executor = RemoteWorkflowExecutor(client)
        handle = await executor.submit(definition, {"full": False})
        assert handle.id == str(client.flow_run.id)
        assert client.deployment_names == ["inventory-refresh/run"]
        assert client.submissions == [(client.deployment.id, {"full": False}, None)]

    asyncio.run(scenario())


@pytest.mark.parametrize("method_name", ["run", "submit"])
def test_remote_idempotency_key_passes_through_exactly(
    monkeypatch: pytest.MonkeyPatch, method_name: str
) -> None:
    """Both concrete remote entry points pass the consumer key unchanged."""

    @flow(name="idempotent-remote-flow")
    def remote_flow(full: bool) -> str:
        return "not executed remotely"

    definition = _definition(monkeypatch, remote_flow)
    client = _Client(Completed(data="remote result"))

    async def scenario() -> None:
        executor = RemoteWorkflowExecutor(client)
        method = getattr(executor, method_name)
        await method(
            definition,
            {"full": False},
            idempotency_key="consumer-owned/key:001",
        )
        assert client.submissions == [
            (
                client.deployment.id,
                {"full": False},
                "consumer-owned/key:001",
            )
        ]

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("keys", "same_run", "creation_count"),
    [
        pytest.param(("same-key", "same-key"), True, 1, id="same-key-dedupes"),
        pytest.param(("key-one", "key-two"), False, 2, id="distinct-keys-create"),
        pytest.param((None, None), False, 2, id="omission-creates-normally"),
    ],
)
def test_remote_fake_server_idempotency_creation_semantics(
    monkeypatch: pytest.MonkeyPatch,
    keys: tuple[str | None, str | None],
    same_run: bool,
    creation_count: int,
) -> None:
    """The server, not the executor, deduplicates only equal supplied keys."""

    @flow(name="idempotency-server-flow")
    def remote_flow() -> None:
        return None

    definition = _definition(monkeypatch, remote_flow)
    client = _DeduplicatingClient()

    async def scenario() -> None:
        executor = RemoteWorkflowExecutor(client)

        async def submit(key: str | None) -> WorkflowRunHandle:
            if key is None:
                return await executor.submit(definition, {})
            return await executor.submit(definition, {}, idempotency_key=key)

        first = await submit(keys[0])
        second = await submit(keys[1])
        assert (first.id == second.id) is same_run
        assert client.creation_count == creation_count
        assert [submission[2] for submission in client.submissions] == list(keys)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("state", "error_type"),
    [
        (Crashed(), CrashedFlowRunError),
        (Failed(data=RuntimeError("failed")), FailedFlowRunError),
        (Completed(), MissingFlowRunResultError),
    ],
)
def test_remote_terminal_faults_raise_narrow_errors_with_server_id(
    monkeypatch: pytest.MonkeyPatch, state: State[Any], error_type: type[FlowRunError]
) -> None:
    """Crashes, failures, and unavailable results never become generic errors."""

    @flow(name="remote-fault-flow")
    def remote_fault_flow() -> None:
        return None

    definition = _definition(monkeypatch, remote_fault_flow)
    client = _Client(state)

    async def scenario() -> None:
        executor = RemoteWorkflowExecutor(client)
        handle = await executor.submit(definition, {})
        with pytest.raises(error_type) as raised:
            await handle.result()
        assert raised.value.run_id == str(client.flow_run.id)

    asyncio.run(scenario())


@pytest.mark.parametrize("failure_point", ["lookup", "deletion-race"])
def test_remote_not_found_translation_discards_provider_details(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    failure_point: str,
) -> None:
    """Both not-found paths expose only the safe deployment target."""

    provider_canary = "provider-secret-token=https://private.invalid"

    @flow(name="absent-flow")
    def absent_flow() -> None:
        return None

    definition = _definition(monkeypatch, absent_flow)

    class NotFoundClient(_Client):
        """Raise a canary-bearing provider error at either submission step."""

        async def read_deployment_by_name(self, name: str) -> _Deployment:
            if failure_point == "lookup":
                raise ObjectNotFound(Exception(provider_canary), provider_canary)
            return await super().read_deployment_by_name(name)

        async def create_flow_run_from_deployment(
            self,
            deployment_id: UUID,
            *,
            parameters: dict[str, Any],
            idempotency_key: str | None = None,
        ) -> _FlowRun:
            raise ObjectNotFound(Exception(provider_canary), provider_canary)

    async def scenario() -> None:
        with pytest.raises(WorkflowNotFoundError) as raised:
            await RemoteWorkflowExecutor(NotFoundClient()).submit(definition, {})

        error = raised.value
        logging.getLogger(__name__).error(
            "translated remote submission failure",
            exc_info=(type(error), error, error.__traceback__),
        )
        assert error.target == "absent-flow/run"
        assert "absent-flow/run" in str(error)
        assert provider_canary not in str(error)
        assert provider_canary not in repr(error)
        assert error.__cause__ is None
        assert error.__context__ is None
        assert provider_canary not in caplog.text

    asyncio.run(scenario())


def test_remote_cancel_requests_cancelling_then_observes_server_acknowledgement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Remote cancellation is a request, not a fabricated terminal result."""

    @flow(name="cancel-remote-flow")
    def cancel_remote_flow() -> None:
        return None

    definition = _definition(monkeypatch, cancel_remote_flow)

    class CancellingClient(_Client):
        """Record cancellation state transitions without a Prefect server."""

        def __init__(self) -> None:
            super().__init__(Running())
            self.cancellation_request: State[Any] | None = None
            self.cancellation_requests: int = 0

        async def set_flow_run_state(
            self, flow_run_id: UUID, state: State[Any]
        ) -> object:
            assert flow_run_id == self.flow_run.id
            self.cancellation_request = state
            self.cancellation_requests += 1
            self.flow_run.state = state
            return object()

    client = CancellingClient()

    async def scenario() -> None:
        handle = await RemoteWorkflowExecutor(client).submit(definition, {})
        assert await handle.cancel() == "cancelling"
        assert client.cancellation_request is not None
        assert client.cancellation_request.is_cancelling()
        assert await handle.status() == "cancelling"
        client.flow_run.state = Cancelled()
        assert await handle.wait() == "cancelled"
        assert await handle.cancel() == "cancelled"
        assert client.cancellation_requests == 1

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("state", "expected_status"),
    [
        pytest.param(None, "failed", id="absent"),
        pytest.param(Scheduled(), "pending", id="scheduled"),
        pytest.param(Pending(), "pending", id="pending"),
        pytest.param(Running(), "running", id="running"),
        pytest.param(Cancelling(), "cancelling", id="cancelling"),
        pytest.param(Paused(), "paused", id="paused"),
        pytest.param(Suspended(), "suspended", id="suspended"),
        pytest.param(Completed(), "completed", id="completed"),
        pytest.param(Failed(), "failed", id="failed"),
        pytest.param(Crashed(), "crashed", id="crashed"),
        pytest.param(Cancelled(), "cancelled", id="cancelled"),
    ],
)
def test_remote_status_maps_every_public_prefect_state(
    monkeypatch: pytest.MonkeyPatch,
    state: State[Any] | None,
    expected_status: str,
) -> None:
    """Remote status distinguishes every supported portable state."""

    @flow(name="status-mapping-flow")
    def status_mapping_flow() -> None:
        return None

    definition = _definition(monkeypatch, status_mapping_flow)
    client = _Client()
    client.flow_run.state = state

    async def scenario() -> None:
        handle = await RemoteWorkflowExecutor(client).submit(definition, {})
        assert await handle.status() == expected_status

    asyncio.run(scenario())


def test_remote_none_state_from_real_prefect_flow_run_is_typed_and_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A server response with no state fails rather than polling forever."""

    @flow(name="missing-state-flow")
    def missing_state_flow() -> None:
        return None

    definition = _definition(monkeypatch, missing_state_flow)
    client = _Client()
    # model_construct is offline and exercises Prefect's actual FlowRun shape.
    client.flow_run = FlowRun.model_construct(id=uuid4(), state=None)

    async def scenario() -> None:
        handle = await RemoteWorkflowExecutor(client).submit(definition, {})
        assert await handle.status() == "failed"
        assert await handle.wait() == "failed"
        with pytest.raises(MissingFlowRunResultError) as raised:
            await handle.result()
        assert raised.value.run_id == str(client.flow_run.id)
        assert await handle.cancel() == "failed"

    asyncio.run(scenario())


def test_discarded_local_failure_is_consumed_but_retained_handle_still_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Task completion consumes unobserved errors without changing retained handles."""
    failed = asyncio.Event()

    @flow(name="discarded-failure-flow")
    async def discarded_failure_flow() -> None:
        failed.set()
        raise ValueError("discarded failure")

    definition = _definition(monkeypatch, discarded_failure_flow)

    async def scenario() -> None:
        executor = LocalWorkflowExecutor()
        loop = asyncio.get_running_loop()
        contexts: list[dict[str, object]] = []
        previous_handler = loop.get_exception_handler()
        loop.set_exception_handler(
            lambda current_loop, context: contexts.append(dict(context))
        )
        try:
            discarded_handle = await executor.submit(definition, {})
            del discarded_handle
            await failed.wait()
            turn = loop.create_future()
            loop.call_soon(turn.set_result, None)
            await turn
            assert not contexts

            retained_handle = await executor.submit(definition, {})
            with pytest.raises(FailedFlowRunError) as raised:
                await retained_handle.result()
            assert isinstance(raised.value.__cause__, ValueError)
        finally:
            loop.set_exception_handler(previous_handler)
            await executor.shutdown()

    asyncio.run(scenario())
