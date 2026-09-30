"""Tests for infrahub_sync.dependency_graph."""

from __future__ import annotations

from infrahub_sync import SchemaMappingField, SchemaMappingModel
from infrahub_sync.dependency_graph import UnresolvedGenericReferenceError, build_dependency_graph, compute_tiers


def _sm(name: str, fields: list[tuple[str, str | None]], identifiers: list[str] | None = None) -> SchemaMappingModel:
    """Build a SchemaMappingModel from (field_name, reference) tuples."""
    return SchemaMappingModel(
        name=name,
        identifiers=identifiers,
        fields=[SchemaMappingField(name=fn, reference=ref) for fn, ref in fields],
    )


def test_build_dependency_graph_simple_chain() -> None:
    mapping = [
        _sm("Tag", [("name", None)], identifiers=["name"]),
        _sm("Device", [("name", None), ("tag", "Tag")], identifiers=["name"]),
        _sm("Interface", [("name", None), ("device", "Device")], identifiers=["name", "device"]),
    ]
    deps = build_dependency_graph(mapping)
    assert deps == {"Tag": set(), "Device": {"Tag"}, "Interface": {"Device"}}


def test_build_dependency_graph_unions_repeated_names() -> None:
    """Same kind name appearing multiple times merges field references."""
    mapping = [
        _sm("RoleGeneric", [("name", None)]),
        _sm("RoleGeneric", [("name", None), ("tag", "BuiltinTag")]),
        _sm("BuiltinTag", [("name", None)]),
    ]
    deps = build_dependency_graph(mapping)
    assert deps == {"RoleGeneric": {"BuiltinTag"}, "BuiltinTag": set()}


def test_compute_tiers_no_cycle() -> None:
    from infrahub_sync.dependency_graph import compute_tiers

    mapping = [
        _sm("Tag", [("name", None)], identifiers=["name"]),
        _sm("Device", [("name", None), ("tag", "Tag")], identifiers=["name"]),
        _sm("Interface", [("name", None), ("device", "Device")], identifiers=["name", "device"]),
    ]
    tiers, dropped = compute_tiers(mapping)
    assert tiers == [{"Tag"}, {"Device"}, {"Interface"}]
    assert dropped == []


def test_compute_tiers_drops_optional_cycle_edge() -> None:
    from infrahub_sync.dependency_graph import compute_tiers

    # AS -> Device (optional, AS.routing_device not in identifiers)
    # Device -> AS (identity-bearing — AS is part of Device.identifiers)
    mapping = [
        _sm(
            "RoutingAS",
            [("asn", None), ("routing_device", "Device")],
            identifiers=["asn"],
        ),
        _sm(
            "Device",
            [("name", None), ("asn", "RoutingAS")],
            identifiers=["name", "asn"],
        ),
    ]
    tiers, dropped = compute_tiers(mapping)
    assert dropped == [("RoutingAS", "Device")]
    assert tiers == [{"RoutingAS"}, {"Device"}]


def test_compute_tiers_raises_on_identity_cycle() -> None:
    import pytest
    from infrahub_sdk.topological_sort import DependencyCycleExistsError

    from infrahub_sync.dependency_graph import compute_tiers

    mapping = [
        _sm("A", [("b", "B")], identifiers=["b"]),
        _sm("B", [("a", "A")], identifiers=["a"]),
    ]
    with pytest.raises(DependencyCycleExistsError):
        compute_tiers(mapping)


def test_compute_tiers_for_netbox_example_config() -> None:
    """End-to-end against examples/netbox_to_infrahub/config.yml."""
    from pathlib import Path

    import yaml

    from infrahub_sync import SyncConfig
    from infrahub_sync.dependency_graph import compute_tiers, flatten_tiers

    config_path = Path(__file__).resolve().parent.parent / "examples" / "netbox_to_infrahub" / "config.yml"
    with config_path.open() as fh:
        data = yaml.safe_load(fh)

    cfg = SyncConfig(**data)
    tiers, _dropped = compute_tiers(cfg.schema_mapping)

    # Tier 0 must include leaf-like kinds with no outgoing refs.
    assert "BuiltinTag" in tiers[0]
    # Every mapped kind must appear somewhere in the computed tiers.
    # (cfg.order is empty for examples that opt into auto-tiering, so this
    # loop would be a no-op against `cfg.order` and miss regressions.)
    flat = set(flatten_tiers(tiers))
    for name in {m.name for m in cfg.schema_mapping}:
        assert name in flat, f"{name} missing from computed tiers"


def test_generic_reference_waits_for_mapped_peers_at_different_depths() -> None:
    mapping = [
        _sm("LocationBuilding", []),
        _sm("LocationFloor", [("parent", "LocationBuilding")]),
        _sm("LocationRackUnit", [("parent", "LocationFloor")]),
        _sm("DcimDevice", [("location", "LocationHosting")]),
    ]
    peers = {"LocationHosting": ("LocationFloor", "LocationRackUnit", "UnmappedKind")}

    assert build_dependency_graph(mapping, peers)["DcimDevice"] == {"LocationFloor", "LocationRackUnit"}
    tiers, dropped = compute_tiers(mapping, peers)
    assert tiers == [
        {"LocationBuilding"},
        {"LocationFloor"},
        {"LocationRackUnit"},
        {"DcimDevice"},
    ]
    assert dropped == []
    assert "LocationHosting" not in set().union(*tiers)


def test_unresolved_generic_reference_is_a_configuration_error() -> None:
    import pytest

    mapping = [_sm("DcimDevice", [("location", "LocationHosting")])]
    with pytest.raises(UnresolvedGenericReferenceError, match=r"LocationHosting.*none of its concrete peer kinds"):
        compute_tiers(mapping, {"LocationHosting": ("LocationFloor", "LocationRackUnit")})


def test_optional_generic_edge_breaks_cycle_deterministically() -> None:
    mapping = [
        _sm("A", [("peer", "SharedGeneric")], identifiers=["name"]),
        _sm("B", [("a", "A")], identifiers=["a"]),
    ]
    tiers, dropped = compute_tiers(mapping, {"SharedGeneric": ("B",)})
    assert tiers == [{"A"}, {"B"}]
    assert dropped == [("A", "B")]
