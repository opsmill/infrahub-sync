"""Sidecar load/save tests for JSON metadata files."""

from __future__ import annotations

from pathlib import Path

from infrahub_sync.cache.sidecars import (
    CursorsFile,
    RunFile,
    SchemaHashFile,
)


def test_cursors_file_load_default_missing(tmp_path: Path) -> None:
    f = CursorsFile.load_or_default(tmp_path / "cursors.json")
    assert f.cursors == {"A": {}, "B": {}}


def test_cursors_file_roundtrip(tmp_path: Path) -> None:
    f = CursorsFile.load_or_default(tmp_path / "cursors.json")
    f.cursors = {"A": {"BuiltinTag": "2026-05-12T15:30:00Z"}, "B": {}}
    f.save()
    g = CursorsFile.load_or_default(tmp_path / "cursors.json")
    assert g.cursors == f.cursors


def test_run_file_records_status(tmp_path: Path) -> None:
    f = RunFile.load_or_default(tmp_path / "run.json")
    f.status = "dry-run"
    f.mode = "diff"
    f.summary = {"resources": 17, "diff_rows": 42}
    f.save()
    g = RunFile.load_or_default(tmp_path / "run.json")
    assert g.status == "dry-run"
    assert g.summary["resources"] == 17


def test_schema_hash_file_text_roundtrip(tmp_path: Path) -> None:
    f = SchemaHashFile(path=tmp_path / "schema-sub-hash.txt", value="abc123")
    f.save()
    assert SchemaHashFile.load(tmp_path / "schema-sub-hash.txt").value == "abc123"


def test_schema_subhash_stable_across_runs() -> None:
    """Same config + same schema => same hash. Different config => different hash."""
    from types import SimpleNamespace

    from infrahub_sync import SchemaMappingField, SchemaMappingModel, SyncAdapter, SyncConfig
    from infrahub_sync.cache import compute_schema_subhash

    cfg_a = SyncConfig(
        name="t",
        source=SyncAdapter(name="netbox"),
        destination=SyncAdapter(name="infrahub"),
        schema_mapping=[SchemaMappingModel(name="Tag", fields=[SchemaMappingField(name="name")])],
    )
    schema_a = {"Tag": SimpleNamespace(kind="Tag", attributes=[], relationships=[])}
    h1 = compute_schema_subhash(cfg_a, schema_a)
    h2 = compute_schema_subhash(cfg_a, schema_a)
    assert h1 == h2

    cfg_b = SyncConfig(
        name="t",
        source=SyncAdapter(name="netbox"),
        destination=SyncAdapter(name="infrahub"),
        schema_mapping=[SchemaMappingModel(name="Tag", fields=[SchemaMappingField(name="slug")])],
    )
    assert compute_schema_subhash(cfg_b, schema_a) != h1
