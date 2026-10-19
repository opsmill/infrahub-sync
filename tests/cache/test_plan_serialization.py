"""Potenda.write_plan turns a diffsync Diff into plan.parquet."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from infrahub_sync.cache.parquet_io import read_table
from infrahub_sync.potenda import Potenda


class _FakeElement:
    """Stand-in for diffsync's DiffElement."""

    def __init__(
        self,
        action: str,
        source_id: str,
        old_attrs: dict | None = None,
        new_attrs: dict | None = None,
    ):
        self.action = action
        self.name = source_id
        self._old = old_attrs or {}
        self._new = new_attrs or {}

    def get_attrs_diffs(self) -> dict:
        result: dict = {}
        if self._old:
            result["-"] = self._old
        if self._new:
            result["+"] = self._new
        return result


class _FakeDiff:
    """Mirrors diffsync.Diff's children mapping shape."""

    def __init__(self, changes_per_resource: dict[str, list[_FakeElement]]):
        self.children = {
            resource: {element.name: element for element in elements}
            for resource, elements in changes_per_resource.items()
        }

    def has_diffs(self) -> bool:
        return any(self.children.values())


def test_write_plan_writes_one_row_per_change(tmp_path: Path) -> None:
    ptd = Potenda(
        source=SimpleNamespace(top_level=[]),  # ty: ignore[invalid-argument-type]
        destination=SimpleNamespace(top_level=[]),  # ty: ignore[invalid-argument-type]
        config=None,  # ty: ignore[invalid-argument-type]
        top_level=[],
        run_dir=tmp_path,
    )
    diff = _FakeDiff(
        {
            "BuiltinTag": [
                _FakeElement("create", "prod", new_attrs={"name": "prod"}),
                _FakeElement(
                    "update",
                    "dev",
                    old_attrs={"description": "old"},
                    new_attrs={"description": "d"},
                ),
            ],
            "DcimDevice": [],
        }
    )
    ptd.write_plan(diff)
    table = read_table(str(tmp_path / "plan.parquet"))
    actions = table.column("action").to_pylist()
    resources = table.column("resource").to_pylist()
    assert sorted(zip(actions, resources)) == [("create", "BuiltinTag"), ("update", "BuiltinTag")]
