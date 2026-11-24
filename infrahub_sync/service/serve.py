"""Run the optional Sync HTTP service with environment-owned providers."""

from __future__ import annotations

import os
from functools import partial
from typing import TYPE_CHECKING, Any

import structlog
import uvicorn
from prefect.client.orchestration import get_client
from prefect.settings import get_current_settings

from infrahub_sync.execution import collect_secret_values
from infrahub_sync.product_store.accounts import DuplicateCredentialError

from .app import create_app
from .auth import StoreAccountResolver, apply_admin_account
from .config_routes import ConfigurationRoutes
from .credentials import no_account_hash
from .health import (
    Check,
    DependencyCheck,
    HealthChecker,
    database_check,
    object_store_check,
    task_manager_check,
    task_manager_db_check,
)
from .liveness import LivenessPolicy, RunLivenessReconciler
from .orchestration import (
    CancellationResult,
    DeploymentSchedule,
    Observation,
    ObservedDeploymentSchedule,
    PoolStatus,
    PrefectOrchestration,
    RunLogRead,
    Submission,
)
from .prefect_ui import prefect_ui_url_from_environment
from .schedules import ScheduleService
from .service import RunService
from .settings import (
    ADMIN_ACCOUNT_PASSWORD_ENV,
    ADMIN_ACCOUNT_TOKEN_ENV,
    AccountSettingsError,
    HealthSettingsError,
    admin_account_settings,
    health_check_timeout_seconds,
    health_min_workers,
    session_idle_seconds,
    web_directory,
)
from .start_check import StartRefusedError, check_bootstrap_record
from .storage import S3_BUCKET_ENV, _database_url, _required_setting, service_product_projection
from .trust_store import TrustStoreError, install_custom_ca_bundle
from .web import create_site

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from fastapi import FastAPI
    from starlette.applications import Starlette

logger = structlog.get_logger(__name__)

# The write targets of a schedule that this build can run. A request for another write target
# is refused with ``schedule-setting-not-available``.
DELIVERED_SCHEDULE_WRITE_TARGETS: frozenset[str] = frozenset({"branch-review", "default-branch"})


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

    async def read_schedules(self) -> tuple[ObservedDeploymentSchedule, ...]:
        async with get_client() as client:
            return await PrefectOrchestration(client).read_schedules()

    async def create_schedule(self, schedule: DeploymentSchedule) -> str:
        async with get_client() as client:
            return await PrefectOrchestration(client).create_schedule(schedule)

    async def update_schedule(self, orchestrator_id: str, schedule: DeploymentSchedule) -> None:
        async with get_client() as client:
            await PrefectOrchestration(client).update_schedule(orchestrator_id, schedule)

    async def delete_schedule(self, orchestrator_id: str) -> None:
        async with get_client() as client:
            await PrefectOrchestration(client).delete_schedule(orchestrator_id)

    async def create_scheduled_runs(self) -> None:
        async with get_client() as client:
            await PrefectOrchestration(client).create_scheduled_runs()


class HealthSettingMissingError(Exception):
    """A health check cannot run because a setting of its component is absent or refused."""

    def __init__(self) -> None:
        super().__init__("a setting of this component is absent or refused")


def _missing_setting_check() -> Check:
    async def check() -> None:  # noqa: RUF029 - the checker awaits every check.
        raise HealthSettingMissingError

    return check


def health_checks(environ: Mapping[str, str], limit: int, prefect_api_url: str | None) -> list[DependencyCheck]:
    """Build the four component checks from the settings the product store and Prefect use.

    In a served process the product projection is built first from the same settings, and it
    stops start-up when one is refused, so a refused setting reaches this function only when a
    test built the API with a fake projection. Such a component then reports `down` with
    `unknown_error` instead of adding a start-up failure; no message names a value.
    """
    names_missing: list[str] = []
    try:
        database = database_check(_database_url(environ), limit)
    except ValueError:
        names_missing.append("database")
        database = _missing_setting_check()
    try:
        object_store = object_store_check(environ, _required_setting(environ, S3_BUCKET_ENV), limit)
    except ValueError:
        names_missing.append("object_store")
        object_store = _missing_setting_check()
    if prefect_api_url:
        task_manager = task_manager_check(prefect_api_url, limit)
        task_manager_db = task_manager_db_check(prefect_api_url, limit)
    else:
        names_missing.extend(("task_manager", "task_manager_db"))
        task_manager = task_manager_db = _missing_setting_check()
    for name in names_missing:
        logger.warning("health check has no usable setting", component=name)
    return [
        DependencyCheck("database", database),
        DependencyCheck("object_store", object_store),
        DependencyCheck("task_manager", task_manager),
        DependencyCheck("task_manager_db", task_manager_db),
    ]


def build_app(
    *,
    projection_factory: Any = service_product_projection,
    resolver_factory: Any = None,
    run_service_factory: Any = RunService,
    configuration_routes_factory: Any = ConfigurationRoutes,
    app_factory: Any = create_app,
    start_check: Any = check_bootstrap_record,
    schedule_service_factory: Any = ScheduleService,
) -> FastAPI:
    """Construct the service app from its environment-owned durable storage profile.

    The CA bundle is installed here, before any client exists, so `main()` and an ASGI server
    that calls this factory (`uvicorn --factory`, as `dev.api` does) trust the same certificates.

    The bootstrap record is checked here too, before the projection initializes the product
    schema, so a refused start, through `main()` or through `uvicorn --factory`, changes
    nothing in the database (spec 018 FR-007b).

    Without a ``resolver_factory``, the ``admin`` account of the store is made to match
    ``INFRAHUB_SYNC_ADMIN_ACCOUNT_PASSWORD`` and ``INFRAHUB_SYNC_ADMIN_ACCOUNT_TOKEN``, the
    password is removed from the environment of the process, and callers are resolved from the
    accounts of the store.

    Raises:
        TrustStoreError: when the CA directory holds a private key or an unreadable certificate.
        AccountSettingsError: when a variable of the ``admin`` account or the idle period of a
            session holds a value that is not accepted, or when another account already has the
            token of ``admin`` as an API token. The message names the variable.
        StartRefusedError: when the bootstrap record is missing or names another Sync version
            or work pool, or cannot be read.
        HealthSettingsError: when ``INFRAHUB_SYNC_HEALTH_CHECK_TIMEOUT`` or
            ``INFRAHUB_SYNC_HEALTH_MIN_WORKERS`` holds a value that is not accepted; it is raised
            before the bootstrap record is read. The message names the variable.
    """
    install_custom_ca_bundle()
    policy = LivenessPolicy.from_environment(worker_query_seconds=os.environ.get("PREFECT_WORKER_QUERY_SECONDS", "10"))
    health_timeout = health_check_timeout_seconds()
    health_minimum = health_min_workers()
    # Validated before any dependency is built, so a bad address stops start-up with no side effect.
    prefect_ui_url = prefect_ui_url_from_environment()
    admin_account = admin_account_settings() if resolver_factory is None else None
    idle_seconds = session_idle_seconds()
    work_pool = os.environ.get("INFRAHUB_SYNC_SERVICE_WORK_POOL", "default")
    start_check(work_pool)
    projection = projection_factory()
    if admin_account is None:
        resolver = resolver_factory()
    else:
        try:
            apply_admin_account(projection, admin_account)
        except DuplicateCredentialError:
            msg = f"{ADMIN_ACCOUNT_TOKEN_ENV} is the value of an API token of another account; choose another value"
            raise AccountSettingsError(msg) from None
        # Removed before the secret values are collected below: the password is in no list of
        # secret values, so no free-text check can be used to test candidate passwords.
        os.environ.pop(ADMIN_ACCOUNT_PASSWORD_ENV, None)
        # Made now, so the first sign-in with an unknown name does not compute it and take
        # longer than a sign-in with a known name.
        no_account_hash()
        resolver = StoreAccountResolver(projection, idle_seconds=idle_seconds, admin_token=admin_account.token)
    # The configuration diagnostics quote declared keys, so this surface needs the ordinary
    # environment secrets as well as the `admin` token that the environment defines.
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
    # Built after the projection, which has already refused any bad database or S3 setting.
    health_checker = HealthChecker(
        checks=health_checks(os.environ, health_timeout, get_current_settings().api.url),
        pool_reader=partial(orchestration.pool_status, work_pool),
        minimum=health_minimum,
        check_timeout=health_timeout,
    )
    reconciler = RunLivenessReconciler(
        projection,
        orchestration,
        policy,
        work_pool,
    )
    schedules = schedule_service_factory(
        projection,
        orchestration,
        resolver,
        delivered_write_targets=DELIVERED_SCHEDULE_WRITE_TARGETS,
        secrets=resolver.secret_values,
    )
    return app_factory(
        service, resolver, configuration_routes, reconciler, schedules=schedules, health_checker=health_checker
    )


def build_service(
    *,
    projection_factory: Any = service_product_projection,
    resolver_factory: Any = None,
    run_service_factory: Any = RunService,
    configuration_routes_factory: Any = ConfigurationRoutes,
    app_factory: Any = create_app,
    start_check: Any = check_bootstrap_record,
    schedule_service_factory: Any = ScheduleService,
) -> Starlette:
    """Construct the served application: the Sync API under ``/api`` and the web interface elsewhere.

    The factories and ``start_check`` pass through to ``build_app`` unchanged. ``INFRAHUB_SYNC_WEB_DIRECTORY`` is read
    here, once; when it names no built web interface, only the API is served.

    Raises:
        TrustStoreError: when the CA directory holds a private key or an unreadable certificate.
        AccountSettingsError: when a variable of the ``admin`` account or the idle period of a
            session holds a value that is not accepted.
        StartRefusedError: when the bootstrap record does not name this version and work pool.
    """
    api = build_app(
        projection_factory=projection_factory,
        resolver_factory=resolver_factory,
        run_service_factory=run_service_factory,
        configuration_routes_factory=configuration_routes_factory,
        app_factory=app_factory,
        start_check=start_check,
        schedule_service_factory=schedule_service_factory,
    )
    return create_site(api, web_directory())


def main() -> None:
    """Serve the Sync API and the web interface; Prefect workers and deployments are separate."""
    try:
        app = build_service()
    except (TrustStoreError, AccountSettingsError, HealthSettingsError, StartRefusedError) as error:
        raise SystemExit(str(error)) from None
    uvicorn.run(app, host=os.environ.get("INFRAHUB_SYNC_SERVICE_HOST", "127.0.0.1"), port=8000)


if __name__ == "__main__":
    main()
