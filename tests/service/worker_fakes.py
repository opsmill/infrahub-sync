"""Stand-ins for the service worker's child start.

`ServiceProcessWorker._start_child` is the one step after admission and the
identity check. These helpers replace it per worker, so a test observes what would
start -- the flow run, the command, and the child environment -- without a process.
The stand-in bypasses Prefect's own start step; the real path through Prefect's
`ProcessWorker.run` is covered in `test_service_worker_admission.py`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, cast

from prefect.workers.process import ProcessWorkerResult

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from prefect.client.schemas.objects import FlowRun
    from prefect.workers.process import ProcessJobConfiguration

    from infrahub_sync.service.worker import ServiceProcessWorker


class ChildRunner(Protocol):
    """A fake child start: receives what the process would, reports a pid and status."""

    async def execute_flow_run(self, **kwargs: Any) -> Any: ...  # noqa: ANN401


def child_start(
    runner: ChildRunner,
) -> Callable[[FlowRun, ProcessJobConfiguration, Any], Awaitable[ProcessWorkerResult]]:
    """Adapt a fake runner to the worker's `_start_child` signature."""

    async def start(flow_run: FlowRun, configuration: ProcessJobConfiguration, task_status: Any) -> ProcessWorkerResult:  # noqa: ANN401
        process = await runner.execute_flow_run(
            flow_run_id=flow_run.id,
            command=configuration.command,
            env=configuration.env,
            task_status=task_status,
        )
        return ProcessWorkerResult(status_code=process.returncode, identifier=str(process.pid))

    return start


def stub_child_start_with(worker: ServiceProcessWorker, runner: ChildRunner) -> None:
    """Replace the worker's child start with the fake runner."""
    worker._start_child = cast("Any", child_start(runner))  # type: ignore[method-assign]
