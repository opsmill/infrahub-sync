"""Run the optional Sync HTTP service with environment-owned providers."""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any

import uvicorn
from prefect.client.orchestration import get_client

from infrahub_sync.execution import collect_secret_values

from .app import create_app
from .auth import AnonymousAccess, EnvironmentPrincipalResolver, PrincipalDirectory
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
from .settings import ANONYMOUS_ACTOR_ENV, anonymous_actor, anonymous_hosts, web_directory
from .site import create_site
from .storage import service_product_projection
from .trust_store import TrustStoreError, install_custom_ca_bundle

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from collections.abc import Sequence

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

    async def read_logs(self, flow_run_ids: Sequence[str]) -> RunLogRead:
        async with get_client() as client:
            return await PrefectOrchestration(client).read_logs(flow_run_ids)


def resolve_anonymous_access(resolver: PrincipalDirectory) -> AnonymousAccess | None:
    """Return the anonymous access the settings name, or ``None`` when it is off, and log it once.

    Raises:
        ValueError: The anonymous actor is not a configured principal, or the allowed hosts are
            empty; the message names the variable and never its value or a token.
    """
    actor = anonymous_actor()
    if actor is None:
        return None
    principal = resolver.principal_for_actor(actor)
    if principal is None:
        msg = f"{ANONYMOUS_ACTOR_ENV} must name an actor in INFRAHUB_SYNC_SERVICE_BEARER_TOKENS"
        raise ValueError(msg)
    access = AnonymousAccess(principal=principal, allowed_hosts=anonymous_hosts())
    logger.log(
        logging.WARNING if principal.administrator else logging.INFO,
        'anonymous access is on: requests without a token act as %s"%s" from hosts %s',
        "administrator " if principal.administrator else "",
        principal.actor,
        ", ".join(sorted(access.allowed_hosts)),
    )
    return access


def build_app(
    *,
    projection_factory: Any = service_product_projection,
    resolver_factory: Any = EnvironmentPrincipalResolver.from_environment,
    run_service_factory: Any = RunService,
    configuration_routes_factory: Any = ConfigurationRoutes,
    app_factory: Any = create_app,
) -> FastAPI:
    """Construct the site: the Sync API under ``/api`` and the web interface, from the environment.

    The CA bundle is installed here, before any client exists, so `main()` and an ASGI server
    that calls this factory (`uvicorn --factory`, as `dev.api` does) trust the same certificates.
    For the same reason, this function sets up the start-up lines itself.

    Raises:
        TrustStoreError: when the CA directory holds a private key or an unreadable certificate.
        ValueError: A setting holds a value that is not accepted, so the service does not start.
    """
    _show_start_up_lines()
    install_custom_ca_bundle()
    policy = LivenessPolicy.from_environment(worker_query_seconds=os.environ.get("PREFECT_WORKER_QUERY_SECONDS", "10"))
    # Validated before any dependency is built, so a bad address stops start-up with no side effect.
    prefect_ui_url = prefect_ui_url_from_environment()
    projection = projection_factory()
    resolver = resolver_factory()
    anonymous = resolve_anonymous_access(resolver)
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
        os.environ.get("INFRAHUB_SYNC_SERVICE_WORK_POOL", "default"),
    )
    api = app_factory(service, resolver, configuration_routes, reconciler, anonymous=anonymous)
    return create_site(api, web_directory=web_directory())


def _show_start_up_lines() -> None:
    """Print this module's start-up lines, such as the anonymous access line, in uvicorn's format.

    Uvicorn configures only its own loggers, so without a handler of its own an INFO line is lost.
    """
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(levelname)s:     %(message)s"))
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)


def main() -> None:
    """Serve the Sync API under ``/api`` and the web interface; Prefect workers and deployments are separate."""
    try:
        app = build_app()
    except TrustStoreError as error:
        raise SystemExit(str(error)) from None
    uvicorn.run(app, host=os.environ.get("INFRAHUB_SYNC_SERVICE_HOST", "127.0.0.1"), port=8000)


if __name__ == "__main__":
    main()
