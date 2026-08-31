"""Managed HTTP API surface: auth boundary and the full registered run lifecycle.

The shipped API is registered-only: a run names a registered configuration version, not
a directory on the worker's disk. So the smoke registers its own package first, through
`POST /configs`, and drives plan → review → apply against that exact version.

The package is Infrahub-to-Infrahub against the preview's own instance — `main` as the
source, the disposable smoke branch as the destination — because the registered path
resolves adapters through the installed loader and admits no filesystem adapter. `main`
is empty in a fresh preview, so the plan is legitimately empty; an empty plan is a
complete artifact and applies like any other, which is exactly the lifecycle under test.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

import httpx
import pytest

from tasks.preview import SMOKE_BRANCH

pytestmark = pytest.mark.preview

POLL_TIMEOUT_SECONDS = 240

# The kind `preview.up` loads into Infrahub before the smoke runs.
SMOKE_KIND = "InfraDevice"


def smoke_package(infrahub_url: str) -> dict[str, Any]:
    """The declared package the smoke registers, as `POST /configs` accepts it.

    Both adapters are the bundled `infrahub` one, so the registered worker resolves them
    through the installed loader with nothing generated and nothing on the filesystem. The
    token is a credential *reference* — the worker resolves `INFRAHUB_API_TOKEN` from its
    own environment, so no secret value is ever posted or recorded.
    """
    return {
        "format_version": 1,
        "configuration": {
            "name": "preview-smoke-registered",
            "source": {
                "name": "infrahub",
                "settings": {"url": infrahub_url, "branch": "main", "token": {"$credential": "infrahub-token"}},
            },
            "destination": {
                "name": "infrahub",
                "settings": {
                    "url": infrahub_url,
                    "branch": SMOKE_BRANCH,
                    "token": {"$credential": "infrahub-token"},
                },
            },
            "schema_mapping": [
                {
                    "name": SMOKE_KIND,
                    "mapping": SMOKE_KIND,
                    "identifiers": ["name"],
                    "fields": [{"name": "name", "mapping": "name"}, {"name": "type", "mapping": "type"}],
                }
            ],
        },
        "credentials": {"infrahub-token": {"provider": "env", "identifier": "INFRAHUB_API_TOKEN"}},
    }


def register_request(infrahub_url: str) -> dict[str, Any]:
    """The `POST /configs` body: a declared package plus its audit reason."""
    return {"package": smoke_package(infrahub_url), "reason": "preview smoke: register the smoke configuration"}


def create_run_request(config_id: str, registry_version: int) -> dict[str, Any]:
    """The `POST /runs` body: a registered version, never a directory name."""
    return {
        "operation": "plan",
        "config_id": config_id,
        "registry_version": registry_version,
        "branch": SMOKE_BRANCH,
        "reason": "preview smoke: create a managed plan",
    }


def apply_run_request(checksum: str) -> dict[str, Any]:
    """The `POST /runs/{id}/apply` body: the reviewed checksum the operator approved."""
    return {
        "expected_checksum": checksum,
        "confirm_writes": True,
        "branch": SMOKE_BRANCH,
        "reason": "preview smoke: apply the reviewed plan",
    }


def _client(preview_env: dict[str, Any], token: str | None) -> httpx.Client:
    headers = {}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    return httpx.Client(base_url=preview_env["urls"]["sync_api"], headers=headers, timeout=30)


def _idempotency() -> dict[str, str]:
    """A fresh key per mutation, so a re-run never replays an earlier smoke's response."""
    return {"Idempotency-Key": f"preview-smoke-{uuid.uuid4()}"}


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


def _registered_version(client: httpx.Client, preview_env: dict[str, Any]) -> tuple[str, int]:
    """Register the smoke package and prove the returned version validates cleanly."""
    registered = client.post("/configs", headers=_idempotency(), json=register_request(preview_env["urls"]["infrahub"]))
    assert registered.status_code == 201, registered.text
    version = registered.json()["version"]
    config_id, registry_version = version["config_id"], version["registry_version"]

    validated = client.post(f"/configs/{config_id}/versions/{registry_version}/validate")
    assert validated.status_code == 200, validated.text
    assert validated.json()["findings"] == [], validated.text
    return config_id, registry_version


def test_requests_without_a_bearer_token_are_refused(preview_env: dict[str, Any]) -> None:
    with _client(preview_env, token=None) as client:
        response = client.get("/runs/does-not-exist")
    assert response.status_code == 401


def test_managed_plan_and_apply_lifecycle(preview_env: dict[str, Any]) -> None:
    with _client(preview_env, token=preview_env["bearer_token"]) as client:
        config_id, registry_version = _registered_version(client, preview_env)

        created = client.post("/runs", headers=_idempotency(), json=create_run_request(config_id, registry_version))
        assert created.status_code == 202, created.text
        run_id = created.json()["run"]["run_id"]

        planned = _wait_for_phase(client, run_id, "planned")
        assert planned["run"]["outcome"] is not None, planned["run"]

        plan_view = client.get(f"/runs/{run_id}/plan")
        assert plan_view.status_code == 200, plan_view.text
        plan_payload = plan_view.json()
        assert plan_payload["checksum_ok"] is True
        # A registered plan records the destination schema semantics it was computed
        # against; the apply refuses before any write when they no longer match.
        assert plan_payload["schema_fingerprint"], plan_payload
        checksum = plan_payload["checksum"]

        apply_accepted = client.post(f"/runs/{run_id}/apply", headers=_idempotency(), json=apply_run_request(checksum))
        assert apply_accepted.status_code == 202, apply_accepted.text

        applied = _wait_for_phase(client, run_id, "applied")
        assert applied["run"]["outcome"] is not None, applied["run"]

        results = client.get(f"/runs/{run_id}/results")
        assert results.status_code == 200, results.text
