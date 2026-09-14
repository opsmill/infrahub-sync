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
from pydantic import ValidationError

from infrahub_sync import cli
from infrahub_sync.client.models import PlanOperationResource
from infrahub_sync.plan.errors import PlanArtifactTornError, PlanFormatApplyUnsupportedError
from infrahub_sync.plan.identity import operation_id
from infrahub_sync.plan.models import PLAN_FORMAT_VERSION, SUPPORTED_FORMAT_VERSIONS, PlannedOperation
from infrahub_sync.plan.reader import LoadedPlan, parse_plan_artifact, read_plan_artifact_bytes
from infrahub_sync.plan.review import read_saved_plan
from infrahub_sync.service.models import EmittedPlanResource
from tests.adapters.test_infrahub_planned_write import engine_over as planned_write_engine_over
from tests.plan.artifact_fixtures import CONFIG_VERSION, RUN_ID, SYNC_NAME, operation_record, write_artifact

if TYPE_CHECKING:
    from pathlib import Path

    from infrahub_sync.potenda import Potenda

DESTINATION_ID = "18d52a8a-7e7d-9bf5-3967-c51149d169da"


def update_record(*, destination_id: str | None = DESTINATION_ID, kind: str = "BuiltinTag") -> dict[str, Any]:
    """One update operation line, carrying a recorded destination id unless told otherwise.

    `destination_id=None` means the key is **absent**, which is the shape a format-2 line
    has and the shape a format-3 update is refused for — so it is popped rather than left to
    the fixture's own update default.
    """
    record = operation_record(action="update", kind=kind, destination_id=destination_id)
    if destination_id is None:
        record.pop("destination_id", None)
    return record


def run_dir(tmp_path: Path) -> Path:
    """The run directory the artifact fixtures write into."""
    directory = tmp_path / SYNC_NAME / RUN_ID
    directory.mkdir(parents=True, exist_ok=True)
    return directory


class CountingDestination:
    """A write surface that accepts every operation and counts them."""

    def __init__(self) -> None:
        self.applied: list[str] = []

    def new_peer_resolver(self) -> object:  # noqa: PLR6301 — the write surface declares it on the instance
        return object()

    def apply_planned_operation(self, *, operation: PlannedOperation, peers: object) -> str:
        _ = peers
        self.applied.append(operation.operation_id)
        return "written-node-1"


def engine_over(directory: Path, *, destination: object | None = None) -> Potenda:
    """A `Potenda` bound to `directory`, with no configuration and no source load.

    The engine is built by the apply suite's own constructor rather than a second one here:
    one place already carries the source/config narrowing an apply-only engine needs, and a
    duplicate would have to repeat its suppressions to say the same thing.
    """
    return planned_write_engine_over(
        directory, destination if destination is not None else RefusingDestination(), run_id=RUN_ID
    )


def parse(directory: Path) -> LoadedPlan:
    """Read and validate the artifact written at `directory`."""
    return parse_plan_artifact(read_plan_artifact_bytes(directory), run_id=RUN_ID)


def test_the_current_plan_format_is_3_and_2_stays_readable() -> None:
    """Format 3 is what `diff` writes; format 2 stays readable for review (ADR 0001)."""
    assert PLAN_FORMAT_VERSION == 3, "A new operation field is a hard format change (extra='forbid')."
    assert frozenset({2, 3}) == SUPPORTED_FORMAT_VERSIONS, "Format-2 plans stay readable and reviewable."


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


# ---------------------------------------------------------------------------------------
# The apply boundary: a format-2 plan reads and reviews, and is refused before any dispatch
# ---------------------------------------------------------------------------------------


class RecordingOwnership:
    """A write-ownership boundary that records whether it was ever asked to prove a hold.

    `before_operation` is the last thing that happens before an operation is dispatched, so
    "it was never called" is how "nothing reached the destination" is asserted rather than
    inferred from the absence of a mutation.
    """

    def __init__(self) -> None:
        self.proofs = 0

    def before_operation(self) -> None:
        self.proofs += 1

    def after_final_operation(self) -> None:
        """Grant the closing proof."""


class RefusingDestination:
    """A write surface that fails the test if the apply ever reaches it."""

    def new_peer_resolver(self) -> object:  # noqa: PLR6301 — the write surface declares it on the instance
        return object()

    def apply_planned_operation(  # noqa: PLR6301 — same
        self, *, operation: PlannedOperation, peers: object
    ) -> str:
        _ = peers
        msg = f"The apply dispatched {operation.operation_id!r}, which a format-2 refusal must prevent."
        raise AssertionError(msg)


def test_applying_a_format_2_plan_is_refused_with_a_re_plan_instruction(tmp_path: Path) -> None:
    """A format-2 update carries no recorded id, so it cannot be keyed and is not applied."""
    directory = run_dir(tmp_path)
    write_artifact(directory, [update_record(destination_id=None)], format_version=2, source_snapshot=[])

    with pytest.raises(PlanFormatApplyUnsupportedError) as excinfo:
        engine_over(directory).apply_plan(ownership=RecordingOwnership(), config_version=CONFIG_VERSION)

    message = str(excinfo.value)
    assert "2" in message, "The refusal names the version it read."
    assert "diff" in message, "The remedy is a fresh plan, and the message says so."
    assert "not touched" in message


def test_the_format_2_refusal_happens_after_the_parse_and_before_any_ownership_proof(tmp_path: Path) -> None:
    """The ordering is the safety property, so it is asserted rather than described.

    **After the parse**: a torn format-2 artifact must still report as torn, because an
    operator fixing an artifact needs to know it is broken and not merely old. **Before
    `ownership.before_operation`**, which is the last step before a dispatch — so the
    destination is provably untouched rather than presumed so.
    """
    directory = run_dir(tmp_path)
    write_artifact(directory, [update_record(destination_id=None)], format_version=2, source_snapshot=[])
    ownership = RecordingOwnership()

    with pytest.raises(PlanFormatApplyUnsupportedError):
        engine_over(directory, destination=RefusingDestination()).apply_plan(
            ownership=ownership, config_version=CONFIG_VERSION
        )

    assert ownership.proofs == 0, "No hold was proven, so no operation was dispatched."


def test_a_torn_format_2_artifact_still_reports_as_torn_rather_than_as_an_old_format(tmp_path: Path) -> None:
    """The parse runs first, so the refusal never masks a broken artifact."""
    directory = run_dir(tmp_path)
    record = update_record(destination_id=None)
    record["tier"] = -1
    write_artifact(directory, [record], format_version=2, source_snapshot=[])

    with pytest.raises(PlanArtifactTornError):
        engine_over(directory).apply_plan(ownership=RecordingOwnership(), config_version=CONFIG_VERSION)


def test_applying_a_format_3_plan_reaches_the_write(tmp_path: Path) -> None:
    """The positive arm: the refusal is about the format, not about applying at all."""
    directory = run_dir(tmp_path)
    write_artifact(directory, [update_record()], source_snapshot=[])
    ownership = RecordingOwnership()

    record = engine_over(directory, destination=CountingDestination()).apply_plan(
        ownership=ownership, config_version=CONFIG_VERSION
    )

    assert ownership.proofs == 1
    assert len(record.applied_operations) == 1


# ---------------------------------------------------------------------------------------
# The review surfaces carry the recorded id
# ---------------------------------------------------------------------------------------
#
# A reviewer approves an apply from what these two render. An id that reached the artifact
# but not the review would mean approving a write whose target was never shown.


def test_the_plan_operation_resource_carries_the_recorded_id() -> None:
    """The public review resource declares the field, so the route can return it."""
    resource = PlanOperationResource(
        operation_id="op_0123456789abcdef",
        action="update",
        kind="BuiltinTag",
        identity={"name": "prod"},
        tier=0,
        payload={"name": "prod"},
        destination_id=DESTINATION_ID,
    )

    assert resource.destination_id == DESTINATION_ID


def test_the_service_retained_review_json_keeps_the_recorded_id() -> None:
    """The bound twin the service emits is derived from the resource, so it keeps the field.

    This is the surface a retained review is read back from long after the run, so a field
    the twin dropped would be gone for good rather than merely unrendered.
    """
    emitted = EmittedPlanResource.model_validate(
        {
            "run_id": RUN_ID,
            "checksum": "a" * 64,
            "checksum_ok": True,
            "verification_notes": (),
            "summary": {
                "by_action": {"update": 1},
                "by_kind": {"BuiltinTag": 1},
                "total": 1,
                "delete_operations_computed": True,
                "deletes_not_executed": 0,
            },
            "operations": (
                {
                    "operation_id": "op_0123456789abcdef",
                    "action": "update",
                    "kind": "BuiltinTag",
                    "identity": {"name": "prod"},
                    "tier": 0,
                    "payload": {"name": "prod"},
                    "destination_id": DESTINATION_ID,
                },
            ),
        }
    )

    assert DESTINATION_ID in emitted.model_dump_json(), "The retained review JSON must carry the id."


def test_cli_plan_detail_renders_the_recorded_id_for_an_update(capsys: pytest.CaptureFixture[str]) -> None:
    """`runs plan --detail` shows which destination object an update will be applied to."""
    cli._operation_detail(
        PlanOperationResource(
            operation_id="op_0123456789abcdef",
            action="update",
            kind="BuiltinTag",
            identity={"name": "prod"},
            tier=0,
            payload={"name": "prod"},
            destination_id=DESTINATION_ID,
        )
    )

    assert DESTINATION_ID in capsys.readouterr().out


def test_cli_plan_detail_shows_no_destination_id_for_a_create(capsys: pytest.CaptureFixture[str]) -> None:
    """A create names no destination object, so the line is absent rather than empty."""
    cli._operation_detail(
        PlanOperationResource(
            operation_id="op_0123456789abcdef",
            action="create",
            kind="BuiltinTag",
            identity={"name": "prod"},
            tier=0,
            payload={"name": "prod"},
        )
    )

    assert "destination id" not in capsys.readouterr().out


# ---------------------------------------------------------------------------------------
# The same rule in process as on disk
# ---------------------------------------------------------------------------------------


def update_operation(*, destination_id: str | None = DESTINATION_ID) -> PlannedOperation:
    """One update built the way derivation builds it, in process and with no context."""
    identity = {"name": "prod"}
    return PlannedOperation(
        operation_id=operation_id("update", "BuiltinTag", identity),
        action="update",
        kind="BuiltinTag",
        identity=identity,
        tier=0,
        payload=dict(identity),
        destination_id=destination_id,
    )


def test_an_in_process_update_without_a_destination_id_is_refused_at_construction() -> None:
    """Derivation and the artifact enforce one rule, so a bad record cannot be built at all.

    An in-process record can only be the **current** format — there is no way to construct an
    older one — so the current version's rule applies with no context supplied. Without this,
    a caller could build an unkeyable update, and the refusal would arrive only later and
    somewhere else: from a reader, against a file, naming a line number.
    """
    with pytest.raises(ValidationError):
        update_operation(destination_id=None)


def test_an_in_process_update_with_an_empty_destination_id_is_refused_at_construction() -> None:
    """An empty string keys nothing, in process exactly as on disk."""
    with pytest.raises(ValidationError):
        update_operation(destination_id="")


def test_an_in_process_update_carrying_a_destination_id_is_built() -> None:
    """The positive arm: the rule refuses the unkeyable record and nothing else."""
    assert update_operation().destination_id == DESTINATION_ID


def test_an_in_process_create_still_carries_no_destination_id() -> None:
    """A create names no destination object, and building one is unaffected by the rule."""
    identity = {"name": "prod"}
    created = PlannedOperation(
        operation_id=operation_id("create", "BuiltinTag", identity),
        action="create",
        kind="BuiltinTag",
        identity=identity,
        tier=0,
        payload=dict(identity),
    )

    assert created.destination_id is None


def test_a_format_2_artifact_update_without_an_id_still_reads() -> None:
    """The reader's context still governs an artifact: a format-2 update legitimately has none.

    This is what keeps a format-2 plan reviewable. The in-process rule must not leak into it.
    """
    identity = {"name": "prod"}
    record = {
        "operation_id": operation_id("update", "BuiltinTag", identity),
        "action": "update",
        "kind": "BuiltinTag",
        "identity": identity,
        "tier": 0,
        "payload": dict(identity),
    }

    loaded = PlannedOperation.model_validate(record, context={"format_version": 2})

    assert loaded.destination_id is None


# ---------------------------------------------------------------------------------------
# A blank recorded id is not a recorded id
# ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("blank", ["", " ", "   ", "\t", "\n"], ids=["empty", "space", "spaces", "tab", "newline"])
def test_a_whitespace_only_destination_id_is_refused_in_process(blank: str) -> None:
    """A blank id is not a key, so an update carrying one cannot be applied by it.

    Only presence and non-blankness are checked here: whether a well-formed id names anything
    is the destination's answer, and it gives it through the stale-id path.
    """
    with pytest.raises(ValidationError):
        update_operation(destination_id=blank)


@pytest.mark.parametrize("blank", ["", " ", "   ", "\t"], ids=["empty", "space", "spaces", "tab"])
def test_a_whitespace_only_destination_id_is_refused_while_reading(tmp_path: Path, blank: str) -> None:
    """The reader enforces the same rule on disk as the model does in process."""
    directory = run_dir(tmp_path)
    write_artifact(directory, [update_record(destination_id=blank)])

    with pytest.raises(PlanArtifactTornError):
        parse(directory)


def test_a_valid_destination_id_is_unaffected(tmp_path: Path) -> None:
    """The rule narrows what counts as blank and nothing else."""
    directory = run_dir(tmp_path)
    write_artifact(directory, [update_record()])

    assert parse(directory).operations[0].destination_id == DESTINATION_ID


def test_a_format_2_update_is_still_readable_under_the_blank_rule(tmp_path: Path) -> None:
    """Format 2 records no id at all, which is absence rather than a blank one."""
    directory = run_dir(tmp_path)
    write_artifact(directory, [update_record(destination_id=None)], format_version=2)

    assert parse(directory).operations[0].destination_id is None
