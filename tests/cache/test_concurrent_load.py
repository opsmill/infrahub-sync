"""Potenda.load_both_sides runs source and destination concurrently."""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from infrahub_sync.potenda import Potenda


def _adapter_with_slow_load(label: str, sleep_seconds: float, *, fail: bool = False) -> MagicMock:
    """Build a fake adapter whose `load()` blocks for `sleep_seconds`
    and records its start/finish wall-clock times."""
    adapter = MagicMock()
    adapter.top_level = []
    adapter.label = label
    adapter.events: list[tuple[str, float]] = []

    def fake_load() -> None:
        adapter.events.append(("start", time.monotonic()))
        time.sleep(sleep_seconds)
        adapter.events.append(("end", time.monotonic()))
        if fail:
            msg = f"{label} load failed"
            raise ValueError(msg)

    adapter.load.side_effect = fake_load
    return adapter


def test_load_both_sides_runs_concurrently(tmp_path: Path) -> None:
    src = _adapter_with_slow_load("src", 0.5)
    dst = _adapter_with_slow_load("dst", 0.5)
    ptd = Potenda(
        source=src,
        destination=dst,
        config=None,  # ty: ignore[invalid-argument-type]
        top_level=[],
        run_dir=tmp_path,
    )
    start = time.monotonic()
    ptd.load_both_sides()
    elapsed = time.monotonic() - start
    # Sequential would be ~1.0s; concurrent should be ~0.5s. Allow generous slack.
    assert elapsed < 0.8, f"loads ran sequentially (elapsed={elapsed:.2f}s)"

    # Intervals must intersect (sequential execution would pass `src_start < dst_end`).
    src_start = next(t for label, t in src.events if label == "start")
    src_end = next(t for label, t in src.events if label == "end")
    dst_start = next(t for label, t in dst.events if label == "start")
    dst_end = next(t for label, t in dst.events if label == "end")
    assert max(src_start, dst_start) < min(src_end, dst_end)


def test_load_both_sides_sequential_when_disabled(tmp_path: Path) -> None:
    src = _adapter_with_slow_load("src", 0.25)
    dst = _adapter_with_slow_load("dst", 0.25)
    ptd = Potenda(
        source=src,
        destination=dst,
        config=None,  # ty: ignore[invalid-argument-type]
        top_level=[],
        run_dir=tmp_path,
        concurrent_load=False,
    )
    start = time.monotonic()
    ptd.load_both_sides()
    elapsed = time.monotonic() - start
    # Sequential should be >= sum of both sleeps. Allow tiny scheduling slack.
    assert elapsed >= 0.45, f"loads ran concurrently despite opt-out (elapsed={elapsed:.2f}s)"


def test_load_both_sides_surfaces_source_failure(tmp_path: Path) -> None:
    src = _adapter_with_slow_load("src", 0.05, fail=True)
    dst = _adapter_with_slow_load("dst", 0.05)
    ptd = Potenda(
        source=src,
        destination=dst,
        config=None,  # ty: ignore[invalid-argument-type]
        top_level=[],
        run_dir=tmp_path,
    )
    with pytest.raises(ValueError, match="src load failed"):
        ptd.load_both_sides()


def test_load_both_sides_surfaces_destination_failure(tmp_path: Path) -> None:
    src = _adapter_with_slow_load("src", 0.05)
    dst = _adapter_with_slow_load("dst", 0.05, fail=True)
    ptd = Potenda(
        source=src,
        destination=dst,
        config=None,  # ty: ignore[invalid-argument-type]
        top_level=[],
        run_dir=tmp_path,
    )
    with pytest.raises(ValueError, match="dst load failed"):
        ptd.load_both_sides()
