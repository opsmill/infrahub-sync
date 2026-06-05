"""Compute write-order tiers for a SyncConfig from its schema_mapping.

The dep graph is derived purely from `SchemaMappingField.reference` entries on
each `SchemaMappingModel`. Self-references (a kind that references itself, e.g.
LocationGeneric.parent) are not write-order edges and are excluded.

Edges where the source field is not in the model's `identifiers` are
"optional": the dependent peer is not part of uniqueness, so the write can be
deferred and the cycle (if any) is broken automatically. Edges where the field
is in `identifiers` are "identity-bearing" — a cycle through identity edges is a
real schema problem and is surfaced to the operator.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from infrahub_sync import SchemaMappingModel

logger = logging.getLogger(__name__)


def build_dependency_graph(schema_mapping: list[SchemaMappingModel]) -> dict[str, set[str]]:
    """Return the dep graph keyed by kind name. Self-edges are excluded."""
    deps: dict[str, set[str]] = {}
    for sm in schema_mapping:
        bucket = deps.setdefault(sm.name, set())
        for field in sm.fields or []:
            if not field.reference:
                continue
            if field.reference == sm.name:
                continue
            bucket.add(field.reference)
    return deps


def _collect_optional_edges(
    schema_mapping: list[SchemaMappingModel],
) -> set[tuple[str, str]]:
    """Edges (src, dst) where the field carrying the reference is NOT part of
    `identifiers` for src. Missing the peer doesn't break uniqueness, so we
    can drop the edge to resolve a cycle."""
    optional: set[tuple[str, str]] = set()
    for sm in schema_mapping:
        identity_set = set(sm.identifiers or [])
        for field in sm.fields or []:
            if not field.reference or field.reference == sm.name:
                continue
            if field.name not in identity_set:
                optional.add((sm.name, field.reference))
    return optional


_MAX_CYCLE_BREAK_ATTEMPTS = 50


def _consecutive_pairs(nodes: list[str]) -> list[tuple[str, str]]:
    """Yield successive `(nodes[i], nodes[i+1])` edges along a reported cycle."""
    return [(nodes[i], nodes[i + 1]) for i in range(len(nodes) - 1)]


def compute_tiers(
    schema_mapping: list[SchemaMappingModel],
) -> tuple[list[set[str]], list[tuple[str, str]]]:
    """Return (tiers, dropped_optional_edges).

    Raises `infrahub_sdk.topological_sort.DependencyCycleExistsError` when a
    cycle goes through identity-bearing edges only.
    """
    from infrahub_sdk.topological_sort import (
        DependencyCycleExistsError,
        topological_sort,
    )

    deps = build_dependency_graph(schema_mapping)
    optional = _collect_optional_edges(schema_mapping)
    dropped: list[tuple[str, str]] = []

    for _ in range(_MAX_CYCLE_BREAK_ATTEMPTS):
        try:
            return topological_sort(deps), dropped
        except DependencyCycleExistsError as exc:
            # Drop every optional edge appearing in *any* reported cycle in one
            # pass, then retry — typically resolves in a single extra sort
            # instead of one-edge-per-iteration (O(n_cycles) sorts). The bounded
            # loop remains only as a safety net should dropping these edges
            # expose a fresh cycle. Sorted for deterministic `dropped` output.
            to_drop = {
                (src, dst)
                for cycle in exc.cycles
                for src, dst in _consecutive_pairs(list(cycle))
                if (src, dst) in optional and dst in deps.get(src, set())
            }
            if not to_drop:
                raise
            for src, dst in sorted(to_drop):
                deps[src].discard(dst)
                dropped.append((src, dst))
    msg = "Exceeded cycle-break budget; aborting tier computation."
    raise RuntimeError(msg)


def flatten_tiers(tiers: list[set[str]]) -> list[str]:
    """Deterministic serial ordering: sort within tier, preserve tier order."""
    return [name for tier in tiers for name in sorted(tier)]
