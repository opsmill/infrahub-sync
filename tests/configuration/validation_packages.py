"""Declared-package builders shared by the accumulating-validation tests."""

from __future__ import annotations

import copy
from typing import Any

from infrahub_sync.configuration import ConfigurationPackage

# Each block names a stored credential and declares no credential setting (spec 027).
SOURCE_CREDENTIAL = "netbox-token"
DESTINATION_CREDENTIAL = "infrahub-token"
_VALID_CONTENT: dict[str, Any] = {
    "format_version": 1,
    "configuration": {
        "name": "from-netbox",
        "source": {
            "name": "netbox",
            "credential": SOURCE_CREDENTIAL,
            "settings": {"url": "https://demo.netbox.dev"},
        },
        "destination": {
            "name": "infrahub",
            "credential": DESTINATION_CREDENTIAL,
            "settings": {"url": "http://localhost:8000"},
        },
    },
}


def credential_package_data(name: str | None = None) -> dict[str, Any]:
    """Return one valid declared package whose blocks each name a stored credential, as mutable content.

    ``name`` replaces the configuration name, since a name is unique among configurations.
    """
    data = copy.deepcopy(_VALID_CONTENT)
    if name is not None:
        data["configuration"]["name"] = name
    return data


def credential_package(data: dict[str, Any] | None = None, *, name: str | None = None) -> ConfigurationPackage:
    """Parse declared content into a package, defaulting to the valid one that names stored credentials."""
    return ConfigurationPackage.model_validate(credential_package_data(name) if data is None else data)


def package_data(name: str | None = None) -> dict[str, Any]:
    """Return one valid declared package whose blocks name no stored credential, as mutable content.

    Such a package registers in a store that holds no stored credential. ``name`` replaces the
    configuration name, since a name is unique among configurations.
    """
    data = credential_package_data(name)
    for block in ("source", "destination"):
        del data["configuration"][block]["credential"]
    return data


def package(data: dict[str, Any] | None = None, *, name: str | None = None) -> ConfigurationPackage:
    """Parse declared content into a package, defaulting to the valid one that names no stored credential."""
    return ConfigurationPackage.model_validate(package_data(name) if data is None else data)
