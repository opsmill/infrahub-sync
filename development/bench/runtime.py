"""Quiet process execution and one-second memory sampling for manual benchmark cells."""

from __future__ import annotations

import contextlib
import os
import platform
import signal
import subprocess  # noqa: S404 -- benchmark commands use fixed argv
import tempfile
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from invoke import Context
from invoke.exceptions import CommandTimedOut, UnexpectedExit
from typing_extensions import Self

if TYPE_CHECKING:
    from collections.abc import Callable

LIMIT_SECONDS = 6 * 60 * 60


class BenchmarkError(RuntimeError):
    """A safe failure message without provider output or credentials."""


class QuietContext(Context):
    """Suppress legacy task banners and command output at the benchmark boundary."""

    deadline: float | None = None

    def run(self, command: str, **kwargs: object):  # noqa: ANN201 -- Invoke has a dynamic Result/Promise API
        kwargs.update(hide=True, pty=False, echo=False)
        requested = kwargs.get("timeout", LIMIT_SECONDS)
        timeout = min(requested, LIMIT_SECONDS) if isinstance(requested, (int, float)) else LIMIT_SECONDS
        if self.deadline is not None:
            timeout = min(timeout, self.deadline - time.monotonic())
            if timeout <= 0:
                msg = "cell exceeded the six-hour limit"
                raise TimeoutError(msg)
        kwargs["timeout"] = timeout
        try:
            return super().run(command, **kwargs)
        except CommandTimedOut:
            if self.deadline is not None and time.monotonic() >= self.deadline:
                msg = "cell exceeded the six-hour limit"
                raise TimeoutError(msg) from None
            msg = "benchmark setup command exceeded its command time limit; provider output suppressed"
            raise BenchmarkError(msg) from None
        except UnexpectedExit as exc:
            msg = f"benchmark setup command failed (exit {exc.result.exited}); provider output suppressed"
            raise BenchmarkError(msg) from None


def output(argv: list[str], *, cwd: Path, env: dict[str, str] | None = None, timeout: float = 300) -> str:
    """Capture command output privately; never expose it through an exception."""
    try:
        result = subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout, check=False)  # noqa: S603
    except subprocess.TimeoutExpired:
        msg = "benchmark setup command exceeded its command time limit; provider output suppressed"
        raise BenchmarkError(msg) from None
    if result.returncode:
        msg = f"benchmark command {Path(argv[0]).name} failed (exit {result.returncode}); provider output suppressed"
        raise BenchmarkError(msg)
    return result.stdout.strip()


def machine_info() -> dict[str, str | int]:
    """Record CPU model/count, physical memory, and OS without the machine's network name."""
    cpu = platform.processor()
    cpuinfo = Path("/proc/cpuinfo")
    if cpuinfo.exists():
        cpu = next(
            (
                line.partition(":")[2].strip()
                for line in cpuinfo.read_text(encoding="utf-8").splitlines()
                if line.startswith("model name")
            ),
            cpu,
        )
    return {
        "cpu": cpu,
        "cpu_count": os.cpu_count() or 0,
        "memory_bytes": os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES"),
        "os": f"{platform.system()} {platform.release()} {platform.machine()}",
    }


def process_rss(pid: int) -> float:
    """Read the Linux sync process's resident memory in MiB."""
    try:
        fields = Path(f"/proc/{pid}/statm").read_text(encoding="utf-8").split()
    except FileNotFoundError:
        return 0.0
    return int(fields[1]) * os.sysconf("SC_PAGE_SIZE") / 1024**2


def docker_memory_mib(statistics: dict[str, Any]) -> float:
    """Match Docker stats working memory for cgroup v1/v2, converting bytes to MiB."""
    memory = statistics["memory_stats"]
    usage = int(memory["usage"])
    cached = memory.get("stats", {})
    for name in ("total_inactive_file", "inactive_file"):
        cache = int(cached.get(name, 0))
        if cache and usage > cache:
            return (usage - cache) / 1024**2
    return usage / 1024**2


class MemorySampler:
    """Sample memory immediately and every second; propagate missing measurement evidence."""

    def __init__(self, read: Callable[[], float]) -> None:
        self.read = read
        self.peak = 0.0
        self.error = False
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._sample, daemon=True)

    def _sample(self) -> None:
        while not self.stop.is_set():
            started = time.monotonic()
            try:
                self.peak = max(self.peak, self.read())
            except (OSError, ValueError, BenchmarkError, TimeoutError):
                self.error = True
                return
            self.stop.wait(max(0, 1 - (time.monotonic() - started)))

    def __enter__(self) -> Self:
        self.thread.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self.stop.set()
        self.thread.join(timeout=15)

    def result(self) -> float:
        """Refuse a missing or failed sample rather than report zero memory."""
        if self.error or self.peak <= 0:
            msg = "peak memory sampling failed"
            raise BenchmarkError(msg)
        return self.peak


def measured_process(argv: list[str], cwd: Path, env: dict[str, str], timeout: float) -> tuple[str, float, float]:
    """Measure a v2 process, killing and reaping its session on every abnormal exit."""
    started = time.monotonic()
    with tempfile.TemporaryFile(mode="w+b") as captured:
        process = subprocess.Popen(  # noqa: S603 -- validated argv, never a shell
            argv, cwd=cwd, env=env, stdout=captured, stderr=subprocess.STDOUT, start_new_session=True
        )
        completed = False
        try:
            with MemorySampler(lambda: process_rss(process.pid)) as sampler:
                try:
                    code = process.wait(timeout=timeout)
                    wall = time.monotonic() - started
                except subprocess.TimeoutExpired:
                    msg = "sync exceeded the six-hour cell limit"
                    raise TimeoutError(msg) from None
            if code:
                msg = "v2 sync failed; the unchanged current mapping may be incompatible with this release"
                raise BenchmarkError(msg)
            captured.seek(0)
            result = captured.read().decode("utf-8", errors="replace"), wall, sampler.result()
            completed = True
            return result
        finally:
            if not completed:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
                process.wait()
