"""Unit tests for InfrahubAdapter._resolve_peer_unique_id error and skip paths.

The full adapter touches an Infrahub server, so these tests build a minimal
stand-in adapter that reuses the real helper. We only care about three things:

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

from infrahub_sync.adapters.infrahub import InfrahubAdapter, PeerIdentifierError


class _FakeStore:
    def __init__(self) -> None:
        self._items: dict[tuple[str, str], object] = {}

    def get(self, *, model: str, identifier: str) -> object:
        return self._items.get((model, identifier))

    def set(self, *, key: str, node: object) -> None:  # match client.store.set signature
        self._items[getattr(node, "_schema", SimpleNamespace(kind="?")).kind, key] = node


class _FakeClient:
    def __init__(self, rehydrated_peer: object | None = None) -> None:
        self.store = _FakeStore()
        self.rehydrated_peer = rehydrated_peer
        self.get_calls: list[dict[str, object]] = []

    def get(self, **kwargs: object) -> object | None:
        self.get_calls.append(kwargs)
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

    def __init__(self, *, continue_on_error: bool = False, rehydrated_peer: object | None = None) -> None:
        # bypass the parent chain entirely
        self.client = _FakeClient(rehydrated_peer=rehydrated_peer)
        self.store = _FakeStore()  # ty: ignore[invalid-assignment]
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


def test_missing_relationship_identifier_is_rehydrated_by_uuid() -> None:
    hydrated_peer = _make_node(
        "LocationGeneric",
        "peer-id",
        {"name": "dc-east", "organization": "acme"},
    )
    harness = _Harness(rehydrated_peer=hydrated_peer)
    parent = _make_node("InfraDevice", "parent-id", {})
    shallow_peer = _make_node("LocationGeneric", "peer-id", {"name": "dc-east"})

    result = harness._resolve_peer_unique_id(  # ty: ignore[invalid-argument-type]
        parent_node=parent,
        rel_name="location",
        peer_node=shallow_peer,
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
