"""Taking over a schedule, and the report of a newer configuration version (spec 021, user story 3).

Scenario 7: an administrator takes over a schedule that stopped because its owner's actor no
longer exists, and the next start produces a run of the administrator. Scenario 6: the schedule
reports a newer registered version and still runs its own (FR-007, FR-026).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest

pytest.importorskip("prefect")
pytest.importorskip("fastapi")

from infrahub_sync.configuration import ConfigurationPackage
from infrahub_sync.product_store import ExecutionFinishWriteback, ProductRun, RunFilters
from infrahub_sync.service import flow as service_flow
from infrahub_sync.service.auth import PRINCIPALS_ENV
from tests.service.schedule_fixtures import (
    ADMIN_TOKEN,
    OTHER_TOKEN,
    OWNER_TOKEN,
    PRINCIPALS,
    WORKER_ID,
    ScheduleApi,
    package,
    restarted,
    schedule_api,
)

if TYPE_CHECKING:
    from pathlib import Path

WITHOUT_OWNER = {name: entry for name, entry in PRINCIPALS.items() if name != "owner"}
TAKEN_OVER = "infrahub_sync.schedule.taken_over"


@pytest.fixture(name="api")
def _api(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> ScheduleApi:
    return schedule_api(monkeypatch, tmp_path)


def _restart(api: ScheduleApi, monkeypatch: pytest.MonkeyPatch, principals: dict[str, dict[str, Any]]) -> ScheduleApi:
    """Start a new API process with another set of actors, and run the step of its start."""
    monkeypatch.setenv(PRINCIPALS_ENV, json.dumps(principals))
    again = restarted(api)
    with again.client:
        pass
    return again


def _succeeded(run_id: str, stage: str, *_args: object, **_kwargs: object) -> tuple[dict[str, Any], Any]:
    result = {"run_id": run_id, "stage": stage, "outcome": "applied"}
    return result, ExecutionFinishWriteback(
        phase="applied", outcome="applied", finished_at=datetime.now(timezone.utc), summary={}, results=result
    )


def _next_start(monkeypatch: pytest.MonkeyPatch, api: ScheduleApi, schedule_id: str) -> dict[str, Any] | object:
    """Run the service flow for the next start of the schedule, on time."""
    response = api.client.get(f"/schedules/{schedule_id}", headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})
    assert response.status_code == 200, response.text
    resource = response.json()
    assert resource["next_start_at"] is not None
    start = datetime.fromisoformat(resource["next_start_at"])
    monkeypatch.setattr(service_flow, "_runtime", lambda: api.projection)
    monkeypatch.setattr(service_flow, "_run_logger", lambda: (logging.getLogger("test-schedule-takeover"), False))
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
    monkeypatch.setattr(service_flow, "_execute_stage", _succeeded)
    return service_flow.service_sync_run.fn(stage="sync", schedule_id=schedule_id)


def _runs(api: ScheduleApi, schedule_id: str) -> list[ProductRun]:
    return list(api.projection.list_runs(RunFilters(schedule_id=schedule_id), limit=200).items)


def test_an_administrator_takes_over_a_stopped_schedule_and_the_next_start_runs(
    api: ScheduleApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Scenario 7."""
    schedule_id = api.create(write_target="default-branch")["schedule_id"]
    again = _restart(api, monkeypatch, WITHOUT_OWNER)
    assert again.stored(schedule_id).state_reason == "owner-not-found"

    response = again.post(schedule_id, "take-over", ADMIN_TOKEN, reason="the owner left the team")

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["owner"], body["state"], body["state_reason"]) == ("admin", "active", None)
    assert body["next_start_at"] is not None
    assert again.orchestration.by_slug(schedule_id).active is True
    events = [event for event in again.projection.audit_events() if event.event_type == TAKEN_OVER]
    assert len(events) == 1
    (event,) = events
    assert (event.actor, event.operation, event.outcome, event.reason) == (
        "admin",
        "schedule-take-over",
        "accepted",
        "the owner left the team",
    )
    assert ("schedule", schedule_id) in {(resource.kind, resource.id) for resource in event.related}

    result = _next_start(monkeypatch, again, schedule_id)

    assert isinstance(result, dict)
    (run,) = _runs(again, schedule_id)
    assert run.actor == "admin"


def test_a_take_over_of_an_active_schedule_changes_only_the_owner(api: ScheduleApi) -> None:
    schedule_id = api.create()["schedule_id"]

    response = api.post(schedule_id, "take-over", ADMIN_TOKEN)

    assert response.status_code == 200, response.text
    stored = api.stored(schedule_id)
    assert (stored.owner, stored.state, stored.state_reason) == ("admin", "active", None)


def test_a_take_over_leaves_a_schedule_stopped_for_another_reason_stopped(api: ScheduleApi) -> None:
    schedule_id = api.create()["schedule_id"]
    stored = api.stored(schedule_id)
    api.projection.update_schedule(
        stored.model_copy(update={"state": "stopped", "state_reason": "repeated-failures"}),
        expected_updated_at=stored.updated_at,
    )

    response = api.post(schedule_id, "take-over", ADMIN_TOKEN)

    assert response.status_code == 200, response.text
    assert (response.json()["owner"], response.json()["state"]) == ("admin", "stopped")
    assert api.stored(schedule_id).state_reason == "repeated-failures"


@pytest.mark.parametrize("token", [OWNER_TOKEN, OTHER_TOKEN])
def test_a_caller_who_is_not_an_administrator_is_refused(api: ScheduleApi, token: str) -> None:
    """The owner of the schedule is refused too: only an administrator takes a schedule over."""
    schedule_id = api.create()["schedule_id"]

    response = api.post(schedule_id, "take-over", token)

    assert response.status_code == 403, response.text
    assert response.json()["error"]["code"] == "forbidden"
    assert api.stored(schedule_id).owner == "owner"
    refused = api.projection.audit_events()[-1]
    assert (refused.event_type, refused.operation, refused.outcome) == (
        "infrahub_sync.schedule.refused",
        "schedule-take-over",
        "refused-authorization",
    )
    assert TAKEN_OVER not in api.event_types()


def test_a_take_over_of_an_unknown_or_deleted_schedule_is_refused(api: ScheduleApi) -> None:
    unknown = api.post(f"s-{'0' * 32}", "take-over", ADMIN_TOKEN)
    schedule_id = api.create()["schedule_id"]
    assert api.post(schedule_id, "delete").status_code == 200

    deleted = api.post(schedule_id, "take-over", ADMIN_TOKEN)

    assert (unknown.status_code, unknown.json()["error"]["code"]) == (404, "schedule-not-found")
    assert (deleted.status_code, deleted.json()["error"]["code"]) == (409, "schedule-deleted")


def test_a_newer_version_is_reported_and_the_schedule_still_runs_its_own(
    api: ScheduleApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Scenario 6: version 2 is registered; the schedule names version 1 and runs version 1."""
    schedule_id = api.create(write_target="default-branch")["schedule_id"]
    assert api.get(schedule_id)["newer_version"] is None

    content = package("from-netbox").model_dump(mode="json")
    content["configuration"]["source"]["settings"]["url"] = "https://netbox-2.example"
    api.projection.add_configuration_version(api.config_id, ConfigurationPackage.model_validate(content))
    listed = api.client.get("/schedules", headers={"Authorization": f"Bearer {OWNER_TOKEN}"}).json()["items"]

    assert api.get(schedule_id)["newer_version"] == 2
    assert [item["newer_version"] for item in listed] == [2]
    result = _next_start(monkeypatch, api, schedule_id)
    assert isinstance(result, dict)
    (run,) = _runs(api, schedule_id)
    assert run.registry_version == 1
    assert api.post(schedule_id, "change", registry_version=2).status_code == 200
    assert api.get(schedule_id)["newer_version"] is None
