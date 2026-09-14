"""The warm-incremental path must carry the destination's `local_id` across runs.

An update operation is keyed by the destination object's Infrahub `id`, and derivation reads
that id off the destination model's `local_id`. A live destination load sets it, but the
warm path rebuilt destination models **without** it: the snapshot persisted identifiers and
attributes only, and the runtime model defaults `local_id` to `None`. A warm run would then
have found no id for an update it had every other reason to derive.

So side B's snapshot carries `local_id` as its own column. It is never conflated with the
engine-controlled `_source_id`: that column is DiffSync's unique-id string, which is a
different thing from the destination's node id and is not usable as a write key.

A snapshot written before this change has no such column. That is treated as a cache miss
for the destination side — the destination is fully extracted and the reason is logged —
rather than as a plan that silently derives unkeyed updates.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, ClassVar

from diffsync import Adapter, DiffSyncModel

from infrahub_sync.cache.cursors import CursorState, CursorTier
from infrahub_sync.cache.incremental import hydrate_from_parquet
from infrahub_sync.cache.parquet_io import write_resource_side
from infrahub_sync.potenda import Potenda

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

DESTINATION_ID = "18d52a8a-7e7d-9bf5-3967-c51149d169da"


class _Device(DiffSyncModel):
    """A destination model shaped like a generated one: it carries `local_id`."""

    _modelname: ClassVar[str] = "InfraDevice"
    _identifiers: ClassVar[tuple[str, ...]] = ("name",)
    _attributes: ClassVar[tuple[str, ...]] = ("description",)

    name: str
    description: str | None = None
    local_id: str | None = None


class _StubAdapter(Adapter):
    """An adapter that records which load path the engine chose for it."""

    InfraDevice = _Device
    top_level: ClassVar[list[str]] = ["InfraDevice"]
    type = "Stub"

    def __init__(self, *, name: str, deltas: list[dict] | None = None) -> None:
        super().__init__(name=name)
        self.calls: list[tuple[str, object]] = []
        self.deltas = deltas or []

    def model_loader(self, model_name: str, _model: Any) -> None:  # noqa: ANN401
        self.calls.append(("model_loader", model_name))

    def load(self) -> None:
        self.calls.append(("full_load", None))

    def cursor_tier_for(self, _model_name: str) -> CursorTier:  # noqa: PLR6301
        return CursorTier.TIMESTAMP

    def list_changed_since(self, _model_name: str, cursor: CursorState) -> list[dict]:
        self.calls.append(("delta", cursor))
        return list(self.deltas)


def make_potenda(tmp_path: Path) -> tuple[Potenda, _StubAdapter, _StubAdapter]:
    """A `Potenda` over two stub adapters, with its run directory prepared."""
    from types import SimpleNamespace

    source = _StubAdapter(name="src")
    destination = _StubAdapter(name="dst")
    config = SimpleNamespace(diffsync_flags=[], incremental=None, name="test-sync")
    potenda = Potenda(
        source=source,
        destination=destination,
        config=config,  # ty: ignore[invalid-argument-type]
        top_level=["InfraDevice"],
        show_progress=False,
        concurrent_load=False,
    )
    potenda.run_dir = tmp_path / "run-current"
    potenda.run_dir.mkdir(parents=True)
    potenda.cache_root = tmp_path
    return potenda, source, destination


def snapshot_columns(run_dir: Path, side: str, resource: str) -> list[str]:
    """The column names of one side's snapshot of one resource."""
    from pyarrow.parquet import read_table

    return list(read_table(str(run_dir / side / f"{resource}.parquet")).column_names)


def write_prior_run(tmp_path: Path, rows: list[dict[str, object]], *, source_ids: list[str]) -> Path:
    """A completed prior run whose side-B snapshot holds `rows`."""
    prev_run = tmp_path / "2026-05-17T10-00-00Z"
    prev_run.mkdir(parents=True)
    write_resource_side(
        run_dir=prev_run,
        side="B",
        resource="InfraDevice",
        rows=rows,
        source_ids=source_ids,
        extract_ts=datetime(2026, 5, 17, 10, 0, tzinfo=timezone.utc),
    )
    (prev_run / "run.json").write_text(json.dumps({"status": "applied"}))
    (prev_run / "schema-sub-hash.txt").write_text("HASHFIXED")
    (prev_run / "cursors.json").write_text(json.dumps({"B": {"InfraDevice": "TIMESTAMP:2026-05-17T10:00:00Z"}}))
    return prev_run


def test_a_destination_snapshot_written_now_retains_local_id(tmp_path: Path) -> None:
    """The recorded destination id has to survive the run boundary to be usable next time."""
    potenda, _source, destination = make_potenda(tmp_path)
    destination.add(_Device(name="device-a", description="first", local_id=DESTINATION_ID))

    potenda._write_side_snapshot("B", destination)

    assert "local_id" in snapshot_columns(potenda.run_dir, "B", "InfraDevice")


def test_the_destination_local_id_is_not_the_diffsync_source_id(tmp_path: Path) -> None:
    """`_source_id` is DiffSync's unique-id string and keys no destination write."""
    potenda, _source, destination = make_potenda(tmp_path)
    destination.add(_Device(name="device-a", description="first", local_id=DESTINATION_ID))

    potenda._write_side_snapshot("B", destination)

    rehydrated: list[dict[str, Any]] = []
    hydrate_from_parquet(
        run_dir=potenda.run_dir,
        side="B",
        resource="InfraDevice",
        add_row=lambda _resource, payload: rehydrated.append(payload),
    )

    assert rehydrated[0]["local_id"] == DESTINATION_ID
    assert rehydrated[0].get("_source_id") is None, "The engine-controlled column must stay out of the payload."


def test_a_warm_destination_load_rebuilds_models_carrying_their_local_id(tmp_path: Path) -> None:
    """The warm path is only safe for updates if the id comes back with the model."""
    potenda, _source, destination = make_potenda(tmp_path)
    potenda._schema_subhash = "HASHFIXED"
    write_prior_run(
        tmp_path,
        [{"name": "device-a", "description": "first", "local_id": DESTINATION_ID}],
        source_ids=["device-a"],
    )

    potenda.load_one_side(side="B", adapter=destination)

    assert destination.get(_Device, "device-a").local_id == DESTINATION_ID
    assert ("full_load", None) not in destination.calls, "A usable snapshot must not force a full extract."


def test_a_pre_change_snapshot_without_the_column_forces_a_full_destination_extract(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A snapshot from before this change cannot key updates, so it is a cache miss."""
    potenda, _source, destination = make_potenda(tmp_path)
    potenda._schema_subhash = "HASHFIXED"
    write_prior_run(tmp_path, [{"name": "device-a", "description": "first"}], source_ids=["device-a"])

    with caplog.at_level("INFO"):
        potenda.load_one_side(side="B", adapter=destination)

    assert any(call[0] in {"full_load", "model_loader"} for call in destination.calls), (
        "A snapshot with no local_id column must be re-extracted, not hydrated."
    )
    assert any("local_id" in record.getMessage() for record in caplog.records), (
        "The reason for the re-extract must be logged."
    )


def test_a_source_snapshot_is_unaffected_by_the_destination_column(tmp_path: Path) -> None:
    """Side A keys nothing at the destination, so its snapshot shape does not change."""
    potenda, source, _destination = make_potenda(tmp_path)
    source.add(_Device(name="device-a", description="first", local_id=DESTINATION_ID))

    potenda._write_side_snapshot("A", source)

    assert "local_id" not in snapshot_columns(potenda.run_dir, "A", "InfraDevice")
