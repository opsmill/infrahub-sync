"""Run the optional managed HTTP service with environment-owned providers."""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

import uvicorn
from prefect.client.orchestration import get_client

from infrahub_sync.product_store import local_product_projection

from ._settings import PRODUCT_CACHE_ENV
from .app import create_app
from .auth import EnvironmentPrincipalResolver
from .config_routes import ConfigurationRoutes
from .orchestration import Observation, PrefectOrchestration, Submission
from .service import ManagedRunService

if TYPE_CHECKING:
    from fastapi import FastAPI


class _ClientPerCallOrchestration:
    """Keep Prefect client ownership inside each asynchronous API operation."""

    async def submit(self, parameters: dict[str, object], *, idempotency_key: str) -> Submission:
        async with get_client() as client:
            return await PrefectOrchestration(client).submit(parameters, idempotency_key=idempotency_key)

    async def observe(self, flow_run_id: str) -> Observation:
        async with get_client() as client:
            return await PrefectOrchestration(client).observe(flow_run_id)

    async def cancel(self, flow_run_id: str) -> Observation:
        async with get_client() as client:
            return await PrefectOrchestration(client).cancel(flow_run_id)


def build_app() -> FastAPI:
    """Construct the managed app from its explicit runtime environment."""
    value = os.environ.get(PRODUCT_CACHE_ENV)
    if not value:
        msg = f"{PRODUCT_CACHE_ENV} must name an absolute durable product-cache directory"
        raise ValueError(msg)
    cache_location = Path(value).expanduser()
    projection = local_product_projection(cache_location)
    resolver = EnvironmentPrincipalResolver.from_environment()
    service = ManagedRunService(projection, _ClientPerCallOrchestration(), secrets=resolver.secret_values)
    configuration_routes = ConfigurationRoutes(cache_location, secrets=resolver.secret_values)
    return create_app(service, resolver, configuration_routes)


def main() -> None:
    """Serve the managed API; Prefect workers and deployments are separate."""
    uvicorn.run(build_app(), host=os.environ.get("INFRAHUB_SYNC_MANAGED_HOST", "127.0.0.1"), port=8000)


if __name__ == "__main__":
    main()
