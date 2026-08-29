"""Credential-reference primitives and runtime provider contracts.

The checks that read these primitives live in :mod:`infrahub_sync.configuration.validation`,
which accumulates findings instead of raising at the first defect, and which owns
``validate_package_credentials`` with them. What stays here is what a credential reference *is*:
how one is declared, how one is rendered into a diagnostic, and how one is resolved at run time.
The dependency runs one way — validation reads these primitives and nothing here reads it back.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from .models import is_renderable_setting_path, safe_pointer_component

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from .models import ConfigurationPackage, CredentialReference

_ENV_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_REGISTERED_CONTEXT = "_infrahub_sync_registered_context"


def select_runtime_credential(
    settings: Mapping[str, object], setting_name: str, environment_names: tuple[str, ...]
) -> object:
    """Select a credential without ambient reads for a registered runtime package."""
    if settings.get(_REGISTERED_CONTEXT) is True:
        return settings.get(setting_name)
    for environment_name in environment_names:
        value = os.environ.get(environment_name)
        if value:
            return value
    return settings.get(setting_name)


@dataclass(frozen=True, slots=True)
class _StoreCapabilities:
    """Connection-free configuration facts for one bundled store type."""

    allowed_settings: frozenset[str]
    credential_setting_paths: tuple[str, ...]

    def __post_init__(self) -> None:
        invalid = sorted(path for path in self.credential_setting_paths if not is_renderable_setting_path(path))
        if invalid:
            msg = f"store credential paths are not renderable setting paths: {invalid!r}"
            raise ValueError(msg)
        outside = sorted(set(self.credential_setting_paths) - self.allowed_settings)
        if outside:
            msg = f"store credential paths outside allowed settings: {outside!r}"
            raise ValueError(msg)


# One entry per store type, so a new type cannot be declared half-way.
_STORE_CAPABILITIES: dict[str, _StoreCapabilities] = {
    "redis": _StoreCapabilities(
        allowed_settings=frozenset({"db", "host", "password", "port", "store_id", "url", "username"}),
        credential_setting_paths=("url", "username", "password"),
    ),
}
# Diagnostics are rendered from declared keys, so they inherit the declaration's size unless
# bounded here the way models.py bounds its own parse diagnostics.
_MAX_DIAGNOSTIC_COMPONENT_LENGTH = 64
_MAX_DIAGNOSTIC_LOCATION_LENGTH = 256
_MAX_DIAGNOSTIC_NAME_ENTRIES = 16
# safe_pointer_component only ever emits ~0 and ~1, so ~2 cannot collide with escaped
# content: a literal "~2" in a declared key renders as "~02". This is what makes a truncated
# pointer unforgeable — declared components are capped at 64, so an over-length package key
# always carries the marker and can never truncate onto a legitimately declared pointer.
# Changing the marker to something escaping can produce reopens that. This holds for the "~2"
# form, which only ever appears in a message; validation.py notes where the pointer form of the
# same marker stops being unforgeable.
_TRUNCATION_MARKER = "~2"


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
