"""Sync API compatibility declarations."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

from packaging.version import InvalidVersion, Version

API_VERSIONS = ("v3-unstable",)
API_STABILITY = "unstable"
_METADATA_ERROR = "service package metadata is unavailable"


def installed_server_version() -> str:
    """Return the installed package version without guessing a fallback."""
    try:
        installed_version = version("infrahub-sync")
    except (PackageNotFoundError, ValueError):
        raise RuntimeError(_METADATA_ERROR) from None
    if not installed_version:
        raise RuntimeError(_METADATA_ERROR) from None
    try:
        Version(installed_version)
    except InvalidVersion:
        raise RuntimeError(_METADATA_ERROR) from None
    return installed_version


def installed_sdk_version() -> str | None:
    """Return the installed ``infrahub-sdk`` version, or ``None`` when its metadata is unavailable.

    ``GET /version`` reports it for discovery only, and the Compose healthcheck reads that
    route, so missing SDK metadata yields ``None`` rather than a failed request.
    """
    try:
        installed_version = version("infrahub-sdk")
    except (PackageNotFoundError, ValueError):
        return None
    return installed_version or None
