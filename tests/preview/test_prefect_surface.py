"""Prefect surface: the service deployment is applied and executes flow runs."""

from __future__ import annotations

import time
from typing import Any

import httpx
import pytest

pytestmark = pytest.mark.preview


def _service_deployment(preview_env: dict[str, Any]) -> dict[str, Any]:
    response = httpx.get(
        f"{preview_env['urls']['prefect']}/api/deployments/name/infrahub-sync-service/run",
        timeout=15,
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_service_deployment_is_applied(preview_env: dict[str, Any]) -> None:
    deployment = _service_deployment(preview_env)

    assert deployment["work_pool_name"] == preview_env["values"]["PREVIEW_WORK_POOL"]


def test_the_service_deployment_carries_no_static_worker_identity(preview_env: dict[str, Any]) -> None:
    job_variables = _service_deployment(preview_env).get("job_variables") or {}

    identity = (job_variables.get("env") or {}).get("PREFECT__WORKER_ID")
    assert identity is None, "worker identity must be injected per child by the executing worker"


def test_service_flow_runs_execute_and_complete(
    preview_env: dict[str, Any], deliberate_terminal_flow_runs: dict[str, str]
) -> None:
    """After the Sync API smoke, the service deployment must hold a completed run.

    Scoped to the service deployment's own flow runs: an unrelated run — a CLI-driven
    flow, or anything else sharing the preview's Prefect server — must neither satisfy
    this check nor fail it.

    The cancellation and schema-drift rows drive service flow runs to cancelled and failed
    states on purpose, and each records the one it produced. Those are checked against the
    state their row asserted and then set aside, so the newest *unexplained* run still has
    to be a completed one: a fresh failure nothing accounted for keeps failing this.

    The poll makes no assertions until that newest unexplained run reaches a terminal state
    (or the deadline passes): an in-flight run is expected — Prefect records terminal
    states a moment after the Sync record finishes, and an apply may legitimately still
    be running when this test starts. The timeout matches the Sync API run budget.
    """
    deployment_id = _service_deployment(preview_env)["id"]
    unexplained: list[dict[str, Any]] = []
    flow_runs: list[dict[str, Any]] = []
    deadline = time.monotonic() + 240
    while time.monotonic() < deadline:
        response = httpx.post(
            f"{preview_env['urls']['prefect']}/api/flow_runs/filter",
            json={
                "flow_runs": {"deployment_id": {"any_": [deployment_id]}},
                "limit": 40,
                "sort": "START_TIME_DESC",
            },
            timeout=15,
        )
        assert response.status_code == 200, response.text
        flow_runs = response.json()
        unexplained = [run for run in flow_runs if run["id"] not in deliberate_terminal_flow_runs]
        if unexplained and unexplained[0]["state_type"] not in {"PENDING", "RUNNING", "SCHEDULED"}:
            break
        time.sleep(3)
    assert flow_runs, "the service deployment recorded no flow runs; the service path has not executed"
    observed = {run["id"]: run["state_type"] for run in flow_runs}
    for flow_run_id, expected in sorted(deliberate_terminal_flow_runs.items()):
        assert observed.get(flow_run_id) == expected, (
            f"service flow run {flow_run_id} was driven to {expected} by its row but Prefect reports "
            f"{observed.get(flow_run_id)}"
        )
    states = {run["state_type"] for run in unexplained}
    assert "COMPLETED" in states, f"no completed service flow runs; observed states: {sorted(states)}"
    assert unexplained[0]["state_type"] == "COMPLETED", (
        f"the most recent unexplained service flow run is {unexplained[0]['state_type']}; "
        "stale failures from earlier sessions are tolerated, a fresh one is not"
    )
