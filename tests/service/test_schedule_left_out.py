"""The left-out setting of a schedule (spec 021, user story 2, scenarios 2 to 5, FR-004, FR-005, research R5).

A scheduled run that writes to the default branch writes the remaining records when the setting
is on, and stops after the plan when it is off. Every scheduled run records its left-out count in
its stage result and in ``details.left_out`` of its result event. The setting is refused for a
schedule with branch review. Every API of this module is built with both write targets in its set
of delivered write targets.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

import pytest

pytest.importorskip("prefect")
pytest.importorskip("fastapi")

from infrahub_sync.product_store import AuditEvent, ProductRun, RunFilters, ScheduleRecord
from infrahub_sync.service.flow import service_sync_run
from infrahub_sync.service.settings import REQUIRE_APPROVAL_ENV
from tests.service.schedule_fixtures import ADMIN_TOKEN, BOTH_WRITE_TARGETS, ScheduleApi, headers, schedule_api
from tests.service.test_schedule_branch_review import (
    SCHEDULE_ID,
    T0,
    FakeInfrahub,
    _left_out_report,
    _start,
    bind_fake_infrahub,
)
from tests.service.test_sync_left_out_stop import _RUN_ID, _harness, _report

if TYPE_CHECKING:
    from pathlib import Path

_STOP_OUTCOME = "left-out-review-required"


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> ScheduleApi:
    monkeypatch.delenv(REQUIRE_APPROVAL_ENV, raising=False)
    return schedule_api(monkeypatch, tmp_path, delivered=BOTH_WRITE_TARGETS)


@pytest.fixture
def infrahub(monkeypatch: pytest.MonkeyPatch, api: ScheduleApi) -> FakeInfrahub:
    """The fake Infrahub client and the plan and write doubles of the branch review tests."""
    return bind_fake_infrahub(monkeypatch, api)


def _schedule(api: ScheduleApi, *, write_when_left_out: bool) -> None:
    """Store an active schedule named ``nightly`` that writes to the default branch, created before ``T0``."""
    created = T0 - timedelta(minutes=30)
    api.projection.create_schedule(
        ScheduleRecord.model_validate(
            {
                "schedule_id": SCHEDULE_ID,
                "name": "nightly",
                "config_id": api.config_id,
                "registry_version": 1,
                "cron": "0 2 * * *",
                "timezone": "UTC",
                "write_target": "default-branch",
                "write_when_left_out": write_when_left_out,
                "left_out_set_by": "owner" if write_when_left_out else None,
                "left_out_set_at": created if write_when_left_out else None,
                "state": "active",
                "owner": "owner",
                "created_at": created,
                "updated_at": created,
            }
        )
    )


def _only_run(api: ScheduleApi) -> ProductRun:
    (run,) = api.projection.list_runs(RunFilters(schedule_id=SCHEDULE_ID)).items
    stored = api.projection.lookup_run(run.run_id).value
    assert stored is not None
    return stored


def _result_event(api: ScheduleApi, run_id: str) -> AuditEvent:
    """The one event of the `sync` stage result of the run."""
    (event,) = [
        event
        for event in api.projection.audit_events()
        if event.run_id == run_id and event.event_type in {"infrahub_sync.run.synced", "infrahub_sync.run.failed"}
    ]
    return event


def _error(response: Any) -> dict[str, Any]:  # noqa: ANN401 - the HTTP response of the test client.
    return response.json()["error"]


# --- The worker: the setting reaches the `sync` stage (scenarios 2 and 3) --------------------------


def test_with_the_setting_on_the_run_writes_the_remaining_records_and_records_the_count(
    monkeypatch: pytest.MonkeyPatch, api: ScheduleApi, infrahub: FakeInfrahub
) -> None:
    """Scenario 2: the run writes to the default branch and its result event holds the left-out count."""
    infrahub.report = _left_out_report()
    _schedule(api, write_when_left_out=True)

    _start(monkeypatch, api, T0)

    run = _only_run(api)
    assert (run.phase, run.outcome) == ("applied", "applied")
    assert infrahub.confirm_left_out == [True]
    assert "SyncBranchCreate" not in infrahub.events
    assert run.results["left_out"] == 2
    event = _result_event(api, run.run_id)
    assert event.event_type == "infrahub_sync.run.synced"
    assert event.details is not None
    assert event.details.left_out == 2


def test_with_the_setting_off_the_run_stops_after_the_plan(
    monkeypatch: pytest.MonkeyPatch, api: ScheduleApi, infrahub: FakeInfrahub
) -> None:
    """Scenario 3: the run writes nothing and waits for the review of the records left out."""
    infrahub.report = _left_out_report()
    _schedule(api, write_when_left_out=False)

    _start(monkeypatch, api, T0)

    run = _only_run(api)
    assert (run.phase, run.outcome) == ("planned", _STOP_OUTCOME)
    assert "execute-run:apply" not in infrahub.events
    assert infrahub.confirm_left_out == []
    assert run.results["failure"]["failure_type"] == "left_out_review_required"
    assert run.results["left_out"] == 2
    event = _result_event(api, run.run_id)
    assert event.details is not None
    assert event.details.left_out == 2


def test_a_run_that_leaves_out_nothing_records_a_count_of_zero(
    monkeypatch: pytest.MonkeyPatch, api: ScheduleApi, infrahub: FakeInfrahub
) -> None:
    _schedule(api, write_when_left_out=False)

    _start(monkeypatch, api, T0)

    run = _only_run(api)
    assert (run.phase, run.outcome) == ("applied", "applied")
    assert infrahub.confirm_left_out == [False]
    assert run.results["left_out"] == 0
    event = _result_event(api, run.run_id)
    assert event.details is not None
    assert event.details.left_out == 0


class _WriteFailedError(RuntimeError):
    """The failure of the write of a scheduled run."""


def test_a_run_that_fails_after_its_plan_records_the_count_in_its_failed_event(
    monkeypatch: pytest.MonkeyPatch, api: ScheduleApi, infrahub: FakeInfrahub
) -> None:
    infrahub.report = _left_out_report()
    infrahub.apply_failure = _WriteFailedError("the write failed")
    _schedule(api, write_when_left_out=True)

    with pytest.raises(RuntimeError, match="the write failed"):
        _start(monkeypatch, api, T0)

    run = _only_run(api)
    assert run.phase == "sync-failed"
    assert run.results["left_out"] == 2
    event = _result_event(api, run.run_id)
    assert event.event_type == "infrahub_sync.run.failed"
    assert event.details is not None
    assert event.details.left_out == 2


def test_a_run_with_branch_review_records_the_count_in_its_result_and_its_event(
    monkeypatch: pytest.MonkeyPatch, api: ScheduleApi, infrahub: FakeInfrahub
) -> None:
    """A run with branch review always writes the remaining records, whatever the setting is (R11)."""
    infrahub.report = _left_out_report()
    api.projection.create_schedule(
        ScheduleRecord.model_validate(
            {
                "schedule_id": SCHEDULE_ID,
                "name": "nightly",
                "config_id": api.config_id,
                "registry_version": 1,
                "cron": "0 2 * * *",
                "timezone": "UTC",
                "write_target": "branch-review",
                "state": "active",
                "owner": "owner",
                "created_at": T0 - timedelta(minutes=30),
                "updated_at": T0 - timedelta(minutes=30),
            }
        )
    )

    _start(monkeypatch, api, T0)

    run = _only_run(api)
    assert (run.phase, run.outcome) == ("applied", "applied")
    assert infrahub.confirm_left_out == [True]
    assert run.results["left_out"] == 2
    assert run.results["branch_review"]["branch_state"] == "written"
    event = _result_event(api, run.run_id)
    assert event.details is not None
    assert event.details.left_out == 2


def test_a_sync_that_a_person_starts_still_stops_and_records_no_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A `sync` of a person keeps the stop, even with `confirm_left_out` among its flow parameters."""
    harness = _harness(tmp_path, monkeypatch, report=_report())

    result = service_sync_run.fn(_RUN_ID, "sync", *harness.binding, confirm_writes=True, confirm_left_out=True)

    assert isinstance(result, dict)
    assert result["outcome"] == _STOP_OUTCOME
    assert "execute-run:apply" not in harness.events
    run = harness.stored()
    assert "left_out" not in run.results
    (event,) = [event for event in harness.projection.audit_events() if event.event_type == "infrahub_sync.run.synced"]
    assert event.details is None


# --- The Sync API: who turned the setting on, and the refusal for branch review (scenarios 4, 5) ---


def test_turning_the_setting_on_records_who_and_when_and_turning_it_off_clears_both(api: ScheduleApi) -> None:
    """Scenario 4: the actor and the time are those of the change that turned the setting on."""
    created = api.create(write_target="default-branch")
    assert (created["write_when_left_out"], created["left_out_set_by"], created["left_out_set_at"]) == (
        False,
        None,
        None,
    )

    turned_on = api.post(created["schedule_id"], "change", ADMIN_TOKEN, write_when_left_out=True)

    assert turned_on.status_code == 200, turned_on.text
    body = turned_on.json()
    assert (body["write_when_left_out"], body["left_out_set_by"]) == (True, "admin")
    assert body["left_out_set_at"] == body["updated_at"]
    assert datetime.fromisoformat(body["left_out_set_at"]) > datetime.fromisoformat(created["created_at"])
    kept = api.post(created["schedule_id"], "change", cron="0 3 * * *").json()
    assert (kept["left_out_set_by"], kept["left_out_set_at"]) == ("admin", body["left_out_set_at"])
    turned_off = api.post(created["schedule_id"], "change", write_when_left_out=False).json()
    assert (turned_off["write_when_left_out"], turned_off["left_out_set_by"], turned_off["left_out_set_at"]) == (
        False,
        None,
        None,
    )


def test_turning_the_setting_on_for_branch_review_is_refused_on_create(api: ScheduleApi) -> None:
    """Scenario 5: a run with branch review always writes the remaining records."""
    response = api.client.post(
        "/schedules", json=api.body(write_target="branch-review", write_when_left_out=True), headers=headers()
    )

    assert response.status_code == 422, response.text
    assert _error(response)["code"] == "schedule-left-out-not-applicable"
    assert api.client.get("/schedules", headers={"Authorization": headers()["Authorization"]}).json()["items"] == []
    refused = api.projection.audit_events()[-1]
    assert (refused.event_type, refused.outcome) == (
        "infrahub_sync.schedule.refused",
        "refused-schedule-left-out-not-applicable",
    )


def test_turning_the_setting_on_for_branch_review_is_refused_on_change(api: ScheduleApi) -> None:
    created = api.create(write_target="branch-review")

    response = api.post(created["schedule_id"], "change", write_when_left_out=True)

    assert response.status_code == 422, response.text
    assert _error(response)["code"] == "schedule-left-out-not-applicable"
    assert api.get(created["schedule_id"]) == created


def test_a_change_to_branch_review_that_turns_the_setting_on_is_refused(api: ScheduleApi) -> None:
    created = api.create(write_target="default-branch")

    response = api.post(created["schedule_id"], "change", write_target="branch-review", write_when_left_out=True)

    assert response.status_code == 422, response.text
    assert _error(response)["code"] == "schedule-left-out-not-applicable"
    assert api.get(created["schedule_id"])["write_target"] == "default-branch"


def test_a_change_of_the_write_target_to_branch_review_turns_the_setting_off(api: ScheduleApi) -> None:
    """Scenario 5: the setting does not apply to branch review, so the change sets it to false."""
    created = api.create(write_target="default-branch", write_when_left_out=True)

    response = api.post(created["schedule_id"], "change", write_target="branch-review")

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["write_target"], body["write_when_left_out"], body["left_out_set_by"], body["left_out_set_at"]) == (
        "branch-review",
        False,
        None,
        None,
    )


def test_a_change_to_the_default_branch_keeps_the_setting_off_until_it_is_turned_on(api: ScheduleApi) -> None:
    created = api.create(write_target="branch-review")

    changed = api.post(created["schedule_id"], "change", write_target="default-branch", write_when_left_out=True)

    assert changed.status_code == 200, changed.text
    body = changed.json()
    assert (body["write_target"], body["write_when_left_out"], body["left_out_set_by"]) == (
        "default-branch",
        True,
        "owner",
    )
    assert body["left_out_set_at"] is not None
    assert datetime.fromisoformat(body["left_out_set_at"]).tzinfo == timezone.utc
