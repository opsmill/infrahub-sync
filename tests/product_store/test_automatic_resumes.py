"""The stored decision of automatic resume (spec 030, data-model.md).

`interrupt_execution` inserts an `AutomaticResume` row in the transaction that interrupts the
write execution, and only when that interruption is saved (FR-013). Every later move of the row
is a compare-and-set on its key and its state, so only one Sync API process moves it (FR-014).

The same cases run against a real PostgreSQL server when `PRODUCT_STORE_TEST_POSTGRESQL_DSN`
names one; see `tests/product_store/postgresql_isolation`.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest
from pydantic import ValidationError

from infrahub_sync.product_store import (
    PLAN_ARTIFACT_ID,
    AutomaticResume,
    AutomaticResumeState,
    MutationReceipt,
    PrefectExecutionLink,
    ProductProjection,
    ProductRun,
)
from infrahub_sync.product_store.store import FileArtifactStore, PostgreSQLRunStore, SQLiteRunStore
from tests.product_store.postgresql_isolation import postgresql_schema_fixture

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

_WORKER_ID = "3d6c1f2a-8b4e-4c7d-9e0f-1a2b3c4d5e6f"
_STARTED_AT = datetime(2026, 11, 23, 9, tzinfo=timezone.utc)
_INTERRUPTED_AT = _STARTED_AT + timedelta(minutes=5)

_postgresql_schema = postgresql_schema_fixture("the automatic resumes' PostgreSQL parameter")


@pytest.fixture(params=("sqlite", pytest.param("postgresql", marks=pytest.mark.integration)))
def projection(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[ProductProjection]:
    """One product projection per provider profile, isolated per test."""
    artifacts = FileArtifactStore(tmp_path / "objects")
    if request.param == "sqlite":
        yield ProductProjection(SQLiteRunStore(tmp_path / "records.sqlite3"), artifacts)
        return
    schema = request.getfixturevalue("_postgresql_schema")
    # pylint: disable-next=import-outside-toplevel
    import psycopg  # ty: ignore[unresolved-import] - TODO: optional service dependency

    from infrahub_sync.service.storage import PsycopgConnectionFactory

    def connect() -> Any:  # noqa: ANN401 - the store's own DB-API connection protocol.
        return PsycopgConnectionFactory(psycopg.connect)(schema.dsn)

    yield ProductProjection(PostgreSQLRunStore(connect), artifacts)


def _rid(name: str) -> str:
    """A run identifier of its own, because the PostgreSQL leg shares one schema per module."""
    return f"{name}-{uuid4().hex[:10]}"


def _open_rows_of(projection: ProductProjection, *run_ids: str) -> tuple[AutomaticResume, ...]:
    """The open rows of the given runs, in the store's order; other tests' rows are left out."""
    return tuple(row for row in projection.open_automatic_resumes() if row.run_id in run_ids)


def _record(run_id: str, flow_run_id: str, **changes: object) -> AutomaticResume:
    values: dict[str, object] = {
        "run_id": run_id,
        "interrupted_flow_run_id": flow_run_id,
        "cause": "rule-8",
        "worker_name": "worker-1",
        "state": "waiting-check",
        "decided_at": _INTERRUPTED_AT,
        "updated_at": _INTERRUPTED_AT,
    }
    return AutomaticResume.model_validate({**values, **changes})


def _claimed_apply(projection: ProductProjection, name: str) -> tuple[str, str]:
    """A run with a published plan and a claimed apply execution that has not ended."""
    run_id = _rid(name)
    projection.create_run(
        ProductRun(
            run_id=run_id,
            operation="plan",
            configuration_reference="config-001@1",
            config_id="config-001",
            registry_version=1,
            package_checksum="a" * 64,
            actor="operator@example.com",
            started_at=_STARTED_AT,
            phase="accepted",
        )
    )
    projection.publish_artifact(
        run_id, artifact_id=PLAN_ARTIFACT_ID, kind="saved-plan-review", media_type="application/json", data=b"{}"
    )
    receipt, _created = projection.reserve_mutation(
        MutationReceipt(
            receipt_id=f"m-apply.{run_id}",
            actor="operator@example.com",
            key_digest=sha256(f"apply:{run_id}".encode()).hexdigest(),
            operation="apply",
            target_run_id=run_id,
            request_fingerprint=sha256(f"apply-request:{run_id}".encode()).hexdigest(),
            reason="automatic resume store",
            resource_id=run_id,
            run_id=run_id,
            prefect_key=sha256(f"prefect:{run_id}".encode()).hexdigest(),
            created_at=_STARTED_AT,
            updated_at=_STARTED_AT,
        ),
        admit_write=True,
    )
    flow_run_id = f"flow-{receipt.receipt_id}"
    projection.add_prefect_execution(
        run_id,
        PrefectExecutionLink(
            flow_run_id=flow_run_id, purpose="apply", attempt=1, submitted_at=datetime.now(timezone.utc)
        ),
        receipt_id=receipt.receipt_id,
    )
    projection.complete_mutation(
        receipt.receipt_id, response_status=202, response_body={"accepted": True}, flow_run_id=flow_run_id
    )
    assert projection.claim_execution(run_id, flow_run_id, worker_id=_WORKER_ID)
    return run_id, flow_run_id


# The record's validation.


@pytest.mark.parametrize(
    "changes",
    [
        pytest.param({"state": "waiting-check"}, id="waiting-check-without-decision"),
        pytest.param({"state": "checking", "check_flow_run_id": "flow-check"}, id="checking"),
        pytest.param({"state": "resuming", "decision": "resume", "start_after": 4}, id="resuming"),
        pytest.param({"state": "ended", "decision": "resume", "resume_flow_run_id": "flow-resume"}, id="ended-resume"),
        pytest.param(
            {
                "state": "ended",
                "decision": "no-resume",
                "reason": "check-found-differs",
                "reason_detail": {"differs": 2},
            },
            id="ended-no-resume",
        ),
    ],
)
def test_a_consistent_record_is_accepted(changes: dict[str, object]) -> None:
    record = _record("run-1", "flow-1", **changes)

    assert record.state == changes["state"]


@pytest.mark.parametrize(
    "changes",
    [
        pytest.param({"state": "waiting-check", "decision": "resume"}, id="decision-while-waiting-check"),
        pytest.param({"state": "checking", "decision": "no-resume", "reason": "refused"}, id="decision-while-checking"),
        pytest.param({"state": "waiting-resume", "decision": "resume"}, id="decision-while-waiting-resume"),
        pytest.param({"state": "ended", "decision": "no-resume"}, id="no-resume-without-reason"),
        pytest.param({"state": "ended", "decision": "resume", "reason": "refused"}, id="reason-with-resume"),
        pytest.param({"state": "waiting-check", "reason": "refused"}, id="reason-without-decision"),
        pytest.param({"reason": "unknown-reason", "state": "ended", "decision": "no-resume"}, id="unknown-reason"),
        pytest.param({"cause": "rule-7"}, id="unknown-cause"),
        pytest.param({"reason_detail": {"differs": [1, 2]}}, id="detail-holds-a-list"),
        pytest.param({"reason_detail": {"differs": 2.5}}, id="detail-holds-a-number-that-is-not-a-count"),
        pytest.param({"operations_not_checked": -1}, id="negative-count"),
        pytest.param({"decided_at": datetime(2026, 11, 23, 9)}, id="time-without-timezone"),  # noqa: DTZ001
    ],
)
def test_an_inconsistent_record_is_refused(changes: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        _record("run-1", "flow-1", **changes)


# The insert in the interruption transaction.


def test_the_interruption_inserts_the_row_in_its_transaction(projection: ProductProjection) -> None:
    run_id, flow_run_id = _claimed_apply(projection, "inserted")
    record = _record(run_id, flow_run_id)

    assert projection.interrupt_execution(run_id, flow_run_id, terminal_at=_INTERRUPTED_AT, automatic_resume=record)

    assert projection.automatic_resumes_of(run_id) == (record,)
    assert _open_rows_of(projection, run_id) == (record,)


def test_a_second_interruption_of_the_same_execution_inserts_nothing(projection: ProductProjection) -> None:
    run_id, flow_run_id = _claimed_apply(projection, "second")
    first = _record(run_id, flow_run_id)
    assert projection.interrupt_execution(run_id, flow_run_id, terminal_at=_INTERRUPTED_AT, automatic_resume=first)

    second = _record(run_id, flow_run_id, cause="rule-6", worker_name=None)
    assert not projection.interrupt_execution(
        run_id, flow_run_id, terminal_at=_INTERRUPTED_AT + timedelta(minutes=1), automatic_resume=second
    )

    assert projection.automatic_resumes_of(run_id) == (first,)


def test_an_interruption_that_is_not_saved_inserts_nothing(projection: ProductProjection) -> None:
    run_id, _flow_run_id = _claimed_apply(projection, "unknown-execution")

    assert not projection.interrupt_execution(
        run_id, "flow-unknown", terminal_at=_INTERRUPTED_AT, automatic_resume=_record(run_id, "flow-unknown")
    )

    assert projection.automatic_resumes_of(run_id) == ()


def test_an_interruption_without_a_record_inserts_nothing(projection: ProductProjection) -> None:
    run_id, flow_run_id = _claimed_apply(projection, "turned-off")

    assert projection.interrupt_execution(run_id, flow_run_id, terminal_at=_INTERRUPTED_AT)

    assert projection.automatic_resumes_of(run_id) == ()


def test_a_record_of_another_execution_is_refused(projection: ProductProjection) -> None:
    run_id, flow_run_id = _claimed_apply(projection, "mismatch")

    with pytest.raises(ValueError, match="must name the run and the execution"):
        projection.interrupt_execution(
            run_id, flow_run_id, terminal_at=_INTERRUPTED_AT, automatic_resume=_record(run_id, "flow-other")
        )

    stored = projection.lookup_run(run_id).value
    assert stored is not None
    assert stored.phase != "interrupted"
    assert projection.automatic_resumes_of(run_id) == ()


def test_a_row_inserted_as_ended_is_not_open(projection: ProductProjection) -> None:
    run_id, flow_run_id = _claimed_apply(projection, "ended")
    record = _record(run_id, flow_run_id, state="ended", decision="no-resume", reason="not-resumable")

    assert projection.interrupt_execution(run_id, flow_run_id, terminal_at=_INTERRUPTED_AT, automatic_resume=record)

    assert projection.automatic_resumes_of(run_id) == (record,)
    assert _open_rows_of(projection, run_id) == ()


# The compare-and-set moves.


def test_a_move_from_the_stored_state_saves_the_fields(projection: ProductProjection) -> None:
    run_id, flow_run_id = _claimed_apply(projection, "moved")
    projection.interrupt_execution(
        run_id, flow_run_id, terminal_at=_INTERRUPTED_AT, automatic_resume=_record(run_id, flow_run_id)
    )
    moved_at = _INTERRUPTED_AT + timedelta(seconds=30)

    assert projection.move_automatic_resume(
        run_id,
        flow_run_id,
        from_state="waiting-check",
        to_state="checking",
        check_flow_run_id="flow-check",
        updated_at=moved_at,
    )
    assert projection.move_automatic_resume(
        run_id,
        flow_run_id,
        from_state="checking",
        to_state="ended",
        decision="no-resume",
        reason="check-found-differs",
        reason_detail={"differs": 3},
        operations_not_checked=12,
        updated_at=moved_at,
    )

    (stored,) = projection.automatic_resumes_of(run_id)
    assert stored == _record(
        run_id,
        flow_run_id,
        state="ended",
        decision="no-resume",
        reason="check-found-differs",
        reason_detail={"differs": 3},
        operations_not_checked=12,
        check_flow_run_id="flow-check",
        updated_at=moved_at,
    )
    assert _open_rows_of(projection, run_id) == ()


def test_a_move_from_a_stale_state_returns_false(projection: ProductProjection) -> None:
    run_id, flow_run_id = _claimed_apply(projection, "stale")
    projection.interrupt_execution(
        run_id, flow_run_id, terminal_at=_INTERRUPTED_AT, automatic_resume=_record(run_id, flow_run_id)
    )
    assert projection.move_automatic_resume(
        run_id, flow_run_id, from_state="waiting-check", to_state="checking", check_flow_run_id="flow-first"
    )

    # A second process read the row in `waiting-check` before the first one moved it.
    assert not projection.move_automatic_resume(
        run_id, flow_run_id, from_state="waiting-check", to_state="checking", check_flow_run_id="flow-second"
    )

    (stored,) = projection.automatic_resumes_of(run_id)
    assert (stored.state, stored.check_flow_run_id) == ("checking", "flow-first")


def test_a_move_of_a_row_that_does_not_exist_returns_false(projection: ProductProjection) -> None:
    assert not projection.move_automatic_resume(
        _rid("missing"), "flow-missing", from_state="waiting-check", to_state="checking"
    )


@pytest.mark.parametrize(
    ("from_state", "to_state", "fields", "message"),
    [
        pytest.param("waiting-check", "resuming", {"decision": "resume"}, "cannot move", id="skips-the-check"),
        pytest.param("ended", "waiting-check", {}, "cannot move", id="leaves-ended"),
        pytest.param("waiting-check", "checking", {"cause": "rule-6"}, "cannot set cause", id="sets-the-cause"),
        pytest.param(
            "waiting-check", "checking", {"decided_at": _STARTED_AT}, "cannot set decided_at", id="sets-the-insert-time"
        ),
    ],
)
def test_a_move_that_the_rules_do_not_allow_is_refused(
    projection: ProductProjection,
    from_state: AutomaticResumeState,
    to_state: AutomaticResumeState,
    fields: dict[str, Any],
    message: str,
) -> None:
    run_id, flow_run_id = _claimed_apply(projection, "refused-move")
    projection.interrupt_execution(
        run_id, flow_run_id, terminal_at=_INTERRUPTED_AT, automatic_resume=_record(run_id, flow_run_id)
    )

    with pytest.raises(ValueError, match=message):
        projection.move_automatic_resume(run_id, flow_run_id, from_state=from_state, to_state=to_state, **fields)

    (stored,) = projection.automatic_resumes_of(run_id)
    assert stored.state == "waiting-check"


def test_a_move_to_an_invalid_row_changes_nothing(projection: ProductProjection) -> None:
    run_id, flow_run_id = _claimed_apply(projection, "invalid-move")
    record = _record(run_id, flow_run_id)
    projection.interrupt_execution(run_id, flow_run_id, terminal_at=_INTERRUPTED_AT, automatic_resume=record)

    # `no-resume` requires a reason.
    with pytest.raises(ValidationError):
        projection.move_automatic_resume(
            run_id, flow_run_id, from_state="waiting-check", to_state="ended", decision="no-resume"
        )

    assert projection.automatic_resumes_of(run_id) == (record,)


def test_open_rows_are_listed_oldest_first(projection: ProductProjection) -> None:
    later_run, later_flow = _claimed_apply(projection, "later")
    earlier_run, earlier_flow = _claimed_apply(projection, "earlier")
    later = _record(later_run, later_flow, decided_at=_INTERRUPTED_AT + timedelta(minutes=1))
    earlier = _record(earlier_run, earlier_flow)
    projection.interrupt_execution(later_run, later_flow, terminal_at=_INTERRUPTED_AT, automatic_resume=later)
    projection.interrupt_execution(earlier_run, earlier_flow, terminal_at=_INTERRUPTED_AT, automatic_resume=earlier)

    assert _open_rows_of(projection, earlier_run, later_run) == (earlier, later)
