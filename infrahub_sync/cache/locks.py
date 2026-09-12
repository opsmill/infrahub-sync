"""Per-pipeline filelock so only one infrahub-sync invocation can write into
the cache for a given sync name at a time."""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import TYPE_CHECKING

from filelock import FileLock

from infrahub_sync.cache.paths import cache_root_for

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

logger = logging.getLogger(__name__)


@contextmanager
def pipeline_lock(sync_name: str, *, timeout: float = 60.0, base_directory: Path | None = None) -> Iterator[None]:
    """Acquire an exclusive lock for `sync_name`. Raises filelock.Timeout if
    the lock cannot be taken within `timeout` seconds.

    `base_directory` names the root to lock inside. A caller that owns a private directory
    for one run passes it, and the lock then lives in that directory rather than in a
    location other processes derive -- which is what keeps a stage-private run private.

    Scope: this lock excludes two invocations that share a cache root. It is **not**
    cross-worker write authority, and it cannot be: taken inside a stage's own private
    directory there is nothing for a second worker to contend on. Cross-worker exclusion
    for a managed write belongs to the configuration guard in
    `infrahub_sync.service.apply_guard`, which serializes writers per registered
    configuration on a PostgreSQL advisory lock.
    """
    root = cache_root_for(sync_name, base_directory=base_directory)
    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / ".lock"
    lock = FileLock(str(lock_path), timeout=timeout)
    logger.debug("Acquiring pipeline lock %s", lock_path)
    with lock:
        try:
            yield
        finally:
            logger.debug("Released pipeline lock %s", lock_path)
