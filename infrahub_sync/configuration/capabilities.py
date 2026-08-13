"""Versioned static declarations for bundled adapter configuration behavior."""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal

from .models import ConfigurationPackage, ValidationFinding

AdapterRole = Literal["source", "destination"]
WriteOperation = Literal["create", "update", "delete"]
ConfigurationValidator = Callable[[ConfigurationPackage, AdapterRole], Sequence[ValidationFinding]]
_ADAPTER_NAME = re.compile(r"^[a-z][a-z0-9_-]*$")


@dataclass(frozen=True, slots=True)
class AdapterConfigurationCapabilities:
    """Connection-free configuration facts supplied by one adapter contract."""

    adapter_name: str
    roles: frozenset[AdapterRole]
    credential_setting_paths: tuple[str, ...] = ()
    supported_destination_write_operations: frozenset[WriteOperation] = frozenset()
    destination_schema_validation: bool = False
    validator: ConfigurationValidator | None = None
    contract_version: Literal[1] = 1

    def __post_init__(self) -> None:
        if _ADAPTER_NAME.fullmatch(self.adapter_name) is None:
            msg = "adapter_name must be a lowercase configuration name"
            raise ValueError(msg)
        if not self.roles:
            msg = f"adapter {self.adapter_name!r} must declare at least one role"
            raise ValueError(msg)
        if "destination" not in self.roles and self.supported_destination_write_operations:
            msg = f"source-only adapter {self.adapter_name!r} cannot declare destination writes"
            raise ValueError(msg)
        if len(self.credential_setting_paths) != len(set(self.credential_setting_paths)):
            msg = f"adapter {self.adapter_name!r} contains duplicate credential paths"
            raise ValueError(msg)
        if any(not path or path.startswith(".") or path.endswith(".") for path in self.credential_setting_paths):
            msg = f"adapter {self.adapter_name!r} contains an invalid credential path"
            raise ValueError(msg)


_SOURCE_ONLY: frozenset[AdapterRole] = frozenset({"source"})
_BOTH: frozenset[AdapterRole] = frozenset({"source", "destination"})
_CREATE_UPDATE: frozenset[WriteOperation] = frozenset({"create", "update"})

BUILTIN_ADAPTER_CAPABILITIES = MappingProxyType(
    {
        "aci": AdapterConfigurationCapabilities(
            adapter_name="aci",
            roles=_SOURCE_ONLY,
            credential_setting_paths=("username", "password"),
        ),
        "genericrestapi": AdapterConfigurationCapabilities(
            adapter_name="genericrestapi",
            roles=_SOURCE_ONLY,
            credential_setting_paths=("token", "username", "password"),
        ),
        "infrahub": AdapterConfigurationCapabilities(
            adapter_name="infrahub",
            roles=_BOTH,
            credential_setting_paths=("token",),
            supported_destination_write_operations=_CREATE_UPDATE,
            destination_schema_validation=True,
        ),
        "ipfabricsync": AdapterConfigurationCapabilities(
            adapter_name="ipfabricsync",
            roles=_SOURCE_ONLY,
            credential_setting_paths=("auth",),
        ),
        "nautobot": AdapterConfigurationCapabilities(
            adapter_name="nautobot",
            roles=_SOURCE_ONLY,
            credential_setting_paths=("token",),
        ),
        "netbox": AdapterConfigurationCapabilities(
            adapter_name="netbox",
            roles=_SOURCE_ONLY,
            credential_setting_paths=("token",),
        ),
        "peeringmanager": AdapterConfigurationCapabilities(
            adapter_name="peeringmanager",
            roles=_BOTH,
            credential_setting_paths=("token", "username", "password"),
            supported_destination_write_operations=_CREATE_UPDATE,
        ),
        "prometheus": AdapterConfigurationCapabilities(
            adapter_name="prometheus",
            roles=_SOURCE_ONLY,
            credential_setting_paths=("token", "username", "password"),
        ),
        "slurpitsync": AdapterConfigurationCapabilities(
            adapter_name="slurpitsync",
            roles=_SOURCE_ONLY,
            credential_setting_paths=("api_key", "token"),
        ),
    }
)


class UnknownAdapterCapabilitiesError(ValueError):
    """An adapter has no safe declared configuration boundary."""


def get_adapter_capabilities(name: str) -> AdapterConfigurationCapabilities:
    """Return the bundled declaration for an exact normalized adapter name."""
    try:
        return BUILTIN_ADAPTER_CAPABILITIES[name.casefold()]
    except KeyError:
        msg = f"adapter {name!r} has no configuration capability declaration"
        raise UnknownAdapterCapabilitiesError(msg) from None
