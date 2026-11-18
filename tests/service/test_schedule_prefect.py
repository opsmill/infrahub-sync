"""The Prefect schedule of each stored schedule, and the step that makes Prefect match the store (spec 021, R1, R13)."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

import pytest

pytest.importorskip("prefect")
pytest.importorskip("fastapi")

import httpx
from prefect.client.schemas.schedules import CronSchedule, IntervalSchedule
from prefect.exceptions import ObjectNotFound, PrefectException
from structlog.testing import capture_logs

from infrahub_sync.product_store import ProductStoreProviderError
from infrahub_sync.service import schedules
from infrahub_sync.service.orchestration import (
    DeploymentSchedule,
    ObservedDeploymentSchedule,
    OrchestrationUnavailableError,
    PrefectOrchestration,
)
from infrahub_sync.service.schedules import match_prefect_schedules
from tests.service.schedule_fixtures import ScheduleApi, headers, package, restarted, schedule_api

if TYPE_CHECKING:
    from pathlib import Path

    from prefect.client.schemas.actions import DeploymentScheduleCreate


PROVIDER_CANARY = "provider-endpoint-and-secret-canary"


@pytest.fixture(name="api")
def _api(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> ScheduleApi:
    return schedule_api(monkeypatch, tmp_path)


def _match(api: ScheduleApi) -> None:
    asyncio.run(match_prefect_schedules(api.projection, api.orchestration))


# ---------------------------------------------------------------------------
# Each request hands the right schedule to the task manager
# ---------------------------------------------------------------------------


def test_create_hands_the_cadence_the_slug_and_the_parameters_to_the_task_manager(api: ScheduleApi) -> None:
    created = api.create(cron="0 2 * * *", timezone="Europe/Paris")
    schedule_id = created["schedule_id"]

    held = api.orchestration.by_slug(schedule_id)

    assert (held.slug, held.cron, held.timezone, held.active) == (schedule_id, "0 2 * * *", "Europe/Paris", True)
    assert held.parameters == {"schedule_id": schedule_id, "stage": "sync"}
    assert api.orchestration.calls == [("create", schedule_id)]
    stored = api.stored(schedule_id)
    assert stored.prefect_synced is True
    assert stored.prefect_schedule_id == held.orchestrator_id


def test_change_pause_resume_and_delete_update_the_schedule_of_the_task_manager(api: ScheduleApi) -> None:
    schedule_id = api.create()["schedule_id"]
    first = api.orchestration.by_slug(schedule_id).orchestrator_id

    assert api.post(schedule_id, "change", cron="30 3 * * 1", timezone="UTC").status_code == 200
    held = api.orchestration.by_slug(schedule_id)
    assert (held.cron, held.timezone, held.active, held.orchestrator_id) == ("30 3 * * 1", "UTC", True, first)

    assert api.post(schedule_id, "pause").status_code == 200
    assert api.orchestration.by_slug(schedule_id).active is False
    assert api.stored(schedule_id).prefect_synced is True

    assert api.post(schedule_id, "resume").status_code == 200
    assert api.orchestration.by_slug(schedule_id).active is True

    deleted = api.post(schedule_id, "delete")
    assert deleted.status_code == 200
    assert api.orchestration.slugs() == set()
    assert deleted.json()["prefect_synced"] is True
    assert api.stored(schedule_id).prefect_schedule_id is None
    assert api.orchestration.calls == [
        ("create", schedule_id),
        ("update", schedule_id),
        ("update", schedule_id),
        ("update", schedule_id),
        ("delete", schedule_id),
    ]


def test_a_request_that_changes_nothing_writes_nothing_to_the_task_manager(api: ScheduleApi) -> None:
    schedule_id = api.create()["schedule_id"]

    assert api.post(schedule_id, "resume").status_code == 200
    assert api.client.get("/schedules", headers=headers()).status_code == 200

    assert api.orchestration.calls == [("create", schedule_id)]


# ---------------------------------------------------------------------------
# The task manager cannot be reached
# ---------------------------------------------------------------------------


def test_a_failed_call_answers_503_and_the_schedule_is_stored_and_not_synced(api: ScheduleApi) -> None:
    api.orchestration.unavailable = True

    response = api.client.post("/schedules", json=api.body(), headers=headers())

    assert response.status_code == 503, response.text
    error = response.json()["error"]
    assert error["code"] == "orchestration-unavailable"
    assert error["mutation_id"] is not None
    listed = api.client.get("/schedules", headers=headers()).json()["items"]
    assert [(item["name"], item["state"], item["prefect_synced"]) for item in listed] == [
        ("from-netbox-nightly", "active", False)
    ]
    assert api.orchestration.slugs() == set()
    assert api.event_types() == ["infrahub_sync.schedule.created"]


def test_the_next_schedule_request_repairs_the_schedule(api: ScheduleApi) -> None:
    api.orchestration.unavailable = True
    request_headers = headers(key="create-once")
    assert api.client.post("/schedules", json=api.body(), headers=request_headers).status_code == 503
    schedule_id = api.client.get("/schedules", headers=headers()).json()["items"][0]["schedule_id"]

    api.orchestration.unavailable = False
    again = api.client.post("/schedules", json=api.body(), headers=request_headers)

    assert again.status_code == 201, again.text
    assert again.json()["schedule_id"] == schedule_id
    assert api.orchestration.by_slug(schedule_id).active is True
    assert api.get(schedule_id)["prefect_synced"] is True
    assert len(api.client.get("/schedules", headers=headers()).json()["items"]) == 1


def test_a_request_on_another_schedule_repairs_the_first(api: ScheduleApi) -> None:
    api.orchestration.unavailable = True
    assert api.client.post("/schedules", json=api.body(), headers=headers()).status_code == 503
    first = api.client.get("/schedules", headers=headers()).json()["items"][0]["schedule_id"]
    api.orchestration.unavailable = False
    other_config = api.projection.create_configuration(package("from-nautobot")).config_id

    api.create(name="from-nautobot-nightly", config_id=other_config)

    assert api.orchestration.by_slug(first).active is True
    assert api.get(first)["prefect_synced"] is True


@pytest.mark.parametrize("action", ["pause", "delete", "change"])
def test_a_failed_call_of_an_action_answers_503_and_a_restart_repairs_it(api: ScheduleApi, action: str) -> None:
    schedule_id = api.create()["schedule_id"]
    api.orchestration.unavailable = True

    response = api.post(schedule_id, action, **({"cron": "0 4 * * *"} if action == "change" else {}))

    assert response.status_code == 503, response.text
    assert response.json()["error"]["code"] == "orchestration-unavailable"
    stored = api.stored(schedule_id)
    assert stored.prefect_synced is False
    assert stored.state == {"pause": "paused", "delete": "deleted", "change": "active"}[action]
    # The task manager still holds the schedule as it was before the request.
    assert api.orchestration.by_slug(schedule_id).active is True
    assert api.orchestration.by_slug(schedule_id).cron == "0 2 * * *"

    api.orchestration.unavailable = False
    again = restarted(api)
    with again.client:
        pass

    if action == "delete":
        assert again.orchestration.slugs() == set()
    else:
        held = again.orchestration.by_slug(schedule_id)
        assert (held.active, held.cron) == ((True, "0 4 * * *") if action == "change" else (False, "0 2 * * *"))
        assert again.stored(schedule_id).prefect_synced is True


def test_the_api_starts_when_the_task_manager_cannot_be_reached(api: ScheduleApi) -> None:
    schedule_id = api.create()["schedule_id"]
    assert api.post(schedule_id, "pause").status_code == 200
    api.orchestration.schedules.clear()
    api.orchestration.unavailable = True
    again = restarted(api)

    with again.client as client:
        response = client.get("/version")

    assert response.status_code == 200
    assert again.orchestration.slugs() == set()

    api.orchestration.unavailable = False
    with restarted(api).client:
        pass
    assert api.orchestration.by_slug(schedule_id).active is False


def _break_the_task_manager(api: ScheduleApi, monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the task manager fail with an error that is not the one of a failed request."""

    async def fail() -> tuple[()]:  # noqa: RUF029 - replaces a coroutine function.
        raise RuntimeError(PROVIDER_CANARY)

    monkeypatch.setattr(api.orchestration, "read_schedules", fail)


def test_another_failure_of_the_task_manager_answers_503_and_saves_the_answer_and_the_event(
    api: ScheduleApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    request_headers = headers(key="create-once")
    with monkeypatch.context() as broken:
        _break_the_task_manager(api, broken)
        response = api.client.post("/schedules", json=api.body(), headers=request_headers)

    assert response.status_code == 503, response.text
    assert response.json()["error"]["code"] == "orchestration-unavailable"
    assert PROVIDER_CANARY not in response.text
    assert api.event_types() == ["infrahub_sync.schedule.created"]

    again = api.client.post("/schedules", json=api.body(), headers=request_headers)

    assert again.status_code == 201, again.text
    assert api.orchestration.by_slug(again.json()["schedule_id"]).active is True
    assert api.event_types() == ["infrahub_sync.schedule.created", "infrahub_sync.schedule.replayed"]


def test_the_api_starts_when_the_task_manager_fails_in_another_way(
    api: ScheduleApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    schedule_id = api.create()["schedule_id"]
    _break_the_task_manager(api, monkeypatch)
    again = restarted(api)

    with again.client as client:
        response = client.get("/version")

    assert response.status_code == 200
    assert again.stored(schedule_id).state == "active"


# ---------------------------------------------------------------------------
# The match step
# ---------------------------------------------------------------------------


def test_a_schedule_that_someone_changed_in_the_task_manager_is_put_back(api: ScheduleApi) -> None:
    schedule_id = api.create()["schedule_id"]
    held = api.orchestration.by_slug(schedule_id)
    assert held.orchestrator_id is not None
    api.orchestration.schedules[held.orchestrator_id] = replace(held, cron="*/5 * * * *", active=False)
    api.orchestration.calls.clear()

    _match(api)

    restored = api.orchestration.by_slug(schedule_id)
    assert (restored.cron, restored.timezone, restored.active) == ("0 2 * * *", "Europe/Paris", True)
    assert api.orchestration.calls == [("update", schedule_id)]


def test_a_schedule_with_other_parameters_or_another_cadence_is_put_back(api: ScheduleApi) -> None:
    schedule_id = api.create()["schedule_id"]
    held = api.orchestration.by_slug(schedule_id)
    assert held.orchestrator_id is not None
    del api.orchestration.schedules[held.orchestrator_id]
    api.orchestration.foreign.append(
        ObservedDeploymentSchedule(orchestrator_id=held.orchestrator_id, slug=schedule_id, schedule=None)
    )

    _match(api)

    api.orchestration.foreign.clear()
    assert api.orchestration.by_slug(schedule_id).orchestrator_id == held.orchestrator_id
    assert api.orchestration.calls[-1] == ("update", schedule_id)


def test_a_schedule_that_the_task_manager_lost_is_created_again(api: ScheduleApi) -> None:
    schedule_id = api.create()["schedule_id"]
    api.orchestration.schedules.clear()

    _match(api)

    assert api.orchestration.by_slug(schedule_id).active is True
    assert api.stored(schedule_id).prefect_schedule_id == api.orchestration.by_slug(schedule_id).orchestrator_id


def test_a_schedule_of_the_task_manager_that_no_stored_schedule_has_is_deleted(api: ScheduleApi) -> None:
    schedule_id = api.create()["schedule_id"]
    stale = "s-" + "f" * 32
    api.orchestration.schedules["stale-id"] = DeploymentSchedule(
        slug=stale, cron="0 1 * * *", timezone="UTC", active=True, orchestrator_id="stale-id"
    )
    duplicate = replace(api.orchestration.by_slug(schedule_id), orchestrator_id="duplicate-id")
    api.orchestration.foreign.append(
        ObservedDeploymentSchedule(orchestrator_id="duplicate-id", slug=schedule_id, schedule=duplicate)
    )

    _match(api)

    assert api.orchestration.slugs() == {schedule_id}
    assert api.orchestration.foreign == []


def test_a_schedule_that_was_stored_after_the_match_read_the_store_keeps_its_prefect_schedule(
    api: ScheduleApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    schedule_id = api.create()["schedule_id"]
    api.orchestration.calls.clear()
    # The match reads the store before another request stores the schedule and hands it to the task manager.
    monkeypatch.setattr(schedules, "_all_schedules", lambda *_arguments: ())

    _match(api)

    assert api.orchestration.slugs() == {schedule_id}
    assert api.orchestration.calls == []
    assert api.stored(schedule_id).prefect_schedule_id is not None


def test_the_match_reads_every_page_of_the_stored_schedules(api: ScheduleApi, monkeypatch: pytest.MonkeyPatch) -> None:
    created = [api.create()["schedule_id"]]
    for index in range(2):
        config_id = api.projection.create_configuration(package(f"source-{index}")).config_id
        created.append(api.create(name=f"schedule-{index}", config_id=config_id)["schedule_id"])
    api.orchestration.calls.clear()
    monkeypatch.setattr(schedules, "_MATCH_PAGE_SIZE", 1)

    _match(api)

    assert api.orchestration.slugs() == set(created)
    assert api.orchestration.calls == []
    api.orchestration.schedules.clear()
    _match(api)
    assert api.orchestration.slugs() == set(created)


def test_a_schedule_of_the_task_manager_with_another_slug_is_left_as_it_is(api: ScheduleApi) -> None:
    api.create()
    foreign = [
        ObservedDeploymentSchedule(orchestrator_id="by-hand", slug="every-hour", schedule=None),
        ObservedDeploymentSchedule(orchestrator_id="no-slug", slug=None, schedule=None),
    ]
    api.orchestration.foreign.extend(foreign)
    api.orchestration.calls.clear()

    _match(api)

    assert api.orchestration.foreign == foreign
    assert api.orchestration.calls == []


def test_a_match_that_finds_nothing_to_do_writes_nothing(api: ScheduleApi) -> None:
    schedule_id = api.create()["schedule_id"]
    updated_at = api.stored(schedule_id).updated_at
    api.orchestration.calls.clear()

    _match(api)

    assert api.orchestration.calls == []
    assert api.stored(schedule_id).updated_at == updated_at


def test_a_match_that_fails_raises_and_marks_the_schedule_it_was_writing(api: ScheduleApi) -> None:
    schedule_id = api.create()["schedule_id"]
    api.orchestration.schedules.clear()

    async def refuse(schedule: DeploymentSchedule) -> str:  # noqa: RUF029 - replaces a coroutine function.
        del schedule
        message = "the orchestrator could not be reached"
        raise OrchestrationUnavailableError(message)

    api.orchestration.create_schedule = refuse  # ty: ignore[invalid-assignment]

    with pytest.raises(OrchestrationUnavailableError):
        _match(api)

    assert api.stored(schedule_id).prefect_synced is False


# ---------------------------------------------------------------------------
# The Prefect client calls
# ---------------------------------------------------------------------------


class _PrefectClient:
    """The schedule methods of the Prefect client, over one deployment held in memory."""

    def __init__(self) -> None:
        self.deployment_id = uuid4()
        self.names: list[str] = []
        self.schedules: dict[UUID, SimpleNamespace] = {}
        self.error: Exception | None = None

    def _fail(self) -> None:
        if self.error is not None:
            raise self.error

    async def read_deployment_by_name(self, name: str) -> SimpleNamespace:
        self._fail()
        self.names.append(name)
        return SimpleNamespace(id=self.deployment_id)

    async def read_deployment_schedules(self, deployment_id: UUID) -> list[Any]:
        assert deployment_id == self.deployment_id
        return list(self.schedules.values())

    async def create_deployment_schedules(
        self, deployment_id: UUID, schedules: list[DeploymentScheduleCreate]
    ) -> list[Any]:
        assert deployment_id == self.deployment_id
        created = []
        for schedule in schedules:
            entry = SimpleNamespace(
                id=uuid4(),
                schedule=schedule.schedule,
                active=schedule.active,
                parameters=schedule.parameters,
                slug=schedule.slug,
            )
            self.schedules[entry.id] = entry
            created.append(entry)
        return created

    async def update_deployment_schedule(  # noqa: PLR0913, PLR0917 - the signature of the Prefect client.
        self,
        deployment_id: UUID,
        schedule_id: UUID,
        active: bool | None = None,  # noqa: FBT001 - the signature of the Prefect client.
        schedule: object = None,
        max_scheduled_runs: int | None = None,
        parameters: dict[str, Any] | None = None,
        slug: str | None = None,
    ) -> None:
        assert deployment_id == self.deployment_id
        assert max_scheduled_runs is None
        if schedule_id not in self.schedules:
            raise ObjectNotFound(http_exc=RuntimeError("not found"))
        self.schedules[schedule_id] = SimpleNamespace(
            id=schedule_id, schedule=schedule, active=active, parameters=parameters, slug=slug
        )

    async def delete_deployment_schedule(self, deployment_id: UUID, schedule_id: UUID) -> None:
        assert deployment_id == self.deployment_id
        if schedule_id not in self.schedules:
            raise ObjectNotFound(http_exc=RuntimeError("not found"))
        del self.schedules[schedule_id]


_SLUG = "s-" + "a" * 32
_WANTED = DeploymentSchedule(slug=_SLUG, cron="0 2 * * *", timezone="Europe/Paris", active=True)


def _orchestration(client: _PrefectClient) -> PrefectOrchestration:
    return PrefectOrchestration(client)  # ty: ignore[invalid-argument-type]


async def test_the_prefect_schedule_holds_the_cron_the_time_zone_the_slug_and_the_parameters() -> None:
    client = _PrefectClient()

    identifier = await _orchestration(client).create_schedule(_WANTED)

    assert client.names == ["infrahub-sync-service/run"]
    entry = client.schedules[UUID(identifier)]
    assert isinstance(entry.schedule, CronSchedule)
    assert (entry.schedule.cron, entry.schedule.timezone) == ("0 2 * * *", "Europe/Paris")
    assert (entry.active, entry.slug) == (True, _SLUG)
    assert entry.parameters == {"schedule_id": _SLUG, "stage": "sync"}


async def test_the_schedules_of_the_deployment_are_read_back_as_they_were_written() -> None:
    client = _PrefectClient()
    orchestration = _orchestration(client)
    identifier = await orchestration.create_schedule(_WANTED)

    (observed,) = await orchestration.read_schedules()

    assert observed.orchestrator_id == identifier
    assert observed.slug == _SLUG
    assert observed.schedule is not None
    assert observed.schedule.matches(_WANTED)


async def test_a_schedule_that_the_sync_api_did_not_write_matches_no_stored_schedule() -> None:
    client = _PrefectClient()
    orchestration = _orchestration(client)
    await orchestration.create_schedule(_WANTED)
    (entry,) = client.schedules.values()
    by_hand = [
        SimpleNamespace(id=uuid4(), schedule=entry.schedule, active=True, parameters={}, slug=_SLUG),
        SimpleNamespace(
            id=uuid4(), schedule=IntervalSchedule(interval=timedelta(hours=1)), active=True, parameters={}, slug=_SLUG
        ),
        SimpleNamespace(id=uuid4(), schedule=entry.schedule, active=True, parameters=entry.parameters, slug=None),
    ]
    client.schedules = {item.id: item for item in by_hand}

    observed = await orchestration.read_schedules()

    assert [item.schedule for item in observed] == [None, None, None]
    assert [item.slug for item in observed] == [_SLUG, _SLUG, None]


async def test_an_update_sets_the_cadence_the_active_flag_the_slug_and_the_parameters() -> None:
    client = _PrefectClient()
    orchestration = _orchestration(client)
    identifier = await orchestration.create_schedule(_WANTED)

    await orchestration.update_schedule(identifier, replace(_WANTED, cron="30 3 * * *", timezone="UTC", active=False))

    entry = client.schedules[UUID(identifier)]
    assert (entry.schedule.cron, entry.schedule.timezone, entry.active, entry.slug) == (
        "30 3 * * *",
        "UTC",
        False,
        _SLUG,
    )
    assert entry.parameters == {"schedule_id": _SLUG, "stage": "sync"}


async def test_a_delete_removes_the_schedule_and_a_schedule_that_is_gone_counts_as_deleted() -> None:
    client = _PrefectClient()
    orchestration = _orchestration(client)
    identifier = await orchestration.create_schedule(_WANTED)

    await orchestration.delete_schedule(identifier)
    await orchestration.delete_schedule(identifier)

    assert client.schedules == {}


@pytest.mark.parametrize(
    "error",
    [
        httpx.ConnectError("refused http://prefect.internal:4200"),
        TimeoutError(),
        ObjectNotFound(http_exc=RuntimeError()),
    ],
)
async def test_a_failed_prefect_call_is_reported_without_its_address(error: Exception) -> None:
    client = _PrefectClient()
    orchestration = _orchestration(client)
    identifier = str(uuid4())
    client.error = error
    calls = (
        orchestration.read_schedules(),
        orchestration.create_schedule(_WANTED),
        orchestration.update_schedule(identifier, _WANTED),
        orchestration.delete_schedule(identifier),
    )

    for call in calls:
        with pytest.raises(OrchestrationUnavailableError) as raised:
            await call
        assert "prefect.internal" not in str(raised.value)
        assert raised.value.error_class == type(error).__name__
        assert raised.value.__cause__ is None


async def test_an_update_of_a_schedule_that_prefect_no_longer_holds_is_reported() -> None:
    with pytest.raises(OrchestrationUnavailableError):
        await _orchestration(_PrefectClient()).update_schedule(str(uuid4()), _WANTED)


async def test_another_prefect_error_is_reported_as_unavailable() -> None:
    client = _PrefectClient()
    client.error = PrefectException("refused http://prefect.internal:4200")

    with pytest.raises(OrchestrationUnavailableError) as raised:
        await _orchestration(client).read_schedules()

    assert raised.value.error_class == "PrefectException"
    assert "prefect.internal" not in str(raised.value)


async def test_a_cadence_that_prefect_refuses_is_reported_as_unavailable() -> None:
    """The Prefect objects are built inside the boundary, so a refused value is not an unexpected error."""
    orchestration = _orchestration(_PrefectClient())
    refused = replace(_WANTED, cron="not a cron")

    with pytest.raises(OrchestrationUnavailableError) as created:
        await orchestration.create_schedule(refused)
    with pytest.raises(OrchestrationUnavailableError) as updated:
        await orchestration.update_schedule(str(uuid4()), refused)

    assert created.value.error_class == updated.value.error_class == "ValidationError"


async def test_an_orchestrator_identifier_that_is_not_one_is_reported_as_unavailable() -> None:
    orchestration = _orchestration(_PrefectClient())

    for call in (orchestration.update_schedule("not-a-uuid", _WANTED), orchestration.delete_schedule("not-a-uuid")):
        with pytest.raises(OrchestrationUnavailableError) as raised:
            await call
        assert raised.value.error_class == "ValueError"


def test_a_failed_schedule_does_not_stop_the_match_of_the_others(api: ScheduleApi) -> None:
    first = api.create()["schedule_id"]
    other_config = api.projection.create_configuration(package("from-nautobot")).config_id
    second = api.create(name="from-nautobot-nightly", config_id=other_config)["schedule_id"]
    api.orchestration.schedules.clear()
    create = api.orchestration.create_schedule

    async def refuse_one(schedule: DeploymentSchedule) -> str:
        # The newest schedule is matched first.
        if schedule.slug == second:
            message = "the orchestrator could not be reached"
            raise OrchestrationUnavailableError(message, error_class="ConnectError")
        return await create(schedule)

    api.orchestration.create_schedule = refuse_one  # ty: ignore[invalid-assignment]

    with pytest.raises(OrchestrationUnavailableError):
        _match(api)

    assert api.orchestration.slugs() == {first}
    assert (api.stored(first).prefect_synced, api.stored(second).prefect_synced) == (True, False)


def test_a_store_error_of_the_match_is_logged_apart_from_an_unreachable_task_manager(
    api: ScheduleApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    api.create()

    def refuse(*_args: object, **_kwargs: object) -> bool:
        raise ProductStoreProviderError

    monkeypatch.setattr(api.projection, "record_schedule_prefect_sync", refuse)
    api.orchestration.schedules.clear()

    with capture_logs() as logs:
        assert asyncio.run(api.schedules._matched()) is False

    assert [(entry["event"], entry["log_level"], entry["error_class"]) for entry in logs] == [
        # The schedule was synced, and the store cannot record that it no longer is.
        ("schedules.not_synced_unrecorded", "warning", "ProductStoreProviderError"),
        ("schedules.match_store_failed", "error", "ProductStoreProviderError"),
    ]


def test_an_unexpected_error_of_the_match_is_logged_at_error_level_with_its_class(
    api: ScheduleApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    _break_the_task_manager(api, monkeypatch)

    with capture_logs() as logs:
        assert asyncio.run(api.schedules._matched()) is False

    assert [(entry["event"], entry["log_level"], entry["error_class"]) for entry in logs] == [
        ("schedules.match_failed", "error", "RuntimeError")
    ]
    assert PROVIDER_CANARY not in str(logs)
