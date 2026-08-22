"""Credential-reference validation and runtime provider contracts."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Protocol, cast
from urllib.parse import urlsplit

from pydantic import ValidationError

from .capabilities import AdapterConfigurationCapabilities, AdapterRole, get_adapter_capabilities
from .models import (
    ConfigurationPackage,
    CredentialReference,
    CredentialReferenceNode,
    safe_pointer_component,
    sort_findings,
)

_ENV_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True, slots=True)
class _StoreCapabilities:
    """Connection-free configuration facts for one bundled store type."""

    allowed_settings: frozenset[str]
    credential_setting_paths: tuple[str, ...]

    def __post_init__(self) -> None:
        outside = sorted(set(self.credential_setting_paths) - self.allowed_settings)
        if outside:
            msg = f"store credential paths outside allowed settings: {outside!r}"
            raise ValueError(msg)


# One entry per store type. Two parallel tables let a new type be added to only one of them,
# which surfaces as an uncaught KeyError rather than a refusal.
_STORE_CAPABILITIES: dict[str, _StoreCapabilities] = {
    "redis": _StoreCapabilities(
        allowed_settings=frozenset({"db", "host", "password", "port", "store_id", "url", "username"}),
        credential_setting_paths=("url", "username", "password"),
    ),
}
# Absolute settings name where to connect; relative ones name a path beneath it. Applying one
# rule to both would refuse every legitimate relative endpoint.
_ABSOLUTE_URL_SETTING_NAMES = frozenset({"base_url", "url"})
_RELATIVE_PATH_SETTING_NAMES = frozenset({"api_endpoint", "endpoint"})
_URL_SETTING_NAMES = _ABSOLUTE_URL_SETTING_NAMES | _RELATIVE_PATH_SETTING_NAMES
_ALLOWED_URL_SCHEMES = frozenset({"http", "https"})
# Diagnostics are rendered from declared keys, so they inherit the declaration's size unless
# bounded here the way models.py bounds its own parse diagnostics.
_MAX_DIAGNOSTIC_COMPONENT_LENGTH = 64
_MAX_DIAGNOSTIC_LOCATION_LENGTH = 256
_MAX_DIAGNOSTIC_NAME_ENTRIES = 16
_TRUNCATION_MARKER = "..."


class CredentialConfigurationError(ValueError):
    """A declared package contains an unsafe or unresolved credential shape."""


def _bounded_component(name: object) -> str:
    """Escape one declared key for display, bounded independently of the declaration."""
    text = str(name)
    if len(text) > _MAX_DIAGNOSTIC_COMPONENT_LENGTH:
        return safe_pointer_component(text[:_MAX_DIAGNOSTIC_COMPONENT_LENGTH]) + _TRUNCATION_MARKER
    return safe_pointer_component(text)


def _bounded_location(location: str) -> str:
    """Bound one accumulated pointer so nesting depth cannot grow the message."""
    if len(location) <= _MAX_DIAGNOSTIC_LOCATION_LENGTH:
        return location
    return location[:_MAX_DIAGNOSTIC_LOCATION_LENGTH] + _TRUNCATION_MARKER


def _render_setting_name_list(names: Iterable[str]) -> str:
    """Render bounded escaped setting names with unambiguous, JSON-decodable boundaries."""
    escaped_names = sorted(_bounded_component(name) for name in names)
    listed = escaped_names[:_MAX_DIAGNOSTIC_NAME_ENTRIES]
    rendered = json.dumps(listed, ensure_ascii=True)
    omitted = len(escaped_names) - len(listed)
    return f"{rendered} and {omitted} more" if omitted else rendered


class CredentialProvider(Protocol):
    """Resolve one non-secret identifier inside a worker-owned runtime."""

    def resolve(self, identifier: str) -> str:
        """Return the non-empty credential value or raise a safe error."""


class EnvironmentCredentialProvider:
    """Resolve credentials from exact environment-variable identifiers."""

    def __init__(self, environment: Mapping[str, str] | None = None) -> None:
        self._environment = os.environ if environment is None else environment

    def resolve(self, identifier: str) -> str:
        """Return a non-empty environment value without including it in errors."""
        if _ENV_IDENTIFIER.fullmatch(identifier) is None:
            msg = f"environment credential identifier {identifier!r} is invalid"
            raise CredentialConfigurationError(msg)
        value = self._environment.get(identifier)
        if value is None or not value:
            msg = f"environment credential {identifier!r} is missing or empty"
            raise CredentialConfigurationError(msg)
        return value


def provider_for(reference: CredentialReference, *, environment: Mapping[str, str] | None = None) -> CredentialProvider:
    """Return the shipped provider for a declared reference."""
    if reference.provider == "env":
        return EnvironmentCredentialProvider(environment)
    msg = f"credential provider {reference.provider!r} is not installed"
    raise CredentialConfigurationError(msg)


def _validate_reference_declarations(package: ConfigurationPackage) -> None:
    """Validate provider names and identifiers without resolving credential values."""
    for name, reference in package.credentials.items():
        if reference.provider != "env":
            msg = f"credential reference {name!r} uses provider {reference.provider!r}, which is not installed"
            raise CredentialConfigurationError(msg)
        if _ENV_IDENTIFIER.fullmatch(reference.identifier) is None:
            msg = f"credential reference {name!r} has an invalid environment identifier"
            raise CredentialConfigurationError(msg)


def _setting_at_path(settings: Mapping[str, object], path: str) -> tuple[bool, object | None]:
    current: object = settings
    for component in path.split("."):
        if not isinstance(current, Mapping) or component not in current:
            return False, None
        current = cast("Mapping[str, object]", current)[component]
    return True, current


def _reference_name(value: object, *, location: str) -> str:
    if not isinstance(value, Mapping) or "$credential" not in value:
        msg = f"{location} contains an inline credential value"
        raise CredentialConfigurationError(msg)
    try:
        node = CredentialReferenceNode.model_validate(value)
    except ValidationError:
        msg = f"{location} contains a malformed credential reference"
        raise CredentialConfigurationError(msg) from None
    return node.reference_name


def _allowed_reference_locations(
    package: ConfigurationPackage,
    source_capabilities: AdapterConfigurationCapabilities,
    destination_capabilities: AdapterConfigurationCapabilities,
) -> frozenset[str]:
    """Return every pointer at which a declared credential reference is resolved at run time."""
    allowed = {
        f"/configuration/{role}/settings/{path.replace('.', '/')}"
        for role, capabilities in (("source", source_capabilities), ("destination", destination_capabilities))
        for path in capabilities.credential_setting_paths
    }
    store = package.configuration.store
    store_capabilities = None if store is None else _STORE_CAPABILITIES.get(store.type)
    if store_capabilities is not None:
        allowed.update(
            f"/configuration/store/settings/{path.replace('.', '/')}"
            for path in store_capabilities.credential_setting_paths
        )
    return frozenset(allowed)


def _validate_all_reference_nodes(
    value: object,
    package: ConfigurationPackage,
    *,
    location: str,
    allowed_locations: frozenset[str],
) -> None:
    """Validate every use of the reserved ``$credential`` key without echoing values."""
    if isinstance(value, Mapping):
        if "$credential" in value:
            bounded = _bounded_location(location)
            if location not in allowed_locations:
                # Nothing resolves a reference here, so the adapter would receive the node itself.
                msg = f"{bounded} is not a credential-bearing setting"
                raise CredentialConfigurationError(msg)
            reference_name = _reference_name(value, location=bounded)
            if reference_name not in package.credentials:
                msg = f"{bounded} names unknown credential reference {reference_name!r}"
                raise CredentialConfigurationError(msg)
            return
        for key, item in value.items():
            escaped = _bounded_component(key)
            _validate_all_reference_nodes(
                item,
                package,
                location=f"{location}/{escaped}",
                allowed_locations=allowed_locations,
            )
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _validate_all_reference_nodes(
                item,
                package,
                location=f"{location}/{index}",
                allowed_locations=allowed_locations,
            )


def _validate_url_setting(value: object, *, setting_name: str, location: str) -> None:
    """Prove one declared endpoint setting carries no credential material and no scheme surprise."""
    if not isinstance(value, str):
        msg = f"{location} must be declared as a string"
        raise CredentialConfigurationError(msg)
    try:
        parsed = urlsplit(value)
    except ValueError:
        parsed = None
    if parsed is None or parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment:
        msg = f"{location} cannot contain user information, query parameters, or fragments"
        raise CredentialConfigurationError(msg)
    if setting_name in _ABSOLUTE_URL_SETTING_NAMES:
        if parsed.scheme not in _ALLOWED_URL_SCHEMES or not parsed.netloc:
            msg = f"{location} must be an absolute http or https URL"
            raise CredentialConfigurationError(msg)
    elif parsed.scheme or parsed.netloc:
        msg = f"{location} must be a relative request path without a scheme or authority"
        raise CredentialConfigurationError(msg)


def _validate_adapter_credentials(
    package: ConfigurationPackage,
    capabilities: AdapterConfigurationCapabilities,
    *,
    role: AdapterRole,
    settings: Mapping[str, object],
) -> None:
    if role not in capabilities.roles:
        msg = f"adapter {capabilities.adapter_name!r} does not support the {role} role"
        raise CredentialConfigurationError(msg)
    unsupported_settings = set(settings) - capabilities.allowed_settings
    if unsupported_settings:
        msg = (
            f"adapter {capabilities.adapter_name!r} contains unsupported declared settings for the {role} role: "
            f"{_render_setting_name_list(unsupported_settings)}"
        )
        raise CredentialConfigurationError(msg)
    for setting_name in sorted(capabilities.allowed_settings & _URL_SETTING_NAMES):
        value = settings.get(setting_name)
        if value is None:
            continue
        _validate_url_setting(
            value,
            setting_name=setting_name,
            location=f"/configuration/{role}/settings/{setting_name}",
        )
    if capabilities.validator is not None:
        findings = sort_findings(capabilities.validator(package, role))
        if findings:
            msg = "; ".join(f"{_bounded_location(finding.location)}: {finding.message}" for finding in findings)
            raise CredentialConfigurationError(msg)
    for path in capabilities.credential_setting_paths:
        present, value = _setting_at_path(settings, path)
        if not present or value is None:
            continue
        location = f"/configuration/{role}/settings/{path.replace('.', '/')}"
        reference_name = _reference_name(value, location=location)
        if reference_name not in package.credentials:
            msg = f"{location} names unknown credential reference {reference_name!r}"
            raise CredentialConfigurationError(msg)


def _validate_store_credentials(package: ConfigurationPackage) -> None:
    """Refuse inline values at credential-bearing store settings."""
    store = package.configuration.store
    if store is None:
        return
    settings = store.settings or {}
    capabilities = _STORE_CAPABILITIES.get(store.type)
    if capabilities is None:
        if settings:
            msg = f"store type {store.type!r} has no configuration capability declaration"
            raise CredentialConfigurationError(msg)
        return
    credential_paths = capabilities.credential_setting_paths
    unsupported_settings = set(settings) - capabilities.allowed_settings
    if unsupported_settings:
        msg = (
            f"store type {store.type!r} contains unsupported declared settings: "
            f"{_render_setting_name_list(unsupported_settings)}"
        )
        raise CredentialConfigurationError(msg)
    for path in credential_paths:
        present, value = _setting_at_path(settings, path)
        if not present or value is None:
            continue
        location = f"/configuration/store/settings/{path.replace('.', '/')}"
        reference_name = _reference_name(value, location=location)
        if reference_name not in package.credentials:
            msg = f"{location} names unknown credential reference {reference_name!r}"
            raise CredentialConfigurationError(msg)


def validate_package_credentials(package: ConfigurationPackage) -> None:
    """Prove bundled adapter settings contain references rather than credential values."""
    _validate_reference_declarations(package)
    source = package.configuration.source
    destination = package.configuration.destination
    _validate_store_credentials(package)
    source_capabilities = get_adapter_capabilities(source.name)
    _validate_adapter_credentials(
        package,
        source_capabilities,
        role="source",
        settings=source.settings or {},
    )
    destination_capabilities = get_adapter_capabilities(destination.name)
    _validate_adapter_credentials(
        package,
        destination_capabilities,
        role="destination",
        settings=destination.settings or {},
    )
    # Last: the surface checks above give a more precise reason for a node on an unsupported
    # setting than "not credential-bearing" would.
    _validate_all_reference_nodes(
        package.declared_content(),
        package,
        location="",
        allowed_locations=_allowed_reference_locations(package, source_capabilities, destination_capabilities),
    )


def resolve_reference(
    package: ConfigurationPackage,
    reference_name: str,
    *,
    environment: Mapping[str, str] | None = None,
) -> str:
    """Resolve one named reference without mutating the declared package."""
    try:
        reference = package.credentials[reference_name]
    except KeyError:
        msg = f"credential reference {reference_name!r} is not declared"
        raise CredentialConfigurationError(msg) from None
    return provider_for(reference, environment=environment).resolve(reference.identifier)
