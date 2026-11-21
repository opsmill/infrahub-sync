"""FastAPI routing for the stable Sync HTTP contract."""

import logging
import os
from asyncio import CancelledError, create_task, sleep
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import asynccontextmanager, suppress
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, FastAPI, Header, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Receive, Scope, Send

from infrahub_sync.client.models import (
    DEFAULT_LOG_LIMIT,
    LOG_STAGES,
    MAX_LOG_LIMIT,
    MAX_REPORT_LIMIT,
    MIN_LIMIT,
    PlanReportPageResource,
)
from infrahub_sync.configuration.removed_credential_format import (
    CREDENTIAL_FORMAT_REMOVED_MESSAGE,
    CREDENTIAL_FORMAT_REMOVED_REASON,
)
from infrahub_sync.configuration.storage import UNSUPPORTED_STORE_MESSAGE, UNSUPPORTED_STORE_REASON
from infrahub_sync.execution import redaction_scope
from infrahub_sync.plan.models import PROBLEM_TYPES
from infrahub_sync.product_store import Account

from .account_routes import account_router
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
from .credential_actions import CredentialActions, destination_credential_values
from .credential_crypto import CredentialCipher
from .credential_routes import credential_router
from .events import ACCOUNT_REFUSED
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
from .presentation import AccountNameMiddleware, AccountReader
from .schedule_routes import schedule_router
from .schedules import ScheduleService
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


_HTTP_ERROR_CODES = {404: "not-found", 405: "method-not-allowed"}


async def _http_error(_request: Request, exc: StarletteHTTPException) -> JSONResponse:  # noqa: RUF029
    """Return the error envelope for a routing refusal such as an unknown path or a wrong method.

    The exception's headers are kept, so a 405 still names the accepted methods in ``Allow``.
    """
    envelope = ErrorEnvelope(
        error=ErrorDetail(
            code=_HTTP_ERROR_CODES.get(exc.status_code, "http-error"),
            message=str(exc.detail),
            status=exc.status_code,
        )
    )
    return JSONResponse(status_code=exc.status_code, content=_error_content(envelope), headers=exc.headers)


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

    The three lists are spec 007's (contracts/sync-api.md).
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
        """Return one page of the saved plan's problems (spec 007, contracts/sync-api.md)."""
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
        """Return one page of the records the saved plan leaves out (spec 007)."""
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
        """Return one page of the records the saved plan keeps without one optional relationship (spec 007)."""
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
    """Return the authenticated ``POST /runs/{run_id}/approve`` route (spec 014, contracts/http-api.md)."""
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
    """Return the authenticated ``GET /runs/{run_id}/logs`` route (spec 012, contracts/logs-route.md)."""
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
    """Return the authenticated ``GET /events`` route (spec 012, contracts/events-route.md)."""
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
    *,
    schedules: ScheduleService | None,
    credential_cipher: CredentialCipher | None,
    authenticate_worker: Callable[..., Principal],
) -> None:
    """Add the routes built outside ``create_app``.

    They are the configurations, the schedules, the approval, the events, the run logs, the saved
    plan, the accounts and the stored credentials.
    """
    if configuration_routes is not None:
        if isinstance(configuration_routes, ConfigurationRoutes):
            # `validate` reads the destination schema with the token of a stored credential,
            # decrypted in this process and returned nowhere (spec 027, FR-004).
            configuration_routes.read_stored_credentials_with(destination_credential_values(service, credential_cipher))
        application.include_router(configuration_router(configuration_routes, authenticate, idempotency_key))
    if schedules is not None:
        application.include_router(schedule_router(schedules, authenticate, idempotency_key))
    application.include_router(approve_router(service, authenticate, idempotency_key))
    application.include_router(events_router(service, authenticate))
    application.include_router(run_logs_router(service, authenticate))
    application.include_router(plan_router(service, authenticate))
    application.include_router(account_router(service, authenticate))
    application.include_router(
        credential_router(
            CredentialActions(service, credential_cipher), authenticate, idempotency_key, authenticate_worker
        )
    )


_BEARER_AUTH = HTTPBearer(auto_error=False, scheme_name="BearerAuth")
WORKER_ONLY_MESSAGE = "the worker account is accepted on the worker routes only"
WORKER_REQUIRED_MESSAGE = "this route accepts the worker account only"


def _authenticator(service: RunService, resolver: PrincipalResolver) -> Callable[..., Principal]:
    """Return the dependency that resolves the bearer value of a request to its principal.

    A missing or unknown value answers ``401 unauthenticated``. An API token after its expiry date
    answers ``401 token-expired``. The ``worker`` account answers ``403 forbidden``: every route
    that uses this dependency is outside the worker routes, which use ``require_worker`` instead
    (spec 027, research R4). Every refusal is recorded, without the value.
    """
    resolve = _principal_resolver(service, resolver)

    def authenticate(
        request: Request,
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_BEARER_AUTH)],
    ) -> Principal:
        principal = resolve(request, credentials)
        if principal.worker:
            _record_refused_authorization(service, principal, request)
            raise ServiceAPIError(403, "forbidden", WORKER_ONLY_MESSAGE)
        return principal

    return authenticate


def require_worker(service: RunService, resolver: PrincipalResolver) -> Callable[..., Principal]:
    """Return the dependency of the worker routes: it accepts the ``worker`` account and no other.

    An unknown, missing or expired value is refused as by ``authenticate``. Every other account,
    an administrator included, answers ``403 forbidden`` (spec 027, FR-005). Every refusal is
    recorded, without the value.
    """
    resolve = _principal_resolver(service, resolver)

    def authenticate_worker(
        request: Request,
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_BEARER_AUTH)],
    ) -> Principal:
        principal = resolve(request, credentials)
        if not principal.worker:
            _record_refused_authorization(service, principal, request)
            raise ServiceAPIError(403, "forbidden", WORKER_REQUIRED_MESSAGE)
        return principal

    return authenticate_worker


def _record_refused_authorization(service: RunService, principal: Principal, request: Request) -> None:
    """Record a known account refused on a route, as ``require_administrator`` does; no token value."""
    service.record_account_event(
        ACCOUNT_REFUSED,
        actor=principal.actor,
        account_id=principal.actor,
        operation=request.url.path,
        outcome="refused-authorization",
    )


def _principal_resolver(
    service: RunService, resolver: PrincipalResolver
) -> Callable[[Request, HTTPAuthorizationCredentials | None], Principal]:
    """Return the step that both dependencies share: the bearer value to its principal, or a ``401``."""

    def resolve(request: Request, credentials: HTTPAuthorizationCredentials | None) -> Principal:
        token = credentials.credentials.lstrip(" ") if credentials is not None else ""
        if not token:
            service.record_authentication_refusal(request.url.path, "missing-or-invalid-authorization")
            raise ServiceAPIError(401, "unauthenticated", "a valid bearer token is required")
        resolution = resolver.resolve(token)
        if resolution.principal is not None:
            return resolution.principal
        if resolution.refusal == "token-expired":
            service.record_authentication_refusal(request.url.path, "expired-bearer-token")
            raise ServiceAPIError(401, "token-expired", "the API token has expired")
        service.record_authentication_refusal(request.url.path, "invalid-bearer-token")
        raise ServiceAPIError(401, "unauthenticated", "a valid bearer token is required")

    return resolve


class RedactionScopeMiddleware:  # pylint: disable=too-few-public-methods
    """Bind one redaction registry for each request (spec 027, research R6).

    A stored value that the Sync API decrypts while it answers a request is added to this
    registry by `CredentialCipher.decrypt` before it is returned, so each message, answer and
    log line of the request is redacted with it. The registry ends with the request, so the
    value is in no list while another request is answered. The work that the application does
    outside a request (its start, the liveness loop) has no registry: a decryption there must
    bind its own `redaction_scope`.
    """

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Pass an HTTP request on inside a new redaction scope.

        Every other scope is passed on with no registry: the `lifespan` scope lasts until the
        process stops, so a registry bound for it would keep its values for that long.
        """
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        with redaction_scope():
            await self._app(scope, receive, send)


def _account_reader(service: RunService) -> AccountReader:
    """Return the account read of the presentation; the service is asked only when an answer names an account."""

    def read_accounts(account_ids: Sequence[str]) -> Mapping[str, Account]:
        return service.read_accounts(account_ids)

    return read_accounts


def _lifespan(reconciler: RunLivenessReconciler | None, schedules: ScheduleService | None) -> Any:
    """Return the lifespan of the application: the step of its start, then the liveness loop until it stops."""

    @asynccontextmanager
    async def lifespan(_application: FastAPI):
        task = None
        if schedules is not None:
            await schedules.start()
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

    return lifespan


# The refusals of a configuration route that have a fixed message of their own. Every other
# refusal answers one general message, and its `reason` names the cause.
_CONFIGURATION_REFUSAL_MESSAGES = {
    UNSUPPORTED_STORE_REASON: UNSUPPORTED_STORE_MESSAGE,
    CREDENTIAL_FORMAT_REMOVED_REASON: CREDENTIAL_FORMAT_REMOVED_MESSAGE,
}


def create_app(
    service: RunService,
    resolver: PrincipalResolver,
    configuration_routes: ConfigurationRoutes | None = None,
    reconciler: RunLivenessReconciler | None = None,
    *,
    schedules: ScheduleService | None = None,
    credential_cipher: CredentialCipher | None = None,
) -> FastAPI:
    """Create the service application from explicit providers.

    With ``schedules``, the application serves the schedule routes. When the API process starts,
    it stops the schedules that can no longer run and makes the Prefect schedules match the stored
    schedules, once (spec 021, research R6, R8 and R13).

    The stored credential routes are always served. Without ``credential_cipher``, which holds the
    key of ``INFRAHUB_SYNC_ENCRYPTION_KEY``, they list and show credentials and answer every change
    with ``503 credential-key-not-configured`` (spec 027). A Sync API process always has the key
    (``serve.build_process_service``); only an application built in a test has none.

    Raises:
        ValueError: A service setting such as ``INFRAHUB_SYNC_REQUIRE_APPROVAL`` holds a value that
            is not accepted, so the service does not start.
    """
    validate_settings()

    application = FastAPI(
        title="Infrahub Sync API", version=installed_server_version(), lifespan=_lifespan(reconciler, schedules)
    )
    authenticate = _authenticator(service, resolver)

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
                message=_CONFIGURATION_REFUSAL_MESSAGES.get(
                    exc.reason or "", "the configuration service refused the request"
                ),
                status=exc.status,
                family=exc.family,
                reason=exc.reason,
                details=exc.details,
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

    # TODO: Starlette types a handler's exception as `Exception`; drop the ignore once ty accepts a narrower one.
    application.add_exception_handler(StarletteHTTPException, _http_error)  # ty: ignore[invalid-argument-type]

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

    # Added before `contain_unhandled_error`, so it runs inside it: every JSON answer of a route or
    # of an exception handler passes through it, and a failed account read answers that 503.
    application.add_middleware(AccountNameMiddleware, read_accounts=_account_reader(service))

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
            # A principal of the product store carries its account name; the identifier in
            # `actor` is never shown.
            actor=principal.actor if principal.name is None else principal.name,
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
    def list_runs(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        _principal: Annotated[Principal, Depends(authenticate)],
        config_id: Annotated[str | None, Query(pattern=CONFIG_ID_PATTERN)] = None,
        registry_version: Annotated[str | None, Query()] = None,
        trigger_type: Annotated[str | None, Query()] = None,
        schedule_id: Annotated[str | None, Query()] = None,
        limit: Annotated[str | None, Query()] = None,
        cursor: Annotated[str | None, Query()] = None,
    ) -> RunPage:
        """Return one page of runs, newest first, optionally for one configuration, version, trigger or schedule."""
        version = (
            None
            if registry_version is None
            else parse_bounded_integer(registry_version, minimum=1, maximum=MAX_REGISTRY_VERSION)
        )
        return service.list_runs(
            config_id=config_id,
            registry_version=version,
            trigger_type=trigger_type,
            schedule_id=schedule_id,
            cursor=cursor,
            limit=parse_page_limit(limit),
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
        """Resume an interrupted write from the run's saved position, after a `verify` passed (spec 016)."""
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

    _include_routers(
        application,
        service,
        authenticate,
        idempotency_key,
        configuration_routes,
        schedules=schedules,
        credential_cipher=credential_cipher,
        authenticate_worker=require_worker(service, resolver),
    )
    # Added last, so it is the outermost middleware: the registry is bound before any route,
    # exception handler or other middleware of the request runs, and all of them share it.
    application.add_middleware(RedactionScopeMiddleware)

    return application
