"""Run the optional Sync HTTP service with environment-owned providers."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

import uvicorn
from prefect.client.orchestration import get_client

from infrahub_sync.execution import collect_secret_values

from .app import create_app
from .auth import EnvironmentPrincipalResolver
from .config_routes import ConfigurationRoutes
from .liveness import LivenessPolicy, RunLivenessReconciler
from .orchestration import CancellationResult, Observation, PoolStatus, PrefectOrchestration, Submission
from .prefect_server import api_startup_check
from .service import RunService
from .storage import service_product_projection

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


async def service_startup_check() -> None:
    """The API's startup check: Infrahub's task manager is a Prefect server this release supports.

    A server of another version, or no `PREFECT_API_URL` at all, stops the API before
    it serves a request. A task manager that is briefly unreachable is reported and
    tolerated (see `prefect_server.api_startup_check`).
    """
    await api_startup_check()


def build_app(
    *,
    projection_factory: Any = service_product_projection,
    resolver_factory: Any = EnvironmentPrincipalResolver.from_environment,
    run_service_factory: Any = RunService,
    configuration_routes_factory: Any = ConfigurationRoutes,
    app_factory: Any = create_app,
    startup_check: Any = None,
) -> FastAPI:
    """Construct the service app from its environment-owned durable storage profile."""
    policy = LivenessPolicy.from_environment(worker_query_seconds=os.environ.get("PREFECT_WORKER_QUERY_SECONDS", "10"))
    projection = projection_factory()
    resolver = resolver_factory()
    startup_check = startup_check or service_startup_check
    # The configuration diagnostics quote declared keys, so this surface needs the ordinary
    # environment secrets as well as the principal resolver's bearer tokens.
    configuration_secrets = tuple(dict.fromkeys((*collect_secret_values(), *resolver.secret_values)))
    configuration_routes = configuration_routes_factory(product_projection=projection, secrets=configuration_secrets)
    orchestration = _ClientPerCallOrchestration()
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
        os.environ.get("INFRAHUB_SYNC_SERVICE_WORK_POOL", "default"),
    )
    return app_factory(service, resolver, configuration_routes, reconciler, startup_check)


def main() -> None:
    """Serve the Sync API; Prefect workers and deployments are separate.

    The Prefect startup check runs in the app's lifespan (see `create_app`).
    """
    uvicorn.run(build_app(), host=os.environ.get("INFRAHUB_SYNC_SERVICE_HOST", "127.0.0.1"), port=8000)


if __name__ == "__main__":
    main()
