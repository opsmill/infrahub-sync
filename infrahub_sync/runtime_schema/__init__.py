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
from .projection import (
    canonical_consumed_schema_projection,
    compute_consumed_schema_fingerprint,
)
from .worker import (
    RuntimeModelPlan,
    bind_runtime_models,
    build_runtime_model_plan,
    read_destination_schema_snapshot,
)

__all__ = [
    "ATTRIBUTE_TYPE_DOMAIN",
    "CARDINALITIES",
    "DestinationSchemaSnapshot",
    "DestinationSchemaUnavailableError",
    "MissingMappedKindError",
    "NormalizedAttribute",
    "NormalizedKind",
    "NormalizedRelationship",
    "RuntimeModelPlan",
    "RuntimeSchemaError",
    "UnsupportedDestinationProfileError",
    "UnsupportedSchemaSemanticsError",
    "bind_runtime_models",
    "build_runtime_model_plan",
    "build_runtime_models",
    "canonical_consumed_schema_projection",
    "compute_consumed_schema_fingerprint",
    "mapped_attribute_kinds",
    "normalize_destination_schema",
    "read_destination_schema_snapshot",
]
