"""Record what Prefect's process worker would launch, without launching it.

`ProcessWorker.run` builds a starter for the child process and hands it to an
executor from `FlowRunExecutorContext`. Replacing that context keeps everything the
worker decides -- which starter, which command, which child environment -- and drops
only what needs a Prefect server and a real process: state proposals and the spawn.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import anyio
from prefect.workers import process as prefect_process
from typing_extensions import Self

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    import pytest
    from anyio.abc import TaskStatus

CHILD_PID = 42


class LaunchRecorder:
    """The starters the worker handed to Prefect's executor, in submission order."""

    def __init__(self) -> None:
        self.starters: list[Any] = []
        self.flow_runs: list[Any] = []
        # Runs after the child reports started, standing in for what the child does.
        self.child: Callable[[Any], Awaitable[None]] | None = None

    @property
    def child_environments(self) -> list[dict[str, str | None]]:
        """The environment each launched child would have been given."""
        return [dict(starter._env) for starter in self.starters]

    def install(self, monkeypatch: pytest.MonkeyPatch) -> LaunchRecorder:
        """Route `ProcessWorker.run` through this recorder for the rest of the test."""
        monkeypatch.setattr(prefect_process, "FlowRunExecutorContext", _ContextFactory(self))
        return self

    @staticmethod
    def installed() -> LaunchRecorder:
        """The recorder the current test installed."""
        factory = prefect_process.FlowRunExecutorContext
        assert isinstance(factory, _ContextFactory), "no LaunchRecorder is installed for this test"
        return factory.recorder


class _ContextFactory:
    def __init__(self, recorder: LaunchRecorder) -> None:
        self.recorder = recorder

    def __call__(self) -> _ExecutorContext:
        return _ExecutorContext(self.recorder)


class _ExecutorContext:
    control_channel = None

    def __init__(self, recorder: LaunchRecorder) -> None:
        self._recorder = recorder
        self._after_exit: list[Callable[[], object]] = []

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_exc: object) -> bool:
        for callback in self._after_exit:
            callback()
        return False

    def call_after_exit(self, callback: Callable[[], object]) -> None:
        self._after_exit.append(callback)

    def create_executor(self, flow_run: object, starter: object, **_options: object) -> _Executor:
        return _Executor(self._recorder, flow_run, starter)


class _Executor:
    def __init__(self, recorder: LaunchRecorder, flow_run: object, starter: object) -> None:
        self._recorder = recorder
        self._flow_run = flow_run
        self._starter = starter

    async def submit(self, task_status: TaskStatus[Any] = anyio.TASK_STATUS_IGNORED) -> SimpleNamespace:
        self._recorder.starters.append(self._starter)
        self._recorder.flow_runs.append(self._flow_run)
        handle = SimpleNamespace(pid=CHILD_PID)
        task_status.started(handle)
        if self._recorder.child is not None:
            await self._recorder.child(self._starter)
        return SimpleNamespace(handle=handle, status_code=0)
