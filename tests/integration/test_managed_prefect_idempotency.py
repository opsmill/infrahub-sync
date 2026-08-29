"""Authorized temporary-server proof for managed Prefect idempotency."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import ExitStack
from pathlib import Path

import pytest

pytest.importorskip("prefect")
pytest.importorskip("opsmill_prefect_extras")

from prefect.client.orchestration import get_client
from prefect.testing.utilities import prefect_test_harness

from infrahub_sync.managed.orchestration import (
    MANAGED_DEPLOYMENT_NAME,
    MANAGED_FLOW_NAME,
    PrefectOrchestration,
)


@pytest.fixture
def isolated_prefect_server(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    """Start and fully tear down Prefect's isolated temporary API server."""
    home = tmp_path / "prefect-home"
    monkeypatch.setenv("PREFECT_HOME", str(home))
    monkeypatch.setenv("PREFECT_LOCAL_STORAGE_PATH", str(home / "storage"))
    with ExitStack() as stack:
        stack.enter_context(prefect_test_harness())
        yield


@pytest.mark.integration
@pytest.mark.asyncio
async def test_same_opaque_key_creates_one_prefect_flow_run(isolated_prefect_server: None) -> None:  # noqa: ARG001
    parameters: dict[str, object] = {
        "run_id": "run-server-idempotency",
        "stage": "plan",
        "config_id": "config-001",
        "registry_version": 1,
        "package_checksum": "a" * 64,
        "branch": None,
        "expected_checksum": None,
        "confirm_writes": False,
    }
    async with get_client() as client:
        flow_id = await client.create_flow_from_name(MANAGED_FLOW_NAME)
        deployment_id = await client.create_deployment(flow_id, name=MANAGED_DEPLOYMENT_NAME)
        orchestration = PrefectOrchestration(client)

        first = await orchestration.submit(parameters, idempotency_key="opaque-server-proof")
        second = await orchestration.submit(parameters, idempotency_key="opaque-server-proof")
        runs = await client.read_flow_runs()

    assert first.flow_run_id == second.flow_run_id
    assert len([run for run in runs if run.deployment_id == deployment_id]) == 1
