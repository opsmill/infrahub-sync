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
            broken = False
            for cycle in exc.cycles:
                cycle_list = list(cycle)
                for i in range(len(cycle_list) - 1):
                    src, dst = cycle_list[i], cycle_list[i + 1]
                    if (src, dst) in optional and dst in deps.get(src, set()):
                        deps[src].discard(dst)
                        dropped.append((src, dst))
                        broken = True
                        break
                if broken:
                    break
            if not broken:
                raise
    msg = "Exceeded cycle-break budget; aborting tier computation."
    raise RuntimeError(msg)


def flatten_tiers(tiers: list[set[str]]) -> list[str]:
    """Deterministic serial ordering: sort within tier, preserve tier order."""
    return [name for tier in tiers for name in sorted(tier)]
