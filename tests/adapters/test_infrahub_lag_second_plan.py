"""Shallow `InterfaceLag` peers that miss `device`, across two plans (spec 019 T012, #167).

In #167, a plan read `InterfacePhysical` nodes whose one-peer relationship `bundle` pointed at
an `InterfaceLag` that the SDK store held only as a shallow peer: its `name` was set and its
`device` (an identifier of `InterfaceLag`) was not. The conversion raised `PeerIdentifierError`.

These tests run the real converter, `InfrahubAdapter.infrahub_node_to_diffsync`, over such
peers with the fakes of `tests/adapters/test_infrahub_peer_identifier.py`, once for each of two
plans (a new adapter each time, as each plan builds one). The converter hydrates the shallow
peer by its id (`_resolve_peer_unique_id`) and gives the same `bundle` value in both plans.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from infrahub_sync import SchemaMappingField, SchemaMappingModel, SyncAdapter, SyncConfig
from infrahub_sync.adapters.infrahub import InfrahubAdapter
from tests.adapters.test_infrahub_peer_identifier import (
    _FakeClient,
    _FakeDeviceModel,
    _FakeLagModel,
    _FakePeerModel,
    _FakeStore,
    _make_sdk_node,
    _rel,
    _schema,
)

if TYPE_CHECKING:
    from types import SimpleNamespace

DEVICE_SCHEMA = _schema("InfraDevice", ["name"])
# `name` is the only attribute, so a shallow peer that holds it passes the attribute check of
# `resolve_peer_node` and reaches `_resolve_peer_unique_id` without its `device`, as in #167.
LAG_SCHEMA = _schema("InterfaceLag", ["name"], [_rel("device", "InfraDevice")])
PHYSICAL_SCHEMA = _schema(
    "InterfacePhysical", ["name"], [_rel("device", "InfraDevice"), _rel("bundle", "InterfaceLag")]
)
SCHEMAS = (DEVICE_SCHEMA, LAG_SCHEMA, PHYSICAL_SCHEMA)

MEMBERS = ("eth0", "eth1")


class _FakePhysicalModel(_FakePeerModel):
    _identifiers = ("device", "name")


def _mapping(kind: str, fields: list[str], identifiers: list[str]) -> SchemaMappingModel:
    return SchemaMappingModel(
        name=kind,
        mapping=kind,
        identifiers=identifiers,
        fields=[SchemaMappingField(name=name, mapping=name) for name in fields],
    )


class _LagHarness(InfrahubAdapter):
    """The Infrahub adapter of one plan, with the fake SDK client and no server."""

    client: _FakeClient
    schema: dict[str, SimpleNamespace]

    def __init__(self, *, rehydrated_peer: object) -> None:
        self.client = _FakeClient(rehydrated_peer=rehydrated_peer)
        self._diffsync_store = _FakeStore()
        self.store = self._diffsync_store  # ty: ignore[invalid-assignment]
        self._peer_unique_ids = {}
        self._instances: list[object] = []
        self.InfraDevice = _FakeDeviceModel
        self.InterfaceLag = _FakeLagModel
        self.InterfacePhysical = _FakePhysicalModel
        self.schema = {schema.kind: schema for schema in SCHEMAS}
        self.config = SyncConfig(
            name="lag-second-plan",
            source=SyncAdapter(name="source", adapter="x:x"),
            destination=SyncAdapter(name="destination", adapter="x:x"),
            order=["InfraDevice", "InterfaceLag", "InterfacePhysical"],
            schema_mapping=[
                _mapping("InfraDevice", ["name"], ["name"]),
                _mapping("InterfaceLag", ["device", "name"], ["device", "name"]),
                _mapping("InterfacePhysical", ["device", "name", "bundle"], ["device", "name"]),
            ],
        )

    def update_or_add_model_instance(self, item: object) -> None:  # ty: ignore[invalid-method-override]
        self._instances.append(item)


def _plan_once() -> tuple[list[dict[str, Any]], _LagHarness]:
    """Convert both LAG members the way one plan does; the LAG is in the SDK store only shallow."""
    full_lag = _make_sdk_node(LAG_SCHEMA, "lag-id", {"name": "ae0"}, {"device": ("InfraDevice", "device-id")})
    harness = _LagHarness(rehydrated_peer=full_lag)
    device = _make_sdk_node(DEVICE_SCHEMA, "device-id", {"name": "leaf-1"})
    shallow_lag = _make_sdk_node(LAG_SCHEMA, "lag-id", {"name": "ae0"})
    harness.client.store.set(key="device-id", node=device)
    harness.client.store.set(key="lag-id", node=shallow_lag)
    members = [
        _make_sdk_node(
            PHYSICAL_SCHEMA,
            f"{name}-id",
            {"name": name},
            {"device": ("InfraDevice", "device-id"), "bundle": ("InterfaceLag", "lag-id")},
        )
        for name in MEMBERS
    ]
    converted = [harness.infrahub_node_to_diffsync(member) for member in members]  # ty: ignore[invalid-argument-type]
    return converted, harness


def test_the_shallow_lag_peer_converts_without_a_peer_identifier_error() -> None:
    converted, _ = _plan_once()

    assert converted == [
        {"local_id": f"{name}-id", "name": name, "device": "leaf-1", "bundle": "leaf-1|ae0"} for name in MEMBERS
    ]


def test_the_shallow_lag_peer_is_hydrated_once_per_plan_by_its_id() -> None:
    _, harness = _plan_once()

    assert harness.client.get_calls == [
        {"id": "lag-id", "kind": "InterfaceLag", "include": ["device", "name"], "populate_store": False}
    ]


def test_two_plans_convert_the_lag_members_to_the_same_data() -> None:
    first, _ = _plan_once()
    second, _ = _plan_once()

    assert first == second
