"""`build_app` reads the health settings first and gives the API a health checker (spec 029)."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import pytest

pytest.importorskip("prefect")
pytest.importorskip("fastapi")

from infrahub_sync.product_store import local_product_projection
from infrahub_sync.service import serve
from infrahub_sync.service.health import DEPENDENCY_NAMES, Check, HealthChecker
from infrahub_sync.service.serve import HealthSettingMissingError, health_checks
from infrahub_sync.service.settings import (
    ADMIN_ACCOUNT_PASSWORD_ENV,
    HEALTH_CHECK_TIMEOUT_ENV,
    HEALTH_MIN_WORKERS_ENV,
    HealthSettingsError,
)
from infrahub_sync.service.storage import DATABASE_URL_ENV, S3_BUCKET_ENV, S3_ENDPOINT_ENV

if TYPE_CHECKING:
    from pathlib import Path


async def _run(check: Check) -> None:
    await check()


@pytest.mark.parametrize(("name", "value"), [(HEALTH_CHECK_TIMEOUT_ENV, "0"), (HEALTH_MIN_WORKERS_ENV, "0")])
def test_a_bad_health_setting_stops_build_app_before_the_start_check_and_the_projection(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    monkeypatch.setenv(name, value)
    calls: list[str] = []

    def start_check(_pool: str) -> None:
        calls.append("start_check")

    def projection_factory() -> object:
        calls.append("projection")
        return object()

    with pytest.raises(HealthSettingsError, match=name):
        serve.build_app(projection_factory=projection_factory, start_check=start_check)

    assert calls == []


def test_main_turns_a_bad_health_setting_into_an_exit_that_names_the_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(HEALTH_CHECK_TIMEOUT_ENV, "-7")
    monkeypatch.setattr(serve.uvicorn, "run", lambda *_args, **_kwargs: pytest.fail("the server started"))

    with pytest.raises(SystemExit) as stopped:
        serve.main()

    assert HEALTH_CHECK_TIMEOUT_ENV in str(stopped.value.code)
    assert "-7" not in str(stopped.value.code)


def test_build_app_passes_a_health_checker_to_the_api(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(ADMIN_ACCOUNT_PASSWORD_ENV, "health-wiring-password-0001")
    projection = local_product_projection(tmp_path.resolve())
    received: dict[str, Any] = {}

    def app_factory(*_args: object, **kwargs: object) -> object:
        received.update(kwargs)
        return object()

    serve.build_app(projection_factory=lambda: projection, start_check=lambda _pool: None, app_factory=app_factory)

    assert isinstance(received["health_checker"], HealthChecker)


def test_a_component_without_a_usable_setting_reports_down_instead_of_stopping_start_up() -> None:
    """Only a test reaches this: in a served process the projection refuses the same settings first."""
    checks = health_checks({S3_ENDPOINT_ENV: "not a url"}, 3, None)

    assert tuple(check.name for check in checks) == DEPENDENCY_NAMES
    for check in checks:
        with pytest.raises(HealthSettingMissingError):
            asyncio.run(_run(check.check))


def test_usable_settings_build_a_check_for_each_component() -> None:
    environ = {DATABASE_URL_ENV: "postgresql://sync@db.invalid/sync", S3_BUCKET_ENV: "sync"}

    checks = health_checks(environ, 3, "http://prefect.invalid/api")

    assert tuple(check.name for check in checks) == DEPENDENCY_NAMES
    assert all(getattr(check.check, "__qualname__", "").split(".")[0] != "_missing_setting_check" for check in checks)
