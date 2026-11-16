"""`build_service` serves the Sync API under /api and keeps the liveness loop of its lifespan running."""

from __future__ import annotations

import json
import threading
from typing import TYPE_CHECKING

import pytest

pytest.importorskip("prefect")
pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from infrahub_sync.product_store import local_product_projection
from infrahub_sync.service import serve
from infrahub_sync.service.auth import PRINCIPALS_ENV
from infrahub_sync.service.settings import WEB_DIRECTORY_ENV

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
    monkeypatch.setenv(PRINCIPALS_ENV, json.dumps({"admin": {"token": "serve-site-token-0001", "administrator": True}}))
    monkeypatch.delenv(WEB_DIRECTORY_ENV, raising=False)
    monkeypatch.delenv("INFRAHUB_SYNC_REQUIRE_APPROVAL", raising=False)
    reconcilers: list[_RecordingReconciler] = []

    def _reconciler(*args: object) -> _RecordingReconciler:
        reconciler = _RecordingReconciler(*args)
        reconcilers.append(reconciler)
        return reconciler

    monkeypatch.setattr(serve, "RunLivenessReconciler", _reconciler)
    projection = local_product_projection(tmp_path.resolve())

    service = serve.build_service(projection_factory=lambda: projection)

    with TestClient(service) as client:
        assert client.get("/api/version").status_code == 200
        assert client.get("/version", headers={"Accept": "application/json"}).status_code == 404
        assert len(reconcilers) == 1
        assert reconcilers[0].iterated.wait(timeout=5)


def test_main_serves_the_outer_application(monkeypatch: pytest.MonkeyPatch) -> None:
    sentinel = object()
    monkeypatch.setattr(serve, "build_service", lambda: sentinel)
    captured: dict[str, object] = {}
    monkeypatch.setattr(serve.uvicorn, "run", lambda app, **_kwargs: captured.update(app=app))

    serve.main()

    assert captured["app"] is sentinel
