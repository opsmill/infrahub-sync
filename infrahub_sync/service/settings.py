"""Service-wide settings of the Sync API that admission reads per request.

``INFRAHUB_SYNC_REQUIRE_APPROVAL`` makes ``apply`` refuse a run without a matching approval and
makes ``sync`` refuse to start. It is read again for every request, so a restart with a new value
takes effect without touching stored runs, and it is validated once when the application is created,
so a malformed value stops the service instead of silently meaning "off".
"""

from __future__ import annotations

import os
from pathlib import Path

from .auth import is_host_name

REQUIRE_APPROVAL_ENV = "INFRAHUB_SYNC_REQUIRE_APPROVAL"
ANONYMOUS_ACTOR_ENV = "INFRAHUB_SYNC_SERVICE_ANONYMOUS_ACTOR"
ANONYMOUS_HOSTS_ENV = "INFRAHUB_SYNC_SERVICE_ANONYMOUS_HOSTS"
WEB_DIRECTORY_ENV = "INFRAHUB_SYNC_SERVICE_WEB_DIRECTORY"
DEFAULT_ANONYMOUS_HOSTS = "localhost,127.0.0.1,::1"

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


def anonymous_actor() -> str | None:
    """Return the actor that requests without a token act as, or ``None`` when anonymous access is off.

    Surrounding whitespace is removed, and an empty value means off.
    """
    value = os.environ.get(ANONYMOUS_ACTOR_ENV, "").strip()
    return value or None


def anonymous_hosts() -> frozenset[str]:
    """Return the lowercase host names that a request without a token must name in its ``Host``.

    Raises:
        ValueError: The setting names no host, or an entry carries a port or IPv6 brackets and so
            could never match; the message names the variable.
    """
    raw = os.environ.get(ANONYMOUS_HOSTS_ENV, DEFAULT_ANONYMOUS_HOSTS)
    hosts = frozenset(entry.strip().lower() for entry in raw.split(",") if entry.strip())
    if not hosts:
        msg = f"{ANONYMOUS_HOSTS_ENV} must name at least one host"
        raise ValueError(msg)
    if not all(is_host_name(host) for host in hosts):
        msg = f"{ANONYMOUS_HOSTS_ENV} must list host names without a port or brackets, such as sync.example.net or ::1"
        raise ValueError(msg)
    return hosts


def web_directory() -> Path | None:
    """Return the directory of the built web interface, or ``None`` when this installation has none."""
    value = os.environ.get(WEB_DIRECTORY_ENV, "").strip()
    return Path(value) if value else None


def validate_settings() -> None:
    """Refuse to start the service when a setting holds a value that is not accepted.

    Raises:
        ValueError: A setting holds a value that is not accepted; the message names the variable.
    """
    approval_required()
