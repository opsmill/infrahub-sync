"""`Potenda.apply_plan` applies the saved plan artifact (FR-012, FR-016, FR-020, FR-023).

The engine's own contract over the write surface: the plan is read from `<run_dir>/plan/`,
verified as one pre-write gate, and executed in **stored order** through the destination's
`apply_planned_operation`. The deep behavioural matrix — peer resolution, the replace-set
flush, rendered-mutation conformance — belongs to `tests/adapters/test_infrahub_planned_write.py`
and `tests/plan/test_apply_conformance.py`.

The destination double is a plain recording object rather than a `MagicMock`, deliberately: a
mock answers every attribute lookup, so it satisfies the write-surface protocol's presence
check for free and the missing-surface case this file has to be able to fail on cannot be
expressed against one.
"""

from __future__ import annotations

import json
import logging
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest
from infrahub_sdk.exceptions import AuthenticationError, GraphQLError, ServerNotResponsiveError

from infrahub_sync.plan.errors import (
    ApplyRecordInvariantError,
    ConvergenceIdentityError,
    OperationApplyFailedError,
    PeerNotFoundError,
    PlanArtifactTornError,
    PlanFormatV1Error,
    PlanVerificationError,
    UnsupportedOperationActionError,
)
from infrahub_sync.plan.models import ACTIONS
from infrahub_sync.plan.reader import parse_plan_artifact
from infrahub_sync.plan.verify import GATED_CHECKS
from infrahub_sync.potenda import Potenda
from tests.plan.artifact_fixtures import CONFIG_VERSION, operation_record, write_artifact

if TYPE_CHECKING:
    from pathlib import Path

    from infrahub_sync.plan.models import PlannedOperation

RUN_ID = "20260727T0915-4c1ab390"


class RecordingDestination:
    """A destination that implements the planned-write surface and records every dispatch.

    Both of the protocol's members, because the pre-write gate is an `isinstance` check
    against it and a destination missing either one is refused (AD086).
    """

    def __init__(self) -> None:
        self.dispatched: list[str] = []

    def new_peer_resolver(self) -> object:  # noqa: PLR6301
        """The per-apply resolver factory; nothing below this double's surface reads it."""
        return object()

    def apply_planned_operation(self, *, operation: PlannedOperation, peers: Any) -> str:  # noqa: ANN401
        _ = peers
        self.dispatched.append(operation.operation_id)
        return f"node-{len(self.dispatched)}"


class SurfacelessDestination:
    """A destination with no planned-write surface at all — the FR-023 refusal case."""


def _potenda(run_directory: Path, destination: object) -> Potenda:
    """Build a Potenda over `run_directory` with no configuration and no source load."""
    return Potenda(
        source=SimpleNamespace(top_level=[]),  # ty: ignore[invalid-argument-type]
        destination=destination,  # ty: ignore[invalid-argument-type]
        config=None,  # ty: ignore[invalid-argument-type]
        top_level=["BuiltinTag"],
        run_dir=run_directory,
        run_id=RUN_ID,
    )


def _run_dir(tmp_path: Path) -> Path:
    directory = tmp_path / RUN_ID
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def test_apply_plan_executes_the_stored_operations_in_stored_order(tmp_path: Path) -> None:
    """Stored order is executed exactly — no re-sorting and no recomputation (FR-012).

    **SC-001's local half only.** That criterion is evidenced by an apply against a live
    destination showing no comparison-engine call on the apply path; what this case measures is
    the dispatch order the engine hands to an in-memory double, which is a precondition for the
    criterion and not the criterion. The live half is
    `tests/integration/test_saved_plan_apply_integration.py`.

    The three operations are written in an order that is **not** their sorted order, so an
    implementation that sorted the operations it read — by identifier, kind or action —
    would dispatch them in a different sequence and fail here rather than passing by
    coincidence.
    """
    directory = _run_dir(tmp_path)
    records = [
        operation_record(identity={"name": "zulu"}),
        operation_record(action="update", identity={"name": "alpha"}),
        operation_record(identity={"name": "mike"}),
    ]
    write_artifact(directory, records, run_id=RUN_ID, source_snapshot=[])
    stored_order = [str(record["operation_id"]) for record in records]
    assert stored_order != sorted(stored_order), "the fixture must not already be in sorted order"

    destination = RecordingDestination()
    record = _potenda(directory, destination).apply_plan(config_version=CONFIG_VERSION)

    assert destination.dispatched == stored_order
    assert record.applied_operations == tuple(stored_order)
    assert record.skipped_delete_operations == ()
    assert record.skipped_delete_count == 0
    # FR-025's last-applied pointer is the final element, not a separate field.
    assert record.applied_operations[-1] == stored_order[-1]


SENTINEL_RUN_FILE = '{"sentinel": "the CLI wrote this"}'


def test_apply_plan_writes_no_run_file(tmp_path: Path) -> None:
    """The engine returns the record and writes nothing — the CLI is the single writer (AD069)."""
    directory = _run_dir(tmp_path)
    write_artifact(directory, [operation_record()], run_id=RUN_ID, source_snapshot=[])
    run_file = directory / "run.json"
    run_file.write_text(SENTINEL_RUN_FILE, encoding="utf-8")

    record = _potenda(directory, RecordingDestination()).apply_plan(config_version=CONFIG_VERSION)

    assert run_file.read_text(encoding="utf-8") == SENTINEL_RUN_FILE
    assert record.as_summary_keys() == {
        "applied_operations": list(record.applied_operations),
        "skipped_delete_operations": [],
        "skipped_delete_count": 0,
        "failed_operation": None,
        "may_have_partially_written": False,
    }


def test_a_destination_without_the_write_surface_is_refused_before_any_write(tmp_path: Path) -> None:
    """FR-023: the missing surface is refused in the pre-write gate, naming the adapter (AD058)."""
    directory = _run_dir(tmp_path)
    write_artifact(directory, [operation_record()], run_id=RUN_ID, source_snapshot=[])

    with pytest.raises(PlanVerificationError) as caught:
        _potenda(directory, SurfacelessDestination()).apply_plan(config_version=CONFIG_VERSION)

    message = str(caught.value)
    assert "write_surface" in message
    assert "SurfacelessDestination" in message
    assert "infrahub-sync sync" in message


def test_a_recorded_delete_is_collected_and_never_dispatched(tmp_path: Path, caplog) -> None:
    """A delete-bearing plan applies its non-deletes and ends with the skip recorded (AD055)."""
    directory = _run_dir(tmp_path)
    create = operation_record(identity={"name": "prod"})
    delete = operation_record(action="delete", identity={"name": "retired"})
    write_artifact(directory, [create, delete], run_id=RUN_ID, source_snapshot=[])

    destination = RecordingDestination()
    with caplog.at_level(logging.DEBUG, logger="infrahub_sync.potenda"):
        record = _potenda(directory, destination).apply_plan(config_version=CONFIG_VERSION)

    assert destination.dispatched == [create["operation_id"]]
    assert record.applied_operations == (create["operation_id"],)
    assert record.skipped_delete_operations == (delete["operation_id"],)
    assert record.skipped_delete_count == 1
    # The knowability invariant, as a value rather than an inference (DBR-016).
    assert set(record.applied_operations) | set(record.skipped_delete_operations) == {
        create["operation_id"],
        delete["operation_id"],
    }

    warnings = [entry for entry in caplog.records if entry.levelno >= logging.WARNING]
    assert len(warnings) == 1
    assert "1" in warnings[0].getMessage()


def test_an_action_outside_the_vocabulary_fails_before_any_dispatch(tmp_path: Path) -> None:
    """FR-017: an operation this release cannot interpret is refused while the plan is read."""
    directory = _run_dir(tmp_path)
    records = [operation_record(identity={"name": "prod"}), operation_record(action="purge", identity={"name": "old"})]
    write_artifact(directory, records, run_id=RUN_ID, source_snapshot=[])

    destination = RecordingDestination()
    with pytest.raises(UnsupportedOperationActionError) as caught:
        _potenda(directory, destination).apply_plan(config_version=CONFIG_VERSION)

    assert destination.dispatched == [], "Nothing is dispatched: the refusal precedes the first write."
    message = str(caught.value)
    assert str(records[1]["operation_id"]) in message
    assert "purge" in message
    for action in ACTIONS:
        assert action in message, f"The refusal must list the recognized vocabulary; {action!r} is absent."
    assert "Next action:" in message


def test_a_changed_configuration_version_refuses_the_apply(tmp_path: Path) -> None:
    """FR-011: the comparison value reaches the verifier, and is compared for equality only."""
    directory = _run_dir(tmp_path)
    write_artifact(directory, [operation_record()], run_id=RUN_ID, source_snapshot=[])

    destination = RecordingDestination()
    with pytest.raises(PlanVerificationError) as caught:
        _potenda(directory, destination).apply_plan(config_version="a-different-configuration-version")

    assert "config_version" in str(caught.value)
    assert destination.dispatched == []


def _recorded_checksum(run_directory: Path) -> str:
    """The `plan_checksum` the stored manifest records, read from disk."""
    recorded = json.loads((run_directory / "plan" / "manifest.json").read_text(encoding="utf-8"))
    return str(recorded["plan_checksum"])


def test_an_approved_checksum_matching_the_artifact_applies(tmp_path: Path) -> None:
    """The approval is answered here, so the matching case has to pass here too."""
    directory = _run_dir(tmp_path)
    record_written = operation_record()
    write_artifact(directory, [record_written], run_id=RUN_ID, source_snapshot=[])

    destination = RecordingDestination()
    applied = _potenda(directory, destination).apply_plan(
        config_version=CONFIG_VERSION, expected_checksum=_recorded_checksum(directory)
    )

    assert destination.dispatched == [record_written["operation_id"]]
    assert applied.applied_operations == (record_written["operation_id"],)


def test_an_unapproved_artifact_is_refused_by_the_engine_before_any_dispatch(tmp_path: Path) -> None:
    """The authoritative approval comparison: it is made where the applied bytes are read.

    The command's own check reads the artifact before the destination exists and therefore
    before the apply consumes it, so only this comparison can bind an approval to what is
    written. It refuses after the verification gate — a torn artifact still gets FR-009's
    evaluate-all disclosure — and before the parse, so nothing is dispatched.
    """
    directory = _run_dir(tmp_path)
    write_artifact(directory, [operation_record()], run_id=RUN_ID, source_snapshot=[])

    destination = RecordingDestination()
    with pytest.raises(PlanVerificationError) as caught:
        _potenda(directory, destination).apply_plan(config_version=CONFIG_VERSION, expected_checksum="0" * 64)

    assert destination.dispatched == [], "nothing may be dispatched for a plan no approval named"
    message = str(caught.value)
    assert "is not the plan this apply approved" in message, message
    assert _recorded_checksum(directory) in message, message
    assert "Next action:" in message, message
    assert message.count("Next action:") == 1, f"the remedy is rendered twice: {message}"


def test_an_unhashable_artifact_refuses_an_approval_rather_than_passing_it(tmp_path: Path) -> None:
    """Fail closed at the engine too: an artifact that cannot be hashed matched no approval."""
    directory = _run_dir(tmp_path)
    write_artifact(directory, [operation_record()], run_id=RUN_ID, source_snapshot=[])
    (directory / "plan" / "manifest.json").write_bytes(b'{"format_version": 2, "config_version": "\xff\xfe"}')

    destination = RecordingDestination()
    with pytest.raises(PlanVerificationError):
        _potenda(directory, destination).apply_plan(config_version=CONFIG_VERSION, expected_checksum="0" * 64)

    assert destination.dispatched == []


def test_no_configuration_and_no_supplied_version_refuses_before_any_write(tmp_path: Path) -> None:
    """`_apply_config_version`'s documented `ValueError`, which nothing else reaches (FR-011, AD013)."""
    directory = _run_dir(tmp_path)
    write_artifact(directory, [operation_record()], run_id=RUN_ID, source_snapshot=[])

    destination = RecordingDestination()
    with pytest.raises(
        ValueError, match="needs a configuration version to compare the plan artifact against"
    ) as caught:
        _potenda(directory, destination).apply_plan()

    message = str(caught.value)
    assert "construct Potenda with a parsed configuration, or pass `config_version`" in message, message
    # The named remedies must not read as advice about the *value* the caller passed: there
    # was no value. A message about printable ASCII here would be the wrong diagnosis.
    assert "printable ASCII" not in message, message
    assert destination.dispatched == []


def test_an_empty_plan_applies_as_a_successful_no_op(tmp_path: Path) -> None:
    """FR-022: zero operations is a success, and verification still runs first (AD033)."""
    directory = _run_dir(tmp_path)
    write_artifact(directory, [], run_id=RUN_ID, source_snapshot=[])

    record = _potenda(directory, RecordingDestination()).apply_plan(config_version=CONFIG_VERSION)

    assert record.applied_operations == ()
    assert record.skipped_delete_count == 0

    with pytest.raises(PlanVerificationError):
        _potenda(directory, SurfacelessDestination()).apply_plan(config_version=CONFIG_VERSION)


# ======================================================================================
# FR-009's gate disclosure and evaluate-all rule, reached from the apply path
# ======================================================================================


def test_an_identity_value_disagreement_refuses_the_apply_before_any_destination_call(tmp_path: Path) -> None:
    """The identity-value check at the apply boundary: the mismatched record never reaches the destination."""
    directory = _run_dir(tmp_path)
    mismatched = operation_record(identity={"name": "reviewed"}, payload={"name": "actually-written"})
    write_artifact(directory, [mismatched], run_id=RUN_ID, source_snapshot=[])
    destination = RecordingDestination()

    with pytest.raises(PlanArtifactTornError):
        _potenda(directory, destination).apply_plan(config_version=CONFIG_VERSION)

    assert destination.dispatched == []


def test_a_tear_and_a_config_version_mismatch_are_both_reported_by_one_apply_attempt(tmp_path: Path) -> None:
    """FR-009: once the gate passes, every failure is named in one refusal (AD036)."""
    directory = _run_dir(tmp_path)
    write_artifact(directory, [operation_record()], run_id=RUN_ID, source_snapshot=[])
    (directory / "plan" / "operations.jsonl").unlink()
    destination = RecordingDestination()

    with pytest.raises(PlanVerificationError) as caught:
        _potenda(directory, destination).apply_plan(config_version="a-different-configuration-version")

    message = str(caught.value)
    assert "torn_operations" in message
    assert "config_version" in message
    assert "2 pre-apply check(s) failed" in message
    assert destination.dispatched == []


def test_the_format_version_gate_tells_the_operator_what_it_did_not_evaluate(tmp_path: Path) -> None:
    """FR-009's gate message, reachable from an apply and not only from a direct verify call."""
    directory = _run_dir(tmp_path)
    write_artifact(directory, [operation_record()], run_id=RUN_ID, source_snapshot=[], format_version=99)
    destination = RecordingDestination()

    with pytest.raises(PlanVerificationError) as caught:
        _potenda(directory, destination).apply_plan(config_version="a-different-configuration-version")

    message = str(caught.value)
    assert "1 pre-apply check(s) failed" in message
    assert "were not evaluated" in message
    for gated in GATED_CHECKS:
        assert gated in message, f"the gate did not name {gated!r} among the checks it skipped"
    assert destination.dispatched == []


def test_a_run_holding_no_plan_artifact_keeps_its_own_verdict(tmp_path: Path) -> None:
    """FR-019's verdict is not degraded into "the manifest could not be parsed" (SC-011)."""
    directory = _run_dir(tmp_path)
    (directory / "plan.parquet").write_bytes(b"pre-existing row format")
    destination = RecordingDestination()

    with pytest.raises(PlanFormatV1Error) as caught:
        _potenda(directory, destination).apply_plan(config_version=CONFIG_VERSION)

    assert "holds no plan artifact" in str(caught.value)
    assert destination.dispatched == []


def test_an_artifact_substituted_after_verification_is_not_what_gets_applied(tmp_path: Path) -> None:
    """The bytes verified are the bytes applied (DBR-006, DBA-004) — T097's guard, extended."""
    directory = _run_dir(tmp_path)
    verified = [operation_record(identity={"name": "verified"})]
    write_artifact(directory, verified, run_id=RUN_ID, source_snapshot=[])
    substituted = [
        operation_record(identity={"name": "substituted"}),
        operation_record(action="update", identity={"name": "also-substituted"}),
    ]

    from infrahub_sync.plan.verify import verify_plan as real_verify

    def _substituting_verify(*args: Any, **kwargs: Any) -> Any:  # noqa: ANN401 — passes through either signature
        failures = real_verify(*args, **kwargs)
        # The race, at its widest: a valid, internally consistent plan replaces the
        # verified one the moment verification has passed.
        write_artifact(directory, substituted, run_id=RUN_ID, source_snapshot=[])
        return failures

    destination = RecordingDestination()
    with patch("infrahub_sync.plan.verify.verify_plan", _substituting_verify):
        record = _potenda(directory, destination).apply_plan(config_version=CONFIG_VERSION)

    assert destination.dispatched == [verified[0]["operation_id"]]
    assert record.applied_operations == (verified[0]["operation_id"],)
    assert {op["operation_id"] for op in substituted}.isdisjoint(destination.dispatched)


class PartiallyWritingDestination(RecordingDestination):
    """A destination whose second operation fails **after** issuing part of its own write.

    The in-tree shape of the problem: `apply_planned_operation` issues the base upsert and only
    then flushes the cardinality-many relationship sets, so a failure in the flush leaves the
    destination changed by an operation the engine never counted as applied. The double
    records the base write separately from the dispatch list so the case can assert the
    destination changed while the applied set does not name the operation.
    """

    def __init__(self) -> None:
        super().__init__()
        self.base_writes: list[str] = []

    def apply_planned_operation(self, *, operation: PlannedOperation, peers: Any) -> str:  # noqa: ANN401
        self.base_writes.append(operation.operation_id)
        if len(self.base_writes) > 1:
            raise GraphQLError([{"message": f"the relationship flush for {operation.operation_id!r} was rejected"}])
        return super().apply_planned_operation(operation=operation, peers=peers)


def test_a_failure_after_the_base_write_names_the_operation_and_marks_the_partial_write(tmp_path: Path) -> None:
    """The record must not imply the failing operation wrote nothing."""
    directory = _run_dir(tmp_path)
    records = [operation_record(identity={"name": "first"}), operation_record(identity={"name": "second"})]
    write_artifact(directory, records, run_id=RUN_ID, source_snapshot=[])
    destination = PartiallyWritingDestination()

    with pytest.raises(OperationApplyFailedError) as caught:
        _potenda(directory, destination).apply_plan(config_version=CONFIG_VERSION)

    failing_id = str(records[1]["operation_id"])
    assert destination.base_writes == [str(records[0]["operation_id"]), failing_id], (
        "the double must have issued the failing operation's base write"
    )
    record = caught.value.apply_record
    assert record.applied_operations == (str(records[0]["operation_id"]),)
    assert failing_id not in record.applied_operations, "a failed operation is never reported as applied"
    assert record.failed_operation == failing_id
    assert record.may_have_partially_written is True
    assert record.as_summary_keys()["failed_operation"] == failing_id

    message = str(caught.value)
    assert "stay written" in message, "the earlier operations' fate stays stated"
    assert "may itself have written part of its change" in message
    assert "re-applying" in message.lower(), "and the convergent remedy (AD033)"


class DefectiveDestination(RecordingDestination):
    """A destination whose second operation raises the defect it is constructed with.

    Not a rejection of any kind: the exception types below are what a programming mistake or an
    SDK shape change raises, and the in-tree example is the adapter's own schema-type guard,
    which raises `TypeError` when `client.schema.get` answers with something other than a
    `NodeSchemaAPI`.
    """

    def __init__(self, defect: Exception) -> None:
        super().__init__()
        self.defect = defect

    def apply_planned_operation(self, *, operation: PlannedOperation, peers: Any) -> str:  # noqa: ANN401
        if self.dispatched:
            raise self.defect
        return super().apply_planned_operation(operation=operation, peers=peers)


DESTINATION_REJECTIONS = (
    pytest.param(GraphQLError([{"message": "Object not found"}]), id="graphql-rejection"),
    pytest.param(AuthenticationError("Authentication failed: 401 Unauthorized"), id="auth-401"),
    pytest.param(ServerNotResponsiveError(url="http://localhost:8000/graphql/main", timeout=10), id="read-timeout"),
    pytest.param(
        PeerNotFoundError("Operation 'op_a' references peer kind 'LocationSite' matching no object."),
        id="plan-taxonomy-peer-miss",
    ),
    pytest.param(
        ConvergenceIdentityError("A saved identity is finer than the destination upsert key."),
        id="convergence-identity-refusal",
    ),
)


@pytest.mark.parametrize("rejection", DESTINATION_REJECTIONS)
def test_a_known_destination_failure_is_wrapped_with_the_operation_and_run_context(
    tmp_path: Path, rejection: Exception
) -> None:
    """Inside the operational boundary, a failure becomes the named taxonomy refusal."""
    directory = _run_dir(tmp_path)
    records = [operation_record(identity={"name": "first"}), operation_record(identity={"name": "second"})]
    write_artifact(directory, records, run_id=RUN_ID, source_snapshot=[])
    destination = DefectiveDestination(rejection)

    with pytest.raises(OperationApplyFailedError) as caught:
        _potenda(directory, destination).apply_plan(config_version=CONFIG_VERSION)

    message = str(caught.value)
    assert str(records[1]["operation_id"]) in message, "the refusal names the operation that failed"
    assert RUN_ID in message, "…and the run it failed in"
    if isinstance(rejection, (GraphQLError, AuthenticationError, ServerNotResponsiveError)):
        assert type(rejection).__name__ in message, "…and the underlying SDK failure category"
        assert str(rejection) not in message, "the raw SDK text must not reach normal operator output"
    else:
        assert str(rejection) in message, "the in-tree refusal must identify the affected plan object"
    assert caught.value.__cause__ is rejection, "…and chains it, so the library traceback survives"
    assert caught.value.apply_record.applied_operations == (str(records[0]["operation_id"]),)
    assert caught.value.apply_record.failed_operation == str(records[1]["operation_id"])
    assert destination.dispatched == [str(records[0]["operation_id"])], "the apply stops at the rejection"
    assert "stay written" in message, "…and says the earlier writes were kept"
    assert caught.value.next_action, "every member of the taxonomy carries a next action (AD059)"
    assert "Next action:" in message


CODE_DEFECTS = (
    pytest.param(TypeError("Expected NodeSchemaAPI for BuiltinTag, got NoneType"), id="type-error"),
    pytest.param(AttributeError("'NoneType' object has no attribute 'peers'"), id="attribute-error"),
    pytest.param(KeyError("hfid"), id="key-error-after-an-sdk-shape-change"),
)


@pytest.mark.parametrize("defect", CODE_DEFECTS)
def test_a_code_defect_escapes_the_apply_unchanged_and_still_carries_the_partial_record(
    tmp_path: Path, defect: Exception
) -> None:
    """A defect must not be presented to the operator as a destination refusal."""
    directory = _run_dir(tmp_path)
    records = [operation_record(identity={"name": "first"}), operation_record(identity={"name": "second"})]
    write_artifact(directory, records, run_id=RUN_ID, source_snapshot=[])
    destination = DefectiveDestination(defect)

    with pytest.raises(type(defect)) as caught:
        _potenda(directory, destination).apply_plan(config_version=CONFIG_VERSION)

    assert caught.value is defect, "the defect reached the caller as itself, not as a copy or a wrapper"
    carried = getattr(caught.value, "apply_record", None)
    assert carried is not None, "the defect carried no record, so the CLI cannot say what was written"
    assert carried.applied_operations == (str(records[0]["operation_id"]),)
    assert carried.failed_operation == str(records[1]["operation_id"])
    assert carried.may_have_partially_written is True


class InterruptingDestination(RecordingDestination):
    """A destination that applies the first operation and is then interrupted."""

    def apply_planned_operation(self, *, operation: PlannedOperation, peers: Any) -> str:  # noqa: ANN401
        if self.dispatched:
            raise KeyboardInterrupt
        return super().apply_planned_operation(operation=operation, peers=peers)


def test_an_interrupt_mid_apply_propagates_as_itself_and_carries_the_partial_record(tmp_path: Path) -> None:
    """Ctrl-C on a long apply keeps its own type **and** the record of what was written."""
    directory = _run_dir(tmp_path)
    records = [operation_record(identity={"name": "first"}), operation_record(identity={"name": "second"})]
    write_artifact(directory, records, run_id=RUN_ID, source_snapshot=[])
    destination = InterruptingDestination()

    with pytest.raises(KeyboardInterrupt) as caught:
        _potenda(directory, destination).apply_plan(config_version=CONFIG_VERSION)

    assert destination.dispatched == [records[0]["operation_id"]]
    partial = getattr(caught.value, "apply_record", None)
    assert partial is not None, "the interrupt carried no partial record"
    assert partial.applied_operations == (records[0]["operation_id"],)
    assert partial.skipped_delete_operations == ()
    assert partial.skipped_delete_count == 0


def test_the_invariant_error_carries_the_record_of_what_was_actually_written(tmp_path: Path) -> None:
    """AD062's safety net must not zero the record it exists to protect."""
    directory = _run_dir(tmp_path)
    records = [operation_record(identity={"name": "first"}), operation_record(identity={"name": "second"})]
    write_artifact(directory, records, run_id=RUN_ID, source_snapshot=[])
    destination = RecordingDestination()
    applied_ids = tuple(str(record["operation_id"]) for record in records)

    real_parse = parse_plan_artifact

    def _inflated_count(raw: Any, **kwargs: Any) -> Any:  # noqa: ANN401 — mirrors the parser
        loaded = real_parse(raw, **kwargs)
        loaded.manifest.operations_count += 1
        return loaded

    with (
        patch("infrahub_sync.plan.reader.parse_plan_artifact", _inflated_count),
        pytest.raises(ApplyRecordInvariantError) as caught,
    ):
        _potenda(directory, destination).apply_plan(config_version=CONFIG_VERSION)

    assert destination.dispatched == list(applied_ids)
    assert caught.value.apply_record.applied_operations == applied_ids
    assert caught.value.apply_record.skipped_delete_count == 0
