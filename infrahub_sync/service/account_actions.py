# pylint: disable=too-many-lines  # every account action and its refusals, for admin, worker and other accounts
"""What the account routes of the Sync API do: the requests, the answers and the actions.

``account_routes`` declares the routes and calls one function of this module for each. A function
reads the product store, the clock and the free-text check through the ``RunService`` it is given.

- ``sign_in`` and ``sign_out`` open and end a session.
- ``create_account``, ``list_accounts``, ``read_account``, ``update_account`` (state and level),
  ``set_account_password``, ``unlock_account``, ``delete_account``, ``list_account_tokens`` and
  ``delete_account_token`` are the actions of an administrator on an account.
- ``change_own_password``, ``list_own_tokens``, ``create_own_token`` and ``delete_own_token`` are the
  actions of an account on itself.

Each accepted change writes one audit event that names the account acted on by its identifier.
The store writes the event in the transaction of the change, so a failure of either writes
neither. ``update_account`` writes a change of state and a change of level in two transactions.
The events of refusals, which change nothing else, are written on their own.

No function of this module puts a password, a token value, a session token, a digest or a hash in
a log line, an exception or an audit record. A session token is in one place, the answer of a
sign-in, and the value of an API token is in one place, the answer of its creation.
"""

from __future__ import annotations

import re
from contextlib import suppress
from datetime import datetime  # noqa: TC003 - Pydantic reads the annotation of a field when the class is built.
from typing import TYPE_CHECKING, Annotated, Literal

import structlog
from pydantic import AfterValidator, AwareDatetime, BaseModel, ConfigDict, Field, StrictBool, field_validator

from infrahub_sync.client.models import ACCOUNT_NAME_PATTERN, REASON_MAX_LENGTH
from infrahub_sync.product_store.accounts import (
    ACCOUNT_ID_PREFIX,
    SIGN_IN_NAME_MAX_LENGTH,
    AccountNameTakenError,
    AccountNotActiveError,
    AccountTokenNameTakenError,
    SignInAttemptsFullError,
    name_key,
)

from . import credentials
from .auth import is_worker_account
from .events import (
    ACCOUNT_CREATED,
    ACCOUNT_DELETED,
    ACCOUNT_DISABLED,
    ACCOUNT_ENABLED,
    ACCOUNT_LEVEL_SET,
    ACCOUNT_PASSWORD_CHANGED,
    ACCOUNT_PASSWORD_SET,
    ACCOUNT_REFUSED,
    ACCOUNT_SIGNED_IN,
    ACCOUNT_SIGNED_OUT,
    ACCOUNT_TOKEN_CREATED,
    ACCOUNT_TOKEN_DELETED,
    ACCOUNT_UNLOCKED,
    account_event,
)
from .presentation import deleted_account_placeholder
from .service import REASON_REFUSED_MESSAGE, RunService, ServiceAPIError
from .settings import (
    ADMIN_ACCOUNT_PASSWORD_ENV,
    ADMIN_ACCOUNT_TOKEN_ENV,
    PASSWORD_MAX_LENGTH,
    PASSWORD_MIN_LENGTH,
    WORKER_TOKEN_ENV,
    session_idle_seconds,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from infrahub_sync.product_store import Account, AccountToken, AuditEvent

    from .auth import Principal

TOKEN_NAME_MAX_LENGTH = 64
# The names no account can be created with, in any letter case: the accounts that the environment
# manages, and the values a record holds when no account applies.
RESERVED_ACCOUNT_NAMES = frozenset({"admin", "worker", "unknown", "unauthenticated"})
# One message for a wrong name, a wrong password, a disabled account and a name that is refused
# after repeated failures, so that an answer does not show which of them happened (FR-003).
SIGN_IN_REFUSED_MESSAGE = "the account name or the password was not accepted"
SIGN_IN_BUSY_MESSAGE = "too many sign-ins are in progress; try again in a few seconds"
NOT_A_SESSION_MESSAGE = "this request presents an API token; only a session can be signed out"
ADMINISTRATOR_REQUIRED_MESSAGE = "administrator access is required"
ACCOUNT_NOT_FOUND_MESSAGE = "no account has this name"
ACCOUNT_NAME_INVALID_MESSAGE = (
    "an account name has 1 to 64 characters among letters, digits, '.', '_', '@' and '-', starts with a "
    "letter or a digit, and is not a name that the product reserves"
)
ACCOUNT_NAME_TAKEN_MESSAGE = "an account already has this name"
PASSWORD_TOO_SHORT_MESSAGE = f"a password has at least {PASSWORD_MIN_LENGTH} characters"
PASSWORD_TOO_LONG_MESSAGE = f"a password has at most {PASSWORD_MAX_LENGTH} characters"
PASSWORD_INVALID_MESSAGE = "a password holds only characters that UTF-8 can encode"
# The event of the log line that tells the operator that a stored password hash cannot be read.
PASSWORD_HASH_UNREADABLE_EVENT = "account_password_hash_unreadable"
# One message for a wrong current password and for a name that is refused after repeated failures.
CURRENT_PASSWORD_REFUSED_MESSAGE = "the current password was not accepted"
TOKEN_NAME_TAKEN_MESSAGE = "the account already has a token with this name"
TOKEN_NOT_FOUND_MESSAGE = "the account has no token with this identifier"
TOKEN_NAME_REFUSED_MESSAGE = "name must not contain a secret or a token"
EXPIRY_IN_THE_PAST_MESSAGE = "expires_at must be a time in the future"
NO_CHANGE_MESSAGE = "the request names no change; give 'disabled', 'administrator' or both"
_REASON_MESSAGE = "reason must be printable and trimmed"
_TOKEN_NAME_MESSAGE = "a token name must be printable and trimmed"
_ENVIRONMENT_MANAGED_MESSAGE = (
    "the environment of the Sync API manages this account; change {variable} and restart the Sync API"
)
_ADMIN_STATE_MESSAGE = (
    "admin is always an active administrator that the environment defines; it cannot be disabled, deleted "
    "or have its level changed"
)
_WORKER_MESSAGE = (
    "worker is the account of the workers that the environment defines; it has no password and cannot be "
    "changed; change {variable} and restart the Sync API to replace its token"
)

_ACCOUNT_NAME = re.compile(ACCOUNT_NAME_PATTERN)

logger = structlog.get_logger(__name__)

AccountState = Literal["active", "disabled", "all"]


def printable_reason(value: str | None) -> str | None:
    """Refuse a reason that is not printable and trimmed, as a configuration request does.

    PostgreSQL stores no NUL character in a text: such a reason would fail the write of the event
    after the action is done.
    """
    if value is not None and (value != value.strip() or not value.isprintable()):
        raise ValueError(_REASON_MESSAGE)
    return value


Reason = Annotated[str | None, Field(min_length=1, max_length=REASON_MAX_LENGTH), AfterValidator(printable_reason)]


class _SecretRequest(BaseModel):
    """A request that can hold a password: no field is trimmed, echoed in an error or printed."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    def __repr__(self) -> str:
        """Show no field: a field is what a caller typed or a secret."""
        return f"{type(self).__name__}()"

    __str__ = __repr__


class SignInRequest(_SecretRequest):
    """The name and the password of a sign-in.

    Neither field has a length rule here: a value that is too long or empty gets the answer of a
    wrong password, not the answer of a request that does not match the schema.
    """

    name: str
    password: str


class CreateAccountRequest(_SecretRequest):
    """The name, the first password and the level of a new account.

    The name and the password have no rule here: each rule has its own refusal code.
    """

    name: str
    password: str
    administrator: StrictBool = False
    reason: Reason = None


class UpdateAccountRequest(BaseModel):
    """The change of the state of an account, of its level, or of both.

    ``disabled`` disables the account or enables it again. ``administrator`` sets its level. A
    field left out keeps its value.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    disabled: StrictBool | None = None
    administrator: StrictBool | None = None
    reason: Reason = None


class SetPasswordRequest(_SecretRequest):
    """The new password that an administrator gives an account."""

    new_password: str
    reason: Reason = None


class ChangePasswordRequest(_SecretRequest):
    """The current password of the caller and the password that replaces it."""

    current_password: str
    new_password: str
    reason: Reason = None


class UnlockRequest(BaseModel):
    """The optional reason for ending the refusal of an account's sign-ins."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    reason: Reason = None


class CreateTokenRequest(BaseModel):
    """The name of a new API token and, when it has one, its expiry date with a time zone."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1, max_length=TOKEN_NAME_MAX_LENGTH)
    expires_at: AwareDatetime | None = None
    reason: Reason = None

    @field_validator("name")
    @classmethod
    def _require_printable_name(cls, value: str) -> str:
        if value != value.strip() or not value.isprintable():
            raise ValueError(_TOKEN_NAME_MESSAGE)
        return value


class AccountResource(BaseModel):
    """What an answer shows of an account: never its identifier and never its password hash."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    administrator: bool
    disabled: bool
    environment_managed: bool
    created_at: datetime
    sign_in_refused_until: datetime | None = None


class DeletedAccountResource(BaseModel):
    """The answer of a deletion: what every record of the account displays from now on."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    placeholder: str


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


class TokenResource(BaseModel):
    """What an answer shows of an API token: never its value and never its digest."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    token_id: str
    name: str
    created_at: datetime
    expires_at: datetime | None = None
    last_used_at: datetime | None = None


class CreatedTokenResource(TokenResource):
    """The answer of the creation of an API token: the token and its value, returned this once."""

    token: str

    def __repr__(self) -> str:
        """Show the token name only, so that no log line or traceback holds the value."""
        return f"CreatedTokenResource(name={self.name!r})"

    __str__ = __repr__


def sign_in_refused() -> ServiceAPIError:
    """Return the one refusal of a sign-in, whatever its cause."""
    return ServiceAPIError(401, "sign-in-refused", SIGN_IN_REFUSED_MESSAGE)


def _event(
    service: RunService,
    event_type: str,
    *,
    actor: str,
    account_id: str,
    operation: str,
    reason: str | None = None,
) -> AuditEvent:
    """Return the event of an accepted account action, which the store writes with the action."""
    return account_event(
        event_type,
        actor=actor,
        account_id=account_id,
        operation=operation,
        reason=reason,
        created_at=service.accounts.now(),
    )


def is_countable_name(name: str) -> bool:
    """Return whether failed sign-ins are counted for this name: 1 to 64 characters that can be stored.

    No character is NUL, and UTF-8 can encode each one.
    """
    # The lower-case form of a name can be longer than the name, and it is the stored key.
    # PostgreSQL stores no NUL character in a text, so a name that holds one is never read there.
    return (
        0 < len(name) <= SIGN_IN_NAME_MAX_LENGTH
        and len(name_key(name)) <= SIGN_IN_NAME_MAX_LENGTH
        and "\x00" not in name
        and credentials.is_encodable(name)
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
    refused is not counted. A new name is not recorded when the table is full of refusals in
    force: the caller answers the same refusal.
    """
    with suppress(SignInAttemptsFullError):
        service.accounts.projection.record_sign_in_failure(name, now=service.accounts.now())


def clear_failed_sign_ins(service: RunService, name: str) -> bool:
    """Forget the failed sign-ins of this name, which ends a refusal. Return whether there were any."""
    return service.accounts.projection.clear_sign_in_attempts(name)


def verify_account_password(account: Account | None, password: str, *, admit: Callable[[], bool] | None = None) -> bool:
    """Check a password against the hash of an account, with one hash computation in every case.

    A name with no account and an account with no password are checked against a fixed hash, so
    the duration of the check does not show whether the account exists. The check holds one place
    of the gate of the process. ``admit`` is called once the place is held, immediately before the
    check: see ``VerificationGate.verify``.

    A stored hash that cannot be read verifies no password, as a wrong password does, and is
    logged with the identifier of the account, never the hash.

    Raises:
        ServiceAPIError: ``503 sign-in-busy`` when no place of the gate became free in time.
        credentials.VerificationNotAdmittedError: ``admit`` returned false; nothing was checked.
    """
    stored_hash = None if account is None else account.password_hash
    gate = credentials.PASSWORD_VERIFICATION_GATE
    checked_hash = credentials.no_account_hash() if stored_hash is None else stored_hash
    try:
        verified = gate.verify(password, checked_hash, admit=admit)
    except credentials.SignInBusyError:
        raise ServiceAPIError(503, "sign-in-busy", SIGN_IN_BUSY_MESSAGE) from None
    if (
        not verified
        and account is not None
        and stored_hash is not None
        and not credentials.is_readable_hash(stored_hash)
    ):
        # The answer and the count stay those of a wrong password; the log line holds no hash.
        logger.warning(PASSWORD_HASH_UNREADABLE_EVENT, account_id=account.account_id)
    return verified and stored_hash is not None


def _check_password_of_name(service: RunService, name: str, account: Account | None, password: str) -> bool | None:
    """Check a password for a name while no other check of the name runs in the process.

    The refusal of the name is read again once a place of the gate is held, immediately before
    the check, and a failure is counted before the next check of the name starts. Return nothing
    when the name became refused while the request waited: no password was checked and nothing
    was counted. Otherwise return whether the password verified, after counting a failure.

    Raises:
        ServiceAPIError: ``503 sign-in-busy``.
    """
    try:
        with credentials.one_check_per_name(name_key(name)):
            try:
                verified = verify_account_password(
                    account, password, admit=lambda: sign_in_refused_until(service, name) is None
                )
            except credentials.VerificationNotAdmittedError:
                return None
            if not verified or account is None or account.disabled or account.deleted_at is not None:
                record_failed_sign_in(service, name)
                return False
            return True
    except credentials.SignInBusyError:
        # The check of another request for the name did not end within the wait of the gate.
        raise ServiceAPIError(503, "sign-in-busy", SIGN_IN_BUSY_MESSAGE) from None


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


def token_resource(token: AccountToken) -> TokenResource:
    """Return what an answer shows of an API token."""
    return TokenResource(
        token_id=token.token_id,
        name=token.name,
        created_at=token.created_at,
        expires_at=token.expires_at,
        last_used_at=token.last_used_at,
    )


def sign_in(service: RunService, body: SignInRequest, path: str) -> SessionResource:
    """Open a session for the account of a name and a password, or refuse with one answer."""
    name = body.name
    if not is_countable_name(name) or not _is_checkable_password(body.password):
        # No account can have this name or this password. Nothing is read, checked or written.
        raise sign_in_refused()
    if sign_in_refused_until(service, name) is not None:
        # Before the password check: the attempt is not counted and the refusal does not move.
        service.record_authentication_refusal(path, "sign-in-refused")
        raise sign_in_refused()
    account = service.accounts.projection.lookup_account_by_name(name)
    # The refusal is read again once the check can start, and is answered in the same way.
    checked = _check_password_of_name(service, name, account, body.password)
    # The `worker` account is refused after the check, so its refusal costs one hash computation
    # as every other refusal does; it has no password, and this holds even if one were stored.
    if not checked or account is None or is_worker_account(account):
        # The record holds one reason for every cause, and no name.
        service.record_authentication_refusal(path, "sign-in-refused")
        raise sign_in_refused()
    token = credentials.new_session_token()
    try:
        # One transaction: inserted only while the account is active with the checked password.
        service.accounts.projection.create_account_session(
            account.account_id,
            digest=credentials.digest(token),
            now=service.accounts.now(),
            signed_in=account,
            event=_event(
                service, ACCOUNT_SIGNED_IN, actor=account.account_id, account_id=account.account_id, operation="sign-in"
            ),
            secrets=service.accounts.secrets,
        )
    except AccountNotActiveError:
        # The account was disabled, deleted or given a new password during the check.
        service.record_authentication_refusal(path, "sign-in-refused")
        raise sign_in_refused() from None
    return SessionResource(token=token, account=account_resource(account), idle_seconds=session_idle_seconds())


def sign_out(service: RunService, principal: Principal) -> None:
    """End the session that the request presented."""
    if principal.kind != "session" or principal.session_id is None:
        raise ServiceAPIError(422, "not-a-session", NOT_A_SESSION_MESSAGE)
    service.accounts.projection.delete_account_session(
        principal.session_id,
        event=_event(
            service, ACCOUNT_SIGNED_OUT, actor=principal.actor, account_id=principal.actor, operation="sign-out"
        ),
        secrets=service.accounts.secrets,
    )


def require_administrator(service: RunService, principal: Principal, operation: str) -> None:
    """Refuse a caller that is not an administrator, and record the refusal.

    Raises:
        ServiceAPIError: ``403 forbidden``.
    """
    if principal.administrator:
        return
    service.record_account_event(
        ACCOUNT_REFUSED,
        actor=principal.actor,
        account_id=principal.actor,
        operation=operation,
        outcome="refused-authorization",
    )
    raise ServiceAPIError(403, "forbidden", ADMINISTRATOR_REQUIRED_MESSAGE)


def _refuse_secret_text(service: RunService, principal: Principal, operation: str, parameter: str) -> ServiceAPIError:
    """Record the refusal of a free text that holds a secret or a token prefix; return the error.

    The event holds no reason: a token that the process cannot list would be stored as it was sent.
    """
    service.record_account_event(
        ACCOUNT_REFUSED,
        actor=principal.actor,
        account_id=principal.actor,
        operation=operation,
        outcome="refused-secret-parameter",
    )
    message = REASON_REFUSED_MESSAGE if parameter == "reason" else TOKEN_NAME_REFUSED_MESSAGE
    return ServiceAPIError(422, "secret-parameter-refused", message)


def _require_safe_reason(service: RunService, principal: Principal, operation: str, reason: str | None) -> None:
    """Refuse a ``reason`` that holds a secret of the process or the prefix of a token, before any record of it."""
    if reason is not None and not service.accounts.accepts_free_text(reason):
        raise _refuse_secret_text(service, principal, operation, "reason")


def _require_account_name(name: str) -> None:
    """Refuse a name that no new account can have (research R8).

    A name that holds the prefix of a token is refused too: a token pasted as a name would be
    stored and shown as it was sent.
    """
    key = name.lower()
    if (
        _ACCOUNT_NAME.fullmatch(name) is None
        or key in RESERVED_ACCOUNT_NAMES
        or key.startswith(ACCOUNT_ID_PREFIX)
        or credentials.contains_token_prefix(name)
    ):
        raise ServiceAPIError(422, "account-name-invalid", ACCOUNT_NAME_INVALID_MESSAGE)


def _is_checkable_password(password: str) -> bool:
    """Return whether an account can have this password: 1 to 1,024 characters that UTF-8 can encode."""
    return 0 < len(password) <= PASSWORD_MAX_LENGTH and credentials.is_encodable(password)


def _require_new_password(password: str) -> None:
    """Refuse a new password that is too short, too long to be hashed, or that UTF-8 cannot encode."""
    if len(password) < PASSWORD_MIN_LENGTH:
        raise ServiceAPIError(422, "password-too-short", PASSWORD_TOO_SHORT_MESSAGE)
    if len(password) > PASSWORD_MAX_LENGTH:
        raise ServiceAPIError(422, "password-too-long", PASSWORD_TOO_LONG_MESSAGE)
    if not credentials.is_encodable(password):
        raise ServiceAPIError(422, "password-invalid", PASSWORD_INVALID_MESSAGE)


def _hash_new_password(password: str) -> str:
    """Return the hash to store for a new password, computed in one place of the gate of the process.

    Raises:
        ServiceAPIError: ``503 sign-in-busy`` when no place of the gate became free in time, or
            ``422 password-invalid`` when UTF-8 cannot encode the password.
    """
    try:
        return credentials.PASSWORD_VERIFICATION_GATE.hash(password)
    except credentials.SignInBusyError:
        raise ServiceAPIError(503, "sign-in-busy", SIGN_IN_BUSY_MESSAGE) from None
    except (credentials.PasswordEncodingError, UnicodeEncodeError):
        # Neither error is chained: ``UnicodeEncodeError`` holds the whole password.
        raise ServiceAPIError(422, "password-invalid", PASSWORD_INVALID_MESSAGE) from None


def _environment_managed(account: Account, variable: str | None = None) -> ServiceAPIError:
    """Return the refusal of a change to an account that the environment manages.

    For ``admin`` it names the variable of a password or a token change. For ``worker`` it names
    the variable of its token, whatever the change.
    """
    if is_worker_account(account):
        message = _WORKER_MESSAGE.format(variable=WORKER_TOKEN_ENV)
    elif variable is None:
        message = _ADMIN_STATE_MESSAGE
    else:
        message = _ENVIRONMENT_MANAGED_MESSAGE.format(variable=variable)
    return ServiceAPIError(409, "environment-managed-account", message)


def _find_account(service: RunService, name: str) -> Account:
    """Return the account with this name, in any letter case. A deleted account has no name.

    Raises:
        ServiceAPIError: ``404 account-not-found``.
    """
    account = None
    if _ACCOUNT_NAME.fullmatch(name) is not None:
        account = service.accounts.projection.lookup_account_by_name(name)
    if account is None or account.name is None or account.deleted_at is not None:
        raise ServiceAPIError(404, "account-not-found", ACCOUNT_NOT_FOUND_MESSAGE)
    return account


def _own_account(service: RunService, principal: Principal) -> Account:
    """Return the account of the caller, which the resolver read for this request.

    Raises:
        ServiceAPIError: ``401 unauthenticated`` when the account was deleted since.
    """
    account = service.accounts.projection.lookup_account(principal.actor)
    if account is None or account.name is None or account.deleted_at is not None:
        raise _unauthenticated()
    return account


def _unauthenticated() -> ServiceAPIError:
    return ServiceAPIError(401, "unauthenticated", "a valid bearer token is required")


def _resource(service: RunService, account: Account) -> AccountResource:
    """Return the resource of an account with the refusal of its sign-ins, when one is in force."""
    name = account.name
    refused_until = None if name is None else sign_in_refused_until(service, name)
    return account_resource(account, refused_until=refused_until)


def create_account(service: RunService, principal: Principal, body: CreateAccountRequest) -> AccountResource:
    """Create an account with its first password; the administrator who asks is the actor of the event.

    Raises:
        ServiceAPIError: ``422 secret-parameter-refused``, ``account-name-invalid``, ``password-too-short``,
            ``password-too-long`` or ``password-invalid``, ``409 account-name-taken``, ``503 sign-in-busy``.
    """
    operation = "create-account"
    _require_safe_reason(service, principal, operation, body.reason)
    _require_account_name(body.name)
    projection = service.accounts.projection
    # Before the hash: a name that is taken costs no hash computation.
    if projection.lookup_account_by_name(body.name) is not None:
        raise ServiceAPIError(409, "account-name-taken", ACCOUNT_NAME_TAKEN_MESSAGE)
    _require_new_password(body.password)
    password_hash = _hash_new_password(body.password)
    try:
        account = projection.create_account(
            name=body.name,
            password_hash=password_hash,
            administrator=body.administrator,
            now=service.accounts.now(),
            event_for=lambda account_id: _event(
                service,
                ACCOUNT_CREATED,
                actor=principal.actor,
                account_id=account_id,
                operation=operation,
                reason=body.reason,
            ),
            secrets=service.accounts.secrets,
        )
    except AccountNameTakenError:
        raise ServiceAPIError(409, "account-name-taken", ACCOUNT_NAME_TAKEN_MESSAGE) from None
    return account_resource(account)


def list_accounts(service: RunService, state: AccountState) -> list[AccountResource]:
    """Return the accounts in this state that are not deleted, by name.

    The refusal of each account is read with one store call for each account: the store has no
    read of several names, and the number of accounts is small.
    """
    disabled = {"active": False, "disabled": True, "all": None}[state]
    return [_resource(service, account) for account in service.accounts.projection.list_accounts(disabled=disabled)]


def read_account(service: RunService, name: str) -> AccountResource:
    """Return the account with this name.

    Raises:
        ServiceAPIError: ``404 account-not-found``.
    """
    return _resource(service, _find_account(service, name))


def update_account(service: RunService, principal: Principal, name: str, body: UpdateAccountRequest) -> AccountResource:
    """Disable an account, which deletes its tokens and its sessions, enable it again, or set its level.

    Every refusal comes before the first change. A field whose value the account already has
    changes nothing and writes no event; each field that changes writes its own event. The
    resolver reads the account at each request, so a new level applies from the next request of
    the account, with no new sign-in.

    Only an administrator reaches this action, so an account cannot raise its own level (FR-011):
    an administrator can lower its own level or keep it.

    Raises:
        ServiceAPIError: ``404 account-not-found``, ``409 environment-managed-account``, or
            ``422 request-invalid`` for a request that names no change.
    """
    _require_safe_reason(service, principal, "update-account", body.reason)
    account = _find_account(service, name)
    if body.disabled is None and body.administrator is None:
        raise ServiceAPIError(422, "request-invalid", NO_CHANGE_MESSAGE)
    if account.environment_managed:
        raise _environment_managed(account)
    projection, now, secrets = service.accounts.projection, service.accounts.now(), service.accounts.secrets
    if body.disabled is not None and body.disabled != account.disabled:
        # The store writes each event in the transaction of its change.
        event = _event(
            service,
            ACCOUNT_DISABLED if body.disabled else ACCOUNT_ENABLED,
            actor=principal.actor,
            account_id=account.account_id,
            operation="disable-account" if body.disabled else "enable-account",
            reason=body.reason,
        )
        change = projection.disable_account if body.disabled else projection.enable_account
        change(account.account_id, now=now, event=event, secrets=secrets)
    if body.administrator is not None and body.administrator != account.administrator:
        projection.set_account_level(
            account.account_id,
            administrator=body.administrator,
            now=now,
            event=_event(
                service,
                ACCOUNT_LEVEL_SET,
                actor=principal.actor,
                account_id=account.account_id,
                operation="set-account-level",
                reason=body.reason,
            ),
            secrets=secrets,
        )
    return read_account(service, name)


def delete_account(service: RunService, principal: Principal, name: str, reason: str | None) -> DeletedAccountResource:
    """Delete an account, and return the placeholder that its records display from now on.

    The store empties the name, the key and the password hash, deletes the tokens, the sessions
    and the failed sign-ins of the name, and writes the event, in one transaction. No run,
    approval, audit event or configuration version changes, and a run in progress continues. An
    administrator may delete its own account: its next request is refused.

    Raises:
        ServiceAPIError: ``404 account-not-found``, for an unknown or already deleted account, or
            ``409 environment-managed-account``.
    """
    operation = "delete-account"
    _require_safe_reason(service, principal, operation, reason)
    account = _find_account(service, name)
    if account.environment_managed:
        raise _environment_managed(account)
    now = service.accounts.now()
    event = account_event(
        ACCOUNT_DELETED,
        actor=principal.actor,
        account_id=account.account_id,
        operation=operation,
        reason=reason,
        created_at=now,
    )
    deleted = service.accounts.projection.delete_account(
        account.account_id, event=event, now=now, secrets=service.accounts.secrets
    )
    if deleted is None:
        # Another request deleted the account after it was read.
        raise ServiceAPIError(404, "account-not-found", ACCOUNT_NOT_FOUND_MESSAGE)
    return DeletedAccountResource(placeholder=deleted_account_placeholder(deleted.short_id))


def list_account_tokens(service: RunService, name: str) -> list[TokenResource]:
    """Return the API tokens of an account, oldest first, without any value.

    Raises:
        ServiceAPIError: ``404 account-not-found``.
    """
    account = _find_account(service, name)
    return [token_resource(token) for token in service.accounts.projection.list_account_tokens(account.account_id)]


def delete_account_token(
    service: RunService, principal: Principal, name: str, token_id: str, reason: str | None
) -> None:
    """Delete one API token of an account; its value is refused afterwards.

    The account that the environment manages is refused before its token is looked for, as
    ``delete_own_token`` does: its one token is defined by the environment.

    Raises:
        ServiceAPIError: ``404 account-not-found``, ``409 environment-managed-account``, or
            ``404 token-not-found`` for a token that the account does not have.
    """
    operation = "delete-account-token"
    _require_safe_reason(service, principal, operation, reason)
    account = _find_account(service, name)
    if account.environment_managed:
        raise _environment_managed(account, ADMIN_ACCOUNT_TOKEN_ENV)
    _delete_token(service, principal, account, token_id, operation=operation, reason=reason)


def _delete_token(
    service: RunService, principal: Principal, account: Account, token_id: str, *, operation: str, reason: str | None
) -> None:
    """Delete one API token of an account and write its event, or refuse with ``404 token-not-found``."""
    event = _event(
        service,
        ACCOUNT_TOKEN_DELETED,
        actor=principal.actor,
        account_id=account.account_id,
        operation=operation,
        reason=reason,
    )
    # PostgreSQL stores no NUL character in a text, so an identifier that holds one names no token.
    if "\x00" in token_id or not service.accounts.projection.delete_account_token(
        account.account_id, token_id, event=event, secrets=service.accounts.secrets
    ):
        raise ServiceAPIError(404, "token-not-found", TOKEN_NOT_FOUND_MESSAGE)


def set_account_password(service: RunService, principal: Principal, name: str, body: SetPasswordRequest) -> None:
    """Replace the password of an account: its sessions end, its tokens stay, its refusal ends.

    Raises:
        ServiceAPIError: ``422 secret-parameter-refused`` for a reason, ``404 account-not-found``,
            ``409 environment-managed-account``, ``422 password-too-short``, ``password-too-long``
            or ``password-invalid``, ``503 sign-in-busy``.
    """
    operation = "set-account-password"
    _require_safe_reason(service, principal, operation, body.reason)
    account = _find_account(service, name)
    if account.environment_managed:
        raise _environment_managed(account, ADMIN_ACCOUNT_PASSWORD_ENV)
    _require_new_password(body.new_password)
    password_hash = _hash_new_password(body.new_password)
    # One transaction: the hash, the end of every session, the end of the refusal and the event.
    replaced = service.accounts.projection.replace_account_password(
        account.account_id,
        password_hash=password_hash,
        event=_event(
            service,
            ACCOUNT_PASSWORD_SET,
            actor=principal.actor,
            account_id=account.account_id,
            operation=operation,
            reason=body.reason,
        ),
        clear_sign_in_name=account.name,
        now=service.accounts.now(),
        secrets=service.accounts.secrets,
    )
    if not replaced:
        # Another request deleted the account after it was read.
        raise ServiceAPIError(404, "account-not-found", ACCOUNT_NOT_FOUND_MESSAGE)


def unlock_account(service: RunService, principal: Principal, name: str, reason: str | None) -> None:
    """End the refusal of the sign-ins of an account, whether or not one is in force.

    Raises:
        ServiceAPIError: ``404 account-not-found``.
    """
    operation = "unlock-account"
    _require_safe_reason(service, principal, operation, reason)
    account = _find_account(service, name)
    service.accounts.projection.clear_sign_in_attempts(
        name,
        event=_event(
            service,
            ACCOUNT_UNLOCKED,
            actor=principal.actor,
            account_id=account.account_id,
            operation=operation,
            reason=reason,
        ),
        secrets=service.accounts.secrets,
    )


def _refuse_current_password(service: RunService, principal: Principal) -> ServiceAPIError:
    """Record the refusal of a current password, without the name; return the error."""
    service.record_account_event(
        ACCOUNT_REFUSED,
        actor=principal.actor,
        account_id=principal.actor,
        operation="change-password",
        outcome="refused-current-password",
    )
    return ServiceAPIError(403, "current-password-refused", CURRENT_PASSWORD_REFUSED_MESSAGE)


def change_own_password(service: RunService, principal: Principal, body: ChangePasswordRequest) -> None:
    """Replace the caller's password: the other sessions end and the tokens stay.

    A wrong current password counts as a failed sign-in of the caller's name. While the name is
    refused, the current password is not checked.

    Raises:
        ServiceAPIError: ``422 secret-parameter-refused`` for a reason,
            ``409 environment-managed-account``, ``422 password-too-short``, ``password-too-long``
            or ``password-invalid``, ``403 current-password-refused``, ``503 sign-in-busy``.
    """
    operation = "change-password"
    _require_safe_reason(service, principal, operation, body.reason)
    account = _own_account(service, principal)
    name = account.name or ""
    if account.environment_managed:
        raise _environment_managed(account, ADMIN_ACCOUNT_PASSWORD_ENV)
    if sign_in_refused_until(service, name) is not None:
        raise _refuse_current_password(service, principal)
    _require_new_password(body.new_password)
    if not _is_checkable_password(body.current_password):
        # No account has this password: nothing is checked and nothing is counted, as at sign-in.
        raise _refuse_current_password(service, principal)
    # A name that became refused while the request waited gets the same answer, uncounted.
    if not _check_password_of_name(service, name, account, body.current_password):
        raise _refuse_current_password(service, principal)
    password_hash = _hash_new_password(body.new_password)
    # One transaction. A request made with an API token keeps no session.
    replaced = service.accounts.projection.replace_account_password(
        account.account_id,
        password_hash=password_hash,
        event=_event(
            service,
            ACCOUNT_PASSWORD_CHANGED,
            actor=principal.actor,
            account_id=account.account_id,
            operation=operation,
            reason=body.reason,
        ),
        keep_session_id=principal.session_id if principal.kind == "session" else None,
        clear_sign_in_name=name,
        now=service.accounts.now(),
        secrets=service.accounts.secrets,
    )
    if not replaced:
        # The account was deleted after the request was resolved.
        raise _unauthenticated()


def list_own_tokens(service: RunService, principal: Principal) -> list[TokenResource]:
    """Return the API tokens of the caller's account, oldest first; ``401`` after its deletion."""
    account_id = _own_account(service, principal).account_id
    return [token_resource(token) for token in service.accounts.projection.list_account_tokens(account_id)]


def create_own_token(service: RunService, principal: Principal, body: CreateTokenRequest) -> CreatedTokenResource:
    """Create an API token for the caller's account; the answer holds its value this once.

    Raises:
        ServiceAPIError: ``409 environment-managed-account``, ``422 expiry-in-the-past``,
            ``409 token-name-taken``, or ``422 secret-parameter-refused`` for a name or a reason
            that holds a secret or a token prefix.
    """
    operation = "create-token"
    _require_safe_reason(service, principal, operation, body.reason)
    if not service.accounts.accepts_free_text(body.name):
        raise _refuse_secret_text(service, principal, operation, "name")
    account = _own_account(service, principal)
    if account.environment_managed:
        raise _environment_managed(account, ADMIN_ACCOUNT_TOKEN_ENV)
    now = service.accounts.now()
    if body.expires_at is not None and body.expires_at <= now:
        raise ServiceAPIError(422, "expiry-in-the-past", EXPIRY_IN_THE_PAST_MESSAGE)
    value = credentials.new_api_token()
    try:
        # One transaction: the token is inserted only while the account is active, with its event.
        token = service.accounts.projection.create_account_token(
            account.account_id,
            name=body.name,
            digest=credentials.digest(value),
            expires_at=body.expires_at,
            now=now,
            event=_event(
                service,
                ACCOUNT_TOKEN_CREATED,
                actor=principal.actor,
                account_id=account.account_id,
                operation=operation,
                reason=body.reason,
            ),
            secrets=service.accounts.secrets,
        )
    except AccountTokenNameTakenError:
        raise ServiceAPIError(409, "token-name-taken", TOKEN_NAME_TAKEN_MESSAGE) from None
    except AccountNotActiveError:
        # The account was disabled or deleted after the request was resolved.
        raise _unauthenticated() from None
    return CreatedTokenResource(**token_resource(token).model_dump(), token=value)


def delete_own_token(service: RunService, principal: Principal, token_id: str, reason: str | None) -> None:
    """Delete one API token of the caller's account; its value is refused afterwards.

    Raises:
        ServiceAPIError: ``409 environment-managed-account``, or ``404 token-not-found`` for a
            token that the caller's account does not have.
    """
    operation = "delete-token"
    _require_safe_reason(service, principal, operation, reason)
    account = _own_account(service, principal)
    if account.environment_managed:
        raise _environment_managed(account, ADMIN_ACCOUNT_TOKEN_ENV)
    _delete_token(service, principal, account, token_id, operation=operation, reason=reason)
