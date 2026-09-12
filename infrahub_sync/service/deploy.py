"""Converge the separate service deployment onto an existing Prefect work pool."""

from __future__ import annotations

import asyncio
import os

from opsmill_prefect_extras.deployments import apply_deployments
from opsmill_prefect_extras.workflows import WorkflowCatalogue, assert_valid_definitions
from prefect.client.orchestration import get_client
from prefect.client.schemas.actions import DeploymentUpdate

from .orchestration import SERVICE_DEFINITION

WORK_POOL_ENV = "INFRAHUB_SYNC_SERVICE_WORK_POOL"
CATALOGUE = WorkflowCatalogue(SERVICE_DEFINITION)
# A worker resolves a deployment's source before the entrypoint runs. The service
# entrypoint is an installed module path, so there is nothing to resolve: no step may
# put a source tree in front of the flow run, and none may choose its working
# directory. Prefect then leaves each flow run in its own disposable directory.
_NO_PULL_STEPS: list[dict[str, dict[str, str]]] = []


async def _converge_pull_steps() -> None:
    """Drive the live deployment's pull steps to empty.

    Stating the empty value is required rather than tidy. Pull steps are not among the
    fields the deployment library reconciles, so omitting them leaves whatever an earlier
    apply installed -- including the retired working-directory step, which would keep
    sending flow runs into a checkout.
    """
    async with get_client() as client:
        deployment = await client.read_deployment_by_name(SERVICE_DEFINITION.key)
        if deployment.pull_steps != _NO_PULL_STEPS:
            await client.update_deployment(deployment.id, deployment=DeploymentUpdate(pull_steps=_NO_PULL_STEPS))


async def _deploy() -> int:
    assert_valid_definitions(CATALOGUE)
    work_pool = os.environ.get(WORK_POOL_ENV)
    if not work_pool:
        msg = f"{WORK_POOL_ENV} must name an existing Prefect work pool"
        raise ValueError(msg)
    report = await apply_deployments(CATALOGUE, work_pool_name=work_pool)
    if not report.is_successful:
        return 1
    await _converge_pull_steps()
    return 0


def main() -> int:
    """Validate the catalogue and apply its one service deployment."""
    return asyncio.run(_deploy())


if __name__ == "__main__":
    raise SystemExit(main())
