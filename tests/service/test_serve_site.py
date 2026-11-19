"""`build_service` serves the Sync API under /api and keeps the liveness loop of its lifespan running."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

import pytest

pytest.importorskip("prefect")
pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from infrahub_sync.product_store import local_product_projection
from infrahub_sync.service import serve
from infrahub_sync.service.settings import ADMIN_ACCOUNT_PASSWORD_ENV, ADMIN_ACCOUNT_TOKEN_ENV, WEB_DIRECTORY_ENV

if TYPE_CHECKING:
    from pathlib import Path


class _RecordingReconciler:
    """Stand in for the liveness reconciler; records each iteration of the lifespan loop."""

    cadence_seconds = 0.01

    def __init__(self, *_args: object) -> None:
        self.iterated = threading.Event()

    async def reconcile_once(self) -> None:
        self.iterated.set()


def test_build_service_serves_the_api_under_api_and_runs_the_liveness_loop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(ADMIN_ACCOUNT_PASSWORD_ENV, "serve-site-password-0001")
    monkeypatch.delenv(ADMIN_ACCOUNT_TOKEN_ENV, raising=False)
    monkeypatch.delenv(WEB_DIRECTORY_ENV, raising=False)
    monkeypatch.delenv("INFRAHUB_SYNC_REQUIRE_APPROVAL", raising=False)
    reconcilers: list[_RecordingReconciler] = []

    def _reconciler(*args: object) -> _RecordingReconciler:
        reconciler = _RecordingReconciler(*args)
        reconcilers.append(reconciler)
        return reconciler

    monkeypatch.setattr(serve, "RunLivenessReconciler", _reconciler)
    projection = local_product_projection(tmp_path.resolve())

    service = serve.build_service(projection_factory=lambda: projection, start_check=lambda _pool: None)

    with TestClient(service) as client:
        assert client.get("/api/version").status_code == 200
        assert client.get("/version", headers={"Accept": "application/json"}).status_code == 404
        assert len(reconcilers) == 1
        assert reconcilers[0].iterated.wait(timeout=5)


def test_build_service_serves_the_web_interface_named_by_the_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    page = "<!doctype html><html><body>built entry page</body></html>"
    directory = tmp_path / "web"
    directory.mkdir()
    (directory / "index.html").write_text(page, encoding="utf-8")
    monkeypatch.setenv(ADMIN_ACCOUNT_PASSWORD_ENV, "serve-site-password-0002")
    monkeypatch.delenv(ADMIN_ACCOUNT_TOKEN_ENV, raising=False)
    monkeypatch.setenv(WEB_DIRECTORY_ENV, str(directory))
    monkeypatch.delenv("INFRAHUB_SYNC_REQUIRE_APPROVAL", raising=False)
    monkeypatch.setattr(serve, "RunLivenessReconciler", _RecordingReconciler)
    projection = local_product_projection((tmp_path / "store").resolve())

    service = serve.build_service(projection_factory=lambda: projection, start_check=lambda _pool: None)

    with TestClient(service) as client:
        response = client.get("/", headers={"Accept": "text/html"})
        assert response.status_code == 200
        assert response.text == page
        assert client.get("/api/version").status_code == 200


def test_build_service_passes_the_schedule_service_factory_to_the_api(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(ADMIN_ACCOUNT_PASSWORD_ENV, "serve-site-password-0003")
    monkeypatch.delenv(ADMIN_ACCOUNT_TOKEN_ENV, raising=False)
    monkeypatch.delenv(WEB_DIRECTORY_ENV, raising=False)
    monkeypatch.delenv("INFRAHUB_SYNC_REQUIRE_APPROVAL", raising=False)
    monkeypatch.setattr(serve, "create_site", lambda api, _directory: api)
    replacement = object()
    captured: dict[str, object] = {}

    def _app(*_args: object, schedules: object) -> object:
        captured["schedules"] = schedules
        return captured

    projection = local_product_projection(tmp_path.resolve())

    serve.build_service(
        projection_factory=lambda: projection,
        start_check=lambda _pool: None,
        app_factory=_app,
        schedule_service_factory=lambda *_args, **_kwargs: replacement,
    )

    assert captured["schedules"] is replacement


def test_main_serves_the_outer_application(monkeypatch: pytest.MonkeyPatch) -> None:
    sentinel = object()
    monkeypatch.setattr(serve, "build_service", lambda: sentinel)
    captured: dict[str, object] = {}
    monkeypatch.setattr(serve.uvicorn, "run", lambda app, **_kwargs: captured.update(app=app))

    serve.main()

    assert captured["app"] is sentinel
