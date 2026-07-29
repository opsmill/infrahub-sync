"""T017, T018, T019 — the plan artifact writer (FR-004, FR-005, FR-010, FR-019, FR-021,
FR-022, FR-026, FR-027).

Three concerns share this file because they are three readings of the same written bytes:

- **T017** the core cases — write order, manifest-last atomicity, ordering, duplicate
  refusal, the empty plan, and the manifest's field set.
- **T018** writer-level determinism, which supports SC-006. The end-to-end criterion is
  T041, over two real plan runs; here it is over two writes of identical content.
- **T019** FR-026 at the **byte** level. T015 asserts it over the model field sets; this
  asserts it over what actually reaches disk, because a field set is not what a later
  consumer parses.

Every assertion reads the files back rather than trusting the returned manifest, except
where the two are deliberately compared.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from infrahub_sync.plan import writer
from infrahub_sync.plan.checksum import compute_plan_checksum
from infrahub_sync.plan.errors import DuplicateOperationIdError
from infrahub_sync.plan.identity import operation_id
from infrahub_sync.plan.models import (
    CHECKSUM_EXCLUDED_FIELDS,
    PLAN_FORMAT_VERSION,
    SC006_MASKED_FIELDS,
    PlanManifest,
    PlannedOperation,
    SourceSnapshotRecord,
)
from infrahub_sync.plan.writer import (
    MANIFEST_FILE_NAME,
    OPERATIONS_FILE_NAME,
    PLAN_DIR_NAME,
    write_plan_artifact,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

RUN_ID = "20260726T1804-9f3ac210"
CONFIG_VERSION = "5f2c9b1e7a4d3c8f"

# The eight fields FR-027 fixes. Nine later outcomes read this manifest, so the set is the
# contract and an addition here is a breaking change for all of them.
FR027_MANIFEST_FIELDS = frozenset(
    {
        "format_version",
        "run_id",
        "created_at",
        "config_version",
        "source_snapshot",
        "operations_count",
        "delete_operations_computed",
        "plan_checksum",
    }
)

# The permitted operation-line key sets, enumerated rather than filtered, so a key added at
# either level fails this file instead of silently reaching nine consumers (FR-026).
PERMITTED_OPERATION_KEY_SETS = frozenset(
    {
        # create / update, no relationship values
        frozenset({"operation_id", "action", "kind", "identity", "tier", "payload"}),
        # create / update carrying relationship values
        frozenset({"operation_id", "action", "kind", "identity", "tier", "payload", "relationships"}),
        # delete — no payload
        frozenset({"operation_id", "action", "kind", "identity", "tier"}),
    }
)

PERMITTED_REFERENCE_KEY_SET = frozenset({"field", "peer_kind", "cardinality", "peers"})

# Vocabulary a write-unit grouping would arrive under. Enumerating the permitted key sets is
# the assertion that actually bites; this names the thing FR-026 forbids so a reviewer
# reading a future failure knows which rule was broken.
GROUPING_VOCABULARY = ("batch", "group", "chunk", "bundle", "transaction", "write_unit", "unit_of_work")

SITE_PEER: dict[str, Any] = {"name": "dc1"}
RACK_IDENTITY: dict[str, Any] = {
    "name": "dc1-rack-a",
    "site": {"peer_kind": "LocationSite", "identity": {"name": "dc1"}},
}


def _operation(  # noqa: PLR0913 — one builder per record field keeps each case to its own concern
    *,
    action: str = "create",
    kind: str = "BuiltinTag",
    identity: dict[str, Any] | None = None,
    payload: dict[str, Any] | None = None,
    relationships: list[dict[str, Any]] | None = None,
    tier: int = 0,
) -> PlannedOperation:
    """Build a valid operation with a correctly derived identifier.

    The payload defaults to the identity so the AD042 identity-in-payload guard is satisfied
    without every case restating it.
    """
    effective_identity = {"name": "prod"} if identity is None else identity
    record: dict[str, Any] = {
        "operation_id": operation_id(action, kind, effective_identity),
        "action": action,
        "kind": kind,
        "identity": effective_identity,
        "tier": tier,
    }
    if action != "delete":
        record["payload"] = dict(effective_identity) if payload is None else payload
    if relationships is not None:
        record["relationships"] = relationships
    return PlannedOperation(**record)


def _tag(name: str, *, tier: int = 0) -> PlannedOperation:
    """One `BuiltinTag` create, named so a test can predict its identifier."""
    return _operation(identity={"name": name}, tier=tier)


def _rack_update() -> PlannedOperation:
    """The contract's worked `LocationRack` example: a reference inside the identity."""
    return _operation(
        action="update",
        kind="LocationRack",
        identity=RACK_IDENTITY,
        payload={"name": "dc1-rack-a"},
        relationships=[
            {"field": "site", "peer_kind": "LocationSite", "cardinality": "one", "peers": [SITE_PEER]},
            {
                "field": "tags",
                "peer_kind": "BuiltinTag",
                "cardinality": "many",
                "peers": [{"name": "prod"}, {"name": "rack"}],
            },
        ],
        tier=2,
    )


def _delete() -> PlannedOperation:
    """The contract's worked delete: no payload, no relationships."""
    return _operation(action="delete", identity={"name": "retired"})


def _snapshot() -> list[SourceSnapshotRecord]:
    """A two-file source-snapshot binding, ordered by path as the manifest requires."""
    return [
        SourceSnapshotRecord(path="A/BuiltinTag.parquet", digest="7e10" + "0" * 60, row_count=12),
        SourceSnapshotRecord(path="A/LocationSite.parquet", digest="cc48" + "0" * 60, row_count=6),
    ]


def _write(
    run_dir: Path,
    operations: Sequence[PlannedOperation],
    *,
    run_id: str = RUN_ID,
    deletes_computed: bool = True,
) -> PlanManifest:
    """Write an artifact into `run_dir` with the fixtures above."""
    return write_plan_artifact(
        run_dir=run_dir,
        run_id=run_id,
        config_version=CONFIG_VERSION,
        source_snapshot=_snapshot(),
        deletes_computed=deletes_computed,
        operations=operations,
    )


def _plan_dir(run_dir: Path) -> Path:
    return run_dir / PLAN_DIR_NAME


def _operations_path(run_dir: Path) -> Path:
    return _plan_dir(run_dir) / OPERATIONS_FILE_NAME


def _manifest_path(run_dir: Path) -> Path:
    return _plan_dir(run_dir) / MANIFEST_FILE_NAME


def _operation_lines(run_dir: Path) -> list[dict[str, Any]]:
    """Parse every written operation line."""
    raw = _operations_path(run_dir).read_bytes().decode("utf-8")
    return [json.loads(line) for line in raw.splitlines()]


def _mask(manifest: dict[str, Any]) -> dict[str, Any]:
    """SC-006's mask: **remove** `run_id` and `created_at`, never blank them (AD035)."""
    return {key: value for key, value in manifest.items() if key not in SC006_MASKED_FIELDS}


def _canonical(mapping: dict[str, Any]) -> bytes:
    """Re-encode a mapping canonically, so a masked comparison is still a byte comparison."""
    return json.dumps(mapping, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


# ======================================================================================
# T017 — write order and manifest-last atomicity (FR-019, AD014)
# ======================================================================================


def test_operations_are_written_first_and_the_manifest_last(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The order is observable because both writes go through one atomic helper (AD014)."""
    calls: list[str] = []
    real = writer._atomic_write_bytes

    def _recording(path: Path, payload: bytes) -> None:
        calls.append(path.name)
        real(path, payload)

    monkeypatch.setattr(writer, "_atomic_write_bytes", _recording)
    _write(tmp_path, [_tag("prod"), _rack_update()])

    assert calls == [OPERATIONS_FILE_NAME, MANIFEST_FILE_NAME]


def test_a_failure_during_the_operations_write_leaves_no_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The manifest's presence is the commit point, so a torn write must never reach it."""

    def _failing(path: Path, payload: bytes) -> None:  # noqa: ARG001 — the payload is irrelevant to the failure
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.name == OPERATIONS_FILE_NAME:
            msg = "simulated I/O failure part-way through the operations write"
            raise OSError(msg)

    monkeypatch.setattr(writer, "_atomic_write_bytes", _failing)
    with pytest.raises(OSError, match="simulated I/O failure"):
        _write(tmp_path, [_tag("prod")])

    assert not _manifest_path(tmp_path).exists()


def test_the_manifest_is_absent_until_the_operations_file_is_complete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Observed from inside the write: when the manifest lands, the operations file is whole."""
    real = writer._atomic_write_bytes
    seen_at_manifest_time: list[bytes] = []

    def _observing(path: Path, payload: bytes) -> None:
        if path.name == MANIFEST_FILE_NAME:
            seen_at_manifest_time.append(_operations_path(tmp_path).read_bytes())
        real(path, payload)

    monkeypatch.setattr(writer, "_atomic_write_bytes", _observing)
    _write(tmp_path, [_tag("prod"), _tag("staging")])

    assert seen_at_manifest_time == [_operations_path(tmp_path).read_bytes()]


def test_a_cleanup_failure_never_replaces_the_error_that_tore_the_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """MIN-025: the tmp-file cleanup is best-effort and must stay that way.

    `replace` fails (the disk-full shape that explains the torn artifact), then `unlink`
    fails too (permissions shifted underneath). The operator's error must be the replace
    failure; a cleanup `PermissionError` that superseded it would hide why the artifact
    is torn.
    """

    def _failing_replace(self: Path, target: object) -> None:  # noqa: ARG001 — the target is irrelevant to the failure
        msg = "simulated replace failure: no space left on device"
        raise OSError(msg)

    def _failing_unlink(self: Path, missing_ok: bool = False) -> None:  # noqa: ARG001, FBT001, FBT002 — mirrors Path.unlink
        msg = "simulated cleanup failure"
        raise PermissionError(msg)

    monkeypatch.setattr(Path, "replace", _failing_replace)
    monkeypatch.setattr(Path, "unlink", _failing_unlink)

    with pytest.raises(OSError, match="simulated replace failure"):
        writer._atomic_write_bytes(tmp_path / "plan" / OPERATIONS_FILE_NAME, b"payload\n")


# ======================================================================================
# T017 — ordering by tier, then identifier (AD001)
# ======================================================================================


def test_operations_are_ordered_by_tier_then_identifier(tmp_path: Path) -> None:
    """Dependency tier ascending, then operation identifier as a byte-wise comparison."""
    operations = [
        _tag("zeta", tier=3),
        _rack_update(),
        _tag("alpha", tier=3),
        _delete(),
        _tag("prod", tier=1),
    ]
    _write(tmp_path, operations)

    written = [(line["tier"], line["operation_id"]) for line in _operation_lines(tmp_path)]
    assert written == sorted(written)
    assert written == sorted((operation.tier, operation.operation_id) for operation in operations)


def test_a_lower_tier_precedes_a_lexically_smaller_identifier_in_a_higher_tier(tmp_path: Path) -> None:
    """Tier is the primary key: the identifier only breaks ties inside one tier."""
    high_tier, low_tier = _tag("a", tier=9), _tag("b", tier=0)
    # Guard the case: without this the assertion below could pass on identifier order alone.
    assert low_tier.operation_id > high_tier.operation_id

    _write(tmp_path, [high_tier, low_tier])

    assert [line["operation_id"] for line in _operation_lines(tmp_path)] == [
        low_tier.operation_id,
        high_tier.operation_id,
    ]


# ======================================================================================
# T017 — a duplicate identifier fails the plan run (FR-021)
# ======================================================================================


def test_a_duplicate_identifier_raises_naming_both_operations(tmp_path: Path) -> None:
    """The message names both operations' kind, action and identity."""
    first = _tag("prod")
    duplicate = _tag("prod")
    assert first.operation_id == duplicate.operation_id

    with pytest.raises(DuplicateOperationIdError) as raised:
        _write(tmp_path, [first, duplicate])

    message = str(raised.value)
    assert first.operation_id in message
    assert message.count("BuiltinTag") == 2
    assert message.count("create") == 2
    assert message.count("prod") >= 2
    assert raised.value.next_action


def test_a_duplicate_identifier_leaves_no_artifact_at_all(tmp_path: Path) -> None:
    """The refusal precedes the first write, so the plan run fails with nothing written."""
    with pytest.raises(DuplicateOperationIdError):
        _write(tmp_path, [_tag("prod"), _tag("prod")])

    assert not _manifest_path(tmp_path).exists()
    assert not _operations_path(tmp_path).exists()


def test_a_duplicate_across_tiers_is_still_refused(tmp_path: Path) -> None:
    """Uniqueness is over the identifier alone, so a differing tier does not excuse it."""
    with pytest.raises(DuplicateOperationIdError):
        _write(tmp_path, [_tag("prod", tier=0), _tag("prod", tier=7)])


# ======================================================================================
# T017 — the empty plan (FR-022)
# ======================================================================================


def test_an_empty_plan_writes_a_present_zero_byte_operations_file(tmp_path: Path) -> None:
    """Present and zero-byte, never absent: that is what keeps empty distinct from torn."""
    manifest = _write(tmp_path, [])

    operations_path = _operations_path(tmp_path)
    assert operations_path.exists()
    assert operations_path.read_bytes() == b""
    assert manifest.operations_count == 0
    assert json.loads(_manifest_path(tmp_path).read_text(encoding="utf-8"))["operations_count"] == 0


def test_an_empty_plan_still_writes_a_complete_manifest(tmp_path: Path) -> None:
    """An empty plan is a complete artifact, so it commits like any other."""
    manifest = _write(tmp_path, [])

    on_disk = json.loads(_manifest_path(tmp_path).read_text(encoding="utf-8"))
    assert set(on_disk) == FR027_MANIFEST_FIELDS
    assert on_disk["plan_checksum"] == manifest.plan_checksum


# ======================================================================================
# T017 — the manifest's field set (FR-027)
# ======================================================================================


def test_the_manifest_carries_exactly_the_eight_fr027_fields(tmp_path: Path) -> None:
    """Exactly the eight — no more, so an added field fails here first."""
    _write(tmp_path, [_tag("prod"), _rack_update(), _delete()])

    on_disk = json.loads(_manifest_path(tmp_path).read_text(encoding="utf-8"))
    assert set(on_disk) == FR027_MANIFEST_FIELDS


def test_the_manifest_field_values_are_what_the_caller_supplied(tmp_path: Path) -> None:
    """Each of the eight carries the value the contract says it carries."""
    operations = [_tag("prod"), _rack_update(), _delete()]
    _write(tmp_path, operations, deletes_computed=False)

    on_disk = json.loads(_manifest_path(tmp_path).read_text(encoding="utf-8"))
    assert on_disk["format_version"] == PLAN_FORMAT_VERSION
    assert on_disk["run_id"] == RUN_ID
    assert on_disk["created_at"].endswith("+00:00")
    assert on_disk["config_version"] == CONFIG_VERSION
    assert on_disk["operations_count"] == len(operations)
    assert on_disk["delete_operations_computed"] is False
    assert [record["path"] for record in on_disk["source_snapshot"]] == [
        "A/BuiltinTag.parquet",
        "A/LocationSite.parquet",
    ]


def test_the_returned_manifest_is_the_one_on_disk(tmp_path: Path) -> None:
    """The return value is not a second, hopefully-equal rendering of the manifest."""
    manifest = _write(tmp_path, [_tag("prod"), _rack_update()])

    on_disk = json.loads(_manifest_path(tmp_path).read_text(encoding="utf-8"))
    assert on_disk == json.loads(manifest.model_dump_json())


def test_the_written_checksum_recomputes_from_the_written_bytes(tmp_path: Path) -> None:
    """`plan_checksum` covers the manifest minus three fields, plus the operations bytes."""
    _write(tmp_path, [_tag("prod"), _rack_update(), _delete()])

    on_disk = json.loads(_manifest_path(tmp_path).read_text(encoding="utf-8"))
    recomputed = compute_plan_checksum(on_disk, _operations_path(tmp_path).read_bytes())
    assert on_disk["plan_checksum"] == recomputed
    assert set(CHECKSUM_EXCLUDED_FIELDS) <= set(on_disk)


def test_an_unknown_manifest_field_is_tolerated_on_read(tmp_path: Path) -> None:
    """The writer emits the eight; the manifest *type* tolerates a ninth (FR-027, AD028)."""
    _write(tmp_path, [_tag("prod")])
    on_disk = json.loads(_manifest_path(tmp_path).read_text(encoding="utf-8"))
    on_disk["schema_fingerprint"] = "a later outcome adds this"

    tolerated = PlanManifest.model_validate(on_disk)
    assert tolerated.model_dump()["schema_fingerprint"] == "a later outcome adds this"


# ======================================================================================
# T017 — the operations file's encoding
# ======================================================================================


def test_every_line_is_lf_terminated_including_the_last(tmp_path: Path) -> None:
    """LF only, and the final line is terminated too (contracts/plan-artifact-format.md)."""
    _write(tmp_path, [_tag("prod"), _rack_update(), _delete()])

    raw = _operations_path(tmp_path).read_bytes()
    assert raw.endswith(b"\n")
    assert b"\r" not in raw
    assert raw.count(b"\n") == 3


def test_a_delete_omits_payload_and_relationships(tmp_path: Path) -> None:
    """Omitted, not `null`: a delete carries neither key on the wire."""
    _write(tmp_path, [_delete()])

    (line,) = _operation_lines(tmp_path)
    assert "payload" not in line
    assert "relationships" not in line


def test_an_empty_relationships_list_is_not_emitted(tmp_path: Path) -> None:
    """The wire format admits absent and never `[]` for `relationships`.

    FR-028.2's load-bearing absent-versus-empty distinction is one level down, in a
    reference's own `peers` — which the case below keeps.
    """
    _write(tmp_path, [_operation(relationships=[])])

    (line,) = _operation_lines(tmp_path)
    assert "relationships" not in line


def test_a_deliberately_empty_peer_set_survives(tmp_path: Path) -> None:
    """`peers: []` under `cardinality: many` is a real value the replace-set write acts on."""
    _write(
        tmp_path,
        [_operation(relationships=[{"field": "tags", "peer_kind": "BuiltinTag", "cardinality": "many", "peers": []}])],
    )

    (line,) = _operation_lines(tmp_path)
    assert line["relationships"] == [{"field": "tags", "peer_kind": "BuiltinTag", "cardinality": "many", "peers": []}]


def test_a_payloads_list_valued_attribute_keeps_source_order(tmp_path: Path) -> None:
    """Canonical ordering never reaches inside a payload value (FR-005)."""
    unsorted = ["zeta", "alpha", "mu"]
    _write(tmp_path, [_operation(payload={"name": "prod", "aliases": unsorted})])

    (line,) = _operation_lines(tmp_path)
    assert line["payload"]["aliases"] == unsorted


def test_written_operations_read_back_as_valid_records(tmp_path: Path) -> None:
    """Every written line round-trips through the record type it was dumped from."""
    operations = [_tag("prod"), _rack_update(), _delete()]
    _write(tmp_path, operations)

    reread = [PlannedOperation(**line) for line in _operation_lines(tmp_path)]
    expected = sorted(operations, key=lambda operation: (operation.tier, operation.operation_id))
    assert reread == expected


# ======================================================================================
# T018 — writer-level determinism (FR-005, supporting SC-006; end-to-end is T041)
# ======================================================================================


def _two_writes(tmp_path: Path, operations: Sequence[PlannedOperation]) -> tuple[Path, Path]:
    """Write the same content into two run directories and return both."""
    first, second = tmp_path / "run-a", tmp_path / "run-b"
    _write(first, list(operations))
    _write(second, list(operations))
    return first, second


def test_writing_identical_content_twice_yields_byte_identical_operations(tmp_path: Path) -> None:
    """`operations.jsonl` is byte-identical with no masking at all."""
    first, second = _two_writes(tmp_path, [_tag("prod"), _rack_update(), _delete()])

    assert _operations_path(first).read_bytes() == _operations_path(second).read_bytes()


def test_operations_are_byte_identical_when_the_input_order_differs(tmp_path: Path) -> None:
    """The sort makes the bytes a function of the operation *set*, not of call order."""
    operations = [_tag("prod"), _rack_update(), _delete(), _tag("staging", tier=1)]
    first, second = tmp_path / "run-a", tmp_path / "run-b"
    _write(first, operations)
    _write(second, list(reversed(operations)))

    assert _operations_path(first).read_bytes() == _operations_path(second).read_bytes()


def test_the_manifest_is_byte_identical_after_removing_run_id_and_created_at(tmp_path: Path) -> None:
    """Masking is key **removal**, applied to **both** sides, before the byte comparison."""
    first, second = tmp_path / "run-a", tmp_path / "run-b"
    _write(first, [_tag("prod"), _rack_update()], run_id="20260726T1804-9f3ac210")
    _write(second, [_tag("prod"), _rack_update()], run_id="20260727T0902-1b7de004")

    left = json.loads(_manifest_path(first).read_text(encoding="utf-8"))
    right = json.loads(_manifest_path(second).read_text(encoding="utf-8"))
    # The precondition: the two runs really do differ before the mask is applied.
    assert left["run_id"] != right["run_id"]
    assert _manifest_path(first).read_bytes() != _manifest_path(second).read_bytes()

    assert _canonical(_mask(left)) == _canonical(_mask(right))


def test_the_mask_removes_the_keys_rather_than_blanking_them(tmp_path: Path) -> None:
    """A blanked key would still be present, and `null` hashes differently from absent."""
    _write(tmp_path, [_tag("prod")])
    manifest = json.loads(_manifest_path(tmp_path).read_text(encoding="utf-8"))

    masked = _mask(manifest)
    assert set(SC006_MASKED_FIELDS).isdisjoint(masked)
    assert set(masked) == FR027_MANIFEST_FIELDS - set(SC006_MASKED_FIELDS)
    assert _canonical(masked) != _canonical({**masked, "run_id": None, "created_at": None})


def test_masking_only_one_side_does_not_compare_equal(tmp_path: Path) -> None:
    """Symmetry is load-bearing: a one-sided mask compares two different shapes."""
    first, second = tmp_path / "run-a", tmp_path / "run-b"
    _write(first, [_tag("prod")], run_id="20260726T1804-9f3ac210")
    _write(second, [_tag("prod")], run_id="20260727T0902-1b7de004")

    left = json.loads(_manifest_path(first).read_text(encoding="utf-8"))
    right = json.loads(_manifest_path(second).read_text(encoding="utf-8"))
    assert _canonical(_mask(left)) != _canonical(right)


def test_the_plan_checksum_needs_no_mask_of_its_own(tmp_path: Path) -> None:
    """It is a function of the checksummed bytes alone, so it is already identical."""
    first, second = tmp_path / "run-a", tmp_path / "run-b"
    _write(first, [_tag("prod"), _delete()], run_id="20260726T1804-9f3ac210")
    _write(second, [_tag("prod"), _delete()], run_id="20260727T0902-1b7de004")

    left = json.loads(_manifest_path(first).read_text(encoding="utf-8"))
    right = json.loads(_manifest_path(second).read_text(encoding="utf-8"))
    assert left["plan_checksum"] == right["plan_checksum"]
    assert "plan_checksum" not in SC006_MASKED_FIELDS


def test_a_differing_extraction_mode_is_expected_to_differ(tmp_path: Path) -> None:
    """`delete_operations_computed` is inside the checksum and is **not** masked.

    This is SC-006's same-extraction-mode precondition, at the writer level: two runs at
    different extraction modes are *expected* to differ, which is why T041 must pin the mode
    rather than widen the mask.
    """
    first, second = tmp_path / "run-a", tmp_path / "run-b"
    _write(first, [_tag("prod")], run_id=RUN_ID, deletes_computed=True)
    _write(second, [_tag("prod")], run_id=RUN_ID, deletes_computed=False)

    left = json.loads(_manifest_path(first).read_text(encoding="utf-8"))
    right = json.loads(_manifest_path(second).read_text(encoding="utf-8"))
    assert _canonical(_mask(left)) != _canonical(_mask(right))
    assert left["plan_checksum"] != right["plan_checksum"]


# ======================================================================================
# T019 — FR-026 at the byte level
# ======================================================================================


def _all_shapes() -> list[PlannedOperation]:
    """One operation of every wire shape the format admits."""
    return [
        _tag("prod"),
        _rack_update(),
        _delete(),
        _operation(kind="LocationSite", identity={"name": "dc2"}, tier=1),
    ]


def test_no_operation_line_carries_a_key_outside_the_permitted_sets(tmp_path: Path) -> None:
    """Enumerated, not filtered: a key added to the operation record fails here."""
    _write(tmp_path, _all_shapes())

    for line in _operation_lines(tmp_path):
        assert frozenset(line) in PERMITTED_OPERATION_KEY_SETS, f"unexpected operation keys: {sorted(line)}"


def test_every_permitted_operation_shape_is_actually_exercised(tmp_path: Path) -> None:
    """Otherwise the enumeration above could pass over a subset of the shapes."""
    _write(tmp_path, _all_shapes())

    observed = {frozenset(line) for line in _operation_lines(tmp_path)}
    assert observed == PERMITTED_OPERATION_KEY_SETS


def test_no_relationship_reference_carries_a_key_outside_its_permitted_set(tmp_path: Path) -> None:
    """A grouping could equally arrive one level down, inside a reference."""
    _write(tmp_path, _all_shapes())

    references = [reference for line in _operation_lines(tmp_path) for reference in line.get("relationships", [])]
    assert references
    for reference in references:
        assert frozenset(reference) == PERMITTED_REFERENCE_KEY_SET


def test_the_manifest_carries_no_key_outside_the_permitted_set(tmp_path: Path) -> None:
    """The manifest level too: nothing there groups operations into write units."""
    _write(tmp_path, _all_shapes())

    on_disk = json.loads(_manifest_path(tmp_path).read_text(encoding="utf-8"))
    assert frozenset(on_disk) == FR027_MANIFEST_FIELDS


def test_no_written_key_reads_as_a_write_unit_grouping(tmp_path: Path) -> None:
    """Names the rule FR-026 states, so a future failure reads as the rule it broke.

    The enumerations above are what bite; this says *why* they are enumerated. Only the
    structural keys are swept — a payload attribute or an identity component is source data
    and may legitimately be called anything.
    """
    _write(tmp_path, _all_shapes())

    structural: set[str] = set(json.loads(_manifest_path(tmp_path).read_text(encoding="utf-8")))
    for line in _operation_lines(tmp_path):
        structural |= set(line)
        for reference in line.get("relationships", []):
            structural |= set(reference)

    for key in structural:
        assert not any(term in key.lower() for term in GROUPING_VOCABULARY), (
            f"key {key!r} reads as a grouping of operations into write units, which FR-026 forbids"
        )
