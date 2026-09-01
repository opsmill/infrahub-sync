"""Prefect surface: the managed deployment is applied and executes flow runs."""

from __future__ import annotations

import time
from typing import Any

import httpx
import pytest

pytestmark = pytest.mark.preview


def _managed_deployment(preview_env: dict[str, Any]) -> dict[str, Any]:
    response = httpx.get(
        f"{preview_env['urls']['prefect']}/api/deployments/name/infrahub-sync-managed/run",
        timeout=15,
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_managed_deployment_is_applied(preview_env: dict[str, Any]) -> None:
    deployment = _managed_deployment(preview_env)

    assert deployment["work_pool_name"] == preview_env["values"]["PREVIEW_WORK_POOL"]


def test_the_managed_deployment_carries_no_static_worker_identity(preview_env: dict[str, Any]) -> None:
    job_variables = _managed_deployment(preview_env).get("job_variables") or {}

    identity = (job_variables.get("env") or {}).get("PREFECT__WORKER_ID")
    assert identity is None, "worker identity must be injected per child by the executing worker"


def test_managed_flow_runs_execute_and_complete(preview_env: dict[str, Any]) -> None:
    """After the managed-API smoke, the managed deployment must hold a completed run.

    Scoped to the managed deployment's own flow runs: an unrelated run — a CLI-driven
    flow, or anything else sharing the preview's Prefect server — must neither satisfy
    this check nor fail it.

    The poll makes no assertions until the newest managed run reaches a terminal state
    (or the deadline passes): an in-flight run is expected — Prefect records terminal
    states a moment after the Sync record finishes, and an apply may legitimately still
    be running when this test starts. The timeout matches the managed-API run budget.
    """
    deployment_id = _managed_deployment(preview_env)["id"]
    newest: dict[str, Any] = {}
    flow_runs: list[dict[str, Any]] = []
    deadline = time.monotonic() + 240
    while time.monotonic() < deadline:
        response = httpx.post(
            f"{preview_env['urls']['prefect']}/api/flow_runs/filter",
            json={
                "flow_runs": {"deployment_id": {"any_": [deployment_id]}},
                "limit": 20,
                "sort": "START_TIME_DESC",
            },
            timeout=15,
        )
        assert response.status_code == 200, response.text
        flow_runs = response.json()
        newest = flow_runs[0] if flow_runs else {}
        if newest and newest["state_type"] not in {"PENDING", "RUNNING", "SCHEDULED"}:
            break
        time.sleep(3)
    assert flow_runs, "the managed deployment recorded no flow runs; the managed path has not executed"
    states = {run["state_type"] for run in flow_runs}
    assert "COMPLETED" in states, f"no completed managed flow runs; observed states: {sorted(states)}"
    assert newest["state_type"] == "COMPLETED", (
        f"the most recent managed flow run is {newest['state_type']}; "
        "stale failures from earlier sessions are tolerated, a fresh one is not"
    )
