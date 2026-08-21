"""Typed declared-configuration records and stable package identity."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from hashlib import sha256
from types import MappingProxyType
from typing import Any, Literal, cast

from diffsync.enum import DiffSyncFlags
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_serializer, field_validator, model_validator
from pydantic_core import PydanticCustomError

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
_UNSUPPORTED_DECLARED_FIELDS_ERROR = "unsupported_declared_fields"
_INVALID_UNICODE_SURROGATE_ERROR = "invalid_unicode_surrogate"
_INVALID_JSON_VALUE_ERROR = "invalid_json_value"
_JSON_NATIVE_FAILURE_REASONS = frozenset(
    {
        "maximum declared-content depth exceeded",
        "non-finite float",
        "non-JSON value",
        "non-string mapping key",
        "recursive list",
        "recursive mapping",
    }
)


class ConfigurationPackageParseError(ValueError):
    """A declared package failed validation at a secret-safe public boundary."""


def _raise_unsupported_declared_fields(
    *,
    location: str,
    fields: Sequence[str],
) -> None:
    """Raise one structured error whose context contains field names but no values."""
    pointer = "/" + location.replace(".", "/").replace("[", "/").replace("]", "")
    raise PydanticCustomError(
        _UNSUPPORTED_DECLARED_FIELDS_ERROR,
        "{location} contains unsupported declared fields: {fields}",  # noqa: RUF027
        {
            "location": location,
            "pointer": pointer,
            "fields": ", ".join(fields),
            "field_names": tuple(fields),
        },
    )


def _require_known_fields(value: Any, model: type[BaseModel], *, location: str) -> None:
    """Prevent legacy permissive models from dropping declaration content before hashing."""
    if not isinstance(value, Mapping):
        return
    unknown = sorted(set(value) - set(model.model_fields))
    if unknown:
        _raise_unsupported_declared_fields(location=location, fields=unknown)


def _require_strict_configuration(value: Any) -> None:
    """Apply extra-forbid semantics throughout the legacy configuration shape."""
    if not isinstance(value, Mapping):
        return
    _require_known_fields(value, SyncConfig, location="configuration")
    if value.get("adapters_path") is not None:
        _raise_unsupported_declared_fields(
            location="configuration",
            fields=("adapters_path",),
        )
    for role in ("source", "destination"):
        adapter = value.get(role)
        _require_known_fields(adapter, SyncAdapter, location=f"configuration.{role}")
        if isinstance(adapter, Mapping) and adapter.get("adapter") is not None:
            _raise_unsupported_declared_fields(
                location=f"configuration.{role}",
                fields=("adapter",),
            )
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
                _require_known_fields(
                    member,
                    model,
                    location=f"{prefix}.{field_name}[{member_index}]",
                )


def _require_unicode_scalars(value: str, *, location: str) -> None:
    """Reject UTF-16 surrogate code points before JSON serialization can replace them."""
    if any(0xD800 <= ord(character) <= 0xDFFF for character in value):
        pointer = location.removeprefix("$") or "/"
        raise PydanticCustomError(
            _INVALID_UNICODE_SURROGATE_ERROR,
            "{location} contains an invalid Unicode surrogate",  # noqa: RUF027
            {"location": location, "pointer": pointer},
        )


def _raise_invalid_json_value(*, location: str, reason: str) -> None:
    """Raise one structured, value-free error for invalid JSON-native content."""
    pointer = location.removeprefix("$") or "/"
    raise PydanticCustomError(
        _INVALID_JSON_VALUE_ERROR,
        "{location}: {reason}",  # noqa: RUF027
        {"location": location, "pointer": pointer, "reason": reason},
    )


def _require_json_native(
    value: Any,
    *,
    location: str = "$",
    _containers: frozenset[int] = frozenset(),
    _depth: int = 0,
) -> None:
    """Reject values outside JSON's native data model before Pydantic coercion."""
    if _depth > _MAX_DECLARATION_DEPTH:
        _raise_invalid_json_value(location=location, reason="maximum declared-content depth exceeded")
    value_type = type(value)
    if value_type is str:
        _require_unicode_scalars(value, location=location)
        return
    if value is None or value_type is bool or value_type is int:
        return
    if value_type is float:
        if not math.isfinite(value):
            _raise_invalid_json_value(location=location, reason="non-finite float")
        return
    if value_type is list:
        if id(value) in _containers:
            _raise_invalid_json_value(location=location, reason="recursive list")
        containers = _containers | {id(value)}
        for index, item in enumerate(value):
            _require_json_native(item, location=f"{location}/{index}", _containers=containers, _depth=_depth + 1)
        return
    if value_type is dict:
        if id(value) in _containers:
            _raise_invalid_json_value(location=location, reason="recursive mapping")
        containers = _containers | {id(value)}
        for key, item in value.items():
            if type(key) is not str:  # pylint: disable=unidiomatic-typecheck  # Exact JSON strings only.
                _raise_invalid_json_value(location=location, reason="non-string mapping key")
            item_location = f"{location}/{safe_pointer_component(key)}"
            _require_unicode_scalars(key, location=item_location)
            _require_json_native(item, location=item_location, _containers=containers, _depth=_depth + 1)
        return
    _raise_invalid_json_value(location=location, reason="non-JSON value")


def _freeze_json(value: Any) -> Any:
    """Return an immutable recursive representation of validated JSON content."""
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze_json(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(item) for item in value)
    return value


def _thaw_json(value: Any) -> Any:
    """Return JSON containers from an immutable declared-content representation."""
    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


class _ImmutableSchemaMappingFilter(SchemaMappingFilter):
    """Package-local immutable form of a legacy schema filter."""

    model_config = ConfigDict(frozen=True)

    @field_validator("value")
    @classmethod
    def _freeze_value(cls, value: Any) -> Any:
        return _freeze_json(value)

    @field_serializer("value")
    def _serialize_value(self, value: Any) -> Any:
        return _thaw_json(value)


class _ImmutableSchemaMappingTransform(SchemaMappingTransform):
    """Package-local immutable form of a legacy schema transform."""

    model_config = ConfigDict(frozen=True)


class _ImmutableSchemaMappingField(SchemaMappingField):
    """Package-local immutable form of a legacy schema field mapping."""

    model_config = ConfigDict(frozen=True)

    @field_validator("static")
    @classmethod
    def _freeze_static(cls, value: Any) -> Any:
        return _freeze_json(value)

    @field_serializer("static")
    def _serialize_static(self, value: Any) -> Any:
        return _thaw_json(value)


class _ImmutableSchemaMappingModel(SchemaMappingModel):
    """Package-local immutable form of one legacy schema mapping."""

    model_config = ConfigDict(frozen=True)

    identifiers: tuple[str, ...] | None = None
    filters: tuple[_ImmutableSchemaMappingFilter, ...] | None = None
    transforms: tuple[_ImmutableSchemaMappingTransform, ...] | None = None
    fields: tuple[_ImmutableSchemaMappingField, ...] = ()

    @field_serializer("identifiers", "filters", "transforms", "fields")
    def _serialize_collections(self, value: tuple[Any, ...] | None) -> list[Any] | None:
        return None if value is None else list(value)


class _ImmutableSyncAdapter(SyncAdapter):
    """Package-local immutable form of legacy adapter settings."""

    model_config = ConfigDict(frozen=True)

    settings: Mapping[str, Any] | None = Field(default_factory=dict, validate_default=True)

    @field_validator("settings")
    @classmethod
    def _freeze_settings(cls, value: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
        return cast("Mapping[str, Any] | None", _freeze_json(value))

    @field_serializer("settings")
    def _serialize_settings(self, value: Mapping[str, Any] | None) -> dict[str, Any] | None:
        return cast("dict[str, Any] | None", _thaw_json(value))


class _ImmutableSyncStore(SyncStore):
    """Package-local immutable form of legacy store settings."""

    model_config = ConfigDict(frozen=True)

    settings: Mapping[str, Any] | None = Field(default_factory=dict, validate_default=True)

    @field_validator("settings")
    @classmethod
    def _freeze_settings(cls, value: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
        return cast("Mapping[str, Any] | None", _freeze_json(value))

    @field_serializer("settings")
    def _serialize_settings(self, value: Mapping[str, Any] | None) -> dict[str, Any] | None:
        return cast("dict[str, Any] | None", _thaw_json(value))


class _ImmutableIncrementalConfig(IncrementalConfig):
    """Package-local immutable form of legacy incremental settings."""

    model_config = ConfigDict(frozen=True)


class _ImmutableSyncConfig(SyncConfig):
    """Deeply immutable declared configuration without changing legacy runtime models."""

    model_config = ConfigDict(frozen=True)

    store: _ImmutableSyncStore | None = None
    source: _ImmutableSyncAdapter
    destination: _ImmutableSyncAdapter
    adapters_path: tuple[str, ...] | None = None
    order: tuple[str, ...] = ()
    schema_mapping: tuple[_ImmutableSchemaMappingModel, ...] = ()
    diffsync_flags: tuple[str | DiffSyncFlags, ...] | None = ()
    incremental: _ImmutableIncrementalConfig | None = None

    @field_serializer("adapters_path", "order", "schema_mapping")
    def _serialize_collections(self, value: tuple[Any, ...] | None) -> list[Any] | None:
        return None if value is None else list(value)

    @field_serializer("diffsync_flags")
    def _serialize_diffsync_flags(
        self,
        value: tuple[str | DiffSyncFlags, ...] | None,
    ) -> list[str | int] | None:
        return None if value is None else [item.value if isinstance(item, DiffSyncFlags) else item for item in value]


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
    configuration: _ImmutableSyncConfig
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


def safe_pointer_component(value: object) -> str:
    """Escape one validation path component for visible, single-line display."""
    components = []
    for character in str(value):
        codepoint = ord(character)
        if character == "\n":
            components.append(r"\n")
        elif character == "\r":
            components.append(r"\r")
        elif character == "\t":
            components.append(r"\t")
        elif not character.isprintable():
            if codepoint <= 0xFFFF:
                components.append(f"\\u{codepoint:04x}")
            else:
                components.append(f"\\U{codepoint:08x}")
        elif character == "~":
            components.append("~0")
        elif character == "/":
            components.append("~1")
        elif character == "\\":
            components.append(r"\\")
        else:
            components.append(character)
    return "".join(components)


def _safe_validation_location(error: Mapping[str, Any]) -> str:
    """Return one JSON-Pointer-like validation location without input values."""
    components = [safe_pointer_component(component) for component in error.get("loc", ())]
    return "/" + "/".join(components) if components else "/"


def _safe_native_validation_failure(error: Mapping[str, Any]) -> tuple[tuple[str, str], ...] | None:
    """Return safe structured JSON-native failure details when available."""
    if error.get("type") == _INVALID_JSON_VALUE_ERROR:
        context = error.get("ctx")
        pointer = context.get("pointer") if isinstance(context, Mapping) else None
        reason = context.get("reason") if isinstance(context, Mapping) else None
        if isinstance(pointer, str) and reason in _JSON_NATIVE_FAILURE_REASONS:
            return ((pointer, cast("str", reason)),)
        return ((_safe_validation_location(error), "invalid JSON value"),)
    if error.get("type") == _INVALID_UNICODE_SURROGATE_ERROR:
        context = error.get("ctx")
        pointer = context.get("pointer") if isinstance(context, Mapping) else None
        location = pointer if isinstance(pointer, str) else _safe_validation_location(error)
        return ((location, "invalid Unicode surrogate"),)
    return None


def _safe_validation_failures(error: Mapping[str, Any]) -> tuple[tuple[str, str], ...]:
    """Return actionable validation details assembled only from safe error metadata."""
    native_failure = _safe_native_validation_failure(error)
    if native_failure is not None:
        return native_failure
    if error.get("type") != _UNSUPPORTED_DECLARED_FIELDS_ERROR:
        return ((_safe_validation_location(error), str(error.get("type", "invalid-value"))),)
    context = error.get("ctx")
    if not isinstance(context, Mapping):
        return ((_safe_validation_location(error), "unsupported declared field"),)
    pointer = context.get("pointer")
    field_names = context.get("field_names")
    if not isinstance(pointer, str) or not isinstance(field_names, tuple):
        return ((_safe_validation_location(error), "unsupported declared field"),)
    failures = []
    for field_name in field_names:
        if not isinstance(field_name, str):
            return ((_safe_validation_location(error), "unsupported declared field"),)
        escaped = safe_pointer_component(field_name)
        failures.append((f"{pointer}/{escaped}", "unsupported declared field"))
    return tuple(failures)


def parse_configuration_package(value: object) -> ConfigurationPackage:
    """Parse declared package content without exposing rejected input in errors."""
    if type(value) is not dict:  # pylint: disable=unidiomatic-typecheck  # Exact JSON object roots only.
        msg = "configuration package is invalid at /: non-JSON value"
        raise ConfigurationPackageParseError(msg)
    try:
        return ConfigurationPackage.model_validate(value)
    except ValidationError as exc:
        failures = sorted(
            {
                failure
                for error in exc.errors(include_input=False, include_context=True, include_url=False)
                for failure in _safe_validation_failures(error)
            }
        )
        details = "; ".join(f"{location}: {reason}" for location, reason in failures)
        msg = f"configuration package is invalid at {details}"
        raise ConfigurationPackageParseError(msg) from None


def sort_findings(findings: Sequence[ValidationFinding]) -> tuple[ValidationFinding, ...]:
    """Return findings in the stable cross-interface order."""
    severity_order = {"error": 0, "warning": 1}
    return tuple(sorted(findings, key=lambda item: (item.location, severity_order[item.severity], item.code)))
