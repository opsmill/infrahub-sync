"""Resolve a built-in namespace by name from the SDK store."""

from __future__ import annotations

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

from infrahub_sync.adapters.infrahub import resolve_peer_node

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


@pytest.fixture(name="store")
def store_fixture(namespace_schema: NodeSchemaAPI) -> NodeStoreSync:
    """A store holding the built-in `default` namespace."""
    client = MagicMock()
    client.default_branch = "main"
    client.request_context = None
    client.schema.all.return_value = {"IpamNamespace": namespace_schema}

    store = NodeStoreSync(default_branch="main")
    node = InfrahubNodeSync(client=client, schema=namespace_schema, data={"id": NAMESPACE_ID, "name": "default"})
    store.set(node=node, key="default")
    return store


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
