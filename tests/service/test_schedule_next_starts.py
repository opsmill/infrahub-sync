"""`POST /schedules/next-starts`: the start times of a cadence before a schedule is saved (spec 026, FR-031)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

import pytest

pytest.importorskip("prefect")
pytest.importorskip("fastapi")

from infrahub_sync.service import schedule_cadence
from tests.service.schedule_fixtures import ADMIN_TOKEN, OWNER_TOKEN, ScheduleApi, headers, schedule_api

if TYPE_CHECKING:
    from pathlib import Path

    from httpx import Response

NOW = datetime(2026, 11, 20, 12, tzinfo=timezone.utc)
SEARCH_WINDOW = timedelta(days=5 * 366)
READ = {"Authorization": f"Bearer {OWNER_TOKEN}"}


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> ScheduleApi:
    return schedule_api(monkeypatch, tmp_path, clock=lambda: NOW)


def _preview(api: ScheduleApi, **body: object) -> Response:
    """Send the preview with a bearer token and no idempotency key."""
    return api.client.post("/schedules/next-starts", json=body, headers=READ)


def _error(response: Response) -> dict[str, Any]:
    return response.json()["error"]


def _time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


@pytest.mark.parametrize(
    ("cron", "zone", "count"),
    [
        ("0 2 * * *", "Europe/Paris", 5),
        ("*/15 * * * *", "UTC", 20),
        ("0 9 * * 1-5", "America/New_York", 1),
    ],
)
def test_the_starts_are_the_starts_that_the_scheduler_computes(
    api: ScheduleApi, cron: str, zone: str, count: int
) -> None:
    response = _preview(api, cron=cron, timezone=zone, count=count)

    assert response.status_code == 200, response.text
    body = response.json()
    expected = schedule_cadence.starts_between(cron, zone, NOW, NOW + SEARCH_WINDOW, count)
    assert [_time(start) for start in body["starts"]] == list(expected)
    assert (body["cron"], body["timezone"]) == (cron, zone)
    assert _time(body["searched_until"]) == NOW + SEARCH_WINDOW


def test_the_time_zone_and_the_count_have_defaults(api: ScheduleApi) -> None:
    response = _preview(api, cron="0 2 * * *")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["timezone"] == "UTC"
    assert [_time(start) for start in body["starts"]] == list(
        schedule_cadence.starts_between("0 2 * * *", "UTC", NOW, NOW + SEARCH_WINDOW, 5)
    )


def test_a_start_in_the_hour_that_daylight_saving_removes_is_the_start_prefect_computes(api: ScheduleApi) -> None:
    response = _preview(api, cron="30 2 28 3 *", timezone="Europe/Paris")

    assert response.status_code == 200, response.text
    assert response.json()["starts"][0] == "2027-03-28T00:30:00Z"


def test_an_expression_with_few_starts_returns_fewer_than_the_count(api: ScheduleApi) -> None:
    response = _preview(api, cron="0 0 29 2 *")

    assert response.status_code == 200, response.text
    starts = response.json()["starts"]
    assert 0 < len(starts) < 5
    assert starts[0] == "2028-02-29T00:00:00Z"


@pytest.mark.parametrize("count", [0, 21, "5", 2.5, None])
def test_a_count_outside_1_to_20_is_refused(api: ScheduleApi, count: object) -> None:
    before = len(api.projection.audit_events())

    response = _preview(api, cron="0 2 * * *", count=count)

    assert response.status_code == 422, response.text
    assert _error(response)["code"] == "request-invalid"
    assert len(api.projection.audit_events()) == before


def test_a_request_without_cron_is_refused(api: ScheduleApi) -> None:
    response = _preview(api, timezone="UTC")

    assert response.status_code == 422, response.text
    assert _error(response)["code"] == "request-invalid"


@pytest.mark.parametrize(
    ("changes", "code"),
    [
        ({"cron": "0 0 30 2 *"}, "schedule-cron-invalid"),
        ({"cron": "61 * * * *"}, "schedule-cron-invalid"),
        ({"cron": "0 2 * *"}, "schedule-cron-invalid"),
        ({"timezone": "Mars/Olympus"}, "schedule-timezone-invalid"),
    ],
)
def test_an_invalid_cadence_is_refused_as_the_create_refuses_it_and_records_no_event(
    api: ScheduleApi, changes: dict[str, str], code: str
) -> None:
    body = {"cron": "0 2 * * *", "timezone": "UTC"} | changes
    before = len(api.projection.audit_events())

    preview = _preview(api, **body)

    assert preview.status_code == 422, preview.text
    assert _error(preview)["code"] == code
    assert len(api.projection.audit_events()) == before
    created = api.client.post("/schedules", json=api.body(**body), headers=headers())
    assert created.status_code == 422, created.text
    assert (_error(created)["code"], _error(created)["message"]) == (code, _error(preview)["message"])


@pytest.mark.parametrize("field", ["cron", "timezone"])
def test_a_credential_is_refused_not_repeated_and_records_no_event(api: ScheduleApi, field: str) -> None:
    body = {"cron": "0 2 * * *", "timezone": "UTC", field: ADMIN_TOKEN}
    before = len(api.projection.audit_events())

    response = _preview(api, **body)

    assert response.status_code == 422, response.text
    assert _error(response)["code"] == "secret-parameter-refused"
    assert ADMIN_TOKEN not in response.text
    assert len(api.projection.audit_events()) == before
    assert ADMIN_TOKEN not in api.stored_text()


def test_the_preview_needs_a_token(api: ScheduleApi) -> None:
    response = api.client.post("/schedules/next-starts", json={"cron": "0 2 * * *"})

    assert response.status_code == 401, response.text


def test_a_create_with_the_same_cadence_answers_the_first_preview_start(api: ScheduleApi) -> None:
    """SC-007: the start the form shows first is the start the saved schedule names."""
    preview = _preview(api, cron="0 2 * * *", timezone="Europe/Paris")
    assert preview.status_code == 200, preview.text

    created = api.create(cron="0 2 * * *", timezone="Europe/Paris")

    assert created["next_start_at"] == preview.json()["starts"][0]
