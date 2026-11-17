"""Routing refusals of the Sync API answer with the error envelope, not FastAPI's default body."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

pytest.importorskip("prefect")
pytest.importorskip("fastapi")

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from infrahub_sync.product_store import local_product_projection
from infrahub_sync.service.app import create_app
from infrahub_sync.service.auth import PRINCIPALS_ENV, EnvironmentPrincipalResolver
from infrahub_sync.service.service import RunService

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> TestClient:
    monkeypatch.setenv(PRINCIPALS_ENV, json.dumps({"owner": {"token": "http-errors-owner-token"}}))
    monkeypatch.delenv("INFRAHUB_SYNC_REQUIRE_APPROVAL", raising=False)
    resolver = EnvironmentPrincipalResolver.from_environment()
    projection = local_product_projection(tmp_path.resolve())
    # No route these tests call reaches the orchestration.
    service = RunService(projection, object(), secrets=resolver.secret_values)  # ty: ignore[invalid-argument-type]
    return TestClient(create_app(service, resolver))


def test_an_unknown_path_answers_with_the_not_found_envelope(client: TestClient) -> None:
    response = client.get("/nope")

    assert response.status_code == 404
    assert response.headers["content-type"] == "application/json"
    error = response.json()["error"]
    assert (error["code"], error["message"], error["status"]) == ("not-found", "Not Found", 404)


def test_a_wrong_method_answers_with_the_method_envelope_and_keeps_allow(client: TestClient) -> None:
    response = client.post("/version")

    assert response.status_code == 405
    assert response.headers["allow"] == "GET"
    error = response.json()["error"]
    assert (error["code"], error["message"], error["status"]) == ("method-not-allowed", "Method Not Allowed", 405)


def test_another_http_exception_answers_with_the_generic_http_error_code(client: TestClient) -> None:
    api = client.app
    assert isinstance(api, FastAPI)

    @api.get("/test-only/teapot")
    def teapot() -> None:
        raise HTTPException(status_code=418, detail="test refusal")

    response = client.get("/test-only/teapot")

    assert response.status_code == 418
    error = response.json()["error"]
    assert (error["code"], error["message"], error["status"]) == ("http-error", "test refusal", 418)


def test_an_existing_route_answers_as_before(client: TestClient) -> None:
    response = client.get("/version")

    assert response.status_code == 200
    assert set(response.json()) >= {"server_version", "api_versions", "stability"}
