"""Converge the separate managed deployment onto an existing Prefect work pool."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import UUID

from opsmill_prefect_extras.deployments import apply_deployments
from opsmill_prefect_extras.workflows import WorkflowCatalogue, assert_valid_definitions
from prefect.client.orchestration import get_client
from prefect.client.schemas.actions import DeploymentUpdate

from .orchestration import MANAGED_DEFINITION

if TYPE_CHECKING:
    from collections.abc import Mapping

WORK_POOL_ENV = "INFRAHUB_SYNC_MANAGED_WORK_POOL"
FLOW_WORKING_DIRECTORY_ENV = "INFRAHUB_SYNC_MANAGED_FLOW_WORKING_DIRECTORY"
MANAGED_WORKER_ID_ENV = "INFRAHUB_SYNC_MANAGED_WORKER_ID"
# The flow-run variable `_claim_current_execution` reads. A self-hosted Prefect worker
# never learns its own backend id — the client asks for one only against Prefect Cloud —
# so on a self-hosted server the deployment is what carries it to the run.
WORKER_ID_VARIABLE = "PREFECT__WORKER_ID"
CATALOGUE = WorkflowCatalogue(MANAGED_DEFINITION)


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
            f"{FLOW_WORKING_DIRECTORY_ENV} must name the absolute directory managed flow "
            "runs execute from (relative paths in Sync configurations resolve against it)"
        )
        raise ValueError(msg)
    directory = Path(raw)
    if not directory.is_absolute() or not directory.is_dir():
        msg = f"{FLOW_WORKING_DIRECTORY_ENV} must be an existing absolute directory, got {raw!r}"
        raise ValueError(msg)
    return str(directory)


def declared_worker_identity() -> str | None:
    """Read the operator-declared worker identity, or `None` when none is declared.

    Exactly one canonical UUID string, the form `_claim_current_execution` accepts. The
    claim is a fencing token — only the worker that claimed an execution may write its
    terminal state back — so a value the server did not issue, or one that merely looks
    like a UUID, is refused here rather than reaching a run and fencing it against an
    identity nobody holds.

    Raises:
        ValueError: a value is declared but is not a canonical UUID string.
    """
    declared = os.environ.get(MANAGED_WORKER_ID_ENV)
    if declared is None:
        return None
    try:
        canonical = str(UUID(declared))
    except ValueError:
        canonical = ""
    if canonical != declared:
        msg = f"{MANAGED_WORKER_ID_ENV} must be a canonical UUID string naming the registered worker"
        raise ValueError(msg)
    return declared


def job_variables_with_worker_identity(job_variables: Mapping[str, Any], worker_id: str) -> dict[str, Any]:
    """Return `job_variables` with the worker identity set, leaving everything else alone.

    One key of one variable: `env` is merged last by `prepare_for_flow_run`, so this is
    what reaches the flow-run process. Every other job variable, and every other `env`
    entry, is carried through — a deployment may legitimately carry more than this.
    """
    env = {**job_variables.get("env", {}), WORKER_ID_VARIABLE: worker_id}
    return {**job_variables, "env": env}


async def _ensure_worker_identity(worker_id: str) -> None:
    """Converge the declared worker identity into the deployment's job variables.

    Alongside the pull-step convergence below and for the same reason: the deployment
    library does not reconcile job variables, so this survives a later catalogue apply.
    """
    async with get_client() as client:
        deployment = await client.read_deployment_by_name(MANAGED_DEFINITION.key)
        desired = job_variables_with_worker_identity(deployment.job_variables or {}, worker_id)
        if deployment.job_variables != desired:
            await client.update_deployment(deployment.id, deployment=DeploymentUpdate(job_variables=desired))


async def _ensure_flow_working_directory(working_directory: str) -> None:
    """Converge the working-directory pull step after the catalogue apply.

    The deployment library has no pull-step concept (routed upstream), and pull
    steps are not among the fields it reconciles, so this update survives later
    catalogue applies.
    """
    desired = flow_pull_steps(working_directory)
    async with get_client() as client:
        deployment = await client.read_deployment_by_name(MANAGED_DEFINITION.key)
        if deployment.pull_steps != desired:
            await client.update_deployment(deployment.id, deployment=DeploymentUpdate(pull_steps=desired))


async def _deploy() -> int:
    assert_valid_definitions(CATALOGUE)
    work_pool = os.environ.get(WORK_POOL_ENV)
    if not work_pool:
        msg = f"{WORK_POOL_ENV} must name an existing Prefect work pool"
        raise ValueError(msg)
    working_directory = required_flow_working_directory()
    worker_id = declared_worker_identity()
    report = await apply_deployments(CATALOGUE, work_pool_name=work_pool)
    if not report.is_successful:
        return 1
    await _ensure_flow_working_directory(working_directory)
    if worker_id is not None:
        await _ensure_worker_identity(worker_id)
    return 0


def main() -> int:
    """Validate the catalogue and apply its one managed deployment."""
    return asyncio.run(_deploy())


if __name__ == "__main__":
    raise SystemExit(main())
