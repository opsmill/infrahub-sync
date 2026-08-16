"""Unit tests for InfrahubAdapter._resolve_peer_unique_id error and skip paths.

The full adapter touches an Infrahub server, so these tests build a minimal
stand-in adapter that reuses the real helper. The focused cases cover:

- Rich errors for missing peer identifier keys.
- Skipping missing identifiers when continue_on_error=True.
- Bounded hydration of missing relationship identifiers.
- Cache preservation and reuse for repeated peer references.
- Propagation of unexpected hydration errors.
- Rich errors after incomplete hydration.
- Unique ID resolution for complete peers.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any

import pytest
from diffsync.exceptions import ObjectNotFound
from infrahub_sdk.exceptions import NodeNotFoundError

from infrahub_sync import SchemaMappingField, SchemaMappingModel, SyncAdapter, SyncConfig
from infrahub_sync.adapters.infrahub import InfrahubAdapter, PeerIdentifierError, resolve_peer_node


class _FakeStore:
    def __init__(self) -> None:
        self._items: dict[tuple[str, str], object] = {}
        self.get_error: Exception | None = None

    def get(
        self,
        *,
        model: str | None = None,
        identifier: str | None = None,
        kind: str | None = None,
        key: str | None = None,
        raise_when_missing: bool = True,
    ) -> object | None:
        if self.get_error is not None:
            raise self.get_error
        store_key = (model or kind or "", identifier or key or "")
        if store_key not in self._items:
            if raise_when_missing:
                msg = f"{store_key} not present in fake store"
                raise ObjectNotFound(msg)
            return None
        return self._items[store_key]

    def seed(self, *, model: str, identifier: str, item: object) -> None:
        self._items[model, identifier] = item

    def set(self, *, key: str, node: object) -> None:  # match client.store.set signature
        kind = getattr(node, "_schema", SimpleNamespace(kind="?")).kind
        self._items[kind, key] = node
        if node_id := getattr(node, "id", None):
            self._items[kind, node_id] = node


class _FakeClient:
    def __init__(
        self,
        rehydrated_peer: object | None = None,
        *,
        raise_not_found: bool = False,
        get_error: Exception | None = None,
    ) -> None:
        self.store = _FakeStore()
        self.rehydrated_peer = rehydrated_peer
        self.raise_not_found = raise_not_found
        self.get_error = get_error
        self.get_calls: list[dict[str, object]] = []

    def get(self, **kwargs: object) -> object | None:
        self.get_calls.append(kwargs)
        if self.get_error is not None:
            raise self.get_error
        if self.raise_not_found:
            raise NodeNotFoundError(identifier={"id": [str(kwargs["id"])]})
        return self.rehydrated_peer


class _FakePeerModel:
    _identifiers = ("name", "organization")

    def __init__(self, **kwargs: object) -> None:
        self._kwargs = kwargs

    @classmethod
    def create_unique_id(cls, **kwargs: object) -> str:
        return "|".join(str(kwargs[k]) for k in cls._identifiers)

    def get_unique_id(self) -> str:
        return type(self).create_unique_id(**self._kwargs)


class _FakeLagModel(_FakePeerModel):
    _identifiers = ("device", "name")


class _FakeDeviceModel(_FakePeerModel):
    _identifiers = ("name",)


class _Harness(InfrahubAdapter):
    """Skip the heavy __init__ that needs a real Infrahub server."""

    client: _FakeClient

    def __init__(
        self,
        *,
        continue_on_error: bool = False,
        rehydrated_peer: object | None = None,
        raise_not_found: bool = False,
        get_error: Exception | None = None,
    ) -> None:
        # bypass the parent chain entirely
        self.client = _FakeClient(
            rehydrated_peer=rehydrated_peer,
            raise_not_found=raise_not_found,
            get_error=get_error,
        )
        self._diffsync_store = _FakeStore()
        self.store = self._diffsync_store  # ty: ignore[invalid-assignment]
        self.continue_on_error = continue_on_error
        self._peer_unique_ids = {}
        self._peer_identifier_errors = {}
        self._instances: list[object] = []
        # Register the fake peer model under its kind so getattr(self, kind) works.
        self.LocationGeneric = _FakePeerModel

    def update_or_add_model_instance(self, item: object) -> None:  # ty: ignore[invalid-method-override]
        self._instances.append(item)
        self._diffsync_store.seed(
            model="LocationGeneric",
            identifier=item.get_unique_id(),  # ty: ignore[unresolved-attribute]
            item=item,
        )

    def infrahub_node_to_diffsync(self, node: object) -> dict[str, Any]:  # noqa: PLR6301
        # Return whatever fake data the test attached to the node.
        return dict(node._fake_diffsync_data)  # ty: ignore[unresolved-attribute]


class _RelationshipHarness(InfrahubAdapter):
    """Exercise production conversion without initializing a live client."""

    client: _FakeClient

    def __init__(self, *, rehydrated_peer: object) -> None:
        self.client = _FakeClient(rehydrated_peer=rehydrated_peer)
        self._diffsync_store = _FakeStore()
        self.store = self._diffsync_store  # ty: ignore[invalid-assignment]
        self.continue_on_error = False
        self._peer_unique_ids = {}
        self._peer_identifier_errors = {}
        self._instances: list[object] = []
        self.InterfaceLag = _FakeLagModel
        self.InfraDevice = _FakeDeviceModel
        self.schema = {"InfraDevice": SimpleNamespace(kind="InfraDevice")}  # ty: ignore[invalid-assignment]
        self.config = SyncConfig(
            name="test",
            source=SyncAdapter(name="source", adapter="x:x"),
            destination=SyncAdapter(name="destination", adapter="x:x"),
            order=["InfraDevice", "InterfaceLag"],
            schema_mapping=[
                SchemaMappingModel(
                    name="InfraDevice",
                    mapping="InfraDevice",
                    identifiers=["name"],
                    fields=[SchemaMappingField(name="name", mapping="name")],
                ),
                SchemaMappingModel(
                    name="InterfaceLag",
                    mapping="InterfaceLag",
                    identifiers=["device", "name"],
                    fields=[
                        SchemaMappingField(name="device", mapping="device"),
                        SchemaMappingField(name="name", mapping="name"),
                        SchemaMappingField(name="description", mapping="description"),
                    ],
                ),
            ],
        )

    def update_or_add_model_instance(self, item: object) -> None:  # ty: ignore[invalid-method-override]
        self._instances.append(item)


def _make_node(kind: str, node_id: str, diffsync_data: dict[str, object]) -> SimpleNamespace:
    return SimpleNamespace(
        id=node_id,
        _schema=SimpleNamespace(kind=kind),
        _fake_diffsync_data=diffsync_data,
    )


def _make_sdk_node(
    kind: str,
    node_id: str,
    attrs: dict[str, object],
    relationships: dict[str, tuple[str, str]] | None = None,
) -> SimpleNamespace:
    relationship_data = relationships or {}
    node = SimpleNamespace(
        id=node_id,
        _schema=SimpleNamespace(
            kind=kind,
            attribute_names=list(attrs),
            attributes=[SimpleNamespace(name=name, optional=False) for name in attrs],
            relationships=[
                SimpleNamespace(name=name, peer=peer_kind, cardinality="one")
                for name, (peer_kind, _peer_id) in relationship_data.items()
            ],
        ),
    )
    for name, value in attrs.items():
        setattr(node, name, SimpleNamespace(value=value))
    for name, (_peer_kind, peer_id) in relationship_data.items():
        setattr(node, name, SimpleNamespace(id=peer_id))
    return node


def _seed_relationship_stores(harness: _RelationshipHarness, *, peer: object, peer_key: str) -> None:
    device = _make_sdk_node("InfraDevice", "device-id", {"name": "router-1"})
    harness.client.store.set(key="router-1", node=device)
    harness.client.store.set(key=peer_key, node=peer)
    harness._diffsync_store.seed(
        model="InfraDevice",
        identifier="router-1",
        item=_FakeDeviceModel(name="router-1"),
    )
    harness._diffsync_store.seed(
        model="InterfaceLag",
        identifier="router-1|lag-1",
        item=_FakeLagModel(device="router-1", name="lag-1"),
    )


def _resolve_cached_sdk_peer(harness: InfrahubAdapter, *, kind: str, unique_id: str) -> object | None:
    return resolve_peer_node(
        key=unique_id,
        rel_schema=SimpleNamespace(peer=kind),  # ty: ignore[invalid-argument-type]
        peer_schema=SimpleNamespace(kind=kind),  # ty: ignore[invalid-argument-type]
        store=harness.client.store,
        fallback=False,
    )


def test_missing_identifier_raises_with_rich_context() -> None:
    harness = _Harness(continue_on_error=False, raise_not_found=True)
    parent = _make_node("InfraDevice", "parent-id", {})
    peer = _make_node("LocationGeneric", "peer-id", {"name": "dc-east"})  # 'organization' missing

    with pytest.raises(PeerIdentifierError) as excinfo:
        harness._resolve_peer_unique_id(parent_node=parent, rel_name="location", peer_node=peer)  # ty: ignore[invalid-argument-type]

    err = excinfo.value
    assert err.parent_kind == "InfraDevice"
    assert err.parent_id == "parent-id"
    assert err.rel_name == "location"
    assert err.peer_kind == "LocationGeneric"
    assert err.peer_id == "peer-id"
    assert err.missing_keys == ("organization",)
    assert "organization" in str(err)
    assert "LocationGeneric" in str(err)
    assert "InfraDevice.location" in str(err)


def test_missing_identifier_skipped_when_continue_on_error(caplog: pytest.LogCaptureFixture) -> None:
    harness = _Harness(continue_on_error=True)
    parent = _make_node("InfraDevice", "parent-id", {})
    peer = _make_node("LocationGeneric", "peer-id", {"name": "dc-east"})

    with caplog.at_level(logging.WARNING, logger="infrahub_sync.adapters.infrahub"):
        result = harness._resolve_peer_unique_id(parent_node=parent, rel_name="location", peer_node=peer)  # ty: ignore[invalid-argument-type]

    assert result is None
    assert any("Skipping peer relationship" in rec.message for rec in caplog.records)


def test_missing_relationship_identifier_is_rehydrated_by_uuid() -> None:
    hydrated_peer = _make_node(
        "LocationGeneric",
        "peer-id",
        {"name": "dc-east", "organization": "acme"},
    )
    harness = _Harness(rehydrated_peer=hydrated_peer)
    parent = _make_node("InfraDevice", "parent-id", {})
    shallow_peer = _make_node("LocationGeneric", "peer-id", {"name": "dc-east"})

    result = harness._resolve_peer_unique_id(
        parent_node=parent,  # ty: ignore[invalid-argument-type]
        rel_name="location",
        peer_node=shallow_peer,  # ty: ignore[invalid-argument-type]
    )

    assert result == "dc-east|acme"
    assert harness.client.get_calls == [
        {
            "id": "peer-id",
            "kind": "LocationGeneric",
            "include": ["name", "organization"],
            "populate_store": False,
        }
    ]
    assert len(harness._instances) == 1
    assert harness.store.get(model="LocationGeneric", identifier="dc-east|acme") is harness._instances[0]
    assert harness.client.store.get(kind="LocationGeneric", key="peer-id", raise_when_missing=False) is None
    assert harness.client.store.get(kind="LocationGeneric", key="dc-east|acme", raise_when_missing=False) is None
    assert _resolve_cached_sdk_peer(harness, kind="LocationGeneric", unique_id="dc-east|acme") is None


def test_relationship_hydration_preserves_rich_sdk_node() -> None:
    rich_peer = _make_sdk_node(
        "InterfaceLag",
        "lag-id",
        {"name": "lag-1", "description": "rich SDK node"},
        {"device": ("InfraDevice", "device-id")},
    )
    identifier_only_peer = _make_sdk_node(
        "InterfaceLag",
        "lag-id",
        {"name": "lag-1"},
        {"device": ("InfraDevice", "device-id")},
    )
    harness = _RelationshipHarness(rehydrated_peer=identifier_only_peer)
    parent = _make_node("InfraDevice", "parent-id", {})
    shallow_peer = _make_sdk_node("InterfaceLag", "lag-id", {"name": "lag-1"})
    _seed_relationship_stores(harness, peer=rich_peer, peer_key="router-1|lag-1")
    harness.client.store.set(key="later-shallow", node=shallow_peer)

    results = []
    for _ in range(2):
        cached_peer = harness.client.store.get(kind="InterfaceLag", key="lag-id")
        results.append(
            harness._resolve_peer_unique_id(
                parent_node=parent,  # ty: ignore[invalid-argument-type]
                rel_name="bundle",
                peer_node=cached_peer,  # ty: ignore[invalid-argument-type]
            )
        )

    assert results == ["router-1|lag-1", "router-1|lag-1"]
    assert harness.client.get_calls == [
        {
            "id": "lag-id",
            "kind": "InterfaceLag",
            "include": ["device", "name"],
            "populate_store": False,
        }
    ]
    cached_by_uuid = harness.client.store.get(kind="InterfaceLag", key="lag-id")
    cached_by_key = harness.client.store.get(kind="InterfaceLag", key="router-1|lag-1")
    assert cached_by_uuid is shallow_peer
    assert cached_by_key is rich_peer
    assert cached_by_key.description.value == "rich SDK node"
    assert _resolve_cached_sdk_peer(harness, kind="InterfaceLag", unique_id="router-1|lag-1") is rich_peer


def test_relationship_hydration_is_reused_for_shared_store_references() -> None:
    identifier_only_peer = _make_sdk_node(
        "InterfaceLag",
        "lag-id",
        {"name": "lag-1"},
        {"device": ("InfraDevice", "device-id")},
    )
    harness = _RelationshipHarness(rehydrated_peer=identifier_only_peer)
    parent = _make_node("InfraDevice", "parent-id", {})
    shallow_peer = _make_sdk_node("InterfaceLag", "lag-id", {"name": "lag-1"})
    _seed_relationship_stores(harness, peer=shallow_peer, peer_key="initial-shallow")

    results = []
    for _ in range(2):
        cached_peer = harness.client.store.get(kind="InterfaceLag", key="lag-id")
        results.append(
            harness._resolve_peer_unique_id(
                parent_node=parent,  # ty: ignore[invalid-argument-type]
                rel_name="bundle",
                peer_node=cached_peer,  # ty: ignore[invalid-argument-type]
            )
        )

    assert results == ["router-1|lag-1", "router-1|lag-1"]
    assert harness.client.get_calls == [
        {
            "id": "lag-id",
            "kind": "InterfaceLag",
            "include": ["device", "name"],
            "populate_store": False,
        }
    ]


def test_unexpected_hydration_error_propagates_with_continue_on_error() -> None:
    get_error = RuntimeError("unexpected hydration failure")
    harness = _Harness(continue_on_error=True, get_error=get_error)
    parent = _make_node("InfraDevice", "parent-id", {})
    shallow_peer = _make_node("LocationGeneric", "peer-id", {"name": "dc-east"})

    with pytest.raises(RuntimeError) as excinfo:
        harness._resolve_peer_unique_id(
            parent_node=parent,  # ty: ignore[invalid-argument-type]
            rel_name="location",
            peer_node=shallow_peer,  # ty: ignore[invalid-argument-type]
        )

    assert excinfo.value is get_error


def test_incomplete_hydration_fetches_once_then_raises_rich_error() -> None:
    incomplete_peer = _make_node("LocationGeneric", "peer-id", {"name": "dc-east"})
    harness = _Harness(rehydrated_peer=incomplete_peer)
    parent = _make_node("InfraDevice", "parent-id", {})
    shallow_peer = _make_node("LocationGeneric", "peer-id", {})

    for _ in range(2):
        with pytest.raises(PeerIdentifierError) as excinfo:
            harness._resolve_peer_unique_id(
                parent_node=parent,  # ty: ignore[invalid-argument-type]
                rel_name="location",
                peer_node=shallow_peer,  # ty: ignore[invalid-argument-type]
            )

        assert excinfo.value.missing_keys == ("organization",)
        assert excinfo.value.present_keys == ("name",)
    assert len(harness.client.get_calls) == 1


@pytest.mark.parametrize("hydration_result", ["not-found", "incomplete"])
def test_unresolvable_peer_logs_once_when_continue_on_error(
    hydration_result: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    incomplete_peer = _make_node("LocationGeneric", "peer-id", {"name": "dc-east"})
    harness = _Harness(
        continue_on_error=True,
        rehydrated_peer=incomplete_peer,
        raise_not_found=hydration_result == "not-found",
    )
    parent = _make_node("InfraDevice", "parent-id", {})
    shallow_peer = _make_node("LocationGeneric", "peer-id", {"name": "dc-east"})

    with caplog.at_level(logging.WARNING, logger="infrahub_sync.adapters.infrahub"):
        results = [
            harness._resolve_peer_unique_id(
                parent_node=parent,  # ty: ignore[invalid-argument-type]
                rel_name="location",
                peer_node=shallow_peer,  # ty: ignore[invalid-argument-type]
            )
            for _ in range(2)
        ]

    assert results == [None, None]
    assert len(harness.client.get_calls) == 1
    assert sum("Skipping peer relationship" in record.message for record in caplog.records) == 1


def test_complete_peer_returns_unique_id() -> None:
    harness = _Harness()
    parent = _make_node("InfraDevice", "parent-id", {})
    peer = _make_node(
        "LocationGeneric",
        "peer-id",
        {"name": "dc-east", "organization": "acme"},
    )

    result = harness._resolve_peer_unique_id(parent_node=parent, rel_name="location", peer_node=peer)  # ty: ignore[invalid-argument-type]

    assert result == "dc-east|acme"
    assert not harness.client.get_calls


def test_unexpected_diffsync_store_error_propagates() -> None:
    harness = _Harness()
    store_error = RuntimeError("unexpected store failure")
    harness._diffsync_store.get_error = store_error
    parent = _make_node("InfraDevice", "parent-id", {})
    peer = _make_node(
        "LocationGeneric",
        "peer-id",
        {"name": "dc-east", "organization": "acme"},
    )

    with pytest.raises(RuntimeError) as excinfo:
        harness._resolve_peer_unique_id(
            parent_node=parent,  # ty: ignore[invalid-argument-type]
            rel_name="location",
            peer_node=peer,  # ty: ignore[invalid-argument-type]
        )

    assert excinfo.value is store_error


def test_complete_peer_adds_identity_alias_without_replacing_uuid_entry() -> None:
    harness = _Harness()
    parent = _make_node("InfraDevice", "parent-id", {})
    rich_peer = _make_node(
        "LocationGeneric",
        "peer-id",
        {"name": "dc-east", "organization": "acme", "description": "rich SDK node"},
    )
    harness.client.store.set(key="peer-id", node=rich_peer)

    result = harness._resolve_peer_unique_id(
        parent_node=parent,  # ty: ignore[invalid-argument-type]
        rel_name="location",
        peer_node=rich_peer,  # ty: ignore[invalid-argument-type]
    )

    assert result == "dc-east|acme"
    assert not harness.client.get_calls
    assert harness.client.store.get(kind="LocationGeneric", key="peer-id") is rich_peer
    assert harness.client.store.get(kind="LocationGeneric", key="dc-east|acme") is rich_peer
