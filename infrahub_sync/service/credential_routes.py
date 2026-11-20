"""The routes of stored credentials for accounts (spec 027, contracts/http-api.md).

``credential_router`` builds them and ``app.py`` mounts them once:

- ``GET /credentials`` and ``GET /credentials/{name}``, for every signed-in account (FR-012);
- ``POST /credentials``, ``PUT /credentials/{name}/value`` and ``PATCH /credentials/{name}``, for
  a caller that holds ``manage-credentials`` (administrators, FR-015), each with ``Idempotency-Key``.

A route calls one method of ``credential_actions.CredentialActions``. A changing route checks the
permission in a dependency declared before the key and the body, so the refusal comes before the
request body is checked against its schema, as on the account routes. No answer holds a value.

The routes are synchronous: a store call and an encryption block, so each request runs in the
thread pool, which shares the redaction registry of the request.
"""

from collections.abc import Callable
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse

from infrahub_sync.client.models import DEFAULT_LIMIT, MAX_LIMIT, MIN_LIMIT

from .auth import Principal
from .credential_actions import (
    CREATE_OPERATION,
    DESCRIBE_OPERATION,
    MANAGE_CREDENTIALS,
    SET_VALUE_OPERATION,
    CreateCredentialRequest,
    CredentialActions,
    CredentialPage,
    CredentialResource,
    CredentialValueRequest,
    DescribeCredentialRequest,
)
from .models import ErrorEnvelope

_READ_ERRORS: dict[int | str, dict[str, Any]] = {status: {"model": ErrorEnvelope} for status in (401, 403, 404, 422)}
_CHANGE_ERRORS: dict[int | str, dict[str, Any]] = {
    status: {"model": ErrorEnvelope} for status in (401, 403, 404, 409, 422, 503)
}


def credential_router(
    actions: CredentialActions, authenticate: Callable[..., Principal], idempotency_key: Callable[..., str]
) -> APIRouter:
    """Return the routes of the stored credentials for accounts."""
    router = APIRouter()

    def manage(operation: str) -> Any:
        """Return the dependency that lets a holder of ``manage-credentials`` through, and records a refusal."""

        def require(principal: Annotated[Principal, Depends(authenticate)]) -> Principal:
            actions.require_permission(principal, MANAGE_CREDENTIALS, operation)
            return principal

        return Depends(require)

    @router.get("/credentials", responses=_READ_ERRORS)
    def list_credentials(
        _principal: Annotated[Principal, Depends(authenticate)],
        offset: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=MIN_LIMIT, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    ) -> CredentialPage:
        """Return one page of credentials sorted by name, with the names of their fields and no value."""
        return actions.list_credentials(offset, limit)

    @router.get("/credentials/{name}", responses=_READ_ERRORS)
    def read_credential(_principal: Annotated[Principal, Depends(authenticate)], name: str) -> CredentialResource:
        """Return one credential by its name, with the names of its fields and no value."""
        return actions.read_credential(name)

    @router.post("/credentials", status_code=201, response_model=CredentialResource, responses=_CHANGE_ERRORS)
    def create_credential(
        principal: Annotated[Principal, manage(CREATE_OPERATION)],
        key: Annotated[str, Depends(idempotency_key)],
        body: CreateCredentialRequest,
    ) -> JSONResponse:
        """Create a credential with its first value; the answer holds the names of its fields only."""
        status, content = actions.create(principal, body, key)
        return JSONResponse(status_code=status, content=content)

    @router.put("/credentials/{name}/value", response_model=CredentialResource, responses=_CHANGE_ERRORS)
    def set_credential_value(
        principal: Annotated[Principal, manage(SET_VALUE_OPERATION)],
        key: Annotated[str, Depends(idempotency_key)],
        name: str,
        body: CredentialValueRequest,
    ) -> JSONResponse:
        """Replace the whole set of fields of a credential; its revision goes up by one."""
        status, content = actions.set_value(principal, name, body, key)
        return JSONResponse(status_code=status, content=content)

    @router.patch("/credentials/{name}", response_model=CredentialResource, responses=_CHANGE_ERRORS)
    def describe_credential(
        principal: Annotated[Principal, manage(DESCRIBE_OPERATION)],
        key: Annotated[str, Depends(idempotency_key)],
        name: str,
        body: DescribeCredentialRequest,
    ) -> JSONResponse:
        """Replace the description of a credential; its revision stays."""
        status, content = actions.describe(principal, name, body, key)
        return JSONResponse(status_code=status, content=content)

    return router
