"""T024 — the plan reader's classification (FR-010, FR-017, FR-019, FR-027, SC-011, SC-018).

A **pure unit test of the classification**, with no apply and no destination in scope. Phase
C adds no apply path and no destination exists yet, so no test in this file asserts a
destination write count or a run-sidecar state: both halves would be asserted against a stub.
The zero-writes and `failed`-run-state halves of SC-011 and SC-018 are T065's, on the Phase F
CLI apply path where an apply actually exists.

What the classification has to keep apart, and what each case here pins:

- **SC-011**: a run with no `plan/` at all is the pre-existing row format, and the message
  sends the operator to re-plan.
- **torn**, in its several shapes, each naming *which* part is torn — including the shape
  most likely in practice, an operations line that parses as JSON but fails record
  validation, which must arrive as a line number and a field rather than a raw pydantic
  traceback (AD059).
- **SC-018**: an unsupported `format_version` names the version found, lists the versions
  supported, and reads **differently** from the SC-011 message, because the remedies differ.
- **AD055**: an `action` outside `ACTIONS` is the genuinely-unsupported class, refused while
  reading. A recorded `delete` is a *valid* action that never reaches that path, so a
  delete-bearing fixture is asserted to read cleanly in the same file — the two classes
  cannot be conflated by accident.
"""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING

import pytest

from infrahub_sync.plan.errors import (
    PlanArtifactTornError,
    PlanArtifactUnreadableError,
    PlanFormatV1Error,
    PlanFormatVersionError,
    UnsupportedOperationActionError,
)
from infrahub_sync.plan.models import ACTIONS, SUPPORTED_FORMAT_VERSIONS
from infrahub_sync.plan.reader import load_plan_artifact
from tests.plan.artifact_fixtures import (
    RUN_ID,
    encode_operations,
    manifest_path,
    operation_record,
    operations_path,
    write_artifact,
)

if TYPE_CHECKING:
    from pathlib import Path

# A `format_version` no release of this tool has written. SC-018's fixture value.
UNSUPPORTED_FORMAT_VERSION = 99

# `chmod 0o000` does not stop root from reading, so the unreadable case would silently
# succeed for the wrong reason under a root test runner.
RUNNING_AS_ROOT = hasattr(os, "geteuid") and os.geteuid() == 0

# Markers of an unhandled pydantic failure reaching the operator. AD059 forbids it: a
# traceback carries no next action.
PYDANTIC_TRACEBACK_MARKERS = ("ValidationError", "Traceback (most recent call last)", "further information visit")


def _run_dir(tmp_path: Path) -> Path:
    """A run directory named as a real run identifier, since the messages quote it."""
    directory = tmp_path / RUN_ID
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _v1_run_dir(tmp_path: Path) -> Path:
    """A run directory in the pre-existing row format: `plan.parquet`, no `plan/` (V19)."""
    directory = _run_dir(tmp_path)
    (directory / "plan.parquet").write_bytes(b"PAR1-not-really-parquet")
    return directory


# ======================================================================================
# SC-011 (reader half) — the pre-existing format
# ======================================================================================


def test_a_v1_run_directory_is_rejected_as_the_pre_existing_format(tmp_path: Path) -> None:
    """No `plan/` at all is v1, and the message directs the operator to re-plan (FR-019)."""
    directory = _v1_run_dir(tmp_path)

    with pytest.raises(PlanFormatV1Error) as raised:
        load_plan_artifact(directory)

    message = str(raised.value)
    assert RUN_ID in message
    assert str(directory / "plan") in message
    assert "diff" in raised.value.next_action
    assert raised.value.next_action in message


def test_the_v1_verdict_does_not_depend_on_the_row_file_being_present(tmp_path: Path) -> None:
    """An empty run directory is v1 too: the verdict is the absence of `plan/`."""
    with pytest.raises(PlanFormatV1Error):
        load_plan_artifact(_run_dir(tmp_path))


# ======================================================================================
# Torn — each shape naming which part is torn (FR-010)
# ======================================================================================


def _torn_missing_operations(tmp_path: Path) -> Path:
    """Manifest present, `operations.jsonl` gone: the commit landed, the content did not."""
    directory = _run_dir(tmp_path)
    write_artifact(directory, [operation_record()])
    operations_path(directory).unlink()
    return directory


def _torn_count_mismatch(tmp_path: Path) -> Path:
    """One line on disk, five recorded — the artifact disagrees with itself."""
    directory = _run_dir(tmp_path)
    write_artifact(directory, [operation_record()], operations_count=5)
    return directory


def _torn_unparseable_manifest(tmp_path: Path) -> Path:
    """A half-written manifest, which is what an interrupted write would leave."""
    directory = _run_dir(tmp_path)
    write_artifact(directory, [operation_record()])
    manifest_path(directory).write_bytes(b'{"format_version": 2, "run_id":')
    return directory


TORN_FIXTURES = {
    "missing_operations_file": (_torn_missing_operations, "operations.jsonl"),
    "count_mismatch": (_torn_count_mismatch, "line"),
    "unparseable_manifest": (_torn_unparseable_manifest, "manifest.json"),
}


@pytest.mark.parametrize(("build", "expected_fragment"), list(TORN_FIXTURES.values()), ids=list(TORN_FIXTURES))
def test_a_torn_artifact_is_rejected_naming_which_part_is_torn(tmp_path: Path, build, expected_fragment: str) -> None:
    """Each torn shape names the run, the part that is torn, and its next action."""
    directory = build(tmp_path)

    with pytest.raises(PlanArtifactTornError) as raised:
        load_plan_artifact(directory)

    message = str(raised.value)
    assert RUN_ID in message
    assert expected_fragment in message
    assert raised.value.next_action
    assert raised.value.next_action in message


def test_the_three_torn_shapes_carry_distinct_messages(tmp_path: Path) -> None:
    """Distinct wording per shape, so "torn" is not one undifferentiated verdict."""
    messages: set[str] = set()
    for name, (build, _fragment) in TORN_FIXTURES.items():
        directory = build(tmp_path / name)
        with pytest.raises(PlanArtifactTornError) as raised:
            load_plan_artifact(directory)
        messages.add(str(raised.value))

    assert len(messages) == len(TORN_FIXTURES)


def test_a_count_mismatch_names_both_the_recorded_and_the_found_count(tmp_path: Path) -> None:
    """Expected versus found, because "the counts disagree" is not actionable on its own."""
    directory = _torn_count_mismatch(tmp_path)

    with pytest.raises(PlanArtifactTornError) as raised:
        load_plan_artifact(directory)

    message = str(raised.value)
    assert "5 operation line(s)" in message
    assert "1 operation line(s)" in message


# ======================================================================================
# Torn — an operations line that parses as JSON but fails record validation (AD059)
# ======================================================================================


def test_a_create_with_no_payload_is_torn_naming_the_line_number_and_the_field(tmp_path: Path) -> None:
    """The likeliest corruption class in practice, and the one AD059 exists for.

    The record parses as JSON, so nothing structural catches it; only the record type
    refuses it. That refusal must arrive as a line number and a field with a next action —
    not as a raw pydantic traceback.
    """
    directory = _run_dir(tmp_path)
    broken = operation_record(identity={"name": "staging"})
    del broken["payload"]
    write_artifact(directory, [operation_record(), broken])

    with pytest.raises(PlanArtifactTornError) as raised:
        load_plan_artifact(directory)

    message = str(raised.value)
    assert "line 2" in message
    assert "payload" in message
    assert raised.value.next_action
    for marker in PYDANTIC_TRACEBACK_MARKERS:
        assert marker not in message, f"the operator is shown a raw pydantic failure: {marker!r}"


def test_a_stored_identifier_that_does_not_match_its_triple_is_torn(tmp_path: Path) -> None:
    """The second validation shape the contract names, so the arm is not payload-specific."""
    directory = _run_dir(tmp_path)
    broken = operation_record()
    broken["operation_id"] = "op_0000000000000000"
    write_artifact(directory, [broken])

    with pytest.raises(PlanArtifactTornError) as raised:
        load_plan_artifact(directory)

    message = str(raised.value)
    assert "line 1" in message
    assert "op_0000000000000000" in message


def test_a_line_that_is_not_json_at_all_is_torn_naming_the_line(tmp_path: Path) -> None:
    """Unparseable and invalid are both torn, and both name where."""
    directory = _run_dir(tmp_path)
    write_artifact(directory, [operation_record(), operation_record(identity={"name": "staging"})])
    operations_path(directory).write_bytes(
        encode_operations([operation_record()]) + b"{not json at all\n",
    )

    with pytest.raises(PlanArtifactTornError) as raised:
        load_plan_artifact(directory)

    assert "line 2" in str(raised.value)


# ======================================================================================
# Unreadable — never degraded to absent (AD036)
# ======================================================================================


@pytest.mark.skipif(RUNNING_AS_ROOT, reason="root reads a 0o000 file, so the case cannot be provoked")
def test_an_unreadable_manifest_names_the_path(tmp_path: Path) -> None:
    """A permission failure keeps its own class: "absent" would send the wrong remedy."""
    directory = _run_dir(tmp_path)
    write_artifact(directory, [operation_record()])
    path = manifest_path(directory)
    path.chmod(0o000)
    try:
        with pytest.raises(PlanArtifactUnreadableError) as raised:
            load_plan_artifact(directory)
    finally:
        path.chmod(0o644)

    message = str(raised.value)
    assert str(path) in message
    assert raised.value.next_action


@pytest.mark.skipif(RUNNING_AS_ROOT, reason="root reads a 0o000 directory, so the case cannot be provoked")
def test_an_unreadable_artifact_directory_is_not_reported_as_the_v1_format(tmp_path: Path) -> None:
    """The degradation AD036 names: `is_dir()` answers `False` on a permission failure."""
    directory = _run_dir(tmp_path)
    write_artifact(directory, [operation_record()])
    plan_directory = directory / "plan"
    plan_directory.chmod(0o000)
    try:
        with pytest.raises(PlanArtifactUnreadableError):
            load_plan_artifact(directory)
    finally:
        plan_directory.chmod(0o755)


# ======================================================================================
# FR-027 — unknown manifest fields survive verbatim
# ======================================================================================


def test_an_unknown_manifest_field_survives_a_read_write_round_trip(tmp_path: Path) -> None:
    """Preserved verbatim, and therefore still inside the checksummed bytes (AD028)."""
    directory = _run_dir(tmp_path)
    written = write_artifact(
        directory,
        [operation_record()],
        schema_fingerprint="a later outcome adds this",
    )

    loaded = load_plan_artifact(directory)

    assert loaded.manifest_mapping["schema_fingerprint"] == "a later outcome adds this"
    assert loaded.manifest.model_dump()["schema_fingerprint"] == "a later outcome adds this"
    # Round trip: what was read back is what was written, key for key and value for value.
    assert loaded.manifest_mapping == written
    assert loaded.manifest_mapping == json.loads(manifest_path(directory).read_text(encoding="utf-8"))


def test_the_raw_operations_bytes_are_carried_through_unchanged(tmp_path: Path) -> None:
    """The checksum is computed over these bytes, so a re-encoding here would be a defect."""
    directory = _run_dir(tmp_path)
    write_artifact(directory, [operation_record(), operation_record(identity={"name": "staging"})])

    loaded = load_plan_artifact(directory)

    assert loaded.operations_bytes == operations_path(directory).read_bytes()
    assert [operation.identity["name"] for operation in loaded.operations] == ["prod", "staging"]


# ======================================================================================
# SC-018 (reader half) — an unsupported format version
# ======================================================================================


def test_an_unsupported_format_version_names_the_version_and_lists_those_supported(tmp_path: Path) -> None:
    """The version **found** and the versions **supported**, per FR-027 and AD059."""
    directory = _run_dir(tmp_path)
    write_artifact(directory, [operation_record()], format_version=UNSUPPORTED_FORMAT_VERSION)

    with pytest.raises(PlanFormatVersionError) as raised:
        load_plan_artifact(directory)

    message = str(raised.value)
    assert str(UNSUPPORTED_FORMAT_VERSION) in message
    for supported in SUPPORTED_FORMAT_VERSIONS:
        assert str(supported) in message
    assert raised.value.next_action


def test_the_version_message_differs_from_the_pre_existing_format_message(tmp_path: Path) -> None:
    """SC-018's own clause: the two remedies differ, so the two messages must differ.

    Asserted as neither message containing the other, not merely as inequality — a version
    message that appended a sentence to the v1 one would satisfy `!=` while still telling
    the operator the artifact predates the format.
    """
    versioned = _run_dir(tmp_path / "versioned")
    write_artifact(versioned, [operation_record()], format_version=UNSUPPORTED_FORMAT_VERSION)

    with pytest.raises(PlanFormatVersionError) as version_raised:
        load_plan_artifact(versioned)
    with pytest.raises(PlanFormatV1Error) as v1_raised:
        load_plan_artifact(_v1_run_dir(tmp_path / "v1"))

    version_message, v1_message = str(version_raised.value), str(v1_raised.value)
    assert version_message != v1_message
    assert version_message not in v1_message
    assert v1_message not in version_message


def test_a_manifest_declaring_no_format_version_is_torn_rather_than_version_refused(tmp_path: Path) -> None:
    """Incomplete is not forward-dated: an absent field is a tear, not a newer writer."""
    directory = _run_dir(tmp_path)
    manifest = write_artifact(directory, [operation_record()])
    del manifest["format_version"]
    manifest_path(directory).write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(PlanArtifactTornError):
        load_plan_artifact(directory)


# ======================================================================================
# AD055 — the genuinely unsupported action, and the delete that is not one
# ======================================================================================


def test_an_unrecognized_action_is_refused_naming_the_operation_and_listing_the_actions(tmp_path: Path) -> None:
    """FR-017's genuinely-unsupported class, refused **while reading** (AD055).

    Refusing here is what puts it before any destination write. This file cannot assert that
    consequence — no apply exists in this phase — so it asserts the refusal's content, and
    T065 asserts the zero writes on the CLI apply path.
    """
    directory = _run_dir(tmp_path)
    purge = operation_record(action="purge")
    write_artifact(directory, [purge])

    with pytest.raises(UnsupportedOperationActionError) as raised:
        load_plan_artifact(directory)

    message = str(raised.value)
    assert purge["operation_id"] in message
    assert "purge" in message
    for action in ACTIONS:
        assert action in message
    assert raised.value.next_action


def test_a_delete_bearing_plan_reads_cleanly(tmp_path: Path) -> None:
    """A recorded `delete` is a **valid** action and never reaches the refusal path."""
    directory = _run_dir(tmp_path)
    write_artifact(directory, [operation_record(action="delete", identity={"name": "retired"})])

    loaded = load_plan_artifact(directory)

    assert [operation.action for operation in loaded.operations] == ["delete"]
    assert loaded.operations[0].payload is None


def test_the_unrecognized_action_wording_is_not_the_delete_wording(tmp_path: Path) -> None:
    """The two classes must not be conflated: one is refused, the other is applied-around.

    A delete is skipped at apply and disclosed; an unrecognized action stops the read. So the
    refusal must not read as the skipped-delete disclosure, or an operator will treat a
    corrupt artifact as this release's known limitation.
    """
    directory = _run_dir(tmp_path)
    write_artifact(directory, [operation_record(action="purge")])

    with pytest.raises(UnsupportedOperationActionError) as raised:
        load_plan_artifact(directory)

    message = str(raised.value).lower()
    assert "skipped" not in message
    assert "not executed" not in message
    assert "re-plan" in message


def test_an_unrecognized_action_takes_precedence_over_a_generic_tear(tmp_path: Path) -> None:
    """A hand-edited artifact reports its action, not a generic validation tear (T020).

    The record below is invalid twice over — an unrecognized action *and* a missing payload —
    and the action verdict is the one that must surface.
    """
    directory = _run_dir(tmp_path)
    doubly_broken = operation_record(action="purge")
    del doubly_broken["payload"]
    write_artifact(directory, [doubly_broken])

    with pytest.raises(UnsupportedOperationActionError):
        load_plan_artifact(directory)
