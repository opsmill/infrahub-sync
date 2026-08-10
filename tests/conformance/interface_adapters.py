"""Explicit adapters from executed interface observations to the DB-006 envelope."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import TYPE_CHECKING

from tests.conformance.oracle import CanonicalEnvelope, Surface

if TYPE_CHECKING:
    from infrahub_sync.plan.review import SavedPlan
    from infrahub_sync.product_store import ProductRun


def product_envelope(  # noqa: PLR0913 - the canonical schema has six required components.
    *,
    surface: Surface,
    operation: str,
    saved: SavedPlan,
    record: ProductRun,
    artifact: bytes,
    destination_effects: Mapping[str, int],
) -> CanonicalEnvelope:
    """Retain the complete product record/artifact while removing only transport rendering."""
    counts = {action: saved.summary().by_action.get(action, 0) for action in ("create", "update", "delete")}
    return CanonicalEnvelope(
        surface=surface,
        operation=operation,
        plan_fingerprint=saved.manifest.plan_checksum,
        counts=counts,
        outcome=record.outcome or record.phase,
        destination_effects=dict(destination_effects),
        product_record=record.model_dump(mode="json"),
        result=dict(record.results),
        artifact_references=[item.model_dump(mode="json") for item in record.artifact_refs],
        artifact_semantics=json.loads(artifact),
    )


def serialized_boundaries(*values: object) -> bytes:
    """Render producing boundaries for the recognizable-sentinel scan."""
    return json.dumps(values, default=str, sort_keys=True).encode()
