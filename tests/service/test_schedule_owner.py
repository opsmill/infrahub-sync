"""The owner of a schedule and the start of the API process (spec 021, scenarios 14 and 15, research R8)."""

from __future__ import annotations

import json
import sqlite3
from typing import TYPE_CHECKING

import pytest

pytest.importorskip("prefect")
pytest.importorskip("fastapi")

from infrahub_sync.service.auth import PRINCIPALS_ENV
from tests.service.schedule_fixtures import ADMIN_TOKEN, PRINCIPALS, ScheduleApi, restarted, schedule_api

if TYPE_CHECKING:
    from pathlib import Path

WITHOUT_OWNER = {name: entry for name, entry in PRINCIPALS.items() if name != "owner"}


@pytest.fixture(name="api")
def _api(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> ScheduleApi:
    return schedule_api(monkeypatch, tmp_path)


def _restart(
    api: ScheduleApi, monkeypatch: pytest.MonkeyPatch, principals: dict[str, dict[str, object]]
) -> ScheduleApi:
    """Start a new API process with another set of actors, and run the step of its start."""
    monkeypatch.setenv(PRINCIPALS_ENV, json.dumps(principals))
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
    assert (stored.state, stored.state_reason, stored.owner) == ("stopped", "owner-not-found", "owner")
    assert again.orchestration.by_slug(schedule_id).active is False
    assert stored.prefect_synced is True
    stopped = [
        event for event in again.projection.audit_events() if event.event_type == "infrahub_sync.schedule.stopped"
    ]
    assert len(stopped) == 1
    event = stopped[0]
    assert (event.actor, event.operation, event.outcome, event.reason) == (
        "owner",
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

    again = _restart(api, monkeypatch, PRINCIPALS)

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


def test_a_resume_succeeds_when_the_owner_exists_again(api: ScheduleApi, monkeypatch: pytest.MonkeyPatch) -> None:
    schedule_id = api.create()["schedule_id"]
    again = _restart(api, monkeypatch, WITHOUT_OWNER)

    again = _restart(again, monkeypatch, PRINCIPALS)
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
    again = _restart(api, monkeypatch, PRINCIPALS)
    assert again.orchestration.by_slug(schedule_id).active is False
    assert again.stored(schedule_id).prefect_synced is True

    response = again.post(schedule_id, "resume")

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["state"], body["state_reason"], body["consecutive_failures"]) == ("active", None, 0)
    assert again.orchestration.by_slug(schedule_id).active is True
    assert again.stored(schedule_id).created_at == stored.created_at
