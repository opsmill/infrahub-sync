"""Branch review of a scheduled run, the part in the Sync service (spec 021, scenarios 16 and 17, research R11).

The branch steps in Infrahub are a later phase: until then the `sync` stage of a run with branch
review is refused, and the run ends as failed.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest

pytest.importorskip("prefect")
pytest.importorskip("fastapi")

from infrahub_sync.product_store import RunFilters, ScheduleRecord
from infrahub_sync.service import flow as service_flow
from infrahub_sync.service.schedule_admission import branch_review_branch, decide_start
from tests.service.schedule_fixtures import OWNER_TOKEN, WORKER_ID, ScheduleApi, headers, package, schedule_api

if TYPE_CHECKING:
    from pathlib import Path

T0 = datetime(2026, 3, 2, 2, tzinfo=timezone.utc)
SCHEDULE_ID = "s-0123456789abcdef0123456789abcdef"
CHECKSUM = "a" * 64


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> ScheduleApi:
    return schedule_api(monkeypatch, tmp_path)


def _schedule(api: ScheduleApi, config_id: str | None = None) -> ScheduleRecord:
    """Store an active schedule named ``nightly`` with branch review, created just before ``T0``."""
    created = T0 - timedelta(minutes=30)
    record = ScheduleRecord.model_validate(
        {
            "schedule_id": SCHEDULE_ID,
            "name": "nightly",
            "config_id": config_id or api.config_id,
            "registry_version": 1,
            "cron": "0 2 * * *",
            "timezone": "UTC",
            "write_target": "branch-review",
            "state": "active",
            "owner": "owner",
            "created_at": created,
            "updated_at": created,
        }
    )
    api.projection.create_schedule(record)
    return record


def _start(monkeypatch: pytest.MonkeyPatch, api: ScheduleApi, start: datetime) -> object:
    """Run the service flow for the flow run of ``start``, with the real `sync` stage."""
    monkeypatch.setattr(service_flow, "_runtime", lambda: api.projection)
    monkeypatch.setattr(service_flow, "_run_logger", lambda: (logging.getLogger("test-branch-review"), False))
    monkeypatch.setenv("PREFECT__WORKER_ID", WORKER_ID)
    flow_run_id = str(uuid4())
    monkeypatch.setattr(service_flow, "_prefect_flow_run_id", lambda: flow_run_id)
    monkeypatch.setattr(
        service_flow,
        "_scheduled_flow_run",
        lambda *_args: service_flow.ScheduledFlowRun(auto_scheduled=True, expected_start_time=start),
    )
    monkeypatch.setattr(service_flow, "_now", lambda: start + timedelta(seconds=5))
    monkeypatch.setattr(service_flow, "collect_secret_values", lambda *_args, **_kwargs: [])
    return service_flow.service_sync_run.fn(stage="sync", schedule_id=SCHEDULE_ID)


def test_branch_review_is_refused_for_a_version_that_declares_a_destination_branch(api: ScheduleApi) -> None:
    """Scenario 16: the run would write to the declared branch with no review."""
    declared = api.projection.create_configuration(package("declared-branch", branch="sync-target")).config_id

    response = api.client.post(
        "/schedules",
        json=api.body(config_id=declared, write_target="branch-review"),
        headers=headers(OWNER_TOKEN),
    )

    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "schedule-branch-declared"


def test_a_start_of_branch_review_on_a_version_that_declares_a_branch_records_a_failed_start(
    monkeypatch: pytest.MonkeyPatch, api: ScheduleApi
) -> None:
    """A stored schedule that the API would refuse today creates no run either."""
    declared = api.projection.create_configuration(package("declared-branch", branch="sync-target")).config_id
    _schedule(api, declared)

    _start(monkeypatch, api, T0)

    assert api.projection.list_runs(RunFilters(schedule_id=SCHEDULE_ID)).items == ()
    (failed,) = [
        event for event in api.projection.audit_events() if event.event_type == "infrahub_sync.schedule.start_failed"
    ]
    assert failed.details is not None
    assert failed.details.cause == "schedule-branch-declared"


def test_the_branch_name_is_derived_from_the_schedule_and_the_start() -> None:
    """``sync/<name>-<YYYYMMDD>-<first 6 characters of sha256(schedule_id:start)>``, in UTC."""
    expected = "sync/nightly-20260302-adcace"

    assert branch_review_branch("nightly", SCHEDULE_ID, T0) == expected
    assert branch_review_branch("nightly", SCHEDULE_ID, T0.astimezone(timezone(timedelta(hours=1)))) == expected
    assert branch_review_branch("nightly", SCHEDULE_ID, T0 + timedelta(days=1)) != expected


def test_two_attempts_of_one_start_derive_the_same_branch(api: ScheduleApi) -> None:
    schedule = _schedule(api)
    now = T0 + timedelta(seconds=5)

    first = decide_start(api.projection, schedule, T0, now, timedelta(seconds=300))
    second = decide_start(api.projection, schedule, T0, now, timedelta(seconds=300))

    assert first.kind == second.kind == "started"
    assert first.run.destination_branch == second.run.destination_branch == "sync/nightly-20260302-adcace"  # ty: ignore[unresolved-attribute]
    assert first.run.run_id != second.run.run_id  # ty: ignore[unresolved-attribute]


def test_the_run_writes_to_the_derived_branch_and_has_a_pending_branch_row(
    monkeypatch: pytest.MonkeyPatch, api: ScheduleApi
) -> None:
    _schedule(api)

    with pytest.raises(RuntimeError, match="BranchReviewNotAvailableError"):
        _start(monkeypatch, api, T0)

    (run,) = api.projection.list_runs(RunFilters(schedule_id=SCHEDULE_ID)).items
    assert run.destination_branch == "sync/nightly-20260302-adcace"
    row = api.projection.lookup_schedule_branch(run.run_id).value
    assert row is not None
    assert (row.schedule_id, row.branch, row.state, row.proposed_change_id) == (
        SCHEDULE_ID,
        "sync/nightly-20260302-adcace",
        "pending",
        None,
    )


def test_the_sync_stage_of_a_run_with_branch_review_fails_until_the_branch_steps_exist(
    monkeypatch: pytest.MonkeyPatch, api: ScheduleApi
) -> None:
    """The stage refuses to plan or write, and the run ends as failed with a named failure class."""
    _schedule(api)

    with pytest.raises(RuntimeError, match="BranchReviewNotAvailableError"):
        _start(monkeypatch, api, T0)

    (run,) = api.projection.list_runs(RunFilters(schedule_id=SCHEDULE_ID)).items
    stored = api.projection.lookup_run(run.run_id).value
    assert stored is not None
    assert (stored.phase, stored.outcome) == ("sync-failed", "failed")
    assert stored.results["sync_failure"]["error_type"] == "BranchReviewNotAvailableError"
    assert not stored.reconciliation_required
    assert api.projection.lookup_schedule_branch(run.run_id).value.state == "pending"  # ty: ignore[unresolved-attribute]


@pytest.mark.parametrize(
    ("action", "body"),
    [
        ("verify", {"reason": "check it"}),
        ("apply", {"expected_checksum": CHECKSUM, "confirm_writes": True}),
        ("resume", {"expected_checksum": CHECKSUM, "confirm_writes": True}),
    ],
)
def test_a_person_cannot_verify_apply_or_resume_a_run_with_branch_review(
    api: ScheduleApi, action: str, body: dict[str, Any]
) -> None:
    """Scenario 17: the next run of the schedule replaces the run."""
    schedule_id = api.create()["schedule_id"]
    run_id = api.scheduled_run(schedule_id, T0 + timedelta(days=400))

    response = api.client.post(f"/runs/{run_id}/{action}", json=body, headers=headers(OWNER_TOKEN))

    assert response.status_code == 409, response.text
    error = response.json()["error"]
    assert error["code"] == "scheduled-run-not-appliable"
    assert "next run of its schedule replaces it" in error["message"]
    refused = [
        event
        for event in api.projection.audit_events()
        if event.run_id == run_id and event.outcome == "refused-scheduled-run-not-appliable"
    ]
    assert [event.operation for event in refused] == [action]


def test_a_scheduled_run_that_writes_to_the_default_branch_is_not_refused_as_a_branch_review_run(
    api: ScheduleApi,
) -> None:
    """The refusal follows the run's branch row: a run with no row goes on to the usual checks."""
    schedule_id = api.create(write_target="default-branch")["schedule_id"]
    run_id = api.scheduled_run(schedule_id, T0 + timedelta(days=400))

    response = api.client.post(f"/runs/{run_id}/verify", json={}, headers=headers(OWNER_TOKEN))

    assert response.json().get("error", {}).get("code") != "scheduled-run-not-appliable"
