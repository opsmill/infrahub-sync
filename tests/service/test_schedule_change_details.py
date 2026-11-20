"""The settings that a change of a schedule altered, in ``details.changes`` of its event (spec 026, T056)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, get_args

import pytest

pytest.importorskip("prefect")
pytest.importorskip("fastapi")

from infrahub_sync.client.models import ChangeScheduleRequest
from infrahub_sync.product_store import AuditEvent, EventDetails, ScheduleSettingChange
from infrahub_sync.product_store.models import ScheduleSettingName
from tests.service.schedule_fixtures import OWNER_TOKEN, ScheduleApi, headers, package, schedule_api

if TYPE_CHECKING:
    from pathlib import Path

_CHANGED = "infrahub_sync.schedule.changed"


@pytest.fixture(name="api")
def _api(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> ScheduleApi:
    return schedule_api(monkeypatch, tmp_path)


def _changed_events(api: ScheduleApi) -> list[AuditEvent]:
    return [event for event in api.projection.audit_events() if event.event_type == _CHANGED]


def _change(api: ScheduleApi, schedule_id: str, **body: object) -> AuditEvent:
    response = api.client.post(f"/schedules/{schedule_id}/change", json=body, headers=headers())
    assert response.status_code == 200, response.text
    *_, event = _changed_events(api)
    return event


def test_every_setting_that_a_change_can_send_is_compared_in_the_order_of_the_request() -> None:
    assert get_args(ScheduleSettingName) == tuple(
        name for name in ChangeScheduleRequest.model_fields if name != "reason"
    )


def test_a_changed_setting_is_listed_with_its_stored_value_before_and_after(api: ScheduleApi) -> None:
    created = api.create()

    event = _change(api, created["schedule_id"], start_late_by_at_most_seconds=2700)

    assert event.details == EventDetails(
        changes=(
            ScheduleSettingChange(
                field="start_late_by_at_most_seconds",
                before=created["start_late_by_at_most_seconds"],
                after=2700,
            ),
        )
    )


def test_a_setting_sent_with_its_stored_value_is_not_listed(api: ScheduleApi) -> None:
    created = api.create()

    event = _change(api, created["schedule_id"], cron="0 2 * * *", timezone="UTC")

    assert event.details == EventDetails(
        changes=(ScheduleSettingChange(field="timezone", before="Europe/Paris", after="UTC"),)
    )


def test_a_change_that_alters_no_stored_value_records_an_empty_list(api: ScheduleApi) -> None:
    created = api.create()

    event = _change(api, created["schedule_id"], cron="0 2 * * *", overlap_policy="reject")

    assert event.details == EventDetails(changes=())


def test_several_changed_settings_are_listed_in_the_order_of_the_request_fields(api: ScheduleApi) -> None:
    created = api.create()
    _version, added = api.projection.add_configuration_version(api.config_id, package("from-netbox-renamed"))
    assert added

    event = _change(
        api,
        created["schedule_id"],
        overlap_policy="queue",
        cron="30 6 * * 1-5",
        registry_version=2,
        write_target="default-branch",
    )

    assert event.details is not None
    assert event.details.changes == (
        ScheduleSettingChange(field="registry_version", before=1, after=2),
        ScheduleSettingChange(field="cron", before="0 2 * * *", after="30 6 * * 1-5"),
        ScheduleSettingChange(field="write_target", before="branch-review", after="default-branch"),
        ScheduleSettingChange(field="overlap_policy", before="reject", after="queue"),
    )
    assert event.registry_version == 2


def test_a_setting_that_the_change_turned_off_by_itself_is_listed(api: ScheduleApi) -> None:
    created = api.create(write_target="default-branch", write_when_left_out=True)

    event = _change(api, created["schedule_id"], write_target="branch-review")

    assert event.details is not None
    assert event.details.changes == (
        ScheduleSettingChange(field="write_target", before="default-branch", after="branch-review"),
        ScheduleSettingChange(field="write_when_left_out", before=True, after=False),
    )


def test_the_events_route_returns_the_changes(api: ScheduleApi) -> None:
    created = api.create()
    _change(api, created["schedule_id"], cron="0 3 * * *", start_late_by_at_most_seconds=1800)

    response = api.client.get(
        "/events",
        headers={"Authorization": f"Bearer {OWNER_TOKEN}"},
        params={"related": f"schedule:{created['schedule_id']}", "event_type": _CHANGED},
    )

    assert response.status_code == 200, response.text
    (item,) = response.json()["items"]
    changes: list[dict[str, Any]] = item["details"]["changes"]
    assert changes == [
        {"field": "cron", "before": "0 2 * * *", "after": "0 3 * * *"},
        {
            "field": "start_late_by_at_most_seconds",
            "before": created["start_late_by_at_most_seconds"],
            "after": 1800,
        },
    ]
    assert item["details"]["start_at"] is None


def test_a_change_answered_503_records_one_event_with_its_changes(api: ScheduleApi) -> None:
    """The event is saved with the changed schedule, before the match with the task manager."""
    created = api.create()
    api.orchestration.unavailable = True
    request_headers = headers(key="change-once")
    body = {"cron": "0 4 * * *"}

    first = api.client.post(f"/schedules/{created['schedule_id']}/change", json=body, headers=request_headers)
    api.orchestration.unavailable = False
    again = api.client.post(f"/schedules/{created['schedule_id']}/change", json=body, headers=request_headers)

    assert (first.status_code, again.status_code) == (503, 200)
    (event,) = _changed_events(api)
    assert event.details == EventDetails(
        changes=(ScheduleSettingChange(field="cron", before="0 2 * * *", after="0 4 * * *"),)
    )


def test_a_change_refused_by_a_check_records_no_change_event(api: ScheduleApi) -> None:
    created = api.create()

    response = api.post(created["schedule_id"], "change", cron="0 2 * *")

    assert response.status_code == 422, response.text
    assert _changed_events(api) == []
