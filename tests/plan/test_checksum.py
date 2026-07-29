"""T012 and T013 — the plan checksum and the source-snapshot binding.

T012 (FR-004, FR-027, AD035) covers `compute_plan_checksum`; T013 (FR-004, FR-010, AD037)
covers `source_snapshot_digest` / `source_snapshot_records`.

**On AD035's "removed, not blanked"** — asserting that "a manifest with `run_id: null`
hashes differently from one with the key absent" cannot fail against a correct
implementation, because the excluded fields are filtered **by name**: an input carrying
`run_id: null` and an input carrying no `run_id` key reduce to the same body and hash
identically, and a blanking implementation collapses the same way. The falsifiable form —
the one the decision actually protects — discriminates *removed* from *blanked* on a
manifest carrying a **real value**: the checksum must equal the hand-computed digest over
the manifest with the key **wholly absent**, and must differ from the one over the manifest
with the key set to `null`. That is what is asserted below.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

import pytest

from infrahub_sync.cache.parquet_io import read_table, write_resource_side
from infrahub_sync.plan.canonical import canonical_json_bytes
from infrahub_sync.plan.checksum import compute_plan_checksum, source_snapshot_digest, source_snapshot_records
from infrahub_sync.plan.errors import PlanArtifactUnreadableError
from infrahub_sync.plan.models import CHECKSUM_EXCLUDED_FIELDS

if TYPE_CHECKING:
    from pathlib import Path

OPERATIONS_BYTES = b'{"action":"create","kind":"BuiltinTag"}\n{"action":"update","kind":"BuiltinTag"}\n'

MANIFEST: dict[str, Any] = {
    "format_version": 2,
    "run_id": "20260726T1804-9f3ac210",
    "created_at": "2026-07-26T18:04:11.512034+00:00",
    "config_version": "5f2c",
    "source_snapshot": [{"path": "A/BuiltinTag.parquet", "digest": "7e10", "row_count": 12}],
    "operations_count": 2,
    "delete_operations_computed": True,
    "plan_checksum": "",
}


def _without(manifest: dict[str, Any], *keys: str) -> dict[str, Any]:
    """The manifest with `keys` **wholly absent**."""
    return {key: value for key, value in manifest.items() if key not in keys}


def _blanked(manifest: dict[str, Any], *keys: str) -> dict[str, Any]:
    """The manifest with `keys` present and set to `null` — the rejected alternative."""
    return {key: (None if key in keys else value) for key, value in manifest.items()}


def _hand_computed(manifest: dict[str, Any], operations: bytes = OPERATIONS_BYTES) -> str:
    """The digest, computed by hand from the spelled-out rule, not from the module."""
    return hashlib.sha256(canonical_json_bytes(manifest) + operations).hexdigest()


# ======================================================================================
# T012 — compute_plan_checksum
# ======================================================================================


def test_checksum_is_lowercase_hex_with_no_prefix() -> None:
    """FR-027.8: lowercase sha256 hex, sixty-four characters, no `sha256:` prefix."""
    produced = compute_plan_checksum(MANIFEST, OPERATIONS_BYTES)
    assert len(produced) == 64
    assert produced == produced.lower()
    assert all(character in "0123456789abcdef" for character in produced)


def test_excluded_fields_are_the_three_declared_ones() -> None:
    """The constant names exactly `plan_checksum`, `run_id` and `created_at`."""
    assert set(CHECKSUM_EXCLUDED_FIELDS) == {"plan_checksum", "run_id", "created_at"}


@pytest.mark.parametrize("excluded", sorted(CHECKSUM_EXCLUDED_FIELDS))
def test_excluded_field_is_removed_not_blanked(excluded: str) -> None:
    """AD035, in its falsifiable form, per excluded field.

    A manifest carrying a **real value** in the excluded field must hash as though the key
    were wholly absent, and must **not** hash as though it were set to `null`. A blanking
    implementation fails the second assertion; a removing one passes both. The other two
    excluded fields are removed on both sides of the comparison, so the field under test is
    the only thing that differs between the two references.
    """
    manifest = dict(MANIFEST)
    manifest[excluded] = "a-real-value"
    others = tuple(field for field in CHECKSUM_EXCLUDED_FIELDS if field != excluded)

    produced = compute_plan_checksum(manifest, OPERATIONS_BYTES)

    assert produced == _hand_computed(_without(manifest, *CHECKSUM_EXCLUDED_FIELDS))
    assert produced != _hand_computed(_blanked(_without(manifest, *others), excluded))


def test_all_three_excluded_fields_are_removed_together() -> None:
    """The same, for the three fields at once — the shape the writer actually calls with."""
    manifest = dict(MANIFEST)
    manifest["run_id"] = "abc"
    manifest["created_at"] = "2026-01-01T00:00:00+00:00"
    manifest["plan_checksum"] = "deadbeef"

    produced = compute_plan_checksum(manifest, OPERATIONS_BYTES)

    assert produced == _hand_computed(_without(manifest, *CHECKSUM_EXCLUDED_FIELDS))
    assert produced != _hand_computed(_blanked(manifest, *CHECKSUM_EXCLUDED_FIELDS))


@pytest.mark.parametrize("excluded", sorted(CHECKSUM_EXCLUDED_FIELDS))
def test_changing_an_excluded_field_does_not_change_the_checksum(excluded: str) -> None:
    """The three excluded fields are outside the checksummed bytes."""
    first = dict(MANIFEST, **{excluded: "value-one"})
    second = dict(MANIFEST, **{excluded: "value-two"})
    assert compute_plan_checksum(first, OPERATIONS_BYTES) == compute_plan_checksum(second, OPERATIONS_BYTES)


@pytest.mark.parametrize("excluded", sorted(CHECKSUM_EXCLUDED_FIELDS))
def test_dropping_an_excluded_field_entirely_does_not_change_the_checksum(excluded: str) -> None:
    """Absent and present-with-a-value are equivalent for an excluded field."""
    with_field = dict(MANIFEST, **{excluded: "value"})
    without_field = _without(with_field, excluded)
    assert compute_plan_checksum(with_field, OPERATIONS_BYTES) == compute_plan_checksum(without_field, OPERATIONS_BYTES)


@pytest.mark.parametrize(
    "changed",
    sorted(set(MANIFEST) - set(CHECKSUM_EXCLUDED_FIELDS)),
)
def test_changing_any_non_excluded_field_changes_the_checksum(changed: str) -> None:
    """Exactly three fields are excluded: every other declared field is inside the bytes."""
    mutated = dict(MANIFEST)
    mutated[changed] = "a-different-value-entirely"
    assert compute_plan_checksum(mutated, OPERATIONS_BYTES) != compute_plan_checksum(MANIFEST, OPERATIONS_BYTES)


def test_unknown_extra_manifest_field_changes_the_checksum() -> None:
    """A tolerated unknown field is inside the checksummed bytes (FR-027, AD028)."""
    with_extra = dict(MANIFEST, schema_fingerprint="fp-1")
    assert compute_plan_checksum(with_extra, OPERATIONS_BYTES) != compute_plan_checksum(MANIFEST, OPERATIONS_BYTES)


def test_unknown_extra_manifest_field_value_change_changes_the_checksum() -> None:
    """Not merely its presence — its value is covered too."""
    first = dict(MANIFEST, schema_fingerprint="fp-1")
    second = dict(MANIFEST, schema_fingerprint="fp-2")
    assert compute_plan_checksum(first, OPERATIONS_BYTES) != compute_plan_checksum(second, OPERATIONS_BYTES)


def test_manifest_key_order_does_not_change_the_checksum() -> None:
    """The manifest body is canonicalised, so insertion order is irrelevant."""
    reversed_manifest = dict(reversed(list(MANIFEST.items())))
    assert list(reversed_manifest) != list(MANIFEST)
    assert compute_plan_checksum(reversed_manifest, OPERATIONS_BYTES) == compute_plan_checksum(
        MANIFEST, OPERATIONS_BYTES
    )


def test_operations_bytes_are_inside_the_checksum() -> None:
    """A changed operations file changes the checksum."""
    assert compute_plan_checksum(MANIFEST, OPERATIONS_BYTES) != compute_plan_checksum(MANIFEST, OPERATIONS_BYTES + b"x")


def test_the_two_byte_sequences_are_joined_with_no_separator() -> None:
    """The concatenation is `canonical_manifest_bytes + operations_bytes`, nothing between."""
    body = canonical_json_bytes(_without(MANIFEST, *CHECKSUM_EXCLUDED_FIELDS))
    assert compute_plan_checksum(MANIFEST, OPERATIONS_BYTES) == hashlib.sha256(body + OPERATIONS_BYTES).hexdigest()


@pytest.mark.parametrize(
    "separator",
    [
        pytest.param(b"\n", id="LF"),
        pytest.param(b"\x00", id="NUL"),
        pytest.param(b" ", id="space"),
        pytest.param(b"|", id="pipe"),
    ],
)
def test_no_candidate_separator_is_present(separator: bytes) -> None:
    """Any separator at all would produce a different digest, so none is used."""
    body = canonical_json_bytes(_without(MANIFEST, *CHECKSUM_EXCLUDED_FIELDS))
    with_separator = hashlib.sha256(body + separator + OPERATIONS_BYTES).hexdigest()
    assert compute_plan_checksum(MANIFEST, OPERATIONS_BYTES) != with_separator


def test_empty_operations_bytes_still_produce_a_checksum() -> None:
    """A zero-operation plan is checksummed like any other (FR-022)."""
    empty = compute_plan_checksum(MANIFEST, b"")
    assert empty == hashlib.sha256(canonical_json_bytes(_without(MANIFEST, *CHECKSUM_EXCLUDED_FIELDS))).hexdigest()
    assert empty != compute_plan_checksum(MANIFEST, OPERATIONS_BYTES)


# ======================================================================================
# T013 — source_snapshot_digest / source_snapshot_records (AD037, PD-008)
# ======================================================================================

_TS_ONE = datetime(2026, 7, 26, 18, 4, 11, tzinfo=timezone.utc)
_TS_TWO = _TS_ONE + timedelta(days=3, seconds=17)

ROWS: list[dict[str, object]] = [
    {"name": "prod", "description": "production"},
    {"name": "staging", "description": "staging"},
    {"name": "retired", "description": None},
]
SOURCE_IDS = ["prod", "staging", "retired"]


def _write_side(  # noqa: PLR0913 — mirrors `write_resource_side`, whose parameters these are
    run_dir: Path,
    *,
    side: str = "A",
    resource: str = "BuiltinTag",
    rows: list[dict[str, object]] | None = None,
    source_ids: list[str] | None = None,
    extract_ts: datetime = _TS_ONE,
    tombstones: list[bool] | None = None,
) -> Path:
    """Write one snapshot through the engine's own writer and return its path."""
    effective_rows = ROWS if rows is None else rows
    write_resource_side(
        run_dir=run_dir,
        side=side,
        resource=resource,
        rows=effective_rows,
        source_ids=source_ids if source_ids is not None else [str(index) for index in range(len(effective_rows))],
        extract_ts=extract_ts,
        tombstones=tombstones,
    )
    return run_dir / side / f"{resource}.parquet"


def test_the_writer_really_stamps_extract_ts_per_run(tmp_path: Path) -> None:
    """Precondition for the invariance case: the two tables differ in that column only.

    Without this the invariance assertion below could pass because nothing differed.
    """
    first = _write_side(tmp_path / "one", extract_ts=_TS_ONE, source_ids=SOURCE_IDS)
    second = _write_side(tmp_path / "two", extract_ts=_TS_TWO, source_ids=SOURCE_IDS)

    left = read_table(str(first)).to_pylist()
    right = read_table(str(second)).to_pylist()

    assert [row["_extract_ts"] for row in left] != [row["_extract_ts"] for row in right]
    assert [{k: v for k, v in row.items() if k != "_extract_ts"} for row in left] == [
        {k: v for k, v in row.items() if k != "_extract_ts"} for row in right
    ]
    assert first.read_bytes() != second.read_bytes()


def test_digest_is_invariant_to_extract_ts(tmp_path: Path) -> None:
    """AD037: two tables identical but for `_extract_ts` digest **equal**.

    A raw-bytes digest fails here, and SC-006 — two consecutive plan runs byte-identical —
    would be unachievable, because `_extract_ts` is allocated once per side per run.
    """
    first = _write_side(tmp_path / "one", extract_ts=_TS_ONE, source_ids=SOURCE_IDS)
    second = _write_side(tmp_path / "two", extract_ts=_TS_TWO, source_ids=SOURCE_IDS)
    assert source_snapshot_digest(first) == source_snapshot_digest(second)


def test_digest_is_sensitive_to_source_id(tmp_path: Path) -> None:
    """`_source_id` stays inside the digest (AD037)."""
    first = _write_side(tmp_path / "one", source_ids=SOURCE_IDS)
    second = _write_side(tmp_path / "two", source_ids=["prod", "staging", "renamed"])
    assert source_snapshot_digest(first) != source_snapshot_digest(second)


def test_digest_is_sensitive_to_tombstone(tmp_path: Path) -> None:
    """`_tombstone` stays inside the digest (AD037)."""
    first = _write_side(tmp_path / "one", source_ids=SOURCE_IDS, tombstones=[False, False, False])
    second = _write_side(tmp_path / "two", source_ids=SOURCE_IDS, tombstones=[False, False, True])
    assert source_snapshot_digest(first) != source_snapshot_digest(second)


def test_digest_is_sensitive_to_row_order(tmp_path: Path) -> None:
    """Rows are digested in file order, so a permutation is a different digest."""
    first = _write_side(tmp_path / "one", rows=ROWS, source_ids=SOURCE_IDS)
    second = _write_side(
        tmp_path / "two",
        rows=[ROWS[1], ROWS[0], ROWS[2]],
        source_ids=[SOURCE_IDS[1], SOURCE_IDS[0], SOURCE_IDS[2]],
    )
    assert source_snapshot_digest(first) != source_snapshot_digest(second)


@pytest.mark.parametrize(
    "mutated_rows",
    [
        pytest.param(
            [{"name": "prod", "description": "PRODUCTION"}, ROWS[1], ROWS[2]],
            id="a string value changed",
        ),
        pytest.param(
            [{"name": "prod", "description": None}, ROWS[1], ROWS[2]],
            id="a value became null",
        ),
        pytest.param(
            [ROWS[0], ROWS[1]],
            id="a row removed",
        ),
    ],
)
def test_digest_is_sensitive_to_any_data_value(tmp_path: Path, mutated_rows: list[dict[str, object]]) -> None:
    """Any change to the logical data changes the digest."""
    baseline = _write_side(tmp_path / "base", rows=ROWS, source_ids=SOURCE_IDS)
    mutated = _write_side(
        tmp_path / "mutated",
        rows=mutated_rows,
        source_ids=SOURCE_IDS[: len(mutated_rows)],
    )
    assert source_snapshot_digest(mutated) != source_snapshot_digest(baseline)


def test_digest_matches_the_spelled_out_rule(tmp_path: Path) -> None:
    """The digest is sha256 over the LF-joined canonical encodings of the logical rows."""
    path = _write_side(tmp_path, source_ids=SOURCE_IDS)
    table = read_table(str(path))
    columns = [name for name in table.column_names if name != "_extract_ts"]
    rows = table.select(columns).to_pylist()
    expected = hashlib.sha256(b"\n".join(canonical_json_bytes(row) for row in rows)).hexdigest()
    assert source_snapshot_digest(path) == expected


def test_record_row_count_is_the_tables_row_count(tmp_path: Path) -> None:
    """`row_count` is the Parquet table's row count, not the byte or column count."""
    _write_side(tmp_path, resource="BuiltinTag", rows=ROWS, source_ids=SOURCE_IDS)
    _write_side(tmp_path, resource="LocationSite", rows=[{"name": "dc1"}], source_ids=["dc1"])

    records = {record["path"]: record["row_count"] for record in source_snapshot_records(tmp_path)}

    assert records["A/BuiltinTag.parquet"] == read_table(str(tmp_path / "A" / "BuiltinTag.parquet")).num_rows
    assert records["A/BuiltinTag.parquet"] == 3
    assert records["A/LocationSite.parquet"] == 1


def test_empty_snapshot_records_zero_rows(tmp_path: Path) -> None:
    """A zero-row snapshot is a present record with `row_count: 0`, not an absent one."""
    _write_side(tmp_path, resource="BuiltinTag", rows=[], source_ids=[])
    (record,) = source_snapshot_records(tmp_path)
    assert record["path"] == "A/BuiltinTag.parquet"
    assert record["row_count"] == 0
    assert record["digest"]


def test_records_are_ordered_by_path(tmp_path: Path) -> None:
    """The list is ordered by `path`, whatever order the files were created in."""
    for resource in ("LocationSite", "BuiltinTag", "InterfacePhysical", "DcimDevice"):
        _write_side(tmp_path, resource=resource, rows=[{"name": "x"}], source_ids=["x"])

    paths = [record["path"] for record in source_snapshot_records(tmp_path)]

    assert paths == sorted(paths)
    assert paths == [
        "A/BuiltinTag.parquet",
        "A/DcimDevice.parquet",
        "A/InterfacePhysical.parquet",
        "A/LocationSite.parquet",
    ]


def test_only_the_source_side_is_recorded(tmp_path: Path) -> None:
    """FR-004 binds the plan to the source snapshot: the `B/` side is not recorded."""
    _write_side(tmp_path, side="A", resource="BuiltinTag", rows=ROWS, source_ids=SOURCE_IDS)
    _write_side(tmp_path, side="B", resource="BuiltinTag", rows=ROWS, source_ids=SOURCE_IDS)
    _write_side(tmp_path, side="B", resource="LocationSite", rows=[{"name": "dc1"}], source_ids=["dc1"])

    records = source_snapshot_records(tmp_path)

    assert [record["path"] for record in records] == ["A/BuiltinTag.parquet"]
    assert all(record["path"].startswith("A/") for record in records)
    assert not any("B/" in record["path"] for record in records)


def test_record_paths_are_run_relative_and_posix(tmp_path: Path) -> None:
    """`path` is relative to the run directory and uses POSIX separators."""
    _write_side(tmp_path, resource="BuiltinTag", rows=ROWS, source_ids=SOURCE_IDS)
    (record,) = source_snapshot_records(tmp_path)
    assert record["path"] == "A/BuiltinTag.parquet"
    assert "\\" not in record["path"]
    assert not record["path"].startswith("/")


def test_no_source_side_yields_an_empty_list(tmp_path: Path) -> None:
    """A run directory with no `A/` side has an empty binding, not a failure."""
    assert source_snapshot_records(tmp_path) == []


def test_records_carry_exactly_the_three_declared_keys(tmp_path: Path) -> None:
    """`{path, digest, row_count}` — the shape `SourceSnapshotRecord` validates."""
    _write_side(tmp_path, resource="BuiltinTag", rows=ROWS, source_ids=SOURCE_IDS)
    (record,) = source_snapshot_records(tmp_path)
    assert set(record) == {"path", "digest", "row_count"}


def test_records_digest_matches_the_per_file_digest(tmp_path: Path) -> None:
    """The record's digest is the same function the per-file helper exposes."""
    path = _write_side(tmp_path, resource="BuiltinTag", rows=ROWS, source_ids=SOURCE_IDS)
    (record,) = source_snapshot_records(tmp_path)
    assert record["digest"] == source_snapshot_digest(path)


# ======================================================================================
# FIX-003 (spec 002) — plan-write time: a snapshot that cannot be digested
# ======================================================================================


def test_plan_write_refuses_a_corrupt_snapshot_with_the_taxonomy_error(tmp_path: Path) -> None:
    """A snapshot whose bytes are not Parquet fails the plan write with a named remedy.

    `source_snapshot_records` runs while the artifact is being written; a garbage
    `A/<resource>.parquet` there would otherwise escape as a raw `ArrowInvalid`
    traceback from what is supposed to be a designed failure path (AD059).
    """
    side = tmp_path / "A"
    side.mkdir(parents=True)
    (side / "BuiltinTag.parquet").write_bytes(b"these bytes are not a Parquet table")

    with pytest.raises(PlanArtifactUnreadableError) as raised:
        source_snapshot_records(tmp_path)

    message = str(raised.value)
    assert "BuiltinTag.parquet" in message
    assert "not a readable Parquet table" in message
    assert "Next action:" in message
    assert "Re-run `diff`" in message
