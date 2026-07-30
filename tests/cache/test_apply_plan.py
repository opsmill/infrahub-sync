"""T066 — `Potenda.apply_plan` applies the saved plan artifact (FR-012, FR-016, FR-020, FR-023).

Rewritten with the v1 dispatch it used to assert. The old file wrote `plan.parquet` and
asserted that a `MagicMock` destination received one per-row call on a write surface no
adapter in the tree ever implemented — removed here as the second apply path FR-019
forbids. What replaces it is the artifact-driven apply: the plan is read from
`<run_dir>/plan/`, verified as one pre-write gate, and executed in **stored order** through
the destination's `apply_planned_operation`.

The destination double below is a plain recording object rather than a `MagicMock`, and
deliberately so: a mock answers every attribute lookup, so it satisfies the write-surface
protocol's presence check for free and the missing-write-surface case — the one this file has
to be able to fail on — cannot be expressed against one.

The deep behavioural matrix (peer resolution, the replace-set flush, the rendered-mutation
conformance) belongs to `tests/adapters/test_infrahub_planned_write.py` and
`tests/plan/test_apply_conformance.py`. What is asserted here is the engine's own contract:
stored order, the pre-write gate, the collected delete, and the returned record.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest
from infrahub_sdk.exceptions import AuthenticationError, GraphQLError, ServerNotResponsiveError

from infrahub_sync.plan.errors import (
    ApplyRecordInvariantError,
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
    """The engine returns the record and writes nothing — the CLI is the single writer (AD069).

    A run file seeded before the apply must come back byte-identical, so an engine that wrote
    its own record over an existing one fails here rather than passing an absence check.
    """
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
    """A delete-bearing plan applies its non-deletes and ends with the skip recorded (AD055).

    Not a failure: applying deletes is out of scope for this release, so the delete is
    collected, the create is still applied, and the count is reported at `logging.WARNING`
    — the level `--quiet` floors the package logger at, so an `INFO` emission would vanish
    for exactly the scripted runs where this warning is the only signal.
    """
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
    """FR-017: an operation this release cannot interpret is refused while the plan is read.

    The pairing with the delete case above is the point. A delete is recorded, understood and
    deliberately not executed; an action outside `ACTIONS` is not understood at all, so
    continuing would mean applying part of a plan whose remainder is uninterpretable.
    """
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


def test_no_configuration_and_no_supplied_version_refuses_before_any_write(tmp_path: Path) -> None:
    """`_apply_config_version`'s documented `ValueError`, which nothing else reaches (FR-011, AD013).

    A `Potenda` built without a parsed configuration cannot recompute the comparison value,
    so an in-process caller that also supplies none is asking for a comparison that cannot be
    made. Refusing is not the same as comparing against a blank value: an empty comparison
    version would still be *a* comparison, and the plan whose manifest happened to record one
    would apply. The refusal is asserted on its own wording for that reason — degrading it to
    `validate_config_version("")` still raises `ValueError`, just the wrong one, about the
    wrong thing, and against a caller who supplied nothing rather than something bad.
    """
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
    """FR-022: zero operations is a success, and verification still runs first (AD033).

    The surfaceless pairing is the second clause: an implementation that short-circuited an
    empty plan before verification would return success for a destination that cannot apply a
    plan at all, and the operator would learn nothing.
    """
    directory = _run_dir(tmp_path)
    write_artifact(directory, [], run_id=RUN_ID, source_snapshot=[])

    record = _potenda(directory, RecordingDestination()).apply_plan(config_version=CONFIG_VERSION)

    assert record.applied_operations == ()
    assert record.skipped_delete_count == 0

    with pytest.raises(PlanVerificationError):
        _potenda(directory, SurfacelessDestination()).apply_plan(config_version=CONFIG_VERSION)


# ======================================================================================
# T097 — FR-009's gate disclosure and evaluate-all rule, reached from the apply path
# ======================================================================================


def test_an_identity_value_disagreement_refuses_the_apply_before_any_destination_call(tmp_path: Path) -> None:
    """FIX-013 at the apply boundary: the mismatched record never reaches the destination.

    The checksum is valid — the artifact was *written* with the disagreement — so nothing
    upstream of record validation can catch it. The parse refuses it as torn, after the
    verification gate and before the first dispatch.
    """
    directory = _run_dir(tmp_path)
    mismatched = operation_record(identity={"name": "reviewed"}, payload={"name": "actually-written"})
    write_artifact(directory, [mismatched], run_id=RUN_ID, source_snapshot=[])
    destination = RecordingDestination()

    with pytest.raises(PlanArtifactTornError):
        _potenda(directory, destination).apply_plan(config_version=CONFIG_VERSION)

    assert destination.dispatched == []


def test_a_tear_and_a_config_version_mismatch_are_both_reported_by_one_apply_attempt(tmp_path: Path) -> None:
    """FR-009: once the gate passes, every failure is named in one refusal (AD036).

    The artifact is broken **twice** — its operations file is gone and its configuration
    version disagrees — and both are named. An apply that read the artifact before verifying
    it raises the reader's single-condition tear instead, so the operator fixes the tear,
    re-runs, and only then learns the configuration also changed. That second break is what
    makes this assertion falsifiable.
    """
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
    """FR-009's gate message, reachable from an apply and not only from a direct verify call.

    An artifact whose `format_version` this release does not understand cannot have its
    remaining fields meaningfully interpreted, so checks 2 to 5 are skipped **and the refusal
    says so, naming them**. The configuration version below also disagrees, so an
    implementation that evaluated everything anyway would report two failures and fail here.
    """
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
    """FR-019's verdict is not degraded into "the manifest could not be parsed" (SC-011).

    Verification now precedes the read, and the gate's first arm answers a missing manifest —
    so without the FR-019 check standing in front of it, a run in the pre-existing row format
    would be refused as an unreadable manifest and sent to the wrong remedy.
    """
    directory = _run_dir(tmp_path)
    (directory / "plan.parquet").write_bytes(b"pre-existing row format")
    destination = RecordingDestination()

    with pytest.raises(PlanFormatV1Error) as caught:
        _potenda(directory, destination).apply_plan(config_version=CONFIG_VERSION)

    assert "holds no plan artifact" in str(caught.value)
    assert destination.dispatched == []


def test_an_artifact_substituted_after_verification_is_not_what_gets_applied(tmp_path: Path) -> None:
    """The bytes verified are the bytes applied (DBR-006, DBA-004) — T097's guard, extended.

    T097 fixed *which refusal fires first*; this case pins the other property of the same
    code: verification and application consume **one** read. A structurally valid
    replacement artifact lands on disk the instant the pre-apply gate has passed — the
    reachable race is a concurrent `diff --run-id X` rewriting `plan/` while `apply
    --run-id X` runs. An apply that re-read the artifact after verifying it would dispatch
    the substituted, never-checksum-verified operations; the one-read apply dispatches
    exactly what it verified and the replacement on disk is inert.
    """
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

    The in-tree shape of FIX-006: `apply_planned_operation` issues the base upsert and only
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
    """FIX-006: the record must not imply the failing operation wrote nothing.

    The destination is changed by the failing operation's base upsert, and that operation is
    in neither the applied nor the skipped-delete set — so without the identifier and the
    marker the run undercounts the writes it caused, and the error message reads as though
    only the earlier operations landed. Convergent re-apply (AD033) is what recovers it, which
    the message has to say rather than leave the operator to know.
    """
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
)


@pytest.mark.parametrize("rejection", DESTINATION_REJECTIONS)
def test_a_known_destination_failure_is_wrapped_with_the_operation_and_run_context(
    tmp_path: Path, rejection: Exception
) -> None:
    """FIX-011: inside the operational boundary, a failure becomes the named taxonomy refusal.

    The four cases are the boundary's whole membership as an operator meets it — the SDK's
    GraphQL, authentication and transport rejections, and the plan taxonomy the write surface
    raises itself. Each has a remedy at the destination, so each is reported as one message
    naming the operation, the run and the next action rather than as a traceback.
    """
    directory = _run_dir(tmp_path)
    records = [operation_record(identity={"name": "first"}), operation_record(identity={"name": "second"})]
    write_artifact(directory, records, run_id=RUN_ID, source_snapshot=[])
    destination = DefectiveDestination(rejection)

    with pytest.raises(OperationApplyFailedError) as caught:
        _potenda(directory, destination).apply_plan(config_version=CONFIG_VERSION)

    message = str(caught.value)
    assert str(records[1]["operation_id"]) in message, "the refusal names the operation that failed"
    assert RUN_ID in message, "…and the run it failed in"
    assert str(rejection) in message, "…and the underlying cause"
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
    """FIX-011: a defect must not be presented to the operator as a destination refusal.

    Wrapping these in `OperationApplyFailedError` advises repairing a destination that is
    working, re-planning against a plan that is fine, and hides the traceback that is the only
    diagnosis of the real fault — the first of which an operator will do, because the message
    told them to. So the exception keeps its own type and traceback. It still has to carry the
    record: the earlier operations are written either way, and the failing one may have written
    part of its own change (AD062).
    """
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
    """Ctrl-C on a long apply keeps its own type **and** the record of what was written.

    Two claims, and both matter. Converting a `KeyboardInterrupt` into
    `OperationApplyFailedError` would swallow the one signal an operator expects to stop the
    process, so the type is asserted. Re-raising it bare would lose the operations already
    written, which the caller has no other way to learn — so the partial record riding on the
    exception is asserted too (AD062, AD069).
    """
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
    """AD062's safety net must not zero the record it exists to protect.

    The check runs *after* the loop wrote every non-delete operation, so an error carrying an
    empty record would report a run that wrote everything as having applied nothing — and
    invite a re-apply against a populated destination. The manifest's count is inflated here
    because that is the only clause of the invariant a well-formed artifact can violate.
    """
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
