"""Service-wide settings of the Sync API that admission reads per request.

``INFRAHUB_SYNC_REQUIRE_APPROVAL`` makes ``apply`` refuse a run without a matching approval and
makes ``sync`` refuse to start. It is read again for every request, so a restart with a new value
takes effect without touching stored runs, and it is validated once when the application is created,
so a malformed value stops the service instead of silently meaning "off".

``INFRAHUB_SYNC_WEB_DIRECTORY`` names the directory of the built web interface. It is read once,
when the service application is built, and never stops the service: a value that names no usable
directory is logged and treated as unset.
"""

from __future__ import annotations

import os
from pathlib import Path

import structlog

REQUIRE_APPROVAL_ENV = "INFRAHUB_SYNC_REQUIRE_APPROVAL"
WEB_DIRECTORY_ENV = "INFRAHUB_SYNC_WEB_DIRECTORY"

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
    ``index.html`` is logged as a warning and treated as unset, so it never stops the service.
    """
    value = os.environ.get(WEB_DIRECTORY_ENV, "").strip()
    if not value:
        return None
    directory = Path(value).resolve()
    if not (directory / "index.html").is_file():
        logger.warning(
            "web interface directory holds no index.html; the web interface is not served",
            variable=WEB_DIRECTORY_ENV,
            path=str(directory),
        )
        return None
    return directory


def validate_settings() -> None:
    """Refuse to start the service when a setting holds a value that is not accepted.

    Raises:
        ValueError: A setting holds a value that is not accepted; the message names the variable.
    """
    approval_required()
