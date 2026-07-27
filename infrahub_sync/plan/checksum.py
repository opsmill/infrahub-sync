"""The plan checksum and the source-snapshot binding (FR-004, FR-010, FR-027.8).

`compute_plan_checksum` covers the canonical manifest **minus** the three fields
`CHECKSUM_EXCLUDED_FIELDS` names, concatenated with the raw bytes of
`operations.jsonl` and **no separator** between the two (AD035). The three fields are
*removed* from the mapping before canonicalization, never blanked: a manifest carrying
`run_id` with a real value hashes as though the key were absent, and **not** as though it
were `null`. (A manifest that already carries them as `null` hashes the same as one that
omits them, because the filter is by name — the removed/blanked distinction is only
observable on a manifest whose excluded fields carry real values.)

`source_snapshot_digest` digests a snapshot's **logical rows** — the Parquet table with
the engine-injected `_extract_ts` column dropped — and not the file's bytes (AD037,
PD-008). `_extract_ts` is allocated once per side per run
(`infrahub_sync/potenda/__init__.py:130`) and injected into every row
(`infrahub_sync/cache/parquet_io.py:126`), so a raw-bytes digest would differ on every
re-plan of an unchanged source and make SC-006 unachievable. `_source_id` and
`_tombstone` stay inside the digest: both are deterministic for identical input and both
are part of what the plan was computed against.
"""

from __future__ import annotations

import hashlib
from operator import itemgetter
from typing import TYPE_CHECKING, Any

from infrahub_sync.cache.parquet_io import read_table
from infrahub_sync.plan.canonical import canonical_json_bytes
from infrahub_sync.plan.models import CHECKSUM_EXCLUDED_FIELDS

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

# Dropped from the digest because the engine stamps it per run, not because it is
# internal — `_source_id` and `_tombstone` are internal too and stay in (AD037, PD-008).
SNAPSHOT_DIGEST_EXCLUDED_COLUMNS = frozenset({"_extract_ts"})

# The side the plan is bound to. FR-004 binds the plan to the source snapshot; the
# destination side (`B/`) is not what the plan is bound to.
SOURCE_SNAPSHOT_SIDE = "A"


def compute_plan_checksum(manifest_mapping: Mapping[str, Any], operations_bytes: bytes) -> str:
    """Compute `plan_checksum` over a manifest mapping and the operations file's bytes.

    The manifest's `plan_checksum`, `run_id` and `created_at` keys are removed — not
    blanked — before canonicalization, and the canonical manifest bytes are concatenated
    with `operations_bytes` with no separator (AD035). Returns lowercase hex, no prefix.
    """
    body = {key: value for key, value in manifest_mapping.items() if key not in CHECKSUM_EXCLUDED_FIELDS}
    return hashlib.sha256(canonical_json_bytes(body) + operations_bytes).hexdigest()


def _digest_and_row_count(path: Path) -> tuple[str, int]:
    """Return the logical-row digest and the row count of one snapshot file."""
    table = read_table(str(path))
    columns = [name for name in table.column_names if name not in SNAPSHOT_DIGEST_EXCLUDED_COLUMNS]
    rows = table.select(columns).to_pylist()
    joined = b"\n".join(canonical_json_bytes(row, kind=path.stem) for row in rows)
    return hashlib.sha256(joined).hexdigest(), table.num_rows


def source_snapshot_digest(path: Path) -> str:
    """Digest one source-snapshot Parquet file's logical rows (AD037, PD-008).

    The table is read with `_extract_ts` dropped, rows kept in file order, each row
    encoded with `canonical_json_bytes` and the encodings joined by LF. Returns lowercase
    hex, no prefix.
    """
    digest, _row_count = _digest_and_row_count(path)
    return digest


def source_snapshot_records(run_dir: Path) -> list[dict[str, Any]]:
    """Record every source-side snapshot the plan was computed against (FR-004, FR-010).

    One `{"path", "digest", "row_count"}` mapping per `A/<resource>.parquet` file, `path`
    run-relative and POSIX, the list ordered by `path`. The mappings are what
    `PlanManifest.source_snapshot` validates into `SourceSnapshotRecord` instances. A run
    directory with no source side yields an empty list, which is a legitimate manifest
    value.
    """
    side_dir = run_dir / SOURCE_SNAPSHOT_SIDE
    if not side_dir.is_dir():
        return []
    records: list[dict[str, Any]] = []
    for path in sorted(side_dir.glob("*.parquet")):
        digest, row_count = _digest_and_row_count(path)
        records.append(
            {
                "path": path.relative_to(run_dir).as_posix(),
                "digest": digest,
                "row_count": row_count,
            }
        )
    return sorted(records, key=itemgetter("path"))
