"""The service reads only its own environment names; the retired ones are inert."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("prefect")

from infrahub_sync.product_store import local_product_projection
from infrahub_sync.service import deploy, serve, settings

if TYPE_CHECKING:
    from pathlib import Path

RETIRED_NAMES = (
    "INFRAHUB_SYNC_MANAGED_BEARER_TOKENS",
    # Spec 022 replaced the principals of the environment with the accounts of the product store.
    "INFRAHUB_SYNC_SERVICE_BEARER_TOKENS",
    "INFRAHUB_SYNC_MANAGED_WORK_POOL",
    "INFRAHUB_SYNC_MANAGED_FLOW_WORKING_DIRECTORY",
    "INFRAHUB_SYNC_MANAGED_HOST",
    "INFRAHUB_SYNC_MANAGED_CACHE_LOCATION",
    # Unit 3 retired the service's flow working directory: a worker resolves the
    # installed module and never enters a source tree.
    "INFRAHUB_SYNC_SERVICE_FLOW_WORKING_DIRECTORY",
)
PRINCIPALS = json.dumps({"admin": {"token": "service-token-canary-0001", "administrator": True}})


@pytest.fixture(autouse=True)
def _clear_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        *RETIRED_NAMES,
        settings.ADMIN_ACCOUNT_PASSWORD_ENV,
        settings.ADMIN_ACCOUNT_TOKEN_ENV,
        settings.SESSION_IDLE_SECONDS_ENV,
        deploy.WORK_POOL_ENV,
        "INFRAHUB_SYNC_SERVICE_HOST",
    ):
        monkeypatch.delenv(name, raising=False)


def test_the_service_environment_names_are_the_declared_ones() -> None:
    assert settings.ADMIN_ACCOUNT_PASSWORD_ENV == "INFRAHUB_SYNC_ADMIN_ACCOUNT_PASSWORD"  # noqa: S105 - the name of a variable.
    assert settings.ADMIN_ACCOUNT_TOKEN_ENV == "INFRAHUB_SYNC_ADMIN_ACCOUNT_TOKEN"  # noqa: S105 - the name of a variable.
    assert settings.SESSION_IDLE_SECONDS_ENV == "INFRAHUB_SYNC_SESSION_IDLE_SECONDS"
    assert deploy.WORK_POOL_ENV == "INFRAHUB_SYNC_SERVICE_WORK_POOL"
    assert settings.WEB_DIRECTORY_ENV == "INFRAHUB_SYNC_WEB_DIRECTORY"


@pytest.mark.parametrize("name", ["INFRAHUB_SYNC_MANAGED_BEARER_TOKENS", "INFRAHUB_SYNC_SERVICE_BEARER_TOKENS"])
def test_a_retired_bearer_token_name_is_ignored(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    """A retired name defines no account: the password of ``admin`` is still required."""
    monkeypatch.setenv(name, PRINCIPALS)

    with pytest.raises(settings.AccountSettingsError, match=settings.ADMIN_ACCOUNT_PASSWORD_ENV):
        settings.admin_account_settings()


def test_a_retired_work_pool_name_is_ignored_by_the_reconciler(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("INFRAHUB_SYNC_MANAGED_WORK_POOL", "retired-pool")
    monkeypatch.setenv(settings.ADMIN_ACCOUNT_PASSWORD_ENV, "service-password-canary-0001")
    monkeypatch.delenv(settings.ADMIN_ACCOUNT_TOKEN_ENV, raising=False)
    projection = local_product_projection(tmp_path)
    captured: dict[str, Any] = {}

    class _Reconciler:
        def __init__(self, _projection: object, _orchestration: object, _policy: object, work_pool: str) -> None:
            captured["work_pool"] = work_pool

    monkeypatch.setattr(serve, "RunLivenessReconciler", _Reconciler)
    serve.build_app(
        projection_factory=lambda: projection,
        run_service_factory=lambda *_args, **_kwargs: object(),
        configuration_routes_factory=lambda **_kwargs: object(),
        app_factory=lambda *args: args,
    )

    assert captured["work_pool"] == "default"


def test_a_retired_host_name_is_ignored_by_the_server(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INFRAHUB_SYNC_MANAGED_HOST", "10.0.0.1")
    monkeypatch.setattr(serve, "build_app", lambda **_factories: object())
    captured: dict[str, Any] = {}
    monkeypatch.setattr(serve.uvicorn, "run", lambda app, **kwargs: captured.update(app=app, **kwargs))

    serve.main()

    assert captured["host"] == "127.0.0.1"


def test_the_retired_product_cache_setting_is_gone() -> None:
    with pytest.raises(ModuleNotFoundError):
        __import__("infrahub_sync.service._settings")
