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
from .orchestration import (
    CancellationResult,
    Observation,
    PoolStatus,
    PrefectOrchestration,
    RunLogRead,
    Submission,
)
from .prefect_ui import prefect_ui_url_from_environment
from .service import RunService
from .settings import web_directory
from .start_check import StartRefusedError, check_bootstrap_record
from .storage import service_product_projection
from .trust_store import TrustStoreError, install_custom_ca_bundle
from .web import create_site

if TYPE_CHECKING:
    from collections.abc import Sequence

    from fastapi import FastAPI
    from starlette.applications import Starlette


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

    async def read_logs(self, flow_run_ids: Sequence[str]) -> RunLogRead:
        async with get_client() as client:
            return await PrefectOrchestration(client).read_logs(flow_run_ids)


def build_app(
    *,
    projection_factory: Any = service_product_projection,
    resolver_factory: Any = EnvironmentPrincipalResolver.from_environment,
    run_service_factory: Any = RunService,
    configuration_routes_factory: Any = ConfigurationRoutes,
    app_factory: Any = create_app,
    start_check: Any = check_bootstrap_record,
) -> FastAPI:
    """Construct the service app from its environment-owned durable storage profile.

    The CA bundle is installed here, before any client exists, so `main()` and an ASGI server
    that calls this factory (`uvicorn --factory`, as `dev.api` does) trust the same certificates.

    The bootstrap record is checked here too, before the projection initializes the product
    schema, so a refused start, through `main()` or through `uvicorn --factory`, changes
    nothing in the database (spec 018 FR-007b).

    Raises:
        TrustStoreError: when the CA directory holds a private key or an unreadable certificate.
        StartRefusedError: when the bootstrap record is missing or names another Sync version
            or work pool, or cannot be read.
    """
    install_custom_ca_bundle()
    policy = LivenessPolicy.from_environment(worker_query_seconds=os.environ.get("PREFECT_WORKER_QUERY_SECONDS", "10"))
    # Validated before any dependency is built, so a bad address stops start-up with no side effect.
    prefect_ui_url = prefect_ui_url_from_environment()
    work_pool = os.environ.get("INFRAHUB_SYNC_SERVICE_WORK_POOL", "default")
    start_check(work_pool)
    projection = projection_factory()
    resolver = resolver_factory()
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
        prefect_ui_url=prefect_ui_url,
    )
    reconciler = RunLivenessReconciler(
        projection,
        orchestration,
        policy,
        work_pool,
    )
    return app_factory(service, resolver, configuration_routes, reconciler)


def build_service(
    *,
    projection_factory: Any = service_product_projection,
    resolver_factory: Any = EnvironmentPrincipalResolver.from_environment,
    run_service_factory: Any = RunService,
    configuration_routes_factory: Any = ConfigurationRoutes,
    app_factory: Any = create_app,
    start_check: Any = check_bootstrap_record,
) -> Starlette:
    """Construct the served application: the Sync API under ``/api`` and the web interface elsewhere.

    The factories and ``start_check`` pass through to ``build_app`` unchanged. ``INFRAHUB_SYNC_WEB_DIRECTORY`` is read
    here, once; when it names no built web interface, only the API is served.

    Raises:
        TrustStoreError: when the CA directory holds a private key or an unreadable certificate.
        StartRefusedError: when the bootstrap record does not name this version and work pool.
    """
    api = build_app(
        projection_factory=projection_factory,
        resolver_factory=resolver_factory,
        run_service_factory=run_service_factory,
        configuration_routes_factory=configuration_routes_factory,
        app_factory=app_factory,
        start_check=start_check,
    )
    return create_site(api, web_directory())


def main() -> None:
    """Serve the Sync API and the web interface; Prefect workers and deployments are separate."""
    try:
        app = build_service()
    except (TrustStoreError, StartRefusedError) as error:
        raise SystemExit(str(error)) from None
    uvicorn.run(app, host=os.environ.get("INFRAHUB_SYNC_SERVICE_HOST", "127.0.0.1"), port=8000)


if __name__ == "__main__":
    main()
