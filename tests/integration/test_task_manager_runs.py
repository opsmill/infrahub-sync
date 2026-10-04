"""A Sync run submitted to Infrahub's task manager appears in Infrahub's task views.

Needs a live Infrahub that ships Prefect 3.8.6 (1.11.3 or 1.11.4) and its task manager, for example the
preview stack (`uv run invoke preview.up`):

    PREFECT_API_URL=http://localhost:4210/api
    INFRAHUB_ADDRESS=http://localhost:8080
    INFRAHUB_API_TOKEN=<INFRAHUB_INITIAL_ADMIN_TOKEN from development/preview.env>

The submitted flow run is cancelled before the test ends. It uses the service
deployment bootstrap applied, creating one only on a bare server, so a preview
whose worker runs that pool may pick the run up first; the test only reads tags
and the task view, never the run's outcome.
"""

from __future__ import annotations

import os
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest

pytest.importorskip("prefect")
pytest.importorskip("opsmill_prefect_extras")

from prefect.client.orchestration import get_client
from prefect.exceptions import ObjectNotFound
from prefect.states import Cancelled

from infrahub_sync.service.orchestration import (
    SERVICE_DEPLOYMENT_NAME,
    SERVICE_FLOW_NAME,
    PrefectOrchestration,
)
from infrahub_sync.service.prefect_server import require_prefect_server_version

_REQUIRED = ("PREFECT_API_URL", "INFRAHUB_ADDRESS", "INFRAHUB_API_TOKEN")
_TASK_QUERY = """
query SyncTask($ids: [String]) {
  InfrahubTask(ids: $ids) {
    count
    edges { node { id branch tags workflow } }
  }
}
"""

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        any(not os.environ.get(name) for name in _REQUIRED),
        reason=f"needs a live Infrahub task manager: set {', '.join(_REQUIRED)}",
    ),
]


async def _infrahub_task(flow_run_id: str) -> dict[str, Any] | None:
    async with httpx.AsyncClient(base_url=os.environ["INFRAHUB_ADDRESS"], timeout=30) as client:
        response = await client.post(
            "/graphql",
            json={"query": _TASK_QUERY, "variables": {"ids": [flow_run_id]}},
            headers={"X-INFRAHUB-KEY": os.environ["INFRAHUB_API_TOKEN"]},
        )
    response.raise_for_status()
    edges = response.json()["data"]["InfrahubTask"]["edges"]
    return edges[0]["node"] if edges else None


async def test_the_task_manager_runs_the_prefect_version_sync_requires() -> None:
    async with get_client() as client:
        assert await require_prefect_server_version(client)


async def test_a_submitted_run_is_listed_by_infrahub_with_its_branch_and_stage() -> None:
    parameters: dict[str, object] = {
        "run_id": f"task-view-{uuid4()}",
        "stage": "plan",
        "config_id": "task-view-proof",
        "registry_version": 1,
        "package_checksum": "a" * 64,
        "branch": "main",
        "expected_checksum": None,
        "confirm_writes": False,
    }
    async with get_client() as client:
        # Reuse the deployment bootstrap applied; create one only on a bare server, so
        # this test never replaces a real deployment and its work-pool assignment.
        try:
            await client.read_deployment_by_name(f"{SERVICE_FLOW_NAME}/{SERVICE_DEPLOYMENT_NAME}")
        except ObjectNotFound:
            flow_id = await client.create_flow_from_name(SERVICE_FLOW_NAME)
            await client.create_deployment(flow_id, name=SERVICE_DEPLOYMENT_NAME)
        submission = await PrefectOrchestration(client).submit(parameters, idempotency_key=f"task-view-{uuid4()}")
        try:
            task = await _infrahub_task(submission.flow_run_id)
        finally:
            await client.set_flow_run_state(UUID(submission.flow_run_id), Cancelled(), force=True)

    assert task is not None, "Infrahub's task view does not list the Sync run"
    assert task["branch"] == "main"
    assert "infrahub.app/workflow-type/sync-plan" in task["tags"]
