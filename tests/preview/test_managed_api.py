"""Managed HTTP API surface: auth boundary and the full run lifecycle."""

from __future__ import annotations

import time
import uuid
from typing import Any

import httpx
import pytest

pytestmark = pytest.mark.preview

POLL_TIMEOUT_SECONDS = 240


def _client(preview_env: dict[str, Any], token: str | None) -> httpx.Client:
    headers = {}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    return httpx.Client(base_url=preview_env["urls"]["sync_api"], headers=headers, timeout=30)


def _wait_finished(client: httpx.Client, run_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + POLL_TIMEOUT_SECONDS
    payload: dict[str, Any] = {}
    while time.monotonic() < deadline:
        response = client.get(f"/runs/{run_id}")
        assert response.status_code == 200, response.text
        payload = response.json()
        if payload["run"]["finished_at"] is not None:
            return payload
        time.sleep(3)
    pytest.fail(f"run {run_id} did not finish within {POLL_TIMEOUT_SECONDS}s: {payload}")


def test_requests_without_a_bearer_token_are_refused(preview_env: dict[str, Any]) -> None:
    with _client(preview_env, token=None) as client:
        response = client.get("/runs/does-not-exist")
    assert response.status_code == 401


def test_managed_plan_and_apply_lifecycle(preview_env: dict[str, Any]) -> None:
    with _client(preview_env, token=preview_env["bearer_token"]) as client:
        created = client.post(
            "/runs",
            headers={"Idempotency-Key": f"preview-smoke-{uuid.uuid4()}"},
            json={
                "sync_name": "custom-example",
                "operation": "plan",
                "configuration_reference": "preview-smoke",
                "reason": "preview smoke: create a managed plan",
            },
        )
        assert created.status_code == 202, created.text
        run_id = created.json()["run"]["run_id"]

        planned = _wait_finished(client, run_id)
        assert planned["run"]["outcome"] not in {None, "failed"}, planned["run"]

        plan_view = client.get(f"/runs/{run_id}/plan")
        assert plan_view.status_code == 200, plan_view.text
        plan_payload = plan_view.json()
        assert plan_payload["checksum_ok"] is True
        checksum = plan_payload["checksum"]

        applied = client.post(
            f"/runs/{run_id}/apply",
            headers={"Idempotency-Key": f"preview-smoke-{uuid.uuid4()}"},
            json={
                "expected_checksum": checksum,
                "confirm_writes": True,
                "reason": "preview smoke: apply the reviewed plan",
            },
        )
        assert applied.status_code == 202, applied.text

        finished = _wait_finished(client, run_id)
        assert finished["run"]["outcome"] not in {None, "failed"}, finished["run"]

        results = client.get(f"/runs/{run_id}/results")
        assert results.status_code == 200, results.text
