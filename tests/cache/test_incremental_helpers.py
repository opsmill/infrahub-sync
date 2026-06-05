from __future__ import annotations

import json
from pathlib import Path

from infrahub_sync.cache.incremental import previous_successful_run_dir, should_use_incremental


def _write_run(cache_root: Path, run_id: str, status: str) -> Path:
    run_dir = cache_root / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "run.json").write_text(json.dumps({"status": status}))
    return run_dir


def test_returns_most_recent_applied_run(tmp_path: Path) -> None:
    _write_run(tmp_path, "2026-05-17T10-00-00Z", "applied")
    latest = _write_run(tmp_path, "2026-05-18T11-00-00Z", "applied")
    _write_run(tmp_path, "2026-05-18T12-00-00Z", "failed")

    assert previous_successful_run_dir(tmp_path) == latest


def test_returns_none_when_no_successful_runs(tmp_path: Path) -> None:
    _write_run(tmp_path, "2026-05-18T11-00-00Z", "failed")

    assert previous_successful_run_dir(tmp_path) is None


def test_returns_none_on_empty_cache(tmp_path: Path) -> None:
    assert previous_successful_run_dir(tmp_path) is None


def test_skip_when_force_full(tmp_path: Path) -> None:
    prev_run = tmp_path / "prev"
    prev_run.mkdir()
    (prev_run / "schema-sub-hash.txt").write_text("abc123")

    decision = should_use_incremental(
        prev_run_dir=prev_run,
        current_subhash="abc123",
        force_full=True,
    )
    assert decision is False


def test_skip_when_no_prev_run() -> None:
    decision = should_use_incremental(
        prev_run_dir=None,
        current_subhash="abc123",
        force_full=False,
    )
    assert decision is False


def test_skip_when_subhash_mismatch(tmp_path: Path) -> None:
    prev_run = tmp_path / "prev"
    prev_run.mkdir()
    (prev_run / "schema-sub-hash.txt").write_text("OLD000")

    decision = should_use_incremental(
        prev_run_dir=prev_run,
        current_subhash="NEW111",
        force_full=False,
    )
    assert decision is False


def test_use_incremental_when_subhash_matches(tmp_path: Path) -> None:
    prev_run = tmp_path / "prev"
    prev_run.mkdir()
    (prev_run / "schema-sub-hash.txt").write_text("abc123")

    decision = should_use_incremental(
        prev_run_dir=prev_run,
        current_subhash="abc123",
        force_full=False,
    )
    assert decision is True


# ---------------------------------------------------------------------------
# Task 4: load_cursors / persist_cursors
# ---------------------------------------------------------------------------
from infrahub_sync.cache.cursors import CursorState, CursorTier  # noqa: E402
from infrahub_sync.cache.incremental import load_cursors, persist_cursors  # noqa: E402


def test_load_cursors_empty(tmp_path: Path) -> None:
    cursors = load_cursors(tmp_path / "cursors.json", side="A")
    assert cursors == {}


def test_persist_then_load_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "cursors.json"
    persist_cursors(
        path,
        side="A",
        cursors={
            "InfraDevice": CursorState(
                tier=CursorTier.TIMESTAMP,
                value="2026-05-18T11:00:00Z",
            )
        },
    )
    persist_cursors(
        path,
        side="B",
        cursors={
            "InfraDevice": CursorState(
                tier=CursorTier.INFRAHUB_DIFF,
                value="2026-05-18T11:05:00Z",
            )
        },
    )

    loaded_a = load_cursors(path, side="A")
    loaded_b = load_cursors(path, side="B")

    assert loaded_a["InfraDevice"].tier is CursorTier.TIMESTAMP
    assert loaded_a["InfraDevice"].value == "2026-05-18T11:00:00Z"
    assert loaded_b["InfraDevice"].tier is CursorTier.INFRAHUB_DIFF
    assert loaded_b["InfraDevice"].value == "2026-05-18T11:05:00Z"


# ---------------------------------------------------------------------------
# Task 5: hydrate_from_parquet
# ---------------------------------------------------------------------------
from datetime import datetime, timezone  # noqa: E402

from infrahub_sync.cache.incremental import hydrate_from_parquet  # noqa: E402
from infrahub_sync.cache.parquet_io import write_resource_side  # noqa: E402


def test_hydrate_replays_prior_rows(tmp_path: Path) -> None:
    run_dir = tmp_path
    extract_ts = datetime(2026, 5, 18, 11, tzinfo=timezone.utc)
    write_resource_side(
        run_dir=run_dir,
        side="A",
        resource="InfraDevice",
        rows=[{"name": "leaf1"}, {"name": "leaf2"}],
        source_ids=["leaf1", "leaf2"],
        extract_ts=extract_ts,
    )

    captured: list[dict] = []

    def add_row(model_name: str, payload: dict) -> None:
        captured.append({"model": model_name, **payload})

    rows_loaded, max_ts = hydrate_from_parquet(
        run_dir=run_dir,
        side="A",
        resource="InfraDevice",
        add_row=add_row,
    )

    assert rows_loaded == 2
    assert max_ts == extract_ts
    assert {c["name"] for c in captured} == {"leaf1", "leaf2"}


def test_hydrate_missing_resource_returns_zero(tmp_path: Path) -> None:
    rows_loaded, max_ts = hydrate_from_parquet(
        run_dir=tmp_path,
        side="A",
        resource="InfraDevice",
        add_row=lambda _model, _payload: None,
    )

    assert rows_loaded == 0
    assert max_ts is None


# ---------------------------------------------------------------------------
# Task 11: cadence / run-counter
# ---------------------------------------------------------------------------


def test_force_full_when_cadence_exceeded(tmp_path: Path) -> None:
    prev_run = tmp_path / "prev"
    prev_run.mkdir()
    (prev_run / "schema-sub-hash.txt").write_text("abc123")

    decision = should_use_incremental(
        prev_run_dir=prev_run,
        current_subhash="abc123",
        force_full=False,
        runs_since_full=10,
        cadence=10,
    )
    assert decision is False
