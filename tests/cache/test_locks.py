"""Cross-process pipeline filelock."""

from __future__ import annotations

import multiprocessing
import time
from pathlib import Path

import pytest

from infrahub_sync.cache.locks import pipeline_lock


def _hold_lock(sync_name: str, cache_dir: str, hold_seconds: float) -> None:
    """Subprocess target: take the lock and sleep."""
    import os

    os.environ["INFRAHUB_SYNC_CACHE_DIR"] = cache_dir
    with pipeline_lock(sync_name):
        time.sleep(hold_seconds)


def test_pipeline_lock_excludes_concurrent_run(tmp_path: Path) -> None:
    cache_dir = str(tmp_path)
    ctx = multiprocessing.get_context("spawn")
    p = ctx.Process(target=_hold_lock, args=("p1", cache_dir, 3.0))
    p.start()
    time.sleep(1.0)
    import os

    os.environ["INFRAHUB_SYNC_CACHE_DIR"] = cache_dir
    from filelock import Timeout

    with pytest.raises(Timeout), pipeline_lock("p1", timeout=0.05):
        pass
    p.join()


def test_pipeline_lock_allows_different_pipelines(tmp_path: Path) -> None:
    import os

    os.environ["INFRAHUB_SYNC_CACHE_DIR"] = str(tmp_path)
    with pipeline_lock("p1"), pipeline_lock("p2"):
        pass
