"""The routes of the local accounts of the Sync API: sign-in and sign-out.

``account_router`` builds every route of this module, and ``app.py`` mounts it once. A route
reads the product store, the clock and the audit record through the ``RunService`` it is given,
when a request arrives and never when the router is built.

The functions that a later account route needs are public and take the service first:

- ``sign_in_refused_until``, ``record_failed_sign_in`` and ``clear_failed_sign_ins`` count the
  failed sign-ins of one name (research R6);
- ``verify_account_password`` checks a password through the gate that limits the checks running
  at the same time (research R2);
- ``account_resource`` builds what an answer shows of an account.

No function of this module puts a password, a session token, a digest or a hash in a log line, an
exception or an audit record. The session token is in one place: the answer of a sign-in.
"""

from collections.abc import Callable
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict

from infrahub_sync.product_store import Account
from infrahub_sync.product_store.accounts import SIGN_IN_NAME_MAX_LENGTH, name_key

from . import credentials
from .auth import Principal
from .events import ACCOUNT_SIGNED_IN, ACCOUNT_SIGNED_OUT
from .models import ErrorEnvelope
from .service import RunService, ServiceAPIError
from .settings import session_idle_seconds

# The longest password that is checked. A longer one is refused before any hash is computed, so
# the size of a request cannot set the cost of a password check.
PASSWORD_MAX_LENGTH = 1024
# One message for a wrong name, a wrong password, a disabled account and a name that is refused
# after repeated failures, so that an answer does not show which of them happened (FR-003).
SIGN_IN_REFUSED_MESSAGE = "the account name or the password was not accepted"
SIGN_IN_BUSY_MESSAGE = "too many sign-ins are in progress; try again in a few seconds"
NOT_A_SESSION_MESSAGE = "this request presents an API token; only a session can be signed out"

_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {status: {"model": ErrorEnvelope} for status in (401, 422, 503)}


class SignInRequest(BaseModel):
    """The name and the password of a sign-in.

    Neither field has a length rule here: a value that is too long or empty gets the answer of a
    wrong password, not the answer of a request that does not match the schema. The password is
    left out of the printed form of the request.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    name: str
    password: str

    def __repr__(self) -> str:
        """Show no field: the name is what a caller typed and the password is a secret."""
        return "SignInRequest()"

    __str__ = __repr__


class AccountResource(BaseModel):
    """What an answer shows of an account: never its identifier and never its password hash."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    administrator: bool
    disabled: bool
    environment_managed: bool
    created_at: datetime
    sign_in_refused_until: datetime | None = None


class SessionResource(BaseModel):
    """The answer of a sign-in: the session token, the account and the idle period of a session.

    The token is returned once. The store keeps its digest only.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    token: str
    account: AccountResource
    idle_seconds: int

    def __repr__(self) -> str:
        """Show the account name only, so that no log line or traceback holds the session token."""
        return f"SessionResource(account={self.account.name!r})"

    __str__ = __repr__


def sign_in_refused() -> ServiceAPIError:
    """Return the one refusal of a sign-in, whatever its cause."""
    return ServiceAPIError(401, "sign-in-refused", SIGN_IN_REFUSED_MESSAGE)


def is_countable_name(name: str) -> bool:
    """Return whether failed sign-ins are counted for this name: 1 to 64 characters, none of them NUL."""
    # The lower-case form of a name can be longer than the name, and it is the stored key.
    # PostgreSQL stores no NUL character in a text, so a name that holds one is never read there.
    return (
        0 < len(name) <= SIGN_IN_NAME_MAX_LENGTH
        and len(name_key(name)) <= SIGN_IN_NAME_MAX_LENGTH
        and "\x00" not in name
    )


def sign_in_refused_until(service: RunService, name: str) -> datetime | None:
    """Return the time until which a sign-in for this name is refused, or nothing when it is not."""
    attempt = service.accounts.projection.read_sign_in_attempt(name)
    if attempt is None or attempt.refused_until is None or attempt.refused_until <= service.accounts.now():
        return None
    return attempt.refused_until


def record_failed_sign_in(service: RunService, name: str) -> None:
    """Count one failed sign-in, or one wrong current password, for this name.

    The fifth failure in 15 minutes refuses the name for 15 minutes. A failure for a name that is
    refused is not counted.
    """
    service.accounts.projection.record_sign_in_failure(name, now=service.accounts.now())


def clear_failed_sign_ins(service: RunService, name: str) -> bool:
    """Forget the failed sign-ins of this name, which ends a refusal. Return whether there were any."""
    return service.accounts.projection.clear_sign_in_attempts(name)


def verify_account_password(account: Account | None, password: str) -> bool:
    """Check a password against the hash of an account, with one hash computation in every case.

    A name with no account and an account with no password are checked against a fixed hash, so
    the duration of the check does not show whether the account exists. The check holds one place
    of the gate of the process.

    Raises:
        ServiceAPIError: ``503 sign-in-busy`` when no place of the gate became free in time.
    """
    stored_hash = None if account is None else account.password_hash
    try:
        verified = credentials.PASSWORD_VERIFICATION_GATE.verify(
            password, credentials.no_account_hash() if stored_hash is None else stored_hash
        )
    except credentials.SignInBusyError:
        raise ServiceAPIError(503, "sign-in-busy", SIGN_IN_BUSY_MESSAGE) from None
    return verified and stored_hash is not None


def account_resource(account: Account, *, refused_until: datetime | None = None) -> AccountResource:
    """Return what an answer shows of an account; ``refused_until`` is a refusal that is in force.

    Raises:
        ValueError: the account is deleted, so it has no name to show.
    """
    if account.name is None:
        msg = "a deleted account has no resource"
        raise ValueError(msg)
    return AccountResource(
        name=account.name,
        administrator=account.administrator,
        disabled=account.disabled,
        environment_managed=account.environment_managed,
        created_at=account.created_at,
        sign_in_refused_until=refused_until,
    )


def _sign_in(service: RunService, body: SignInRequest, path: str) -> SessionResource:
    """Open a session for the account of a name and a password, or refuse with one answer."""
    name = body.name
    if not is_countable_name(name) or not 0 < len(body.password) <= PASSWORD_MAX_LENGTH:
        # No account can have this name or this password. Nothing is read, checked or written.
        raise sign_in_refused()
    if sign_in_refused_until(service, name) is not None:
        # Before the password check: the attempt is not counted and the refusal does not move.
        service.record_authentication_refusal(path, "sign-in-refused")
        raise sign_in_refused()
    account = service.accounts.projection.lookup_account_by_name(name)
    verified = verify_account_password(account, body.password)
    if account is None or not verified or account.disabled or account.deleted_at is not None:
        record_failed_sign_in(service, name)
        # The record holds one reason for every cause, and no name.
        service.record_authentication_refusal(path, "sign-in-refused")
        raise sign_in_refused()
    clear_failed_sign_ins(service, name)
    token = credentials.new_session_token()
    service.accounts.projection.create_account_session(
        account.account_id, digest=credentials.digest(token), now=service.accounts.now()
    )
    service.record_account_event(
        ACCOUNT_SIGNED_IN, actor=account.account_id, account_id=account.account_id, operation="sign-in"
    )
    return SessionResource(token=token, account=account_resource(account), idle_seconds=session_idle_seconds())


def _sign_out(service: RunService, principal: Principal) -> None:
    """End the session that the request presented."""
    if principal.kind != "session" or principal.session_id is None:
        raise ServiceAPIError(422, "not-a-session", NOT_A_SESSION_MESSAGE)
    service.accounts.projection.delete_account_session(principal.session_id)
    service.record_account_event(
        ACCOUNT_SIGNED_OUT, actor=principal.actor, account_id=principal.actor, operation="sign-out"
    )


def account_router(service: RunService, authenticate: Callable[..., Principal]) -> APIRouter:
    """Return the routes of the local accounts (spec 022, contracts/http-api.md).

    The routes are synchronous: a password check and a store call block, so each request runs in
    the thread pool.
    """
    router = APIRouter()

    @router.post("/auth/sign-in", responses=_ERROR_RESPONSES)
    def sign_in(body: SignInRequest, request: Request) -> SessionResource:
        """Open a session with an account name and its password; the answer holds the session token once."""
        return _sign_in(service, body, request.url.path)

    @router.post("/auth/sign-out", status_code=204, responses=_ERROR_RESPONSES)
    def sign_out(principal: Annotated[Principal, Depends(authenticate)]) -> None:
        """End the session of the request; its session token is refused afterwards."""
        _sign_out(service, principal)

    return router
