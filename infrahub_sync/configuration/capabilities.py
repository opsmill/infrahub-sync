"""Versioned static declarations for bundled adapter configuration behavior."""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Any, Literal
from urllib.parse import urlsplit

from .models import _SETTING_NAME, ConfigurationPackage, ValidationFinding, is_renderable_setting_path

AdapterRole = Literal["source", "destination"]
WriteOperation = Literal["create", "update", "delete"]
ConfigurationValidator = Callable[[ConfigurationPackage, AdapterRole], Sequence[ValidationFinding]]
# The destination-schema accessor contract: (package, branch) -> one JSON-native schema
# snapshot, mapping each kind name to its ordered "human_friendly_id" and
# "uniqueness_constraints" component paths, its attributes
# (name -> {"kind", "optional", "default_value", "unique"}), and its relationships
# (name -> {"peer", "cardinality", "optional", "kind"}). Raises DestinationSchemaReadError
# and nothing else for a read that fails; performs I/O only when called, never at import.
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
    transport or status failure, an unresolvable declared credential, a declared
    client configuration the SDK refuses (``ValueError``, including pydantic's validation
    error), and a response the call accepted but that cannot be normalized into a schema
    snapshot is classified into :class:`DestinationSchemaReadError` — the accessor contract
    the schema checks handle. The SDK-call arms carry exception type names, never
    third-party content; a normalization failure carries one fixed message
    (:func:`_normalized_schema_snapshot`).
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
    return _normalized_schema_snapshot(schema)


# The one message every normalization failure carries. Fixed, so a hostile response puts
# nothing into it: not an exception type name — a metaclass executes on that read — and
# never third-party exception text.
_UNUSABLE_SCHEMA_RESPONSE = "destination returned an unusable schema response"


def _normalized_schema_snapshot(schema: object) -> Mapping[str, Any]:
    """Normalize one client schema response inside one total typed boundary.

    A response the client call accepted but that is not a usable schema is still a read
    the destination rejected. The entire normalization — the root check, iterating the
    members, reading their attribute and relationship shapes, and validating the built
    snapshot — runs under one rule: any ordinary ``Exception`` it raises (``items()``
    raising, a raising attribute property, raising iteration — whatever a third-party
    response can do) becomes one *new* :class:`DestinationSchemaReadError` carrying the
    fixed message and ``reason="rejected"``. The rejection reads nothing from the
    exception — not its type, whose ``__name__`` a hostile metaclass executes on — and a
    ``DestinationSchemaReadError`` raised during normalization is rewrapped rather than
    passed through, so untrusted response code cannot forge its own reason.
    ``BaseException`` still propagates.
    """
    try:
        return _build_schema_snapshot(schema)
    except Exception:  # noqa: BLE001  # pylint: disable=broad-exception-caught  # The totality rule: re-raised typed, nothing read.
        raise DestinationSchemaReadError(_UNUSABLE_SCHEMA_RESPONSE, reason="rejected") from None


def _build_schema_snapshot(schema: object) -> dict[str, Any]:
    """Build the snapshot from a third-party response, inside the boundary above.

    Each kind carries its ordered ``human_friendly_id`` and ``uniqueness_constraints``
    component paths and, per member, every property that can change a constructed
    runtime model or a planned write. Nothing else from the response crosses.
    """
    if not isinstance(schema, Mapping):
        raise DestinationSchemaReadError(_UNUSABLE_SCHEMA_RESPONSE, reason="rejected")
    snapshot: dict[str, Any] = {}
    for kind, node in schema.items():
        if not isinstance(kind, str):
            raise DestinationSchemaReadError(_UNUSABLE_SCHEMA_RESPONSE, reason="rejected")
        # One namespace per kind, shared by both groups, so a relationship cannot reuse an
        # attribute's name any more than another attribute can.
        claimed: set[str] = set()
        snapshot[kind] = {
            "human_friendly_id": _optional_string_path(getattr(node, "human_friendly_id", None)),
            "uniqueness_constraints": _optional_string_paths(getattr(node, "uniqueness_constraints", None)),
            "attributes": _collected_members(
                getattr(node, "attributes", None), claimed=claimed, shape=_attribute_shape
            ),
            "relationships": _collected_members(
                getattr(node, "relationships", None), claimed=claimed, shape=_relationship_shape
            ),
        }
    _require_usable_snapshot(snapshot)
    return snapshot


def _attribute_shape(attribute: Any) -> dict[str, Any]:
    """The attribute properties that can change a runtime model or a planned write."""
    return {
        "kind": _member_text(attribute.kind),
        "optional": _exact_bool(attribute.optional),
        "default_value": _json_native_default(attribute.default_value),
        "unique": _exact_bool(attribute.unique),
    }


def _relationship_shape(relationship: Any) -> dict[str, Any]:
    """The relationship properties that can change a runtime model or a planned write."""
    return {
        "peer": relationship.peer,
        "cardinality": _member_text(relationship.cardinality),
        "optional": _exact_bool(relationship.optional),
        "kind": _member_text(relationship.kind),
    }


def _collected_members(
    members: Any,
    *,
    claimed: set[str],
    shape: Callable[[Any], dict[str, Any]],
) -> dict[str, Any]:
    """Collect one kind's members by name, refusing a name that cannot mean one member.

    A dict comprehension over a third-party response keeps the **last** of two members
    sharing a name, silently. The typed SDK admits that — duplicate attribute names,
    duplicate relationship names, and an attribute and a relationship sharing one name are
    all constructible — so the model built, the fingerprint computed from it, and every
    write planned against it would depend on the order the destination happened to answer
    in. A name is claimed once per kind across both groups, and a second claim is a
    response this adapter cannot read rather than a choice to make.

    A member name also becomes a model field name, a plan payload key, and text in logs and
    refusals, so a name carrying control, format, or separator characters — or no name at
    all — is refused here for the same reason: it is not a name Sync can carry through to
    those places intact.
    """
    collected: dict[str, Any] = {}
    for member in members or ():
        name = member.name
        if not isinstance(name, str) or not name or not name.isprintable():
            raise DestinationSchemaReadError(_UNUSABLE_SCHEMA_RESPONSE, reason="rejected")
        if name in claimed:
            raise DestinationSchemaReadError(_UNUSABLE_SCHEMA_RESPONSE, reason="rejected")
        claimed.add(name)
        collected[name] = shape(member)
    return collected


def _optional_string_path(value: object) -> list[str]:
    """Copy an optional SDK component path without coercing malformed containers."""
    if value is None:
        return []
    return _string_path(value)


def _optional_string_paths(value: object) -> list[list[str]]:
    """Copy optional SDK component paths without coercing malformed containers."""
    if value is None:
        return []
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise DestinationSchemaReadError(_UNUSABLE_SCHEMA_RESPONSE, reason="rejected")
    return [_string_path(path) for path in value]


def _string_path(value: object) -> list[str]:
    """Copy one non-string SDK sequence containing only string components."""
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        raise DestinationSchemaReadError(_UNUSABLE_SCHEMA_RESPONSE, reason="rejected")
    components: list[str] = []
    for component in value:
        if not isinstance(component, str):
            raise DestinationSchemaReadError(_UNUSABLE_SCHEMA_RESPONSE, reason="rejected")
        components.append(component)
    return components


def _exact_bool(value: object) -> bool:
    """Return an SDK flag only when it is an exact boolean."""
    if value is True:
        return True
    if value is False:
        return False
    raise DestinationSchemaReadError(_UNUSABLE_SCHEMA_RESPONSE, reason="rejected")


def _member_text(value: object) -> object:
    """Return the value of an SDK string enum, leaving anything else to the shape check."""
    return value.value if isinstance(value, Enum) else value


def _json_native_default(value: object) -> Any:
    """Keep a JSON-native declared default; refuse anything a model cannot reproduce.

    A non-finite float is refused here rather than carried: JSON has no encoding for it,
    so it could not survive the canonical projection a plan is identified by.
    """
    if isinstance(value, float) and not math.isfinite(value):
        raise DestinationSchemaReadError(_UNUSABLE_SCHEMA_RESPONSE, reason="rejected")
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, Enum):
        return _json_native_default(value.value)
    if isinstance(value, (list, tuple)):
        return [_json_native_default(item) for item in value]
    if isinstance(value, Mapping) and all(isinstance(key, str) for key in value):
        return {key: _json_native_default(item) for key, item in value.items()}
    raise DestinationSchemaReadError(_UNUSABLE_SCHEMA_RESPONSE, reason="rejected")


def _require_usable_snapshot(snapshot: Mapping[str, Any]) -> None:
    """Refuse a built snapshot that is not the string shape its consumers expect.

    The last step inside the normalization boundary: the members were read without
    raising, every kind is a string and `_collected_members` has already settled every
    member *name*, but the snapshot is usable only when each declared text property is a
    string too — the shape the SDK contract promises and the content checks and the
    normalized runtime domain rely on.
    """
    for entry in snapshot.values():
        attributes: dict[str, Any] = entry["attributes"]
        relationships: dict[str, Any] = entry["relationships"]
        paths: list[Any] = [
            *entry["human_friendly_id"],
            *(component for constraint in entry["uniqueness_constraints"] for component in constraint),
        ]
        usable = (
            all(isinstance(attribute["kind"], str) for attribute in attributes.values())
            and all(
                isinstance(relationship["peer"], str)
                and isinstance(relationship["cardinality"], str)
                and isinstance(relationship["kind"], str)
                for relationship in relationships.values()
            )
            and all(isinstance(component, str) for component in paths)
        )
        if not usable:
            msg = "destination returned an unusable schema member shape"
            raise DestinationSchemaReadError(msg, reason="rejected")


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
