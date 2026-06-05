"""Potenda writes per-resource Parquet snapshots when run_dir is set."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from infrahub_sync.cache.parquet_io import read_table
from infrahub_sync.potenda import Potenda


class _FakeRecord(SimpleNamespace):
    """Stand-in for a DiffSyncModel instance."""

    _identifiers: tuple[str, ...] = ("name",)

    def get_attrs(self) -> dict:
        return {"name": self.name, "description": getattr(self, "description", None)}

    def get_unique_id(self) -> str:
        return self.name


def _make_fake_adapter(records_by_kind: dict[str, list[_FakeRecord]]) -> MagicMock:
    """diffsync-like adapter that yields records via `get_all`."""
    adapter = MagicMock()
    adapter.top_level = list(records_by_kind.keys())
    adapter.get_all.side_effect = lambda kind: records_by_kind.get(kind, [])
    adapter.load = MagicMock()
    return adapter


def test_potenda_writes_resource_parquet(tmp_path: Path) -> None:
    src = _make_fake_adapter({"BuiltinTag": [_FakeRecord(name="prod", description="x"), _FakeRecord(name="dev")]})
    dst = _make_fake_adapter({"BuiltinTag": [_FakeRecord(name="prod", description="x")]})
    ptd = Potenda(
        source=src,
        destination=dst,
        config=None,  # ty: ignore[invalid-argument-type]
        top_level=["BuiltinTag"],
        run_dir=tmp_path,
    )
    ptd.source_load()
    ptd.destination_load()

    a_table = read_table(str(tmp_path / "A" / "BuiltinTag.parquet"))
    b_table = read_table(str(tmp_path / "B" / "BuiltinTag.parquet"))
    assert sorted(a_table.column("_source_id").to_pylist()) == ["dev", "prod"]
    assert b_table.column("_source_id").to_pylist() == ["prod"]
