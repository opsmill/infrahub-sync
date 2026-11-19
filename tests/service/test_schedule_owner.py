"""The owner of a schedule and the start of the API process (spec 021, scenarios 14 and 15, research R8).

The owner is an account of the product store. A test disables it to make the owner unable to act.
"""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

import pytest

pytest.importorskip("prefect")
pytest.importorskip("fastapi")

from infrahub_sync.service.auth import Principal, StoreAccountResolver
from infrahub_sync.service.settings import REQUIRE_APPROVAL_ENV
from tests.service.accounts import IDLE_SECONDS
from tests.service.schedule_fixtures import ADMIN_TOKEN, ScheduleApi, restarted, schedule_api

if TYPE_CHECKING:
    from pathlib import Path

# Whether the owner's account is enabled when the API process starts again.
WITH_OWNER = True
WITHOUT_OWNER = False


@pytest.fixture(name="api")
def _api(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> ScheduleApi:
    return schedule_api(monkeypatch, tmp_path)


def _restart(api: ScheduleApi, _monkeypatch: pytest.MonkeyPatch, owner_enabled: bool) -> ScheduleApi:  # noqa: FBT001
    """Enable or disable the owner's account, start a new API process and run the step of its start."""
    owner = api.projection.lookup_account(api.account_id("owner"))
    assert owner is not None
    if owner.disabled == owner_enabled:
        (api.enable if owner_enabled else api.disable)("owner")
    again = restarted(api)
    with again.client:
        pass
    return again


def test_a_start_of_the_api_stops_a_schedule_whose_owner_is_gone(
    api: ScheduleApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    schedule_id = api.create()["schedule_id"]
    assert api.orchestration.by_slug(schedule_id).active is True

    again = _restart(api, monkeypatch, WITHOUT_OWNER)

    stored = again.stored(schedule_id)
    assert (stored.state, stored.state_reason, stored.owner) == (
        "stopped",
        "owner-not-found",
        again.account_id("owner"),
    )
    assert again.orchestration.by_slug(schedule_id).active is False
    assert stored.prefect_synced is True
    stopped = [
        event for event in again.projection.audit_events() if event.event_type == "infrahub_sync.schedule.stopped"
    ]
    assert len(stopped) == 1
    event = stopped[0]
    assert (event.actor, event.operation, event.outcome, event.reason) == (
        again.account_id("owner"),
        "schedule-start",
        "owner-not-found",
        None,
    )
    assert ("schedule", schedule_id) in {(resource.kind, resource.id) for resource in event.related}
    assert event.details is None


def test_the_stopped_schedule_shows_its_reason_and_no_next_start(
    api: ScheduleApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    schedule_id = api.create()["schedule_id"]

    again = _restart(api, monkeypatch, WITHOUT_OWNER)
    response = again.client.get(f"/schedules/{schedule_id}", headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})

    body = response.json()
    assert (body["state"], body["state_reason"], body["next_start_at"]) == ("stopped", "owner-not-found", None)


def test_a_second_start_writes_no_second_event(api: ScheduleApi, monkeypatch: pytest.MonkeyPatch) -> None:
    api.create()

    again = _restart(api, monkeypatch, WITHOUT_OWNER)
    again = _restart(again, monkeypatch, WITHOUT_OWNER)

    assert again.event_types().count("infrahub_sync.schedule.stopped") == 1


def test_a_start_leaves_a_schedule_whose_owner_exists_and_a_paused_schedule(
    api: ScheduleApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    kept = api.create()["schedule_id"]

    again = _restart(api, monkeypatch, WITH_OWNER)

    assert again.stored(kept).state == "active"
    assert again.post(kept, "pause").status_code == 200
    again = _restart(again, monkeypatch, WITHOUT_OWNER)
    assert again.stored(kept).state == "paused"
    assert "infrahub_sync.schedule.stopped" not in again.event_types()


def test_a_resume_is_refused_while_the_owner_is_gone(api: ScheduleApi, monkeypatch: pytest.MonkeyPatch) -> None:
    schedule_id = api.create()["schedule_id"]
    again = _restart(api, monkeypatch, WITHOUT_OWNER)

    response = again.post(schedule_id, "resume", ADMIN_TOKEN)

    assert response.status_code == 409, response.text
    error = response.json()["error"]
    assert error["code"] == "schedule-stopped"
    assert "owner-not-found" in error["message"]
    assert again.stored(schedule_id).state == "stopped"
    assert again.orchestration.by_slug(schedule_id).active is False
    refused = again.projection.audit_events()[-1]
    assert (refused.event_type, refused.outcome) == ("infrahub_sync.schedule.refused", "refused-schedule-stopped")


def test_a_resume_of_a_paused_schedule_whose_owner_is_gone_stops_it(
    api: ScheduleApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FR-013: a resume succeeds only when no reason to stop holds, also for a paused schedule."""
    schedule_id = api.create()["schedule_id"]
    assert api.post(schedule_id, "pause").status_code == 200
    again = _restart(api, monkeypatch, WITHOUT_OWNER)
    assert again.stored(schedule_id).state == "paused"

    response = again.post(schedule_id, "resume", ADMIN_TOKEN)

    assert response.status_code == 409, response.text
    error = response.json()["error"]
    assert error["code"] == "schedule-stopped"
    assert "owner-not-found" in error["message"]
    assert "enable the account" in error["message"]
    stored = again.stored(schedule_id)
    assert (stored.state, stored.state_reason, stored.owner) == (
        "stopped",
        "owner-not-found",
        again.account_id("owner"),
    )
    assert again.orchestration.by_slug(schedule_id).active is False
    stopped = [
        event for event in again.projection.audit_events() if event.event_type == "infrahub_sync.schedule.stopped"
    ]
    assert [(event.actor, event.operation, event.outcome, event.reason) for event in stopped] == [
        (again.account_id("admin"), "schedule-resume", "owner-not-found", None)
    ]
    assert ("schedule", schedule_id) in {(resource.kind, resource.id) for resource in stopped[0].related}
    refused = again.projection.audit_events()[-1]
    assert (refused.event_type, refused.outcome) == ("infrahub_sync.schedule.refused", "refused-schedule-stopped")


def test_a_second_resume_of_the_stopped_schedule_writes_no_second_stop(
    api: ScheduleApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    schedule_id = api.create()["schedule_id"]
    assert api.post(schedule_id, "pause").status_code == 200
    again = _restart(api, monkeypatch, WITHOUT_OWNER)
    assert again.post(schedule_id, "resume", ADMIN_TOKEN).status_code == 409

    response = again.post(schedule_id, "resume", ADMIN_TOKEN)

    assert response.status_code == 409, response.text
    assert again.event_types().count("infrahub_sync.schedule.stopped") == 1
    assert again.stored(schedule_id).state == "stopped"


def test_a_resume_of_a_paused_schedule_whose_owner_exists_makes_it_active(
    api: ScheduleApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    schedule_id = api.create()["schedule_id"]
    assert api.post(schedule_id, "pause").status_code == 200
    again = _restart(api, monkeypatch, WITH_OWNER)

    response = again.post(schedule_id, "resume")

    assert response.status_code == 200, response.text
    assert (response.json()["state"], response.json()["state_reason"]) == ("active", None)
    assert "infrahub_sync.schedule.stopped" not in again.event_types()


def test_a_resume_succeeds_when_the_owner_exists_again(api: ScheduleApi, monkeypatch: pytest.MonkeyPatch) -> None:
    schedule_id = api.create()["schedule_id"]
    again = _restart(api, monkeypatch, WITHOUT_OWNER)

    again = _restart(again, monkeypatch, WITH_OWNER)
    assert again.stored(schedule_id).state == "stopped"
    response = again.post(schedule_id, "resume")

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["state"], body["state_reason"], body["owner"]) == ("active", None, "owner")
    assert body["next_start_at"] is not None
    assert again.orchestration.by_slug(schedule_id).active is True


def test_a_resume_after_repeated_failures_always_succeeds_and_clears_the_count(
    api: ScheduleApi, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    schedule_id = api.create()["schedule_id"]
    stored = api.stored(schedule_id)
    # The worker stops a schedule with the end of its fifth failed run, and no method of the
    # service sets the count, so the row is written as the store writes it then.
    with sqlite3.connect(tmp_path.resolve() / "product-records.sqlite3") as connection:
        connection.execute(
            "UPDATE schedules SET state = 'stopped', state_reason = 'repeated-failures', "
            "consecutive_failures = 5, prefect_synced = 0 WHERE schedule_id = ?",
            (schedule_id,),
        )
        connection.commit()
    assert api.stored(schedule_id).consecutive_failures == 5
    again = _restart(api, monkeypatch, WITH_OWNER)
    assert again.orchestration.by_slug(schedule_id).active is False
    assert again.stored(schedule_id).prefect_synced is True

    response = again.post(schedule_id, "resume")

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["state"], body["state_reason"], body["consecutive_failures"]) == ("active", None, 0)
    assert again.orchestration.by_slug(schedule_id).active is True
    assert again.stored(schedule_id).created_at == stored.created_at


def test_a_resume_after_the_owner_returns_checks_approval_too(
    api: ScheduleApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FR-002: the owner's account is enabled again, but the schedule writes to the default branch while approval is on."""
    schedule_id = api.create(write_target="default-branch")["schedule_id"]
    stopped = _restart(api, monkeypatch, WITHOUT_OWNER)
    assert stopped.stored(schedule_id).state_reason == "owner-not-found"
    monkeypatch.setenv(REQUIRE_APPROVAL_ENV, "true")
    again = _restart(api, monkeypatch, WITH_OWNER)

    response = again.post(schedule_id, "resume")

    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "schedule-stopped"
    assert "approval-required" in response.json()["error"]["message"]
    stored = again.stored(schedule_id)
    assert (stored.state, stored.state_reason) == ("stopped", "approval-required")
    events = [
        event for event in again.projection.audit_events() if event.event_type == "infrahub_sync.schedule.stopped"
    ]
    assert [(event.operation, event.outcome) for event in events] == [
        ("schedule-start", "owner-not-found"),
        ("schedule-resume", "approval-required"),
    ]


def test_the_owner_lookup_returns_an_enabled_account_only(api: ScheduleApi) -> None:
    """The check of the owner reads the account in the store; it never reads a token."""
    resolver = StoreAccountResolver(api.projection, idle_seconds=IDLE_SECONDS)
    owner_id = api.account_id("owner")

    principal = resolver.principal_of(owner_id)

    assert principal == Principal(actor=owner_id, administrator=False, name="owner")
    assert resolver.principal_of("acct_" + "0" * 32) is None
    assert resolver.principal_of("owner") is None
    api.disable("owner")
    assert resolver.principal_of(owner_id) is None
