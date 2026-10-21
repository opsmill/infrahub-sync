"""Unit tests for InfrahubAdapter._resolve_peer_unique_id error and skip paths.

The full adapter touches an Infrahub server, so these tests build a minimal
stand-in adapter that reuses the real helper. We only care about five things:

  1. Missing peer identifier keys raise PeerIdentifierError with rich context.
  2. continue_on_error=True logs a warning and returns None instead of raising.
  3. Successful path returns the peer's unique_id.
  4. A peer that is not in the DiffSync store yet resolves without loading it,
     and a peer that is already there is left untouched.
  5. Only the identifier fields of a peer are converted: converting all of its
     relationships can loop, for example device > primary IP > interface > device.
  6. A peer that misses identifier keys is fetched again by its own kind first:
     the SDK store can hold a partial copy, prefetched through a relationship
     to a generic.

The stand-in adapter keeps DiffSync's real LocalStore, which raises
ObjectNotFound for a missing object. The fake store previously returned None
and hid the original load failure.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from types import SimpleNamespace
from typing import Any, Protocol

import pytest
from diffsync import DiffSyncModel
from diffsync.exceptions import ObjectNotFound
from diffsync.store.local import LocalStore
from infrahub_sdk.exceptions import NodeNotFoundError

from infrahub_sync import DiffSyncModelMixin, SchemaMappingFilter, SchemaMappingModel, SyncAdapter, SyncConfig
from infrahub_sync.adapters.infrahub import InfrahubAdapter, PeerIdentifierError


class _KindedNode(Protocol):
    """The public surface a stored node has to expose: its kind."""

    def get_kind(self) -> str: ...


class _FakeStore:
    def __init__(self) -> None:
        self._items: dict[tuple[str, str], object] = {}

    def get(self, *, model: str, identifier: str) -> object:
        return self._items.get((model, identifier))

    def set(self, *, key: str, node: _KindedNode) -> None:  # match client.store.set signature
        self._items[node.get_kind(), key] = node


class _FakeClient:
    def __init__(self) -> None:
        self.store = _FakeStore()
        self.nodes: list[SimpleNamespace] = []
        # Nodes that client.get() returns, by ID, and the IDs it was called with.
        self.nodes_by_id: dict[str, object] = {}
        self.fetched: list[tuple[str, str]] = []

    def all(self, *, kind: str, include: list[str], populate_store: bool) -> list[SimpleNamespace]:
        assert kind == "LocationGeneric"
        assert isinstance(include, list)
        assert populate_store
        return self.nodes

    def get(self, *, id: str, kind: str, populate_store: bool = True) -> object:  # noqa: A002, ARG002
        self.fetched.append((kind, id))
        if id not in self.nodes_by_id:
            raise NodeNotFoundError(identifier={"id": [id]}, message=f"{id} not found")
        return self.nodes_by_id[id]


class _FakePeerModel(DiffSyncModelMixin, DiffSyncModel):
    _modelname = "LocationGeneric"
    _identifiers = ("name", "organization")

    name: str
    organization: str
    parent: str | None = None

    @classmethod
    def create_unique_id(cls, **identifiers: object) -> str:
        return "|".join(str(identifiers[k]) for k in cls._identifiers)


class _Harness(InfrahubAdapter):
    """Skip the heavy __init__ that needs a real Infrahub server."""

    def __init__(self, *, continue_on_error: bool = False) -> None:  # pylint: disable=super-init-not-called
        # bypass the parent chain entirely
        self.fake_client = _FakeClient()
        self.client = self.fake_client
        self.store = LocalStore(adapter=self)
        self.continue_on_error = continue_on_error
        # Register the fake peer model under its kind so getattr(self, kind) works.
        self.LocationGeneric = _FakePeerModel

    def infrahub_node_to_diffsync(self, node: object, fields: Iterable[str] | None = None) -> dict[str, Any]:
        # Return whatever fake data the test attached to the node, limited to `fields`.
        wanted = set(fields) if fields is not None else None
        data = dict(node._fake_diffsync_data)  # ty: ignore[unresolved-attribute]
        peer = getattr(node, "_peer", None)
        if peer is not None and (wanted is None or "parent" in wanted):
            data["parent"] = self._resolve_peer_unique_id(parent_node=node, rel_name="parent", peer_node=peer)  # ty: ignore[invalid-argument-type]
        if wanted is None:
            return data
        return {key: value for key, value in data.items() if key in wanted}


def _make_node(kind: str, node_id: str, diffsync_data: dict[str, object]) -> SimpleNamespace:
    """A fake SDK node exposing the public ``get_kind()`` the adapter reads."""
    return SimpleNamespace(
        id=node_id,
        get_kind=lambda: kind,
        _fake_diffsync_data=diffsync_data,
    )


def test_missing_identifier_raises_with_rich_context() -> None:
    harness = _Harness(continue_on_error=False)
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


def test_peer_not_in_store_resolves_without_adding() -> None:
    """The parent of a node in a tree of one kind is not loaded yet when the node is converted.

    model_loader converts every node of a kind before it adds them to the store, so
    the parent must resolve without a store lookup or premature insertion.
    """
    harness = _Harness()
    child = _make_node("LocationGeneric", "child-id", {})
    peer = _make_node("LocationGeneric", "peer-id", {"name": "dc-east", "organization": "acme"})

    result = harness._resolve_peer_unique_id(parent_node=child, rel_name="parent", peer_node=peer)  # ty: ignore[invalid-argument-type]

    assert result == "dc-east|acme"
    with pytest.raises(ObjectNotFound):
        harness.store.get(model="LocationGeneric", identifier="dc-east|acme")
    assert harness.fake_client.store.get(model="LocationGeneric", identifier="dc-east|acme") is None


def test_peer_already_in_store_is_reused() -> None:
    harness = _Harness()
    existing = _FakePeerModel(name="dc-east", organization="acme")
    harness.store.add(obj=existing)
    parent = _make_node("InfraDevice", "parent-id", {})
    peer = _make_node("LocationGeneric", "peer-id", {"name": "dc-east", "organization": "acme"})

    result = harness._resolve_peer_unique_id(parent_node=parent, rel_name="location", peer_node=peer)  # ty: ignore[invalid-argument-type]

    assert result == "dc-east|acme"
    assert harness.store.get(model="LocationGeneric", identifier="dc-east|acme") is existing
    assert harness.fake_client.store.get(model="LocationGeneric", identifier="dc-east|acme") is None


def test_model_loader_does_not_add_filtered_peer() -> None:
    harness = _Harness()
    harness.config = SyncConfig(
        name="filtered-tree",
        source=SyncAdapter(name="infrahub"),
        destination=SyncAdapter(name="netbox"),
        schema_mapping=[
            SchemaMappingModel(
                name="LocationGeneric",
                filters=[SchemaMappingFilter(field="name", operation="!=", value="parent")],
            )
        ],
    )
    parent = _make_node("LocationGeneric", "parent-id", {"name": "parent", "organization": "acme"})
    child = _make_node("LocationGeneric", "child-id", {"name": "child", "organization": "acme"})
    child._peer = parent
    harness.fake_client.nodes = [child, parent]

    harness.model_loader(model_name="LocationGeneric", model=_FakePeerModel)

    stored_child = harness.store.get(model="LocationGeneric", identifier="child|acme")
    assert isinstance(stored_child, _FakePeerModel)
    assert stored_child.parent == "parent|acme"
    with pytest.raises(ObjectNotFound):
        harness.store.get(model="LocationGeneric", identifier="parent|acme")


def test_model_loader_adds_parent_in_unfiltered_tree() -> None:
    harness = _Harness()
    harness.config = SyncConfig(
        name="unfiltered-tree",
        source=SyncAdapter(name="infrahub"),
        destination=SyncAdapter(name="netbox"),
        schema_mapping=[SchemaMappingModel(name="LocationGeneric")],
    )
    parent = _make_node("LocationGeneric", "parent-id", {"name": "parent", "organization": "acme"})
    child = _make_node("LocationGeneric", "child-id", {"name": "child", "organization": "acme"})
    child._peer = parent
    harness.fake_client.nodes = [child, parent]

    harness.model_loader(model_name="LocationGeneric", model=_FakePeerModel)

    stored_child = harness.store.get(model="LocationGeneric", identifier="child|acme")
    stored_parent = harness.store.get(model="LocationGeneric", identifier="parent|acme")
    assert isinstance(stored_child, _FakePeerModel)
    assert isinstance(stored_parent, _FakePeerModel)
    assert stored_child.parent == "parent|acme"
    assert stored_parent.name == "parent"


def test_partial_peer_is_fetched_again_by_its_kind() -> None:
    """A rack prefetched through LocationHosting has no site, which identifies it.

    The adapter fetches the rack again by its own kind and builds the unique_id from it.
    """
    harness = _Harness()
    parent = _make_node("InfraDevice", "parent-id", {})
    partial = _make_node("LocationGeneric", "peer-id", {"name": "dc-east"})
    complete = _make_node("LocationGeneric", "peer-id", {"name": "dc-east", "organization": "acme"})
    harness.fake_client.nodes_by_id["peer-id"] = complete

    result = harness._resolve_peer_unique_id(parent_node=parent, rel_name="location", peer_node=partial)  # ty: ignore[invalid-argument-type]

    assert result == "dc-east|acme"
    assert harness.fake_client.fetched == [("LocationGeneric", "peer-id")]


def test_peer_still_missing_identifier_after_fetch_raises() -> None:
    harness = _Harness()
    parent = _make_node("InfraDevice", "parent-id", {})
    peer = _make_node("LocationGeneric", "peer-id", {"name": "dc-east"})
    harness.fake_client.nodes_by_id["peer-id"] = _make_node("LocationGeneric", "peer-id", {"name": "dc-east"})

    with pytest.raises(PeerIdentifierError) as excinfo:
        harness._resolve_peer_unique_id(parent_node=parent, rel_name="location", peer_node=peer)  # ty: ignore[invalid-argument-type]

    assert excinfo.value.missing_keys == ("organization",)
    assert harness.fake_client.fetched == [("LocationGeneric", "peer-id")]


def test_peer_identifiers_only_are_converted() -> None:
    """Converting every relationship of a peer can loop: device > primary IP > interface > device."""
    harness = _Harness()
    calls: list[tuple[str, tuple[str, ...] | None]] = []
    original = harness.infrahub_node_to_diffsync

    def recording(node: object, fields: Iterable[str] | None = None) -> dict[str, Any]:
        calls.append((node.id, tuple(fields) if fields is not None else None))  # ty: ignore[unresolved-attribute]
        return original(node, fields)

    harness.infrahub_node_to_diffsync = recording  # ty: ignore[invalid-assignment]
    parent = _make_node("InfraDevice", "parent-id", {})
    peer = _make_node("LocationGeneric", "peer-id", {"name": "dc-east", "organization": "acme", "devices": ["x"]})

    result = harness._resolve_peer_unique_id(parent_node=parent, rel_name="location", peer_node=peer)  # ty: ignore[invalid-argument-type]

    assert result == "dc-east|acme"
    assert calls == [("peer-id", ("name", "organization"))]
