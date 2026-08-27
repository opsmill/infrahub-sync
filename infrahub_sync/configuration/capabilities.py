"""Versioned static declarations for bundled adapter configuration behavior."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal
from urllib.parse import urlsplit

from .models import _SETTING_NAME, ConfigurationPackage, ValidationFinding, is_renderable_setting_path

AdapterRole = Literal["source", "destination"]
WriteOperation = Literal["create", "update", "delete"]
ConfigurationValidator = Callable[[ConfigurationPackage, AdapterRole], Sequence[ValidationFinding]]
# The destination-schema accessor contract: (package, branch) -> one JSON-native schema
# snapshot, mapping each kind name to its attributes (name -> attribute kind) and
# relationships (name -> {"peer", "cardinality"}). Raises DestinationSchemaReadError and
# nothing else for a read that fails; performs I/O only when called, never at import.
DestinationSchemaAccessor = Callable[[ConfigurationPackage, str], Mapping[str, Any]]
_SCHEMA_READ_REASON = re.compile(r"^[a-z]{1,32}$")
_ADAPTER_NAME = re.compile(r"^[a-z][a-z0-9_-]*$")
_ADAPTER_ROLES: frozenset[AdapterRole] = frozenset({"source", "destination"})
_WRITE_OPERATIONS: frozenset[WriteOperation] = frozenset({"create", "update", "delete"})


class DestinationSchemaReadError(Exception):
    """An accessor could not read the destination schema, classified by ``reason``.

    ``reason`` is a short lowercase word naming the failure class ("timeout",
    "unauthorized", "unreachable", ...) and is validated here so the finding a
    failed read becomes can carry it verbatim. The message follows the module's
    own rule for refusal text: exception type names, never third-party content.
    """

    def __init__(self, message: str, *, reason: str) -> None:
        if _SCHEMA_READ_REASON.fullmatch(reason) is None:
            msg = f"schema read failure reason {reason!r} is not a short lowercase word"
            raise ValueError(msg)
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class AdapterConfigurationCapabilities:
    """Connection-free configuration facts supplied by one adapter contract."""

    adapter_name: str
    roles: frozenset[AdapterRole]
    allowed_settings: frozenset[str] = frozenset()
    credential_setting_paths: tuple[str, ...] = ()
    supported_destination_write_operations: frozenset[WriteOperation] = frozenset()
    destination_schema_validation: bool = False
    destination_schema_accessor: DestinationSchemaAccessor | None = None
    # Whether this adapter implements incremental extraction (the cursor_tier_for /
    # list_changed_since overrides). A package declaring `incremental:` against a source
    # that does not is warned about the unqualified optional feature; the conformance
    # tests hold this flag to the runtime overrides so it cannot drift.
    incremental_extraction: bool = False
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
        if self.destination_schema_validation != (self.destination_schema_accessor is not None):
            # The declaration and the accessor are one fact: declaring schema validation
            # without a working accessor — or the reverse — is a registration-time error.
            msg = (
                f"adapter {self.adapter_name!r} must declare destination schema validation "
                "together with its schema accessor"
            )
            raise ValueError(msg)
        if len(credential_setting_paths) != len(set(credential_setting_paths)):
            msg = f"adapter {self.adapter_name!r} contains duplicate credential paths"
            raise ValueError(msg)
        if any(
            not isinstance(setting, str) or _SETTING_NAME.fullmatch(setting) is None for setting in allowed_settings
        ):
            msg = f"adapter {self.adapter_name!r} contains an invalid allowed setting"
            raise ValueError(msg)
        if any(not is_renderable_setting_path(path) for path in credential_setting_paths):
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
        "response_key_pattern",
        "timeout",
        "token",
        "url",
        "username",
        "verify_ssl",
    }
)


def _validate_relative_rest_mapping_endpoints(
    package: ConfigurationPackage,
    role: AdapterRole,
) -> tuple[ValidationFinding, ...]:
    """Refuse schema mappings that can carry request authority or inline query values."""
    adapter = package.configuration.source if role == "source" else package.configuration.destination
    findings = []
    for index, mapping in enumerate(package.configuration.schema_mapping):
        endpoint = mapping.mapping
        if not endpoint:
            continue
        try:
            parsed = urlsplit(endpoint)
        except ValueError:
            parsed = None
        if parsed is not None and not (parsed.scheme or parsed.netloc or parsed.query or parsed.fragment):
            continue
        findings.append(
            ValidationFinding(
                code="unsafe-rest-request-endpoint",
                severity="error",
                location=f"/configuration/schema_mapping/{index}/mapping",
                message=(
                    f"{adapter.name} schema mapping endpoints must be a relative request path without authority, "
                    "user information, query parameters, or fragments"
                ),
            )
        )
    return tuple(findings)


def _resolved_client_settings(package: ConfigurationPackage, branch: str) -> tuple[str, dict[str, Any]]:
    """Resolve the declared destination settings one schema read needs.

    Returns the declared url and the SDK configuration built from the declared settings,
    refusing — as :class:`DestinationSchemaReadError` — a missing url and a declared token
    credential that does not resolve.
    """
    # Imported here, not at module load, for the same reason as the client imports below.
    # pylint: disable-next=import-outside-toplevel
    from .credentials import CredentialConfigurationError, resolve_reference

    settings = package.configuration.destination.settings or {}
    url = settings.get("url")
    if not isinstance(url, str) or not url:
        msg = "destination setting 'url' is required to read the destination schema"
        raise DestinationSchemaReadError(msg, reason="unconfigured")
    sdk_config: dict[str, Any] = {"timeout": 60, "default_branch": branch}
    token = settings.get("token")
    if isinstance(token, Mapping):
        reference_name = token.get("$credential")
        if isinstance(reference_name, str):
            try:
                sdk_config["api_token"] = resolve_reference(package, reference_name)
            except CredentialConfigurationError as exc:
                msg = f"destination token credential could not be resolved: {type(exc).__name__}"
                raise DestinationSchemaReadError(msg, reason="credentials") from None
    verify_ssl = settings.get("verify_ssl")
    if verify_ssl is not None:
        sdk_config["tls_insecure"] = not verify_ssl
    return url, sdk_config


def _read_infrahub_destination_schema(package: ConfigurationPackage, branch: str) -> Mapping[str, Any]:
    """Read one destination schema snapshot from a live Infrahub server.

    The bundled accessor behind ``infrahub``'s schema-validation declaration. Only the
    explicit validation opt-in calls it, so resolving the *declared* token credential
    reference here is the sanctioned read (contract section 4); the branch arrives already
    resolved from the declared setting and nothing ambient is consulted for it. Every
    SDK-raised error (``infrahub_sdk.exceptions.Error`` and its subclasses), every HTTP
    transport or status failure, an unresolvable declared credential, and a declared
    client configuration the SDK refuses (``ValueError``, including pydantic's validation
    error) is classified into :class:`DestinationSchemaReadError` — the accessor contract
    the schema checks handle — carrying exception type names, never third-party content.
    """
    # Imported here, not at module load: this module stays connection-free to import, and
    # the default validate path never touches a network client (envelope AR7).
    # pylint: disable=import-outside-toplevel
    import httpx
    from infrahub_sdk import Config, InfrahubClientSync
    from infrahub_sdk.exceptions import (
        AuthenticationError,
        ServerNotReachableError,
        ServerNotResponsiveError,
    )
    from infrahub_sdk.exceptions import (
        Error as InfrahubSdkError,
    )

    # pylint: enable=import-outside-toplevel
    url, sdk_config = _resolved_client_settings(package, branch)
    try:
        client = InfrahubClientSync(address=url, config=Config(**sdk_config))
        schema = client.schema.all(branch=branch)
    except (httpx.TimeoutException, ServerNotResponsiveError) as exc:
        msg = f"destination schema read timed out: {type(exc).__name__}"
        raise DestinationSchemaReadError(msg, reason="timeout") from None
    except AuthenticationError as exc:
        msg = f"destination refused the schema read credentials: {type(exc).__name__}"
        raise DestinationSchemaReadError(msg, reason="unauthorized") from None
    except httpx.HTTPStatusError as exc:
        unauthorized = exc.response.status_code in {401, 403}
        reason = "unauthorized" if unauthorized else "unreachable"
        msg = f"destination refused the schema read: {type(exc).__name__}"
        raise DestinationSchemaReadError(msg, reason=reason) from None
    except (httpx.TransportError, ServerNotReachableError) as exc:
        msg = f"destination server could not be reached: {type(exc).__name__}"
        raise DestinationSchemaReadError(msg, reason="unreachable") from None
    except InfrahubSdkError as exc:
        # The SDK's own base error: everything it raises that the arms above did not
        # classify (a missing branch, a GraphQL refusal, undecodable content) is still a
        # read the destination rejected, never an untyped escape (review F1).
        msg = f"destination rejected the schema read: {type(exc).__name__}"
        raise DestinationSchemaReadError(msg, reason="rejected") from None
    except ValueError as exc:
        # Config(**sdk_config) refusing the declared settings: pydantic's ValidationError
        # subclasses ValueError, so both land here as one unusable-configuration class.
        msg = f"destination client could not be configured from the declared settings: {type(exc).__name__}"
        raise DestinationSchemaReadError(msg, reason="unconfigured") from None
    return {
        kind: {
            "attributes": {attribute.name: attribute.kind for attribute in getattr(node, "attributes", ()) or ()},
            "relationships": {
                relationship.name: {"peer": relationship.peer, "cardinality": relationship.cardinality}
                for relationship in getattr(node, "relationships", ()) or ()
            },
        }
        for kind, node in schema.items()
    }


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
            validator=_validate_relative_rest_mapping_endpoints,
        ),
        "infrahub": AdapterConfigurationCapabilities(
            adapter_name="infrahub",
            roles=_BOTH,
            allowed_settings=frozenset({"branch", "owner", "source", "token", "url", "verify_ssl"}),
            credential_setting_paths=("token",),
            supported_destination_write_operations=_CREATE_UPDATE,
            destination_schema_validation=True,
            destination_schema_accessor=_read_infrahub_destination_schema,
            incremental_extraction=True,
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
            incremental_extraction=True,
        ),
        "netbox": AdapterConfigurationCapabilities(
            adapter_name="netbox",
            roles=_SOURCE_ONLY,
            allowed_settings=frozenset({"token", "url", "verify_ssl"}),
            credential_setting_paths=("token",),
            incremental_extraction=True,
        ),
        "peeringmanager": AdapterConfigurationCapabilities(
            adapter_name="peeringmanager",
            roles=_BOTH,
            allowed_settings=_GENERIC_REST_SETTINGS,
            credential_setting_paths=("token", "username", "password"),
            supported_destination_write_operations=frozenset({"update"}),
            validator=_validate_relative_rest_mapping_endpoints,
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
    """Return the bundled declaration for an exact registered adapter name."""
    # Exact, not case-folded: every consumer resolves the name verbatim (module import,
    # PluginLoader camelization, the destination check in the CLI), and the declared name is
    # hashed as written, so a folded match would admit names that split package identity.
    try:
        return BUILTIN_ADAPTER_CAPABILITIES[name]
    except KeyError:
        msg = f"adapter {name!r} has no configuration capability declaration"
        raise UnknownAdapterCapabilitiesError(msg) from None
