"""FastAPI routing for the stable Sync HTTP contract."""

import logging
import os
from asyncio import CancelledError, create_task, sleep
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, FastAPI, Header, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from infrahub_sync.client.models import (
    DEFAULT_LOG_LIMIT,
    LOG_STAGES,
    MAX_LOG_LIMIT,
    MAX_REPORT_LIMIT,
    MIN_LIMIT,
    PlanReportPageResource,
)
from infrahub_sync.configuration.storage import UNSUPPORTED_STORE_MESSAGE, UNSUPPORTED_STORE_REASON
from infrahub_sync.plan.models import PROBLEM_TYPES

from .auth import Principal, PrincipalResolver
from .compatibility import API_STABILITY, API_VERSIONS, installed_server_version
from .config_routes import (
    CONFIG_ID_PATTERN,
    MAX_REGISTRY_VERSION,
    ConfigurationAPIError,
    ConfigurationRoutes,
    configuration_router,
    parse_bounded_integer,
    parse_page_limit,
)
from .liveness import RunLivenessReconciler
from .models import (
    ApplyRunRequest,
    ApprovedRunResource,
    ApproveRunRequest,
    ArtifactListResource,
    CancelRunRequest,
    ConfigErrorDetail,
    ConfigErrorEnvelope,
    CreateRunRequest,
    ErrorDetail,
    ErrorEnvelope,
    EventPage,
    PlanResource,
    PrincipalResource,
    ResultsResource,
    ResumeRunRequest,
    RunLogPage,
    RunPage,
    RunResource,
    ServiceStatusResource,
    VerifyRunRequest,
    VersionResource,
)
from .paging import InvalidCursorError
from .service import MAX_EVENT_OFFSET, RunService, ServiceAPIError, parse_event_time
from .settings import approval_required, validate_settings

logger = logging.getLogger(__name__)

ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    status: {"model": ErrorEnvelope} for status in (401, 403, 404, 409, 410, 422, 503)
}


async def _service_error(_request: Request, exc: ServiceAPIError) -> JSONResponse:  # noqa: RUF029
    """Return a classified service refusal; its ``details`` appear only when the refusal has any."""
    envelope = ErrorEnvelope(
        error=ErrorDetail(
            code=exc.code,
            message=exc.message,
            status=exc.status,
            run_id=exc.run_id,
            mutation_id=exc.mutation_id,
            details=exc.details,
            left_out=exc.left_out,
        )
    )
    headers = {"WWW-Authenticate": "Bearer"} if exc.status == 401 else None
    return JSONResponse(status_code=exc.status, content=_error_content(envelope), headers=headers)


def _error_content(envelope: ErrorEnvelope) -> dict[str, Any]:
    """Dump an error envelope, leaving ``details`` out when the refusal has none.

    Most refusals carry no structured context, so their body keeps the fields it always had.
    """
    content = envelope.model_dump(mode="json")
    if envelope.error.details is None:
        del content["error"]["details"]
    return content


def _repeated(values: list[str] | None) -> tuple[str, ...]:
    """Return a repeatable query parameter's values, none when it is absent."""
    return () if values is None else tuple(values)


def _optional_time(value: str | None) -> datetime | None:
    """Parse an event-list time bound when one is given."""
    return None if value is None else parse_event_time(value)


def _page_offset(value: str | None) -> int:
    """Parse the ``offset`` of the event list or a run's log without FastAPI's coercions; zero when absent."""
    return 0 if value is None else parse_bounded_integer(value, minimum=0, maximum=MAX_EVENT_OFFSET)


def _log_stage(value: str | None) -> str | None:
    """Parse the run log's ``stage`` filter; none when it is absent."""
    if value is not None and value not in LOG_STAGES:
        raise ServiceAPIError(422, "request-invalid", "the request does not match the API schema")
    return value


def _log_limit(value: str | None) -> int:
    """Parse the run log's ``limit`` without FastAPI's coercions; the default when absent."""
    if value is None:
        return DEFAULT_LOG_LIMIT
    return parse_bounded_integer(value, minimum=MIN_LIMIT, maximum=MAX_LOG_LIMIT)


def _report_limit(value: str | None) -> int:
    """Parse a plan report route's ``limit`` without FastAPI's coercions; the maximum when absent."""
    if value is None:
        return MAX_REPORT_LIMIT
    return parse_bounded_integer(value, minimum=MIN_LIMIT, maximum=MAX_REPORT_LIMIT)


def plan_router(service: RunService, authenticate: Callable[..., Principal]) -> APIRouter:
    """Return the authenticated routes of a saved plan: its review and its problem, left-out and kept lists.

    The three lists are spec 019's (contracts/sync-api.md).
    """
    router = APIRouter()

    @router.get("/runs/{run_id}/plan", responses=ERROR_RESPONSES)
    def get_plan(run_id: str, _principal: Annotated[Principal, Depends(authenticate)]) -> PlanResource:
        return service.get_plan(run_id)

    @router.get("/runs/{run_id}/plan/problems", responses=ERROR_RESPONSES)
    def get_plan_problems(  # pylint: disable=too-many-arguments,too-many-positional-arguments  # the route's filters and paging
        run_id: str,
        _principal: Annotated[Principal, Depends(authenticate)],
        offset: Annotated[str | None, Query()] = None,
        limit: Annotated[str | None, Query()] = None,
        problem_type: Annotated[str | None, Query()] = None,
        kind: Annotated[str | None, Query()] = None,
        expected_checksum: Annotated[str | None, Query()] = None,
    ) -> PlanReportPageResource:
        """Return one page of the saved plan's problems (spec 019, contracts/sync-api.md)."""
        if problem_type is not None and problem_type not in PROBLEM_TYPES:
            raise ServiceAPIError(422, "request-invalid", "the request does not match the API schema")
        return service.get_plan_report(
            run_id,
            entry="problem",
            offset=_page_offset(offset),
            limit=_report_limit(limit),
            problem_type=problem_type,
            kind=kind,
            expected_checksum=expected_checksum,
        )

    @router.get("/runs/{run_id}/plan/left-out", responses=ERROR_RESPONSES)
    def get_plan_left_out(
        run_id: str,
        _principal: Annotated[Principal, Depends(authenticate)],
        offset: Annotated[str | None, Query()] = None,
        limit: Annotated[str | None, Query()] = None,
        kind: Annotated[str | None, Query()] = None,
        expected_checksum: Annotated[str | None, Query()] = None,
    ) -> PlanReportPageResource:
        """Return one page of the records the saved plan leaves out (spec 019)."""
        return service.get_plan_report(
            run_id,
            entry="left_out",
            offset=_page_offset(offset),
            limit=_report_limit(limit),
            kind=kind,
            expected_checksum=expected_checksum,
        )

    @router.get("/runs/{run_id}/plan/kept", responses=ERROR_RESPONSES)
    def get_plan_kept(
        run_id: str,
        _principal: Annotated[Principal, Depends(authenticate)],
        offset: Annotated[str | None, Query()] = None,
        limit: Annotated[str | None, Query()] = None,
        kind: Annotated[str | None, Query()] = None,
        expected_checksum: Annotated[str | None, Query()] = None,
    ) -> PlanReportPageResource:
        """Return one page of the records the saved plan keeps without one optional relationship (spec 019)."""
        return service.get_plan_report(
            run_id,
            entry="kept",
            offset=_page_offset(offset),
            limit=_report_limit(limit),
            kind=kind,
            expected_checksum=expected_checksum,
        )

    return router


def approve_router(
    service: RunService, authenticate: Callable[..., Principal], idempotency_key: Callable[..., str]
) -> APIRouter:
    """Return the authenticated ``POST /runs/{run_id}/approve`` route (spec 019, contracts/http-api.md)."""
    router = APIRouter()

    @router.post(
        "/runs/{run_id}/approve", status_code=200, response_model=ApprovedRunResource, responses=ERROR_RESPONSES
    )
    async def approve_run(
        run_id: str,
        body: ApproveRunRequest,
        principal: Annotated[Principal, Depends(authenticate)],
        key: Annotated[str, Depends(idempotency_key)],
    ) -> JSONResponse:
        """Record approval of the run's saved plan without applying it; submits no execution."""
        status, content = await service.approve_run(run_id, body, principal, key)
        return JSONResponse(status_code=status, content=content)

    return router


def run_logs_router(service: RunService, authenticate: Callable[..., Principal]) -> APIRouter:
    """Return the authenticated ``GET /runs/{run_id}/logs`` route (spec 017, contracts/logs-route.md)."""
    router = APIRouter()

    @router.get("/runs/{run_id}/logs", responses=ERROR_RESPONSES)
    async def read_run_logs(
        run_id: str,
        _principal: Annotated[Principal, Depends(authenticate)],
        stage: Annotated[str | None, Query()] = None,
        limit: Annotated[str | None, Query()] = None,
        offset: Annotated[str | None, Query()] = None,
    ) -> RunLogPage:
        """Return one page of the lines Sync wrote during the run, oldest first."""
        return await service.read_run_logs(
            run_id, stage=_log_stage(stage), limit=_log_limit(limit), offset=_page_offset(offset)
        )

    return router


def events_router(service: RunService, authenticate: Callable[..., Principal]) -> APIRouter:
    """Return the authenticated ``GET /events`` route (spec 017, contracts/events-route.md)."""
    router = APIRouter()

    @router.get("/events", responses=ERROR_RESPONSES)
    def list_events(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        _principal: Annotated[Principal, Depends(authenticate)],
        run_id: Annotated[str | None, Query()] = None,
        config_id: Annotated[str | None, Query()] = None,
        event_type: Annotated[list[str] | None, Query()] = None,
        related: Annotated[list[str] | None, Query()] = None,
        since: Annotated[str | None, Query()] = None,
        until: Annotated[str | None, Query()] = None,
        limit: Annotated[str | None, Query()] = None,
        offset: Annotated[str | None, Query()] = None,
    ) -> EventPage:
        """Return one page of typed events, oldest first, that match every given filter."""
        return service.list_events(
            run_id=run_id,
            config_id=config_id,
            event_types=_repeated(event_type),
            related=_repeated(related),
            since=_optional_time(since),
            until=_optional_time(until),
            limit=parse_page_limit(limit),
            offset=_page_offset(offset),
        )

    return router


def _include_routers(
    application: FastAPI,
    service: RunService,
    authenticate: Callable[..., Principal],
    idempotency_key: Callable[..., str],
    configuration_routes: ConfigurationRoutes | None,
) -> None:
    """Add the routes built outside ``create_app``: configurations, approval, events, run logs and the saved plan."""
    if configuration_routes is not None:
        application.include_router(configuration_router(configuration_routes, authenticate, idempotency_key))
    application.include_router(approve_router(service, authenticate, idempotency_key))
    application.include_router(events_router(service, authenticate))
    application.include_router(run_logs_router(service, authenticate))
    application.include_router(plan_router(service, authenticate))


def create_app(
    service: RunService,
    resolver: PrincipalResolver,
    configuration_routes: ConfigurationRoutes | None = None,
    reconciler: RunLivenessReconciler | None = None,
) -> FastAPI:
    """Create the service application from explicit providers.

    Raises:
        ValueError: A service setting such as ``INFRAHUB_SYNC_REQUIRE_APPROVAL`` holds a value that
            is not accepted, so the service does not start.
    """
    validate_settings()

    @asynccontextmanager
    async def lifespan(_application: FastAPI):
        task = None
        if reconciler is not None:

            async def reconcile_loop() -> None:
                while True:
                    try:
                        await reconciler.reconcile_once()
                    except CancelledError:  # pylint: disable=try-except-raise
                        raise
                    except Exception as exc:  # noqa: BLE001  # pylint: disable=broad-exception-caught
                        logger.error("service liveness iteration failed: %s", type(exc).__name__)  # noqa: TRY400
                    await sleep(reconciler.cadence_seconds)

            task = create_task(reconcile_loop())
        try:
            yield
        finally:
            if task is not None:
                task.cancel()
                with suppress(CancelledError):
                    await task

    application = FastAPI(title="Infrahub Sync API", version=installed_server_version(), lifespan=lifespan)
    bearer_auth = HTTPBearer(auto_error=False, scheme_name="BearerAuth")

    def authenticate(
        request: Request,
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_auth)],
    ) -> Principal:
        token = credentials.credentials.lstrip(" ") if credentials is not None else ""
        if not token:
            service.record_authentication_refusal(request.url.path, "missing-or-invalid-authorization")
            raise ServiceAPIError(401, "unauthenticated", "a valid bearer token is required")
        principal = resolver.resolve(token)
        if principal is None:
            service.record_authentication_refusal(request.url.path, "invalid-bearer-token")
            raise ServiceAPIError(401, "unauthenticated", "a valid bearer token is required")
        return principal

    def idempotency_key(value: Annotated[str | None, Header(alias="Idempotency-Key")] = None) -> str:
        if value is None or not value.strip():
            raise ServiceAPIError(422, "idempotency-key-required", "a non-empty Idempotency-Key header is required")
        return value

    application.add_exception_handler(ServiceAPIError, _service_error)  # ty: ignore[invalid-argument-type]

    @application.exception_handler(ConfigurationAPIError)
    async def configuration_error(_request: Request, exc: ConfigurationAPIError) -> JSONResponse:  # noqa: RUF029
        """Return a safe configuration refusal with its structured reason."""
        envelope = ConfigErrorEnvelope(
            error=ConfigErrorDetail(
                code=f"configs-{exc.family}",
                message=(
                    UNSUPPORTED_STORE_MESSAGE
                    if exc.reason == UNSUPPORTED_STORE_REASON
                    else "the configuration service refused the request"
                ),
                status=exc.status,
                family=exc.family,
                reason=exc.reason,
            )
        )
        return JSONResponse(status_code=exc.status, content=envelope.model_dump(mode="json"))

    @application.exception_handler(InvalidCursorError)
    async def invalid_cursor(_request: Request, exc: InvalidCursorError) -> JSONResponse:  # noqa: RUF029
        """Refuse a paging cursor that does not decode or was issued for other filters."""
        envelope = ErrorEnvelope(error=ErrorDetail(code="invalid-cursor", message=exc.message, status=422))
        return JSONResponse(status_code=422, content=_error_content(envelope))

    @application.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, _exc: RequestValidationError) -> JSONResponse:  # noqa: RUF029
        envelope = ErrorEnvelope(
            error=ErrorDetail(code="request-invalid", message="the request does not match the API schema", status=422)
        )
        return JSONResponse(status_code=422, content=_error_content(envelope))

    @application.get("/version")
    def get_version() -> VersionResource:
        """Return the installed server and supported unstable API versions."""
        return VersionResource(
            server_version=installed_server_version(), api_versions=API_VERSIONS, stability=API_STABILITY
        )

    @application.get("/status")
    async def get_status() -> ServiceStatusResource:
        """Return unauthenticated lifecycle state without provider identifiers."""
        return await service.status(os.environ.get("INFRAHUB_SYNC_SERVICE_WORK_POOL", "default"))

    @application.middleware("http")
    async def contain_unhandled_error(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        try:
            return await call_next(request)
        except Exception as exc:  # pylint: disable=broad-exception-caught  # noqa: BLE001
            logger.error(  # noqa: TRY400 - raw traceback text must not cross this log boundary.
                "Sync API request failed: %s", type(exc).__name__
            )
        envelope = ErrorEnvelope(
            error=ErrorDetail(
                code="service-unavailable",
                message="the Sync service is temporarily unavailable",
                status=503,
            )
        )
        return JSONResponse(status_code=503, content=_error_content(envelope))

    @application.get("/me", responses=ERROR_RESPONSES)
    def get_me(principal: Annotated[Principal, Depends(authenticate)]) -> PrincipalResource:
        """Return the caller's actor name, administrator flag and the approval setting; performs no other action."""
        return PrincipalResource(
            actor=principal.actor,
            administrator=principal.administrator,
            approval_required=approval_required(),
        )

    @application.post("/runs", status_code=202, response_model=RunResource, responses=ERROR_RESPONSES)
    async def create_run(
        body: CreateRunRequest,
        principal: Annotated[Principal, Depends(authenticate)],
        key: Annotated[str, Depends(idempotency_key)],
    ) -> JSONResponse:
        status, content = await service.create_run(body, principal, key)
        return JSONResponse(status_code=status, content=content)

    @application.get("/runs", responses=ERROR_RESPONSES)
    def list_runs(
        _principal: Annotated[Principal, Depends(authenticate)],
        config_id: Annotated[str | None, Query(pattern=CONFIG_ID_PATTERN)] = None,
        registry_version: Annotated[str | None, Query()] = None,
        limit: Annotated[str | None, Query()] = None,
        cursor: Annotated[str | None, Query()] = None,
    ) -> RunPage:
        """Return one page of runs, newest first, optionally for one configuration or version."""
        version = (
            None
            if registry_version is None
            else parse_bounded_integer(registry_version, minimum=1, maximum=MAX_REGISTRY_VERSION)
        )
        return service.list_runs(
            config_id=config_id, registry_version=version, cursor=cursor, limit=parse_page_limit(limit)
        )

    @application.get("/runs/{run_id}", responses=ERROR_RESPONSES)
    async def get_run(run_id: str, _principal: Annotated[Principal, Depends(authenticate)]) -> RunResource:
        observations = await reconciler.reconcile_run(run_id) if reconciler is not None else {}
        return await service.get_run(run_id, observations)

    @application.get("/runs/{run_id}/results", responses=ERROR_RESPONSES)
    def get_results(run_id: str, _principal: Annotated[Principal, Depends(authenticate)]) -> ResultsResource:
        return service.get_results(run_id)

    @application.get(
        "/runs/{run_id}/artifacts",
        responses=ERROR_RESPONSES,
    )
    def list_artifacts(run_id: str, _principal: Annotated[Principal, Depends(authenticate)]) -> ArtifactListResource:
        return service.list_artifacts(run_id)

    @application.get("/runs/{run_id}/artifacts/{artifact_id}", responses=ERROR_RESPONSES)
    def get_artifact(
        run_id: str, artifact_id: str, _principal: Annotated[Principal, Depends(authenticate)]
    ) -> Response:
        data, media_type, digest = service.get_artifact(run_id, artifact_id)
        return Response(content=data, media_type=media_type, headers={"Digest": f"sha-256={digest}"})

    @application.post("/runs/{run_id}/verify", status_code=202, response_model=RunResource, responses=ERROR_RESPONSES)
    async def verify_run(
        run_id: str,
        body: VerifyRunRequest,
        principal: Annotated[Principal, Depends(authenticate)],
        key: Annotated[str, Depends(idempotency_key)],
    ) -> JSONResponse:
        status, content = await service.verify_run(run_id, body, principal, key)
        return JSONResponse(status_code=status, content=content)

    @application.post("/runs/{run_id}/apply", status_code=202, response_model=RunResource, responses=ERROR_RESPONSES)
    async def apply_run(
        run_id: str,
        body: ApplyRunRequest,
        principal: Annotated[Principal, Depends(authenticate)],
        key: Annotated[str, Depends(idempotency_key)],
    ) -> JSONResponse:
        status, content = await service.apply_run(run_id, body, principal, key)
        return JSONResponse(status_code=status, content=content)

    @application.post("/runs/{run_id}/resume", status_code=202, response_model=RunResource, responses=ERROR_RESPONSES)
    async def resume_run(
        run_id: str,
        body: ResumeRunRequest,
        principal: Annotated[Principal, Depends(authenticate)],
        key: Annotated[str, Depends(idempotency_key)],
    ) -> JSONResponse:
        """Resume an interrupted write from the run's saved position, after a `verify` passed (spec 022)."""
        status, content = await service.resume_run(run_id, body, principal, key)
        return JSONResponse(status_code=status, content=content)

    @application.post(
        "/runs/{run_id}/cancel",
        status_code=202,
        response_model=RunResource,
        responses={
            # A run with nothing running whose plan finished is withdrawn in the request itself.
            200: {"model": RunResource, "description": "The finished plan was withdrawn; the run is cancelled."},
            **ERROR_RESPONSES,
        },
    )
    async def cancel_run(
        run_id: str,
        body: CancelRunRequest,
        principal: Annotated[Principal, Depends(authenticate)],
        key: Annotated[str, Depends(idempotency_key)],
    ) -> JSONResponse:
        status, content = await service.cancel_run(run_id, body, principal, key)
        return JSONResponse(status_code=status, content=content)

    _include_routers(application, service, authenticate, idempotency_key, configuration_routes)

    return application
