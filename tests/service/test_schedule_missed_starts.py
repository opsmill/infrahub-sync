"""Late and missed starts, and the ``queue`` overlap policy (spec 021, user story 3, research R7, R10).

Each test describes the flow run that Prefect holds (its start time) and the current time, then
runs the service flow with the `sync` stage replaced, so no adapter is built. A run in progress is
a `plan` that a person started through the Sync API; the tests that need it to end replace the
store question of research R7 with one that answers "no run" from then on.
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

from infrahub_sync.configuration.capabilities import DestinationSchemaReadError
from infrahub_sync.product_store import AuditEvent, ExecutionFinishWriteback, ProductRun, RunFilters, ScheduleRecord
from infrahub_sync.runtime_schema import worker as runtime_schema_worker
from infrahub_sync.service import flow as service_flow
from infrahub_sync.service.flow import DESTINATION_RECHECK_SECONDS, QUEUED_START_RECHECK_SECONDS
from tests.service.schedule_fixtures import OWNER_TOKEN, WORKER_ID, ScheduleApi, headers, schedule_api

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

CREATED = datetime(2026, 3, 2, 1, 30, tzinfo=timezone.utc)
T0 = datetime(2026, 3, 2, 2, tzinfo=timezone.utc)
HOUR = timedelta(hours=1)
ON_TIME = timedelta(seconds=10)
MISSED = "infrahub_sync.schedule.start_missed"
SKIPPED = "infrahub_sync.schedule.start_skipped"
QUEUED = "infrahub_sync.schedule.start_queued"


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> ScheduleApi:
    return schedule_api(monkeypatch, tmp_path)


def _schedule(
    api: ScheduleApi, *, cron: str = "0 * * * *", overlap_policy: str = "reject", late_limit: int = 3600
) -> str:
    """Store an active schedule to the default branch, hourly by default, created half an hour before ``T0``."""
    schedule_id = f"s-{uuid4().hex}"
    api.projection.create_schedule(
        ScheduleRecord.model_validate(
            {
                "schedule_id": schedule_id,
                "name": f"hourly-{schedule_id[2:10]}",
                "config_id": api.config_id,
                "registry_version": 1,
                "cron": cron,
                "timezone": "UTC",
                "write_target": "default-branch",
                "overlap_policy": overlap_policy,
                "start_late_by_at_most_seconds": late_limit,
                "state": "active",
                "owner": "owner",
                "created_at": CREATED,
                "updated_at": CREATED,
            }
        )
    )
    return schedule_id


def _succeeded(run_id: str, stage: str, *_args: object, **_kwargs: object) -> tuple[dict[str, Any], Any]:
    result = {"run_id": run_id, "stage": stage, "outcome": "applied"}
    return result, ExecutionFinishWriteback(
        phase="applied", outcome="applied", finished_at=datetime.now(timezone.utc), summary={}, results=result
    )


def _start(  # noqa: PLR0913  # pylint: disable=too-many-arguments
    monkeypatch: pytest.MonkeyPatch,
    api: ScheduleApi,
    schedule_id: str,
    start: datetime,
    now: datetime,
    *,
    flow_run_id: str | None = None,
) -> dict[str, Any] | State[Any]:
    """Run the service flow at ``now`` for one flow run of the schedule whose start time is ``start``."""
    monkeypatch.setattr(service_flow, "_runtime", lambda: api.projection)
    monkeypatch.setattr(service_flow, "_run_logger", lambda: (logging.getLogger("test-schedule-missed"), False))
    monkeypatch.setenv("PREFECT__WORKER_ID", WORKER_ID)
    identifier = flow_run_id or str(uuid4())
    monkeypatch.setattr(service_flow, "_prefect_flow_run_id", lambda: identifier)
    monkeypatch.setattr(
        service_flow,
        "_scheduled_flow_run",
        lambda *_args: service_flow.ScheduledFlowRun(auto_scheduled=True, expected_start_time=start),
    )
    monkeypatch.setattr(service_flow, "_now", lambda: now)
    monkeypatch.setattr(service_flow, "collect_secret_values", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(service_flow, "_execute_stage", _succeeded)
    # No Prefect server: the scheduled state of a start that waits is returned, not stored.
    # `test_prefect_keeps_the_state_that_runs_a_waiting_start_again` stores it.
    monkeypatch.setattr(service_flow, "_run_again_later", lambda state, _lines: state)
    return service_flow.service_sync_run.fn(stage="sync", schedule_id=schedule_id)


def _runs(api: ScheduleApi, schedule_id: str) -> list[ProductRun]:
    return list(api.projection.list_runs(RunFilters(schedule_id=schedule_id), limit=200).items)


def _events(api: ScheduleApi, event_type: str) -> list[AuditEvent]:
    return [event for event in api.projection.audit_events() if event.event_type == event_type]


def _start_at(event: AuditEvent) -> datetime | None:
    return None if event.details is None else event.details.start_at


def _person_plan(api: ScheduleApi) -> str:
    """Start a `plan` of the schedule's configuration as a person, and return its run."""
    response = api.client.post(
        "/runs",
        json={"operation": "plan", "config_id": api.config_id, "registry_version": 1, "reason": "look first"},
        headers=headers(OWNER_TOKEN),
    )
    assert response.status_code == 202, response.text
    return str(response.json()["run"]["run_id"])


class _RunInProgress:
    """The store question of research R7, with a switch that ends the run in progress."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, api: ScheduleApi) -> None:
        self.ended = False
        asked = api.projection.run_in_progress

        def run_in_progress(config_id: str, now: datetime, receipt_age_limit: timedelta) -> str | None:
            return None if self.ended else asked(config_id, now, receipt_age_limit)

        monkeypatch.setattr(api.projection, "run_in_progress", run_in_progress)


@pytest.fixture
def in_progress(monkeypatch: pytest.MonkeyPatch, api: ScheduleApi) -> tuple[str, _RunInProgress]:
    switch = _RunInProgress(monkeypatch, api)
    return _person_plan(api), switch


def _requeued(state: object, now: datetime) -> bool:
    """Whether the flow run returned the state that makes Prefect run it again one minute later."""
    return (
        isinstance(state, State)
        and state.is_scheduled()
        and state.name == "AwaitingRetry"
        and state.state_details.scheduled_time == now + timedelta(seconds=QUEUED_START_RECHECK_SECONDS)
    )


def test_a_start_late_inside_the_duration_runs_and_is_recorded(
    monkeypatch: pytest.MonkeyPatch, api: ScheduleApi
) -> None:
    """Scenario 1: the Sync service returns 40 minutes after a start, with a duration of one hour."""
    schedule_id = _schedule(api)

    result = _start(monkeypatch, api, schedule_id, T0, T0 + timedelta(minutes=40))

    assert isinstance(result, dict)
    (run,) = _runs(api, schedule_id)
    assert (run.scheduled_start_at, run.started_at) == (T0, T0 + timedelta(minutes=40))
    (missed,) = _events(api, MISSED)
    assert (missed.outcome, missed.run_id, missed.operation) == ("missed-started-late", run.run_id, "schedule-start")
    assert _start_at(missed) == T0


def test_a_start_on_time_writes_no_schedule_event(monkeypatch: pytest.MonkeyPatch, api: ScheduleApi) -> None:
    schedule_id = _schedule(api)

    _start(monkeypatch, api, schedule_id, T0, T0 + ON_TIME)

    assert len(_runs(api, schedule_id)) == 1
    assert _events(api, MISSED) == []


@pytest.mark.parametrize(
    ("cron", "late_limit", "late"),
    [
        pytest.param("0 2 * * *", 3600, 3 * HOUR, id="nightly-three-hours-late"),
        pytest.param("0 * * * *", 600, timedelta(seconds=601), id="hourly-past-a-short-duration"),
    ],
)
def test_a_start_late_outside_the_duration_is_skipped(
    monkeypatch: pytest.MonkeyPatch, api: ScheduleApi, cron: str, late_limit: int, late: timedelta
) -> None:
    """Scenario 2: the Sync service returns after the duration of the most recent start."""
    schedule_id = _schedule(api, cron=cron, late_limit=late_limit)

    state = _start(monkeypatch, api, schedule_id, T0, T0 + late)

    assert isinstance(state, State)
    assert state.is_completed()
    assert _runs(api, schedule_id) == []
    (missed,) = _events(api, MISSED)
    assert (missed.outcome, missed.run_id, _start_at(missed)) == ("missed-skipped", None, T0)


@pytest.mark.parametrize("order", ["oldest-first", "newest-first"])
def test_four_missed_starts_give_one_run_and_four_events(
    monkeypatch: pytest.MonkeyPatch, api: ScheduleApi, order: str
) -> None:
    """Scenario 3: whatever order Prefect gives the four late flow runs, only the most recent one runs."""
    schedule_id = _schedule(api)
    starts = [T0 + offset * HOUR for offset in range(4)]
    now = starts[-1] + timedelta(minutes=40)

    for start in starts if order == "oldest-first" else [starts[-1], *starts[:-1]]:
        _start(monkeypatch, api, schedule_id, start, now)

    (run,) = _runs(api, schedule_id)
    assert run.scheduled_start_at == starts[-1]
    missed = sorted(_events(api, MISSED), key=lambda event: _start_at(event) or T0)
    assert [(_start_at(event), event.outcome) for event in missed] == [
        *((start, "missed-skipped") for start in starts[:-1]),
        (starts[-1], "missed-started-late"),
    ]
    assert missed[-1].run_id == run.run_id
    assert api.stored(schedule_id).last_start_at == starts[-1]


@pytest.mark.parametrize("policy", ["reject", "queue"])
def test_a_late_run_follows_the_overlap_policy(
    monkeypatch: pytest.MonkeyPatch, api: ScheduleApi, in_progress: tuple[str, _RunInProgress], policy: str
) -> None:
    """Scenario 8, FR-019: a late start that finds a run in progress is skipped or waits, like any start."""
    run_in_progress, _switch = in_progress
    schedule_id = _schedule(api, overlap_policy=policy)
    now = T0 + timedelta(minutes=40)

    state = _start(monkeypatch, api, schedule_id, T0, now)

    assert _runs(api, schedule_id) == []
    assert _events(api, MISSED) == []
    if policy == "reject":
        (skipped,) = _events(api, SKIPPED)
        assert (skipped.outcome, skipped.run_id) == ("skipped-run-in-progress", run_in_progress)
        assert isinstance(state, State)
        assert state.is_completed()
    else:
        (queued,) = _events(api, QUEUED)
        assert (queued.outcome, queued.run_id, _start_at(queued)) == ("queued", run_in_progress, T0)
        assert _requeued(state, now)


def test_queue_makes_one_start_wait_and_run_when_the_run_in_progress_ends(
    monkeypatch: pytest.MonkeyPatch, api: ScheduleApi, in_progress: tuple[str, _RunInProgress]
) -> None:
    """Scenarios 4 and 5: one start waits, a further start is skipped, the waiting start runs at the end."""
    run_in_progress, switch = in_progress
    schedule_id = _schedule(api, overlap_policy="queue")
    flow_run_id = str(uuid4())

    first = _start(monkeypatch, api, schedule_id, T0, T0 + ON_TIME, flow_run_id=flow_run_id)
    checks = [
        _start(
            monkeypatch, api, schedule_id, T0, T0 + ON_TIME + minutes * timedelta(minutes=1), flow_run_id=flow_run_id
        )
        for minutes in (1, 2, 3)
    ]
    further = _start(monkeypatch, api, schedule_id, T0 + HOUR, T0 + HOUR + ON_TIME)

    assert _requeued(first, T0 + ON_TIME)
    assert all(
        _requeued(check, T0 + ON_TIME + minutes * timedelta(minutes=1))
        for check, minutes in zip(checks, (1, 2, 3), strict=True)
    )
    (queued,) = _events(api, QUEUED)
    assert (queued.outcome, queued.run_id, _start_at(queued)) == ("queued", run_in_progress, T0)
    (skipped,) = _events(api, SKIPPED)
    assert (skipped.outcome, _start_at(skipped)) == ("skipped-start-waiting", T0 + HOUR)
    assert isinstance(further, State)
    assert further.is_completed()
    assert api.stored(schedule_id).waiting_start_at == T0
    assert _runs(api, schedule_id) == []

    switch.ended = True
    ended_at = T0 + HOUR + timedelta(minutes=5)
    result = _start(monkeypatch, api, schedule_id, T0, ended_at, flow_run_id=flow_run_id)

    assert isinstance(result, dict)
    (run,) = _runs(api, schedule_id)
    assert (run.scheduled_start_at, run.started_at) == (T0, ended_at)
    stored = api.stored(schedule_id)
    assert (stored.waiting_start_at, stored.last_start_at) == (None, T0 + HOUR)
    assert _events(api, MISSED) == []

    following = _start(monkeypatch, api, schedule_id, T0 + 2 * HOUR, T0 + 2 * HOUR + ON_TIME)

    assert isinstance(following, dict)
    assert len(_runs(api, schedule_id)) == 2
    assert _events(api, MISSED) == []


def test_a_pause_ends_the_waiting_start(
    monkeypatch: pytest.MonkeyPatch, api: ScheduleApi, in_progress: tuple[str, _RunInProgress]
) -> None:
    """A paused schedule starts no run: the waiting start does not run after a resume, and blocks no start."""
    _run_in_progress, switch = in_progress
    schedule_id = _schedule(api, overlap_policy="queue")
    flow_run_id = str(uuid4())
    _start(monkeypatch, api, schedule_id, T0, T0 + ON_TIME, flow_run_id=flow_run_id)
    assert api.stored(schedule_id).waiting_start_at == T0

    assert api.post(schedule_id, "pause").status_code == 200
    assert api.stored(schedule_id).waiting_start_at is None
    paused_check = _start(monkeypatch, api, schedule_id, T0, T0 + timedelta(minutes=1), flow_run_id=flow_run_id)
    assert api.post(schedule_id, "resume").status_code == 200
    switch.ended = True
    resumed_check = _start(monkeypatch, api, schedule_id, T0, T0 + timedelta(minutes=2), flow_run_id=flow_run_id)
    resumed_at = api.stored(schedule_id).updated_at
    next_start = resumed_at.replace(minute=0, second=0, microsecond=0) + HOUR
    following = _start(monkeypatch, api, schedule_id, next_start, next_start + ON_TIME)

    for state in (paused_check, resumed_check):
        assert isinstance(state, State)
        assert state.is_completed()
    assert isinstance(following, dict)
    assert [run.scheduled_start_at for run in _runs(api, schedule_id)] == [next_start]
    assert _events(api, SKIPPED) == []


def test_a_change_of_the_overlap_policy_ends_the_waiting_start(
    monkeypatch: pytest.MonkeyPatch, api: ScheduleApi, in_progress: tuple[str, _RunInProgress]
) -> None:
    _run_in_progress, _switch = in_progress
    schedule_id = _schedule(api, overlap_policy="queue")
    _start(monkeypatch, api, schedule_id, T0, T0 + ON_TIME)

    response = api.post(schedule_id, "change", overlap_policy="reject")

    assert response.status_code == 200, response.text
    assert api.stored(schedule_id).waiting_start_at is None


def test_a_waiting_start_that_can_no_longer_run_ends_the_wait(
    monkeypatch: pytest.MonkeyPatch, api: ScheduleApi, in_progress: tuple[str, _RunInProgress]
) -> None:
    """A waiting start whose version was removed records a failed start and clears the wait."""
    _run_in_progress, switch = in_progress
    schedule_id = _schedule(api, overlap_policy="queue")
    flow_run_id = str(uuid4())
    _start(monkeypatch, api, schedule_id, T0, T0 + ON_TIME, flow_run_id=flow_run_id)
    switch.ended = True
    stored = api.stored(schedule_id)
    api.projection.update_schedule(
        stored.model_copy(update={"registry_version": 99}), expected_updated_at=stored.updated_at
    )
    # The change above moved `updated_at`; the waiting start is still the same start.
    assert api.stored(schedule_id).waiting_start_at == T0

    state = _start(monkeypatch, api, schedule_id, T0, T0 + timedelta(minutes=1), flow_run_id=flow_run_id)

    assert isinstance(state, State)
    assert state.is_completed()
    (failed,) = _events(api, "infrahub_sync.schedule.start_failed")
    assert _start_at(failed) == T0
    assert api.stored(schedule_id).waiting_start_at is None


@pytest.fixture
def _prefect_server() -> Iterator[None]:
    """A real Prefect server, so the state that the flow run returns goes through Prefect's rules."""
    from prefect.testing.utilities import prefect_test_harness

    with prefect_test_harness():
        yield


# The functions that need a real flow run; `_start` replaces them.
_RUN_AGAIN_LATER = service_flow._run_again_later
_PREFECT_FLOW_RUN_ID = service_flow._prefect_flow_run_id


@pytest.mark.usefixtures("_prefect_server")
def test_prefect_keeps_the_state_that_runs_a_waiting_start_again(
    monkeypatch: pytest.MonkeyPatch, api: ScheduleApi, in_progress: tuple[str, _RunInProgress]
) -> None:
    """Research R7, FR-035: a waiting start ends its process with `AwaitingRetry`, and the flow run can run again.

    The flow is called as the worker's process calls it, for its result rather than its state.
    Prefect 3.8.6 cannot end a flow run in a scheduled state from the state the flow returns, so
    the flow stores the state itself and raises `Pause`, which the worker's process ends with
    status 0. The test shows that no state follows `AwaitingRetry`, that Prefect keeps its time
    and the flow run's `expected_start_time`, and that it accepts the moves a worker makes to
    run the flow run again once that time has passed. The flow run is created by a direct call
    of the flow, not by a schedule; the development stack showed a worker running it again.
    """
    from prefect.client.orchestration import get_client
    from prefect.client.schemas.objects import StateType
    from prefect.client.schemas.responses import SetStateStatus
    from prefect.exceptions import Pause
    from prefect.states import Pending, Running

    _run_in_progress, _switch = in_progress
    schedule_id = _schedule(api, overlap_policy="queue")
    # Two minutes in the past, so the time of the next attempt has passed and a worker may take it.
    now = datetime.now(timezone.utc) - timedelta(minutes=2)
    start = now.replace(minute=0, second=0, microsecond=0)
    _start(monkeypatch, api, schedule_id, start, now)  # applies the replacements; the start waits
    monkeypatch.setattr(service_flow, "_run_again_later", _RUN_AGAIN_LATER)
    monkeypatch.setattr(service_flow, "_prefect_flow_run_id", _PREFECT_FLOW_RUN_ID)

    with pytest.raises(Pause):
        service_flow.service_sync_run(stage="sync", schedule_id=schedule_id)

    with get_client(sync_client=True) as client:
        (stored,) = [run for run in client.read_flow_runs() if run.parameters.get("schedule_id") == schedule_id]
        assert stored.state is not None
        assert (stored.state.type, stored.state.name) == (StateType.SCHEDULED, "AwaitingRetry")
        recheck_at = now + timedelta(seconds=QUEUED_START_RECHECK_SECONDS)
        assert stored.state.state_details.scheduled_time == recheck_at
        history = client.read_flow_run_states(stored.id)
        assert [state.name for state in history][-2:] == ["Running", "AwaitingRetry"]
        expected = stored.expected_start_time
        assert expected is not None
        assert expected != recheck_at
        for move in (Pending(), Running()):
            assert client.set_flow_run_state(stored.id, move).status == SetStateStatus.ACCEPT
        assert client.read_flow_run(stored.id).expected_start_time == expected
    assert api.stored(schedule_id).waiting_start_at == start


# The worker's real check before admission; `schedule_api` replaces it for every other test.
_DESTINATION_NOT_READY = service_flow._destination_not_ready


def _destination(monkeypatch: pytest.MonkeyPatch, reason: str | None) -> list[str]:
    """Make the destination schema read fail with ``reason``, or answer when ``reason`` is ``None``.

    Returns the list of branches the check read, oldest first.
    """
    read: list[str] = []

    def read_schema(_package: object, branch: str) -> dict[str, Any]:
        read.append(branch)
        if reason is not None:
            message = "the destination schema read failed"
            raise DestinationSchemaReadError(message, reason=reason)
        return {}

    monkeypatch.setattr(service_flow, "_destination_not_ready", _DESTINATION_NOT_READY)
    monkeypatch.setattr(runtime_schema_worker, "read_destination_schema_snapshot", read_schema)
    return read


def _waits_for_destination(state: object, now: datetime) -> bool:
    """Whether the flow run returned the state that makes Prefect read the destination again one minute later."""
    return (
        isinstance(state, State)
        and state.is_scheduled()
        and state.name == "AwaitingRetry"
        and state.state_details.scheduled_time == now + timedelta(seconds=DESTINATION_RECHECK_SECONDS)
    )


@pytest.mark.parametrize("reason", ["unreachable", "timeout"])
def test_a_start_waits_while_its_destination_does_not_answer(
    monkeypatch: pytest.MonkeyPatch, api: ScheduleApi, reason: str
) -> None:
    """FR-035: the Sync service and Infrahub start again after a maintenance; Infrahub answers last.

    The start waits with no run and no failure, then runs once Infrahub answers, and records
    that it started late.
    """
    schedule_id = _schedule(api)
    read = _destination(monkeypatch, reason)
    flow_run_id = str(uuid4())
    waiting_at = T0 + timedelta(minutes=6)

    waited = _start(monkeypatch, api, schedule_id, T0, waiting_at, flow_run_id=flow_run_id)

    assert _waits_for_destination(waited, waiting_at)
    assert _runs(api, schedule_id) == []
    assert _events(api, MISSED) == []
    assert api.stored(schedule_id).consecutive_failures == 0
    assert read == ["main"]

    _destination(monkeypatch, None)
    ran = _start(monkeypatch, api, schedule_id, T0, waiting_at + timedelta(minutes=1), flow_run_id=flow_run_id)

    assert isinstance(ran, dict)
    (run,) = _runs(api, schedule_id)
    assert run.scheduled_start_at == T0
    (missed,) = _events(api, MISSED)
    assert (missed.outcome, missed.run_id) == ("missed-started-late", run.run_id)


@pytest.mark.parametrize("reason", ["unauthorized", "forbidden", "nobranch"])
def test_a_start_does_not_wait_for_a_destination_that_refuses_it(
    monkeypatch: pytest.MonkeyPatch, api: ScheduleApi, reason: str
) -> None:
    """A refused token, a missing permission or a missing branch is no outage: the run starts and records it."""
    schedule_id = _schedule(api)
    _destination(monkeypatch, reason)

    result = _start(monkeypatch, api, schedule_id, T0, T0 + ON_TIME)

    assert isinstance(result, dict)
    assert len(_runs(api, schedule_id)) == 1


def test_a_start_stops_waiting_one_recheck_before_its_late_start_duration_ends(
    monkeypatch: pytest.MonkeyPatch, api: ScheduleApi
) -> None:
    """A start that could not run after one more minute runs now, and its run records the destination failure."""
    schedule_id = _schedule(api, late_limit=600)
    _destination(monkeypatch, "unreachable")
    last_wait = T0 + timedelta(seconds=600 - DESTINATION_RECHECK_SECONDS)

    waited = _start(monkeypatch, api, schedule_id, T0, last_wait)
    ran = _start(monkeypatch, api, schedule_id, T0, last_wait + timedelta(seconds=1))

    assert _waits_for_destination(waited, last_wait)
    assert isinstance(ran, dict)
    assert len(_runs(api, schedule_id)) == 1


def test_a_paused_schedule_does_not_read_its_destination(monkeypatch: pytest.MonkeyPatch, api: ScheduleApi) -> None:
    schedule_id = _schedule(api)
    read = _destination(monkeypatch, "unreachable")
    assert api.post(schedule_id, "pause").status_code == 200

    state = _start(monkeypatch, api, schedule_id, T0, T0 + ON_TIME)

    assert isinstance(state, State)
    assert state.is_completed()
    assert read == []
    assert _runs(api, schedule_id) == []
