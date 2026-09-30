"""Tests for infrahub_sync.dependency_graph."""

from __future__ import annotations

import pytest

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
    mapping = [_sm("DcimDevice", [("location", "LocationHosting")])]
    with pytest.raises(UnresolvedGenericReferenceError, match=r"LocationHosting.*none of its concrete peer kinds"):
        compute_tiers(mapping, {"LocationHosting": ("LocationFloor", "LocationRackUnit")})


def test_mapped_generic_remains_a_dependency_even_without_concrete_peers() -> None:
    mapping = [_sm("LocationHosting", []), _sm("DcimDevice", [("location", "LocationHosting")])]

    assert build_dependency_graph(mapping, {"LocationHosting": ("UnmappedKind",)})["DcimDevice"] == {"LocationHosting"}
    assert compute_tiers(mapping, {"LocationHosting": ("UnmappedKind",)})[0] == [
        {"LocationHosting"},
        {"DcimDevice"},
    ]


def test_mapped_generic_remains_a_dependency_alongside_mapped_concrete_peers() -> None:
    mapping = [
        _sm("LocationHosting", []),
        _sm("LocationFloor", []),
        _sm("DcimDevice", [("location", "LocationHosting")]),
    ]

    assert build_dependency_graph(mapping, {"LocationHosting": ("LocationFloor",)})["DcimDevice"] == {
        "LocationHosting",
        "LocationFloor",
    }


def test_optional_generic_edge_breaks_cycle_deterministically() -> None:
    mapping = [
        _sm("A", [("peer", "SharedGeneric")], identifiers=["name"]),
        _sm("B", [("a", "A")], identifiers=["a"]),
    ]
    tiers, dropped = compute_tiers(mapping, {"SharedGeneric": ("B",)})
    assert tiers == [{"A"}, {"B"}]
    assert dropped == [("A", "B")]


@pytest.mark.parametrize("reference_kind", ["concrete", "generic"])
def test_optional_peer_clique_has_stable_tiers(reference_kind: str) -> None:
    siblings = ("PeerA", "PeerB", "PeerC", "PeerD")
    if reference_kind == "generic":
        mapping = [
            *[_sm(kind, [("parent", "SharedGeneric")], identifiers=["name"]) for kind in siblings],
            _sm("Device", [("location", "SharedGeneric")], identifiers=["name"]),
        ]
        peers = {"SharedGeneric": siblings}
    else:
        mapping = [
            *[
                _sm(kind, [(f"peer_{peer}", peer) for peer in siblings if peer != kind], identifiers=["name"])
                for kind in siblings
            ],
            _sm("Device", [(f"location_{peer}", peer) for peer in siblings], identifiers=["name"]),
        ]
        peers = None

    tiers, dropped = compute_tiers(mapping, peers)

    assert tiers == [{"PeerC", "PeerD"}, {"PeerA", "PeerB"}, {"Device"}]
    assert dropped == [
        ("PeerA", "PeerB"),
        ("PeerB", "PeerA"),
        ("PeerB", "PeerC"),
        ("PeerC", "PeerA"),
        ("PeerC", "PeerB"),
        ("PeerC", "PeerD"),
        ("PeerD", "PeerA"),
        ("PeerD", "PeerB"),
        ("PeerD", "PeerC"),
    ]


@pytest.mark.parametrize("reverse_mapping", [False, True])
def test_cycle_breaking_keeps_optional_edges_outside_discovered_cycles(*, reverse_mapping: bool) -> None:
    mapping = [
        _sm("K0", [("f_K1", "K1"), ("f_K2", "K2")], identifiers=["name"]),
        _sm("K1", [("f_K2", "K2")], identifiers=["f_K2"]),
        _sm("K2", [("f_K0", "K0")], identifiers=["name"]),
    ]
    if reverse_mapping:
        mapping.reverse()

    tiers, dropped = compute_tiers(mapping)

    assert tiers == [{"K2"}, {"K0", "K1"}]
    assert dropped == [("K0", "K1"), ("K2", "K0")]
