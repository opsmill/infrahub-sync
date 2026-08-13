"""Typed declared-configuration records and stable package identity."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from hashlib import sha256
from types import MappingProxyType
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_serializer, field_validator, model_validator

from infrahub_sync import (
    IncrementalConfig,
    SchemaMappingField,
    SchemaMappingFilter,
    SchemaMappingModel,
    SchemaMappingTransform,
    SyncAdapter,
    SyncConfig,
    SyncStore,
)
from infrahub_sync.plan.canonical import canonical_json_bytes

_REFERENCE_NAME_PATTERN = r"^[A-Za-z][A-Za-z0-9_.-]{0,127}$"
_PROVIDER_NAME_PATTERN = r"^[a-z][a-z0-9-]{0,63}$"
_REFERENCE_NAME_RE = re.compile(_REFERENCE_NAME_PATTERN)
_MAX_DECLARATION_DEPTH = 64


class ConfigurationPackageParseError(ValueError):
    """A declared package failed validation at a secret-safe public boundary."""


def _require_known_fields(value: Any, model: type[BaseModel], *, location: str) -> None:
    """Prevent legacy permissive models from dropping declaration content before hashing."""
    if not isinstance(value, Mapping):
        return
    unknown = sorted(set(value) - set(model.model_fields))
    if unknown:
        msg = f"{location} contains unsupported declared fields: {', '.join(unknown)}"
        raise ValueError(msg)


def _require_strict_configuration(value: Any) -> None:
    """Apply extra-forbid semantics throughout the legacy configuration shape."""
    if not isinstance(value, Mapping):
        return
    _require_known_fields(value, SyncConfig, location="configuration")
    if value.get("adapters_path") is not None:
        msg = "configuration contains unsupported declared fields: adapters_path"
        raise ValueError(msg)
    for role in ("source", "destination"):
        adapter = value.get(role)
        _require_known_fields(adapter, SyncAdapter, location=f"configuration.{role}")
        if isinstance(adapter, Mapping) and adapter.get("adapter") is not None:
            msg = f"configuration.{role} contains unsupported declared fields: adapter"
            raise ValueError(msg)
    _require_known_fields(value.get("store"), SyncStore, location="configuration.store")
    _require_known_fields(value.get("incremental"), IncrementalConfig, location="configuration.incremental")
    mappings = value.get("schema_mapping")
    if not isinstance(mappings, list):
        return
    for index, mapping in enumerate(mappings):
        prefix = f"configuration.schema_mapping[{index}]"
        _require_known_fields(mapping, SchemaMappingModel, location=prefix)
        if not isinstance(mapping, Mapping):
            continue
        declared_mapping = cast("Mapping[str, object]", mapping)
        nested_models = (
            ("filters", SchemaMappingFilter),
            ("transforms", SchemaMappingTransform),
            ("fields", SchemaMappingField),
        )
        for field_name, model in nested_models:
            members = declared_mapping.get(field_name)
            if not isinstance(members, list):
                continue
            for member_index, member in enumerate(members):
                _require_known_fields(member, model, location=f"{prefix}.{field_name}[{member_index}]")


def _require_json_native(
    value: Any,
    *,
    location: str = "$",
    _containers: frozenset[int] = frozenset(),
    _depth: int = 0,
) -> None:
    """Reject values outside JSON's native data model before Pydantic coercion."""
    if _depth > _MAX_DECLARATION_DEPTH:
        msg = f"{location} exceeds the maximum declared-content depth"
        raise ValueError(msg)
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            msg = f"{location} contains a non-finite float"
            raise ValueError(msg)
        return
    if isinstance(value, list):
        if id(value) in _containers:
            msg = f"{location} contains a recursive list"
            raise ValueError(msg)
        containers = _containers | {id(value)}
        for index, item in enumerate(value):
            _require_json_native(item, location=f"{location}/{index}", _containers=containers, _depth=_depth + 1)
        return
    if isinstance(value, Mapping):
        if id(value) in _containers:
            msg = f"{location} contains a recursive mapping"
            raise ValueError(msg)
        containers = _containers | {id(value)}
        for key, item in value.items():
            if not isinstance(key, str):
                msg = f"{location} contains a non-string mapping key"
                raise ValueError(msg)  # noqa: TRY004 - Pydantic input errors use one ValidationError surface.
            escaped = key.replace("~", "~0").replace("/", "~1")
            _require_json_native(item, location=f"{location}/{escaped}", _containers=containers, _depth=_depth + 1)
        return
    msg = f"{location} contains non-JSON value type {type(value).__name__!r}"
    raise ValueError(msg)


class CredentialReference(BaseModel):
    """Non-secret pointer to a runtime credential provider entry."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: str = Field(pattern=_PROVIDER_NAME_PATTERN)
    identifier: str = Field(min_length=1, max_length=256)


class CredentialReferenceNode(BaseModel):
    """Exact reference node accepted at a credential-bearing setting path."""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    reference_name: str = Field(alias="$credential", pattern=_REFERENCE_NAME_PATTERN)


class ConfigurationPackageMetadata(BaseModel):
    """Behavior-affecting metadata included in declared package identity."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    adapter_api_version: Literal[1] = 1


class ValidationFinding(BaseModel):
    """Stable, secret-safe result produced by configuration validation."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    code: str = Field(pattern=r"^[a-z][a-z0-9-]*$")
    severity: Literal["error", "warning"]
    location: str = Field(pattern=r"^(/(?:[^~/]|~[01])*)*$")
    message: str = Field(min_length=1)


class ConfigurationPackage(BaseModel):
    """Strict version-1 envelope containing only declared, non-secret content."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    format_version: Literal[1] = 1
    configuration: SyncConfig
    package_metadata: ConfigurationPackageMetadata = Field(default_factory=ConfigurationPackageMetadata)
    credentials: Mapping[str, CredentialReference] = Field(default_factory=dict, validate_default=True)

    @model_validator(mode="before")
    @classmethod
    def _require_strict_json_input(cls, value: Any) -> Any:
        _require_json_native(value)
        return value

    @field_validator("configuration", mode="before")
    @classmethod
    def _reject_ignored_configuration_fields(cls, value: Any) -> Any:
        _require_strict_configuration(value)
        return value

    @field_validator("credentials")
    @classmethod
    def _validate_reference_names(cls, value: Mapping[str, CredentialReference]) -> Mapping[str, CredentialReference]:
        invalid = sorted(name for name in value if _REFERENCE_NAME_RE.fullmatch(name) is None)
        if invalid:
            msg = f"credential reference names are invalid: {', '.join(invalid)}"
            raise ValueError(msg)
        return MappingProxyType(dict(value))

    @field_serializer("credentials")
    def _serialize_credentials(self, value: Mapping[str, CredentialReference]) -> dict[str, CredentialReference]:
        return dict(value)

    def declared_content(self) -> dict[str, Any]:
        """Return the exact JSON-native content covered by the package checksum."""
        return self.model_dump(mode="json", by_alias=True)

    def checksum(self) -> str:
        """Return lowercase SHA-256 over canonical declared package content."""
        return sha256(canonical_json_bytes(self.declared_content(), kind="configuration-package")).hexdigest()


def _safe_validation_location(error: Mapping[str, Any]) -> str:
    """Return one JSON-Pointer-like validation location without input values."""
    components = []
    for component in error.get("loc", ()):
        escaped = str(component).replace("~", "~0").replace("/", "~1")
        components.append(escaped)
    return "/" + "/".join(components) if components else "/"


def parse_configuration_package(value: object) -> ConfigurationPackage:
    """Parse declared package content without exposing rejected input in errors."""
    try:
        return ConfigurationPackage.model_validate(value)
    except ValidationError as exc:
        failures = sorted(
            {
                (_safe_validation_location(error), str(error.get("type", "invalid-value")))
                for error in exc.errors(include_input=False, include_context=False, include_url=False)
            }
        )
        details = "; ".join(f"{location}: {reason}" for location, reason in failures)
        msg = f"configuration package is invalid at {details}"
        raise ConfigurationPackageParseError(msg) from None


def sort_findings(findings: Sequence[ValidationFinding]) -> tuple[ValidationFinding, ...]:
    """Return findings in the stable cross-interface order."""
    severity_order = {"error": 0, "warning": 1}
    return tuple(sorted(findings, key=lambda item: (item.location, severity_order[item.severity], item.code)))
