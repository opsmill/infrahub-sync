"""Declared-package builders shared by the accumulating-validation tests."""

from __future__ import annotations

import copy
from typing import Any

from infrahub_sync.configuration import ConfigurationPackage

_VALID_CONTENT: dict[str, Any] = {
    "format_version": 1,
    "configuration": {
        "name": "from-netbox",
        "source": {
            "name": "netbox",
            "settings": {"url": "https://demo.netbox.dev", "token": {"$credential": "netbox-token"}},
        },
        "destination": {
            "name": "infrahub",
            "settings": {"url": "http://localhost:8000", "token": {"$credential": "infrahub-token"}},
        },
    },
    "credentials": {
        "netbox-token": {"provider": "env", "identifier": "INFRAHUB_SYNC_CREDENTIAL_NETBOX_TOKEN"},
        "infrahub-token": {"provider": "env", "identifier": "INFRAHUB_SYNC_CREDENTIAL_INFRAHUB_API_TOKEN"},
    },
}

# The same package in the format of spec 027: each block names a stored credential and
# declares no credential setting, and the package has no ``credentials`` block.
SOURCE_CREDENTIAL = "netbox-token"
DESTINATION_CREDENTIAL = "infrahub-token"
_VALID_CREDENTIAL_CONTENT: dict[str, Any] = {
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


def package_data() -> dict[str, Any]:
    """Return one valid declared package as mutable JSON-native content."""
    return copy.deepcopy(_VALID_CONTENT)


def package(data: dict[str, Any] | None = None) -> ConfigurationPackage:
    """Parse declared content into a package, defaulting to the valid one."""
    return ConfigurationPackage.model_validate(package_data() if data is None else data)


def credential_package_data() -> dict[str, Any]:
    """Return one valid declared package whose blocks each name a stored credential, as mutable content."""
    return copy.deepcopy(_VALID_CREDENTIAL_CONTENT)


def credential_package(data: dict[str, Any] | None = None) -> ConfigurationPackage:
    """Parse declared content into a package, defaulting to the valid one that names stored credentials."""
    return ConfigurationPackage.model_validate(credential_package_data() if data is None else data)
