"""Credential primitives of a registered package's runtime, and of its diagnostics.

The checks that read these primitives live in :mod:`infrahub_sync.configuration.validation`,
which accumulates findings instead of raising at the first defect, and which owns
``validate_package_credentials`` with them. What stays here is how an adapter selects a
credential setting at run time, and how a declared key is rendered into a diagnostic. The
dependency runs one way — validation reads these primitives and nothing here reads it back.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .models import REDACTED, is_renderable_setting_path, safe_pointer_component

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence


_REGISTERED_CONTEXT = "_infrahub_sync_registered_context"


def is_registered_context(settings: Mapping[str, object]) -> bool:
    """Whether adapter settings belong to a registered package's runtime instance.

    Registered settings are the package's whole authority: an adapter reads no worker
    environment variable for an address, a credential, or TLS verification when this is
    true. Transport settings such as a CA file, a proxy, or retries still come from the
    environment. Only direct adapter use keeps an environment fallback.
    """
    return settings.get(_REGISTERED_CONTEXT) is True


def declared_settings(settings: Mapping[str, object]) -> dict[str, object]:
    """Copy adapter settings without the internal registered-context marker.

    Use this before settings reach a third-party constructor as keywords: the marker is
    for this package's own checks, and an SDK with a fixed signature refuses it.
    """
    return {key: value for key, value in settings.items() if key != _REGISTERED_CONTEXT}


def pin_infrahub_sdk_authority(sdk_config: dict[str, Any]) -> dict[str, Any]:
    """Fill the Infrahub SDK ``Config`` fields it would otherwise read from ``INFRAHUB_*``.

    ``infrahub_sdk.Config`` is a pydantic settings class: every field its caller leaves out
    is read from the environment. For a registered package the address, the credentials,
    and TLS verification come from the package alone, so each one the package does not
    declare is passed explicitly as unset — no token, no username or password, and TLS
    verified. Transport settings the package cannot declare stay with the worker.
    """
    pinned = dict(sdk_config)
    for field, unset in (("api_token", None), ("username", None), ("password", None), ("tls_insecure", False)):
        pinned.setdefault(field, unset)
    return pinned


def select_runtime_credential(
    settings: Mapping[str, object], setting_name: str, environment_names: tuple[str, ...]
) -> str | None:
    """Select a credential without ambient reads for a registered runtime package."""
    if is_registered_context(settings):
        value = settings.get(setting_name)
        return value if isinstance(value, str) else None
    for environment_name in environment_names:
        value = os.environ.get(environment_name)
        if value:
            return value
    value = settings.get(setting_name)
    return value if isinstance(value, str) else None


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


def _rendered_component(name: object, secrets: Sequence[str]) -> str:
    """Render one caller-declared key, replacing it whole when it carries a collected value.

    Redaction happens on the complete key, before the bound below cuts it: whole-value
    matching at a later boundary cannot find a 64-character prefix of a longer secret.
    """
    text = str(name)
    if any(secret in text for secret in secrets):
        return REDACTED
    return _bounded_component(text)


def _bounded_location(location: str) -> str:
    """Bound one accumulated pointer so nesting depth cannot grow the message."""
    if len(location) <= _MAX_DIAGNOSTIC_LOCATION_LENGTH:
        return location
    return location[:_MAX_DIAGNOSTIC_LOCATION_LENGTH] + _TRUNCATION_MARKER


def _render_setting_name_list(names: Iterable[str], secrets: Sequence[str] = ()) -> str:
    """Render bounded escaped setting names with unambiguous, JSON-decodable boundaries."""
    escaped_names = sorted(_rendered_component(name, secrets) for name in names)
    listed = escaped_names[:_MAX_DIAGNOSTIC_NAME_ENTRIES]
    rendered = json.dumps(listed, ensure_ascii=True)
    omitted = len(escaped_names) - len(listed)
    return f"{rendered} and {omitted} more" if omitted else rendered
