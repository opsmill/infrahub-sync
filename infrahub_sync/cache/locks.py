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

logger = logging.getLogger(__name__)


@contextmanager
def pipeline_lock(sync_name: str, *, timeout: float = 60.0) -> Iterator[None]:
    """Acquire an exclusive lock for `sync_name`. Raises filelock.Timeout if
    the lock cannot be taken within `timeout` seconds."""
    root = cache_root_for(sync_name)
    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / ".lock"
    lock = FileLock(str(lock_path), timeout=timeout)
    logger.debug("Acquiring pipeline lock %s", lock_path)
    with lock:
        try:
            yield
        finally:
            logger.debug("Released pipeline lock %s", lock_path)
