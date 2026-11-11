"""A stopped `sync` gives back its write admission, so its confirmed apply can be admitted.

Spec 019 FR-012 and FR-023, research R11, data-model.md "Stage result `failure` for a stopped
`sync`", and decision 2 of 2026-11-11. A `sync` run reserves its one write admission when it is
created. When its plan leaves out records, the stage stops before any write. The stop's
writeback asks the store to give the admission back (`release_write_admission=True`), and the
store does so under these conditions:

- only when the run record shows that no operation was dispatched: the stop's verdict is
  `completed`/`succeeded`, the run does not require reconciliation, and the writeback records
  no applied operation
- only for the admission a `sync` reserved, never for an apply's
- in the same transaction that records the stop, so either both are stored or neither is

The confirmed apply then takes a new admission through the normal `reserve_mutation` path, and
every rule of that path stays as it is: a second apply, and an apply on a `sync` run that
dispatched any operation, are refused with `run-execution-conflict`.

The tests drive the public `ProductProjection`, so the release must reach the store through
the projection's own writeback sanitizer, not only through the relational records.

The store keeps one write admission per run, not per configuration. A run of the same
configuration that is admitted between the stop and the confirmed apply does not change what
the store decides for the stopped run; the configuration write guard of the worker serializes
the two writes. `tests/service/test_sync_left_out_stop.py` covers that guard for T078.
"""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from typing import TYPE_CHECKING, Any, cast
from uuid import uuid4

import pytest
from pydantic import ValidationError

from infrahub_sync.product_store import (
    AuditEvent,
    ExecutionFinishWriteback,
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

_SESSION = uuid4().hex[:10]
_CONFLICT_CODE = "run-execution-conflict"
_WORKER_ID = "7c1e2a90-3b4d-4f6e-8a1b-2c3d4e5f6a7b"
_OTHER_WORKER_ID = "0a1b2c3d-4e5f-4a6b-8c7d-8e9f0a1b2c3d"
_STARTED_AT = datetime(2026, 11, 11, 9, tzinfo=timezone.utc)
_STOP_OUTCOME = "left-out-review-required"
_NOT_IMPLEMENTED = "spec 019 US2 not implemented"

# The stop's writeback cannot carry `release_write_admission` before T039.
_RELEASE_MISSING = pytest.mark.xfail(strict=True, raises=ValidationError, reason=_NOT_IMPLEMENTED)

_postgresql_schema = postgresql_schema_fixture("the stopped sync admission's PostgreSQL parameter")


@pytest.fixture(params=("sqlite", pytest.param("postgresql", marks=pytest.mark.integration)))
def projection(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[ProductProjection]:
    """One product projection per provider profile, isolated per test."""
    artifacts = FileArtifactStore(tmp_path / "objects")
    if request.param == "sqlite":
        yield ProductProjection(SQLiteRunStore(tmp_path / "records.sqlite3"), artifacts)
        return
    schema = request.getfixturevalue("_postgresql_schema")
    # Installed only on Python 3.11 and later, with the service profile.
    psycopg: Any = pytest.importorskip("psycopg")
    # pylint: disable-next=import-outside-toplevel
    from infrahub_sync.service.storage import PsycopgConnectionFactory

    def connect() -> Any:  # noqa: ANN401 - the store's own DB-API connection protocol.
        return PsycopgConnectionFactory(psycopg.connect)(schema.dsn)

    yield ProductProjection(PostgreSQLRunStore(connect), artifacts)


def _rid(name: str) -> str:
    return f"{name}-{_SESSION}"


def _run(run_id: str, *, operation: str, config_id: str = "config-001") -> ProductRun:
    return ProductRun(
        run_id=run_id,
        operation=operation,  # ty: ignore[invalid-argument-type] - the literal is one of Operation.
        configuration_reference=f"{config_id}@1",
        config_id=config_id,
        registry_version=1,
        package_checksum="a" * 64,
        actor="operator@example.com",
        started_at=_STARTED_AT,
        phase="accepted",
    )


def _receipt(receipt_id: str, *, run_id: str, operation: str) -> MutationReceipt:
    return MutationReceipt(
        receipt_id=f"{receipt_id}.{run_id}",
        actor="operator@example.com",
        key_digest=sha256(f"{receipt_id}:{run_id}".encode()).hexdigest(),
        operation=operation,
        target_run_id=run_id,
        request_fingerprint=sha256(f"{operation}:{run_id}".encode()).hexdigest(),
        reason="stopped sync admission",
        resource_id=run_id,
        run_id=run_id,
        prefect_key=sha256(f"prefect:{receipt_id}:{run_id}".encode()).hexdigest(),
        created_at=_STARTED_AT,
        updated_at=_STARTED_AT,
    )


def _refusal_code(receipt: MutationReceipt) -> str | None:
    body = receipt.response_body
    if body is None:
        return None
    error = body.get("error")
    return error.get("code") if isinstance(error, dict) else None


def _submit(projection: ProductProjection, run_id: str, receipt: MutationReceipt, *, purpose: str) -> str:
    """Append, answer and claim the execution one admitted receipt owns, as the service and worker do."""
    flow_run_id = f"flow-{purpose}-{receipt.receipt_id}"
    projection.add_prefect_execution(
        run_id,
        PrefectExecutionLink(
            flow_run_id=flow_run_id, purpose=purpose, attempt=1, submitted_at=datetime.now(timezone.utc)
        ),
        receipt_id=receipt.receipt_id,
    )
    projection.complete_mutation(
        receipt.receipt_id, response_status=202, response_body={"accepted": True}, flow_run_id=flow_run_id
    )
    projection.claim_execution(run_id, flow_run_id, worker_id=_WORKER_ID)
    return flow_run_id


def _claimed_sync(projection: ProductProjection, run_id: str, *, config_id: str = "config-001") -> str:
    """Create a `sync` run with its write admission, as `RunService.create_run` does, and claim its stage."""
    receipt = _receipt("m-sync", run_id=run_id, operation="sync")
    reserved, created = projection.reserve_mutation(
        receipt, run=_run(run_id, operation="sync", config_id=config_id), admit_write=True
    )
    assert created
    assert reserved.state == "reserved"
    return _submit(projection, run_id, reserved, purpose="sync")


def _stop_writeback(*, release: bool = True, **overrides: Any) -> ExecutionFinishWriteback:  # noqa: ANN401
    """The writeback of a `sync` that stopped after a plan that leaves out records."""
    fields: dict[str, Any] = {
        "phase": "planned",
        "outcome": _STOP_OUTCOME,
        "finished_at": datetime.now(timezone.utc),
        "summary": {"sync_name": "inventory", "left_out": 12},
        "results": {
            "stage": "sync",
            "outcome": _STOP_OUTCOME,
            "failure": {"stage": "sync", "failure_type": "left_out_review_required", "left_out": 12},
        },
    }
    fields.update(overrides)
    if release:
        fields["release_write_admission"] = True
    return ExecutionFinishWriteback(**fields)


def _commit(  # noqa: PLR0913 - the store's own verdict arguments, each varied by a case.
    projection: ProductProjection,
    run_id: str,
    flow_run_id: str,
    writeback: ExecutionFinishWriteback,
    *,
    verdict: tuple[str, str] = ("completed", "succeeded"),
    worker_id: str = _WORKER_ID,
    event: AuditEvent | None = None,
) -> bool:
    return projection.commit_claimed_execution(
        run_id,
        flow_run_id,
        worker_id=worker_id,
        terminal_at=datetime.now(timezone.utc),
        terminal_state=verdict[0],  # ty: ignore[invalid-argument-type] - one of the three verdicts.
        terminal_outcome=verdict[1],  # ty: ignore[invalid-argument-type] - same.
        writeback=writeback,
        event=event,
    )


def _apply(projection: ProductProjection, run_id: str, name: str) -> MutationReceipt:
    """Request an apply on an existing run through the normal write reservation."""
    reserved, _created = projection.reserve_mutation(_receipt(name, run_id=run_id, operation="apply"), admit_write=True)
    return reserved


def _assert_refused(receipt: MutationReceipt) -> None:
    assert receipt.state == "accepted"
    assert receipt.response_status == 409
    assert _refusal_code(receipt) == _CONFLICT_CODE


def _stored(projection: ProductProjection, run_id: str) -> ProductRun:
    run = projection.lookup_run(run_id).value
    assert run is not None
    return run


# --- T033: the release and the confirmed apply -------------------------------------------------


@_RELEASE_MISSING
def test_a_stopped_sync_records_the_stop_and_admits_its_confirmed_apply(projection: ProductProjection) -> None:
    """The stop and the release are one commit; the apply then reserves through the normal path."""
    run_id = _rid("stopped-sync")
    flow_run_id = _claimed_sync(projection, run_id)

    assert _commit(projection, run_id, flow_run_id, _stop_writeback()) is True

    run = _stored(projection, run_id)
    assert (run.phase, run.outcome) == ("planned", _STOP_OUTCOME)
    assert run.reconciliation_required is False
    admitted = _apply(projection, run_id, "m-confirmed-apply")
    assert admitted.state == "reserved", "The confirmed apply of a stopped sync is admitted."
    assert admitted.response_status is None
    # The new admission names the apply's own receipt, so the apply may append its execution.
    _submit(projection, run_id, admitted, purpose="apply")


@_RELEASE_MISSING
def test_a_second_apply_after_the_confirmed_apply_is_refused(projection: ProductProjection) -> None:
    """One write admission per run that writes, and none after it (FR-023)."""
    run_id = _rid("stopped-sync-twice")
    flow_run_id = _claimed_sync(projection, run_id)
    _commit(projection, run_id, flow_run_id, _stop_writeback())
    first = _apply(projection, run_id, "m-first-apply")
    assert first.state == "reserved"

    second = _apply(projection, run_id, "m-second-apply")

    _assert_refused(second)


@_RELEASE_MISSING
def test_a_second_apply_after_the_confirmed_apply_finished_is_refused(projection: ProductProjection) -> None:
    """A terminal apply still holds the run's one admission: the run is not reopened."""
    run_id = _rid("stopped-sync-applied")
    flow_run_id = _claimed_sync(projection, run_id)
    _commit(projection, run_id, flow_run_id, _stop_writeback())
    first = _apply(projection, run_id, "m-first-apply")
    apply_flow = _submit(projection, run_id, first, purpose="apply")
    _commit(
        projection,
        run_id,
        apply_flow,
        ExecutionFinishWriteback(
            phase="applied", outcome="applied", finished_at=datetime.now(timezone.utc), summary={}, results={}
        ),
    )

    _assert_refused(_apply(projection, run_id, "m-later-apply"))


@pytest.mark.parametrize(
    ("verdict", "overrides"),
    [
        pytest.param(
            ("interrupted", "ambiguous"),
            {"phase": "sync-interrupted", "outcome": "ambiguous", "summary": {"applied_operations": ["op-1"]}},
            id="ambiguous-after-a-dispatch",
        ),
        pytest.param(
            ("failed", "failed"),
            {
                "phase": "sync-failed",
                "outcome": "failed",
                "summary": {"applied_operations": ["op-1"], "failed_operation": "op-2"},
            },
            id="failed-after-writing-one-operation",
        ),
        pytest.param(
            ("completed", "succeeded"),
            {
                "phase": "applied",
                "outcome": "applied",
                "summary": {"applied_operations": ["op-1"]},
                "results": {"applied_operations": ["op-1"]},
            },
            id="completed-write",
        ),
    ],
)
@_RELEASE_MISSING
def test_a_sync_that_dispatched_an_operation_keeps_its_admission_even_when_a_release_is_asked(
    projection: ProductProjection, verdict: tuple[str, str], overrides: dict[str, Any]
) -> None:
    """The release depends on the run record, not on the request: a dispatched write is never given back."""
    run_id = _rid(f"dispatched-{verdict[0]}")
    flow_run_id = _claimed_sync(projection, run_id)

    _commit(projection, run_id, flow_run_id, _stop_writeback(**overrides), verdict=verdict)

    _assert_refused(_apply(projection, run_id, "m-apply-after-dispatch"))


def test_a_sync_that_wrote_without_asking_for_a_release_keeps_its_admission(projection: ProductProjection) -> None:
    """Today's rule, unchanged: an apply on a `sync` run that wrote is refused."""
    run_id = _rid("sync-applied")
    flow_run_id = _claimed_sync(projection, run_id)
    _commit(
        projection,
        run_id,
        flow_run_id,
        ExecutionFinishWriteback(
            phase="applied",
            outcome="applied",
            finished_at=datetime.now(timezone.utc),
            summary={"create": 1},
            results={"operation": "sync"},
        ),
    )

    _assert_refused(_apply(projection, run_id, "m-apply-after-sync"))


def test_a_stop_without_the_release_keeps_the_admission(projection: ProductProjection) -> None:
    """Nothing gives the admission back unless the stop's writeback asks for it."""
    run_id = _rid("stop-no-release")
    flow_run_id = _claimed_sync(projection, run_id)

    _commit(projection, run_id, flow_run_id, _stop_writeback(release=False))

    _assert_refused(_apply(projection, run_id, "m-apply-unreleased"))


@_RELEASE_MISSING
def test_the_release_never_gives_back_an_apply_admission(projection: ProductProjection) -> None:
    """Only the admission a `sync` reserved at creation can be given back."""
    run_id = _rid("apply-release")
    receipt = _receipt("m-plan", run_id=run_id, operation="plan")
    projection.reserve_mutation(receipt, run=_run(run_id, operation="plan"))
    plan_flow = _submit(projection, run_id, receipt, purpose="plan")
    _commit(
        projection,
        run_id,
        plan_flow,
        ExecutionFinishWriteback(
            phase="planned", outcome="planned", finished_at=datetime.now(timezone.utc), summary={}, results={}
        ),
    )
    admitted = _apply(projection, run_id, "m-apply")
    apply_flow = _submit(projection, run_id, admitted, purpose="apply")

    _commit(projection, run_id, apply_flow, _stop_writeback())

    _assert_refused(_apply(projection, run_id, "m-apply-again"))


# --- T033: one state transition ---------------------------------------------------------------


def _event(run_id: str) -> AuditEvent:
    return AuditEvent(
        event_id=f"e-{uuid4().hex}",
        run_id=run_id,
        actor="operator@example.com",
        operation="sync",
        reason="stopped sync admission",
        outcome=_STOP_OUTCOME,
        created_at=datetime.now(timezone.utc),
    )


def _assert_nothing_recorded(projection: ProductProjection, run_id: str) -> None:
    """The stop is not stored, the execution stays open and the admission stays in place."""
    run = _stored(projection, run_id)
    assert (run.phase, run.outcome) != ("planned", _STOP_OUTCOME)
    assert run.prefect_executions[-1].terminal_at is None
    _assert_refused(_apply(projection, run_id, "m-apply-after-rollback"))


@_RELEASE_MISSING
def test_a_stop_that_fails_after_the_release_stores_neither(
    projection: ProductProjection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The last statement of the stop's transaction fails: the release is rolled back with it."""
    run_id = _rid("stop-event-fails")
    flow_run_id = _claimed_sync(projection, run_id)
    records = projection._records

    def refuse_event(*_args: object, **_kwargs: object) -> None:
        msg = "the audit event could not be stored"
        raise RuntimeError(msg)

    monkeypatch.setattr(records, "_insert_event", refuse_event)
    writeback = _stop_writeback()

    with pytest.raises(RuntimeError, match="audit event"):
        _commit(projection, run_id, flow_run_id, writeback, event=_event(run_id))

    monkeypatch.undo()
    _assert_nothing_recorded(projection, run_id)


class _RefusingCursor:
    """A cursor that refuses the statement that gives back a write admission."""

    def __init__(self, cursor: Any) -> None:  # noqa: ANN401 - the provider's own DB-API cursor.
        self._cursor = cursor

    def execute(self, sql: str, *args: Any) -> Any:  # noqa: ANN401 - DB-API.
        if "DELETE" in sql.upper() and "WRITE_ADMISSIONS" in sql.upper():
            msg = "the write admission could not be given back"
            raise RuntimeError(msg)
        return self._cursor.execute(sql, *args)

    def __getattr__(self, name: str) -> Any:  # noqa: ANN401 - every other DB-API call is the real one.
        return getattr(self._cursor, name)


class _RefusingConnection:
    def __init__(self, connection: Any) -> None:  # noqa: ANN401 - the provider's own DB-API connection.
        self._connection = connection

    def cursor(self) -> _RefusingCursor:
        return _RefusingCursor(self._connection.cursor())

    def __getattr__(self, name: str) -> Any:  # noqa: ANN401 - commit, rollback and close are the real ones.
        return getattr(self._connection, name)


@_RELEASE_MISSING
def test_a_release_that_fails_does_not_record_the_stop(
    projection: ProductProjection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The release cannot be a later, separate step that leaves a stored stop holding its admission."""
    run_id = _rid("release-fails")
    flow_run_id = _claimed_sync(projection, run_id)
    # The relational provider, whose connection factory is wrapped rather than replaced.
    records = cast("Any", projection._records)
    connect = records._connect
    monkeypatch.setattr(records, "_connect", lambda: _RefusingConnection(connect()))
    writeback = _stop_writeback()

    with pytest.raises(RuntimeError, match="given back"):
        _commit(projection, run_id, flow_run_id, writeback)

    monkeypatch.undo()
    _assert_nothing_recorded(projection, run_id)


@_RELEASE_MISSING
def test_a_stop_from_a_worker_that_does_not_own_the_execution_changes_nothing(projection: ProductProjection) -> None:
    """A verdict the store does not accept stores no stop and gives back no admission."""
    run_id = _rid("stop-wrong-worker")
    flow_run_id = _claimed_sync(projection, run_id)

    committed = _commit(projection, run_id, flow_run_id, _stop_writeback(), worker_id=_OTHER_WORKER_ID)

    assert committed is False
    _assert_nothing_recorded(projection, run_id)


# --- T078: another run of the same configuration between the stop and the apply -----------------


@_RELEASE_MISSING
def test_another_run_admitted_between_the_stop_and_the_apply_leaves_the_store_decision_unchanged(
    projection: ProductProjection,
) -> None:
    """The store admits per run; the configuration write guard, not the store, serializes the two writes.

    A second `sync` of the same configuration takes its own write admission after the stop. The
    stopped run's confirmed apply is still decided by the normal per-run rule, and the two runs
    never share an admission. That the apply then waits for, or is refused by, the other run's
    configuration write guard is covered by `tests/service/test_sync_left_out_stop.py`.
    """
    stopped = _rid("stopped-before-competitor")
    competitor = _rid("competing-sync")
    stopped_flow = _claimed_sync(projection, stopped, config_id="config-shared")
    _commit(projection, stopped, stopped_flow, _stop_writeback())

    _claimed_sync(projection, competitor, config_id="config-shared")
    admitted = _apply(projection, stopped, "m-apply-after-competitor")

    assert admitted.state == "reserved"
    _assert_refused(_apply(projection, competitor, "m-apply-on-competitor"))
