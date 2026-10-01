"""Source-guaranteed watermarks and full extraction when no bound is safe."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar

import pytest
from diffsync import Adapter, DiffSyncModel

from infrahub_sync.cache.cursors import CursorState, CursorTier
from infrahub_sync.cache.incremental import load_cursors
from infrahub_sync.cache.parquet_io import read_table
from infrahub_sync.potenda import Potenda


class _Device(DiffSyncModel):
    _modelname: ClassVar[str] = "Device"
    _identifiers: ClassVar[tuple[str, ...]] = ("name",)
    _attributes: ClassVar[tuple[str, ...]] = ("description",)

    name: str
    description: str
    local_id: str | None = None


class _Empty(_Device):
    _modelname: ClassVar[str] = "Empty"


# The source rounds timestamps to seconds. Even a change after the first query
# can therefore equal the truncated source clock at extraction start.
SOURCE_START = datetime(2026, 1, 1, 0, 0, 1, 999999, tzinfo=timezone.utc)
CHANGE_TIME = SOURCE_START.replace(microsecond=0)
SAFE_BOUND = CHANGE_TIME - timedelta(seconds=1)


class _TimestampSource(Adapter):
    Device = _Device
    Empty = _Empty
    top_level: ClassVar[list[str]] = ["Device", "Empty"]

    def __init__(self, rows: dict[str, list[dict]], *, safe: bool, exclusive: bool, mutate: bool) -> None:
        """Initialize synthetic rows, cursor guarantees, and query tracking."""
        super().__init__(name="timestamp-source")
        self.rows = rows
        self.safe = safe
        self.exclusive = exclusive
        self.mutate = mutate
        self.calls: list[tuple[str, object]] = []

    def cursor_tier_for(self, _model_name: str) -> CursorTier:  # noqa: PLR6301
        """Advertise timestamp queries for each synthetic resource."""
        return CursorTier.TIMESTAMP

    def safe_cursor_before_load(self, model_name: str) -> CursorState | None:
        """Return a source bound that accounts for precision and query boundaries."""
        self.calls.append(("bound", model_name))
        # The synthetic source guarantees this bound precedes all changes that
        # can commit after the queries start, including its precision bucket.
        bound = SAFE_BOUND if self.exclusive else CHANGE_TIME
        return CursorState(CursorTier.TIMESTAMP, bound.isoformat(), safe=True) if self.safe else None

    def model_loader(self, model_name: str, model: type[DiffSyncModel]) -> None:
        """Load a resource and optionally change rows during the final query."""
        self.calls.append(("full", model_name))
        for row in self.rows[model_name]:
            self.add(model(**row))
        if self.mutate and model_name == self.top_level[-1]:
            self.rows["Device"][0]["description"] = "new"
            if "Empty" in self.top_level:
                self.rows["Empty"] = [{"name": "appeared", "description": "new"}]

    def load(self) -> None:
        """Load every configured synthetic resource in order."""
        for model_name in self.top_level:
            self.model_loader(model_name, getattr(self, model_name))

    def list_changed_since(self, model_name: str, cursor: CursorState) -> list[dict]:
        """Return repeated changed rows using the configured timestamp boundary."""
        self.calls.append(("delta", cursor))
        assert cursor.value is not None
        bound = datetime.fromisoformat(cursor.value)
        include = bound < CHANGE_TIME if self.exclusive else bound <= CHANGE_TIME
        # Deliberate overlap and repeated rows must update cached objects in memory.
        rows = [dict(row) for row in self.rows[model_name]] if include else []
        return rows + rows


def _engine(root: Path, run: str, source: _TimestampSource, side: str) -> Potenda:
    """Build a sequential engine with an isolated run directory."""
    other = _TimestampSource({"Device": [], "Empty": []}, safe=False, exclusive=False, mutate=False)
    pot = Potenda(
        source=source if side == "A" else other,
        destination=source if side == "B" else other,
        config=SimpleNamespace(diffsync_flags=[]),  # ty: ignore[invalid-argument-type]
        top_level=["Device", "Empty"] if "Empty" in source.rows else ["Device"],
        show_progress=False,
        concurrent_load=False,
        run_dir=root / run,
        cache_root=root,
        schema_subhash="matching-schema",
    )
    assert pot.run_dir is not None
    pot.run_dir.mkdir()
    return pot


@pytest.mark.parametrize("side", ["A", "B"])
@pytest.mark.parametrize("multiple_resources", [False, True])
@pytest.mark.parametrize("exclusive", [False, True])
@pytest.mark.parametrize("safe", [False, True])
def test_in_flight_change_is_read_next_run(
    tmp_path: Path, side: str, *, multiple_resources: bool, exclusive: bool, safe: bool
) -> None:
    """Mid-load changes survive precision loss, strict boundaries and clock skew."""
    rows = {"Device": [{"name": "device", "description": "old", "local_id": "original-id"}]}
    if multiple_resources:
        rows["Empty"] = []
    first_source = _TimestampSource(rows, safe=safe, exclusive=exclusive, mutate=True)
    first = _engine(tmp_path, "run-1", first_source, side)
    load_first = first.source_load if side == "A" else first.destination_load
    load_first()
    assert first_source.get("Device", "device").description == "old"  # ty: ignore[unresolved-attribute]
    first.persist_cursors_for_run(side=side)
    assert first.run_dir is not None
    # The host's diagnostic timestamp is far ahead of the source. It must never
    # become the query bound, even for an empty resource.
    metadata_ts = read_table(str(first.run_dir / side / "Device.parquet")).column("_extract_ts")[0].as_py()
    assert metadata_ts > CHANGE_TIME
    saved = load_cursors(first.run_dir / "cursors.json", side=side)
    if safe:
        assert set(saved) == set(rows)
        expected_bound = SAFE_BOUND if exclusive else CHANGE_TIME
        assert all(cursor.safe and cursor.value == expected_bound.isoformat() for cursor in saved.values())
    else:
        assert saved == {}
    assert first_source.calls[: len(rows)] == [("bound", resource) for resource in rows]
    (first.run_dir / "schema-sub-hash.txt").write_text("matching-schema")
    (first.run_dir / "run.json").write_text(json.dumps({"status": "dry-run"}))

    second_source = _TimestampSource(rows, safe=safe, exclusive=exclusive, mutate=False)
    second = _engine(tmp_path, "run-2", second_source, side)
    load_second = second.source_load if side == "A" else second.destination_load
    load_second()
    device = second_source.get("Device", "device")
    assert device.description == "new"  # ty: ignore[unresolved-attribute]
    assert len(second_source.get_all("Device")) == 1
    if side == "B":
        assert device.local_id == "original-id"  # ty: ignore[unresolved-attribute]
    if multiple_resources:
        assert second_source.get("Empty", "appeared").description == "new"  # ty: ignore[unresolved-attribute]
    assert any(call[0] == "delta" for call in second_source.calls) is safe
    assert second._side_full_extract[side] is not safe


def test_failed_load_does_not_persist_a_candidate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Discard the captured source bound when extraction fails."""
    source = _TimestampSource({"Device": []}, safe=True, exclusive=False, mutate=False)
    pot = _engine(tmp_path, "run-1", source, "A")

    def fail() -> None:
        """Raise a synthetic extraction failure."""
        msg = "load failed"
        raise RuntimeError(msg)

    monkeypatch.setattr(source, "load", fail)
    with pytest.raises(ValueError, match="load failed"):
        pot.source_load()
    pot.persist_cursors_for_run(side="A")
    assert pot.run_dir is not None
    assert not (pot.run_dir / "cursors.json").exists()


@pytest.mark.parametrize("side", ["A", "B"])
@pytest.mark.parametrize("stage", ["load", "snapshot"])
@pytest.mark.parametrize("interrupt_type", [KeyboardInterrupt, SystemExit])
def test_interrupted_reload_cannot_advance_cursor_against_retained_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    side: str,
    stage: str,
    interrupt_type: type[BaseException],
) -> None:
    """Reusing a successful run after interruption must retain its safe bound."""
    rows = {"Device": [{"name": "device", "description": "old", "local_id": "original-id"}]}
    source = _TimestampSource(rows, safe=True, exclusive=False, mutate=False)
    first = _engine(tmp_path, "run-1", source, side)
    load = first.source_load if side == "A" else first.destination_load
    load()
    first.persist_cursors_for_run(side=side)
    assert first.run_dir is not None
    cursor_path = first.run_dir / "cursors.json"
    saved_cursors = cursor_path.read_bytes()
    snapshot_path = first.run_dir / side / "Device.parquet"
    saved_snapshot = snapshot_path.read_bytes()
    (first.run_dir / "schema-sub-hash.txt").write_text("matching-schema")
    (first.run_dir / "run.json").write_text(json.dumps({"status": "dry-run"}))

    rows["Device"][0]["description"] = "new"
    interrupt = interrupt_type("reload interrupted")

    def reload() -> None:
        """Update the stored row and optionally interrupt extraction."""
        row = rows["Device"][0]
        source.update_or_add_model_instance(
            _Device(name=row["name"], description=row["description"], local_id=row["local_id"])
        )
        if stage == "load":
            raise interrupt

    def interrupt_snapshot(**_kwargs: object) -> None:
        """Interrupt snapshot writing before any bytes are written."""
        raise interrupt

    with monkeypatch.context() as patch:
        patch.setattr(
            source,
            "safe_cursor_before_load",
            lambda _resource: CursorState(
                CursorTier.TIMESTAMP, (CHANGE_TIME + timedelta(seconds=10)).isoformat(), safe=True
            ),
        )
        patch.setattr(source, "load", reload)
        patch.setattr("infrahub_sync.cache.parquet_io.write_resource_side", interrupt_snapshot)
        with pytest.raises(interrupt_type) as caught:
            load()
        assert caught.value is interrupt

    first.persist_cursors_for_run(side=side)
    assert cursor_path.read_bytes() == saved_cursors
    assert snapshot_path.read_bytes() == saved_snapshot

    next_source = _TimestampSource(rows, safe=True, exclusive=False, mutate=False)
    second = _engine(tmp_path, "run-2", next_source, side)
    load_next = second.source_load if side == "A" else second.destination_load
    load_next()
    assert any(call[0] == "delta" for call in next_source.calls)
    assert next_source.get("Device", "device").description == "new"  # ty: ignore[unresolved-attribute]


def test_forced_full_extract_does_not_request_a_safe_cursor(tmp_path: Path) -> None:
    """Bypass source cursor hooks during forced full extraction."""
    source = _TimestampSource({"Device": []}, safe=True, exclusive=False, mutate=False)
    pot = _engine(tmp_path, "run-1", source, "A")
    pot.force_full_extract = True
    pot.source_load()
    assert source.calls == [("full", "Device")]
    pot.persist_cursors_for_run(side="A")
    assert pot.run_dir is not None
    assert not (pot.run_dir / "cursors.json").exists()


def test_legacy_watermark_is_not_used_even_with_a_safe_source(tmp_path: Path) -> None:
    """Replace an unqualified host watermark with a full extraction."""
    source = _TimestampSource(
        {"Device": [{"name": "device", "description": "old"}]}, safe=True, exclusive=False, mutate=True
    )
    first = _engine(tmp_path, "run-1", source, "A")
    first.source_load()
    assert first.run_dir is not None
    (first.run_dir / "run.json").write_text(json.dumps({"status": "dry-run"}))
    (first.run_dir / "schema-sub-hash.txt").write_text("matching-schema")
    # An old, host-derived bound can be later than changes the cache never saw.
    (first.run_dir / "cursors.json").write_text(json.dumps({"A": {"Device": "TIMESTAMP:2099-01-01T00:00:00Z"}}))
    second_source = _TimestampSource(source.rows, safe=True, exclusive=False, mutate=False)
    second = _engine(tmp_path, "run-2", second_source, "A")
    second.source_load()
    assert second_source.get("Device", "device").description == "new"  # ty: ignore[unresolved-attribute]
    assert not any(call[0] == "delta" for call in second_source.calls)


@pytest.mark.parametrize("delta_id", [None, "replacement-id"])
def test_overlap_updates_are_in_memory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, delta_id: str | None) -> None:
    """Last yielded attributes win; missing destination ids retain the cached id."""
    rows = {"Device": [{"name": "device", "description": "cached", "local_id": "cached-id"}]}
    first_source = _TimestampSource(rows, safe=True, exclusive=False, mutate=False)
    first = _engine(tmp_path, "run-1", first_source, "B")
    first.destination_load()
    first.persist_cursors_for_run(side="B")
    assert first.run_dir is not None
    (first.run_dir / "run.json").write_text(json.dumps({"status": "dry-run"}))
    (first.run_dir / "schema-sub-hash.txt").write_text("matching-schema")

    source = _TimestampSource(rows, safe=True, exclusive=False, mutate=False)
    second = _engine(tmp_path, "run-2", source, "B")
    delta = {"name": "device", "description": ""}
    if delta_id is not None:
        delta["local_id"] = delta_id
    monkeypatch.setattr(
        source,
        "list_changed_since",
        lambda _resource, _cursor: [{"name": "device", "description": "earlier"}, delta, delta],
    )

    def refuse_write(*_args: object, **_kwargs: object) -> None:
        """Fail if extraction invokes a destination write method."""
        pytest.fail("extraction must not invoke a model write method")

    monkeypatch.setattr(_Device, "update", refuse_write)
    second.destination_load()
    device = source.get("Device", "device")
    assert isinstance(device, _Device)
    assert not device.description
    assert device.local_id == (delta_id or "cached-id")
    assert len(source.get_all("Device")) == 1


def _plan_engine(root: Path, run: str, source: _TimestampSource, destination: _TimestampSource) -> Potenda:
    """Build a real mapped engine that can write and read a saved plan."""
    from infrahub_sync import SchemaMappingField, SchemaMappingModel, SyncAdapter, SyncInstance

    config = SyncInstance(
        name="safe-watermarks",
        directory=str(root),
        source=SyncAdapter(name="netbox"),
        destination=SyncAdapter(name="infrahub"),
        order=list(source.rows),
        schema_mapping=[
            SchemaMappingModel(
                name=kind,
                mapping=kind,
                identifiers=["name"],
                fields=[SchemaMappingField(name=field, mapping=field) for field in ("name", "description")],
            )
            for kind in source.rows
        ],
    )
    engine = Potenda(
        source=source,
        destination=destination,
        config=config,
        top_level=list(source.rows),
        show_progress=False,
        concurrent_load=False,
        run_dir=root / run,
        run_id=run,
        cache_root=root,
        schema_subhash="matching-schema",
    )
    assert engine.run_dir is not None
    engine.run_dir.mkdir()
    return engine


def _retain_run(engine: Potenda) -> None:
    """Retain the successful run metadata and explicit direct-caller cursors."""
    assert engine.run_dir is not None
    engine.persist_cursors_for_run(side="A")
    engine.persist_cursors_for_run(side="B")
    (engine.run_dir / "run.json").write_text(json.dumps({"status": "dry-run"}))
    (engine.run_dir / "schema-sub-hash.txt").write_text("matching-schema")


@pytest.mark.parametrize("safe_destination", [False, True])
def test_complete_fallback_destination_records_delete_proposals(tmp_path: Path, *, safe_destination: bool) -> None:
    """Only a complete full fallback computes deletes, as recorded in the saved plan."""
    from infrahub_sync.plan.reader import load_plan_artifact

    source_rows = {"Device": [{"name": "device", "description": "unchanged"}]}
    destination_rows = {"Device": [*source_rows["Device"], {"name": "orphan", "description": "destination-only"}]}
    first = _plan_engine(
        tmp_path,
        "run-1",
        _TimestampSource(source_rows, safe=True, exclusive=False, mutate=False),
        _TimestampSource(destination_rows, safe=safe_destination, exclusive=False, mutate=False),
    )
    first.load_both_sides()
    first.write_plan(first.diff())
    assert first.run_dir is not None
    full_plan = load_plan_artifact(first.run_dir)
    assert full_plan.manifest.delete_operations_computed is True
    assert len(full_plan.operations) == 1
    _retain_run(first)
    second = _plan_engine(
        tmp_path,
        "run-2",
        _TimestampSource(source_rows, safe=True, exclusive=False, mutate=False),
        _TimestampSource(destination_rows, safe=safe_destination, exclusive=False, mutate=False),
    )
    second.load_both_sides()
    second.write_plan(second.diff())
    assert second.run_dir is not None
    plan = load_plan_artifact(second.run_dir)
    assert second._side_full_extract == {"A": False, "B": not safe_destination}
    assert plan.manifest.delete_operations_computed is not safe_destination
    deletes = [operation for operation in plan.operations if operation.action == "delete"]
    assert [operation.identity for operation in deletes] == ([] if safe_destination else [{"name": "orphan"}])
    assert plan.manifest.operations_count == len(deletes)
    assert isinstance(second.destination, _TimestampSource)
    assert [call[0] for call in second.destination.calls] == ["bound", "delta" if safe_destination else "full"]

    # Identical input encodes identically after complete fallback. A genuine
    # incremental destination changes both the delete disclosure and checksum.
    assert (plan.manifest.plan_checksum == full_plan.manifest.plan_checksum) is not safe_destination


@pytest.mark.parametrize("snapshot_state", ["missing", "empty"])
def test_source_snapshot_cache_miss_does_not_delete_existing_objects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, snapshot_state: str
) -> None:
    """An absent cache requires a full query; a valid empty cache can use a delta."""
    from infrahub_sync.plan.reader import load_plan_artifact

    rows = {"Device": [{"name": "device", "description": "unchanged"}], "Empty": []}
    first_source_rows = rows if snapshot_state == "missing" else {"Device": [], "Empty": []}
    first = _plan_engine(
        tmp_path,
        "run-1",
        _TimestampSource(first_source_rows, safe=True, exclusive=False, mutate=False),
        _TimestampSource(rows, safe=False, exclusive=False, mutate=False),
    )
    first.load_both_sides()
    _retain_run(first)
    assert first.run_dir is not None
    assert load_cursors(first.run_dir / "cursors.json", side="A")["Device"].safe
    if snapshot_state == "missing":
        (first.run_dir / "A" / "Device.parquet").unlink()

    source = _TimestampSource(rows, safe=True, exclusive=False, mutate=False)
    # The existing Device has not changed since the saved cursor. A missing
    # baseline plus an empty delta must not reconstruct an empty source store.
    if snapshot_state == "missing":
        monkeypatch.setattr(source, "list_changed_since", lambda _resource, _cursor: [])
    second = _plan_engine(
        tmp_path,
        "run-2",
        source,
        _TimestampSource(rows, safe=False, exclusive=False, mutate=False),
    )
    second.load_both_sides()
    second.write_plan(second.diff())
    assert second.run_dir is not None
    plan = load_plan_artifact(second.run_dir)
    assert plan.manifest.delete_operations_computed is True
    assert plan.operations == []
    assert len(source.get_all("Device")) == 1
    assert (("full", "Device") in source.calls) is (snapshot_state == "missing")
    # Empty is still incremental; a single full resource does not make A full.
    assert second._side_full_extract == {"A": False, "B": True}


@pytest.mark.parametrize("side", ["A", "B"])
def test_missing_required_model_refuses_before_resource_queries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, side: str
) -> None:
    """A missing source model cannot become an apparently absent source kind."""
    rows = {"Device": [{"name": "device", "description": "unchanged"}], "Empty": []}
    first = _plan_engine(
        tmp_path,
        "run-1",
        _TimestampSource(rows, safe=False, exclusive=False, mutate=False),
        _TimestampSource(rows, safe=False, exclusive=False, mutate=False),
    )
    first.load_both_sides()
    _retain_run(first)
    second = _plan_engine(
        tmp_path,
        "run-2",
        _TimestampSource(rows, safe=False, exclusive=False, mutate=False),
        _TimestampSource(rows, safe=False, exclusive=False, mutate=False),
    )
    adapter = second.source if side == "A" else second.destination
    assert isinstance(adapter, _TimestampSource)
    monkeypatch.setattr(adapter, "Empty", None)
    with pytest.raises(ValueError, match="required model 'Empty' is missing"):
        second.load_both_sides()
    assert all(call[0] == "bound" for call in adapter.calls)
    assert second._side_full_extract[side] is False
    second.persist_cursors_for_run(side=side)
    assert second.run_dir is not None
    assert not (second.run_dir / "cursors.json").exists()
    assert not (second.run_dir / "plan").exists()


@pytest.mark.parametrize("side", ["A", "B"])
def test_failed_resource_fallback_is_not_marked_full(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, side: str
) -> None:
    """One successful resource cannot certify a failed multi-resource extraction."""
    rows = {"Device": [], "Empty": []}
    first = _plan_engine(
        tmp_path,
        "run-1",
        _TimestampSource(rows, safe=False, exclusive=False, mutate=False),
        _TimestampSource(rows, safe=False, exclusive=False, mutate=False),
    )
    first.load_both_sides()
    _retain_run(first)
    source = _TimestampSource(rows, safe=True, exclusive=False, mutate=False)
    second = _engine(tmp_path, "run-2", source, side)
    completed: list[str] = []

    def fail_later_resource(model_name: str, model: type[DiffSyncModel]) -> None:  # noqa: ARG001
        """Fail the later resource after recording the first successful query."""
        if model_name == "Empty":
            msg = "later resource failed"
            raise RuntimeError(msg)
        completed.append(model_name)

    monkeypatch.setattr(source, "model_loader", fail_later_resource)
    load = second.source_load if side == "A" else second.destination_load
    with pytest.raises(ValueError, match="later resource failed"):
        load()
    assert completed == ["Device"]
    assert second._side_full_extract[side] is False
    second.persist_cursors_for_run(side=side)
    assert second.run_dir is not None
    assert not (second.run_dir / "cursors.json").exists()
