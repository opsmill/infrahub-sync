"""Atomic Parquet I/O + the well-known schemas used in the cache layout."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import fsspec

if TYPE_CHECKING:
    from datetime import datetime
    from pathlib import Path
import pyarrow as pa
import pyarrow.parquet as pq

logger = logging.getLogger(__name__)


# Columns injected into every per-resource snapshot by `write_resource_side`.
# Single-source so consumers (e.g. `hydrate_from_parquet`) can strip them
# without re-listing the names.
SNAPSHOT_INTERNAL_COLUMNS = frozenset({"_extract_ts", "_source_id", "_tombstone"})


PLAN_SCHEMA = pa.schema(
    [
        pa.field("action", pa.string(), nullable=False),
        pa.field("resource", pa.string(), nullable=False),
        pa.field("source_id", pa.string(), nullable=False),
        pa.field("dest_id", pa.string()),
        pa.field("attribute", pa.string()),
        pa.field("old_value", pa.string()),
        pa.field("new_value", pa.string()),
        pa.field("owner", pa.string()),
        pa.field("skip_reason", pa.string()),
        pa.field("conflict_class", pa.string()),
    ]
)


ERRORS_SCHEMA = pa.schema(
    [
        pa.field("error_class", pa.string(), nullable=False),
        pa.field("resource", pa.string(), nullable=False),
        pa.field("source_id", pa.string()),
        pa.field("dest_id", pa.string()),
        pa.field("attribute", pa.string()),
        pa.field("message", pa.string(), nullable=False),
        pa.field("hint", pa.string()),
        pa.field("retry_count", pa.int64(), nullable=False),
        pa.field("terminal", pa.bool_(), nullable=False),
    ]
)


def write_table(uri: str, table: pa.Table) -> None:
    """Write a Parquet table to `uri` atomically.

    The write goes to `<uri>.tmp` and is then renamed over `uri`, so a
    crashed process never leaves a half-written canonical file.
    """
    fs, path = fsspec.core.url_to_fs(uri)
    tmp_path = f"{path}.tmp"
    parent = path.rsplit("/", 1)[0] if "/" in path else "."
    if not fs.exists(parent):
        fs.makedirs(parent, exist_ok=True)
    with fs.open(tmp_path, "wb") as fh:
        pq.write_table(table, fh, compression="snappy")
    if fs.exists(path):
        fs.rm(path)
    fs.mv(tmp_path, path)


def read_table(uri: str) -> pa.Table:
    """Read a Parquet table from `uri`."""
    fs, path = fsspec.core.url_to_fs(uri)
    with fs.open(path, "rb") as fh:
        return pq.read_table(fh)


def write_plan(*, run_dir: Path, rows: list[dict[str, str]]) -> None:
    """Write the diff plan to `<run_dir>/plan.parquet`."""
    table = pa.Table.from_pylist(rows, schema=PLAN_SCHEMA)
    write_table(str(run_dir / "plan.parquet"), table)


def read_plan(*, run_dir: Path) -> pa.Table:
    """Read the diff plan from `<run_dir>/plan.parquet`."""
    return read_table(str(run_dir / "plan.parquet"))


def write_resource_side(
    *,
    run_dir: Path,
    side: str,
    resource: str,
    rows: list[dict[str, object]],
    source_ids: list[str],
    extract_ts: datetime,
    tombstones: list[bool] | None = None,
) -> None:
    """Write one side's snapshot of one resource to
    `<run_dir>/<side>/<resource>.parquet`.

    Injects three engine-controlled columns: `_extract_ts`, `_source_id`,
    `_tombstone`. `side` is "A" (source) or "B" (destination).
    """
    if side not in {"A", "B"}:
        msg = f"side must be 'A' or 'B', got {side!r}"
        raise ValueError(msg)
    if len(rows) != len(source_ids):
        msg = (
            f"rows ({len(rows)}) and source_ids ({len(source_ids)}) length "
            "mismatch — refusing to write a misaligned snapshot."
        )
        raise ValueError(msg)
    tombs = tombstones if tombstones is not None else [False] * len(rows)
    if len(tombs) != len(rows):
        msg = "tombstones length does not match rows length"
        raise ValueError(msg)

    if rows:
        merged = []
        for row, sid, tomb in zip(rows, source_ids, tombs, strict=True):
            payload = dict(row)
            payload["_extract_ts"] = extract_ts
            payload["_source_id"] = sid
            payload["_tombstone"] = tomb
            merged.append(payload)
        table = pa.Table.from_pylist(merged)
    else:
        # Empty snapshots still get a file (so re-apply can see "0 rows" and
        # the guardrail can compare counts).
        table = pa.table(
            {
                "_extract_ts": pa.array([], type=pa.timestamp("ns", tz="UTC")),
                "_source_id": pa.array([], type=pa.string()),
                "_tombstone": pa.array([], type=pa.bool_()),
            }
        )

    write_table(str(run_dir / side / f"{resource}.parquet"), table)
