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
_ADAPTER_ROLES: frozenset[AdapterRole] = frozenset({"source", "destination"})
_WRITE_OPERATIONS: frozenset[WriteOperation] = frozenset({"create", "update", "delete"})
_SETTING_NAME = re.compile(r"^[a-z][a-z0-9_]*$")


@dataclass(frozen=True, slots=True)
class AdapterConfigurationCapabilities:
    """Connection-free configuration facts supplied by one adapter contract."""

    adapter_name: str
    roles: frozenset[AdapterRole]
    allowed_settings: frozenset[str] = frozenset()
    credential_setting_paths: tuple[str, ...] = ()
    supported_destination_write_operations: frozenset[WriteOperation] = frozenset()
    destination_schema_validation: bool = False
    validator: ConfigurationValidator | None = None
    contract_version: Literal[1] = 1

    def __post_init__(self) -> None:
        roles = frozenset(self.roles)
        allowed_settings = frozenset(self.allowed_settings)
        credential_setting_paths = tuple(self.credential_setting_paths)
        write_operations = frozenset(self.supported_destination_write_operations)
        object.__setattr__(self, "roles", roles)
        object.__setattr__(self, "allowed_settings", allowed_settings)
        object.__setattr__(self, "credential_setting_paths", credential_setting_paths)
        object.__setattr__(self, "supported_destination_write_operations", write_operations)

        if _ADAPTER_NAME.fullmatch(self.adapter_name) is None:
            msg = "adapter_name must be a lowercase configuration name"
            raise ValueError(msg)
        unsupported_roles = roles - _ADAPTER_ROLES
        if unsupported_roles:
            msg = f"adapter {self.adapter_name!r} contains unsupported roles: {sorted(unsupported_roles)!r}"
            raise ValueError(msg)
        unsupported_write_operations = write_operations - _WRITE_OPERATIONS
        if unsupported_write_operations:
            msg = (
                f"adapter {self.adapter_name!r} contains unsupported destination write operations: "
                f"{sorted(unsupported_write_operations)!r}"
            )
            raise ValueError(msg)
        if not roles:
            msg = f"adapter {self.adapter_name!r} must declare at least one role"
            raise ValueError(msg)
        if "destination" not in roles and write_operations:
            msg = f"source-only adapter {self.adapter_name!r} cannot declare destination writes"
            raise ValueError(msg)
        if len(credential_setting_paths) != len(set(credential_setting_paths)):
            msg = f"adapter {self.adapter_name!r} contains duplicate credential paths"
            raise ValueError(msg)
        if any(
            not isinstance(setting, str) or _SETTING_NAME.fullmatch(setting) is None for setting in allowed_settings
        ):
            msg = f"adapter {self.adapter_name!r} contains an invalid allowed setting"
            raise ValueError(msg)
        if any(not path or path.startswith(".") or path.endswith(".") for path in credential_setting_paths):
            msg = f"adapter {self.adapter_name!r} contains an invalid credential path"
            raise ValueError(msg)
        unsupported_credential_paths = sorted(
            path for path in credential_setting_paths if path.partition(".")[0] not in allowed_settings
        )
        if unsupported_credential_paths:
            msg = f"adapter {self.adapter_name!r} contains credential paths outside allowed settings"
            raise ValueError(msg)


_SOURCE_ONLY: frozenset[AdapterRole] = frozenset({"source"})
_BOTH: frozenset[AdapterRole] = frozenset({"source", "destination"})
_CREATE_UPDATE: frozenset[WriteOperation] = frozenset({"create", "update"})


_GENERIC_REST_SETTINGS = frozenset(
    {
        "api_endpoint",
        "auth_method",
        "password",
        "password_env_vars",
        "response_key_pattern",
        "timeout",
        "token",
        "token_env_vars",
        "url",
        "url_env_vars",
        "username",
        "username_env_vars",
        "verify_ssl",
    }
)


BUILTIN_ADAPTER_CAPABILITIES = MappingProxyType(
    {
        "aci": AdapterConfigurationCapabilities(
            adapter_name="aci",
            roles=_SOURCE_ONLY,
            allowed_settings=frozenset({"api_endpoint", "password", "url", "username", "verify"}),
            credential_setting_paths=("username", "password"),
        ),
        "genericrestapi": AdapterConfigurationCapabilities(
            adapter_name="genericrestapi",
            roles=_SOURCE_ONLY,
            allowed_settings=_GENERIC_REST_SETTINGS,
            credential_setting_paths=("token", "username", "password"),
        ),
        "infrahub": AdapterConfigurationCapabilities(
            adapter_name="infrahub",
            roles=_BOTH,
            allowed_settings=frozenset({"branch", "owner", "source", "token", "url", "verify_ssl"}),
            credential_setting_paths=("token",),
            supported_destination_write_operations=_CREATE_UPDATE,
            destination_schema_validation=True,
        ),
        "ipfabricsync": AdapterConfigurationCapabilities(
            adapter_name="ipfabricsync",
            roles=_SOURCE_ONLY,
            allowed_settings=frozenset({"auth", "base_url", "verify_ssl"}),
            credential_setting_paths=("auth",),
        ),
        "nautobot": AdapterConfigurationCapabilities(
            adapter_name="nautobot",
            roles=_SOURCE_ONLY,
            allowed_settings=frozenset({"token", "url", "verify_ssl"}),
            credential_setting_paths=("token",),
        ),
        "netbox": AdapterConfigurationCapabilities(
            adapter_name="netbox",
            roles=_SOURCE_ONLY,
            allowed_settings=frozenset({"token", "url", "verify_ssl"}),
            credential_setting_paths=("token",),
        ),
        "peeringmanager": AdapterConfigurationCapabilities(
            adapter_name="peeringmanager",
            roles=_BOTH,
            allowed_settings=_GENERIC_REST_SETTINGS,
            credential_setting_paths=("token", "username", "password"),
            supported_destination_write_operations=_CREATE_UPDATE,
        ),
        "prometheus": AdapterConfigurationCapabilities(
            adapter_name="prometheus",
            roles=_SOURCE_ONLY,
            allowed_settings=frozenset(
                {
                    "auth_method",
                    "endpoint",
                    "mode",
                    "password",
                    "promql",
                    "timeout",
                    "token",
                    "url",
                    "username",
                    "verify_ssl",
                }
            ),
            credential_setting_paths=("token", "username", "password"),
        ),
        "slurpitsync": AdapterConfigurationCapabilities(
            adapter_name="slurpitsync",
            roles=_SOURCE_ONLY,
            allowed_settings=frozenset({"api_key", "token", "url", "verify_ssl"}),
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
