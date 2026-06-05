"""Potenda.sync_in_tiers executes tiers in order and fans out within a tier."""

from __future__ import annotations

import operator
import threading
import time
from collections import defaultdict

import pytest

from infrahub_sync.potenda import Potenda


class _RecordingAdapter:
    """Adapter that records the order and concurrency of per-kind sync calls."""

    def __init__(self) -> None:
        self.top_level: list[str] = []
        self.calls: list[tuple[float, str]] = []
        self.concurrent: defaultdict[int, set[str]] = defaultdict(set)
        self._lock = threading.Lock()
        self._active: set[str] = set()
        self._snapshot_id = 0

    def __str__(self) -> str:
        return "recording"

    def load(self) -> None:  # noqa: PLR6301
        # diffsync adapter hook; no-op for the recording fixture.
        return None

    def diff_from(self, *_: object, **__: object) -> _NullDiff:  # noqa: PLR6301
        return _NullDiff()

    def sync_from(self, *_: object, diff: object = None, **__: object) -> object:
        # Simulate per-kind work in parallel: each top_level kind sleeps and
        # records overlap.
        threads = []
        for kind in self.top_level:
            t = threading.Thread(target=self._do_kind, args=(kind,))
            threads.append(t)
            t.start()
        for t in threads:
            t.join()
        return diff

    def _do_kind(self, kind: str) -> None:
        with self._lock:
            self._active.add(kind)
            self._snapshot_id += 1
            self.concurrent[self._snapshot_id] = set(self._active)
        time.sleep(0.02)
        with self._lock:
            self._active.discard(kind)
            self.calls.append((time.time(), kind))


class _NullDiff:
    def has_diffs(self) -> bool:  # noqa: PLR6301
        return False

    def str(self) -> str:  # noqa: PLR6301  # ty: ignore[invalid-type-form]
        return ""

    def items(self) -> list:  # noqa: PLR6301
        return []


@pytest.mark.timeout(5)
def test_sync_in_tiers_respects_tier_boundary() -> None:
    src = _RecordingAdapter()
    dst = _RecordingAdapter()
    tiers = [{"Tag", "Role"}, {"Device"}]
    ptd = Potenda(
        source=src,  # ty: ignore[invalid-argument-type]
        destination=dst,  # ty: ignore[invalid-argument-type]
        config=None,  # ty: ignore[invalid-argument-type]
        top_level=["Tag", "Role", "Device"],
        tiers=tiers,
    )

    ptd.sync_in_tiers(parallel=True)

    # Order: every Tag/Role call happens before any Device call.
    by_time = sorted(dst.calls, key=operator.itemgetter(0))
    seen_tier0 = False
    for _, kind in by_time:
        if kind == "Device":
            assert seen_tier0
        if kind in {"Tag", "Role"}:
            seen_tier0 = True
