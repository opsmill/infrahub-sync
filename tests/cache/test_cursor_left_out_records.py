"""A kind whose records were left out at load keeps no cursor, so the next run reads them again (review #9).

A record the load could not build is in the side's load report and not in the store, so it is
not in the snapshot either. If the run advanced the incremental cursor of its kind, the next run
would read only the records changed since, and the left-out record would not be read again
until it changed. So the run persists no cursor for a kind that has an entry in the load report:
the next run reads that kind in full. The other kinds keep their cursor.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from infrahub_sync import LoadReportEntry
from infrahub_sync.cache.incremental import load_cursors
from tests.cache.test_safe_watermarks import _engine, _TimestampSource

if TYPE_CHECKING:
    from pathlib import Path

    from infrahub_sync.potenda import Potenda


def _loaded(tmp_path: Path, side: str) -> tuple[_TimestampSource, Potenda]:
    rows = {"Device": [{"name": "device", "description": "old"}], "Empty": []}
    source = _TimestampSource(rows, safe=True, exclusive=True, mutate=False)
    engine = _engine(tmp_path, "run-1", source, side)
    (engine.source_load if side == "A" else engine.destination_load)()
    return source, engine


@pytest.mark.parametrize("side", ["A", "B"])
def test_a_kind_with_left_out_records_keeps_no_cursor(tmp_path: Path, side: str) -> None:
    _source, engine = _loaded(tmp_path, side)
    entry = LoadReportEntry(kind="Device", source_id="7", reason="transform_failed", field="description")
    setattr(engine, "source_load_report" if side == "A" else "destination_load_report", [entry])

    engine.persist_cursors_for_run(side=side)

    assert engine.run_dir is not None
    saved = load_cursors(engine.run_dir / "cursors.json", side=side)
    assert set(saved) == {"Empty"}


@pytest.mark.parametrize("side", ["A", "B"])
def test_an_empty_load_report_keeps_every_cursor(tmp_path: Path, side: str) -> None:
    _source, engine = _loaded(tmp_path, side)

    engine.persist_cursors_for_run(side=side)

    assert engine.run_dir is not None
    saved = load_cursors(engine.run_dir / "cursors.json", side=side)
    assert set(saved) == {"Device", "Empty"}
