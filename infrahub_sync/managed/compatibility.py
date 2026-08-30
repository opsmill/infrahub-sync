"""Managed API compatibility declarations."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

from packaging.version import InvalidVersion, Version

API_VERSIONS = ("v3-unstable",)
API_STABILITY = "unstable"
_METADATA_ERROR = "managed package metadata is unavailable"


def installed_server_version() -> str:
    """Return the installed package version without guessing a fallback."""
    try:
        installed_version = version("infrahub-sync")
    except (PackageNotFoundError, ValueError):
        raise RuntimeError(_METADATA_ERROR) from None
    if type(installed_version) is not str or not installed_version:  # pylint: disable=unidiomatic-typecheck
        raise RuntimeError(_METADATA_ERROR) from None
    try:
        Version(installed_version)
    except InvalidVersion:
        raise RuntimeError(_METADATA_ERROR) from None
    return installed_version
