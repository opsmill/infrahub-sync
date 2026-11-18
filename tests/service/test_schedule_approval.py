"""The approval setting and the write target of a schedule (spec 021, scenarios 8 and 9, research R6).

Every API of this module is built with both write targets in its set of delivered write targets.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

pytest.importorskip("prefect")
pytest.importorskip("fastapi")

from infrahub_sync.service.settings import REQUIRE_APPROVAL_ENV
from tests.service.schedule_fixtures import BOTH_WRITE_TARGETS, ScheduleApi, headers, restarted, schedule_api

if TYPE_CHECKING:
    from pathlib import Path


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
