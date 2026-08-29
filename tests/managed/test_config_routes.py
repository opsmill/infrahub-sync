"""Focused transport checks for configuration HTTP resources."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from infrahub_sync.managed.app import create_app
from infrahub_sync.managed.auth import PRINCIPALS_ENV, EnvironmentPrincipalResolver
from infrahub_sync.managed.config_routes import ConfigurationRoutes
from infrahub_sync.managed.orchestration import Observation, Submission
from infrahub_sync.managed.service import ManagedRunService
from infrahub_sync.product_store import local_product_projection
from tests.configuration.validation_packages import package_data


class _Orchestration:  # pylint: disable=too-few-public-methods
    async def submit(self, _parameters: dict[str, object], *, _idempotency_key: str) -> Submission:  # noqa: PLR6301
        return Submission(flow_run_id="test-flow", state="pending")

    async def observe(self, _flow_run_id: str) -> Observation:  # noqa: PLR6301
        return Observation(available=True, state="running")

    async def cancel(self, _flow_run_id: str) -> Observation:  # noqa: PLR6301
        return Observation(available=True, state="cancelled")


def test_configuration_routes_register_then_read(tmp_path: Path, monkeypatch: object) -> None:
    """A configuration resource delegates registration and its scoped reads."""
    monkeypatch.setenv(
        PRINCIPALS_ENV, json.dumps({"admin": {"token": "admin-token-canary-0003", "administrator": True}})
    )
    resolver = EnvironmentPrincipalResolver.from_environment()
    projection = local_product_projection(tmp_path)
    runs = ManagedRunService(projection, _Orchestration(), secrets=resolver.secret_values)
    routes = ConfigurationRoutes(tmp_path, secrets=resolver.secret_values)
    client = TestClient(create_app(runs, resolver, routes))
    response = client.post(
        "/configs",
        headers={"Authorization": "Bearer admin-token-canary-0003"},
        json={"package": package_data(), "reason": "register"},
    )
    assert response.status_code == 201, response.text
    config_id = response.json()["configuration"]["config_id"]
    assert client.get("/configs", headers={"Authorization": "Bearer admin-token-canary-0003"}).status_code == 200
    assert (
        client.get(f"/configs/{config_id}", headers={"Authorization": "Bearer admin-token-canary-0003"}).status_code
        == 200
    )
