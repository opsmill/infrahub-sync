"""Schedules in the product store (spec 021): the records, the admission of a start, and overlap.

The store keeps one row for each schedule, one row for each run with branch review, and writes
the run of a scheduled start with its receipt, its write admission, its claimed execution and
its events in one transaction (research R2). It also counts the failed runs of a schedule in a
row and stops the schedule at five (FR-032), and answers which run of a configuration is in
progress (research R7).

The same cases run against a real PostgreSQL server when `PRODUCT_STORE_TEST_POSTGRESQL_DSN`
names one; see `tests/product_store/postgresql_isolation`. Each PostgreSQL case has its own
schema, because the schedule list reads every schedule of the database.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from threading import Barrier
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest
from pydantic import ValidationError

from infrahub_sync.product_store import (
    MAX_CONSECUTIVE_FAILURES,
    SCHEDULE_START_OPERATION,
    SCHEDULE_STOPPED_EVENT_TYPE,
    AuditEvent,
    AutomaticResume,
    BranchReviewKept,
    BranchReviewOutcome,
    ConfigurationNotFoundError,
    DuplicateRunError,
    EventDetails,
    EventFilters,
    ExecutionFinishWriteback,
    MutationReceipt,
    PrefectExecutionLink,
    ProductProjection,
    ProductRun,
    RelatedResource,
    RunFilters,
    ScheduleBranch,
    ScheduleChangedError,
    ScheduleDeletedError,
    ScheduledStartAlreadyRecordedError,
    ScheduledStartNotRun,
    ScheduledStartResult,
    ScheduledStartRun,
    ScheduleExistsForConfigurationError,
    ScheduleFilters,
    ScheduleNameInUseError,
    ScheduleNotFoundError,
    ScheduleRecord,
    ScheduleTransitionError,
)
from infrahub_sync.product_store.store import FileArtifactStore, PostgreSQLRunStore, SQLiteRunStore
from tests.product_store.postgresql_isolation import isolated_schema_fixture
from tests.product_store.test_contract import _named_configuration_package

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from tests.product_store.postgresql_isolation import IsolatedSchema

_OWNER = "operator@example.com"
_WORKER_ID = "3d6c1f2a-8b4e-4c7d-9e0f-1a2b3c4d5e6f"
_CREATED_AT = datetime(2026, 11, 17, 9, tzinfo=timezone.utc)
_FIRST_START = datetime(2026, 11, 18, 2, tzinfo=timezone.utc)
_DAY = timedelta(days=1)


@pytest.fixture(name="_postgresql_schema")
def _postgresql_schema_fixture() -> Iterator[IsolatedSchema]:
    """Hold one generated schema for one case, and drop only that schema."""
    yield from isolated_schema_fixture("the schedules' PostgreSQL parameter")


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


def _schedule(projection: ProductProjection, **overrides: object) -> ScheduleRecord:
    """Build a schedule of a newly registered configuration; nothing is stored for the schedule."""
    values: dict[str, object] = {
        "schedule_id": f"s-{uuid4().hex}",
        "name": f"nightly-{uuid4().hex[:8]}",
        "registry_version": 1,
        "cron": "0 2 * * *",
        "write_target": "default-branch",
        "state": "active",
        "owner": _OWNER,
        "created_at": _CREATED_AT,
        "updated_at": _CREATED_AT,
    }
    values.update(overrides)
    if "config_id" not in values:
        # A name is unique among configurations, so each configuration gets its own.
        package = _named_configuration_package(f"configuration-{uuid4().hex[:8]}")
        values["config_id"] = projection.create_configuration(package).config_id
    return ScheduleRecord.model_validate(values)


def _created(projection: ProductProjection, **overrides: object) -> ScheduleRecord:
    schedule = _schedule(projection, **overrides)
    projection.create_schedule(schedule)
    return schedule


def _stored(projection: ProductProjection, schedule_id: str) -> ScheduleRecord:
    stored = projection.lookup_schedule(schedule_id).value
    assert stored is not None
    return stored


def _changed(schedule: ScheduleRecord, **changes: object) -> ScheduleRecord:
    return ScheduleRecord.model_validate({**schedule.model_dump(), **changes})


def _scheduled_run(
    projection: ProductProjection, schedule: ScheduleRecord, start: datetime, **overrides: object
) -> ProductRun:
    version = projection.lookup_configuration_version(schedule.config_id, schedule.registry_version).value
    assert version is not None
    values: dict[str, object] = {
        "run_id": f"run-{uuid4().hex[:12]}",
        "operation": "sync",
        "configuration_reference": f"{schedule.config_id}@{schedule.registry_version}",
        "config_id": schedule.config_id,
        "registry_version": schedule.registry_version,
        "package_checksum": version.package_checksum,
        "actor": schedule.owner,
        "started_at": start + timedelta(seconds=1),
        "phase": "accepted",
        "trigger_type": "scheduled",
        "schedule_id": schedule.schedule_id,
        "scheduled_start_at": start,
    }
    values.update(overrides)
    return ProductRun.model_validate(values)


def _start_event(schedule: ScheduleRecord, start: datetime, *, outcome: str, run_id: str | None = None) -> AuditEvent:
    return AuditEvent(
        event_id=f"a-{uuid4().hex}",
        run_id=run_id,
        actor=schedule.owner,
        operation=SCHEDULE_START_OPERATION,
        reason=None,
        outcome=outcome,
        created_at=start + timedelta(seconds=1),
        event_type="infrahub_sync.schedule.start_missed"
        if outcome.startswith("missed")
        else "infrahub_sync.schedule.start_skipped",
        config_id=schedule.config_id,
        registry_version=schedule.registry_version,
        related=(
            RelatedResource(kind="account", id=schedule.owner),
            RelatedResource(kind="schedule", id=schedule.schedule_id),
        ),
        details=EventDetails(start_at=start),
    )


def _admit(  # noqa: PLR0913 - the facts of one admission.
    projection: ProductProjection,
    schedule: ScheduleRecord,
    start: datetime,
    *,
    branch: str | None = None,
    events: tuple[AuditEvent, ...] = (),
    kept_run_ids: tuple[str, ...] = (),
    run: ProductRun | None = None,
    receipt_age_limit: timedelta | None = None,
) -> ScheduledStartResult:
    """Admit a start that runs, from the stored state of the schedule."""
    new_run = run or _scheduled_run(projection, schedule, start, destination_branch=branch)
    return projection.admit_scheduled_start(
        schedule.schedule_id,
        start,
        f"flow-{new_run.run_id}",
        _WORKER_ID,
        start + timedelta(seconds=1),
        ScheduledStartRun(
            run=new_run,
            branch_review=branch is not None,
            events=events,
            kept_run_ids=kept_run_ids,
            last_start_at=getattr(projection.lookup_schedule(schedule.schedule_id).value, "last_start_at", None),
            waiting_start_at=getattr(projection.lookup_schedule(schedule.schedule_id).value, "waiting_start_at", None),
        ),
        receipt_age_limit=receipt_age_limit,
    )


def _skip(  # noqa: PLR0913 - the facts of one admission.
    projection: ProductProjection,
    schedule: ScheduleRecord,
    start: datetime,
    *,
    cause: str = "skipped-run-in-progress",
    events: tuple[AuditEvent, ...] = (),
    kept_run_ids: tuple[str, ...] = (),
) -> ScheduledStartResult:
    return projection.admit_scheduled_start(
        schedule.schedule_id,
        start,
        f"flow-skip-{uuid4().hex[:8]}",
        _WORKER_ID,
        start + timedelta(seconds=1),
        ScheduledStartNotRun.model_validate(
            {
                "cause": cause,
                "last_start_at": _stored(projection, schedule.schedule_id).last_start_at,
                "waiting_start_at": _stored(projection, schedule.schedule_id).waiting_start_at,
                "events": events,
                "kept_run_ids": kept_run_ids,
            }
        ),
    )


def _end(  # noqa: PLR0913 - the verdict of one execution.
    projection: ProductProjection,
    run_id: str,
    *,
    state: str = "failed",
    outcome: str = "failed",
    phase: str = "failed",
    run_outcome: str = "failed",
    release_write_admission: bool = False,
    at: datetime = _FIRST_START + timedelta(minutes=5),
) -> None:
    assert projection.commit_claimed_execution(
        run_id,
        f"flow-{run_id}",
        worker_id=_WORKER_ID,
        terminal_at=at,
        terminal_state=state,  # ty: ignore[invalid-argument-type] - the cases pass the legal verdicts.
        terminal_outcome=outcome,  # ty: ignore[invalid-argument-type]
        writeback=ExecutionFinishWriteback(
            phase=phase,
            outcome=run_outcome,
            finished_at=at,
            summary={},
            results={},
            release_write_admission=release_write_admission,
        ),
    )


def _failed_runs(projection: ProductProjection, schedule: ScheduleRecord, count: int, *, first: int = 0) -> list[str]:
    """Admit ``count`` starts of the schedule, one a day, and end each run as failed."""
    run_ids: list[str] = []
    for index in range(first, first + count):
        result = _admit(projection, schedule, _FIRST_START + index * _DAY)
        assert result.run_id is not None
        _end(projection, result.run_id)
        run_ids.append(result.run_id)
    return run_ids


def _schedule_events(projection: ProductProjection, schedule: ScheduleRecord) -> tuple[AuditEvent, ...]:
    related = (RelatedResource(kind="schedule", id=schedule.schedule_id),)
    return projection.list_events(EventFilters(related=related)).items


# --- create, read, list -----------------------------------------------------------------


def test_a_created_schedule_reads_back_equal_and_an_unknown_one_is_unavailable(projection: ProductProjection) -> None:
    schedule = _created(projection, timezone="Europe/Paris", prefect_schedule_id="prefect-1")

    assert _stored(projection, schedule.schedule_id) == schedule
    unknown = projection.lookup_schedule(f"s-{uuid4().hex}")
    assert not unknown.available
    assert unknown.reason is not None


def test_a_schedule_is_created_active(projection: ProductProjection) -> None:
    with pytest.raises(ValueError, match="active"):
        projection.create_schedule(_schedule(projection, state="paused"))


def test_a_schedule_is_created_with_its_events_in_one_transaction(projection: ProductProjection) -> None:
    schedule = _schedule(projection)
    event = _start_event(schedule, _FIRST_START, outcome="skipped-run-in-progress")
    projection.record_audit(event)

    with pytest.raises(Exception, match=r"(?i)unique|duplicate|provider failed"):
        projection.create_schedule(schedule, events=(event,))

    assert not projection.lookup_schedule(schedule.schedule_id).available


def test_a_name_is_unique_among_schedules_that_are_not_deleted(projection: ProductProjection) -> None:
    first = _created(projection, name="nightly")

    with pytest.raises(ScheduleNameInUseError):
        projection.create_schedule(_schedule(projection, name="nightly"))

    projection.update_schedule(_changed(first, state="deleted"))
    second = _created(projection, name="nightly")
    assert _stored(projection, second.schedule_id).name == "nightly"
    assert _stored(projection, first.schedule_id).state == "deleted"


def test_a_configuration_has_one_schedule_that_is_not_deleted(projection: ProductProjection) -> None:
    first = _created(projection)

    with pytest.raises(ScheduleExistsForConfigurationError) as refused:
        projection.create_schedule(_schedule(projection, config_id=first.config_id, registry_version=1))

    assert (refused.value.schedule_id, refused.value.name) == (first.schedule_id, first.name)
    assert first.name in str(refused.value)
    projection.update_schedule(_changed(first, state="deleted"))
    _created(projection, config_id=first.config_id)


def test_the_schedule_list_pages_newest_first_with_a_cursor(projection: ProductProjection) -> None:
    schedules = [_created(projection, created_at=_CREATED_AT + timedelta(minutes=minute)) for minute in (0, 1, 1, 2)]
    expected = sorted(schedules, key=lambda item: (item.created_at, item.schedule_id), reverse=True)

    first = projection.list_schedules(ScheduleFilters(), limit=3)
    last = first.items[-1]
    second = projection.list_schedules(ScheduleFilters(), after=(last.created_at, last.schedule_id), limit=3)

    assert first.has_more
    assert not second.has_more
    assert [item.schedule_id for item in (*first.items, *second.items)] == [item.schedule_id for item in expected]
    with pytest.raises(ValueError, match="limit"):
        projection.list_schedules(ScheduleFilters(), limit=0)


def test_the_schedule_list_filters_on_the_configuration_and_the_state(projection: ProductProjection) -> None:
    active = _created(projection)
    paused = _created(projection)
    deleted = _created(projection)
    projection.update_schedule(_changed(paused, state="paused"))
    projection.update_schedule(_changed(deleted, state="deleted"))

    def listed(**filters: object) -> set[str]:
        page = projection.list_schedules(ScheduleFilters.model_validate(filters))
        return {item.schedule_id for item in page.items}

    assert listed() == {active.schedule_id, paused.schedule_id}
    assert listed(state="deleted") == {deleted.schedule_id}
    assert listed(state="paused") == {paused.schedule_id}
    assert listed(config_id=paused.config_id) == {paused.schedule_id}
    assert listed(config_id=deleted.config_id) == set()
    assert listed(config_id=deleted.config_id, state="deleted") == {deleted.schedule_id}


# --- change and state transitions --------------------------------------------------------


def test_a_change_saves_the_settings_and_returns_the_stored_schedule(projection: ProductProjection) -> None:
    schedule = _created(projection)
    later = _CREATED_AT + timedelta(hours=1)
    event = _start_event(schedule, _FIRST_START, outcome="skipped-run-in-progress")

    stored = projection.update_schedule(
        _changed(
            schedule,
            cron="30 3 * * *",
            timezone="Europe/Paris",
            write_when_left_out=True,
            left_out_set_by="admin@example.com",
            left_out_set_at=later,
            owner="admin@example.com",
            updated_at=later,
        ),
        expected_updated_at=schedule.updated_at,
        events=(event,),
    )

    assert stored == _stored(projection, schedule.schedule_id)
    assert (stored.cron, stored.timezone, stored.owner) == ("30 3 * * *", "Europe/Paris", "admin@example.com")
    assert (stored.write_when_left_out, stored.left_out_set_by, stored.left_out_set_at) == (
        True,
        "admin@example.com",
        later,
    )
    assert stored.updated_at == later
    assert [item.event_id for item in _schedule_events(projection, schedule)] == [event.event_id]


@pytest.mark.parametrize("field", ["name", "config_id", "created_at"])
def test_a_change_cannot_set_the_name_the_configuration_or_the_creation_time(
    projection: ProductProjection, field: str
) -> None:
    schedule = _created(projection)
    other = _schedule(projection, created_at=_CREATED_AT + timedelta(days=1))

    with pytest.raises(ValueError, match=field):
        projection.update_schedule(_changed(schedule, **{field: getattr(other, field)}))

    assert _stored(projection, schedule.schedule_id) == schedule


def test_a_change_of_an_unknown_schedule_is_refused(projection: ProductProjection) -> None:
    with pytest.raises(ScheduleNotFoundError):
        projection.update_schedule(_schedule(projection))


def test_a_change_made_from_an_earlier_read_is_refused(projection: ProductProjection) -> None:
    schedule = _created(projection)
    later = _CREATED_AT + timedelta(hours=1)
    projection.update_schedule(_changed(schedule, state="paused", updated_at=later))

    with pytest.raises(ScheduleChangedError):
        projection.update_schedule(_changed(schedule, cron="0 4 * * *"), expected_updated_at=schedule.updated_at)

    assert _stored(projection, schedule.schedule_id).cron == schedule.cron


def test_a_change_keeps_what_the_admission_of_a_start_wrote(projection: ProductProjection) -> None:
    schedule = _created(projection)
    _failed_runs(projection, schedule, 1)

    stored = projection.update_schedule(_changed(schedule, cron="0 4 * * *"))

    assert (stored.last_start_at, stored.consecutive_failures) == (_FIRST_START, 1)


_STOPPED = {"state": "stopped", "state_reason": "owner-not-found"}
_ACTIVE = {"state": "active", "state_reason": None}


@pytest.mark.parametrize(
    "steps",
    [
        pytest.param([{"state": "paused"}, _ACTIVE], id="pause-then-resume"),
        pytest.param([_STOPPED, _ACTIVE], id="stop-then-resume"),
        pytest.param([{"state": "stopped", "state_reason": "approval-required"}, _ACTIVE], id="approval-stop"),
        pytest.param([{"state": "deleted"}], id="delete-active"),
        pytest.param([{"state": "paused"}, {"state": "deleted"}], id="delete-paused"),
        pytest.param([_STOPPED, {"state": "deleted", "state_reason": None}], id="delete-stopped"),
        # A resume of a paused schedule whose reason to stop holds stops it in one write.
        pytest.param([{"state": "paused"}, _STOPPED], id="paused-to-stopped"),
        pytest.param(
            [_STOPPED, {"state": "stopped", "state_reason": "approval-required"}], id="stopped-with-another-reason"
        ),
    ],
)
def test_a_schedule_follows_the_state_transitions_of_the_data_model(
    projection: ProductProjection, steps: list[dict[str, object]]
) -> None:
    schedule = _created(projection)

    for step in steps:
        schedule = projection.update_schedule(_changed(schedule, **step))

    assert schedule.state == steps[-1]["state"]


@pytest.mark.parametrize(
    "steps",
    [
        pytest.param([_STOPPED, {"state": "paused", "state_reason": None}], id="stopped-to-paused"),
    ],
)
def test_a_state_transition_outside_the_data_model_is_refused(
    projection: ProductProjection, steps: list[dict[str, object]]
) -> None:
    schedule = _created(projection)
    schedule = projection.update_schedule(_changed(schedule, **steps[0]))

    with pytest.raises(ScheduleTransitionError):
        projection.update_schedule(_changed(schedule, **steps[1]))

    assert _stored(projection, schedule.schedule_id).state == steps[0]["state"]


def test_a_paused_schedule_is_stopped_with_its_event_in_one_write(projection: ProductProjection) -> None:
    schedule = projection.update_schedule(_changed(_created(projection), state="paused", updated_at=_FIRST_START))
    stopped_at = _FIRST_START + timedelta(minutes=1)
    event = _start_event(schedule, _FIRST_START, outcome="approval-required").model_copy(
        update={"event_type": "infrahub_sync.schedule.stopped", "details": None}
    )

    stored = projection.update_schedule(
        _changed(schedule, state="stopped", state_reason="approval-required", updated_at=stopped_at),
        expected_updated_at=_FIRST_START,
        events=(event,),
    )

    assert (stored.state, stored.state_reason, stored.updated_at) == ("stopped", "approval-required", stopped_at)
    assert [item.event_id for item in _schedule_events(projection, schedule)] == [event.event_id]


def test_a_copy_that_the_model_refuses_is_not_stored_without_secrets(
    projection: ProductProjection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A copy made with ``model_copy`` is not validated; the store validates it again before it writes it."""
    schedule = _created(projection)
    written: list[ScheduleRecord] = []
    monkeypatch.setattr(projection._records, "update_schedule", lambda record, **_: written.append(record))

    with pytest.raises(ValueError, match="stopped"):
        projection.update_schedule(schedule.model_copy(update={"state": "stopped"}))

    assert written == []


def test_a_schedule_stopped_again_for_another_reason_starts_counting_failures_from_zero_when_active(
    projection: ProductProjection,
) -> None:
    """A stop for repeated failures, then for approval, then a change that makes it active: the count starts again."""
    schedule = _created(projection)
    _failed_runs(projection, schedule, 5)
    stopped = _stored(projection, schedule.schedule_id)
    assert (stopped.state_reason, stopped.consecutive_failures) == ("repeated-failures", 5)
    relabelled = projection.update_schedule(_changed(stopped, state_reason="approval-required"))

    active = projection.update_schedule(_changed(relabelled, state="active", state_reason=None))

    assert (active.state, active.consecutive_failures) == ("active", 0)


@pytest.mark.parametrize("state", ["active", "paused", "deleted"])
def test_a_deleted_schedule_accepts_no_action(projection: ProductProjection, state: str) -> None:
    schedule = _created(projection)
    deleted = projection.update_schedule(_changed(schedule, state="deleted"))

    with pytest.raises(ScheduleDeletedError):
        projection.update_schedule(_changed(deleted, state=state))


def test_the_prefect_schedule_is_recorded_without_a_change_of_the_settings(projection: ProductProjection) -> None:
    schedule = _created(projection)

    assert projection.record_schedule_prefect_sync(schedule.schedule_id, prefect_schedule_id="prefect-7", synced=True)
    assert not projection.record_schedule_prefect_sync(f"s-{uuid4().hex}", prefect_schedule_id=None, synced=False)

    stored = _stored(projection, schedule.schedule_id)
    assert (stored.prefect_schedule_id, stored.prefect_synced, stored.updated_at) == ("prefect-7", True, _CREATED_AT)


# --- the admission of a start -------------------------------------------------------------


def test_an_admitted_start_writes_the_run_its_receipt_its_admission_and_its_claimed_execution(
    projection: ProductProjection,
) -> None:
    schedule = _created(projection)
    missed = _start_event(schedule, _FIRST_START - _DAY, outcome="missed-skipped")

    result = _admit(projection, schedule, _FIRST_START, events=(missed,))

    assert result.started
    assert result.cause is None
    assert result.schedule.last_start_at == _FIRST_START
    run = projection.lookup_run(result.run_id or "").value
    assert run is not None
    assert (run.trigger_type, run.schedule_id, run.scheduled_start_at) == (
        "scheduled",
        schedule.schedule_id,
        _FIRST_START,
    )
    assert (run.actor, run.config_id, run.registry_version, run.phase) == (_OWNER, schedule.config_id, 1, "accepted")
    (link,) = run.prefect_executions
    assert (link.flow_run_id, link.purpose, link.attempt) == (f"flow-{run.run_id}", "sync", 1)
    assert (link.claiming_worker_id, link.claimed_at) == (_WORKER_ID, _FIRST_START + timedelta(seconds=1))
    assert link.terminal_at is None
    receipt = projection.lookup_mutation_for_execution(run.run_id, link.flow_run_id, "sync").value
    assert receipt is not None
    assert (receipt.resource_kind, receipt.actor, receipt.state, receipt.run_id) == (
        "run",
        _OWNER,
        "accepted",
        run.run_id,
    )
    assert (
        receipt.key_digest == sha256(f"schedule:{schedule.schedule_id}:{_FIRST_START.isoformat()}".encode()).hexdigest()
    )
    assert projection.lookup_mutation(_OWNER, receipt.key_digest).value == receipt
    assert [item.event_id for item in _schedule_events(projection, schedule)] == [missed.event_id]
    # The run holds its one write admission: an apply of a person on it is refused.
    refused, _created_now = projection.reserve_mutation(_apply_receipt(run.run_id), admit_write=True)
    assert refused.response_body is not None
    assert refused.response_body["error"]["code"] == "run-execution-conflict"
    # The worker that admitted the start owns the execution, and commits its verdict.
    _end(projection, run.run_id, state="completed", outcome="succeeded", phase="applied", run_outcome="applied")


def _apply_receipt(run_id: str) -> MutationReceipt:
    return MutationReceipt(
        receipt_id=f"apply.{run_id}",
        actor=_OWNER,
        key_digest=sha256(f"apply:{run_id}".encode()).hexdigest(),
        operation="apply",
        target_run_id=run_id,
        request_fingerprint=sha256(f"apply-request:{run_id}".encode()).hexdigest(),
        reason="apply the scheduled run",
        resource_id=run_id,
        run_id=run_id,
        prefect_key=sha256(f"prefect:{run_id}".encode()).hexdigest(),
        created_at=_FIRST_START,
        updated_at=_FIRST_START,
    )


def test_two_admissions_of_one_start_create_one_run(projection: ProductProjection) -> None:
    schedule = _created(projection)
    first = _admit(projection, schedule, _FIRST_START)
    late_event = _start_event(schedule, _FIRST_START, outcome="missed-started-late")

    with pytest.raises(DuplicateRunError, match=first.run_id or "unknown"):
        projection.admit_scheduled_start(
            schedule.schedule_id,
            # The same instant, written with another offset.
            _FIRST_START.astimezone(timezone(timedelta(hours=2))),
            "flow-second",
            _WORKER_ID,
            _FIRST_START + timedelta(seconds=2),
            ScheduledStartRun(
                run=_scheduled_run(projection, schedule, _FIRST_START), events=(late_event,), last_start_at=None
            ),
        )

    runs = projection.list_runs(RunFilters(schedule_id=schedule.schedule_id)).items
    assert [run.run_id for run in runs] == [first.run_id]
    assert _schedule_events(projection, schedule) == ()


def test_two_concurrent_admissions_of_one_start_create_one_run(projection: ProductProjection) -> None:
    schedule = _created(projection)
    barrier = Barrier(2)

    def admit(name: str) -> str:
        run = _scheduled_run(projection, schedule, _FIRST_START)
        decision = ScheduledStartRun(run=run, last_start_at=None)
        barrier.wait(timeout=10)
        try:
            result = projection.admit_scheduled_start(
                schedule.schedule_id, _FIRST_START, f"flow-{name}", _WORKER_ID, _FIRST_START, decision
            )
        except DuplicateRunError:
            return "duplicate"
        assert result.run_id == run.run_id
        return "started"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = sorted(pool.map(admit, ("one", "two")))

    assert outcomes == ["duplicate", "started"]
    assert len(projection.list_runs(RunFilters(schedule_id=schedule.schedule_id)).items) == 1


def test_a_start_that_does_not_run_writes_only_its_events(projection: ProductProjection) -> None:
    schedule = _created(projection)
    skipped = _start_event(schedule, _FIRST_START, outcome="skipped-run-in-progress", run_id=None)

    result = _skip(projection, schedule, _FIRST_START, events=(skipped,))

    assert (result.started, result.cause, result.run_id) == (False, "skipped-run-in-progress", None)
    assert result.schedule.last_start_at == _FIRST_START
    assert projection.list_runs(RunFilters(schedule_id=schedule.schedule_id)).items == ()
    (event,) = _schedule_events(projection, schedule)
    assert event == skipped
    assert event.details == EventDetails(start_at=_FIRST_START)
    # A start that does not run is not a failed run (FR-015).
    assert result.schedule.consecutive_failures == 0


def test_a_start_that_was_recorded_is_not_recorded_again(projection: ProductProjection) -> None:
    schedule = _created(projection)
    _skip(projection, schedule, _FIRST_START + _DAY, events=(_start_event(schedule, _FIRST_START + _DAY, outcome="x"),))

    for start in (_FIRST_START + _DAY, _FIRST_START):
        with pytest.raises(ScheduledStartAlreadyRecordedError):
            _skip(projection, schedule, start, events=(_start_event(schedule, start, outcome="skipped-again"),))

    assert len(_schedule_events(projection, schedule)) == 1
    assert _stored(projection, schedule.schedule_id).last_start_at == _FIRST_START + _DAY


@pytest.mark.parametrize(
    "state", [{"state": "paused"}, {"state": "stopped", "state_reason": "owner-not-found"}, {"state": "deleted"}]
)
def test_a_schedule_that_is_not_active_creates_no_run_and_records_nothing(
    projection: ProductProjection, state: dict[str, object]
) -> None:
    schedule = _created(projection)
    projection.update_schedule(_changed(schedule, **state))

    result = _admit(
        projection, schedule, _FIRST_START, events=(_start_event(schedule, _FIRST_START, outcome="missed-skipped"),)
    )

    assert (result.started, result.cause) == (False, "schedule-not-active")
    assert result.schedule.last_start_at is None
    assert projection.list_runs(RunFilters(schedule_id=schedule.schedule_id)).items == ()
    assert _schedule_events(projection, schedule) == ()


def test_an_admission_of_an_unknown_schedule_is_refused(projection: ProductProjection) -> None:
    with pytest.raises(ScheduleNotFoundError):
        _admit(projection, _schedule(projection), _FIRST_START)


def test_a_decision_made_from_an_earlier_last_start_is_refused(projection: ProductProjection) -> None:
    schedule = _created(projection)
    _skip(projection, schedule, _FIRST_START)
    start = _FIRST_START + _DAY

    with pytest.raises(ScheduleChangedError):
        projection.admit_scheduled_start(
            schedule.schedule_id,
            start,
            "flow-stale",
            _WORKER_ID,
            start,
            ScheduledStartNotRun(
                cause="missed", last_start_at=None, events=(_start_event(schedule, start, outcome="missed-skipped"),)
            ),
        )

    assert _schedule_events(projection, schedule) == ()
    assert _stored(projection, schedule.schedule_id).last_start_at == _FIRST_START


def test_a_decision_made_before_a_change_of_the_schedule_is_refused(projection: ProductProjection) -> None:
    """A change saved after the decision read the schedule is not admitted under the earlier settings."""
    schedule = _created(projection)
    projection.update_schedule(
        schedule.model_copy(update={"cron": "30 2 * * *", "updated_at": schedule.updated_at + _DAY}),
        expected_updated_at=schedule.updated_at,
    )

    with pytest.raises(ScheduleChangedError):
        projection.admit_scheduled_start(
            schedule.schedule_id,
            _FIRST_START,
            "flow-stale",
            _WORKER_ID,
            _FIRST_START,
            ScheduledStartNotRun(
                cause="missed",
                schedule_updated_at=schedule.updated_at,
                events=(_start_event(schedule, _FIRST_START, outcome="missed-skipped"),),
            ),
        )

    assert _schedule_events(projection, schedule) == ()
    assert _stored(projection, schedule.schedule_id).last_start_at is None


# --- the start that waits under the `queue` policy (research R7, FR-016) -----------------


def test_a_queued_start_waits_and_its_later_admission_runs_it(projection: ProductProjection) -> None:
    schedule = _created(projection, overlap_policy="queue")
    later = _FIRST_START + _DAY

    queued = _skip(projection, schedule, _FIRST_START, cause="queued")
    again = _skip(projection, schedule, _FIRST_START, cause="queued")
    further = _skip(projection, schedule, later, cause="skipped-start-waiting")
    ran = _admit(projection, schedule, _FIRST_START)

    assert queued.schedule.waiting_start_at == _FIRST_START
    assert again.schedule.waiting_start_at == _FIRST_START
    assert (further.schedule.waiting_start_at, further.schedule.last_start_at) == (_FIRST_START, later)
    assert ran.started
    # The waiting start ran: the wait ends, and the last recorded start does not move back.
    assert (ran.schedule.waiting_start_at, ran.schedule.last_start_at) == (None, later)
    (run,) = projection.list_runs(RunFilters(schedule_id=schedule.schedule_id)).items
    assert run.scheduled_start_at == _FIRST_START


def test_any_other_decision_on_the_waiting_start_ends_the_wait(projection: ProductProjection) -> None:
    schedule = _created(projection, overlap_policy="queue")
    _skip(projection, schedule, _FIRST_START, cause="queued")

    result = _skip(projection, schedule, _FIRST_START, cause="failed")

    assert (result.cause, result.schedule.waiting_start_at) == ("failed", None)
    with pytest.raises(ScheduledStartAlreadyRecordedError):
        _skip(projection, schedule, _FIRST_START, cause="queued")


def test_a_start_refused_for_approval_stops_the_schedule_in_the_admission(projection: ProductProjection) -> None:
    """The worker's check of research R6: the start creates no run, and the schedule stops with its events."""
    schedule = _created(projection, overlap_policy="queue", prefect_synced=True)
    _skip(projection, schedule, _FIRST_START, cause="queued")
    start = _FIRST_START + _DAY
    skipped = _start_event(schedule, start, outcome="skipped-approval-required")
    stopped = _start_event(schedule, start, outcome="approval-required").model_copy(
        update={"event_type": "infrahub_sync.schedule.stopped", "details": None}
    )

    result = _skip(projection, schedule, start, cause="approval-required", events=(skipped, stopped))

    assert (result.started, result.cause) == (False, "approval-required")
    stored = _stored(projection, schedule.schedule_id)
    assert (stored.state, stored.state_reason, stored.prefect_synced) == ("stopped", "approval-required", False)
    assert (stored.waiting_start_at, stored.last_start_at) == (None, start)
    assert stored.updated_at == start + timedelta(seconds=1)
    assert projection.list_runs(RunFilters(schedule_id=schedule.schedule_id)).items == ()
    # The two events have one time, so their order is not part of the check.
    assert {item.event_id for item in _schedule_events(projection, schedule)} == {skipped.event_id, stopped.event_id}


def test_a_decision_made_from_another_waiting_start_is_refused(projection: ProductProjection) -> None:
    schedule = _created(projection, overlap_policy="queue")
    _skip(projection, schedule, _FIRST_START, cause="queued")
    start = _FIRST_START + _DAY

    with pytest.raises(ScheduleChangedError):
        projection.admit_scheduled_start(
            schedule.schedule_id,
            start,
            "flow-stale",
            _WORKER_ID,
            start,
            ScheduledStartNotRun(cause="queued", last_start_at=_FIRST_START, waiting_start_at=None),
        )

    assert _stored(projection, schedule.schedule_id).waiting_start_at == _FIRST_START


@pytest.mark.parametrize(
    ("changes", "ends"),
    [
        pytest.param({"state": "paused"}, True, id="paused"),
        pytest.param({"state": "stopped", "state_reason": "owner-not-found"}, True, id="stopped"),
        pytest.param({"state": "deleted"}, True, id="deleted"),
        pytest.param({"cron": "0 3 * * *"}, True, id="cadence"),
        pytest.param({"timezone": "Europe/Paris"}, True, id="time-zone"),
        pytest.param({"overlap_policy": "reject"}, True, id="overlap-policy"),
        pytest.param({"owner": "administrator"}, False, id="owner"),
        pytest.param({"start_late_by_at_most_seconds": 600}, False, id="late-start-duration"),
    ],
)
def test_a_change_that_stops_the_waiting_start_from_running_ends_the_wait(
    projection: ProductProjection,
    changes: dict[str, object],
    ends: bool,  # noqa: FBT001
) -> None:
    """The waiting flow run creates no run after these changes, so the wait must not block later starts."""
    schedule = _created(projection, overlap_policy="queue")
    _skip(projection, schedule, _FIRST_START, cause="queued")
    stored = _stored(projection, schedule.schedule_id)

    updated = projection.update_schedule(_changed(stored, **changes))

    assert updated.waiting_start_at == (None if ends else _FIRST_START)


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"actor": "someone-else@example.com"}, id="another-actor"),
        pytest.param({"package_checksum": "f" * 64}, id="another-package"),
        pytest.param({"scheduled_start_at": _FIRST_START + _DAY}, id="another-start"),
        pytest.param({"schedule_id": f"s-{'0' * 32}"}, id="another-schedule"),
    ],
)
def test_a_run_that_does_not_match_the_stored_schedule_is_refused(
    projection: ProductProjection, overrides: dict[str, object]
) -> None:
    schedule = _created(projection)

    with pytest.raises(ScheduleChangedError):
        _admit(projection, schedule, _FIRST_START, run=_scheduled_run(projection, schedule, _FIRST_START, **overrides))

    assert projection.list_runs(RunFilters(schedule_id=schedule.schedule_id)).items == ()
    assert _stored(projection, schedule.schedule_id).last_start_at is None


def test_a_run_of_a_version_that_is_not_registered_is_refused(projection: ProductProjection) -> None:
    schedule = _created(projection)
    run = _scheduled_run(projection, schedule, _FIRST_START)
    projection.update_schedule(_changed(schedule, registry_version=2))

    with pytest.raises(ConfigurationNotFoundError):
        _admit(
            projection,
            schedule,
            _FIRST_START,
            run=ProductRun.model_validate(
                {**run.model_dump(), "registry_version": 2, "configuration_reference": f"{schedule.config_id}@2"}
            ),
        )

    assert projection.list_runs(RunFilters(schedule_id=schedule.schedule_id)).items == ()


def test_the_decision_must_say_branch_review_for_a_schedule_with_branch_review(projection: ProductProjection) -> None:
    schedule = _created(projection, write_target="branch-review")

    with pytest.raises(ScheduleChangedError):
        _admit(projection, schedule, _FIRST_START)


def test_one_start_of_a_schedule_holds_one_run_in_the_run_table(projection: ProductProjection) -> None:
    schedule = _created(projection)
    projection.create_run(_scheduled_run(projection, schedule, _FIRST_START))

    with pytest.raises(DuplicateRunError):
        projection.create_run(_scheduled_run(projection, schedule, _FIRST_START))

    projection.create_run(_scheduled_run(projection, schedule, _FIRST_START + _DAY))
    assert len(projection.list_runs(RunFilters(schedule_id=schedule.schedule_id)).items) == 2


# --- schedule branches --------------------------------------------------------------------


def _branch_schedule(projection: ProductProjection) -> ScheduleRecord:
    return _created(projection, write_target="branch-review")


def _branch_run(projection: ProductProjection, schedule: ScheduleRecord, day: int, **facts: Any) -> ScheduleBranch:  # noqa: ANN401
    result = _admit(projection, schedule, _FIRST_START + day * _DAY, branch=f"sync/nightly-{day}", **facts)
    assert result.run_id is not None
    _end(projection, result.run_id, state="completed", outcome="succeeded", phase="applied", run_outcome="applied")
    row = projection.lookup_schedule_branch(result.run_id).value
    assert row is not None
    return row


def test_a_run_with_branch_review_has_a_pending_branch_row(projection: ProductProjection) -> None:
    schedule = _branch_schedule(projection)

    result = _admit(projection, schedule, _FIRST_START, branch="sync/nightly-20261118-abc123")

    run = projection.lookup_run(result.run_id or "").value
    assert run is not None
    assert run.destination_branch == "sync/nightly-20261118-abc123"
    assert projection.schedule_branches(schedule.schedule_id) == (
        ScheduleBranch(
            schedule_id=schedule.schedule_id,
            run_id=run.run_id,
            branch="sync/nightly-20261118-abc123",
            state="pending",
            created_at=_FIRST_START + timedelta(seconds=1),
        ),
    )
    assert result.earlier_branches == ()
    assert not projection.lookup_schedule_branch("run-unknown").available


def test_an_open_branch_row_names_its_proposed_change() -> None:
    with pytest.raises(ValidationError, match="proposed change"):
        ScheduleBranch(
            schedule_id="s-" + "3f" * 16,
            run_id="run-open-without-change",
            branch="sync/nightly-20261118-abc123",
            state="open",
            created_at=_FIRST_START,
        )


def test_a_run_that_writes_to_the_default_branch_has_no_branch_row(projection: ProductProjection) -> None:
    schedule = _created(projection)

    _admit(projection, schedule, _FIRST_START)

    assert projection.schedule_branches(schedule.schedule_id) == ()


def test_a_branch_row_follows_its_states(projection: ProductProjection) -> None:
    schedule = _branch_schedule(projection)
    first = _branch_run(projection, schedule, 0)
    second = _branch_run(projection, schedule, 1)
    third = _branch_run(projection, schedule, 2)

    opened = projection.update_schedule_branch(first.model_copy(update={"state": "open", "proposed_change_id": "pc-1"}))
    replaced = projection.update_schedule_branch(
        opened.model_copy(update={"state": "replaced", "replaced_by_run_id": second.run_id})
    )
    removed = projection.update_schedule_branch(second.model_copy(update={"state": "removed"}))
    kept = projection.update_schedule_branch(third.model_copy(update={"state": "kept"}))

    assert (opened.state, opened.proposed_change_id) == ("open", "pc-1")
    assert (replaced.state, replaced.replaced_by_run_id, replaced.proposed_change_id) == (
        "replaced",
        second.run_id,
        "pc-1",
    )
    assert [row.state for row in projection.schedule_branches(schedule.schedule_id)] == ["replaced", "removed", "kept"]
    assert projection.schedule_branches(schedule.schedule_id, states=("pending", "open")) == ()
    assert projection.schedule_branches(schedule.schedule_id, states=("kept", "removed")) == (removed, kept)


@pytest.mark.parametrize("final", ["removed", "replaced", "kept"])
def test_a_branch_row_in_a_final_state_accepts_no_change(projection: ProductProjection, final: str) -> None:
    schedule = _branch_schedule(projection)
    row = _branch_run(projection, schedule, 0)
    replaced_by = row.run_id if final == "replaced" else None
    stored = projection.update_schedule_branch(
        row.model_copy(update={"state": final, "replaced_by_run_id": replaced_by})
    )

    with pytest.raises(ScheduleTransitionError):
        projection.update_schedule_branch(
            stored.model_copy(update={"state": "open", "replaced_by_run_id": None, "proposed_change_id": "pc-1"})
        )

    assert projection.lookup_schedule_branch(row.run_id).value == stored


def test_a_branch_row_cannot_return_to_pending_or_change_its_branch(projection: ProductProjection) -> None:
    schedule = _branch_schedule(projection)
    row = _branch_run(projection, schedule, 0)
    opened = projection.update_schedule_branch(row.model_copy(update={"state": "open", "proposed_change_id": "pc-1"}))

    with pytest.raises(ScheduleTransitionError):
        projection.update_schedule_branch(opened.model_copy(update={"state": "pending"}))
    with pytest.raises(ValueError, match="branch"):
        projection.update_schedule_branch(opened.model_copy(update={"branch": "sync/other"}))
    with pytest.raises(ScheduleNotFoundError):
        projection.update_schedule_branch(opened.model_copy(update={"run_id": "run-unknown"}))


def _review_event(schedule: ScheduleRecord, run_id: str, outcome: str) -> AuditEvent:
    """An event of a proposed change of a scheduled run."""
    event_type = {"opened": "proposed_change_opened", "replaced": "proposed_change_replaced"}.get(
        outcome, "proposed_change_kept"
    )
    return AuditEvent(
        event_id=f"a-{uuid4().hex}",
        run_id=run_id,
        actor=schedule.owner,
        operation="sync",
        reason=None,
        outcome=outcome,
        created_at=_FIRST_START + timedelta(days=10),
        event_type=f"infrahub_sync.schedule.{event_type}",
        config_id=schedule.config_id,
        registry_version=schedule.registry_version,
        related=(
            RelatedResource(kind="account", id=schedule.owner),
            RelatedResource(kind="schedule", id=schedule.schedule_id),
        ),
        details=EventDetails(branch="sync/nightly-5"),
    )


def test_a_committed_result_that_removed_the_branch_moves_the_row_to_removed(projection: ProductProjection) -> None:
    """The first part of ``branch_review`` is saved with the stage result (data-model.md)."""
    schedule = _branch_schedule(projection)
    result = _admit(projection, schedule, _FIRST_START, branch="sync/nightly-0")
    assert result.run_id is not None
    at = _FIRST_START + timedelta(minutes=5)

    assert projection.commit_claimed_execution(
        result.run_id,
        f"flow-{result.run_id}",
        worker_id=_WORKER_ID,
        terminal_at=at,
        terminal_state="completed",
        terminal_outcome="succeeded",
        writeback=ExecutionFinishWriteback(
            phase="applied",
            outcome="no-change",
            finished_at=at,
            summary={},
            results={"branch_review": {"branch": "sync/nightly-0", "branch_state": "removed"}},
        ),
    )

    row = projection.lookup_schedule_branch(result.run_id).value
    assert row is not None
    assert row.state == "removed"


def test_the_outcome_of_a_branch_review_moves_the_rows_and_merges_the_result(projection: ProductProjection) -> None:
    """The second part of ``branch_review`` and its events are saved with one call; a handled row is skipped."""
    schedule = _branch_schedule(projection)

    def opened(day: int) -> ScheduleBranch:
        row = _branch_run(projection, schedule, day)
        return projection.update_schedule_branch(
            row.model_copy(update={"state": "open", "proposed_change_id": f"pc-{day}"})
        )

    replaced, pending, merged, approved = opened(0), _branch_run(projection, schedule, 1), opened(2), opened(3)
    gone = projection.update_schedule_branch(
        _branch_run(projection, schedule, 4).model_copy(update={"state": "removed"})
    )
    own = _branch_run(projection, schedule, 5)
    projection.record_results(own.run_id, {"branch_review": {"branch": own.branch, "branch_state": "written"}})
    opened_event = _review_event(schedule, own.run_id, "opened")
    by_run = {
        row.run_id: (_review_event(schedule, own.run_id, outcome),)
        for row, outcome in (
            (replaced, "replaced"),
            (pending, "replaced"),
            (gone, "replaced"),
            (merged, "kept-merged"),
            (approved, "kept-approved"),
        )
    }

    saved = projection.record_branch_review_outcome(
        BranchReviewOutcome(
            run_id=own.run_id,
            proposed_change_id="pc-5",
            replaced=(replaced.run_id, pending.run_id, gone.run_id),
            kept=(
                BranchReviewKept(run_id=merged.run_id, cause="merged"),
                BranchReviewKept(run_id=approved.run_id, cause="approved"),
            ),
        ),
        events=(opened_event,),
        earlier_events=by_run,
    )

    assert saved.replaced == (replaced.run_id, pending.run_id)
    states = {
        row.run_id: (row.state, row.replaced_by_run_id) for row in projection.schedule_branches(schedule.schedule_id)
    }
    assert states == {
        replaced.run_id: ("replaced", own.run_id),
        pending.run_id: ("replaced", own.run_id),
        merged.run_id: ("kept", None),
        approved.run_id: ("open", None),
        gone.run_id: ("removed", None),
        own.run_id: ("open", None),
    }
    assert projection.lookup_schedule_branch(own.run_id).value.proposed_change_id == "pc-5"  # ty: ignore[unresolved-attribute]
    stored = projection.lookup_run(own.run_id).value
    assert stored is not None
    assert stored.results["branch_review"] == {
        "branch": own.branch,
        "branch_state": "written",
        "proposed_change_id": "pc-5",
        "replaced": [replaced.run_id, pending.run_id],
        "kept": [{"run_id": merged.run_id, "cause": "merged"}, {"run_id": approved.run_id, "cause": "approved"}],
        "failure": None,
    }
    written = {event.event_id for event in projection.audit_events()}
    assert opened_event.event_id in written
    assert by_run[gone.run_id][0].event_id not in written, "A row another run handled writes no event."
    assert all(by_run[row.run_id][0].event_id in written for row in (replaced, pending, merged, approved))


def test_an_own_row_that_another_run_replaced_stays_replaced(projection: ProductProjection) -> None:
    """Research R15: a later run replaced the row while this run opened its proposed change."""
    schedule = _branch_schedule(projection)
    own = _branch_run(projection, schedule, 0)
    later = _branch_run(projection, schedule, 1)
    replaced = projection.update_schedule_branch(
        own.model_copy(update={"state": "replaced", "replaced_by_run_id": later.run_id})
    )

    saved = projection.record_branch_review_outcome(BranchReviewOutcome(run_id=own.run_id, proposed_change_id="pc-0"))

    assert saved.proposed_change_id == "pc-0"
    assert projection.lookup_schedule_branch(own.run_id).value == replaced


def test_a_failed_proposed_change_leaves_the_row_pending(projection: ProductProjection) -> None:
    schedule = _branch_schedule(projection)
    own = _branch_run(projection, schedule, 0)

    saved = projection.record_branch_review_outcome(
        BranchReviewOutcome(run_id=own.run_id, failure="proposed_change_open_failed")
    )

    assert saved.failure == "proposed_change_open_failed"
    assert projection.lookup_schedule_branch(own.run_id).value == own
    with pytest.raises(ScheduleNotFoundError):
        projection.record_branch_review_outcome(BranchReviewOutcome(run_id="run-unknown"))


def test_an_admission_returns_the_earlier_branches_and_keeps_those_a_person_closed(
    projection: ProductProjection,
) -> None:
    schedule = _branch_schedule(projection)
    merged = _branch_run(projection, schedule, 0)
    still_open = projection.update_schedule_branch(
        _branch_run(projection, schedule, 1).model_copy(update={"state": "open", "proposed_change_id": "pc-2"})
    )

    result = _admit(
        projection, schedule, _FIRST_START + 2 * _DAY, branch="sync/nightly-2", kept_run_ids=(merged.run_id,)
    )

    assert result.earlier_branches == (still_open,)
    kept = projection.lookup_schedule_branch(merged.run_id).value
    assert kept is not None
    assert kept.state == "kept"
    with pytest.raises(ScheduleChangedError):
        _admit(projection, schedule, _FIRST_START + 3 * _DAY, branch="sync/nightly-3", kept_run_ids=(merged.run_id,))
    assert len(projection.schedule_branches(schedule.schedule_id)) == 3


def test_a_start_that_does_not_run_also_keeps_the_branches_a_person_closed(projection: ProductProjection) -> None:
    schedule = _branch_schedule(projection)
    merged = _branch_run(projection, schedule, 0)

    result = _skip(
        projection, schedule, _FIRST_START + _DAY, cause="skipped-approved-change-open", kept_run_ids=(merged.run_id,)
    )

    assert result.earlier_branches == ()
    assert [row.state for row in projection.schedule_branches(schedule.schedule_id)] == ["kept"]


# --- failed runs in a row (FR-032) --------------------------------------------------------


def test_the_fifth_failed_run_in_a_row_stops_the_schedule_with_its_event(projection: ProductProjection) -> None:
    schedule = _created(projection, prefect_schedule_id="prefect-1")
    projection.record_schedule_prefect_sync(schedule.schedule_id, prefect_schedule_id="prefect-1", synced=True)

    run_ids = _failed_runs(projection, schedule, MAX_CONSECUTIVE_FAILURES - 1)
    assert _stored(projection, schedule.schedule_id).state == "active"
    assert _schedule_events(projection, schedule) == ()
    run_ids += _failed_runs(projection, schedule, 1, first=MAX_CONSECUTIVE_FAILURES - 1)

    stopped = _stored(projection, schedule.schedule_id)
    assert (stopped.state, stopped.state_reason, stopped.consecutive_failures) == ("stopped", "repeated-failures", 5)
    # The Prefect schedule no longer matches the record, until the Sync API deactivates it.
    assert not stopped.prefect_synced
    assert stopped.updated_at == _FIRST_START + timedelta(minutes=5)
    (event,) = _schedule_events(projection, schedule)
    assert (event.event_type, event.outcome, event.run_id) == (
        SCHEDULE_STOPPED_EVENT_TYPE,
        "repeated-failures",
        run_ids[-1],
    )
    assert (event.actor, event.operation, event.details) == (_OWNER, SCHEDULE_START_OPERATION, None)
    assert set(event.related) == {
        RelatedResource(kind="account", id=_OWNER),
        RelatedResource(kind="config", id=schedule.config_id),
        RelatedResource(kind="config_version", id=f"{schedule.config_id}@1"),
        RelatedResource(kind="schedule", id=schedule.schedule_id),
    }
    assert event.event_id in (projection.lookup_run(run_ids[-1]).value or _missing()).audit_links
    # A stopped schedule creates no run.
    assert _admit(projection, schedule, _FIRST_START + 9 * _DAY).cause == "schedule-not-active"


def _missing() -> ProductRun:
    msg = "the run is not stored"
    raise AssertionError(msg)


def test_a_resume_of_a_schedule_stopped_for_repeated_failures_sets_the_count_to_zero(
    projection: ProductProjection,
) -> None:
    schedule = _created(projection)
    _failed_runs(projection, schedule, MAX_CONSECUTIVE_FAILURES)
    stopped = _stored(projection, schedule.schedule_id)

    resumed = projection.update_schedule(_changed(stopped, state="active", state_reason=None))

    assert (resumed.state, resumed.state_reason, resumed.consecutive_failures) == ("active", None, 0)


@pytest.mark.parametrize(
    ("verdict", "expected"),
    [
        pytest.param(
            {"state": "completed", "outcome": "succeeded", "phase": "applied", "run_outcome": "applied"},
            0,
            id="succeeded",
        ),
        pytest.param(
            {"state": "completed", "outcome": "succeeded", "phase": "planned", "run_outcome": "no-change"},
            0,
            id="no-change",
        ),
        pytest.param(
            {
                "state": "completed",
                "outcome": "succeeded",
                "phase": "planned",
                "run_outcome": "left-out-review-required",
                "release_write_admission": True,
            },
            2,
            id="stopped-for-a-review-of-records-left-out",
        ),
        pytest.param({}, 3, id="failed"),
        pytest.param(
            {"state": "interrupted", "outcome": "ambiguous", "phase": "interrupted", "run_outcome": "ambiguous"},
            3,
            id="ambiguous",
        ),
    ],
)
def test_the_end_of_a_scheduled_run_sets_the_count_of_failed_runs(
    projection: ProductProjection, verdict: dict[str, Any], expected: int
) -> None:
    schedule = _created(projection)
    _failed_runs(projection, schedule, 2)
    result = _admit(projection, schedule, _FIRST_START + 2 * _DAY)

    _end(projection, result.run_id or "", **verdict)

    stored = _stored(projection, schedule.schedule_id)
    assert (stored.consecutive_failures, stored.state) == (expected, "active")


def test_an_interrupted_scheduled_run_counts_as_failed(projection: ProductProjection) -> None:
    schedule = _created(projection)
    run_ids = _failed_runs(projection, schedule, MAX_CONSECUTIVE_FAILURES - 1)
    result = _admit(projection, schedule, _FIRST_START + 5 * _DAY)
    run_id = result.run_id or ""

    assert projection.interrupt_execution(run_id, f"flow-{run_id}", terminal_at=_FIRST_START + 6 * _DAY)

    stored = _stored(projection, schedule.schedule_id)
    assert (stored.state, stored.state_reason, stored.consecutive_failures) == ("stopped", "repeated-failures", 5)
    (event,) = _schedule_events(projection, schedule)
    assert (event.run_id, event.created_at) == (run_id, _FIRST_START + 6 * _DAY)
    assert run_id not in run_ids


@pytest.mark.parametrize(
    ("decision", "expected"),
    [
        pytest.param({"state": "waiting-check"}, 2, id="automatic-resume-waits"),
        pytest.param({"state": "ended", "decision": "no-resume", "reason": "not-resumable"}, 3, id="no-resume"),
    ],
)
def test_an_interrupted_scheduled_run_with_an_automatic_resume_counts_later(
    projection: ProductProjection, decision: dict[str, object], expected: int
) -> None:
    """While its automatic resume row is not ended, the interruption leaves the count as it is (spec 030, R11)."""
    schedule = _created(projection)
    _failed_runs(projection, schedule, 2)
    result = _admit(projection, schedule, _FIRST_START + 2 * _DAY)
    run_id = result.run_id or ""
    flow_run_id = f"flow-{run_id}"
    at = _FIRST_START + 3 * _DAY
    record = AutomaticResume.model_validate(
        {
            "run_id": run_id,
            "interrupted_flow_run_id": flow_run_id,
            "cause": "rule-6",
            "decided_at": at,
            "updated_at": at,
            **decision,
        }
    )

    assert projection.interrupt_execution(run_id, flow_run_id, terminal_at=at, automatic_resume=record)

    stored = _stored(projection, schedule.schedule_id)
    assert (stored.consecutive_failures, stored.state) == (expected, "active")


def test_a_scheduled_run_that_a_person_cancelled_does_not_change_the_count(projection: ProductProjection) -> None:
    schedule = _created(projection)
    _failed_runs(projection, schedule, MAX_CONSECUTIVE_FAILURES - 1)
    result = _admit(projection, schedule, _FIRST_START + 5 * _DAY)
    run_id = result.run_id or ""
    flow_run_id = f"flow-{run_id}"
    at = _FIRST_START + 5 * _DAY + timedelta(minutes=1)
    cancel = MutationReceipt.model_validate(
        {**_apply_receipt(run_id).model_dump(), "receipt_id": f"cancel.{run_id}", "operation": "cancel"}
    )
    projection.reserve_mutation(cancel)
    assert projection.claim_mutation(cancel.receipt_id)
    assert projection.request_execution_cancellation(
        run_id,
        flow_run_id,
        requested_at=at,
        recovery_deadline_at=at + timedelta(seconds=30),
        recovery_seconds=30,
        expected_latest_position=0,
        receipt_id=cancel.receipt_id,
    )

    # The cancellation of a claimed write ends the run as ambiguous, with a stored refusal.
    assert not projection.acknowledge_execution_cancellation(
        run_id,
        flow_run_id,
        acknowledged_at=at + timedelta(seconds=1),
        response_status=202,
        response_body={"run": {"run_id": run_id}, "orchestration": []},
    )

    run = projection.lookup_run(run_id).value or _missing()
    assert (run.phase, run.outcome) == ("interrupted", "ambiguous")
    stored = _stored(projection, schedule.schedule_id)
    assert (stored.consecutive_failures, stored.state) == (MAX_CONSECUTIVE_FAILURES - 1, "active")


def test_a_paused_schedule_is_not_stopped_by_a_failed_run(projection: ProductProjection) -> None:
    schedule = _created(projection)
    _failed_runs(projection, schedule, MAX_CONSECUTIVE_FAILURES - 1)
    result = _admit(projection, schedule, _FIRST_START + 5 * _DAY)
    projection.update_schedule(_changed(_stored(projection, schedule.schedule_id), state="paused"))

    _end(projection, result.run_id or "")

    stored = _stored(projection, schedule.schedule_id)
    assert (stored.state, stored.consecutive_failures) == ("paused", 5)
    assert _schedule_events(projection, schedule) == ()


def test_a_stage_that_a_person_starts_on_a_scheduled_run_does_not_change_the_count(
    projection: ProductProjection,
) -> None:
    schedule = _created(projection)
    _failed_runs(projection, schedule, 1)
    result = _admit(projection, schedule, _FIRST_START + _DAY)
    run_id = result.run_id or ""
    _end(
        projection,
        run_id,
        state="completed",
        outcome="succeeded",
        phase="planned",
        run_outcome="left-out-review-required",
        release_write_admission=True,
    )
    apply = _apply_receipt(run_id)
    reserved, _created_now = projection.reserve_mutation(apply, admit_write=True)
    assert reserved.state != "accepted"
    link = PrefectExecutionLink(flow_run_id="flow-apply", purpose="apply", attempt=1, submitted_at=_FIRST_START + _DAY)
    projection.add_prefect_execution(run_id, link, receipt_id=apply.receipt_id)
    assert projection.claim_execution(run_id, "flow-apply", worker_id=_WORKER_ID, claimed_at=_FIRST_START + _DAY)

    assert projection.commit_claimed_execution(
        run_id,
        "flow-apply",
        worker_id=_WORKER_ID,
        terminal_at=_FIRST_START + _DAY + timedelta(minutes=9),
        terminal_state="failed",
        terminal_outcome="failed",
        writeback=ExecutionFinishWriteback(
            phase="failed",
            outcome="failed",
            finished_at=_FIRST_START + _DAY + timedelta(minutes=9),
            summary={},
            results={},
        ),
    )

    assert _stored(projection, schedule.schedule_id).consecutive_failures == 1


# --- which run of a configuration is in progress (research R7) ------------------------------

_LIMIT = timedelta(seconds=300)


def _manual_run(
    projection: ProductProjection, schedule: ScheduleRecord, name: str
) -> tuple[ProductRun, MutationReceipt]:
    """Reserve a `plan` of a person on the schedule's configuration: the run and its unresolved receipt."""
    run = ProductRun.model_validate(
        {
            **_scheduled_run(projection, schedule, _FIRST_START).model_dump(),
            "run_id": f"run-{name}-{uuid4().hex[:8]}",
            "operation": "plan",
            "trigger_type": "manual",
            "schedule_id": None,
            "scheduled_start_at": None,
        }
    )
    receipt = MutationReceipt.model_validate(
        {**_apply_receipt(run.run_id).model_dump(), "receipt_id": f"plan.{run.run_id}", "operation": "plan"}
    )
    reserved, created = projection.reserve_mutation(receipt, run=run)
    assert created
    return run, reserved


def _submitted(projection: ProductProjection, run: ProductRun, receipt: MutationReceipt) -> str:
    flow_run_id = f"flow-{run.run_id}"
    link = PrefectExecutionLink(flow_run_id=flow_run_id, purpose="plan", attempt=1, submitted_at=_FIRST_START)
    projection.add_prefect_execution(run.run_id, link, receipt_id=receipt.receipt_id)
    projection.complete_mutation(
        receipt.receipt_id, response_status=202, response_body={"accepted": True}, flow_run_id=flow_run_id
    )
    return flow_run_id


def _in_progress(
    projection: ProductProjection, schedule: ScheduleRecord, *, after: timedelta = timedelta(0)
) -> str | None:
    return projection.run_in_progress(schedule.config_id, now=_FIRST_START + after, receipt_age_limit=_LIMIT)


def test_no_run_of_a_configuration_is_in_progress_before_any_run(projection: ProductProjection) -> None:
    assert _in_progress(projection, _created(projection)) is None


def test_a_run_with_an_execution_that_has_not_ended_is_in_progress(projection: ProductProjection) -> None:
    schedule = _created(projection)
    other = _created(projection)
    run, receipt = _manual_run(projection, schedule, "plan")
    _submitted(projection, run, receipt)

    assert _in_progress(projection, schedule, after=timedelta(days=30)) == run.run_id
    assert _in_progress(projection, other) is None


def test_a_start_decided_to_run_is_refused_when_a_run_of_the_configuration_started_since(
    projection: ProductProjection,
) -> None:
    """The decision read the runs in progress before the admission; the admission reads them again."""
    schedule = _created(projection)
    run, receipt = _manual_run(projection, schedule, "plan")
    _submitted(projection, run, receipt)

    with pytest.raises(ScheduleChangedError, match="started after the decision"):
        _admit(projection, schedule, _FIRST_START, receipt_age_limit=_LIMIT)

    assert _stored(projection, schedule.schedule_id).last_start_at is None
    assert _in_progress(projection, schedule) == run.run_id


def test_a_scheduled_run_that_was_admitted_is_in_progress_until_it_ends(projection: ProductProjection) -> None:
    schedule = _created(projection)
    result = _admit(projection, schedule, _FIRST_START)

    assert _in_progress(projection, schedule) == result.run_id
    _end(
        projection, result.run_id or "", state="completed", outcome="succeeded", phase="applied", run_outcome="applied"
    )
    assert _in_progress(projection, schedule) is None


def test_an_unresolved_receipt_of_a_stage_counts_only_while_it_is_younger_than_the_limit(
    projection: ProductProjection,
) -> None:
    schedule = _created(projection)
    run, _receipt = _manual_run(projection, schedule, "half-way")

    assert _in_progress(projection, schedule, after=_LIMIT - timedelta(seconds=1)) == run.run_id
    assert _in_progress(projection, schedule, after=_LIMIT) is None
    assert _in_progress(projection, schedule, after=_LIMIT + timedelta(seconds=1)) is None


def test_an_unresolved_receipt_that_starts_no_stage_does_not_count(projection: ProductProjection) -> None:
    schedule = _created(projection)
    run, receipt = _manual_run(projection, schedule, "approve")
    flow_run_id = _submitted(projection, run, receipt)
    assert projection.claim_execution(run.run_id, flow_run_id, worker_id=_WORKER_ID, claimed_at=_FIRST_START)
    _end_plan(projection, run.run_id, flow_run_id)
    approve = MutationReceipt.model_validate(
        {**_apply_receipt(run.run_id).model_dump(), "receipt_id": f"approve.{run.run_id}", "operation": "approve"}
    )
    projection.reserve_mutation(approve)

    assert _in_progress(projection, schedule) is None


def _end_plan(
    projection: ProductProjection,
    run_id: str,
    flow_run_id: str,
    *,
    state: str = "completed",
    outcome: str = "succeeded",
) -> None:
    assert projection.commit_claimed_execution(
        run_id,
        flow_run_id,
        worker_id=_WORKER_ID,
        terminal_at=_FIRST_START + timedelta(minutes=1),
        terminal_state=state,  # ty: ignore[invalid-argument-type] - the cases pass the legal verdicts.
        terminal_outcome=outcome,  # ty: ignore[invalid-argument-type]
        writeback=ExecutionFinishWriteback(
            phase="planned" if outcome == "succeeded" else "interrupted",
            outcome="planned" if outcome == "succeeded" else "ambiguous",
            finished_at=_FIRST_START + timedelta(minutes=1),
            summary={},
            results={},
        ),
    )


def test_a_planned_run_that_waits_for_a_person_is_not_in_progress(projection: ProductProjection) -> None:
    schedule = _created(projection)
    run, receipt = _manual_run(projection, schedule, "planned")
    flow_run_id = _submitted(projection, run, receipt)
    assert projection.claim_execution(run.run_id, flow_run_id, worker_id=_WORKER_ID, claimed_at=_FIRST_START)

    _end_plan(projection, run.run_id, flow_run_id)

    assert (projection.lookup_run(run.run_id).value or _missing()).phase == "planned"
    assert _in_progress(projection, schedule) is None


def test_an_interrupted_run_is_not_in_progress(projection: ProductProjection) -> None:
    schedule = _created(projection)
    run, receipt = _manual_run(projection, schedule, "interrupted")
    flow_run_id = _submitted(projection, run, receipt)
    assert projection.claim_execution(run.run_id, flow_run_id, worker_id=_WORKER_ID, claimed_at=_FIRST_START)

    assert projection.interrupt_execution(run.run_id, flow_run_id, terminal_at=_FIRST_START + timedelta(minutes=1))

    assert (projection.lookup_run(run.run_id).value or _missing()).phase == "interrupted"
    assert _in_progress(projection, schedule) is None


def test_an_abandoned_execution_is_not_in_progress(projection: ProductProjection) -> None:
    schedule = _created(projection)
    run, receipt = _manual_run(projection, schedule, "abandoned")
    flow_run_id = _submitted(projection, run, receipt)

    assert projection.abandon_execution(run.run_id, flow_run_id, terminal_at=_FIRST_START + timedelta(minutes=1))

    assert (projection.lookup_run(run.run_id).value or _missing()).phase == "abandoned"
    assert _in_progress(projection, schedule) is None
