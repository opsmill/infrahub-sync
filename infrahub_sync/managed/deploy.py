"""Converge the separate managed deployment onto an existing Prefect work pool."""

from __future__ import annotations

import asyncio
import os

from opsmill_prefect_extras.deployments import apply_deployments
from opsmill_prefect_extras.workflows import WorkflowCatalogue, assert_valid_definitions

from .orchestration import MANAGED_DEFINITION

WORK_POOL_ENV = "INFRAHUB_SYNC_MANAGED_WORK_POOL"
CATALOGUE = WorkflowCatalogue(MANAGED_DEFINITION)


async def _deploy() -> int:
    assert_valid_definitions(CATALOGUE)
    work_pool = os.environ.get(WORK_POOL_ENV)
    if not work_pool:
        msg = f"{WORK_POOL_ENV} must name an existing Prefect work pool"
        raise ValueError(msg)
    report = await apply_deployments(CATALOGUE, work_pool_name=work_pool)
    return 0 if report.is_successful else 1


def main() -> int:
    """Validate the catalogue and apply its one managed deployment."""
    return asyncio.run(_deploy())


if __name__ == "__main__":
    raise SystemExit(main())
