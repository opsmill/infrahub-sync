"""Opt-in end-to-end test of a remote `plan` run through a served Prefect deployment.

Drives quickstart Scenario 1 programmatically against real services, using only
Prefect's own REST API — the same interaction a remote caller has:

1. `GET /api/deployments/name/infrahub-sync/run` for the deployment id,
2. `POST /api/deployments/{id}/create_flow_run`, asserting the flow-run id comes
   back synchronously,
3. poll `GET /api/flow_runs/{id}` to COMPLETED,
4. `POST /api/logs/filter` for the bridged `infrahub_sync` lifecycle lines and
   the fixed-format summary line.

Prerequisites, each of which produces a clean skip naming what is missing:

- the `prefect` extra installed,
- `INFRAHUB_ADDRESS` + `INFRAHUB_API_TOKEN` for the destination,
- `PREFECT_API_URL` pointing at a running Prefect server,
- a served deployment: `python -m infrahub_sync.orchestration.serve`, started
  from the repository root with
  `INFRAHUB_SYNC_CONFIG_DIRECTORY="$PWD/examples/custom_adapter"`,
- an empty qualified destination, so the plan reports exactly five creates.

Run with::

    INFRAHUB_ADDRESS=http://localhost:8000 \\
    INFRAHUB_API_TOKEN=<token> \\
    PREFECT_API_URL=http://127.0.0.1:4200/api \\
    pytest tests/integration/test_remote_run_live.py -m integration
"""

from __future__ import annotations

import os
import time

import pytest

pytest.importorskip("prefect", reason="the optional 'prefect' extra is not installed")

import requests

from infrahub_sync.orchestration.flow import DEPLOYMENT_NAME, FLOW_NAME

pytestmark = pytest.mark.integration

SYNC_NAME = "custom-example"
REQUIRED_ENV_VARS = ("INFRAHUB_ADDRESS", "INFRAHUB_API_TOKEN", "PREFECT_API_URL")
EXPECTED_SUMMARY = "summary=create:5,update:0,delete:0"

REQUEST_TIMEOUT = 30
RUN_TIMEOUT = 300
POLL_INTERVAL = 2
TERMINAL_STATE_TYPES = frozenset({"COMPLETED", "FAILED", "CRASHED", "CANCELLED"})


def _api_url() -> str:
    """Return the Prefect API base URL, or skip naming what is missing."""
    for name in REQUIRED_ENV_VARS:
        if not os.environ.get(name):
            pytest.skip(f"{name} is not set")
    return os.environ["PREFECT_API_URL"].rstrip("/")


def _deployment_id(api_url: str) -> str:
    """Resolve the served deployment, or skip naming what is not running."""
    try:
        response = requests.get(f"{api_url}/deployments/name/{FLOW_NAME}/{DEPLOYMENT_NAME}", timeout=REQUEST_TIMEOUT)
    except requests.RequestException as exc:
        pytest.skip(f"the Prefect API at {api_url} is unreachable: {type(exc).__name__}")
    if response.status_code == 404:
        pytest.skip(
            f"deployment {FLOW_NAME}/{DEPLOYMENT_NAME} is not registered — serve is not running; "
            "start `python -m infrahub_sync.orchestration.serve` from the repository root"
        )
    response.raise_for_status()
    body = response.json()
    assert body["status"] == "READY"
    assert body["enforce_parameter_schema"] is True
    return str(body["id"])


def _await_terminal_state(api_url: str, flow_run_id: str) -> dict:
    """Poll one flow run until it reaches a terminal state."""
    deadline = time.monotonic() + RUN_TIMEOUT
    state: dict = {}
    while time.monotonic() < deadline:
        response = requests.get(f"{api_url}/flow_runs/{flow_run_id}", timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        state = response.json()["state"]
        if state.get("type") in TERMINAL_STATE_TYPES:
            return state
        time.sleep(POLL_INTERVAL)
    pytest.fail(f"flow run {flow_run_id} did not reach a terminal state within {RUN_TIMEOUT}s (last state {state})")


def _log_messages(api_url: str, flow_run_id: str) -> list[str]:
    """Return every log message Prefect recorded for one flow run."""
    response = requests.post(
        f"{api_url}/logs/filter",
        json={"logs": {"flow_run_id": {"any_": [flow_run_id]}}},
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    return [record["message"] for record in response.json()]


def test_remote_plan_run_completes_and_is_observable() -> None:
    """DBA-003/DBA-004: a remote `plan` reaches COMPLETED and is observable in Prefect."""
    api_url = _api_url()
    deployment_id = _deployment_id(api_url)

    create = requests.post(
        f"{api_url}/deployments/{deployment_id}/create_flow_run",
        json={"parameters": {"sync_name": SYNC_NAME, "operation": "plan"}},
        timeout=REQUEST_TIMEOUT,
    )
    create.raise_for_status()
    # SC-001: the identifier arrives in the synchronous create response.
    flow_run_id = create.json()["id"]
    assert flow_run_id

    state = _await_terminal_state(api_url, flow_run_id)
    assert state["type"] == "COMPLETED", f"run {flow_run_id} ended {state['type']}: {state.get('message')}"

    messages = _log_messages(api_url, flow_run_id)
    bridged = [message for message in messages if message.startswith("infrahub_sync")]
    assert bridged, f"no bridged infrahub_sync log lines for run {flow_run_id}: {messages}"

    summary_lines = [message for message in messages if "finished: status=" in message]
    assert len(summary_lines) == 1, f"expected exactly one summary line, got {summary_lines}"
    summary_line = summary_lines[0]
    assert "status=planned" in summary_line
    assert "changed=True" in summary_line
    assert EXPECTED_SUMMARY in summary_line, (
        f"expected {EXPECTED_SUMMARY} against an empty destination, got {summary_line!r}"
    )
