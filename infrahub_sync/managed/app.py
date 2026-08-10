"""FastAPI routing for the stable managed Sync HTTP contract."""

import logging
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .auth import Principal, PrincipalResolver
from .models import (
    ApplyRunRequest,
    ArtifactListResource,
    CancelRunRequest,
    CreateRunRequest,
    ErrorDetail,
    ErrorEnvelope,
    PlanResource,
    ResultsResource,
    RunResource,
    VerifyRunRequest,
)
from .service import ManagedAPIError, ManagedRunService

logger = logging.getLogger(__name__)

ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    status: {"model": ErrorEnvelope} for status in (401, 403, 404, 409, 410, 422, 503)
}


def create_app(service: ManagedRunService, resolver: PrincipalResolver) -> FastAPI:
    """Create the managed application from explicit providers."""
    application = FastAPI(title="Infrahub Sync managed API", version="1.0.0")

    def authenticate(request: Request, authorization: Annotated[str | None, Header()] = None) -> Principal:
        if authorization is None or not authorization.startswith("Bearer "):
            service.record_authentication_refusal(request.url.path, "missing-or-invalid-authorization")
            raise ManagedAPIError(401, "unauthenticated", "a valid bearer token is required")
        token = authorization.removeprefix("Bearer ")
        principal = resolver.resolve(token)
        if principal is None:
            service.record_authentication_refusal(request.url.path, "invalid-bearer-token")
            raise ManagedAPIError(401, "unauthenticated", "a valid bearer token is required")
        return principal

    def idempotency_key(value: Annotated[str | None, Header(alias="Idempotency-Key")] = None) -> str:
        if value is None or not value.strip():
            raise ManagedAPIError(422, "idempotency-key-required", "a non-empty Idempotency-Key header is required")
        return value

    @application.exception_handler(ManagedAPIError)
    async def managed_error(_request: Request, exc: ManagedAPIError) -> JSONResponse:  # noqa: RUF029
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

    @application.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, _exc: RequestValidationError) -> JSONResponse:  # noqa: RUF029
        envelope = ErrorEnvelope(
            error=ErrorDetail(code="request-invalid", message="the request does not match the API schema", status=422)
        )
        return JSONResponse(status_code=422, content=envelope.model_dump(mode="json"))

    @application.exception_handler(Exception)
    async def unavailable_error(_request: Request, exc: Exception) -> JSONResponse:  # noqa: RUF029
        logger.error("managed API request failed: %s", type(exc).__name__)
        envelope = ErrorEnvelope(
            error=ErrorDetail(
                code="service-unavailable",
                message="the managed Sync service is temporarily unavailable",
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

    return application
