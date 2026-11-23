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
activity after which a session ends. ``INFRAHUB_SYNC_HEALTH_CHECK_TIMEOUT`` is the time limit of
each health check and ``INFRAHUB_SYNC_HEALTH_MIN_WORKERS`` the number of live workers below which the
health report is unhealthy. No message of this module contains a value.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from .credentials import TOKEN_PREFIXES, is_encodable

if TYPE_CHECKING:
    from collections.abc import Mapping

REQUIRE_APPROVAL_ENV = "INFRAHUB_SYNC_REQUIRE_APPROVAL"
WEB_DIRECTORY_ENV = "INFRAHUB_SYNC_WEB_DIRECTORY"
ADMIN_ACCOUNT_PASSWORD_ENV = "INFRAHUB_SYNC_ADMIN_ACCOUNT_PASSWORD"
ADMIN_ACCOUNT_TOKEN_ENV = "INFRAHUB_SYNC_ADMIN_ACCOUNT_TOKEN"
SESSION_IDLE_SECONDS_ENV = "INFRAHUB_SYNC_SESSION_IDLE_SECONDS"
HEALTH_CHECK_TIMEOUT_ENV = "INFRAHUB_SYNC_HEALTH_CHECK_TIMEOUT"
HEALTH_MIN_WORKERS_ENV = "INFRAHUB_SYNC_HEALTH_MIN_WORKERS"

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

HEALTH_CHECK_TIMEOUT_DEFAULT = 3
HEALTH_CHECK_TIMEOUT_MINIMUM = 1
HEALTH_CHECK_TIMEOUT_MAXIMUM = 60
HEALTH_MIN_WORKERS_DEFAULT = 1
HEALTH_MIN_WORKERS_MINIMUM = 1
# More digits than any sensible number of workers; Python refuses to convert a very long text of digits.
_HEALTH_MIN_WORKERS_MAX_DIGITS = 9

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
    if len(token) < ADMIN_ACCOUNT_TOKEN_MIN_LENGTH:
        msg = f"{ADMIN_ACCOUNT_TOKEN_ENV} must have at least {ADMIN_ACCOUNT_TOKEN_MIN_LENGTH} characters"
        raise AccountSettingsError(msg)
    if not (token.isascii() and token.isprintable()) or token != token.strip():
        # A request presents the token in an HTTP header, which the Sync API reads as Latin-1 and
        # without surrounding spaces: any other token could never match. A byte that is not UTF-8
        # reaches Python as a lone surrogate, which has no digest.
        msg = f"{ADMIN_ACCOUNT_TOKEN_ENV} must hold printable ASCII characters only, with no space at either end"
        raise AccountSettingsError(msg)
    if token.startswith(TOKEN_PREFIXES):
        msg = f"{ADMIN_ACCOUNT_TOKEN_ENV} must not start with {' or '.join(TOKEN_PREFIXES)}"
        raise AccountSettingsError(msg)
    return AdminAccountSettings(password=password, token=token)


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


class HealthSettingsError(ValueError):
    """A health setting holds a value that is not accepted; the message names the variable, not the value."""


def _whole_number(value: str, max_digits: int) -> int | None:
    """Return the value as a whole number when it holds only ASCII digits, else None."""
    if not (value.isascii() and value.isdigit()) or len(value) > max_digits:
        return None
    return int(value)


def health_check_timeout_seconds(environ: Mapping[str, str] | None = None) -> int:
    """Return the time limit, in seconds, of each health check and of the work-pool read.

    Unset means the default of 3 seconds. An empty value is refused.

    Raises:
        HealthSettingsError: the value is not a whole number from 1 to 60.
    """
    env = os.environ if environ is None else environ
    value = env.get(HEALTH_CHECK_TIMEOUT_ENV)
    if value is None:
        return HEALTH_CHECK_TIMEOUT_DEFAULT
    seconds = _whole_number(value.strip(), len(str(HEALTH_CHECK_TIMEOUT_MAXIMUM)))
    if seconds is None or not HEALTH_CHECK_TIMEOUT_MINIMUM <= seconds <= HEALTH_CHECK_TIMEOUT_MAXIMUM:
        msg = (
            f"{HEALTH_CHECK_TIMEOUT_ENV} must be a whole number of seconds "
            f"from {HEALTH_CHECK_TIMEOUT_MINIMUM} to {HEALTH_CHECK_TIMEOUT_MAXIMUM}"
        )
        raise HealthSettingsError(msg)
    return seconds


def health_min_workers(environ: Mapping[str, str] | None = None) -> int:
    """Return the number of live workers below which the health report is unhealthy.

    Unset means the default of 1. An empty value is refused.

    Raises:
        HealthSettingsError: the value is not a whole number of at least 1.
    """
    env = os.environ if environ is None else environ
    value = env.get(HEALTH_MIN_WORKERS_ENV)
    if value is None:
        return HEALTH_MIN_WORKERS_DEFAULT
    minimum = _whole_number(value.strip(), _HEALTH_MIN_WORKERS_MAX_DIGITS)
    if minimum is None or minimum < HEALTH_MIN_WORKERS_MINIMUM:
        msg = (
            f"{HEALTH_MIN_WORKERS_ENV} must be a whole number of at least {HEALTH_MIN_WORKERS_MINIMUM}, "
            f"with at most {_HEALTH_MIN_WORKERS_MAX_DIGITS} digits"
        )
        raise HealthSettingsError(msg)
    return minimum


def validate_settings() -> None:
    """Refuse to start the service when a setting holds a value that is not accepted.

    The variables of the ``admin`` account are not read here: the served application reads them
    once, applies them and removes the password from the environment of the process.

    Raises:
        ValueError: A setting holds a value that is not accepted; the message names the variable.
    """
    approval_required()
    session_idle_seconds()
    health_check_timeout_seconds()
    health_min_workers()
