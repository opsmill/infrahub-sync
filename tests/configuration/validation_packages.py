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
        "netbox-token": {"provider": "env", "identifier": "NETBOX_TOKEN"},
        "infrahub-token": {"provider": "env", "identifier": "INFRAHUB_API_TOKEN"},
    },
}


def package_data() -> dict[str, Any]:
    """Return one valid declared package as mutable JSON-native content."""
    return copy.deepcopy(_VALID_CONTENT)


def package(data: dict[str, Any] | None = None) -> ConfigurationPackage:
    """Parse declared content into a package, defaulting to the valid one."""
    return ConfigurationPackage.model_validate(package_data() if data is None else data)
