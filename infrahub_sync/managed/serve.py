"""Run the optional managed HTTP service with environment-owned providers."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

import uvicorn
from prefect.client.orchestration import get_client

from .app import create_app
from .auth import EnvironmentPrincipalResolver
from .config_routes import ConfigurationRoutes
from .liveness import LivenessPolicy, RunLivenessReconciler
from .orchestration import CancellationResult, Observation, PoolStatus, PrefectOrchestration, Submission
from .service import ManagedRunService
from .storage import managed_product_projection

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

    async def pool_status(self, work_pool_name: str, now: Any) -> PoolStatus:
        async with get_client() as client:
            return await PrefectOrchestration(client).pool_status(work_pool_name, now)

    async def cancel(self, flow_run_id: str) -> CancellationResult:
        async with get_client() as client:
            return await PrefectOrchestration(client).cancel(flow_run_id)


def build_app(
    *,
    projection_factory: Any = managed_product_projection,
    resolver_factory: Any = EnvironmentPrincipalResolver.from_environment,
    run_service_factory: Any = ManagedRunService,
    configuration_routes_factory: Any = ConfigurationRoutes,
    app_factory: Any = create_app,
) -> FastAPI:
    """Construct the managed app from its environment-owned durable storage profile."""
    projection = projection_factory()
    resolver = resolver_factory()
    configuration_routes = configuration_routes_factory(product_projection=projection, secrets=resolver.secret_values)
    orchestration = _ClientPerCallOrchestration()
    policy = LivenessPolicy.from_environment()
    service = run_service_factory(
        projection,
        orchestration,
        secrets=resolver.secret_values,
        cancellation_recovery_seconds=policy.stall_threshold_seconds,
    )
    reconciler = RunLivenessReconciler(
        projection,
        orchestration,
        policy,
        os.environ.get("INFRAHUB_SYNC_MANAGED_WORK_POOL", "default"),
    )
    return app_factory(service, resolver, configuration_routes, reconciler)


def main() -> None:
    """Serve the managed API; Prefect workers and deployments are separate."""
    uvicorn.run(build_app(), host=os.environ.get("INFRAHUB_SYNC_MANAGED_HOST", "127.0.0.1"), port=8000)


if __name__ == "__main__":
    main()
