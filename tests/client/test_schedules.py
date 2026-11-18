"""The schedule methods of `SyncClient` and the schedule filters of the run and event lists (spec 021, contracts/cli.md)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import httpx
import pytest

from infrahub_sync.client import (
    APIError,
    ChangeScheduleRequest,
    ClientInputError,
    CreateScheduleRequest,
    ProtocolError,
    ScheduleActionRequest,
    SchedulePage,
    ScheduleResource,
    SyncClient,
)

if TYPE_CHECKING:
    from collections.abc import Callable

TOKEN = "x"  # noqa: S105 - one-character boundary canary.
NOW = datetime(2026, 11, 18, 9, tzinfo=timezone.utc).isoformat()
SCHEDULE_ID = "s-0123456789abcdef"


def _version() -> dict[str, object]:
    return {"server_version": "3.0.0", "api_versions": ["v3-unstable"], "stability": "unstable"}


def _schedule(schedule_id: str = SCHEDULE_ID, **extra: object) -> dict[str, object]:
    return {
        "schedule_id": schedule_id,
        "name": "from-netbox-nightly",
        "config_id": "from-netbox",
        "registry_version": 3,
        "cron": "0 2 * * *",
        "timezone": "Europe/Paris",
        "write_target": "branch-review",
        "write_when_left_out": False,
        "left_out_set_by": None,
        "left_out_set_at": None,
        "overlap_policy": "reject",
        "state": "active",
        "state_reason": None,
        "owner": "operator",
        "next_start_at": NOW,
        "last_run_id": None,
        "prefect_synced": True,
        "consecutive_failures": 0,
        "open_branch": None,
        "created_at": NOW,
        "updated_at": NOW,
        **extra,
    }


def _client(requests: list[httpx.Request], answer: Callable[[httpx.Request], httpx.Response]) -> SyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/version":
            return httpx.Response(200, json=_version())
        requests.append(request)
        return answer(request)

    return SyncClient("https://example.test", TOKEN, transport=httpx.MockTransport(handler))


def _fixed(status: int, body: object) -> Callable[[httpx.Request], httpx.Response]:
    return lambda _request: httpx.Response(status, json=body)


def test_create_schedule_posts_the_request_with_its_key_and_parses_the_schedule() -> None:
    requests: list[httpx.Request] = []
    client = _client(requests, _fixed(201, _schedule()))
    request = CreateScheduleRequest(
        name="from-netbox-nightly",
        config_id="from-netbox",
        registry_version=3,
        cron="0 2 * * *",
        timezone="Europe/Paris",
        reason="nightly import",
    )

    schedule = client.create_schedule(request, "key-1")

    assert isinstance(schedule, ScheduleResource)
    assert schedule.schedule_id == SCHEDULE_ID
    assert schedule.next_start_at is not None
    sent = requests[0]
    assert (sent.method, sent.url.path) == ("POST", "/api/schedules")
    assert sent.headers["Idempotency-Key"] == "key-1"
    assert sent.headers["Authorization"] == f"Bearer {TOKEN}"
    body = json.loads(sent.content)
    assert body["cron"] == "0 2 * * *"
    assert body["timezone"] == "Europe/Paris"
    assert body["write_target"] == "branch-review"
    assert body["reason"] == "nightly import"


def test_create_schedule_refusal_is_a_typed_api_error() -> None:
    requests: list[httpx.Request] = []
    refusal = {"error": {"status": 409, "code": "schedule-name-in-use", "message": "in use"}}
    client = _client(requests, _fixed(409, refusal))
    request = CreateScheduleRequest(name="n", config_id="c", registry_version=1, cron="* * * * *")

    with pytest.raises(APIError) as raised:
        client.create_schedule(request, "key-1")
    assert (raised.value.status, raised.value.code) == (409, "schedule-name-in-use")


def test_list_schedules_sends_the_filters_and_parses_the_page() -> None:
    requests: list[httpx.Request] = []
    client = _client(requests, _fixed(200, {"items": [_schedule()], "next_cursor": "c2"}))

    page = client.list_schedules(config_id="from-netbox", state="stopped", limit=10, cursor="c1")

    assert isinstance(page, SchedulePage)
    assert [item.schedule_id for item in page.items] == [SCHEDULE_ID]
    assert page.next_cursor == "c2"
    assert requests[0].url.path == "/api/schedules"
    assert dict(requests[0].url.params) == {
        "config_id": "from-netbox",
        "state": "stopped",
        "limit": "10",
        "cursor": "c1",
    }


def test_list_schedules_without_arguments_sends_no_query() -> None:
    requests: list[httpx.Request] = []
    client = _client(requests, _fixed(200, {"items": [], "next_cursor": None}))

    assert client.list_schedules() == SchedulePage(items=(), next_cursor=None)
    assert requests[0].url.query == b""


def test_iter_schedules_follows_next_cursor_until_null() -> None:
    requests: list[httpx.Request] = []
    pages: dict[str | None, object] = {
        None: {"items": [_schedule("s-3")], "next_cursor": "c1"},
        "c1": {"items": [_schedule("s-2")], "next_cursor": None},
    }
    client = _client(requests, lambda request: httpx.Response(200, json=pages[request.url.params.get("cursor")]))

    schedules = list(client.iter_schedules(config_id="from-netbox", limit=1))

    assert [item.schedule_id for item in schedules] == ["s-3", "s-2"]
    assert [request.url.params.get("cursor") for request in requests] == [None, "c1"]
    assert all(request.url.params["config_id"] == "from-netbox" for request in requests)


def test_iter_schedules_refuses_a_cursor_that_does_not_advance() -> None:
    requests: list[httpx.Request] = []
    client = _client(requests, _fixed(200, {"items": [], "next_cursor": "c1"}))

    with pytest.raises(ProtocolError) as raised:
        list(client.iter_schedules())
    assert raised.value.operation == "list_schedules"


def test_get_schedule_reads_one_schedule_with_its_open_branch() -> None:
    requests: list[httpx.Request] = []
    open_branch = {"run_id": "run-1", "branch": "sync/nightly-1", "proposed_change_id": "pc-1"}
    client = _client(requests, _fixed(200, _schedule(state="deleted", open_branch=open_branch)))

    schedule = client.get_schedule(SCHEDULE_ID)

    assert (requests[0].method, requests[0].url.path) == ("GET", f"/api/schedules/{SCHEDULE_ID}")
    assert schedule.state == "deleted"
    assert schedule.open_branch is not None
    assert schedule.open_branch.proposed_change_id == "pc-1"


def test_change_schedule_posts_the_changed_setting_and_leaves_the_others_null() -> None:
    requests: list[httpx.Request] = []
    client = _client(requests, _fixed(200, _schedule(cron="0 3 * * *")))

    schedule = client.change_schedule(SCHEDULE_ID, ChangeScheduleRequest(cron="0 3 * * *"), "key-2")

    assert schedule.cron == "0 3 * * *"
    sent = requests[0]
    assert (sent.method, sent.url.path) == ("POST", f"/api/schedules/{SCHEDULE_ID}/change")
    assert sent.headers["Idempotency-Key"] == "key-2"
    # A null setting keeps its value: the Sync API reads `None` as a setting that the change leaves out.
    assert json.loads(sent.content) == {
        "registry_version": None,
        "cron": "0 3 * * *",
        "timezone": None,
        "write_target": None,
        "write_when_left_out": None,
        "overlap_policy": None,
        "start_late_by_at_most_seconds": None,
        "reason": None,
    }


@pytest.mark.parametrize(
    ("method", "suffix", "state"),
    [
        ("pause_schedule", "pause", "paused"),
        ("resume_schedule", "resume", "active"),
        ("delete_schedule", "delete", "deleted"),
    ],
)
def test_schedule_actions_post_their_route_with_the_reason(method: str, suffix: str, state: str) -> None:
    requests: list[httpx.Request] = []
    client = _client(requests, _fixed(200, _schedule(state=state)))

    schedule = getattr(client, method)(SCHEDULE_ID, ScheduleActionRequest(reason="maintenance"), "key-3")

    assert schedule.state == state
    sent = requests[0]
    assert (sent.method, sent.url.path) == ("POST", f"/api/schedules/{SCHEDULE_ID}/{suffix}")
    assert sent.headers["Idempotency-Key"] == "key-3"
    assert json.loads(sent.content) == {"reason": "maintenance"}


def test_resume_of_a_stopped_schedule_whose_reason_holds_is_a_typed_api_error() -> None:
    requests: list[httpx.Request] = []
    refusal = {"error": {"status": 409, "code": "schedule-stopped", "message": "stopped"}}
    client = _client(requests, _fixed(409, refusal))

    with pytest.raises(APIError) as raised:
        client.resume_schedule(SCHEDULE_ID, ScheduleActionRequest(), "key-4")
    assert raised.value.code == "schedule-stopped"


def test_list_runs_sends_the_trigger_type_and_schedule_filters() -> None:
    requests: list[httpx.Request] = []
    client = _client(requests, _fixed(200, {"items": [], "next_cursor": None}))

    client.list_runs(trigger_type="scheduled", schedule_id=SCHEDULE_ID)

    assert dict(requests[0].url.params) == {"trigger_type": "scheduled", "schedule_id": SCHEDULE_ID}


def test_iter_runs_keeps_the_schedule_filters_on_every_page() -> None:
    requests: list[httpx.Request] = []
    client = _client(requests, _fixed(200, {"items": [], "next_cursor": None}))

    list(client.iter_runs(trigger_type="manual", schedule_id=SCHEDULE_ID))

    assert requests[0].url.params["trigger_type"] == "manual"
    assert requests[0].url.params["schedule_id"] == SCHEDULE_ID


def test_list_events_sends_the_related_schedule_and_parses_the_details() -> None:
    requests: list[httpx.Request] = []
    event = {
        "event_id": "e-1",
        "event_type": "infrahub_sync.schedule.start_skipped",
        "occurred_at": NOW,
        "actor": "operator",
        "operation": "schedule",
        "outcome": "skipped",
        "related": [{"kind": "schedule", "id": SCHEDULE_ID}],
        "details": {"start_at": NOW, "cause": "run-in-progress"},
    }
    client = _client(requests, _fixed(200, {"items": [event], "limit": 50, "offset": 0, "has_more": False}))

    page = client.list_events(related=(("schedule", SCHEDULE_ID),))

    assert requests[0].url.params.get_list("related") == [f"schedule:{SCHEDULE_ID}"]
    details = page.items[0].details
    assert details is not None
    assert details.cause == "run-in-progress"


@pytest.mark.parametrize(
    ("call", "argument"),
    [
        (lambda client: client.get_schedule("bad id"), "schedule_id"),
        (lambda client: client.change_schedule("", ChangeScheduleRequest(cron="* * * * *"), "k"), "schedule_id"),
        (lambda client: client.pause_schedule("a/b", ScheduleActionRequest(), "k"), "schedule_id"),
        (lambda client: client.resume_schedule(SCHEDULE_ID, ScheduleActionRequest(), " "), "idempotency_key"),
        (lambda client: client.list_schedules(config_id="bad id"), "config_id"),
        (lambda client: client.list_schedules(state=""), "state"),
        (lambda client: client.list_schedules(state=" active"), "state"),
        (lambda client: client.list_schedules(limit=0), "limit"),
        (lambda client: client.list_runs(trigger_type="daily"), "trigger_type"),
        (lambda client: client.list_runs(schedule_id="bad id"), "schedule_id"),
    ],
)
def test_schedule_arguments_are_refused_before_any_request(call: Callable[[SyncClient], object], argument: str) -> None:
    requests: list[httpx.Request] = []
    client = _client(requests, _fixed(200, {}))

    with pytest.raises(ClientInputError) as raised:
        call(client)
    assert raised.value.argument == argument
    assert requests == []


def test_a_schedule_answer_that_does_not_match_the_resource_is_a_protocol_error() -> None:
    requests: list[httpx.Request] = []
    client = _client(requests, _fixed(200, {"schedule_id": SCHEDULE_ID}))

    with pytest.raises(ProtocolError) as raised:
        client.get_schedule(SCHEDULE_ID)
    assert raised.value.operation == "get_schedule"
