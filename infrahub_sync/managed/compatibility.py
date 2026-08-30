"""Managed API compatibility declarations."""

from __future__ import annotations

import re
from importlib.metadata import PackageNotFoundError, version

API_VERSIONS = ("v3-unstable",)
API_STABILITY = "unstable"
_METADATA_ERROR = "managed package metadata is unavailable"
_SEMANTIC_VERSION_PATTERN = re.compile(
    r"^(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
    r"(?:-(?:0|[1-9][0-9]*|[A-Za-z-][0-9A-Za-z-]*)"
    r"(?:\.(?:0|[1-9][0-9]*|[A-Za-z-][0-9A-Za-z-]*))*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)


def installed_server_version() -> str:
    """Return the installed package version without guessing a fallback."""
    try:
        installed_version = version("infrahub-sync")
    except (PackageNotFoundError, ValueError):
        raise RuntimeError(_METADATA_ERROR) from None
    if (
        type(installed_version) is not str  # pylint: disable=unidiomatic-typecheck
        or _SEMANTIC_VERSION_PATTERN.fullmatch(installed_version) is None
    ):
        raise RuntimeError(_METADATA_ERROR) from None
    return installed_version
