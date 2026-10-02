"""Destination generic-peer metadata for ordering and plan derivation."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

from infrahub_sdk.schema.main import GenericSchemaAPI

if TYPE_CHECKING:
    from infrahub_sync.runtime_schema import RuntimeModelPlan


def generic_peers_for_destination(
    destination: object, runtime_models: RuntimeModelPlan | None
) -> Mapping[str, tuple[str, ...]]:
    """Use the run's validated snapshot, or the direct adapter's live schema."""
    if runtime_models is not None:
        return runtime_models.generic_peers
    schema = getattr(destination, "schema", None)
    if not isinstance(schema, Mapping):
        return {}
    return {kind: tuple(node.used_by) for kind, node in schema.items() if isinstance(node, GenericSchemaAPI)}
