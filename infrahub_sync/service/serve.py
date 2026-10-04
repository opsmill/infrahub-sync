"""Run the optional Sync HTTP service with environment-owned providers."""

from __future__ import annotations

import logging
import os
from functools import partial
from typing import TYPE_CHECKING, Any

import uvicorn
from infrahub_sdk.exceptions import AuthenticationError
from infrahub_sdk.exceptions import Error as InfrahubSdkError
from prefect.client.orchestration import get_client

from infrahub_sync.execution import collect_secret_values
from infrahub_sync.platform.client import (
    SERVICE_ACCOUNT_REFUSED,
    PlatformSettings,
    ServiceAccountRefusedError,
    caller_client,
    default_branch,
    service_client,
)
from infrahub_sync.platform.schema_check import require_sync_schema

from .app import create_app
from .auth import InfrahubPrincipalResolver
from .config_routes import ConfigurationRoutes
from .infrahub_records import infrahub_records, mirror_run
from .liveness import LivenessPolicy, RunLivenessReconciler
from .orchestration import CancellationResult, Observation, PoolStatus, PrefectOrchestration, Submission
from .prefect_server import api_startup_check
from .service import RunService
from .storage import service_product_projection

if TYPE_CHECKING:
    from collections.abc import Iterable

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

    async def tag_nodes(self, flow_run_id: str, node_ids: Iterable[str]) -> None:
        async with get_client() as client:
            await PrefectOrchestration(client).tag_nodes(flow_run_id, node_ids)


logger = logging.getLogger(__name__)


def infrahub_principal_resolver() -> InfrahubPrincipalResolver:
    """Identify each caller through Infrahub, with the caller's own token."""
    settings = PlatformSettings.from_environment()
    return InfrahubPrincipalResolver(partial(caller_client, settings), service_secrets=(settings.token,))


async def service_startup_check() -> None:
    """The API's startup checks: Infrahub's task manager, then the Sync schema in Infrahub.

    A missing or outdated schema extension stops the API, and so does a service-account
    token Infrahub refuses. An Infrahub that cannot be
    reached is reported and tolerated, like a briefly absent task manager: requests
    are answered with 503 until it is back.
    """
    await api_startup_check()
    client = service_client(PlatformSettings.from_environment())
    try:
        branch = await default_branch(client)
    except AuthenticationError:
        raise ServiceAccountRefusedError(SERVICE_ACCOUNT_REFUSED) from None
    except InfrahubSdkError as error:
        logger.warning("Infrahub could not be reached at startup (%s); the API serves anyway", type(error).__name__)
        return
    await require_sync_schema(client, branch)


def build_app(
    *,
    projection_factory: Any = service_product_projection,
    resolver_factory: Any = None,
    run_service_factory: Any = RunService,
    configuration_routes_factory: Any = ConfigurationRoutes,
    app_factory: Any = create_app,
    startup_check: Any = None,
    records_factory: Any = None,
) -> FastAPI:
    """Construct the service app from its environment-owned durable storage profile."""
    policy = LivenessPolicy.from_environment(worker_query_seconds=os.environ.get("PREFECT_WORKER_QUERY_SECONDS", "10"))
    projection = projection_factory()
    resolver = (resolver_factory or infrahub_principal_resolver)()
    startup_check = startup_check or service_startup_check
    # The configuration diagnostics quote declared keys, so this surface needs the ordinary
    # environment secrets as well as the principal resolver's bearer tokens.
    configuration_secrets = tuple(dict.fromkeys((*collect_secret_values(), *resolver.secret_values)))
    orchestration = _ClientPerCallOrchestration()
    # Infrahub's records whenever the API identifies callers through Infrahub; a
    # resolver without Infrahub keeps configurations in the product store.
    if records_factory is None and isinstance(resolver, InfrahubPrincipalResolver):
        records_factory = infrahub_records
    configurations, mirror = records_factory() if records_factory is not None else (None, None)
    configuration_routes = configuration_routes_factory(
        product_projection=projection, secrets=configuration_secrets, configurations=configurations
    )
    service = run_service_factory(
        projection,
        orchestration,
        secrets=resolver.secret_values,
        cancellation_recovery_seconds=policy.stall_threshold_seconds,
        configurations=configurations,
        mirror=mirror,
    )
    reconciler = RunLivenessReconciler(
        projection,
        orchestration,
        policy,
        os.environ.get("INFRAHUB_SYNC_SERVICE_WORK_POOL", "default"),
        on_change=partial(mirror_run, projection, mirror) if mirror is not None else None,
    )
    return app_factory(service, resolver, configuration_routes, reconciler, startup_check)


def main() -> None:
    """Serve the Sync API; Prefect workers and deployments are separate.

    The Prefect startup check runs in the app's lifespan (see `create_app`).
    """
    uvicorn.run(build_app(), host=os.environ.get("INFRAHUB_SYNC_SERVICE_HOST", "127.0.0.1"), port=8000)


if __name__ == "__main__":
    main()
