"""The admission of a scheduled flow run by the worker (spec 021, user story 1, research R2, R7, R10).

Each test describes the flow run that Prefect holds (whether a schedule created it and its start
time) and the current time, then runs the service flow with the `sync` stage replaced, so no
adapter is built. The schedule is stored with fixed times, so the missed starts are known.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest

pytest.importorskip("prefect")
pytest.importorskip("fastapi")

from prefect.states import State

from infrahub_sync.product_store import (
    AuditEvent,
    ExecutionFinishWriteback,
    LookupResult,
    ProductRun,
    RunFilters,
    ScheduleRecord,
)
from infrahub_sync.service import flow as service_flow
from infrahub_sync.service.schedule_admission import MISSED_STARTS_RECORDED_ONE_BY_ONE
from tests.service.schedule_fixtures import OWNER_TOKEN, WORKER_ID, ScheduleApi, headers, schedule_api

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

CREATED = datetime(2026, 3, 2, 1, 30, tzinfo=timezone.utc)
T0 = datetime(2026, 3, 2, 2, tzinfo=timezone.utc)
HOUR = timedelta(hours=1)
ON_TIME = timedelta(seconds=10)


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> ScheduleApi:
    return schedule_api(monkeypatch, tmp_path)


def _schedule(
    api: ScheduleApi, *, cron: str = "0 * * * *", write_target: str = "default-branch", created: datetime = CREATED
) -> str:
    """Store an active schedule created at ``created``, owned by ``owner``, and return its identifier."""
    schedule_id = f"s-{uuid4().hex}"
    api.projection.create_schedule(
        ScheduleRecord.model_validate(
            {
                "schedule_id": schedule_id,
                "name": f"nightly-{schedule_id[2:10]}",
                "config_id": api.config_id,
                "registry_version": 1,
                "cron": cron,
                "timezone": "UTC",
                "write_target": write_target,
                "state": "active",
                "owner": "owner",
                "created_at": created,
                "updated_at": created,
            }
        )
    )
    return schedule_id


def _succeeded(outcome: str = "applied") -> Callable[..., tuple[dict[str, Any], ExecutionFinishWriteback]]:
    def execute(run_id: str, stage: str, *_args: object, **_kwargs: object) -> tuple[dict[str, Any], Any]:
        result = {"run_id": run_id, "stage": stage, "outcome": outcome}
        return result, ExecutionFinishWriteback(
            phase="applied", outcome=outcome, finished_at=datetime.now(timezone.utc), summary={}, results=result
        )

    return execute


class _StageFailedError(RuntimeError):
    """The failure of a replaced `sync` stage."""


def _failed(*_args: object, **_kwargs: object) -> tuple[dict[str, Any], Any]:
    raise _StageFailedError


def _start(  # noqa: PLR0913  # pylint: disable=too-many-arguments
    monkeypatch: pytest.MonkeyPatch,
    api: ScheduleApi,
    schedule_id: str,
    start: datetime,
    *,
    late: timedelta = ON_TIME,
    auto_scheduled: bool = True,
    flow_run_id: str | None = None,
    execute: Callable[..., tuple[dict[str, Any], Any]] | None = None,
) -> dict[str, Any] | State[Any]:
    """Run the service flow for one flow run of the schedule, ``late`` after its start time."""
    monkeypatch.setattr(service_flow, "_runtime", lambda: api.projection)
    monkeypatch.setattr(service_flow, "_run_logger", lambda: (logging.getLogger("test-schedule-admission"), False))
    monkeypatch.setenv("PREFECT__WORKER_ID", WORKER_ID)
    identifier = flow_run_id or str(uuid4())
    monkeypatch.setattr(service_flow, "_prefect_flow_run_id", lambda: identifier)
    monkeypatch.setattr(
        service_flow,
        "_scheduled_flow_run",
        lambda *_args: service_flow.ScheduledFlowRun(auto_scheduled=auto_scheduled, expected_start_time=start),
    )
    monkeypatch.setattr(service_flow, "_now", lambda: start + late)
    monkeypatch.setattr(service_flow, "collect_secret_values", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(service_flow, "_execute_stage", execute or _succeeded())
    return service_flow.service_sync_run.fn(stage="sync", schedule_id=schedule_id)


def _runs(api: ScheduleApi, schedule_id: str) -> list[ProductRun]:
    return list(api.projection.list_runs(RunFilters(schedule_id=schedule_id), limit=200).items)


def _events(api: ScheduleApi, event_type: str) -> list[AuditEvent]:
    return [event for event in api.projection.audit_events() if event.event_type == event_type]


def _failures(api: ScheduleApi, schedule_id: str) -> int:
    return api.stored(schedule_id).consecutive_failures


def _completed(state: object) -> bool:
    return isinstance(state, State) and state.is_completed()


def _failed_state(state: object) -> bool:
    return isinstance(state, State) and state.is_failed()


def test_a_scheduled_flow_run_creates_one_run_of_the_owner_for_the_version(
    monkeypatch: pytest.MonkeyPatch, api: ScheduleApi
) -> None:
    """Scenario 2: the run names the schedule and its start, the owner and the version."""
    schedule_id = _schedule(api)

    result = _start(monkeypatch, api, schedule_id, T0)

    assert isinstance(result, dict)
    (run,) = _runs(api, schedule_id)
    assert run.run_id == result["run_id"]
    assert (run.trigger_type, run.schedule_id, run.scheduled_start_at) == ("scheduled", schedule_id, T0)
    assert run.actor == "owner"
    assert (run.config_id, run.registry_version, run.operation) == (api.config_id, 1, "sync")
    assert run.started_at == T0 + ON_TIME
    (accepted,) = [event for event in _events(api, "infrahub_sync.run.accepted") if event.run_id == run.run_id]
    assert ("schedule", schedule_id) in {(item.kind, item.id) for item in accepted.related}
    assert api.stored(schedule_id).last_start_at == T0


def test_a_second_flow_run_of_one_start_and_a_retried_flow_run_create_no_second_run(
    monkeypatch: pytest.MonkeyPatch, api: ScheduleApi
) -> None:
    schedule_id = _schedule(api)
    flow_run_id = str(uuid4())
    _start(monkeypatch, api, schedule_id, T0, flow_run_id=flow_run_id)

    second = _start(monkeypatch, api, schedule_id, T0)
    retried = _start(monkeypatch, api, schedule_id, T0, flow_run_id=flow_run_id)

    assert _failed_state(second)
    assert _failed_state(retried)
    assert len(_runs(api, schedule_id)) == 1


def test_a_flow_run_that_no_schedule_created_is_refused(monkeypatch: pytest.MonkeyPatch, api: ScheduleApi) -> None:
    """A flow run started by hand from the Prefect interface with schedule_id writes nothing."""
    schedule_id = _schedule(api)
    before = len(api.projection.audit_events())

    state = _start(monkeypatch, api, schedule_id, T0, auto_scheduled=False)

    assert _failed_state(state)
    assert _runs(api, schedule_id) == []
    assert len(api.projection.audit_events()) == before
    assert api.stored(schedule_id).last_start_at is None


@pytest.mark.parametrize("action", ["pause", "delete"])
def test_a_paused_or_deleted_schedule_creates_no_run(
    monkeypatch: pytest.MonkeyPatch, api: ScheduleApi, action: str
) -> None:
    schedule_id = _schedule(api)
    assert api.post(schedule_id, action).status_code == 200

    state = _start(monkeypatch, api, schedule_id, T0)

    assert _completed(state)
    assert _runs(api, schedule_id) == []
    assert _events(api, "infrahub_sync.schedule.start_missed") == []


def test_reject_skips_the_start_and_names_the_run_in_progress(
    monkeypatch: pytest.MonkeyPatch, api: ScheduleApi
) -> None:
    """Scenario 7: a plan that a person started is in progress when the start comes."""
    schedule_id = _schedule(api)
    response = api.client.post(
        "/runs",
        json={"operation": "plan", "config_id": api.config_id, "registry_version": 1, "reason": "look first"},
        headers=headers(OWNER_TOKEN),
    )
    assert response.status_code == 202, response.text
    in_progress = response.json()["run"]["run_id"]

    state = _start(monkeypatch, api, schedule_id, T0)

    assert _completed(state)
    assert _runs(api, schedule_id) == []
    (skipped,) = _events(api, "infrahub_sync.schedule.start_skipped")
    assert (skipped.run_id, skipped.outcome, skipped.actor) == (in_progress, "skipped-run-in-progress", "owner")
    assert skipped.details is not None
    assert skipped.details.start_at == T0


def test_a_start_more_than_five_minutes_late_is_recorded_as_missed_with_no_run(
    monkeypatch: pytest.MonkeyPatch, api: ScheduleApi
) -> None:
    schedule_id = _schedule(api)

    state = _start(monkeypatch, api, schedule_id, T0, late=timedelta(seconds=301))
    on_time = _start(monkeypatch, api, schedule_id, T0 + HOUR, late=timedelta(seconds=300))

    assert _completed(state)
    (missed,) = _events(api, "infrahub_sync.schedule.start_missed")
    assert (missed.outcome, missed.run_id, missed.operation) == ("missed-skipped", None, "schedule-start")
    assert missed.details is not None
    assert missed.details.start_at == T0
    assert isinstance(on_time, dict)
    assert [run.scheduled_start_at for run in _runs(api, schedule_id)] == [T0 + HOUR]


def test_starts_with_no_flow_run_since_the_last_start_are_recorded_as_missed(
    monkeypatch: pytest.MonkeyPatch, api: ScheduleApi
) -> None:
    schedule_id = _schedule(api)
    _start(monkeypatch, api, schedule_id, T0)

    result = _start(monkeypatch, api, schedule_id, T0 + 5 * HOUR)

    assert isinstance(result, dict)
    missed = _events(api, "infrahub_sync.schedule.start_missed")
    assert sorted(event.details.start_at for event in missed if event.details and event.details.start_at) == [
        T0 + n * HOUR for n in range(1, 5)
    ]
    assert len(missed) == 4
    assert {event.outcome for event in missed} == {"missed-skipped"}
    assert len(_runs(api, schedule_id)) == 2


def test_a_resumed_schedule_records_no_missed_start_for_the_time_it_was_paused(
    monkeypatch: pytest.MonkeyPatch, api: ScheduleApi
) -> None:
    """A paused schedule records no missed start: the count begins at the resume."""
    schedule_id = _schedule(api)
    _start(monkeypatch, api, schedule_id, T0)
    assert api.post(schedule_id, "pause").status_code == 200
    assert api.post(schedule_id, "resume").status_code == 200
    resumed_at = api.stored(schedule_id).updated_at
    start = resumed_at.replace(minute=0, second=0, microsecond=0) + HOUR

    result = _start(monkeypatch, api, schedule_id, start)

    assert isinstance(result, dict)
    assert _events(api, "infrahub_sync.schedule.start_missed") == []


def test_more_than_500_missed_starts_give_500_events_and_one_summary(
    monkeypatch: pytest.MonkeyPatch, api: ScheduleApi
) -> None:
    """Every minute for 1500 minutes: 1499 missed starts, more than one page of the cadence."""
    minute = timedelta(minutes=1)
    schedule_id = _schedule(api, cron="* * * * *", created=T0 - timedelta(seconds=30))
    _start(monkeypatch, api, schedule_id, T0)

    _start(monkeypatch, api, schedule_id, T0 + 1500 * minute)

    missed = _events(api, "infrahub_sync.schedule.start_missed")
    assert len(missed) == MISSED_STARTS_RECORDED_ONE_BY_ONE + 1
    (summary,) = [event for event in missed if event.details and event.details.starts is not None]
    assert summary.details is not None
    assert summary.details.start_at is None
    assert (summary.details.starts, summary.details.first_start_at, summary.details.last_start_at) == (
        999,
        T0 + minute,
        T0 + 999 * minute,
    )
    assert sorted(event.details.start_at for event in missed if event.details and event.details.start_at) == [
        T0 + n * minute for n in range(1000, 1500)
    ]


def test_a_configuration_version_that_cannot_be_run_records_a_failed_start(
    monkeypatch: pytest.MonkeyPatch, api: ScheduleApi
) -> None:
    schedule_id = _schedule(api)
    monkeypatch.setattr(
        api.projection,
        "lookup_configuration_version",
        lambda *_args: LookupResult(value=None, reason="the version is not registered"),
    )

    state = _start(monkeypatch, api, schedule_id, T0)

    assert _completed(state)
    assert _runs(api, schedule_id) == []
    (failed,) = _events(api, "infrahub_sync.schedule.start_failed")
    assert (failed.outcome, failed.run_id) == ("failed", None)
    assert failed.details is not None
    assert (failed.details.cause, failed.details.start_at) == ("configuration-version-not-found", T0)


def test_30_starts_give_30_runs_or_recorded_starts(monkeypatch: pytest.MonkeyPatch, api: ScheduleApi) -> None:
    """SC-002: every fifth start comes late and is recorded as missed; each other start runs."""
    schedule_id = _schedule(api)
    starts = [T0 + n * HOUR for n in range(30)]

    for number, start in enumerate(starts):
        _start(monkeypatch, api, schedule_id, start, late=timedelta(minutes=10) if number % 5 == 4 else ON_TIME)

    ran = [run.scheduled_start_at for run in _runs(api, schedule_id)]
    missed = [event.details.start_at for event in _events(api, "infrahub_sync.schedule.start_missed") if event.details]
    assert len(ran) == 24
    assert len(missed) == 6
    assert sorted([*ran, *missed]) == starts


def test_the_fifth_failed_run_in_a_row_stops_the_schedule(monkeypatch: pytest.MonkeyPatch, api: ScheduleApi) -> None:
    """Scenario 21: the stop names the fifth run, and the next start creates no run."""
    schedule_id = _schedule(api)
    for number in range(5):
        with pytest.raises(RuntimeError, match="_StageFailedError"):
            _start(monkeypatch, api, schedule_id, T0 + number * HOUR, execute=_failed)

    stored = api.stored(schedule_id)
    assert (stored.state, stored.state_reason, stored.consecutive_failures) == ("stopped", "repeated-failures", 5)
    fifth = next(run for run in _runs(api, schedule_id) if run.scheduled_start_at == T0 + 4 * HOUR)
    (stopped,) = _events(api, "infrahub_sync.schedule.stopped")
    assert (stopped.run_id, stopped.outcome) == (fifth.run_id, "repeated-failures")

    after = _start(monkeypatch, api, schedule_id, T0 + 5 * HOUR)

    assert _completed(after)
    assert len(_runs(api, schedule_id)) == 5


@pytest.mark.parametrize("outcome", ["applied", "no-change"])
def test_a_successful_or_unchanged_run_sets_the_count_to_zero(
    monkeypatch: pytest.MonkeyPatch, api: ScheduleApi, outcome: str
) -> None:
    schedule_id = _schedule(api)
    for number in range(2):
        with pytest.raises(RuntimeError, match="_StageFailedError"):
            _start(monkeypatch, api, schedule_id, T0 + number * HOUR, execute=_failed)
    assert _failures(api, schedule_id) == 2

    _start(monkeypatch, api, schedule_id, T0 + 2 * HOUR, execute=_succeeded(outcome))

    assert _failures(api, schedule_id) == 0


def test_a_skipped_start_leaves_the_count(monkeypatch: pytest.MonkeyPatch, api: ScheduleApi) -> None:
    schedule_id = _schedule(api)
    with pytest.raises(RuntimeError, match="_StageFailedError"):
        _start(monkeypatch, api, schedule_id, T0, execute=_failed)
    response = api.client.post(
        "/runs",
        json={"operation": "plan", "config_id": api.config_id, "registry_version": 1, "reason": "look first"},
        headers=headers(OWNER_TOKEN),
    )
    assert response.status_code == 202, response.text

    _start(monkeypatch, api, schedule_id, T0 + HOUR)

    assert len(_events(api, "infrahub_sync.schedule.start_skipped")) == 1
    assert _failures(api, schedule_id) == 1


def test_a_cancelled_run_leaves_the_count(monkeypatch: pytest.MonkeyPatch, api: ScheduleApi) -> None:
    """A person cancels the scheduled run while its stage runs; the stage then stops."""
    schedule_id = _schedule(api)
    with pytest.raises(RuntimeError, match="_StageFailedError"):
        _start(monkeypatch, api, schedule_id, T0, execute=_failed)

    def cancelled(run_id: str, *_args: object, **_kwargs: object) -> tuple[dict[str, Any], Any]:
        response = api.client.post(f"/runs/{run_id}/cancel", json={"reason": "stop it"}, headers=headers(OWNER_TOKEN))
        assert response.status_code in {200, 202, 409}, response.text
        raise _StageFailedError

    with pytest.raises(RuntimeError, match="_StageFailedError"):
        _start(monkeypatch, api, schedule_id, T0 + HOUR, execute=cancelled)

    second = next(run for run in _runs(api, schedule_id) if run.scheduled_start_at == T0 + HOUR)
    assert second.finished_at is not None
    assert _failures(api, schedule_id) == 1


def test_a_resume_of_a_stopped_schedule_sets_the_count_to_zero(
    monkeypatch: pytest.MonkeyPatch, api: ScheduleApi
) -> None:
    schedule_id = _schedule(api)
    for number in range(5):
        with pytest.raises(RuntimeError, match="_StageFailedError"):
            _start(monkeypatch, api, schedule_id, T0 + number * HOUR, execute=_failed)
    assert api.stored(schedule_id).state == "stopped"

    response = api.post(schedule_id, "resume")

    assert response.status_code == 200, response.text
    stored = api.stored(schedule_id)
    assert (stored.state, stored.consecutive_failures) == ("active", 0)
