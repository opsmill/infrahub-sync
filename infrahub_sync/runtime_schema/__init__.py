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

__all__ = [
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
    "normalize_destination_schema",
]
