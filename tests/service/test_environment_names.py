"""The service reads only its own environment names; the retired ones are inert."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("prefect")

from infrahub_sync.service import auth, deploy, serve

if TYPE_CHECKING:
    from pathlib import Path

RETIRED_NAMES = (
    "INFRAHUB_SYNC_MANAGED_BEARER_TOKENS",
    "INFRAHUB_SYNC_MANAGED_WORK_POOL",
    "INFRAHUB_SYNC_MANAGED_FLOW_WORKING_DIRECTORY",
    "INFRAHUB_SYNC_MANAGED_HOST",
    "INFRAHUB_SYNC_MANAGED_CACHE_LOCATION",
)
PRINCIPALS = json.dumps({"admin": {"token": "service-token-canary-0001", "administrator": True}})


@pytest.fixture(autouse=True)
def _clear_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        *RETIRED_NAMES,
        auth.PRINCIPALS_ENV,
        deploy.WORK_POOL_ENV,
        deploy.FLOW_WORKING_DIRECTORY_ENV,
        "INFRAHUB_SYNC_SERVICE_HOST",
    ):
        monkeypatch.delenv(name, raising=False)


def test_the_service_environment_names_are_the_declared_ones() -> None:
    assert auth.PRINCIPALS_ENV == "INFRAHUB_SYNC_SERVICE_BEARER_TOKENS"
    assert deploy.WORK_POOL_ENV == "INFRAHUB_SYNC_SERVICE_WORK_POOL"
    assert deploy.FLOW_WORKING_DIRECTORY_ENV == "INFRAHUB_SYNC_SERVICE_FLOW_WORKING_DIRECTORY"


def test_a_retired_bearer_token_name_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INFRAHUB_SYNC_MANAGED_BEARER_TOKENS", PRINCIPALS)

    with pytest.raises(ValueError, match=auth.PRINCIPALS_ENV):
        auth.EnvironmentPrincipalResolver.from_environment()


def test_a_retired_flow_working_directory_name_is_ignored(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("INFRAHUB_SYNC_MANAGED_FLOW_WORKING_DIRECTORY", str(tmp_path))

    with pytest.raises(ValueError, match=deploy.FLOW_WORKING_DIRECTORY_ENV):
        deploy.required_flow_working_directory()


def test_a_retired_work_pool_name_is_ignored_by_the_reconciler(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INFRAHUB_SYNC_MANAGED_WORK_POOL", "retired-pool")
    monkeypatch.setenv(auth.PRINCIPALS_ENV, PRINCIPALS)
    captured: dict[str, Any] = {}

    class _Reconciler:
        def __init__(self, _projection: object, _orchestration: object, _policy: object, work_pool: str) -> None:
            captured["work_pool"] = work_pool

    monkeypatch.setattr(serve, "RunLivenessReconciler", _Reconciler)
    serve.build_app(
        projection_factory=object,
        run_service_factory=lambda *_args, **_kwargs: object(),
        configuration_routes_factory=lambda **_kwargs: object(),
        app_factory=lambda *args: args,
    )

    assert captured["work_pool"] == "default"


def test_a_retired_host_name_is_ignored_by_the_server(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INFRAHUB_SYNC_MANAGED_HOST", "10.0.0.1")
    monkeypatch.setattr(serve, "build_app", object)
    captured: dict[str, Any] = {}
    monkeypatch.setattr(serve.uvicorn, "run", lambda app, **kwargs: captured.update(app=app, **kwargs))

    serve.main()

    assert captured["host"] == "127.0.0.1"


def test_the_retired_product_cache_setting_is_gone() -> None:
    with pytest.raises(ModuleNotFoundError):
        __import__("infrahub_sync.service._settings")
