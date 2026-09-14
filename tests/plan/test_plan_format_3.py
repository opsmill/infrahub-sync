"""Plan format 3: the recorded destination id, and what each surface does with it.

Format 3 adds one optional operation field, `destination_id`, and makes its presence a
**format-aware** record rule: under format 3 an update must carry a non-empty id and a
create or delete must carry none, and under format 2 the field must be absent entirely.
The rule is enforced while reading, before any destination write (AD055).

Format-2 plans stay readable and reviewable — a reviewer must still be able to open a plan
written by an older version — but they are no longer applyable, because an update in a
format-2 plan carries no recorded id and so cannot be keyed. `apply` refuses such a plan
with `PlanFormatApplyUnsupportedError` and an explicit re-plan instruction.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from infrahub_sync.plan.errors import PlanArtifactTornError
from infrahub_sync.plan.models import PLAN_FORMAT_VERSION, SUPPORTED_FORMAT_VERSIONS
from infrahub_sync.plan.reader import parse_plan_artifact, read_plan_artifact_bytes
from infrahub_sync.plan.review import read_saved_plan
from tests.plan.artifact_fixtures import RUN_ID, SYNC_NAME, operation_record, write_artifact

if TYPE_CHECKING:
    from pathlib import Path

DESTINATION_ID = "18d52a8a-7e7d-9bf5-3967-c51149d169da"


def update_record(*, destination_id: str | None = DESTINATION_ID, **overrides: Any) -> dict[str, Any]:
    """One update operation line, carrying a recorded destination id unless told otherwise.

    `destination_id=None` means the key is **absent**, which is the shape a format-2 line
    has and the shape a format-3 update is refused for — so it is popped rather than left to
    the fixture's own update default.
    """
    record = operation_record(action="update", destination_id=destination_id, **overrides)
    if destination_id is None:
        record.pop("destination_id", None)
    return record


def run_dir(tmp_path: Path) -> Path:
    """The run directory the artifact fixtures write into."""
    directory = tmp_path / SYNC_NAME / RUN_ID
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def parse(directory: Path) -> Any:
    """Read and validate the artifact written at `directory`."""
    return parse_plan_artifact(read_plan_artifact_bytes(directory), run_id=RUN_ID)


def test_the_current_plan_format_is_3_and_2_stays_readable() -> None:
    """Format 3 is what `diff` writes; format 2 stays readable for review (ADR 0001)."""
    assert PLAN_FORMAT_VERSION == 3, "A new operation field is a hard format change (extra='forbid')."
    assert SUPPORTED_FORMAT_VERSIONS == frozenset({2, 3}), "Format-2 plans stay readable and reviewable."


def test_a_format_3_update_round_trips_its_destination_id(tmp_path: Path) -> None:
    """The recorded id survives the canonical encoding and comes back on the operation."""
    directory = run_dir(tmp_path)
    write_artifact(directory, [update_record()])

    loaded = parse(directory)

    assert [operation.destination_id for operation in loaded.operations] == [DESTINATION_ID]


def test_a_format_3_create_carries_no_destination_id(tmp_path: Path) -> None:
    """A create is keyed by its complete HFID, never by an id it cannot know."""
    directory = run_dir(tmp_path)
    write_artifact(directory, [operation_record(action="create")])

    loaded = parse(directory)

    assert loaded.operations[0].destination_id is None


def test_a_destination_id_on_a_create_is_refused_while_reading(tmp_path: Path) -> None:
    """A create that names a destination object is incoherent: refuse before any write."""
    directory = run_dir(tmp_path)
    record = operation_record(action="create")
    record["destination_id"] = DESTINATION_ID
    write_artifact(directory, [record])

    with pytest.raises(PlanArtifactTornError):
        parse(directory)


def test_a_destination_id_on_a_delete_is_refused_while_reading(tmp_path: Path) -> None:
    """Deletes are recorded and never executed (ADR 0004), so they key nothing."""
    directory = run_dir(tmp_path)
    record = operation_record(action="delete")
    record["destination_id"] = DESTINATION_ID
    write_artifact(directory, [record])

    with pytest.raises(PlanArtifactTornError):
        parse(directory)


def test_a_format_3_update_without_a_destination_id_is_refused_while_reading(tmp_path: Path) -> None:
    """The whole point of the bump: under format 3 an update is keyed or it is not applied."""
    directory = run_dir(tmp_path)
    write_artifact(directory, [update_record(destination_id=None)])

    with pytest.raises(PlanArtifactTornError):
        parse(directory)


def test_a_format_3_update_with_an_empty_destination_id_is_refused_while_reading(tmp_path: Path) -> None:
    """An empty string keys nothing at the destination, so it is not a recorded id."""
    directory = run_dir(tmp_path)
    write_artifact(directory, [update_record(destination_id="")])

    with pytest.raises(PlanArtifactTornError):
        parse(directory)


def test_a_format_2_operation_carrying_the_new_field_is_refused_while_reading(tmp_path: Path) -> None:
    """Format 2 does not have the field: a format-2 line carrying it is torn, not tolerated."""
    directory = run_dir(tmp_path)
    write_artifact(directory, [update_record()], format_version=2)

    with pytest.raises(PlanArtifactTornError):
        parse(directory)


def test_a_format_2_plan_containing_an_update_still_reads(tmp_path: Path) -> None:
    """Reviewability is preserved: an older plan opens, and its update carries no id."""
    directory = run_dir(tmp_path)
    write_artifact(directory, [update_record(destination_id=None)], format_version=2)

    loaded = parse(directory)

    assert loaded.operations[0].action == "update"
    assert loaded.operations[0].destination_id is None


def test_a_format_2_plan_containing_an_update_still_reviews(tmp_path: Path) -> None:
    """`runs plan` on an older plan is a read: it must keep working after the bump."""
    directory = run_dir(tmp_path)
    write_artifact(directory, [update_record(destination_id=None)], format_version=2)

    plan = read_saved_plan(sync_name=SYNC_NAME, run_id=RUN_ID, base_directory=tmp_path)

    assert [operation.action for operation in plan.operations()] == ["update"]


def test_tampering_with_a_recorded_destination_id_fails_the_plan_checksum(tmp_path: Path) -> None:
    """The id is inside the checksummed bytes, so re-pointing an update is detected."""
    directory = run_dir(tmp_path)
    write_artifact(directory, [update_record()])
    operations_file = directory / "plan" / "operations.jsonl"
    operations_file.write_bytes(operations_file.read_bytes().replace(DESTINATION_ID.encode(), b"18d52a8a-dead-beef"))

    plan = read_saved_plan(sync_name=SYNC_NAME, run_id=RUN_ID, base_directory=tmp_path)

    assert plan.checksum_ok is False, "A re-pointed update must not verify against the recorded checksum."
