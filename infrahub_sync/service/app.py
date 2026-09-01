"""FastAPI routing for the stable Sync HTTP contract."""

import logging
import os
from asyncio import CancelledError, create_task, sleep
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .auth import Principal, PrincipalResolver
from .compatibility import API_STABILITY, API_VERSIONS, installed_server_version
from .config_routes import ConfigurationAPIError, ConfigurationRoutes, configuration_router
from .liveness import RunLivenessReconciler
from .models import (
    ApplyRunRequest,
    ArtifactListResource,
    CancelRunRequest,
    ConfigErrorDetail,
    ConfigErrorEnvelope,
    CreateRunRequest,
    ErrorDetail,
    ErrorEnvelope,
    PlanResource,
    ResultsResource,
    RunResource,
    ServiceStatusResource,
    VerifyRunRequest,
    VersionResource,
)
from .service import RunService, ServiceAPIError

logger = logging.getLogger(__name__)

ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    status: {"model": ErrorEnvelope} for status in (401, 403, 404, 409, 410, 422, 503)
}


def create_app(
    service: RunService,
    resolver: PrincipalResolver,
    configuration_routes: ConfigurationRoutes | None = None,
    reconciler: RunLivenessReconciler | None = None,
) -> FastAPI:
    """Create the service application from explicit providers."""

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

    application = FastAPI(title="Infrahub Sync Sync API", version=installed_server_version(), lifespan=lifespan)
    bearer_auth = HTTPBearer(auto_error=False, scheme_name="BearerAuth")

    def authenticate(
        request: Request,
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_auth)],
    ) -> Principal:
        if credentials is None:
            service.record_authentication_refusal(request.url.path, "missing-or-invalid-authorization")
            raise ServiceAPIError(401, "unauthenticated", "a valid bearer token is required")
        token = credentials.credentials.lstrip(" ")
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

    @application.exception_handler(ServiceAPIError)
    async def managed_error(_request: Request, exc: ServiceAPIError) -> JSONResponse:  # noqa: RUF029
        envelope = ErrorEnvelope(
            error=ErrorDetail(
                code=exc.code,
                message=exc.message,
                status=exc.status,
                run_id=exc.run_id,
                mutation_id=exc.mutation_id,
            )
        )
        headers = {"WWW-Authenticate": "Bearer"} if exc.status == 401 else None
        return JSONResponse(status_code=exc.status, content=envelope.model_dump(mode="json"), headers=headers)

    @application.exception_handler(ConfigurationAPIError)
    async def configuration_error(_request: Request, exc: ConfigurationAPIError) -> JSONResponse:  # noqa: RUF029
        envelope = ConfigErrorEnvelope(
            error=ConfigErrorDetail(
                code=f"configs-{exc.family}",
                message="the configuration service refused the request",
                status=exc.status,
                family=exc.family,
                reason=exc.reason,
            )
        )
        return JSONResponse(status_code=exc.status, content=envelope.model_dump(mode="json"))

    @application.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, _exc: RequestValidationError) -> JSONResponse:  # noqa: RUF029
        envelope = ErrorEnvelope(
            error=ErrorDetail(code="request-invalid", message="the request does not match the API schema", status=422)
        )
        return JSONResponse(status_code=422, content=envelope.model_dump(mode="json"))

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
        return JSONResponse(status_code=503, content=envelope.model_dump(mode="json"))

    @application.post("/runs", status_code=202, response_model=RunResource, responses=ERROR_RESPONSES)
    async def create_run(
        body: CreateRunRequest,
        principal: Annotated[Principal, Depends(authenticate)],
        key: Annotated[str, Depends(idempotency_key)],
    ) -> JSONResponse:
        status, content = await service.create_run(body, principal, key)
        return JSONResponse(status_code=status, content=content)

    @application.get("/runs/{run_id}", responses=ERROR_RESPONSES)
    async def get_run(run_id: str, _principal: Annotated[Principal, Depends(authenticate)]) -> RunResource:
        if reconciler is not None:
            await reconciler.reconcile_run(run_id)
        return await service.get_run(run_id)

    @application.get("/runs/{run_id}/plan", responses=ERROR_RESPONSES)
    def get_plan(run_id: str, _principal: Annotated[Principal, Depends(authenticate)]) -> PlanResource:
        return service.get_plan(run_id)

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

    @application.post("/runs/{run_id}/cancel", status_code=202, response_model=RunResource, responses=ERROR_RESPONSES)
    async def cancel_run(
        run_id: str,
        body: CancelRunRequest,
        principal: Annotated[Principal, Depends(authenticate)],
        key: Annotated[str, Depends(idempotency_key)],
    ) -> JSONResponse:
        status, content = await service.cancel_run(run_id, body, principal, key)
        return JSONResponse(status_code=status, content=content)

    if configuration_routes is not None:
        application.include_router(configuration_router(configuration_routes, authenticate, idempotency_key))

    return application
