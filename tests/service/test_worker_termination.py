"""The worker stops on SIGTERM, which Kubernetes and Docker send to stop a container."""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess  # noqa: S404 -- a separate process is the point: it receives a real SIGTERM
import sys
import textwrap
import time

import pytest

from infrahub_sync.service import worker

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="SIGTERM handlers are POSIX only")


def test_sigterm_stops_the_run_and_returns() -> None:
    stopped: list[str] = []

    async def start() -> None:
        asyncio.get_running_loop().call_later(0.05, os.kill, os.getpid(), signal.SIGTERM)
        try:
            await asyncio.sleep(30)
        finally:
            stopped.append("exit steps ran")

    async def scenario() -> None:
        await asyncio.wait_for(worker.run_until_terminated(start), timeout=5)

    asyncio.run(scenario())

    assert stopped == ["exit steps ran"]


def test_the_sigterm_handler_is_removed_afterwards() -> None:
    async def start() -> None:
        await asyncio.sleep(0)

    async def scenario() -> bool:
        await worker.run_until_terminated(start)
        return asyncio.get_running_loop().remove_signal_handler(signal.SIGTERM)

    assert asyncio.run(scenario()) is False


def test_a_handled_sigterm_leaves_no_cancellation_pending() -> None:
    """A later `TaskGroup` or `timeout` in the same task must not count the handled SIGTERM."""

    async def start() -> None:
        os.kill(os.getpid(), signal.SIGTERM)
        await asyncio.sleep(30)

    async def scenario() -> int:
        await worker.run_until_terminated(start)
        task = asyncio.current_task()
        assert task is not None
        # `Task.cancelling` is Python 3.11+, as the service itself is.
        return task.cancelling() if sys.version_info >= (3, 11) else 0

    assert asyncio.run(scenario()) == 0


def test_a_cancel_without_sigterm_still_propagates() -> None:
    async def start() -> None:
        await asyncio.sleep(30)

    async def scenario() -> None:
        task = asyncio.ensure_future(worker.run_until_terminated(start))
        await asyncio.sleep(0.05)
        task.cancel()
        await task

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(scenario())


def test_main_runs_the_worker_inside_the_sigterm_handler(monkeypatch: pytest.MonkeyPatch) -> None:
    started: list[str] = []

    class _Worker:
        def __init__(self, **_settings: object) -> None:
            pass

        @staticmethod
        async def start() -> None:
            started.append("started")
            os.kill(os.getpid(), signal.SIGTERM)
            await asyncio.sleep(30)

    monkeypatch.setattr(worker, "install_custom_ca_bundle", lambda: None)
    monkeypatch.setattr(worker, "ServiceProcessWorker", _Worker)

    assert worker.main(["--pool", "team-pool"], start_check=lambda _pool: None) == 0
    assert started == ["started"]


def test_a_process_stops_within_a_second_of_sigterm() -> None:
    """A separate process, as in a container: SIGTERM ends it with exit code 0."""
    script = textwrap.dedent(
        """
        import asyncio, sys
        from infrahub_sync.service.worker import run_until_terminated

        async def start():
            sys.stdout.write("ready\\n")
            sys.stdout.flush()
            await asyncio.sleep(60)

        asyncio.run(run_until_terminated(start))
        """
    )
    process = subprocess.Popen(  # noqa: S603 -- fixed interpreter and script
        [sys.executable, "-c", script], stdout=subprocess.PIPE, text=True
    )
    try:
        assert process.stdout is not None
        assert process.stdout.readline().strip() == "ready"
        sent = time.monotonic()
        process.send_signal(signal.SIGTERM)
        code = process.wait(timeout=10)
        elapsed = time.monotonic() - sent
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()

    assert code == 0
    assert elapsed < 1
