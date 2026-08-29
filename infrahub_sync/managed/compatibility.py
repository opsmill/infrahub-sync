"""Managed API compatibility declarations."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

API_VERSIONS = ("v3-unstable",)
API_STABILITY = "unstable"
_METADATA_ERROR = "managed package metadata is unavailable"


def installed_server_version() -> str:
    """Return the installed package version without guessing a fallback."""
    try:
        return version("infrahub-sync")
    except (PackageNotFoundError, ValueError) as exc:
        raise RuntimeError(_METADATA_ERROR) from exc
