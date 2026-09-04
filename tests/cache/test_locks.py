"""Cross-process pipeline filelock."""

from __future__ import annotations

import multiprocessing
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from infrahub_sync.cache.locks import pipeline_lock

if TYPE_CHECKING:
    from multiprocessing.synchronize import Event as EventT


def _hold_lock(sync_name: str, cache_dir: str, acquired: EventT, release: EventT) -> None:
    """Subprocess target: take the lock, signal acquisition, then hold until told to release."""
    import os

    os.environ["INFRAHUB_SYNC_CACHE_DIR"] = cache_dir
    with pipeline_lock(sync_name):
        acquired.set()
        # Bounded hold so the child never lingers if the parent dies mid-test.
        release.wait(timeout=10.0)


def test_pipeline_lock_excludes_concurrent_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cache_dir = str(tmp_path)
    ctx = multiprocessing.get_context("spawn")
    acquired = ctx.Event()
    release = ctx.Event()
    p = ctx.Process(target=_hold_lock, args=("p1", cache_dir, acquired, release))
    p.start()
    try:
        # Wait for the child to actually hold the lock instead of guessing with a sleep.
        assert acquired.wait(timeout=10.0), "child process never acquired the lock"
        # Through monkeypatch, so the setting is restored when this test ends. Written
        # into `os.environ` directly it outlived the test and reached every later one --
        # including any that assert the service reads no cache setting.
        monkeypatch.setenv("INFRAHUB_SYNC_CACHE_DIR", cache_dir)
        from filelock import Timeout

        with pytest.raises(Timeout), pipeline_lock("p1", timeout=0.05):
            pass
    finally:
        release.set()
        p.join(timeout=10.0)
        if p.is_alive():
            p.terminate()
            p.join()


def test_pipeline_lock_allows_different_pipelines(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INFRAHUB_SYNC_CACHE_DIR", str(tmp_path))
    with pipeline_lock("p1"), pipeline_lock("p2"):
        pass


def test_the_lock_tests_leave_no_cache_setting_behind() -> None:
    """The direct-CLI cache setting must not leak out of this module.

    Both tests above configure a cache root. Left in `os.environ`, that value would reach
    every later test in the session, so a case asserting the service reads no cache
    setting would pass or fail depending on collection order rather than on behaviour.
    """
    import os

    assert "INFRAHUB_SYNC_CACHE_DIR" not in os.environ
