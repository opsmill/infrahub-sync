"""The worker's alive file and the Compose healthcheck that reads it (spec 029, research R-7).

The worker writes `<epoch seconds> <interval seconds>` to `$TMPDIR/infrahub-sync-worker-alive`
on its own asyncio timer. The timer depends on no other service, so the file shows only that the
worker's process and event loop run: a paused or stopped process, or an event loop blocked by
synchronous code, stops the writes. An outage of PostgreSQL or Prefect does not.

The Compose healthcheck runs `WORKER_HEALTHCHECK_COMMAND` with `python -I -c`. It imports only
the standard library, so it does not import this package every ten seconds.
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping

ALIVE_FILE_NAME = "infrahub-sync-worker-alive"

logger = structlog.get_logger(__name__)

# One statement per line, standard library only, and no `$`: Compose would interpolate it.
# Exit 0 when the file was written within max(3 * interval, 30) seconds, the interval being
# the one written in the file; otherwise print one line naming the reason, with no path, and
# exit 1 (`sys.exit` with a string writes it to standard error).
WORKER_HEALTHCHECK_COMMAND = """\
import os, sys, time
path = os.path.join(os.environ.get("TMPDIR") or "/tmp", "infrahub-sync-worker-alive")
try:
    fields = open(path, encoding="ascii").read().split()
except (OSError, ValueError):
    sys.exit("worker alive file is missing or unreadable")
if len(fields) != 2 or not all(field.isdigit() for field in fields):
    sys.exit("worker alive file is malformed")
if time.time() - int(fields[0]) > max(3 * int(fields[1]), 30):
    sys.exit("worker alive file is older than its limit")"""


def alive_file_path(environ: Mapping[str, str]) -> Path:
    """Return the alive file's path in `TMPDIR`, or in `/tmp` when it is unset or empty.

    The same rule as `WORKER_HEALTHCHECK_COMMAND`, so the writer and the reader find one file.
    """
    directory = environ.get("TMPDIR") or "/tmp"  # noqa: S108 - the healthcheck command reads the same path.
    return Path(directory) / ALIVE_FILE_NAME


def _write_alive_file(path: Path, text: str) -> None:
    """Write `text` to `path` through a temporary file, so a reader never sees a partial line."""
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    temporary.write_text(text, encoding="ascii")
    temporary.replace(path)


async def run_alive_writer(
    interval_seconds: int,
    path: Path,
    *,
    sleep: Callable[[float], Awaitable[object]] = asyncio.sleep,
    clock: Callable[[], float] = time.time,
) -> None:
    """Write `<epoch seconds> <interval seconds>` to `path` every interval, until cancelled.

    A write failure logs one warning, the first time only, and the writer keeps running, so
    the worker never stops because of this file. The warning names neither the path nor the
    error text.
    """
    warned = False
    while True:
        try:
            _write_alive_file(path, f"{int(clock())} {interval_seconds}")
        except OSError:
            if not warned:
                logger.warning("worker_alive_file_write_failed", interval_seconds=interval_seconds)
                warned = True
        await sleep(interval_seconds)
