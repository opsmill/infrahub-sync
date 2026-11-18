"""The approval setting and the write target of a schedule (spec 021, scenarios 8 and 9, research R6).

Every API of this module is built with both write targets in its set of delivered write targets.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import pytest

pytest.importorskip("prefect")
pytest.importorskip("fastapi")

from structlog.testing import capture_logs

from infrahub_sync.product_store import ProductStoreProviderError
from infrahub_sync.service import schedules
from infrahub_sync.service.schedules import match_prefect_schedules
from infrahub_sync.service.settings import REQUIRE_APPROVAL_ENV
from tests.service.schedule_fixtures import BOTH_WRITE_TARGETS, ScheduleApi, headers, restarted, schedule_api

if TYPE_CHECKING:
    from pathlib import Path

    from infrahub_sync.product_store import ScheduleRecord


@pytest.fixture(name="api")
def _api(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> ScheduleApi:
    monkeypatch.delenv(REQUIRE_APPROVAL_ENV, raising=False)
    return schedule_api(monkeypatch, tmp_path, delivered=BOTH_WRITE_TARGETS)


def _restart(api: ScheduleApi) -> ScheduleApi:
    again = restarted(api, delivered=BOTH_WRITE_TARGETS)
    with again.client:
        pass
    return again


def test_with_required_approval_a_schedule_that_writes_to_the_default_branch_is_refused(
    api: ScheduleApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(REQUIRE_APPROVAL_ENV, "true")

    response = api.client.post("/schedules", json=api.body(write_target="default-branch"), headers=headers())

    assert response.status_code == 409, response.text
    error = response.json()["error"]
    assert error["code"] == "approval-required-for-schedule"
    assert REQUIRE_APPROVAL_ENV in error["message"]
    assert api.orchestration.calls == []
    refused = api.projection.audit_events()[-1]
    assert (refused.event_type, refused.outcome) == (
        "infrahub_sync.schedule.refused",
        "refused-approval-required-for-schedule",
    )


def test_the_approval_refusal_comes_before_the_refusal_of_a_write_target_that_is_not_delivered(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(REQUIRE_APPROVAL_ENV, "true")
    api = schedule_api(monkeypatch, tmp_path, delivered=frozenset())

    response = api.client.post("/schedules", json=api.body(write_target="default-branch"), headers=headers())

    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "approval-required-for-schedule"


def test_with_required_approval_a_change_to_the_default_branch_is_refused(
    api: ScheduleApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(REQUIRE_APPROVAL_ENV, "true")
    created = api.create()

    response = api.post(created["schedule_id"], "change", write_target="default-branch")

    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "approval-required-for-schedule"
    assert api.get(created["schedule_id"])["write_target"] == "branch-review"


def test_with_required_approval_a_schedule_with_branch_review_is_accepted(
    api: ScheduleApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The schedule needs no approval: its runs open a proposed change, which is the review."""
    monkeypatch.setenv(REQUIRE_APPROVAL_ENV, "true")

    created = api.create(write_target="branch-review")

    assert (created["state"], created["write_target"]) == ("active", "branch-review")
    assert api.orchestration.by_slug(created["schedule_id"]).active is True
    again = _restart(api)
    assert again.stored(created["schedule_id"]).state == "active"
    assert "infrahub_sync.schedule.stopped" not in again.event_types()


def test_without_required_approval_a_schedule_that_writes_to_the_default_branch_is_accepted(api: ScheduleApi) -> None:
    created = api.create(write_target="default-branch", write_when_left_out=True)

    assert (created["write_target"], created["write_when_left_out"], created["left_out_set_by"]) == (
        "default-branch",
        True,
        "owner",
    )
    assert created["left_out_set_at"] == created["created_at"]
    changed = api.post(created["schedule_id"], "change", write_when_left_out=False).json()
    assert (changed["write_when_left_out"], changed["left_out_set_by"], changed["left_out_set_at"]) == (
        False,
        None,
        None,
    )


def test_a_restart_with_approval_turned_on_stops_a_default_branch_schedule(
    api: ScheduleApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    schedule_id = api.create(write_target="default-branch")["schedule_id"]
    assert api.orchestration.by_slug(schedule_id).active is True

    monkeypatch.setenv(REQUIRE_APPROVAL_ENV, "true")
    again = _restart(api)

    stored = again.stored(schedule_id)
    assert (stored.state, stored.state_reason) == ("stopped", "approval-required")
    assert again.orchestration.by_slug(schedule_id).active is False
    stopped = [
        event for event in again.projection.audit_events() if event.event_type == "infrahub_sync.schedule.stopped"
    ]
    assert [(event.actor, event.operation, event.outcome) for event in stopped] == [
        ("owner", "schedule-start", "approval-required")
    ]


def test_a_stopped_default_branch_schedule_is_resumed_only_when_the_reason_no_longer_holds(
    api: ScheduleApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    schedule_id = api.create(write_target="default-branch")["schedule_id"]
    monkeypatch.setenv(REQUIRE_APPROVAL_ENV, "true")
    again = _restart(api)

    refused = again.post(schedule_id, "resume")

    assert refused.status_code == 409, refused.text
    error = refused.json()["error"]
    assert error["code"] == "schedule-stopped"
    assert "approval-required" in error["message"]

    monkeypatch.delenv(REQUIRE_APPROVAL_ENV)
    again = _restart(again)
    resumed = again.post(schedule_id, "resume")
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["state"] == "active"


def test_a_resume_of_a_paused_default_branch_schedule_while_approval_is_required_stops_it(
    api: ScheduleApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FR-002 and FR-013: the schedule cannot run, so the resume stops it and names the action it needs."""
    schedule_id = api.create(write_target="default-branch")["schedule_id"]
    assert api.post(schedule_id, "pause").status_code == 200
    monkeypatch.setenv(REQUIRE_APPROVAL_ENV, "true")
    again = _restart(api)
    assert again.stored(schedule_id).state == "paused"

    response = again.post(schedule_id, "resume")

    assert response.status_code == 409, response.text
    error = response.json()["error"]
    assert error["code"] == "schedule-stopped"
    assert "approval-required" in error["message"]
    assert "change the write target of the schedule to branch-review" in error["message"]
    stored = again.stored(schedule_id)
    assert (stored.state, stored.state_reason) == ("stopped", "approval-required")
    assert again.orchestration.by_slug(schedule_id).active is False
    stopped = [
        event for event in again.projection.audit_events() if event.event_type == "infrahub_sync.schedule.stopped"
    ]
    assert [(event.actor, event.operation, event.outcome) for event in stopped] == [
        ("owner", "schedule-resume", "approval-required")
    ]
    changed = again.post(schedule_id, "change", write_target="branch-review")
    assert changed.status_code == 200, changed.text
    assert (changed.json()["state"], changed.json()["state_reason"]) == ("active", None)


def test_a_resume_of_a_paused_branch_review_schedule_while_approval_is_required_makes_it_active(
    api: ScheduleApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    schedule_id = api.create(write_target="branch-review")["schedule_id"]
    assert api.post(schedule_id, "pause").status_code == 200
    monkeypatch.setenv(REQUIRE_APPROVAL_ENV, "true")

    response = api.post(schedule_id, "resume")

    assert response.status_code == 200, response.text
    assert response.json()["state"] == "active"


def test_a_change_to_branch_review_makes_a_schedule_stopped_for_approval_active(
    api: ScheduleApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    schedule_id = api.create(write_target="default-branch")["schedule_id"]
    monkeypatch.setenv(REQUIRE_APPROVAL_ENV, "true")
    again = _restart(api)

    response = again.post(schedule_id, "change", write_target="branch-review")

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["state"], body["state_reason"], body["write_target"]) == ("active", None, "branch-review")
    assert again.orchestration.by_slug(schedule_id).active is True


def _stopped_for_repeated_failures(api: ScheduleApi, schedule_id: str) -> None:
    """Store the schedule as the end of its fifth failed run stops it, and let Prefect match the store."""
    stored = api.stored(schedule_id)
    api.projection.update_schedule(stored.model_copy(update={"state": "stopped", "state_reason": "repeated-failures"}))
    asyncio.run(match_prefect_schedules(api.projection, api.orchestration))


def test_a_resume_after_repeated_failures_while_approval_is_required_stops_the_schedule_for_approval(
    api: ScheduleApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FR-002: a resume of a stopped schedule checks the approval setting too, not only the reason it stopped for."""
    schedule_id = api.create(write_target="default-branch")["schedule_id"]
    _stopped_for_repeated_failures(api, schedule_id)
    monkeypatch.setenv(REQUIRE_APPROVAL_ENV, "true")

    response = api.post(schedule_id, "resume")

    assert response.status_code == 409, response.text
    error = response.json()["error"]
    assert error["code"] == "schedule-stopped"
    assert "approval-required" in error["message"]
    stored = api.stored(schedule_id)
    assert (stored.state, stored.state_reason) == ("stopped", "approval-required")
    assert api.orchestration.by_slug(schedule_id).active is False
    stopped = [event for event in api.projection.audit_events() if event.event_type == "infrahub_sync.schedule.stopped"]
    assert [(event.actor, event.operation, event.outcome) for event in stopped] == [
        ("owner", "schedule-resume", "approval-required")
    ]
    again = api.post(schedule_id, "resume")
    assert again.status_code == 409, again.text
    assert len([event for event in api.event_types() if event == "infrahub_sync.schedule.stopped"]) == 1


def test_a_resume_after_repeated_failures_makes_a_schedule_with_branch_review_active_while_approval_is_required(
    api: ScheduleApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    schedule_id = api.create(write_target="branch-review")["schedule_id"]
    _stopped_for_repeated_failures(api, schedule_id)
    monkeypatch.setenv(REQUIRE_APPROVAL_ENV, "true")

    response = api.post(schedule_id, "resume")

    assert response.status_code == 200, response.text
    assert (response.json()["state"], response.json()["state_reason"]) == ("active", None)


def test_a_resume_that_stops_a_paused_schedule_saves_the_stop_in_one_write(
    api: ScheduleApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A schedule must never be left active with no reason when the second of two writes fails."""
    schedule_id = api.create(write_target="default-branch")["schedule_id"]
    assert api.post(schedule_id, "pause").status_code == 200
    monkeypatch.setenv(REQUIRE_APPROVAL_ENV, "true")
    writes: list[tuple[str, str | None, int]] = []
    update = api.projection.update_schedule

    def recorded(schedule: ScheduleRecord, **settings: Any) -> ScheduleRecord:  # noqa: ANN401 - passes them on.
        writes.append((schedule.state, schedule.state_reason, len(settings.get("events", ()))))
        return update(schedule, **settings)

    monkeypatch.setattr(api.projection, "update_schedule", recorded)

    response = api.post(schedule_id, "resume")

    assert response.status_code == 409, response.text
    assert writes == [("stopped", "approval-required", 1)]


def _store_fails(api: ScheduleApi, monkeypatch: pytest.MonkeyPatch, times: int) -> list[int]:
    """Make the list of schedules fail ``times`` times with a store error, then work; return the calls."""
    calls: list[int] = []
    real = api.projection.list_schedules

    def flaky(*args: Any, **kwargs: Any) -> Any:  # noqa: ANN401 - passes the page through.
        calls.append(len(calls))
        if len(calls) <= times:
            raise ProductStoreProviderError
        return real(*args, **kwargs)

    monkeypatch.setattr(api.projection, "list_schedules", flaky)
    monkeypatch.setattr(schedules, "_STOP_RETRY_SECONDS", 0)
    return calls


def test_a_start_retries_the_stop_step_after_a_store_error(api: ScheduleApi, monkeypatch: pytest.MonkeyPatch) -> None:
    schedule_id = api.create(write_target="default-branch")["schedule_id"]
    monkeypatch.setenv(REQUIRE_APPROVAL_ENV, "true")
    _store_fails(api, monkeypatch, 2)

    again = _restart(api)

    stored = again.stored(schedule_id)
    assert (stored.state, stored.state_reason) == ("stopped", "approval-required")
    assert again.orchestration.by_slug(schedule_id).active is False


def test_a_start_whose_stop_step_keeps_failing_logs_an_error(api: ScheduleApi, monkeypatch: pytest.MonkeyPatch) -> None:
    schedule_id = api.create(write_target="default-branch")["schedule_id"]
    monkeypatch.setenv(REQUIRE_APPROVAL_ENV, "true")
    calls = _store_fails(api, monkeypatch, 100)

    with capture_logs() as logs:
        again = _restart(api)

    assert len(calls) == schedules._STOP_ATTEMPTS
    assert again.stored(schedule_id).state == "active"
    failed = [entry for entry in logs if entry["event"] == "schedules.stop_failed"]
    assert [(entry["log_level"], entry["error_class"]) for entry in failed] == [("error", "ProductStoreProviderError")]
