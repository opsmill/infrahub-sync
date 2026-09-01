"""Converge the separate service deployment onto an existing Prefect work pool."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from opsmill_prefect_extras.deployments import apply_deployments
from opsmill_prefect_extras.workflows import WorkflowCatalogue, assert_valid_definitions
from prefect.client.orchestration import get_client
from prefect.client.schemas.actions import DeploymentUpdate

from .orchestration import SERVICE_DEFINITION

WORK_POOL_ENV = "INFRAHUB_SYNC_SERVICE_WORK_POOL"
FLOW_WORKING_DIRECTORY_ENV = "INFRAHUB_SYNC_SERVICE_FLOW_WORKING_DIRECTORY"
CATALOGUE = WorkflowCatalogue(SERVICE_DEFINITION)


def flow_pull_steps(working_directory: str) -> list[dict[str, dict[str, str]]]:
    """Render the deployment's only pull step: enter the declared directory.

    A worker resolves a deployment's source before the entrypoint runs. Without
    an explicit pull step Prefect falls back to copying the deployment ``path``
    into the worker's working directory — with no ``path`` every flow run
    crashes on a literal ``None`` directory, and with one it splats the source
    tree into the working directory on every run. ``set_working_directory``
    copies nothing; the flow loads from the definition's absolute entrypoint.
    """
    return [{"prefect.deployments.steps.set_working_directory": {"directory": working_directory}}]


def required_flow_working_directory() -> str:
    """Read and validate the operator-declared flow working directory."""
    raw = os.environ.get(FLOW_WORKING_DIRECTORY_ENV)
    if not raw:
        msg = (
            f"{FLOW_WORKING_DIRECTORY_ENV} must name the absolute directory service flow "
            "runs execute from (relative paths in Sync configurations resolve against it)"
        )
        raise ValueError(msg)
    directory = Path(raw)
    if not directory.is_absolute() or not directory.is_dir():
        msg = f"{FLOW_WORKING_DIRECTORY_ENV} must be an existing absolute directory, got {raw!r}"
        raise ValueError(msg)
    return str(directory)


async def _ensure_flow_working_directory(working_directory: str) -> None:
    """Converge the working-directory pull step after the catalogue apply.

    The deployment library has no pull-step concept (routed upstream), and pull
    steps are not among the fields it reconciles, so this update survives later
    catalogue applies.
    """
    desired = flow_pull_steps(working_directory)
    async with get_client() as client:
        deployment = await client.read_deployment_by_name(SERVICE_DEFINITION.key)
        if deployment.pull_steps != desired:
            await client.update_deployment(deployment.id, deployment=DeploymentUpdate(pull_steps=desired))


async def _deploy() -> int:
    assert_valid_definitions(CATALOGUE)
    work_pool = os.environ.get(WORK_POOL_ENV)
    if not work_pool:
        msg = f"{WORK_POOL_ENV} must name an existing Prefect work pool"
        raise ValueError(msg)
    working_directory = required_flow_working_directory()
    report = await apply_deployments(CATALOGUE, work_pool_name=work_pool)
    if not report.is_successful:
        return 1
    await _ensure_flow_working_directory(working_directory)
    return 0


def main() -> int:
    """Validate the catalogue and apply its one service deployment."""
    return asyncio.run(_deploy())


if __name__ == "__main__":
    raise SystemExit(main())
