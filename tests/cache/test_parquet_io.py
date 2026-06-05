"""parquet_io tests — atomic writes, schema enforcement, plan roundtrip."""

from __future__ import annotations

from pathlib import Path

import pyarrow as pa

from infrahub_sync.cache.parquet_io import (
    PLAN_SCHEMA,
    read_table,
    write_plan,
    write_table,
)


def test_write_table_atomically_no_tmp_left(tmp_path: Path) -> None:
    table = pa.table({"x": [1, 2, 3]})
    target = tmp_path / "out.parquet"
    write_table(str(target), table)
    assert target.exists()
    assert not list(tmp_path.glob("*.tmp"))
    read = read_table(str(target))
    assert read.column("x").to_pylist() == [1, 2, 3]


def test_write_table_overwrites_existing(tmp_path: Path) -> None:
    target = tmp_path / "out.parquet"
    write_table(str(target), pa.table({"x": [1]}))
    write_table(str(target), pa.table({"x": [42]}))
    assert read_table(str(target)).column("x").to_pylist() == [42]


def test_plan_roundtrip(tmp_path: Path) -> None:
    rows = [
        {
            "action": "create",
            "resource": "BuiltinTag",
            "source_id": "tag-1",
            "dest_id": "",
            "attribute": "",
            "old_value": "",
            "new_value": '{"name":"prod"}',
            "owner": "",
            "skip_reason": "",
            "conflict_class": "",
        }
    ]
    write_plan(run_dir=tmp_path, rows=rows)
    table = read_table(str(tmp_path / "plan.parquet"))
    assert table.schema == PLAN_SCHEMA
    assert table.column("action").to_pylist() == ["create"]


def test_write_resource_side_injects_metadata_columns(tmp_path: Path) -> None:
    from datetime import datetime, timezone

    from infrahub_sync.cache.parquet_io import (
        read_table,
        write_resource_side,
    )

    rows = [
        {"name": "prod", "description": None},
        {"name": "dev", "description": "dev tag"},
    ]
    extract_ts = datetime(2026, 5, 12, 15, 30, tzinfo=timezone.utc)
    write_resource_side(
        run_dir=tmp_path,
        side="A",
        resource="BuiltinTag",
        rows=rows,  # ty: ignore[invalid-argument-type]
        source_ids=["tag-1", "tag-2"],
        extract_ts=extract_ts,
    )
    table = read_table(str(tmp_path / "A" / "BuiltinTag.parquet"))
    assert table.column("_source_id").to_pylist() == ["tag-1", "tag-2"]
    assert table.column("_tombstone").to_pylist() == [False, False]
    # _extract_ts uses ns-precision UTC.
    ts_col = table.column("_extract_ts").to_pylist()
    assert all(ts == extract_ts for ts in ts_col)
    # Caller-supplied columns preserved.
    assert table.column("name").to_pylist() == ["prod", "dev"]
