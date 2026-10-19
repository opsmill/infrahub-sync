from datetime import datetime, timezone
from pathlib import Path
from typing import Any, ClassVar

from diffsync import Adapter, DiffSyncModel

from infrahub_sync.cache.cursors import CursorState, CursorTier
from infrahub_sync.potenda import Potenda


class _Device(DiffSyncModel):
    _modelname: ClassVar[str] = "InfraDevice"
    _identifiers: ClassVar[tuple[str, ...]] = ("name",)
    _attributes: ClassVar[tuple[str, ...]] = ("description",)

    name: str
    description: str | None = None


class _StubAdapter(Adapter):
    InfraDevice = _Device
    top_level: ClassVar[list[str]] = ["InfraDevice"]
    type = "Stub"

    def __init__(self, *, name: str, deltas: list[dict] | None = None):
        super().__init__(name=name)
        self.calls: list[tuple[str, object]] = []
        self.deltas = deltas or []

    def model_loader(self, model_name: str, _model: Any) -> None:  # noqa: ANN401
        self.calls.append(("model_loader", model_name))

    def load(self) -> None:
        # Default load — record a "full_load" call and add nothing.
        self.calls.append(("full_load", None))

    def cursor_tier_for(self, _model_name: str) -> CursorTier:  # noqa: PLR6301
        return CursorTier.TIMESTAMP

    def list_changed_since(self, _model_name: str, cursor: CursorState) -> list[dict]:
        self.calls.append(("delta", cursor))
        return list(self.deltas)


def _make_potenda(tmp_path: Path):
    src = _StubAdapter(name="src")
    dst = _StubAdapter(name="dst")
    from types import SimpleNamespace

    config = SimpleNamespace(diffsync_flags=[], incremental=None, name="test-sync")
    pot = Potenda(
        source=src,
        destination=dst,
        config=config,  # ty: ignore[invalid-argument-type]
        top_level=["InfraDevice"],
        show_progress=False,
        concurrent_load=False,
    )
    pot.run_dir = tmp_path / "run-current"
    pot.run_dir.mkdir(parents=True)
    pot.cache_root = tmp_path
    return pot, src, dst


def test_falls_back_to_full_load_when_no_prior_run(tmp_path: Path) -> None:
    pot, src, _ = _make_potenda(tmp_path)
    pot._schema_subhash = "abc"

    pot.load_one_side(side="A", adapter=src)

    assert ("full_load", None) in src.calls
    assert all(call[0] != "delta" for call in src.calls)


def test_uses_incremental_when_prior_run_matches(tmp_path: Path) -> None:
    import json

    from infrahub_sync.cache.parquet_io import write_resource_side

    prev_run = tmp_path / "2026-05-17T10-00-00Z"
    prev_run.mkdir(parents=True)
    (prev_run / "run.json").write_text(json.dumps({"status": "applied"}))
    (prev_run / "schema-sub-hash.txt").write_text("HASHFIXED")
    (prev_run / "cursors.json").write_text(json.dumps({"A": {"InfraDevice": "TIMESTAMP:2026-05-17T10:00:00Z"}}))
    write_resource_side(
        run_dir=prev_run,
        side="A",
        resource="InfraDevice",
        rows=[{"name": "leaf-existing", "description": "old"}],
        source_ids=["leaf-existing"],
        extract_ts=datetime(2026, 5, 17, 10, tzinfo=timezone.utc),
    )

    pot, src, _ = _make_potenda(tmp_path)
    src.deltas = [{"name": "leaf-new", "description": "new"}]
    pot.cache_root = tmp_path
    pot._schema_subhash = "HASHFIXED"

    pot.load_one_side(side="A", adapter=src)

    assert all(call[0] != "full_load" for call in src.calls)
    assert any(call[0] == "delta" for call in src.calls)
    names = {d.name for d in src.get_all("InfraDevice")}
    assert names == {"leaf-existing", "leaf-new"}


def test_cursor_persisted_after_load(tmp_path: Path) -> None:
    from infrahub_sync.cache.incremental import load_cursors
    from infrahub_sync.cache.parquet_io import write_resource_side

    pot, src, _ = _make_potenda(tmp_path)
    pot._schema_subhash = "abc"

    # First run: full extract (no prior run), then snapshot is written +
    # cursor persisted. We simulate the snapshot directly because the
    # stub adapter doesn't actually populate the store.
    pot.load_one_side(side="A", adapter=src)
    write_resource_side(
        run_dir=pot.run_dir,
        side="A",
        resource="InfraDevice",
        rows=[{"name": "leaf1", "description": "x"}],
        source_ids=["leaf1"],
        extract_ts=datetime(2026, 5, 18, 11, tzinfo=timezone.utc),
    )
    pot.persist_cursors_for_run(side="A")

    cursors_path = pot.run_dir / "cursors.json"
    assert cursors_path.exists()

    loaded = load_cursors(cursors_path, side="A")
    assert loaded["InfraDevice"].tier is CursorTier.TIMESTAMP
    assert loaded["InfraDevice"].value == "2026-05-18T11:00:00+00:00"
