"""The bootstrap job gives the deployment its Prefect schedules again (spec 021, research R13).

The job applies the deployment with a definition that holds no schedule, and that removes the
Prefect schedules of the deployment. Its last step creates the Prefect schedule of every stored
schedule again.
"""

from __future__ import annotations

import sqlite3
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

import pytest

pytest.importorskip("prefect")
pytest.importorskip("fastapi")
pytest.importorskip("opsmill_prefect_extras")

from infrahub_sync.product_store import local_product_projection
from infrahub_sync.service import bootstrap
from infrahub_sync.service.orchestration import OrchestrationUnavailableError
from tests.service.schedule_fixtures import ScheduleApi, package, schedule_api

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

PROVIDER_CANARY = "provider-endpoint-and-secret-canary"


class _Bucket:
    """An S3 client that the replaced store checks never call."""


def _bootstrap(api: ScheduleApi, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Replace every provider of the bootstrap job; the fake deployment apply removes the Prefect schedules."""
    steps: list[str] = []
    client = object()

    def apply_deployment() -> int:
        api.orchestration.schedules.clear()
        steps.append("deployment")
        return 0

    async def converge_pool(_name: str) -> bool:  # noqa: RUF029 - replaces a coroutine function.
        return False

    @asynccontextmanager
    async def get_client() -> AsyncIterator[object]:  # noqa: RUF029 - replaces Prefect's client context.
        steps.append("client")
        yield client

    def orchestration(received: object) -> object:
        assert received is client
        steps.append("match")
        return api.orchestration

    monkeypatch.setattr(bootstrap.boto3, "client", lambda *_arguments, **_settings: _Bucket())
    monkeypatch.setattr(bootstrap, "converge_bucket", lambda *_arguments: False)
    monkeypatch.setattr(bootstrap, "check_store_settings", lambda *_arguments, **_settings: None)
    monkeypatch.setattr(bootstrap, "check_create_only", lambda *_arguments: None)
    monkeypatch.setattr(bootstrap, "_required", lambda _name: "present")
    monkeypatch.setattr(bootstrap, "_converge_pool", converge_pool)
    monkeypatch.setattr(bootstrap, "apply_deployment", apply_deployment)
    monkeypatch.setattr(bootstrap, "service_product_projection", lambda: api.projection)
    monkeypatch.setattr(bootstrap, "get_client", get_client)
    monkeypatch.setattr(bootstrap, "PrefectOrchestration", orchestration)
    return steps


@pytest.fixture(name="api")
def _api(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> ScheduleApi:
    return schedule_api(monkeypatch, tmp_path)


def test_after_the_bootstrap_job_every_stored_schedule_has_its_prefect_schedule_again(
    api: ScheduleApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    active = api.create()
    other_config = api.projection.create_configuration(package("from-nautobot")).config_id
    paused = api.create(name="from-nautobot-nightly", config_id=other_config, cron="15 4 * * *", timezone="UTC")
    assert api.post(paused["schedule_id"], "pause").status_code == 200
    third_config = api.projection.create_configuration(package("from-peeringmanager")).config_id
    deleted = api.create(name="from-peeringmanager-nightly", config_id=third_config)
    assert api.post(deleted["schedule_id"], "delete").status_code == 200
    before = {slug: api.orchestration.by_slug(slug) for slug in api.orchestration.slugs()}
    assert set(before) == {active["schedule_id"], paused["schedule_id"]}
    steps = _bootstrap(api, monkeypatch)

    result = bootstrap.main()

    assert result == 0
    assert steps == ["deployment", "client", "match"]
    assert api.orchestration.slugs() == {active["schedule_id"], paused["schedule_id"]}
    for slug, earlier in before.items():
        held = api.orchestration.by_slug(slug)
        assert held.matches(earlier)
        assert held.parameters == {"schedule_id": slug, "stage": "sync"}
        stored = api.stored(slug)
        assert (stored.prefect_synced, stored.prefect_schedule_id) == (True, held.orchestrator_id)
    assert api.orchestration.by_slug(active["schedule_id"]).active is True
    assert api.orchestration.by_slug(paused["schedule_id"]).active is False


def test_the_bootstrap_job_stops_no_schedule_and_writes_no_event(
    api: ScheduleApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only the API process knows its actors and its approval setting, so only it stops a schedule."""
    schedule_id = api.create()["schedule_id"]
    monkeypatch.delenv("INFRAHUB_SYNC_SERVICE_BEARER_TOKENS")
    events = len(api.projection.audit_events())
    _bootstrap(api, monkeypatch)

    assert bootstrap.main() == 0

    assert api.stored(schedule_id).state == "active"
    assert len(api.projection.audit_events()) == events


def test_a_repeated_bootstrap_job_leaves_one_prefect_schedule_for_each_schedule(
    api: ScheduleApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    schedule_id = api.create()["schedule_id"]
    _bootstrap(api, monkeypatch)

    assert bootstrap.main() == 0
    assert bootstrap.main() == 0

    assert [schedule.slug for schedule in api.orchestration.schedules.values()] == [schedule_id]


def test_a_failed_match_is_reported_with_its_own_failure_code(
    api: ScheduleApi, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    api.create()
    _bootstrap(api, monkeypatch)

    async def refuse() -> tuple[()]:  # noqa: RUF029 - replaces a coroutine function.
        raise OrchestrationUnavailableError(PROVIDER_CANARY)

    monkeypatch.setattr(api.orchestration, "read_schedules", refuse)

    with caplog.at_level("ERROR"):
        result = bootstrap.main()

    assert result == 1
    assert "refused: schedules-not-matched" in caplog.text
    assert PROVIDER_CANARY not in caplog.text


def test_a_provider_failure_of_the_match_stays_behind_its_failure_code(
    api: ScheduleApi, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _bootstrap(api, monkeypatch)

    @asynccontextmanager
    async def get_client() -> AsyncIterator[object]:  # noqa: RUF029 - replaces Prefect's client context.
        raise RuntimeError(PROVIDER_CANARY)
        yield  # pylint: disable=unreachable

    monkeypatch.setattr(bootstrap, "get_client", get_client)

    with caplog.at_level("ERROR"):
        result = bootstrap.main()

    assert result == 1
    assert "refused: schedules-not-matched" in caplog.text
    assert PROVIDER_CANARY not in caplog.text


def test_a_failed_match_names_the_class_of_its_failure(
    api: ScheduleApi, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    api.create()
    _bootstrap(api, monkeypatch)

    async def refuse() -> tuple[()]:  # noqa: RUF029 - replaces a coroutine function.
        raise OrchestrationUnavailableError(PROVIDER_CANARY, error_class="ConnectError")

    monkeypatch.setattr(api.orchestration, "read_schedules", refuse)

    with caplog.at_level("ERROR"):
        assert bootstrap.main() == 1

    assert "schedules-not-matched: OrchestrationUnavailableError" in caplog.text
    assert PROVIDER_CANARY not in caplog.text


def test_a_product_database_of_an_earlier_build_is_reported_with_its_own_failure_code(
    api: ScheduleApi, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The message says that the database must be replaced; it was hidden behind product-store-unavailable."""
    steps = _bootstrap(api, monkeypatch)
    earlier = tmp_path / "earlier"
    earlier.mkdir()
    with sqlite3.connect(earlier / "product-records.sqlite3") as connection:
        connection.execute("CREATE TABLE product_runs (run_id TEXT PRIMARY KEY, started_at TEXT NOT NULL)")
        connection.commit()
    # The real store raises the refusal; the bootstrap job receives it as it does from PostgreSQL.
    monkeypatch.setattr(bootstrap, "service_product_projection", lambda: local_product_projection(earlier))

    with caplog.at_level("ERROR"):
        result = bootstrap.main()

    assert result == 1
    assert "refused: product-database-from-earlier-build" in caplog.text
    assert "product-store-unavailable" not in caplog.text
    assert "created by an earlier build" in caplog.text
    assert "Replace it with a new, empty database" in caplog.text
    assert "table 'product_runs' lacks trigger_type, schedule_id, scheduled_start_at" in caplog.text
    assert steps == ["deployment"]


def test_another_product_store_failure_keeps_the_product_store_failure_code(
    api: ScheduleApi, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _bootstrap(api, monkeypatch)

    def fail() -> object:
        raise RuntimeError(PROVIDER_CANARY)

    monkeypatch.setattr(bootstrap, "service_product_projection", fail)

    with caplog.at_level("ERROR"):
        result = bootstrap.main()

    assert result == 1
    assert "refused: product-store-unavailable" in caplog.text
    assert PROVIDER_CANARY not in caplog.text
