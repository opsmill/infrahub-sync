"""The plan checksum and the source-snapshot binding (FR-004, FR-010, FR-027.8).

`compute_plan_checksum` covers the canonical manifest **minus** the three fields
`CHECKSUM_EXCLUDED_FIELDS` names, concatenated with the raw bytes of
`operations.jsonl` and **no separator** between the two (AD035). The three fields are
*removed* from the mapping before canonicalization, never blanked: a manifest carrying
`run_id` with a real value hashes as though the key were absent, and **not** as though it
were `null`. (A manifest that already carries them as `null` hashes the same as one that
omits them, because the filter is by name — the removed/blanked distinction is only
observable on a manifest whose excluded fields carry real values.)

`snapshot_digest_and_row_count` digests a snapshot's **logical rows** — the Parquet table with
the engine-injected `_extract_ts` column dropped — and not the file's bytes (AD037,
PD-008). `_extract_ts` is allocated once per side per run
(`infrahub_sync/potenda/__init__.py:177`, stored per side at `:182`) and injected into every row
(`infrahub_sync/cache/parquet_io.py:126`), so a raw-bytes digest would differ on every
re-plan of an unchanged source and make SC-006 unachievable. `_source_id` and
`_tombstone` stay inside the digest: both are deterministic for identical input and both
are part of what the plan was computed against.

The digest is defined over the whole row sequence and **computed one bounded batch at a
time**, so its cost in memory is a batch rather than a multiple of the dataset (FIX-014).
The definition is what the artifact contract fixes; the batch size is a tuning knob, and
every batch size — including one row — yields the same digest.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Any

from pyarrow import ArrowInvalid

from infrahub_sync.cache.parquet_io import iter_row_batches
from infrahub_sync.plan.canonical import canonical_json_bytes
from infrahub_sync.plan.errors import PlanArtifactUnreadableError
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

# Written between two logical rows' canonical encodings and nowhere else — no trailing
# separator, so the digested bytes are the LF-**joined** encodings the rule states.
ROW_SEPARATOR = b"\n"

# How many rows the snapshot digest holds in memory at once. The digest is defined over the
# whole row sequence but computed incrementally, so this is a memory bound and not part of
# the artifact contract: every batch size yields the same digest (FIX-014, spec 002).
SNAPSHOT_DIGEST_BATCH_SIZE = 10_000


def compute_plan_checksum(manifest_mapping: Mapping[str, Any], operations_bytes: bytes) -> str:
    """Compute `plan_checksum` over a manifest mapping and the operations file's bytes.

    The manifest's `plan_checksum`, `run_id` and `created_at` keys are removed — not
    blanked — before canonicalization, and the canonical manifest bytes are concatenated
    with `operations_bytes` with no separator (AD035). Returns lowercase hex, no prefix.
    """
    body = {key: value for key, value in manifest_mapping.items() if key not in CHECKSUM_EXCLUDED_FIELDS}
    return hashlib.sha256(canonical_json_bytes(body) + operations_bytes).hexdigest()


def snapshot_digest_and_row_count(path: Path, *, batch_size: int = SNAPSHOT_DIGEST_BATCH_SIZE) -> tuple[str, int]:
    """Return one snapshot file's logical-row digest and its row count together.

    The pre-apply verifier compares **both** against the manifest, and reading the Parquet
    file twice to get them separately would double the cost of the check for no benefit.

    The file is read with `_extract_ts` projected out, rows kept in file order, each row
    encoded with `canonical_json_bytes` and the encodings joined by LF (AD037, PD-008).
    The digest is lowercase hex, no prefix.

    Hashed **incrementally**, from one open of the file, one bounded record batch at a
    time. SHA-256 is streamable, and the previous shape — the whole decompressed table,
    then a list of every row as a dict, then every row's canonical encoding, then the
    joined byte string — made peak memory a multiple of the dataset and put an operational
    ceiling on the kinds a plan can be created for at all (FIX-014, spec 002).

    The digest is **unchanged** by that: the hash is fed exactly the bytes the join used to
    allocate — each logical row's canonical encoding in file order, a single LF between two
    of them and none at either end — and the row count is accumulated over the same pass
    rather than read from a second one.
    """
    digest = hashlib.sha256()
    row_count = 0
    for batch in iter_row_batches(
        str(path),
        batch_size=batch_size,
        excluded_columns=SNAPSHOT_DIGEST_EXCLUDED_COLUMNS,
    ):
        for row in batch:
            if row_count:
                digest.update(ROW_SEPARATOR)
            digest.update(canonical_json_bytes(row, kind=path.stem))
            row_count += 1
    return digest.hexdigest(), row_count


def source_snapshot_records(run_dir: Path) -> list[dict[str, Any]]:
    """Record every source-side snapshot the plan was computed against (FR-004, FR-010).

    One `{"path", "digest", "row_count"}` mapping per `A/<resource>.parquet` file, `path`
    run-relative and POSIX, the list ordered by `path` — which is the glob's sorted order,
    since every `path` is derived from it. The mappings are what
    `PlanManifest.source_snapshot` validates into `SourceSnapshotRecord` instances. A run
    directory with no source side yields an empty list, which is a legitimate manifest
    value.
    """
    side_dir = run_dir / SOURCE_SNAPSHOT_SIDE
    if not side_dir.is_dir():
        return []
    records: list[dict[str, Any]] = []
    for path in sorted(side_dir.glob("*.parquet")):
        try:
            digest, row_count = snapshot_digest_and_row_count(path)
        except ArrowInvalid as exc:
            # A snapshot this run cannot digest cannot be bound to the plan, so the plan
            # write fails with the taxonomy's message rather than a raw pyarrow traceback
            # (AD059). Same classification the verifier gives the condition at apply time.
            msg = (
                f"The source snapshot at {str(path)!r} could not be digested for the plan "
                f"manifest: its bytes are not a readable Parquet table ({exc})."
            )
            raise PlanArtifactUnreadableError(
                msg,
                next_action="Re-run `diff` for this sync to rebuild the snapshot and its plan artifact.",
            ) from exc
        except OSError as exc:
            # `glob` listed the path, so the file was there: this is removed-between-listing-
            # and-open, or stat-allowed/read-denied. FIX-003 asked for the same treatment here
            # that the verifier already gives the condition at apply time; without this arm a
            # read-denied snapshot escaped `diff` as a raw `PermissionError` from what is a
            # designed failure path (AD059).
            #
            # It keeps the class-level next action — check permissions and ownership — rather
            # than the re-plan one above. Unreadable is a different condition from corrupt
            # bytes and has a different remedy (AD036): re-running `diff` would meet the same
            # denial, so telling the operator to do that would loop them.
            msg = (
                f"The source snapshot at {str(path)!r} exists but could not be read for the plan "
                f"manifest: {exc.strerror or exc}."
            )
            raise PlanArtifactUnreadableError(msg) from exc
        records.append(
            {
                "path": path.relative_to(run_dir).as_posix(),
                "digest": digest,
                "row_count": row_count,
            }
        )
    return records
