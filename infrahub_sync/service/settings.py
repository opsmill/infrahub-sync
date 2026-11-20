"""Service-wide settings of the Sync API that admission reads per request.

``INFRAHUB_SYNC_REQUIRE_APPROVAL`` makes ``apply`` refuse a run without a matching approval and
makes ``sync`` refuse to start. It is read again for every request, so a restart with a new value
takes effect without touching stored runs, and it is validated once when the application is created,
so a malformed value stops the service instead of silently meaning "off".

``INFRAHUB_SYNC_WEB_DIRECTORY`` names the directory of the built web interface. It is read once,
when the service application is built, and never stops the service: a value that names no usable
directory is logged and treated as unset.

``INFRAHUB_SYNC_ADMIN_ACCOUNT_PASSWORD`` and ``INFRAHUB_SYNC_ADMIN_ACCOUNT_TOKEN`` define the
``admin`` account. They are read once, when the served application is built, and a value that is
not accepted stops the service. ``INFRAHUB_SYNC_SESSION_IDLE_SECONDS`` is the period without
activity after which a session ends.

``INFRAHUB_SYNC_ENCRYPTION_KEY`` and ``INFRAHUB_SYNC_WORKER_TOKEN`` are read by ``sync-api``: the key
of stored credentials and the token of the ``worker`` account. ``INFRAHUB_SYNC_WORKER_TOKEN`` and
``INFRAHUB_SYNC_API_URL`` are read by each ``sync-worker`` to reach the Sync API. A value that is not
accepted stops the process. No message of this module contains a value.
"""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

import structlog

from .credentials import TOKEN_PREFIXES, is_encodable

if TYPE_CHECKING:
    from collections.abc import Mapping

REQUIRE_APPROVAL_ENV = "INFRAHUB_SYNC_REQUIRE_APPROVAL"
WEB_DIRECTORY_ENV = "INFRAHUB_SYNC_WEB_DIRECTORY"
ADMIN_ACCOUNT_PASSWORD_ENV = "INFRAHUB_SYNC_ADMIN_ACCOUNT_PASSWORD"
ADMIN_ACCOUNT_TOKEN_ENV = "INFRAHUB_SYNC_ADMIN_ACCOUNT_TOKEN"
SESSION_IDLE_SECONDS_ENV = "INFRAHUB_SYNC_SESSION_IDLE_SECONDS"
ENCRYPTION_KEY_ENV = "INFRAHUB_SYNC_ENCRYPTION_KEY"
WORKER_TOKEN_ENV = "INFRAHUB_SYNC_WORKER_TOKEN"
WORKER_API_URL_ENV = "INFRAHUB_SYNC_API_URL"
ENCRYPTION_KEY_BYTES = 32

PASSWORD_MIN_LENGTH = 12
# The longest password that is checked or stored. A longer one is refused before any hash is
# computed, so the size of a request cannot set the cost of a password check.
PASSWORD_MAX_LENGTH = 1024
ADMIN_ACCOUNT_TOKEN_MIN_LENGTH = 16
SESSION_IDLE_SECONDS_DEFAULT = 28800
SESSION_IDLE_SECONDS_MINIMUM = 60
# The largest period that a ``timedelta`` holds with room to spare: about 31,700 years.
SESSION_IDLE_SECONDS_MAXIMUM = 999_999_999_999
_SESSION_IDLE_SECONDS_MAX_DIGITS = len(str(SESSION_IDLE_SECONDS_MAXIMUM))
_SESSION_IDLE_SECONDS_RULE = (
    f"must be a whole number from {SESSION_IDLE_SECONDS_MINIMUM} to {SESSION_IDLE_SECONDS_MAXIMUM}"
)

logger = structlog.get_logger(__name__)

_TRUE_VALUES = frozenset({"true", "1", "yes", "on"})
_FALSE_VALUES = frozenset({"false", "0", "no", "off"})


def _parse_flag(name: str, value: str | None) -> bool:
    """Return the boolean a flag variable names; unset means off.

    Raises:
        ValueError: The value is not one of the accepted words; the message names the variable
            and the accepted values, never the value itself.
    """
    if value is None:
        return False
    normalized = value.strip().lower()
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES:
        return False
    msg = f"{name} must be one of true, 1, yes, on, false, 0, no or off (in any case)"
    raise ValueError(msg)


def approval_required() -> bool:
    """Return whether this site requires an approved plan before ``apply`` and refuses ``sync``.

    Raises:
        ValueError: ``INFRAHUB_SYNC_REQUIRE_APPROVAL`` holds a value that is not accepted.
    """
    return _parse_flag(REQUIRE_APPROVAL_ENV, os.environ.get(REQUIRE_APPROVAL_ENV))


def web_directory() -> Path | None:
    """Return the resolved directory of the built web interface, or ``None`` when there is none.

    Unset or empty means no web interface. A value whose resolved directory holds no regular file
    ``index.html``, or that cannot be checked, is logged as a warning and treated as unset, so it
    never stops the service.
    """
    value = os.environ.get(WEB_DIRECTORY_ENV, "").strip()
    if not value:
        return None
    try:
        directory = Path(value).resolve()
        found = (directory / "index.html").is_file()
    except (OSError, RuntimeError) as error:
        # `is_file` raises PermissionError for a directory the service cannot enter, and `resolve`
        # raises RuntimeError for a symbolic link loop before Python 3.13. Never the error text.
        logger.warning(
            "web interface directory cannot be checked; the web interface is not served",
            variable=WEB_DIRECTORY_ENV,
            error_type=type(error).__name__,
        )
        return None
    if not found:
        logger.warning(
            "web interface directory holds no index.html; the web interface is not served",
            variable=WEB_DIRECTORY_ENV,
            path=str(directory),
        )
        return None
    return directory


class AccountSettingsError(ValueError):
    """A variable that defines accounts or sessions holds a value that is not accepted.

    The message names the variable and the rule, never the value.
    """


@dataclass(frozen=True)
class AdminAccountSettings:
    """The password and the optional API token of the ``admin`` account, as the environment gives them."""

    password: str = field(repr=False)
    token: str | None = field(default=None, repr=False)


def admin_account_settings(environ: Mapping[str, str] | None = None) -> AdminAccountSettings:
    """Return the validated variables of the ``admin`` account.

    An empty token counts as unset: ``admin`` then has no API token.

    Raises:
        AccountSettingsError: the password is unset, empty, shorter than 12 or longer than 1,024
            characters, or not valid UTF-8 text; or the token is shorter than 16 characters,
            holds a character that is not printable ASCII, starts or ends with a space, or
            starts with ``ifs_pat_`` or ``ifs_ses_``.
    """
    env = os.environ if environ is None else environ
    password = env.get(ADMIN_ACCOUNT_PASSWORD_ENV, "")
    if not password:
        msg = f"{ADMIN_ACCOUNT_PASSWORD_ENV} must be set: it is the password of the admin account"
        raise AccountSettingsError(msg)
    if len(password) < PASSWORD_MIN_LENGTH:
        msg = f"{ADMIN_ACCOUNT_PASSWORD_ENV} must have at least {PASSWORD_MIN_LENGTH} characters"
        raise AccountSettingsError(msg)
    if len(password) > PASSWORD_MAX_LENGTH:
        # No sign-in could succeed with it: a sign-in refuses a longer password before any check.
        msg = f"{ADMIN_ACCOUNT_PASSWORD_ENV} must have at most {PASSWORD_MAX_LENGTH} characters"
        raise AccountSettingsError(msg)
    if not is_encodable(password):
        # A byte that is not UTF-8 reaches Python as a lone surrogate, which cannot be hashed.
        msg = f"{ADMIN_ACCOUNT_PASSWORD_ENV} must be valid UTF-8 text"
        raise AccountSettingsError(msg)
    token = env.get(ADMIN_ACCOUNT_TOKEN_ENV, "")
    if not token:
        return AdminAccountSettings(password=password)
    _check_environment_token(ADMIN_ACCOUNT_TOKEN_ENV, token, AccountSettingsError)
    return AdminAccountSettings(password=password, token=token)


def _check_environment_token(name: str, token: str, error: type[AccountSettingsError]) -> None:
    """Refuse a token that the environment defines and that a request could never present.

    Raises:
        AccountSettingsError: of the given class; the message names the variable and the rule.
    """
    if len(token) < ADMIN_ACCOUNT_TOKEN_MIN_LENGTH:
        msg = f"{name} must have at least {ADMIN_ACCOUNT_TOKEN_MIN_LENGTH} characters"
        raise error(msg)
    if not (token.isascii() and token.isprintable()) or token != token.strip():
        # A request presents the token in an HTTP header, which the Sync API reads as Latin-1 and
        # without surrounding spaces: any other token could never match. A byte that is not UTF-8
        # reaches Python as a lone surrogate, which has no digest.
        msg = f"{name} must hold printable ASCII characters only, with no space at either end"
        raise error(msg)
    if token.startswith(TOKEN_PREFIXES):
        msg = f"{name} must not start with {' or '.join(TOKEN_PREFIXES)}"
        raise error(msg)


class CredentialSettingsError(AccountSettingsError):
    """A variable of stored credentials holds a value that is not accepted.

    It is an :class:`AccountSettingsError`, so a start that reports a bad account variable reports
    this one the same way. The message names the variable and the rule, never the value.
    """


@dataclass(frozen=True)
class CredentialSettings:
    """The encryption key of stored credentials and the token of the ``worker`` account, for ``sync-api``."""

    encryption_key: bytes = field(repr=False)
    worker_token: str = field(repr=False)


@dataclass(frozen=True)
class WorkerSettings:
    """The address of the Sync API and the token that a ``sync-worker`` presents to it."""

    api_url: str
    token: str = field(repr=False)


def _encryption_key(env: Mapping[str, str]) -> bytes:
    """Return the decoded encryption key.

    Raises:
        CredentialSettingsError: the key is unset, empty, not base64, or not 32 bytes once decoded.
    """
    value = env.get(ENCRYPTION_KEY_ENV, "").strip()
    if not value:
        msg = f"{ENCRYPTION_KEY_ENV} must be set: it is the base64 encoding of the 32-byte key of stored credentials"
        raise CredentialSettingsError(msg)
    try:
        key = base64.b64decode(value, validate=True)
    except ValueError:
        # `binascii.Error` for a character or a padding that is not base64, `ValueError` for a
        # character that is not ASCII. Never the error text: it can quote the value.
        msg = f"{ENCRYPTION_KEY_ENV} must be base64 text"
        raise CredentialSettingsError(msg) from None
    if len(key) != ENCRYPTION_KEY_BYTES:
        msg = f"{ENCRYPTION_KEY_ENV} must decode to {ENCRYPTION_KEY_BYTES} bytes"
        raise CredentialSettingsError(msg)
    return key


def _worker_token(env: Mapping[str, str]) -> str:
    """Return the token of the ``worker`` account.

    Raises:
        CredentialSettingsError: the token is unset or empty, or breaks a rule of the ``admin`` token.
    """
    token = env.get(WORKER_TOKEN_ENV, "")
    if not token:
        msg = f"{WORKER_TOKEN_ENV} must be set: it is the token of the worker account"
        raise CredentialSettingsError(msg)
    _check_environment_token(WORKER_TOKEN_ENV, token, CredentialSettingsError)
    return token


def credential_settings(environ: Mapping[str, str] | None = None) -> CredentialSettings:
    """Return the validated variables of stored credentials that ``sync-api`` reads.

    Raises:
        CredentialSettingsError: the encryption key is unset, empty, not base64 or not 32 bytes
            once decoded; or the worker token is unset, empty, shorter than 16 characters, holds
            a character that is not printable ASCII, starts or ends with a space, starts with
            ``ifs_pat_`` or ``ifs_ses_``, or equals the token of the ``admin`` account.
    """
    env = os.environ if environ is None else environ
    key = _encryption_key(env)
    token = _worker_token(env)
    _check_differs_from_admin_token(env, token)
    return CredentialSettings(encryption_key=key, worker_token=token)


def _check_differs_from_admin_token(env: Mapping[str, str], token: str) -> None:
    """Refuse a worker token equal to the token of the ``admin`` account.

    Raises:
        CredentialSettingsError: the two variables hold the same value.
    """
    admin_token = env.get(ADMIN_ACCOUNT_TOKEN_ENV, "")
    # A start-up check of two values the operator already holds: no timing to protect.
    if admin_token and token == admin_token:
        msg = f"{WORKER_TOKEN_ENV} must differ from {ADMIN_ACCOUNT_TOKEN_ENV}"
        raise CredentialSettingsError(msg)


def worker_account_token(environ: Mapping[str, str] | None = None) -> str | None:
    """Return the token of the ``worker`` account that ``sync-api`` applies, or ``None`` when it is unset.

    An empty value counts as unset. Until the Compose files give every ``sync-api`` the variable
    (spec 027, T054), a start without it applies no ``worker`` account; the rules of a value that
    is set are those of :func:`credential_settings`.

    Raises:
        CredentialSettingsError: the token is shorter than 16 characters, holds a character that is
            not printable ASCII, starts or ends with a space, starts with ``ifs_pat_`` or
            ``ifs_ses_``, or equals the token of the ``admin`` account.
    """
    env = os.environ if environ is None else environ
    if not env.get(WORKER_TOKEN_ENV, ""):
        return None
    token = _worker_token(env)
    _check_differs_from_admin_token(env, token)
    return token


def worker_settings(environ: Mapping[str, str] | None = None) -> WorkerSettings:
    """Return the validated variables that a ``sync-worker`` reads to reach the Sync API.

    Raises:
        CredentialSettingsError: the worker token breaks a rule of :func:`credential_settings`
            other than the comparison with the ``admin`` token, which a worker does not hold; or
            the address is unset, empty, has a space at either end, is not an ``http://`` or
            ``https://`` address with a host, or holds a user name or password.
    """
    env = os.environ if environ is None else environ
    token = _worker_token(env)
    address = env.get(WORKER_API_URL_ENV, "")
    if not address:
        msg = f"{WORKER_API_URL_ENV} must be set: it is the address of the Sync API, for example http://sync-api:8000"
        raise CredentialSettingsError(msg)
    try:
        parts = urlsplit(address)
        hostname = parts.hostname
    except ValueError:
        parts, hostname = None, None
    if parts is None or address != address.strip() or parts.scheme not in {"http", "https"} or not hostname:
        msg = (
            f"{WORKER_API_URL_ENV} must be an http:// or https:// address with a host, for example http://sync-api:8000"
        )
        raise CredentialSettingsError(msg)
    if parts.username is not None or parts.password is not None:
        # A password in the address would reach every log line that names the address.
        msg = f"{WORKER_API_URL_ENV} must not hold a user name or password"
        raise CredentialSettingsError(msg)
    return WorkerSettings(api_url=address, token=token)


def session_idle_seconds(environ: Mapping[str, str] | None = None) -> int:
    """Return the period without activity, in seconds, after which a session ends.

    Unset or empty means the default of 28800 seconds.

    Raises:
        AccountSettingsError: the value is not a whole number from 60 to 999999999999.
    """
    env = os.environ if environ is None else environ
    value = env.get(SESSION_IDLE_SECONDS_ENV, "").strip()
    if not value:
        return SESSION_IDLE_SECONDS_DEFAULT
    # The length is checked first: Python refuses to convert a very long text of digits.
    if (
        not (value.isascii() and value.isdigit())
        or len(value) > _SESSION_IDLE_SECONDS_MAX_DIGITS
        or not SESSION_IDLE_SECONDS_MINIMUM <= int(value) <= SESSION_IDLE_SECONDS_MAXIMUM
    ):
        msg = f"{SESSION_IDLE_SECONDS_ENV} {_SESSION_IDLE_SECONDS_RULE}"
        raise AccountSettingsError(msg)
    return int(value)


def validate_settings() -> None:
    """Refuse to start the service when a setting holds a value that is not accepted.

    The variables of the ``admin`` account are not read here: the served application reads them
    once, applies them and removes the password from the environment of the process.

    Raises:
        ValueError: A setting holds a value that is not accepted; the message names the variable.
    """
    approval_required()
    session_idle_seconds()
