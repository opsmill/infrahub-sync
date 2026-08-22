"""Typed declared-configuration records and stable package identity."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from hashlib import sha256
from types import MappingProxyType
from typing import Annotated, Any, Literal, cast
from unicodedata import category

from diffsync.enum import DiffSyncFlags
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    field_serializer,
    field_validator,
    model_validator,
)
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
_SAFE_POINTER_RE = re.compile(r"^(/(?:[^~/]|~[01])*)*$")
_MAX_DECLARATION_DEPTH = 64
_MAX_VALIDATION_ERRORS = 256
_MAX_FINAL_FAILURES = 256
_MAX_ERROR_RECORD_ENTRIES = 16
_MAX_METADATA_KEY_LENGTH = 64
_MAX_ERROR_TYPE_LENGTH = 128
_MAX_LOCATION_MEMBERS = 64
_MAX_LOCATION_STRING_LENGTH = 256
_MAX_CONTEXT_POINTER_LENGTH = 4096
_MAX_CONTEXT_ENTRIES = 16
_MAX_CONTEXT_COLLECTION_MEMBERS = 128
_MAX_CONTEXT_STRING_LENGTH = 256
_MAX_CONTEXT_INTEGER = 2**63 - 1
_UNSUPPORTED_DECLARED_FIELDS_ERROR = "unsupported_declared_fields"
_INVALID_UNICODE_SURROGATE_ERROR = "invalid_unicode_surrogate"
_INVALID_JSON_VALUE_ERROR = "invalid_json_value"
_INVALID_DIFFSYNC_FLAG_NAME_ERROR = "invalid_diffsync_flag_name"
_SAFE_PYDANTIC_FAILURE_REASONS = {
    "missing": "required field is missing",
    "literal_error": "unsupported value",
    "extra_forbidden": "unsupported declared field",
    "string_pattern_mismatch": "does not match required pattern",
    "string_too_short": "value is too short",
    "string_too_long": "value is too long",
    "string_type": "wrong type",
    "model_type": "wrong type",
    "dict_type": "wrong type",
    "tuple_type": "wrong type",
    "int_type": "wrong type",
    "int_parsing": "wrong type",
    "int_from_float": "wrong type",
    "int_parsing_size": "number is outside supported range",
}
_DIFFSYNC_FLAG_FAILURE_REASONS = frozenset(
    {
        "diffsync flag name must be a string",
        "unknown diffsync flag name",
    }
)
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


_STRICT_CONFIGURATION_CHILDREN: dict[type[BaseModel], dict[str, tuple[type[BaseModel], bool]]] = {
    SyncConfig: {
        "store": (SyncStore, False),
        "source": (SyncAdapter, False),
        "destination": (SyncAdapter, False),
        "schema_mapping": (SchemaMappingModel, True),
        "incremental": (IncrementalConfig, False),
    },
    SchemaMappingModel: {
        "filters": (SchemaMappingFilter, True),
        "transforms": (SchemaMappingTransform, True),
        "fields": (SchemaMappingField, True),
    },
}


def _require_strict_model(value: Any, model: type[BaseModel], *, location: str) -> None:
    """Apply extra-forbid semantics to one registered legacy model node."""
    if not isinstance(value, Mapping):
        return
    _require_known_fields(value, model, location=location)
    if model is SyncConfig and value.get("adapters_path") is not None:
        _raise_unsupported_declared_fields(location=location, fields=("adapters_path",))
    if model is SyncAdapter and value.get("adapter") is not None:
        _raise_unsupported_declared_fields(location=location, fields=("adapter",))
    for field_name, (child_model, many) in _STRICT_CONFIGURATION_CHILDREN.get(model, {}).items():
        child = value.get(field_name)
        child_location = f"{location}.{field_name}"
        if not many:
            _require_strict_model(child, child_model, location=child_location)
            continue
        if not isinstance(child, list):
            continue
        for index, member in enumerate(child):
            _require_strict_model(member, child_model, location=f"{child_location}[{index}]")


def _require_strict_configuration(value: Any) -> None:
    """Apply extra-forbid semantics throughout the legacy configuration shape."""
    _require_strict_model(value, SyncConfig, location="configuration")


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

    @field_validator("diffsync_flags", mode="before")
    @classmethod
    def _require_named_diffsync_flags(cls, value: Any) -> Any:
        # pylint: disable=unidiomatic-typecheck
        if type(value) is list:
            for index, item in enumerate(value):
                if type(item) is not str:
                    reason = "diffsync flag name must be a string"
                    raise PydanticCustomError(
                        _INVALID_DIFFSYNC_FLAG_NAME_ERROR,
                        reason,
                        {"index": index, "reason": reason},
                    )
                if item not in DiffSyncFlags.__members__:
                    reason = "unknown diffsync flag name"
                    raise PydanticCustomError(
                        _INVALID_DIFFSYNC_FLAG_NAME_ERROR,
                        reason,
                        {"index": index, "reason": reason},
                    )
        # pylint: enable=unidiomatic-typecheck
        return value

    @field_serializer("adapters_path", "order", "schema_mapping")
    def _serialize_collections(self, value: tuple[Any, ...] | None) -> list[Any] | None:
        return None if value is None else list(value)

    @field_serializer("diffsync_flags")
    def _serialize_diffsync_flags(
        self,
        value: tuple[str | DiffSyncFlags, ...] | None,
    ) -> list[str] | None:
        return None if value is None else [item.name if isinstance(item, DiffSyncFlags) else item for item in value]


class CredentialReference(BaseModel):
    """Non-secret pointer to a runtime credential provider entry."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: str = Field(pattern=_PROVIDER_NAME_PATTERN)
    identifier: str = Field(min_length=1, max_length=256)


CredentialReferenceName = Annotated[str, StringConstraints(pattern=_REFERENCE_NAME_PATTERN)]


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
    credentials: Mapping[CredentialReferenceName, CredentialReference] = Field(
        default_factory=dict, validate_default=True
    )

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
    def _freeze_credentials(
        cls, value: Mapping[CredentialReferenceName, CredentialReference]
    ) -> Mapping[CredentialReferenceName, CredentialReference]:
        """Freeze credentials after Pydantic validates every key and value."""
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
        elif character == ":":
            components.append(r"\u003a")
        elif character == ";":
            components.append(r"\u003b")
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


def _has_closed_metadata_shape(value: object, *, max_entries: int) -> bool:
    """Return whether metadata is a bounded exact dict with inert exact-string keys."""
    if type(value) is not dict:  # pylint: disable=unidiomatic-typecheck
        return False
    if len(value) > max_entries:
        return False
    return all(
        type(key) is str and len(key) <= _MAX_METADATA_KEY_LENGTH  # pylint: disable=unidiomatic-typecheck
        for key in value
    )


def _safe_validation_location(raw_location: object) -> tuple[str, bool]:
    """Decode a bounded location and report whether every component was valid."""
    if type(raw_location) is not tuple:  # pylint: disable=unidiomatic-typecheck
        return "/", False
    if len(raw_location) > _MAX_LOCATION_MEMBERS:
        return "/", False
    components: list[str] = []
    complete = True
    for component in raw_location:
        if type(component) is str:  # pylint: disable=unidiomatic-typecheck
            if len(component) > _MAX_LOCATION_STRING_LENGTH:
                complete = False
                break
            components.append(safe_pointer_component(component))
        elif type(component) is int:  # pylint: disable=unidiomatic-typecheck
            if not 0 <= component <= _MAX_CONTEXT_INTEGER:
                complete = False
                break
            components.append(str(component))
        else:
            complete = False
            break
    location = "/" + "/".join(components) if components else "/"
    return location, complete


def _render_context_pointer(pointer: str) -> str:
    """Encode diagnostic delimiters in an already-valid RFC 6901 pointer."""
    return pointer.replace("\\", r"\\").replace(":", r"\u003a").replace(";", r"\u003b")


def _safe_context_pointer(value: object) -> str | None:
    """Decode one bounded absolute RFC 6901 pointer without executable values."""
    if type(value) is not str:  # pylint: disable=unidiomatic-typecheck
        return None
    if len(value) > _MAX_CONTEXT_POINTER_LENGTH or not value.startswith("/"):
        return None
    if _SAFE_POINTER_RE.fullmatch(value) is None:
        return None
    if not all(character.isprintable() for character in value):
        return None
    if any(category(character) in {"Cc", "Cf", "Cs", "Zl", "Zp"} for character in value):
        return None
    return _render_context_pointer(value)


def _closed_context(record: dict[object, object]) -> dict[object, object] | None:
    """Return a custom context only after its complete dictionary shape is safe."""
    context = record.get("ctx")
    if not _has_closed_metadata_shape(context, max_entries=_MAX_CONTEXT_ENTRIES):
        return None
    assert type(context) is dict  # pylint: disable=unidiomatic-typecheck  # Narrow after the exact-type gate.
    return cast("dict[object, object]", context)


def _safe_context_string(value: object) -> str | None:
    """Return one bounded exact custom-context string."""
    if type(value) is not str or len(value) > _MAX_CONTEXT_STRING_LENGTH:  # pylint: disable=unidiomatic-typecheck
        return None
    return value


def _credential_key_failure(error_type: str, raw_location: object) -> tuple[str, str] | None:
    """Normalize Pydantic's versioned constrained-mapping-key location marker."""
    if error_type != "string_pattern_mismatch" or type(raw_location) is not tuple:  # pylint: disable=unidiomatic-typecheck
        return None
    if len(raw_location) != 3:
        return None
    container, name, marker = raw_location
    if not (
        type(container) is str  # pylint: disable=unidiomatic-typecheck
        and container == "credentials"
        and type(name) is str  # pylint: disable=unidiomatic-typecheck
        and type(marker) is str  # pylint: disable=unidiomatic-typecheck
        and marker == "[key]"
    ):
        return None
    location = "/credentials"
    if len(name) <= _MAX_LOCATION_STRING_LENGTH:
        location = f"{location}/{safe_pointer_component(name)}"
    return location, "invalid credential reference name"


def _is_oversized_credential_location(raw_location: object) -> bool:
    """Recognize child errors whose constrained credential key exceeds the display bound."""
    if type(raw_location) is not tuple or not 2 <= len(raw_location) <= _MAX_LOCATION_MEMBERS:  # pylint: disable=unidiomatic-typecheck
        return False
    container, name, *children = raw_location
    if not (
        type(container) is str  # pylint: disable=unidiomatic-typecheck
        and container == "credentials"
        and type(name) is str  # pylint: disable=unidiomatic-typecheck
        and len(name) > _MAX_LOCATION_STRING_LENGTH
    ):
        return False
    return all(
        (type(child) is str and len(child) <= _MAX_LOCATION_STRING_LENGTH)  # pylint: disable=unidiomatic-typecheck
        or (type(child) is int and 0 <= child <= _MAX_CONTEXT_INTEGER)  # pylint: disable=unidiomatic-typecheck
        for child in children
    )


def _decode_validation_record(record: object) -> tuple[tuple[str, str], ...]:  # noqa: PLR0911
    """Decode one record through the closed diagnostic grammar."""
    if not _has_closed_metadata_shape(record, max_entries=_MAX_ERROR_RECORD_ENTRIES):
        return (("/", "invalid value"),)
    assert type(record) is dict  # pylint: disable=unidiomatic-typecheck  # Narrow after the exact-type gate.
    safe_record = cast("dict[object, object]", record)
    error_type = safe_record.get("type")
    if type(error_type) is not str or len(error_type) > _MAX_ERROR_TYPE_LENGTH:  # pylint: disable=unidiomatic-typecheck
        return (("/", "invalid value"),)
    raw_location = safe_record.get("loc")
    location, valid_location = _safe_validation_location(raw_location)

    credential_failure = _credential_key_failure(error_type, raw_location)
    if credential_failure is not None:
        return (credential_failure,)
    if not valid_location and _is_oversized_credential_location(raw_location):
        location = "/credentials"
        valid_location = True
    if not valid_location:
        return ((location, "invalid value"),)

    if error_type == _INVALID_JSON_VALUE_ERROR:
        context = _closed_context(safe_record)
        if context is not None:
            pointer = _safe_context_pointer(context.get("pointer"))
            reason = _safe_context_string(context.get("reason"))
            if pointer is not None and reason in _JSON_NATIVE_FAILURE_REASONS:
                return ((pointer, reason),)
        return ((location, "invalid JSON value"),)

    if error_type == _INVALID_UNICODE_SURROGATE_ERROR:
        context = _closed_context(safe_record)
        if context is not None:
            pointer = _safe_context_pointer(context.get("pointer"))
            if pointer is not None:
                return ((pointer, "invalid Unicode surrogate"),)
        return ((location, "invalid Unicode surrogate"),)

    if error_type == _UNSUPPORTED_DECLARED_FIELDS_ERROR:
        context = _closed_context(safe_record)
        if context is None:
            return ((location, "unsupported declared field"),)
        pointer = _safe_context_pointer(context.get("pointer"))
        field_names = context.get("field_names")
        if (
            pointer is None
            or type(field_names) is not tuple  # pylint: disable=unidiomatic-typecheck
            or not 1 <= len(field_names) <= _MAX_CONTEXT_COLLECTION_MEMBERS
            or any(
                type(field_name) is not str or len(field_name) > _MAX_CONTEXT_STRING_LENGTH  # pylint: disable=unidiomatic-typecheck
                for field_name in field_names
            )
        ):
            return ((location, "unsupported declared field"),)
        return tuple(
            (f"{pointer}/{safe_pointer_component(field_name)}", "unsupported declared field")
            for field_name in field_names
        )

    if error_type == _INVALID_DIFFSYNC_FLAG_NAME_ERROR:
        context = _closed_context(safe_record)
        if context is not None:
            index = context.get("index")
            reason = _safe_context_string(context.get("reason"))
            if (
                type(index) is int  # pylint: disable=unidiomatic-typecheck
                and 0 <= index <= _MAX_CONTEXT_INTEGER
                and reason in _DIFFSYNC_FLAG_FAILURE_REASONS
            ):
                return ((f"{location}/{index}", reason),)
        return ((location, "invalid diffsync flag name"),)

    reason = _SAFE_PYDANTIC_FAILURE_REASONS.get(error_type, "invalid value")
    return ((location, reason),)


def _decode_validation_errors(errors: object) -> tuple[tuple[str, str], ...]:
    """Decode returned Pydantic metadata with bounded total output."""
    if type(errors) is not list or not 1 <= len(errors) <= _MAX_VALIDATION_ERRORS:  # pylint: disable=unidiomatic-typecheck
        return (("/", "invalid value"),)
    failures: set[tuple[str, str]] = set()
    for record in errors:
        failures.update(_decode_validation_record(record))
        if len(failures) > _MAX_FINAL_FAILURES:
            return (("/", "invalid value"),)
    return tuple(sorted(failures))


def _configuration_package_parse_error(
    failures: Sequence[tuple[str, str]],
) -> ConfigurationPackageParseError:
    """Format one public parse error from value-free failure details."""
    details = "; ".join(f"{location}: {reason}" for location, reason in failures)
    return ConfigurationPackageParseError(f"configuration package is invalid at {details}")


def parse_configuration_package(value: object) -> ConfigurationPackage:
    """Parse declared package content without exposing rejected input in errors."""
    if type(value) is not dict:  # pylint: disable=unidiomatic-typecheck  # Exact JSON object roots only.
        raise _configuration_package_parse_error((("/", "non-JSON value"),))
    validation_errors: object | None = None
    try:
        return ConfigurationPackage.model_validate(value)
    except ValidationError as exc:
        validation_errors = exc.errors(include_input=False, include_context=True, include_url=False)
    failures = _decode_validation_errors(validation_errors)
    raise _configuration_package_parse_error(failures)


def sort_findings(findings: Sequence[ValidationFinding]) -> tuple[ValidationFinding, ...]:
    """Return findings in the stable cross-interface order."""
    severity_order = {"error": 0, "warning": 1}
    return tuple(sorted(findings, key=lambda item: (item.location, severity_order[item.severity], item.code)))
