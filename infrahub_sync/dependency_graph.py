"""Compute write-order tiers for a SyncConfig from its schema_mapping.

The dep graph is derived from `SchemaMappingField.reference` entries on each
`SchemaMappingModel`, expanding known generics to mapped peer kinds. Self-references
(a kind that references itself, e.g. LocationGeneric.parent) are not write-order
edges and are excluded.

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
    from collections.abc import Mapping

    from infrahub_sync import SchemaMappingModel

logger = logging.getLogger(__name__)


class UnresolvedGenericReferenceError(ValueError):
    """A mapped reference names a generic with no mapped concrete peer."""


def _reference_targets(
    schema_mapping: list[SchemaMappingModel], generic_peers: Mapping[str, tuple[str, ...]] | None
) -> dict[str, tuple[str, ...]]:
    """Expand known generics to mapped concrete kinds, refusing empty expansions."""
    mapped = {model.name for model in schema_mapping}
    targets: dict[str, tuple[str, ...]] = {}
    for model in schema_mapping:
        for field in model.fields or []:
            reference = field.reference
            if not reference or reference in targets:
                continue
            if generic_peers is None or reference not in generic_peers:
                targets[reference] = (reference,)
                continue
            peers = mapped.intersection(generic_peers[reference])
            if reference in mapped:
                peers.add(reference)
            if not peers:
                msg = (
                    f"schema_mapping kind {model.name!r} field {field.name!r} references generic "
                    f"{reference!r}, but none of its concrete peer kinds are mapped"
                )
                raise UnresolvedGenericReferenceError(msg)
            targets[reference] = tuple(sorted(peers))
    return targets


def build_dependency_graph(
    schema_mapping: list[SchemaMappingModel], generic_peers: Mapping[str, tuple[str, ...]] | None = None
) -> dict[str, set[str]]:
    """Return dependencies keyed by mapped kind, excluding self-edges."""
    targets = _reference_targets(schema_mapping, generic_peers)
    deps: dict[str, set[str]] = {}
    for sm in schema_mapping:
        bucket = deps.setdefault(sm.name, set())
        for field in sm.fields or []:
            if not field.reference:
                continue
            bucket.update(peer for peer in targets[field.reference] if peer != sm.name)
    return deps


def _collect_optional_edges(
    schema_mapping: list[SchemaMappingModel],
    generic_peers: Mapping[str, tuple[str, ...]] | None = None,
) -> set[tuple[str, str]]:
    """Edges (src, dst) where the field carrying the reference is NOT part of
    `identifiers` for src. Missing the peer doesn't break uniqueness, so we
    can drop the edge to resolve a cycle."""
    optional: set[tuple[str, str]] = set()
    targets = _reference_targets(schema_mapping, generic_peers)
    for sm in schema_mapping:
        identity_set = set(sm.identifiers or [])
        for field in sm.fields or []:
            if not field.reference:
                continue
            if field.name not in identity_set:
                optional.update((sm.name, peer) for peer in targets[field.reference] if peer != sm.name)
    return optional


def _cyclic_components(deps: Mapping[str, set[str]]) -> list[set[str]]:
    """Find strongly connected components with cycles in stable traversal order."""
    next_index = 0
    indices: dict[str, int] = {}
    lowlinks: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    components: list[set[str]] = []

    def visit(node: str) -> None:
        nonlocal next_index
        indices[node] = next_index
        lowlinks[node] = next_index
        next_index += 1
        stack.append(node)
        on_stack.add(node)
        for peer in sorted(deps.get(node, ())):
            if peer not in indices:
                visit(peer)
                lowlinks[node] = min(lowlinks[node], lowlinks[peer])
            elif peer in on_stack:
                lowlinks[node] = min(lowlinks[node], indices[peer])
        if lowlinks[node] == indices[node]:
            component: set[str] = set()
            while stack:
                peer = stack.pop()
                on_stack.remove(peer)
                component.add(peer)
                if peer == node:
                    break
            if len(component) > 1 or node in deps.get(node, set()):
                components.append(component)

    for node in sorted(set(deps).union(*(set(peers) for peers in deps.values()))):
        if node not in indices:
            visit(node)
    return components


def compute_tiers(
    schema_mapping: list[SchemaMappingModel],
    generic_peers: Mapping[str, tuple[str, ...]] | None = None,
) -> tuple[list[set[str]], list[tuple[str, str]]]:
    """Return (tiers, dropped_optional_edges).

    Raises `infrahub_sdk.topological_sort.DependencyCycleExistsError` when a
    cycle goes through identity-bearing edges only.
    """
    from infrahub_sdk.topological_sort import (
        DependencyCycleExistsError,
        topological_sort,
    )

    deps = build_dependency_graph(schema_mapping, generic_peers)
    optional = _collect_optional_edges(schema_mapping, generic_peers)
    dropped: list[tuple[str, str]] = []

    while True:
        try:
            return topological_sort(deps), dropped
        except DependencyCycleExistsError:
            # The SDK reports cycles by walking sets, so their edges depend on the
            # process hash seed. Every edge within a cyclic strongly connected
            # component belongs to a cycle. Drop its optional edges in one pass.
            to_drop = {
                (src, dst)
                for component in _cyclic_components(deps)
                for src in component
                for dst in deps.get(src, set()).intersection(component)
                if (src, dst) in optional
            }
            if not to_drop:
                raise
            for src, dst in sorted(to_drop):
                deps[src].discard(dst)
                dropped.append((src, dst))


def flatten_tiers(tiers: list[set[str]]) -> list[str]:
    """Deterministic serial ordering: sort within tier, preserve tier order."""
    return [name for tier in tiers for name in sorted(tier)]
