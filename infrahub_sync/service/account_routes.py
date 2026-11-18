"""The routes of the local accounts of the Sync API (spec 022, contracts/http-api.md).

``account_router`` builds every route of this module, and ``app.py`` mounts it once:

- ``POST /auth/sign-in`` and ``POST /auth/sign-out``;
- ``/accounts``, ``/accounts/{name}`` and ``/accounts/{name}/tokens``, for an administrator;
- ``/me/password`` and ``/me/tokens``, for the account of the caller.

A route calls one function of ``account_actions``, which holds the requests, the answers and the
actions. A route reads the product store, the clock and the audit record through the
``RunService`` it is given, when a request arrives and never when the router is built.

An ``/accounts`` route checks that the caller is an administrator in a dependency, so the refusal
comes before the request body is read.
"""

from collections.abc import Callable
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import AfterValidator

from infrahub_sync.client.models import REASON_MAX_LENGTH

from . import account_actions as actions
from .account_actions import (
    PASSWORD_MAX_LENGTH,
    AccountResource,
    AccountState,
    ChangePasswordRequest,
    CreateAccountRequest,
    CreatedTokenResource,
    CreateTokenRequest,
    DeletedAccountResource,
    SessionResource,
    SetPasswordRequest,
    SignInRequest,
    TokenResource,
    UnlockRequest,
    UpdateAccountRequest,
    account_resource,
    clear_failed_sign_ins,
    is_countable_name,
    record_failed_sign_in,
    sign_in_refused,
    sign_in_refused_until,
    verify_account_password,
)
from .auth import Principal
from .models import ErrorEnvelope
from .service import RunService

__all__ = [
    "PASSWORD_MAX_LENGTH",
    "AccountResource",
    "CreatedTokenResource",
    "SessionResource",
    "SignInRequest",
    "TokenResource",
    "account_resource",
    "account_router",
    "clear_failed_sign_ins",
    "is_countable_name",
    "record_failed_sign_in",
    "sign_in_refused",
    "sign_in_refused_until",
    "verify_account_password",
]


def _errors(*statuses: int) -> dict[int | str, dict[str, Any]]:
    """Return the error answers of a route for the OpenAPI document."""
    return {status: {"model": ErrorEnvelope} for status in statuses}


_SIGN_IN_ERRORS = _errors(401, 422, 503)
_ACCOUNT_ERRORS = _errors(401, 403, 404, 409, 422, 503)
_OWN_ERRORS = _errors(401, 403, 404, 409, 422, 503)

_Authenticate = Callable[..., Principal]
_ReasonQuery = Annotated[
    str | None, Query(min_length=1, max_length=REASON_MAX_LENGTH), AfterValidator(actions.printable_reason)
]


def _sign_in_router(service: RunService, authenticate: _Authenticate) -> APIRouter:
    """Return the routes that open and end a session."""
    router = APIRouter()

    @router.post("/auth/sign-in", responses=_SIGN_IN_ERRORS)
    def sign_in(body: SignInRequest, request: Request) -> SessionResource:
        """Open a session with an account name and its password; the answer holds the session token once."""
        return actions.sign_in(service, body, request.url.path)

    @router.post("/auth/sign-out", status_code=204, responses=_SIGN_IN_ERRORS)
    def sign_out(principal: Annotated[Principal, Depends(authenticate)]) -> None:
        """End the session of the request; its session token is refused afterwards."""
        actions.sign_out(service, principal)

    return router


def _administrator_router(service: RunService, authenticate: _Authenticate) -> APIRouter:
    """Return the routes of an administrator on the accounts.

    Every route answers ``403 forbidden`` to a caller that is not an administrator, before any
    other check of the request.
    """
    router = APIRouter()

    def administrator(operation: str) -> Any:
        """Return the dependency that lets an administrator through and records a refusal under this operation."""

        def require(principal: Annotated[Principal, Depends(authenticate)]) -> Principal:
            actions.require_administrator(service, principal, operation)
            return principal

        return Depends(require)

    @router.get("/accounts", responses=_ACCOUNT_ERRORS)
    def list_accounts(
        _principal: Annotated[Principal, administrator("list-accounts")], state: AccountState = "all"
    ) -> list[AccountResource]:
        """Return the accounts that are not deleted, by name; one state only when ``state`` names it."""
        return actions.list_accounts(service, state)

    @router.post("/accounts", status_code=201, responses=_ACCOUNT_ERRORS)
    def create_account(
        principal: Annotated[Principal, administrator("create-account")], body: CreateAccountRequest
    ) -> AccountResource:
        """Create an account with its first password."""
        return actions.create_account(service, principal, body)

    @router.get("/accounts/{name}", responses=_ACCOUNT_ERRORS)
    def read_account(_principal: Annotated[Principal, administrator("read-account")], name: str) -> AccountResource:
        """Return one account by its name, in any letter case."""
        return actions.read_account(service, name)

    @router.patch("/accounts/{name}", responses=_ACCOUNT_ERRORS)
    def update_account(
        principal: Annotated[Principal, administrator("update-account")], name: str, body: UpdateAccountRequest
    ) -> AccountResource:
        """Disable an account, which deletes its tokens and its sessions, enable it again, or set its level."""
        return actions.update_account(service, principal, name, body)

    @router.put("/accounts/{name}/password", status_code=204, responses=_ACCOUNT_ERRORS)
    def set_account_password(
        principal: Annotated[Principal, administrator("set-account-password")], name: str, body: SetPasswordRequest
    ) -> None:
        """Replace the password of an account; every session of the account ends."""
        actions.set_account_password(service, principal, name, body)

    @router.post("/accounts/{name}/unlock", status_code=204, responses=_ACCOUNT_ERRORS)
    def unlock_account(
        principal: Annotated[Principal, administrator("unlock-account")], name: str, body: UnlockRequest | None = None
    ) -> None:
        """End the refusal of an account's sign-ins that follows repeated failures."""
        actions.unlock_account(service, principal, name, None if body is None else body.reason)

    @router.delete("/accounts/{name}", responses=_ACCOUNT_ERRORS)
    def delete_account(
        principal: Annotated[Principal, administrator("delete-account")], name: str, reason: _ReasonQuery = None
    ) -> DeletedAccountResource:
        """Delete an account; its records display the placeholder of the answer. It cannot be reversed."""
        return actions.delete_account(service, principal, name, reason)

    @router.get("/accounts/{name}/tokens", responses=_ACCOUNT_ERRORS)
    def list_account_tokens(
        _principal: Annotated[Principal, administrator("list-account-tokens")], name: str
    ) -> list[TokenResource]:
        """Return the API tokens of an account, without any value."""
        return actions.list_account_tokens(service, name)

    @router.delete("/accounts/{name}/tokens/{token_id}", status_code=204, responses=_ACCOUNT_ERRORS)
    def delete_account_token(
        principal: Annotated[Principal, administrator("delete-account-token")],
        name: str,
        token_id: str,
        reason: _ReasonQuery = None,
    ) -> None:
        """Delete one API token of an account; its value is refused afterwards."""
        actions.delete_account_token(service, principal, name, token_id, reason)

    return router


def _own_account_router(service: RunService, authenticate: _Authenticate) -> APIRouter:
    """Return the routes of the caller on its own account: its password and its API tokens."""
    router = APIRouter()

    @router.put("/me/password", status_code=204, responses=_OWN_ERRORS)
    def change_own_password(
        principal: Annotated[Principal, Depends(authenticate)], body: ChangePasswordRequest
    ) -> None:
        """Replace the caller's password; the other sessions of the account end and its tokens stay."""
        actions.change_own_password(service, principal, body)

    @router.get("/me/tokens", responses=_OWN_ERRORS)
    def list_own_tokens(principal: Annotated[Principal, Depends(authenticate)]) -> list[TokenResource]:
        """Return the API tokens of the caller's account, without any value."""
        return actions.list_own_tokens(service, principal)

    @router.post("/me/tokens", status_code=201, responses=_OWN_ERRORS)
    def create_own_token(
        principal: Annotated[Principal, Depends(authenticate)], body: CreateTokenRequest
    ) -> CreatedTokenResource:
        """Create an API token for the caller's account; the answer holds its value this once."""
        return actions.create_own_token(service, principal, body)

    @router.delete("/me/tokens/{token_id}", status_code=204, responses=_OWN_ERRORS)
    def delete_own_token(
        principal: Annotated[Principal, Depends(authenticate)], token_id: str, reason: _ReasonQuery = None
    ) -> None:
        """Delete one API token of the caller's account; its value is refused afterwards."""
        actions.delete_own_token(service, principal, token_id, reason)

    return router


def account_router(service: RunService, authenticate: _Authenticate) -> APIRouter:
    """Return the routes of the local accounts (spec 022, contracts/http-api.md).

    The routes are synchronous: a password check and a store call block, so each request runs in
    the thread pool.
    """
    router = APIRouter()
    router.include_router(_sign_in_router(service, authenticate))
    router.include_router(_administrator_router(service, authenticate))
    router.include_router(_own_account_router(service, authenticate))
    return router
