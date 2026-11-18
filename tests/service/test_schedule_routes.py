"""The schedule routes of the Sync API (spec 021, contracts/http-api.md)."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest

pytest.importorskip("prefect")
pytest.importorskip("fastapi")

from infrahub_sync.service import serve
from infrahub_sync.service.schedule_cadence import next_start
from infrahub_sync.service.settings import REQUIRE_APPROVAL_ENV
from tests.service.schedule_fixtures import (
    ADMIN_TOKEN,
    OTHER_TOKEN,
    OWNER_TOKEN,
    ScheduleApi,
    headers,
    package,
    schedule_api,
)

if TYPE_CHECKING:
    from pathlib import Path

    from httpx import Response

    from infrahub_sync.product_store import ScheduleRecord

READ = {"Authorization": f"Bearer {OWNER_TOKEN}"}
# The fields of the schedule resource before user story 3 is delivered.
RESOURCE_FIELDS = {
    "schedule_id",
    "name",
    "config_id",
    "registry_version",
    "newer_version",
    "cron",
    "timezone",
    "write_target",
    "write_when_left_out",
    "left_out_set_by",
    "left_out_set_at",
    "overlap_policy",
    "start_late_by_at_most_seconds",
    "state",
    "state_reason",
    "owner",
    "next_start_at",
    "waiting_start_at",
    "last_run_id",
    "prefect_synced",
    "consecutive_failures",
    "open_branch",
    "created_at",
    "updated_at",
}


@pytest.fixture(name="api")
def _api(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> ScheduleApi:
    return schedule_api(monkeypatch, tmp_path)


def _error(response: Response) -> dict[str, Any]:
    return response.json()["error"]


def _time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


# ---------------------------------------------------------------------------
# The set of delivered write targets
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("delivered", "body"),
    [
        (frozenset(), {"write_target": "branch-review"}),
        (frozenset(), {"write_target": "default-branch"}),
        (frozenset({"default-branch"}), {"write_target": "branch-review"}),
        (frozenset({"branch-review"}), {"write_target": "default-branch"}),
        # A request with no write target never gets the default branch chosen for it (FR-003).
        (frozenset(), {}),
        (frozenset({"default-branch"}), {}),
    ],
)
def test_a_write_target_that_is_not_delivered_is_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, delivered: frozenset[str], body: dict[str, Any]
) -> None:
    api = schedule_api(monkeypatch, tmp_path, delivered=delivered)

    response = api.client.post("/schedules", json=api.body(**body), headers=headers())

    assert response.status_code == 422, response.text
    assert _error(response)["code"] == "schedule-setting-not-available"
    assert api.client.get("/schedules", headers=READ).json()["items"] == []
    assert api.orchestration.calls == []


@pytest.mark.parametrize(
    ("delivered", "write_target", "status", "code"),
    [
        (frozenset(), "branch-review", 422, "schedule-setting-not-available"),
        (frozenset({"branch-review"}), "branch-review", 404, "configuration-version-not-found"),
    ],
)
def test_the_write_target_is_checked_before_the_configuration_is_read(  # noqa: PLR0913, PLR0917 - parametrized
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    delivered: frozenset[str],
    write_target: str,
    status: int,
    code: str,
) -> None:
    """A caller can ask whether a write target is delivered before it registers a configuration.

    The smoke test of spec 021 sends a create request that names a configuration that does not
    exist: `422 schedule-setting-not-available` means the write target is not delivered, and
    `404` means it is.
    """
    api = schedule_api(monkeypatch, tmp_path, delivered=delivered)

    response = api.client.post(
        "/schedules", json=api.body(config_id="not-registered", write_target=write_target), headers=headers()
    )

    assert response.status_code == status, response.text
    assert _error(response)["code"] == code


def test_the_served_api_delivers_branch_review(monkeypatch: pytest.MonkeyPatch) -> None:
    """A deployment runs schedules with branch review; the default branch waits for user story 2."""
    received: dict[str, object] = {}

    def schedule_service(*providers: object, **settings: object) -> str:
        received.update(settings, providers=len(providers))
        return "the schedule service"

    monkeypatch.setattr(serve, "RunLivenessReconciler", lambda *_arguments: object())
    built = serve.build_app(
        projection_factory=object,
        resolver_factory=lambda: SimpleNamespace(secret_values=("a-token",)),
        run_service_factory=lambda *_arguments, **_settings: object(),
        configuration_routes_factory=lambda **_settings: object(),
        app_factory=lambda *_arguments, **settings: settings,
        start_check=lambda _pool: None,
        schedule_service_factory=schedule_service,
    )

    assert received == {
        "delivered_write_targets": frozenset({"branch-review"}),
        "secrets": ("a-token",),
        "providers": 3,
    }
    assert built == {"schedules": "the schedule service"}


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


def test_create_applies_the_defaults_and_returns_the_next_start(api: ScheduleApi) -> None:
    before = datetime.now(timezone.utc)

    response = api.client.post(
        "/schedules",
        json={"name": "from-netbox-nightly", "config_id": api.config_id, "registry_version": 1, "cron": "0 2 * * *"},
        headers=headers(),
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert set(body) == RESOURCE_FIELDS
    assert body["schedule_id"].startswith("s-")
    assert len(body["schedule_id"]) == 34
    expected = {
        "name": "from-netbox-nightly",
        "config_id": api.config_id,
        "registry_version": 1,
        "cron": "0 2 * * *",
        "timezone": "UTC",
        "write_target": "branch-review",
        "write_when_left_out": False,
        "left_out_set_by": None,
        "left_out_set_at": None,
        "overlap_policy": "reject",
        "start_late_by_at_most_seconds": 3600,
        "newer_version": None,
        "waiting_start_at": None,
        "state": "active",
        "state_reason": None,
        "owner": "owner",
        "last_run_id": None,
        "prefect_synced": True,
        "consecutive_failures": 0,
        "open_branch": None,
    }
    assert {name: body[name] for name in expected} == expected
    assert body["next_start_at"].endswith("Z")
    assert _time(body["next_start_at"]) == next_start("0 2 * * *", "UTC", before)
    assert body["created_at"] == body["updated_at"]


def test_create_computes_the_next_start_in_the_time_zone_of_the_schedule(api: ScheduleApi) -> None:
    before = datetime.now(timezone.utc)

    body = api.create(cron="0 2 * * *", timezone="Europe/Paris")

    start = _time(body["next_start_at"])
    assert start == next_start("0 2 * * *", "Europe/Paris", before)
    assert start > before
    # 02:00 in Paris is 01:00 UTC in winter and 00:00 UTC in summer.
    assert start.hour in {0, 1}


def test_the_stored_schedule_holds_the_request_and_no_token(api: ScheduleApi) -> None:
    body = api.create()

    stored = api.stored(body["schedule_id"])

    assert (stored.name, stored.owner, stored.cron, stored.timezone) == (
        "from-netbox-nightly",
        "owner",
        "0 2 * * *",
        "Europe/Paris",
    )
    assert stored.start_late_by_at_most_seconds == 3600
    assert OWNER_TOKEN not in stored.model_dump_json()


@pytest.mark.parametrize(
    ("changes", "status", "code"),
    [
        ({"registry_version": 9}, 404, "configuration-version-not-found"),
        ({"config_id": "c-unknown"}, 404, "configuration-version-not-found"),
        ({"cron": "not a cron at all"}, 422, "schedule-cron-invalid"),
        ({"cron": "0 2 * *"}, 422, "schedule-cron-invalid"),
        ({"cron": "0 0 2 * * *"}, 422, "schedule-cron-invalid"),
        ({"cron": "0 2 30 2 *"}, 422, "schedule-cron-invalid"),
        ({"timezone": "Europe/Atlantis"}, 422, "schedule-timezone-invalid"),
        ({"timezone": ""}, 422, "schedule-timezone-invalid"),
        ({"start_late_by_at_most_seconds": 59}, 422, "request-invalid"),
        ({"start_late_by_at_most_seconds": 604801}, 422, "request-invalid"),
        ({"overlap_policy": "wait"}, 422, "request-invalid"),
        ({"owner": "other"}, 422, "request-invalid"),
        ({"name": "not a name"}, 422, "request-invalid"),
    ],
)
def test_create_refuses_a_request_that_the_contract_refuses(
    api: ScheduleApi, changes: dict[str, Any], status: int, code: str
) -> None:
    response = api.client.post("/schedules", json=api.body(**changes), headers=headers())

    assert response.status_code == status, response.text
    assert _error(response)["code"] == code
    assert api.client.get("/schedules", headers=READ).json()["items"] == []
    assert api.orchestration.calls == []


def test_the_message_of_an_invalid_cron_expression_holds_the_reason(api: ScheduleApi) -> None:
    response = api.client.post("/schedules", json=api.body(cron="0 2 * *"), headers=headers())

    assert "five fields" in _error(response)["message"]


def test_a_name_in_use_is_refused_and_a_deleted_schedule_frees_its_name(api: ScheduleApi) -> None:
    first = api.create()
    other_config = api.projection.create_configuration(package("from-nautobot")).config_id

    refused = api.client.post("/schedules", json=api.body(config_id=other_config), headers=headers())

    assert refused.status_code == 409, refused.text
    assert _error(refused)["code"] == "schedule-name-in-use"

    assert api.post(first["schedule_id"], "delete").status_code == 200
    accepted = api.client.post("/schedules", json=api.body(config_id=other_config), headers=headers())
    assert accepted.status_code == 201, accepted.text


def test_a_second_schedule_for_a_configuration_is_refused_with_the_name_of_the_first(api: ScheduleApi) -> None:
    first = api.create()

    response = api.client.post("/schedules", json=api.body(name="from-netbox-hourly"), headers=headers())

    assert response.status_code == 409, response.text
    error = _error(response)
    assert error["code"] == "schedule-exists-for-configuration"
    assert "from-netbox-nightly" in error["message"]
    assert [item["schedule_id"] for item in api.client.get("/schedules", headers=READ).json()["items"]] == [
        first["schedule_id"]
    ]


def test_branch_review_is_refused_for_a_version_that_declares_its_destination_branch(api: ScheduleApi) -> None:
    declared = api.projection.create_configuration(package("declares-a-branch", branch="staging")).config_id

    response = api.client.post("/schedules", json=api.body(config_id=declared), headers=headers())

    assert response.status_code == 409, response.text
    assert _error(response)["code"] == "schedule-branch-declared"

    accepted = api.client.post(
        "/schedules", json=api.body(config_id=declared, write_target="default-branch"), headers=headers()
    )
    assert accepted.status_code == 201, accepted.text


def test_a_schedule_route_needs_a_token_and_a_post_needs_an_idempotency_key(api: ScheduleApi) -> None:
    assert api.client.get("/schedules").status_code == 401
    assert api.client.post("/schedules", json=api.body()).status_code == 401
    response = api.client.post("/schedules", json=api.body(), headers=READ)
    assert response.status_code == 422
    assert _error(response)["code"] == "idempotency-key-required"


# ---------------------------------------------------------------------------
# List and view
# ---------------------------------------------------------------------------


def test_view_returns_the_resource_and_an_unknown_schedule_is_not_found(api: ScheduleApi) -> None:
    created = api.create()

    assert api.get(created["schedule_id"]) == created
    for unknown in ("s-" + "0" * 32, "not-a-schedule"):
        response = api.client.get(f"/schedules/{unknown}", headers=READ)
        assert response.status_code == 404
        assert _error(response)["code"] == "schedule-not-found"


def test_view_holds_the_fields_that_schedules_show_prints(api: ScheduleApi) -> None:
    """The state and its reason, the most recent run, the task manager flag and the count of failed runs."""
    created = api.create()
    run_id = api.scheduled_run(created["schedule_id"], datetime(2026, 11, 17, 1, tzinfo=timezone.utc))

    body = api.get(created["schedule_id"])

    assert body["last_run_id"] == run_id
    assert body["state"] == "active"
    assert body["state_reason"] is None
    assert body["prefect_synced"] is True
    assert body["consecutive_failures"] == 0
    # The fields of user story 3: no newer version, the default duration, no waiting start.
    assert (body["newer_version"], body["start_late_by_at_most_seconds"], body["waiting_start_at"]) == (
        None,
        3600,
        None,
    )


def test_queue_and_the_late_start_duration_are_accepted_by_create_and_change(api: ScheduleApi) -> None:
    """User story 3: `schedule-setting-not-available` no longer applies to these two settings."""
    created = api.create(overlap_policy="queue", start_late_by_at_most_seconds=600)

    assert (created["overlap_policy"], created["start_late_by_at_most_seconds"]) == ("queue", 600)
    stored = api.stored(created["schedule_id"])
    assert (stored.overlap_policy, stored.start_late_by_at_most_seconds) == ("queue", 600)

    response = api.post(created["schedule_id"], "change", overlap_policy="reject", start_late_by_at_most_seconds=7200)

    assert response.status_code == 200, response.text
    assert (response.json()["overlap_policy"], response.json()["start_late_by_at_most_seconds"]) == ("reject", 7200)
    stored = api.stored(created["schedule_id"])
    assert (stored.overlap_policy, stored.start_late_by_at_most_seconds) == ("reject", 7200)


def test_the_list_is_newest_first_and_filters_and_pages(api: ScheduleApi) -> None:
    configs = [api.config_id] + [
        api.projection.create_configuration(package(f"source-{index}")).config_id for index in range(2)
    ]
    created = [api.create(name=f"schedule-{index}", config_id=config) for index, config in enumerate(configs)]
    assert api.post(created[0]["schedule_id"], "pause").status_code == 200
    assert api.post(created[1]["schedule_id"], "delete").status_code == 200

    everything = api.client.get("/schedules", headers=READ).json()
    assert [item["name"] for item in everything["items"]] == ["schedule-2", "schedule-0"]
    assert everything["next_cursor"] is None

    def names(**params: str) -> list[str]:
        response = api.client.get("/schedules", headers=READ, params=params)
        assert response.status_code == 200, response.text
        return [item["name"] for item in response.json()["items"]]

    assert names(state="paused") == ["schedule-0"]
    assert names(state="deleted") == ["schedule-1"]
    assert names(config_id=configs[2]) == ["schedule-2"]
    assert names(config_id=configs[1]) == []

    first = api.client.get("/schedules", headers=READ, params={"limit": "1"}).json()
    assert [item["name"] for item in first["items"]] == ["schedule-2"]
    second = api.client.get("/schedules", headers=READ, params={"limit": "1", "cursor": first["next_cursor"]}).json()
    assert [item["name"] for item in second["items"]] == ["schedule-0"]
    assert second["next_cursor"] is None

    for params in ({"state": "sleeping"}, {"limit": "0"}, {"cursor": "not-a-cursor"}):
        assert api.client.get("/schedules", headers=READ, params=params).status_code == 422


# ---------------------------------------------------------------------------
# Change
# ---------------------------------------------------------------------------


def test_the_owner_changes_the_version_and_the_cadence(api: ScheduleApi) -> None:
    created = api.create()
    _version, added = api.projection.add_configuration_version(api.config_id, package("from-netbox-renamed"))
    assert added
    before = datetime.now(timezone.utc)

    response = api.post(created["schedule_id"], "change", registry_version=2, cron="30 3 * * *", timezone="UTC")

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["registry_version"], body["cron"], body["timezone"]) == (2, "30 3 * * *", "UTC")
    assert _time(body["next_start_at"]) == next_start("30 3 * * *", "UTC", before)
    assert body["schedule_id"] == created["schedule_id"]
    assert body["prefect_synced"] is True
    assert api.get(created["schedule_id"]) == body


@pytest.mark.parametrize(
    ("change", "status", "code"),
    [
        ({"registry_version": 9}, 404, "configuration-version-not-found"),
        ({"cron": "0 2 * *"}, 422, "schedule-cron-invalid"),
        ({"timezone": "Europe/Atlantis"}, 422, "schedule-timezone-invalid"),
        ({"start_late_by_at_most_seconds": 59}, 422, "request-invalid"),
        ({"start_late_by_at_most_seconds": 604801}, 422, "request-invalid"),
        ({"overlap_policy": "wait"}, 422, "request-invalid"),
        ({"name": "another-name"}, 422, "request-invalid"),
        ({"config_id": "c-other"}, 422, "request-invalid"),
        ({"owner": "other"}, 422, "request-invalid"),
        ({}, 422, "request-invalid"),
        ({"reason": "nothing to change"}, 422, "request-invalid"),
    ],
)
def test_change_refuses_a_request_that_the_contract_refuses(
    api: ScheduleApi, change: dict[str, Any], status: int, code: str
) -> None:
    created = api.create()

    response = api.post(created["schedule_id"], "change", **change)

    assert response.status_code == status, response.text
    assert _error(response)["code"] == code
    assert api.get(created["schedule_id"]) == created


def test_a_change_to_a_write_target_that_is_not_delivered_is_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    api = schedule_api(monkeypatch, tmp_path, delivered=frozenset({"branch-review"}))
    created = api.create()

    response = api.post(created["schedule_id"], "change", write_target="default-branch")

    assert response.status_code == 422, response.text
    assert _error(response)["code"] == "schedule-setting-not-available"


def test_a_change_to_branch_review_is_refused_for_a_version_that_declares_its_branch(api: ScheduleApi) -> None:
    declared = api.projection.create_configuration(package("declares-a-branch", branch="staging")).config_id
    created = api.create(config_id=declared, write_target="default-branch")

    response = api.post(created["schedule_id"], "change", write_target="branch-review")

    assert response.status_code == 409, response.text
    assert _error(response)["code"] == "schedule-branch-declared"


# ---------------------------------------------------------------------------
# Pause, resume and delete
# ---------------------------------------------------------------------------


def test_the_owner_pauses_and_resumes(api: ScheduleApi) -> None:
    created = api.create()
    schedule_id = created["schedule_id"]

    paused = api.post(schedule_id, "pause", reason="maintenance of the source")

    assert paused.status_code == 200, paused.text
    assert (paused.json()["state"], paused.json()["next_start_at"]) == ("paused", None)
    assert api.post(schedule_id, "pause").json()["state"] == "paused"

    resumed = api.post(schedule_id, "resume")

    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["state"] == "active"
    assert resumed.json()["next_start_at"] is not None
    assert api.post(schedule_id, "resume").json()["state"] == "active"


def test_a_no_op_pause_and_resume_change_nothing(api: ScheduleApi) -> None:
    created = api.create()
    schedule_id = created["schedule_id"]

    assert api.post(schedule_id, "resume").status_code == 200
    assert api.stored(schedule_id).updated_at == _time(created["updated_at"])

    paused = api.post(schedule_id, "pause").json()
    assert api.post(schedule_id, "pause").status_code == 200
    assert api.stored(schedule_id).updated_at == _time(paused["updated_at"])


def test_delete_keeps_the_record_and_a_second_deletion_answers_200(api: ScheduleApi) -> None:
    created = api.create()
    schedule_id = created["schedule_id"]

    deleted = api.post(schedule_id, "delete", reason="replaced by another schedule")

    assert deleted.status_code == 200, deleted.text
    assert (deleted.json()["state"], deleted.json()["next_start_at"]) == ("deleted", None)
    assert api.get(schedule_id)["state"] == "deleted"
    assert api.post(schedule_id, "delete").status_code == 200
    for action, body in (("pause", {}), ("resume", {}), ("change", {"cron": "0 3 * * *"})):
        response = api.post(schedule_id, action, **body)
        assert response.status_code == 409, response.text
        assert _error(response)["code"] == "schedule-deleted"


@pytest.mark.parametrize("action", ["pause", "resume", "delete", "change"])
def test_an_action_on_an_unknown_schedule_is_not_found(api: ScheduleApi, action: str) -> None:
    body = {"cron": "0 3 * * *"} if action == "change" else {}

    response = api.post("s-" + "0" * 32, action, **body)

    assert response.status_code == 404, response.text
    assert _error(response)["code"] == "schedule-not-found"


def test_a_request_does_not_overwrite_a_stop_that_was_saved_while_it_was_processed(
    api: ScheduleApi, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The worker stops a schedule with the end of its fifth failed run, at any time."""
    schedule_id = api.create()["schedule_id"]
    save = api.projection.update_schedule
    saves: list[str] = []

    def stop_first(schedule: ScheduleRecord, **settings: Any) -> ScheduleRecord:  # noqa: ANN401 - passed through.
        if not saves:
            with sqlite3.connect(tmp_path.resolve() / "product-records.sqlite3") as connection:
                connection.execute(
                    "UPDATE schedules SET state = 'stopped', state_reason = 'repeated-failures', "
                    "consecutive_failures = 5, prefect_synced = 0, updated_at = ? WHERE schedule_id = ?",
                    ("2026-11-18T00:00:00+00:00", schedule_id),
                )
                connection.commit()
        saves.append(schedule.state)
        return save(schedule, **settings)

    monkeypatch.setattr(api.projection, "update_schedule", stop_first)

    response = api.post(schedule_id, "pause")

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["state"], body["state_reason"], body["consecutive_failures"]) == ("stopped", "repeated-failures", 5)
    assert saves == ["paused"]
    assert api.orchestration.by_slug(schedule_id).active is False


# ---------------------------------------------------------------------------
# The owner and the administrator
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("action", "body"), [("change", {"cron": "0 3 * * *"}), ("pause", {}), ("resume", {}), ("delete", {})]
)
def test_another_actor_who_is_not_an_administrator_is_refused(
    api: ScheduleApi, action: str, body: dict[str, Any]
) -> None:
    created = api.create()

    response = api.post(created["schedule_id"], action, OTHER_TOKEN, **body)

    assert response.status_code == 403, response.text
    assert _error(response)["code"] == "forbidden"
    assert api.get(created["schedule_id"]) == created


def test_another_actor_views_and_lists_a_schedule(api: ScheduleApi) -> None:
    created = api.create()
    other = {"Authorization": f"Bearer {OTHER_TOKEN}"}

    assert api.client.get(f"/schedules/{created['schedule_id']}", headers=other).json() == created
    assert api.client.get("/schedules", headers=other).json()["items"] == [created]


def test_an_administrator_acts_on_a_schedule_of_another_owner_and_the_owner_stays(api: ScheduleApi) -> None:
    created = api.create()
    schedule_id = created["schedule_id"]

    assert api.post(schedule_id, "pause", ADMIN_TOKEN).json()["state"] == "paused"
    assert api.post(schedule_id, "resume", ADMIN_TOKEN).json()["state"] == "active"
    changed = api.post(schedule_id, "change", ADMIN_TOKEN, cron="0 4 * * *").json()
    assert (changed["cron"], changed["owner"]) == ("0 4 * * *", "owner")
    assert api.post(schedule_id, "delete", ADMIN_TOKEN).json()["state"] == "deleted"


def test_any_authenticated_actor_creates_a_schedule_and_owns_it(api: ScheduleApi) -> None:
    assert api.create(OTHER_TOKEN)["owner"] == "other"


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


def test_a_repeated_key_returns_the_first_answer_and_creates_nothing(api: ScheduleApi) -> None:
    request_headers = headers(key="create-once")

    first = api.client.post("/schedules", json=api.body(), headers=request_headers)
    second = api.client.post("/schedules", json=api.body(), headers=request_headers)

    assert (first.status_code, second.status_code) == (201, 201)
    assert second.json() == first.json()
    assert len(api.client.get("/schedules", headers=READ).json()["items"]) == 1
    assert api.orchestration.calls == [("create", first.json()["schedule_id"])]


def test_a_repeated_key_with_other_content_is_refused(api: ScheduleApi) -> None:
    request_headers = headers(key="create-once")
    assert api.client.post("/schedules", json=api.body(), headers=request_headers).status_code == 201

    response = api.client.post("/schedules", json=api.body(cron="0 5 * * *"), headers=request_headers)

    assert response.status_code == 409, response.text
    assert _error(response)["code"] == "idempotency-conflict"


def test_a_repeated_key_of_an_action_returns_the_first_answer(api: ScheduleApi) -> None:
    created = api.create()
    request_headers = headers(key="pause-once")
    path = f"/schedules/{created['schedule_id']}/pause"

    first = api.client.post(path, json={}, headers=request_headers)
    assert api.post(created["schedule_id"], "resume").status_code == 200
    second = api.client.post(path, json={}, headers=request_headers)

    assert second.json() == first.json()
    assert api.get(created["schedule_id"])["state"] == "active"
    other = api.client.post(f"/schedules/{created['schedule_id']}/resume", json={}, headers=request_headers)
    assert _error(other)["code"] == "idempotency-conflict"


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


def test_each_accepted_request_writes_the_event_of_its_operation(api: ScheduleApi) -> None:
    created = api.create(reason="nightly import")
    schedule_id = created["schedule_id"]
    api.post(schedule_id, "change", cron="0 3 * * *")
    api.post(schedule_id, "pause")
    api.post(schedule_id, "resume")
    api.post(schedule_id, "delete")

    events = [event for event in api.projection.audit_events() if event.event_type is not None]

    assert [(event.event_type, event.operation, event.outcome) for event in events] == [
        ("infrahub_sync.schedule.created", "schedule-create", "accepted"),
        ("infrahub_sync.schedule.changed", "schedule-change", "accepted"),
        ("infrahub_sync.schedule.paused", "schedule-pause", "accepted"),
        ("infrahub_sync.schedule.resumed", "schedule-resume", "accepted"),
        ("infrahub_sync.schedule.deleted", "schedule-delete", "accepted"),
    ]
    first = events[0]
    assert (first.actor, first.reason, first.run_id) == ("owner", "nightly import", None)
    assert {(resource.kind, resource.id) for resource in first.related} == {
        ("account", "owner"),
        ("config", api.config_id),
        ("config_version", f"{api.config_id}@1"),
        ("schedule", schedule_id),
    }
    primary = first.primary_resource
    assert primary is not None
    assert (primary.kind, primary.id) == ("schedule", schedule_id)


def test_the_events_of_a_schedule_are_listed_by_its_related_resource(api: ScheduleApi) -> None:
    created = api.create()
    api.post(created["schedule_id"], "pause")

    response = api.client.get("/events", headers=READ, params={"related": f"schedule:{created['schedule_id']}"})

    assert response.status_code == 200, response.text
    assert [item["event_type"] for item in response.json()["items"]] == [
        "infrahub_sync.schedule.created",
        "infrahub_sync.schedule.paused",
    ]


def test_each_refused_request_writes_one_refused_event(api: ScheduleApi) -> None:
    created = api.create()
    schedule_id = created["schedule_id"]

    api.client.post("/schedules", json=api.body(name="another"), headers=headers())
    api.client.post("/schedules", json=api.body(cron="0 2 * *", name="another"), headers=headers())
    api.post(schedule_id, "pause", OTHER_TOKEN)
    api.post("s-" + "0" * 32, "pause")

    refused = [event for event in api.projection.audit_events() if event.event_type == "infrahub_sync.schedule.refused"]

    assert [(event.operation, event.outcome, event.actor) for event in refused] == [
        ("schedule-create", "refused-schedule-exists-for-configuration", "owner"),
        ("schedule-create", "refused-schedule-cron-invalid", "owner"),
        ("schedule-pause", "refused-authorization", "other"),
        ("schedule-pause", "refused-schedule-not-found", "owner"),
    ]
    assert api.event_types().count("infrahub_sync.schedule.created") == 1


def test_a_replayed_request_writes_one_replayed_event(api: ScheduleApi) -> None:
    request_headers = headers(key="create-once")
    api.client.post("/schedules", json=api.body(), headers=request_headers)
    api.client.post("/schedules", json=api.body(), headers=request_headers)

    assert api.event_types() == ["infrahub_sync.schedule.created", "infrahub_sync.schedule.replayed"]
    replayed = api.projection.audit_events()[-1]
    assert (replayed.operation, replayed.outcome) == ("schedule-create", "replayed")


# ---------------------------------------------------------------------------
# Credentials (FR-025)
# ---------------------------------------------------------------------------


def test_a_credential_in_a_reason_reaches_no_record_no_event_and_no_answer(api: ScheduleApi) -> None:
    created = api.client.post(
        "/schedules", json=api.body(reason=f"token is {OTHER_TOKEN}"), headers=headers(key="with-a-canary")
    )
    assert created.status_code == 201, created.text
    schedule_id = created.json()["schedule_id"]
    paused = api.post(schedule_id, "pause", reason=f"paused with {OTHER_TOKEN}")
    refused = api.post(schedule_id, "pause", OTHER_TOKEN, reason=f"refused with {ADMIN_TOKEN}")
    replayed = api.client.post(
        "/schedules", json=api.body(reason=f"token is {OTHER_TOKEN}"), headers=headers(key="with-a-canary")
    )

    answers = created.text + paused.text + refused.text + replayed.text
    stored = api.stored_text()
    for canary in (OWNER_TOKEN, OTHER_TOKEN, ADMIN_TOKEN):
        assert canary not in answers
        assert canary not in stored
    assert len(api.projection.audit_events()) == 4


@pytest.mark.parametrize("field", ["cron", "timezone", "name", "config_id"])
def test_a_credential_in_a_setting_is_refused_and_not_stored(api: ScheduleApi, field: str) -> None:
    response = api.client.post("/schedules", json=api.body(**{field: OTHER_TOKEN}), headers=headers())

    assert response.status_code == 422, response.text
    assert _error(response)["code"] == "secret-parameter-refused"
    assert OTHER_TOKEN not in response.text
    assert OTHER_TOKEN not in api.stored_text()
    refused = api.projection.audit_events()[-1]
    assert (refused.event_type, refused.outcome) == ("infrahub_sync.schedule.refused", "refused-secret-parameter")
    assert refused.config_id is None


# ---------------------------------------------------------------------------
# The open branch (FR-034)
# ---------------------------------------------------------------------------


def _open_branch(api: ScheduleApi, schedule_id: str) -> dict[str, Any]:
    """Store a run with branch review that opened a proposed change, and return the open branch it leaves."""
    branch = "sync/from-netbox-nightly-20261117-a1b2c3"
    run_id = api.scheduled_run(schedule_id, datetime(2026, 11, 17, 1, tzinfo=timezone.utc), branch=branch)
    row = api.projection.lookup_schedule_branch(run_id).value
    assert row is not None
    api.projection.update_schedule_branch(row.model_copy(update={"state": "open", "proposed_change_id": "pc-1"}))
    return {"run_id": run_id, "branch": branch, "proposed_change_id": "pc-1"}


def test_a_delete_answers_with_the_open_branch(api: ScheduleApi) -> None:
    created = api.create()
    expected = _open_branch(api, created["schedule_id"])

    assert api.get(created["schedule_id"])["open_branch"] == expected
    deleted = api.post(created["schedule_id"], "delete")

    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["open_branch"] == expected


def test_a_change_to_the_default_branch_answers_with_the_open_branch(api: ScheduleApi) -> None:
    created = api.create()
    expected = _open_branch(api, created["schedule_id"])

    changed = api.post(created["schedule_id"], "change", write_target="default-branch")

    assert changed.status_code == 200, changed.text
    assert changed.json()["write_target"] == "default-branch"
    assert changed.json()["open_branch"] == expected


def test_a_schedule_that_writes_to_the_default_branch_is_created_when_approval_is_not_required(
    api: ScheduleApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    """User story 2, scenario 1: the write target is an explicit choice, and its runs have no branch row."""
    monkeypatch.delenv(REQUIRE_APPROVAL_ENV, raising=False)

    created = api.create(write_target="default-branch")

    assert (created["state"], created["write_target"], created["write_when_left_out"]) == (
        "active",
        "default-branch",
        False,
    )
    assert created["open_branch"] is None
    assert api.orchestration.by_slug(created["schedule_id"]).active is True
    run_id = api.scheduled_run(created["schedule_id"], datetime(2027, 1, 1, 1, tzinfo=timezone.utc))
    assert api.projection.lookup_schedule_branch(run_id).value is None
    assert api.get(created["schedule_id"])["open_branch"] is None
