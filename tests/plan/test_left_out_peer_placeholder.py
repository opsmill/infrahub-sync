"""A reference to a record the load left out derives to a placeholder pair (spec 009 T045).

Research R7: a reference to a record of the load report, or to a peer marked "not in the
source", resolves to a placeholder pair marked left out instead of raising
`SourcePeerUnresolvedError`. The selection step removes every placeholder later.

The T001 fixture holds both cases: the five interfaces `eth0` to `eth4` refer to `core-1`,
which the load could not build (`wrong_value_type`), and the IP address `10.0.0.9/32` refers
through `vrf` to a VRF the source does not return.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from infrahub_sync.plan.derive import operations_from_diff
from infrahub_sync.plan.errors import SourcePeerUnresolvedError
from infrahub_sync.plan.left_out_peer import LEFT_OUT_PEER_KEY, LeftOutPeers, holds_left_out_peer, is_left_out_peer
from infrahub_sync.runtime_schema.worker import bind_runtime_models
from tests.plan.problem_fixtures import (
    MISSING_VRF_SOURCE_ID,
    PROBLEM_DEVICE_DEPENDENTS,
    PROBLEM_DEVICE_SOURCE_ID,
    ProblemFixtureAdapter,
    Variant,
    build_config,
    build_models,
    build_source_adapter,
)

if TYPE_CHECKING:
    from infrahub_sync.plan.models import PlannedOperation


def _plan_operations(source: ProblemFixtureAdapter) -> list[PlannedOperation]:
    config = build_config()
    destination = ProblemFixtureAdapter(target="destination", adapter=config.destination, config=config, records={})
    bind_runtime_models(destination, build_models(config))
    destination.load()
    return operations_from_diff(
        destination.diff_from(source),
        config=config,
        tier_of=lambda _: 0,
        source_adapter=source,
        destination_adapter=destination,
    )


@pytest.fixture(name="operations")
def _operations() -> list[PlannedOperation]:
    source = build_source_adapter(Variant.ALL)
    source.load()
    return _plan_operations(source)


def _by_name(operations: list[PlannedOperation], kind: str, field: str, value: str) -> list[PlannedOperation]:
    return [
        operation
        for operation in operations
        if operation.kind == kind and (operation.payload or {}).get(field) == value
    ]


def test_a_reference_to_a_record_the_load_could_not_build_derives_to_a_placeholder(
    operations: list[PlannedOperation],
) -> None:
    interfaces = [
        operation
        for operation in operations
        if operation.kind == "InterfacePhysical" and is_left_out_peer(operation.identity["device"]["identity"])
    ]

    assert len(interfaces) == PROBLEM_DEVICE_DEPENDENTS
    for operation in interfaces:
        device = operation.identity["device"]
        assert device["peer_kind"] == "DcimDevice"
        assert device["identity"][LEFT_OUT_PEER_KEY]["cause"] == "not_loaded"
        assert device["identity"][LEFT_OUT_PEER_KEY]["source_id"] == PROBLEM_DEVICE_SOURCE_ID
        (relationship,) = operation.relationships or []
        assert relationship.field == "device"
        assert is_left_out_peer(relationship.peers[0])
        assert holds_left_out_peer(operation)


def test_a_reference_to_a_peer_not_in_the_source_derives_to_a_placeholder(
    operations: list[PlannedOperation],
) -> None:
    (address,) = _by_name(operations, "IpamIPAddress", "address", "10.0.0.9/32")
    relationships = {relationship.field: relationship for relationship in address.relationships or []}

    assert relationships["vrf"].peer_kind == "IpamVRF"
    marker: dict[str, Any] = relationships["vrf"].peers[0][LEFT_OUT_PEER_KEY]
    assert marker["cause"] == "not_in_source"
    assert marker["source_id"] == MISSING_VRF_SOURCE_ID
    assert holds_left_out_peer(address)


def test_operations_without_a_left_out_peer_hold_no_placeholder(operations: list[PlannedOperation]) -> None:
    (address,) = _by_name(operations, "IpamIPAddress", "address", "10.0.0.1/32")
    (leaf,) = _by_name(operations, "DcimDevice", "name", "leaf-1")

    assert not holds_left_out_peer(address)
    assert not holds_left_out_peer(leaf)


def test_the_left_out_peers_come_from_the_load_report_only() -> None:
    source = build_source_adapter(Variant.ALL)
    source.load()
    peers = LeftOutPeers.from_adapter(source)

    assert set(peers.by_kind) == {"DcimDevice"}
    assert set(peers.by_kind["DcimDevice"].values()) == {PROBLEM_DEVICE_SOURCE_ID}


def test_without_a_load_report_the_reference_still_refuses() -> None:
    """A store holding a reference to a record nobody recorded keeps the strict refusal."""
    source = build_source_adapter(Variant.ALL)
    source.load()
    source._admission.load_report.clear()

    with pytest.raises(SourcePeerUnresolvedError):
        _plan_operations(source)
