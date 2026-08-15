"""Unit tests for InfrahubAdapter._resolve_peer_unique_id error and skip paths.

The full adapter touches an Infrahub server, so these tests build a minimal
stand-in adapter that reuses the real helper. We only care about four things:

  1. Missing peer identifier keys raise PeerIdentifierError with rich context.
  2. continue_on_error=True logs a warning and returns None instead of raising.
  3. Missing relationship identifiers are rehydrated before resolving unique_id.
  4. Successful path returns the peer's unique_id.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any

import pytest
from infrahub_sdk.exceptions import NodeNotFoundError

from infrahub_sync.adapters.infrahub import InfrahubAdapter, PeerIdentifierError


class _FakeStore:
    def __init__(self) -> None:
        self._items: dict[tuple[str, str], object] = {}

    def get(self, *, model: str, identifier: str) -> object:
        return self._items.get((model, identifier))

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
        self._instances: list[object] = []
        # Register the fake peer model under its kind so getattr(self, kind) works.
        self.LocationGeneric = _FakePeerModel

    def update_or_add_model_instance(self, item: object) -> None:  # ty: ignore[invalid-method-override]
        self._instances.append(item)

    def infrahub_node_to_diffsync(self, node: object) -> dict[str, Any]:  # noqa: PLR6301
        # Return whatever fake data the test attached to the node.
        return dict(node._fake_diffsync_data)  # ty: ignore[unresolved-attribute]


def _make_node(kind: str, node_id: str, diffsync_data: dict[str, object]) -> SimpleNamespace:
    return SimpleNamespace(
        id=node_id,
        _schema=SimpleNamespace(kind=kind),
        _fake_diffsync_data=diffsync_data,
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


def test_rehydrated_existing_peer_refreshes_uuid_cache_for_repeated_references() -> None:
    hydrated_peer = _make_node(
        "LocationGeneric",
        "peer-id",
        {"name": "dc-east", "organization": "acme"},
    )
    harness = _Harness(rehydrated_peer=hydrated_peer)
    parent = _make_node("InfraDevice", "parent-id", {})
    shallow_peer = _make_node("LocationGeneric", "peer-id", {"name": "dc-east"})
    harness.client.store.set(key="initial-shallow", node=shallow_peer)
    harness._diffsync_store.seed(
        model="LocationGeneric",
        identifier="dc-east|acme",
        item=_FakePeerModel(name="dc-east", organization="acme"),
    )

    results = []
    for _ in range(2):
        cached_peer = harness.client.store.get(model="LocationGeneric", identifier="peer-id")
        results.append(
            harness._resolve_peer_unique_id(
                parent_node=parent,  # ty: ignore[invalid-argument-type]
                rel_name="location",
                peer_node=cached_peer,  # ty: ignore[invalid-argument-type]
            )
        )

    assert results == ["dc-east|acme", "dc-east|acme"]
    assert len(harness.client.get_calls) == 1
    assert harness.client.store.get(model="LocationGeneric", identifier="peer-id") is hydrated_peer


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
    shallow_peer = _make_node("LocationGeneric", "peer-id", {"name": "dc-east"})

    with pytest.raises(PeerIdentifierError) as excinfo:
        harness._resolve_peer_unique_id(
            parent_node=parent,  # ty: ignore[invalid-argument-type]
            rel_name="location",
            peer_node=shallow_peer,  # ty: ignore[invalid-argument-type]
        )

    assert excinfo.value.missing_keys == ("organization",)
    assert len(harness.client.get_calls) == 1


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
    assert harness.client.get_calls == []
