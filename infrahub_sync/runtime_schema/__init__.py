"""Runtime destination-schema discovery and in-memory DiffSync model construction."""

from __future__ import annotations

from .domain import (
    CARDINALITIES,
    DestinationSchemaSnapshot,
    NormalizedAttribute,
    NormalizedKind,
    NormalizedRelationship,
    normalize_destination_schema,
)
from .errors import (
    DestinationSchemaUnavailableError,
    MissingMappedKindError,
    RuntimeSchemaError,
    UnsupportedDestinationProfileError,
    UnsupportedSchemaSemanticsError,
)
from .models import ATTRIBUTE_TYPE_DOMAIN, build_runtime_models, mapped_attribute_kinds

__all__ = [
    "ATTRIBUTE_TYPE_DOMAIN",
    "CARDINALITIES",
    "DestinationSchemaSnapshot",
    "DestinationSchemaUnavailableError",
    "MissingMappedKindError",
    "NormalizedAttribute",
    "NormalizedKind",
    "NormalizedRelationship",
    "RuntimeSchemaError",
    "UnsupportedDestinationProfileError",
    "UnsupportedSchemaSemanticsError",
    "build_runtime_models",
    "mapped_attribute_kinds",
    "normalize_destination_schema",
]
