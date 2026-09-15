"""Tests that Infrahub's built-in `default` namespace resolves from the store.

The NetBox example sends every prefix and address to an IP namespace, using
`default` for objects with no VRF. A NetBox instance normally has no VRF of that
name, so that peer is usually absent from the *source* store. It does not have
to be there: because
`IpamNamespace` is a mapped kind, the Infrahub adapter loads every namespace
from the destination — the built-in `default` included — and stores it under
its DiffSync unique id before any prefix or address is written.

`diffsync_to_infrahub` is the write path that consumes that store. It calls
`resolve_peer_node` with no client and with `fallback` left at its default of
``False``, so the store is the only thing that can resolve the peer. These
tests pin that, using real SDK schema, node and store objects rather than
stand-ins, since the behavior under test is the SDK store lookup itself.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest
from infrahub_sdk.node import InfrahubNodeSync
from infrahub_sdk.schema import RelationshipCardinality
from infrahub_sdk.schema.main import (
    AttributeKind,
    AttributeSchemaAPI,
    GenericSchemaAPI,
    NodeSchemaAPI,
    RelationshipSchemaAPI,
)
from infrahub_sdk.store import NodeStoreSync

from infrahub_sync.adapters.infrahub import diffsync_to_infrahub, resolve_peer_node

PREFIX = "10.0.0.0/24"
NAMESPACE_ID = "namespace-default-id"

# In Infrahub's core schema, `IpamPrefix.ip_namespace` points at the generic
# `BuiltinIPNamespace`, whose `used_by` list carries the concrete kinds.
NAMESPACE_PEER = "BuiltinIPNamespace"


@pytest.fixture(name="namespace_schema")
def namespace_schema_fixture() -> NodeSchemaAPI:
    return NodeSchemaAPI(
        name="Namespace",
        namespace="Ipam",
        human_friendly_id=["name__value"],
        attributes=[AttributeSchemaAPI(name="name", kind=AttributeKind.TEXT)],
    )


@pytest.fixture(name="generic_schema")
def generic_schema_fixture() -> GenericSchemaAPI:
    return GenericSchemaAPI(name="IPNamespace", namespace="Builtin", used_by=["IpamNamespace"])


@pytest.fixture(name="prefix_schema")
def prefix_schema_fixture() -> NodeSchemaAPI:
    return NodeSchemaAPI(
        name="Prefix",
        namespace="Ipam",
        human_friendly_id=["ip_namespace__name__value", "prefix__value"],
        attributes=[AttributeSchemaAPI(name="prefix", kind=AttributeKind.TEXT)],
        relationships=[
            RelationshipSchemaAPI(
                name="ip_namespace",
                peer=NAMESPACE_PEER,
                cardinality=RelationshipCardinality.ONE,
            )
        ],
    )


# A namespace takes the name of its NetBox VRF, and NetBox allows any non-empty
# text there. The store keys these exactly as the mapping produces them.
OTHER_NAMESPACES = {"100": "namespace-100-id", "emoji \U0001f600": "namespace-emoji-id"}


@pytest.fixture(name="store")
def store_fixture(namespace_schema: NodeSchemaAPI) -> NodeStoreSync:
    """A destination store holding the built-in `default` namespace and two VRF-named ones."""
    client = MagicMock()
    client.default_branch = "main"
    client.request_context = None
    client.schema.all.return_value = {"IpamNamespace": namespace_schema}

    store = NodeStoreSync(default_branch="main")
    for name, node_id in {"default": NAMESPACE_ID, **OTHER_NAMESPACES}.items():
        node = InfrahubNodeSync(client=client, schema=namespace_schema, data={"id": node_id, "name": name})
        store.set(node=node, key=name)
    return store


def test_default_namespace_resolves_from_the_destination_store(
    store: NodeStoreSync,
    generic_schema: GenericSchemaAPI,
    prefix_schema: NodeSchemaAPI,
) -> None:
    """A prefix created with `ip_namespace = default` finds the preloaded node."""
    data = diffsync_to_infrahub(
        ids={"prefix": PREFIX, "ip_namespace": "default"},
        attrs={},
        store=store,
        node_schema=prefix_schema,
        schemas={NAMESPACE_PEER: generic_schema},
    )

    assert data["ip_namespace"] == NAMESPACE_ID


def test_default_namespace_resolves_without_the_id_fallback(
    store: NodeStoreSync,
    generic_schema: GenericSchemaAPI,
    prefix_schema: NodeSchemaAPI,
) -> None:
    """`resolve_peer_node` finds the namespace with fallback off and no client."""
    rel_schema = prefix_schema.relationships[0]

    peer_node = resolve_peer_node(
        key="default",
        rel_schema=rel_schema,
        peer_schema=generic_schema,
        store=store,
    )

    assert peer_node is not None
    assert peer_node.id == NAMESPACE_ID
    assert peer_node.get_kind() == "IpamNamespace"


def test_a_namespace_missing_from_the_store_is_not_resolved(
    store: NodeStoreSync,
    generic_schema: GenericSchemaAPI,
    prefix_schema: NodeSchemaAPI,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Nothing invents a peer: an unknown namespace is reported, not resolved."""
    with caplog.at_level(logging.WARNING, logger="infrahub_sync.adapters.infrahub"):
        data = diffsync_to_infrahub(
            ids={"prefix": PREFIX, "ip_namespace": "no-such-namespace"},
            attrs={},
            store=store,
            node_schema=prefix_schema,
            schemas={NAMESPACE_PEER: generic_schema},
        )

    assert data["ip_namespace"] != NAMESPACE_ID
    assert "no-such-namespace" in caplog.text


@pytest.mark.parametrize(("namespace", "expected_id"), list(OTHER_NAMESPACES.items()))
def test_vrf_named_namespaces_resolve_from_the_destination_store(
    store: NodeStoreSync,
    generic_schema: GenericSchemaAPI,
    prefix_schema: NodeSchemaAPI,
    namespace: str,
    expected_id: str,
) -> None:
    """A numeric or non-BMP VRF name reaches the store as that exact string."""
    data = diffsync_to_infrahub(
        ids={"prefix": PREFIX, "ip_namespace": namespace},
        attrs={},
        store=store,
        node_schema=prefix_schema,
        schemas={NAMESPACE_PEER: generic_schema},
    )

    assert data["ip_namespace"] == expected_id
