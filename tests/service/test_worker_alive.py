"""The worker's alive file and the Compose healthcheck that reads it (spec 029, research R-7)."""

from __future__ import annotations

import asyncio
import math
import os
import subprocess  # noqa: S404 -- the healthcheck runs as its own process, as Docker runs it
import sys
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from types import SimpleNamespace

import pytest
from structlog.testing import capture_logs

from infrahub_sync.service import worker
from infrahub_sync.service.worker_alive import (
    ALIVE_FILE_NAME,
    WORKER_HEALTHCHECK_COMMAND,
    alive_file_path,
    run_alive_writer,
)


class _StopWriterError(Exception):
    """Raised by the fake sleep to end the writer loop after a number of rounds."""


def _fake_sleep(rounds: int, delays: list[float]) -> Callable[[float], Awaitable[None]]:
    async def sleep(delay: float) -> None:  # noqa: RUF029 -- the writer awaits its sleep
        delays.append(delay)
        if len(delays) >= rounds:
            raise _StopWriterError

    return sleep


def test_alive_file_path_is_built_from_tmpdir(tmp_path: Path) -> None:
    assert alive_file_path({"TMPDIR": str(tmp_path)}) == tmp_path / ALIVE_FILE_NAME


def test_alive_file_path_falls_back_to_the_system_temporary_directory() -> None:
    import tempfile

    assert alive_file_path({}) == Path(tempfile.gettempdir()) / ALIVE_FILE_NAME


def test_the_writer_writes_the_time_and_interval_every_interval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / ALIVE_FILE_NAME
    delays: list[float] = []
    contents: list[str] = []
    times = iter([1000.7, 1010.2, 1020.9])
    replaced: list[tuple[str, str]] = []
    real_replace = os.replace

    def recording_replace(source: str | os.PathLike[str], target: str | os.PathLike[str]) -> None:
        replaced.append((os.fspath(source), os.fspath(target)))
        real_replace(source, target)
        contents.append(path.read_text(encoding="ascii"))

    monkeypatch.setattr(os, "replace", recording_replace)

    with pytest.raises(_StopWriterError):
        asyncio.run(run_alive_writer(10, path, sleep=_fake_sleep(3, delays), clock=lambda: next(times)))

    assert contents == ["1000 10", "1010 10", "1020 10"]
    assert delays == [10, 10, 10]
    assert all(target == os.fspath(path) and source != target for source, target in replaced)
    assert sorted(item.name for item in tmp_path.iterdir()) == [ALIVE_FILE_NAME]


def test_a_write_failure_warns_once_and_the_writer_keeps_running(tmp_path: Path) -> None:
    path = tmp_path / "missing-directory" / ALIVE_FILE_NAME
    delays: list[float] = []

    with capture_logs() as logs, pytest.raises(_StopWriterError):
        asyncio.run(run_alive_writer(10, path, sleep=_fake_sleep(4, delays), clock=time.time))

    assert delays == [10, 10, 10, 10]
    warnings = [entry for entry in logs if entry["log_level"] == "warning"]
    assert len(warnings) == 1
    assert str(tmp_path) not in str(warnings[0])


def _run_healthcheck(tmp_path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 -- fixed interpreter and the module's own command
        [sys.executable, "-I", "-c", WORKER_HEALTHCHECK_COMMAND],
        env={"TMPDIR": str(tmp_path)},
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )


def _write_alive(tmp_path: Path, text: str) -> None:
    (tmp_path / ALIVE_FILE_NAME).write_text(text, encoding="ascii")


@pytest.mark.parametrize(
    ("age", "interval"),
    [(0, 10), (29, 10), (59, 20)],
    ids=["now", "29s-old-interval-10", "59s-old-interval-20"],
)
def test_the_healthcheck_passes_for_a_recent_file(tmp_path: Path, age: int, interval: int) -> None:
    # Rounded up, so the age the check computes is at most `age` plus its own start-up time.
    _write_alive(tmp_path, f"{math.ceil(time.time()) - age} {interval}")

    result = _run_healthcheck(tmp_path)

    assert result.returncode == 0, result.stderr
    assert not result.stdout
    assert not result.stderr


def test_the_healthcheck_fails_for_a_file_older_than_the_limit(tmp_path: Path) -> None:
    _write_alive(tmp_path, f"{int(time.time()) - 31} 10")

    result = _run_healthcheck(tmp_path)

    assert result.returncode == 1
    lines = (result.stdout + result.stderr).splitlines()
    assert len(lines) == 1
    assert "/" not in lines[0]


@pytest.mark.parametrize("content", [None, "", "abc 10", "123"], ids=["missing", "empty", "not-a-number", "one-field"])
def test_the_healthcheck_fails_for_a_missing_or_malformed_file(tmp_path: Path, content: str | None) -> None:
    if content is not None:
        _write_alive(tmp_path, content)

    result = _run_healthcheck(tmp_path)

    assert result.returncode == 1
    lines = (result.stdout + result.stderr).splitlines()
    assert len(lines) == 1
    assert "/" not in lines[0]


def test_the_worker_writes_its_alive_file_while_it_runs_and_stops_with_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    path = tmp_path / ALIVE_FILE_NAME
    seen: list[bool] = []
    writers: list[asyncio.Task[object]] = []

    async def start() -> None:
        await asyncio.sleep(0.05)
        seen.append(path.exists())
        writers.extend(task for task in asyncio.all_tasks() if task is not asyncio.current_task())

    asyncio.run(worker.run_with_alive_file(start, 10))

    assert seen == [True]
    assert path.read_text(encoding="ascii").endswith(" 10")
    assert writers
    assert all(task.done() for task in writers)


@pytest.mark.parametrize(("value", "expected"), [(10, 10), (2.5, 3), (30, 30)])
def test_the_writer_interval_is_the_worker_heartbeat_in_whole_seconds(
    monkeypatch: pytest.MonkeyPatch, value: float, expected: int
) -> None:
    settings = SimpleNamespace(worker=SimpleNamespace(heartbeat_seconds=value))
    monkeypatch.setattr(worker, "get_current_settings", lambda: settings)

    assert worker._heartbeat_interval_seconds() == expected
