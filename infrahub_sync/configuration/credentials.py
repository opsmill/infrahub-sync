"""Credential-reference validation and runtime provider contracts."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterable, Mapping
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
_STORE_CREDENTIAL_SETTING_PATHS = {
    "redis": ("url", "username", "password"),
}
_STORE_SETTING_PATHS = {
    "redis": frozenset({"db", "host", "password", "port", "store_id", "url", "username"}),
}
_URL_SETTING_NAMES = frozenset({"api_endpoint", "base_url", "endpoint", "url"})


class CredentialConfigurationError(ValueError):
    """A declared package contains an unsafe or unresolved credential shape."""


def _render_setting_name_list(names: Iterable[str]) -> str:
    """Render escaped setting names with unambiguous, JSON-decodable boundaries."""
    escaped_names = sorted(safe_pointer_component(name) for name in names)
    return json.dumps(escaped_names, ensure_ascii=True)


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


def _validate_all_reference_nodes(
    value: object,
    package: ConfigurationPackage,
    *,
    location: str,
) -> None:
    """Validate every use of the reserved ``$credential`` key without echoing values."""
    if isinstance(value, Mapping):
        if "$credential" in value:
            reference_name = _reference_name(value, location=location)
            if reference_name not in package.credentials:
                msg = f"{location} names unknown credential reference {reference_name!r}"
                raise CredentialConfigurationError(msg)
            return
        for key, item in value.items():
            escaped = safe_pointer_component(key)
            _validate_all_reference_nodes(item, package, location=f"{location}/{escaped}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _validate_all_reference_nodes(item, package, location=f"{location}/{index}")


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
        location = f"/configuration/{role}/settings/{setting_name}"
        if not isinstance(value, str):
            msg = f"{location} must be declared as a string"
            raise CredentialConfigurationError(msg)
        try:
            parsed = urlsplit(value)
        except ValueError:
            parsed = None
        if (
            parsed is None
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            msg = f"{location} cannot contain user information, query parameters, or fragments"
            raise CredentialConfigurationError(msg)
    if capabilities.validator is not None:
        findings = sort_findings(capabilities.validator(package, role))
        if error := next((finding for finding in findings if finding.severity == "error"), None):
            msg = f"{error.location}: {error.message}"
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
    try:
        credential_paths = _STORE_CREDENTIAL_SETTING_PATHS[store.type]
    except KeyError:
        if settings:
            msg = f"store type {store.type!r} has no configuration capability declaration"
            raise CredentialConfigurationError(msg) from None
        return
    unsupported_settings = set(settings) - _STORE_SETTING_PATHS[store.type]
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
    _validate_all_reference_nodes(package.declared_content(), package, location="")
    _validate_store_credentials(package)
    _validate_adapter_credentials(
        package,
        get_adapter_capabilities(source.name),
        role="source",
        settings=source.settings or {},
    )
    _validate_adapter_credentials(
        package,
        get_adapter_capabilities(destination.name),
        role="destination",
        settings=destination.settings or {},
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
