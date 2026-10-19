"""Sync (serial and parallel) writes the same cache artifacts as diff:
pipeline_lock, run.json, plan.parquet, last-successful-rowcounts.json."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from infrahub_sync.cache.parquet_io import read_table
from infrahub_sync.potenda import Potenda


class _FakeRecord(SimpleNamespace):
    _identifiers: tuple[str, ...] = ("name",)

    def get_attrs(self) -> dict:
        return {"name": self.name}

    def get_unique_id(self) -> str:
        return self.name


def _make_fake_adapter(records_by_kind: dict[str, list[_FakeRecord]]) -> MagicMock:
    adapter = MagicMock()
    adapter.top_level = list(records_by_kind.keys())
    adapter.get_all.side_effect = lambda kind: records_by_kind.get(kind, [])
    adapter.load = MagicMock()
    return adapter


class _Child:
    def __init__(self, action: str, name: str) -> None:
        self.action = action
        self.name = name

    def get_attrs_diffs(self) -> dict:  # noqa: PLR6301
        return {"+": {"name": "x"}}


class _Diff:
    def __init__(self, changes: dict[str, list[_Child]]) -> None:  # ty: ignore[invalid-type-form]
        self.children = {resource: {child.name: child for child in elements} for resource, elements in changes.items()}

    def has_diffs(self) -> bool:
        return any(self.children.values())

    def str(self) -> str:  # noqa: PLR6301  # ty: ignore[invalid-type-form]
        return ""


def test_sync_in_tiers_aggregates_plan_rows_across_tiers(tmp_path: Path) -> None:
    """One plan.parquet at <run_dir> with rows from EVERY tier."""
    src = _make_fake_adapter({"Tag": [_FakeRecord(name="prod")], "Device": [_FakeRecord(name="d1")]})
    dst = _make_fake_adapter({"Tag": [], "Device": []})

    # Two tiers, two distinct fake diffs.
    diffs_per_call = iter(
        [
            _Diff({"Tag": [_Child("create", "prod")]}),
            _Diff({"Device": [_Child("create", "d1")]}),
        ]
    )

    ptd = Potenda(
        source=src,
        destination=dst,
        config=None,  # ty: ignore[invalid-argument-type]
        top_level=["Tag", "Device"],
        tiers=[{"Tag"}, {"Device"}],
        run_dir=tmp_path,
    )
    # Patch out diff/sync to use the fake diff iterator.
    ptd.diff = lambda: next(diffs_per_call)  # ty: ignore[invalid-assignment]
    ptd.sync = MagicMock()  # ty: ignore[invalid-assignment]
    ptd.persist_baseline_counts = MagicMock()  # ty: ignore[invalid-assignment]

    ptd.sync_in_tiers(parallel=True)

    plan = read_table(str(tmp_path / "plan.parquet"))
    assert sorted(plan.column("resource").to_pylist()) == ["Device", "Tag"]
    assert sorted(plan.column("action").to_pylist()) == ["create", "create"]
    ptd.persist_baseline_counts.assert_called_once()  # ty: ignore[unresolved-attribute]


def test_sync_in_tiers_runs_rowcount_guardrail(tmp_path: Path) -> None:
    src = _make_fake_adapter({"Tag": [_FakeRecord(name="prod")]})
    dst = _make_fake_adapter({"Tag": []})

    ptd = Potenda(
        source=src,
        destination=dst,
        config=None,  # ty: ignore[invalid-argument-type]
        top_level=["Tag"],
        tiers=[{"Tag"}],
        run_dir=tmp_path,
    )
    ptd.diff = lambda: _Diff({"Tag": []})  # ty: ignore[invalid-assignment]
    ptd.sync = MagicMock()  # ty: ignore[invalid-assignment]
    ptd.check_rowcount_guardrail = MagicMock()  # ty: ignore[invalid-assignment]
    ptd.persist_baseline_counts = MagicMock()  # ty: ignore[invalid-assignment]

    ptd.sync_in_tiers(parallel=True, allow_rowcount_drop=True)

    ptd.check_rowcount_guardrail.assert_called_once_with(allow_drop=True)  # ty: ignore[unresolved-attribute]
    ptd.persist_baseline_counts.assert_called_once()  # ty: ignore[unresolved-attribute]
