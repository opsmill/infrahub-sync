"""The service worker starts only the service's own deployment, exactly as the service applied it.

A process worker executes whatever its work pool hands it, and everything that
decides what that child runs -- the deployment, its entrypoint and pull steps,
the pool's job template and any job variables -- is data on the Prefect server.
These tests pin the admission boundary: a flow run is refused before any
process starts unless every one of those facts is what the service itself
registered, and the refusal is recorded on the flow run as a crash with a fixed
reason.

The first group drives the real submission path with a fake client, so each
refusal can be staged in isolation. The last group runs against a real
temporary Prefect server, because what the server stores back -- the pool
template round trip, an absent pull-step list, a Crashed transition from
Scheduled -- is what the comparison actually meets in production.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from typing_extensions import Self

pytest.importorskip("prefect")

from prefect.client.orchestration import get_client
from prefect.client.schemas.actions import WorkPoolCreate
from prefect.client.schemas.objects import StateType, WorkerStatus
from prefect.testing.utilities import prefect_test_harness
from prefect.workers.process import ProcessJobConfiguration, ProcessWorker, ProcessWorkerResult

from infrahub_sync.service import worker as worker_module
from infrahub_sync.service.orchestration import SERVICE_DEFINITION
from infrahub_sync.service.worker import ServiceFlowRunRefusedError, ServiceProcessWorker, service_worker_name

if TYPE_CHECKING:
    from collections.abc import Iterator

    from prefect.client.schemas.objects import FlowRun, WorkPool

POOL_NAME = "service-pool"
POOL_ID = UUID("6f3f7a0e-2f7e-4d55-9a53-0b8a3c0f5a11")
WORKER_ID = UUID("0d9e3c55-8f2a-4b7c-9e61-2b0f4c7d8e90")
SERVICE_FLOW_ID = UUID("b1c2d3e4-f5a6-4b7c-8d9e-0f1a2b3c4d5e")
SERVICE_DEPLOYMENT_ID = UUID("c2d3e4f5-a6b7-4c8d-9e0f-1a2b3c4d5e6f")
FOREIGN_FLOW_ID = UUID("d3e4f5a6-b7c8-4d9e-8f1a-2b3c4d5e6f70")
REFUSAL_PREFIX = "Failed to submit flow run to infrastructure: ServiceFlowRunRefusedError"
OVERRIDDEN_COMMAND = "sh -c true"


def _service_deployment(**changes: object) -> SimpleNamespace:
    fields: dict[str, object] = {
        "id": SERVICE_DEPLOYMENT_ID,
        "name": SERVICE_DEFINITION.deployment_name,
        "flow_id": SERVICE_FLOW_ID,
        "entrypoint": SERVICE_DEFINITION.entrypoint,
        "pull_steps": [],
        "job_variables": {},
        "updated": None,
    }
    fields.update(changes)
    return SimpleNamespace(**fields)


class _Client:
    def __init__(self, deployment: SimpleNamespace | None, flows: dict[UUID, str]) -> None:
        self.deployment = deployment
        self.flows = flows

    async def read_workers_for_work_pool(  # noqa: PLR6301 - Prefect client protocol.
        self,
        work_pool_name: str,
        worker_filter: object = None,  # noqa: ARG002 - pinned Prefect client shape.
        offset: int | None = None,  # noqa: ARG002 - pinned Prefect client shape.
        limit: int | None = None,  # noqa: ARG002 - pinned Prefect client shape.
    ) -> list[object]:
        assert work_pool_name == POOL_NAME
        return [SimpleNamespace(id=WORKER_ID, name="service-a", work_pool_id=POOL_ID, status=WorkerStatus.ONLINE)]

    async def read_deployment(self, deployment_id: UUID) -> SimpleNamespace:
        assert self.deployment is not None
        assert deployment_id == self.deployment.id
        return self.deployment

    async def read_flow(self, flow_id: UUID) -> SimpleNamespace:
        return SimpleNamespace(id=flow_id, name=self.flows[flow_id], labels={})


class _Runner:
    """Stands in for the child start, the step after admission and identity checks."""

    def __init__(self) -> None:
        self.starts: list[dict[str, Any]] = []

    async def start(
        self,
        flow_run: FlowRun,
        configuration: ProcessJobConfiguration,
        task_status: Any,  # noqa: ANN401
    ) -> ProcessWorkerResult:
        self.starts.append({"flow_run_id": flow_run.id, "configuration": configuration, "env": configuration.env})
        task_status.started(42)
        return ProcessWorkerResult(status_code=0, identifier="42")


class _TaskStatus:
    """What Prefect's task group hands a submission; a refusal reports through it."""

    def __init__(self) -> None:
        self._future = SimpleNamespace(done=lambda: bool(self.values))
        self.values: list[object] = []

    def started(self, value: object = None) -> None:
        self.values.append(value)


async def _worker(
    deployment: SimpleNamespace | None,
    *,
    flows: dict[UUID, str] | None = None,
    base_job_template: dict[str, Any] | None = None,
) -> tuple[ServiceProcessWorker, _Runner]:
    worker = ServiceProcessWorker(work_pool_name=POOL_NAME, name="service-a")
    worker._client = cast("Any", _Client(deployment, flows or {SERVICE_FLOW_ID: SERVICE_DEFINITION.flow_name}))
    template = ProcessWorker.get_default_base_job_template() if base_job_template is None else base_job_template
    worker._work_pool = cast("WorkPool", SimpleNamespace(id=POOL_ID, name=POOL_NAME, base_job_template=template))
    await worker._refresh_worker_identity()
    runner = _Runner()
    worker._start_child = cast("Any", runner.start)  # type: ignore[method-assign]
    worker._emit_flow_run_submitted_event = cast("Any", lambda _configuration: None)  # type: ignore[method-assign]
    worker._give_worker_labels_to_flow_run = cast("Any", AsyncMock())  # type: ignore[method-assign]
    worker._propose_submitting_state = cast("Any", AsyncMock())  # type: ignore[method-assign]
    worker._propose_crashed_state = cast("Any", AsyncMock())  # type: ignore[method-assign]
    worker._release_limit_slot = cast("Any", lambda _flow_run_id: None)  # type: ignore[method-assign]
    return worker, runner


def _flow_run(
    *,
    deployment_id: UUID | None = SERVICE_DEPLOYMENT_ID,
    flow_id: UUID = SERVICE_FLOW_ID,
    job_variables: dict[str, Any] | None = None,
) -> FlowRun:
    return cast(
        "FlowRun",
        SimpleNamespace(
            id=uuid4(),
            name="service-run",
            flow_id=flow_id,
            deployment_id=deployment_id,
            parent_task_run_id=None,
            job_variables={} if job_variables is None else job_variables,
        ),
    )


async def _submit(worker: ServiceProcessWorker, flow_run: FlowRun) -> tuple[object, _TaskStatus]:
    status = _TaskStatus()
    result = await worker._submit_run_and_capture_errors(flow_run, cast("Any", status))
    return result, status


def _refused(worker: ServiceProcessWorker, runner: _Runner, result: object, status: _TaskStatus) -> str:
    """Assert a refusal before any start, and return the crash message it recorded."""
    assert isinstance(result, ServiceFlowRunRefusedError)
    assert runner.starts == [], "a refused flow run reached the child start"
    assert len(status.values) == 1
    assert isinstance(status.values[0], ServiceFlowRunRefusedError)
    crashed = cast("AsyncMock", worker._propose_crashed_state)
    crashed.assert_awaited_once()
    call = crashed.await_args
    assert call is not None
    message = call.args[1]
    assert message.startswith(REFUSAL_PREFIX)
    return message


async def test_the_service_deployment_as_applied_is_started() -> None:
    worker, runner = await _worker(_service_deployment())

    result, _status = await _submit(worker, _flow_run())

    assert not isinstance(result, Exception)
    assert len(runner.starts) == 1
    cast("AsyncMock", worker._propose_crashed_state).assert_not_awaited()
    # Prefect 3.8.6 runs an unconfigured command through its workspace supervisor,
    # which re-resolves the deployment and may pick another launcher; only a
    # configured command is run exactly as admitted.
    configuration = runner.starts[0]["configuration"]
    assert configuration._command_configured is True
    assert configuration.command == worker_module._SERVICE_CHILD_COMMAND


async def test_a_flow_run_without_a_deployment_is_refused() -> None:
    worker, runner = await _worker(None)

    result, status = await _submit(worker, _flow_run(deployment_id=None))

    _refused(worker, runner, result, status)


@pytest.mark.parametrize(
    ("deployment", "flows"),
    [
        pytest.param(
            _service_deployment(name="other"),
            {SERVICE_FLOW_ID: SERVICE_DEFINITION.flow_name},
            id="foreign-deployment-name",
        ),
        pytest.param(
            _service_deployment(flow_id=FOREIGN_FLOW_ID),
            {SERVICE_FLOW_ID: SERVICE_DEFINITION.flow_name},
            id="deployment-of-another-flow",
        ),
        pytest.param(
            _service_deployment(),
            {SERVICE_FLOW_ID: "other-flow"},
            id="foreign-flow-name",
        ),
        pytest.param(
            _service_deployment(entrypoint="other_package.flows:other"),
            {SERVICE_FLOW_ID: SERVICE_DEFINITION.flow_name},
            id="changed-entrypoint",
        ),
        pytest.param(
            _service_deployment(entrypoint=None),
            {SERVICE_FLOW_ID: SERVICE_DEFINITION.flow_name},
            id="missing-entrypoint",
        ),
    ],
)
async def test_a_foreign_deployment_is_refused_before_any_start(
    deployment: SimpleNamespace,
    flows: dict[UUID, str],
) -> None:
    worker, runner = await _worker(deployment, flows=flows)

    result, status = await _submit(worker, _flow_run())

    _refused(worker, runner, result, status)


async def test_pull_steps_on_the_service_deployment_are_refused() -> None:
    step = {"prefect.deployments.steps.run_shell_script": {"script": "true"}}
    worker, runner = await _worker(_service_deployment(pull_steps=[step]))

    result, status = await _submit(worker, _flow_run())

    _refused(worker, runner, result, status)


@pytest.mark.parametrize(
    "job_variables",
    [
        pytest.param({"command": OVERRIDDEN_COMMAND}, id="command"),
        pytest.param({"working_dir": "/tmp"}, id="working-directory"),  # noqa: S108 - a value, never used.
        pytest.param({"env": {"PYTHONPATH": "/tmp"}}, id="environment"),  # noqa: S108 - a value, never used.
    ],
)
async def test_flow_run_job_variables_are_refused(job_variables: dict[str, Any]) -> None:
    worker, runner = await _worker(_service_deployment())

    result, status = await _submit(worker, _flow_run(job_variables=job_variables))

    _refused(worker, runner, result, status)


async def test_deployment_job_variables_are_refused() -> None:
    worker, runner = await _worker(_service_deployment(job_variables={"command": OVERRIDDEN_COMMAND}))

    result, status = await _submit(worker, _flow_run())

    _refused(worker, runner, result, status)


async def test_a_changed_pool_job_template_is_refused() -> None:
    template = ProcessWorker.get_default_base_job_template()
    template["variables"]["properties"]["command"]["default"] = OVERRIDDEN_COMMAND
    worker, runner = await _worker(_service_deployment(), base_job_template=template)

    result, status = await _submit(worker, _flow_run())

    _refused(worker, runner, result, status)


async def test_the_refusal_reason_does_not_echo_server_supplied_values() -> None:
    """The crash message crosses to the Prefect server; it names the rule, never the value."""
    marker = "value-that-must-not-be-echoed"
    worker, runner = await _worker(_service_deployment())

    result, status = await _submit(worker, _flow_run(job_variables={"env": {"TOKEN": marker}}))

    message = _refused(worker, runner, result, status)
    assert marker not in message
    assert "TOKEN" not in message


async def test_a_configuration_that_skipped_admission_is_not_started() -> None:
    """`run` itself refuses a configuration the admission step never approved."""
    worker, runner = await _worker(_service_deployment())
    configuration = worker.job_configuration()
    configuration._identity_generation = worker._identity_generation
    configuration.env = {"PREFECT__WORKER_ID": str(worker.backend_id)}

    with pytest.raises(ServiceFlowRunRefusedError):
        await worker.run(_flow_run(), configuration)

    assert runner.starts == []


# ---------------------------------------------------------------------------
# Against a real Prefect server.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def _prefect_server() -> Iterator[None]:
    """One real Prefect server for the module, because starting one is not cheap."""
    with prefect_test_harness():
        yield


async def _create_deployment(
    client: Any,  # noqa: ANN401 - Prefect's client is untyped at this seam.
    names: tuple[str, str],
    pool: str,
    *,
    entrypoint: str,
    pull_steps: list[dict[str, Any]],
) -> UUID:
    flow_name, deployment_name = names
    flow_id = await client.create_flow_from_name(flow_name)
    return cast(
        "UUID",
        await client.create_deployment(
            flow_id=flow_id,
            name=deployment_name,
            work_pool_name=pool,
            entrypoint=entrypoint,
            pull_steps=pull_steps,
        ),
    )


@pytest.mark.usefixtures("_prefect_server")
async def test_a_real_server_run_of_a_foreign_deployment_is_crashed_and_never_started() -> None:
    """Every admission fact read back from a real server, through the real polling path."""
    pool = f"admission-{uuid4().hex[:12]}"
    async with get_client() as client:
        await client.create_work_pool(
            work_pool=WorkPoolCreate(
                name=pool,
                type="process",
                base_job_template=ProcessWorker.get_default_base_job_template(),
            )
        )
        assert SERVICE_DEFINITION.entrypoint is not None
        service = await _create_deployment(
            client,
            (SERVICE_DEFINITION.flow_name, SERVICE_DEFINITION.deployment_name),
            pool,
            entrypoint=SERVICE_DEFINITION.entrypoint,
            pull_steps=[],
        )
        foreign = await _create_deployment(
            client,
            ("foreign-flow", "run"),
            pool,
            entrypoint="foreign_package.flows.run",
            pull_steps=[{"prefect.deployments.steps.run_shell_script": {"script": "true"}}],
        )
        admitted = await client.create_flow_run_from_deployment(service)
        overridden = await client.create_flow_run_from_deployment(
            service,
            job_variables={"command": OVERRIDDEN_COMMAND},
        )
        foreign_run = await client.create_flow_run_from_deployment(foreign)

    worker = ServiceProcessWorker(work_pool_name=pool, name=service_worker_name(), create_pool_if_not_found=False)
    runner = _Runner()
    async with worker:
        await worker.sync_with_backend()
        assert worker.backend_id is not None, "the real server did not issue an identity"
        worker._start_child = cast("Any", runner.start)  # type: ignore[method-assign]
        await worker.get_and_submit_flow_runs()

    started = {start["flow_run_id"] for start in runner.starts}
    assert started == {admitted.id}, "only the service deployment, as applied, may reach a child start"
    async with get_client() as client:
        for refused in (overridden, foreign_run):
            state = (await client.read_flow_run(refused.id)).state
            assert state is not None
            assert state.type == StateType.CRASHED, f"a refused run was left {state.type}"
            assert (state.message or "").startswith(REFUSAL_PREFIX)
        admitted_state = (await client.read_flow_run(admitted.id)).state
        assert admitted_state is not None
        assert admitted_state.type != StateType.CRASHED


async def test_an_admitted_run_starts_the_admitted_command_and_never_reads_the_deployment_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real `ServiceProcessWorker` start step on Prefect 3.8.6.

    Only the process start and its executor context are replaced. The deployment
    record is server data anyone who can write to the task manager can change after
    admission, so neither the child nor the parent's crash and cancellation hooks
    may read it again: the child is pinned to the installed entrypoint, and the
    parent's hook resolver returns the installed flow.
    """
    starts: list[dict[str, Any]] = []
    resolvers: list[Any] = []

    class Starter:
        def __init__(self, **kwargs: Any) -> None:  # noqa: ANN401 - Prefect's starter keywords.
            starts.append(kwargs)

    class Executor:
        async def submit(self, *, task_status: Any) -> SimpleNamespace:  # noqa: ANN401, PLR6301
            handle = SimpleNamespace(pid=42)
            task_status.started(handle)
            return SimpleNamespace(status_code=0, handle=handle)

    class Context:
        control_channel = None

        async def __aenter__(self) -> Self:
            return self

        async def __aexit__(self, *_exc: object) -> None:
            return None

        def create_executor(self, _flow_run: object, starter: object, **kwargs: Any) -> Executor:  # noqa: ANN401, PLR6301
            assert isinstance(starter, Starter), "the child went through Prefect's workspace supervisor"
            resolvers.append(kwargs["resolve_flow"])
            return Executor()

    def read_again(*_args: object, **_kwargs: object) -> None:
        pytest.fail("the deployment was read again after admission")

    monkeypatch.setattr(worker_module, "EngineCommandStarter", Starter)
    monkeypatch.setattr(worker_module, "FlowRunExecutorContext", Context)
    monkeypatch.setattr("prefect.flows.load_flow_from_flow_run", read_again)
    worker, _runner = await _worker(_service_deployment())
    del worker._start_child  # the real start step, not the test stand-in

    result, status = await _submit(worker, _flow_run())

    assert not isinstance(result, Exception), result
    assert status.values == [42]
    assert len(starts) == 1
    assert starts[0]["command"] == worker_module._SERVICE_CHILD_COMMAND
    assert starts[0]["env"]["PREFECT__WORKER_ID"] == str(worker.backend_id)
    assert starts[0]["env"]["PREFECT__FLOW_ENTRYPOINT"] == SERVICE_DEFINITION.entrypoint
    assert not worker._identity_lock.locked(), "the identity lease was not released after the start"
    resolved = await resolvers[0](_flow_run())
    assert resolved.name == SERVICE_DEFINITION.flow_name


async def test_a_configuration_whose_command_is_not_configured_is_refused() -> None:
    """Prefect would run it through its workspace supervisor, so admission refuses it."""
    worker, _runner = await _worker(_service_deployment())
    flow_run = _flow_run()
    deployment = cast("Any", worker._client).deployment
    flow = cast("Any", SimpleNamespace(id=SERVICE_FLOW_ID, name=SERVICE_DEFINITION.flow_name, labels={}))
    configuration = worker_module.ServiceProcessJobConfiguration(command=None, env={})
    configuration.prepare_for_flow_run(flow_run, deployment, flow, worker._work_pool)
    assert configuration._command_configured is True
    configuration._command_configured = False

    reason = worker_module._admission_refusal(configuration, flow_run, deployment, flow, worker._work_pool)

    assert reason == "child command is not configured explicitly"
