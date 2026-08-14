"""Prefect surface: the managed deployment is applied and executes flow runs."""

from __future__ import annotations

import time
from typing import Any

import httpx
import pytest

pytestmark = pytest.mark.preview


def test_managed_deployment_is_applied(preview_env: dict[str, Any]) -> None:
    response = httpx.get(
        f"{preview_env['urls']['prefect']}/api/deployments/name/infrahub-sync-managed/run",
        timeout=15,
    )
    assert response.status_code == 200, response.text
    deployment = response.json()
    assert deployment["work_pool_name"] == preview_env["values"]["PREVIEW_WORK_POOL"]


def test_managed_flow_runs_execute_and_complete(preview_env: dict[str, Any]) -> None:
    """After the managed-API smoke, Prefect must hold at least one completed run.

    The newest flow run's state is polled briefly: Prefect records terminal
    states a moment after the Sync record finishes, so an immediate read can
    still observe RUNNING.
    """
    newest: dict[str, Any] = {}
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        response = httpx.post(
            f"{preview_env['urls']['prefect']}/api/flow_runs/filter",
            json={"limit": 20, "sort": "START_TIME_DESC"},
            timeout=15,
        )
        assert response.status_code == 200, response.text
        flow_runs = response.json()
        assert flow_runs, "no flow runs recorded; the managed path has not executed"
        states = {run["state_type"] for run in flow_runs}
        assert "COMPLETED" in states, f"no completed managed flow runs; observed states: {sorted(states)}"
        newest = flow_runs[0]
        if newest["state_type"] not in {"PENDING", "RUNNING", "SCHEDULED"}:
            break
        time.sleep(3)
    assert newest["state_type"] == "COMPLETED", (
        f"the most recent managed flow run is {newest['state_type']}; "
        "stale failures from earlier sessions are tolerated, a fresh one is not"
    )
