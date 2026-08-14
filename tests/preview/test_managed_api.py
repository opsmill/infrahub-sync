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


def _wait_for_phase(client: httpx.Client, run_id: str, target_phase: str) -> dict[str, Any]:
    """Poll until the durable record reaches the target phase.

    Polling ``finished_at`` is not enough: an admitted apply continues the
    planning run's record, and only the flow's eventual finish updates the
    phase — the plan stage's ``finished_at`` is already set and stays set.
    """
    deadline = time.monotonic() + POLL_TIMEOUT_SECONDS
    payload: dict[str, Any] = {}
    while time.monotonic() < deadline:
        response = client.get(f"/runs/{run_id}")
        assert response.status_code == 200, response.text
        payload = response.json()
        phase = payload["run"]["phase"]
        if phase == target_phase:
            return payload
        if "failed" in phase:
            pytest.fail(f"run {run_id} failed while waiting for {target_phase!r}: {payload['run']}")
        time.sleep(3)
    pytest.fail(f"run {run_id} did not reach {target_phase!r} within {POLL_TIMEOUT_SECONDS}s: {payload}")


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

        planned = _wait_for_phase(client, run_id, "planned")
        assert planned["run"]["outcome"] is not None, planned["run"]

        plan_view = client.get(f"/runs/{run_id}/plan")
        assert plan_view.status_code == 200, plan_view.text
        plan_payload = plan_view.json()
        assert plan_payload["checksum_ok"] is True
        checksum = plan_payload["checksum"]

        apply_accepted = client.post(
            f"/runs/{run_id}/apply",
            headers={"Idempotency-Key": f"preview-smoke-{uuid.uuid4()}"},
            json={
                "expected_checksum": checksum,
                "confirm_writes": True,
                "reason": "preview smoke: apply the reviewed plan",
            },
        )
        assert apply_accepted.status_code == 202, apply_accepted.text

        applied = _wait_for_phase(client, run_id, "applied")
        assert applied["run"]["outcome"] is not None, applied["run"]

        results = client.get(f"/runs/{run_id}/results")
        assert results.status_code == 200, results.text
