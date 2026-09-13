"""The adapter reads a kind's schema from its own mapping, never from the SDK's manager.

An ``InfrahubNodeSync`` built with an explicit schema — directly, or through
``from_graphql(..., schema=...)`` — never registers that schema in
``client.schema.cache`` (SDK 1.18.1 `node/node.py:1318-1331` and `1346-1352`).
``SchemaManagerSync.get()`` therefore misses and issues ``GET /api/schema``
(`schema/__init__.py:594-603, 761-771`). The adapter paths exercised here previously
read ``node._schema`` and made no request at all, so reading through the schema manager
would add one.

Each test drives a real SDK node and a real client whose schema cache is empty and whose
HTTP layer raises on any use, so an added schema fetch fails the test rather than passing
silently.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, NoReturn

import pytest
from infrahub_sdk import Config, InfrahubClientSync
from infrahub_sdk.node import InfrahubNodeSync
from infrahub_sdk.schema.main import AttributeKind, AttributeSchemaAPI, NodeSchemaAPI

from infrahub_sync import (
    SchemaMappingField,
    SchemaMappingModel,
    SyncAdapter,
    SyncConfig,
)
from infrahub_sync.adapters.infrahub import InfrahubAdapter

if TYPE_CHECKING:
    from collections.abc import MutableMapping

    from infrahub_sdk.schema import MainSchemaTypesAPI

KIND = "TestingWidget"
# The client never reaches the network here; this only satisfies ``Config``.
_API_TOKEN = "not-a-real-token"  # noqa: S105


class _SchemaFetchAttemptedError(RuntimeError):
    """Raised in place of any HTTP call the adapter is not supposed to make."""


def _node_schema() -> NodeSchemaAPI:
    """A kind with one attribute and no relationships, built in-process."""
    return NodeSchemaAPI(
        name="Widget",
        namespace="Testing",
        attributes=[AttributeSchemaAPI(name="name", kind=AttributeKind.TEXT, optional=False)],
    )


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> InfrahubClientSync:
    """A real client whose schema cache is empty and whose transport always fails.

    ``_get``/``_post`` are the two methods ``SchemaManagerSync._fetch`` goes through, so
    replacing them turns any schema request into a test failure instead of a real socket.
    """
    built = InfrahubClientSync(address="http://localhost:8000", config=Config(api_token=_API_TOKEN))

    def _refuse(*_args: object, **_kwargs: object) -> NoReturn:
        msg = "the adapter issued an HTTP request it should not need"
        raise _SchemaFetchAttemptedError(msg)

    monkeypatch.setattr(built, "_get", _refuse)
    monkeypatch.setattr(built, "_post", _refuse)
    return built


def _adapter(client: InfrahubClientSync, schema: MutableMapping[str, MainSchemaTypesAPI]) -> InfrahubAdapter:
    """An adapter holding the loaded schema mapping, bypassing the live ``__init__``."""
    adapter = InfrahubAdapter.__new__(InfrahubAdapter)
    adapter.client = client
    adapter.schema = schema
    adapter.config = SyncConfig(
        name="test",
        source=SyncAdapter(name="src", adapter="x:x"),
        destination=SyncAdapter(name="dst", adapter="x:x"),
        order=[KIND],
        schema_mapping=[
            SchemaMappingModel(
                name=KIND,
                mapping=KIND,
                identifiers=["name"],
                fields=[SchemaMappingField(name="name", mapping="name")],
            ),
        ],
    )
    return adapter


def test_the_schema_cache_starts_empty_for_a_directly_constructed_node(client: InfrahubClientSync) -> None:
    """The premise: building a node with an explicit schema populates no cache entry."""
    schema = _node_schema()

    node = InfrahubNodeSync(client=client, schema=schema, branch="main", data={"id": "w1", "name": {"value": "a"}})

    assert not client.schema.cache, "a directly constructed node registers no schema"
    assert node.get_kind() == KIND


def test_the_schema_manager_would_fetch_for_such_a_node(client: InfrahubClientSync) -> None:
    """The hazard this guards against: the manager misses and reaches for the network."""
    InfrahubNodeSync(client=client, schema=_node_schema(), branch="main", data={"id": "w1"})

    with pytest.raises(_SchemaFetchAttemptedError):
        client.schema.get(kind=KIND, branch="main")


def test_conversion_of_an_uncached_node_issues_no_request(client: InfrahubClientSync) -> None:
    """``infrahub_node_to_diffsync`` converts from the adapter's mapping alone."""
    schema = _node_schema()
    adapter = _adapter(client, {KIND: schema})
    node = InfrahubNodeSync(
        client=client, schema=schema, branch="main", data={"id": "w1", "name": {"value": "widget-a"}}
    )

    data = adapter.infrahub_node_to_diffsync(node=node)

    assert data == {"local_id": "w1", "name": "widget-a"}
    assert not client.schema.cache, "conversion must not populate the schema cache either"


def test_identifier_reconciliation_of_an_uncached_store_node_issues_no_request(client: InfrahubClientSync) -> None:
    """The peer path: a directly constructed node in the SDK store is judged without a fetch."""
    schema = _node_schema()
    adapter = _adapter(client, {KIND: schema})
    node = InfrahubNodeSync(
        client=client, schema=schema, branch="main", data={"id": "w1", "name": {"value": "widget-a"}}
    )
    client.store.set(node=node, key="w1")

    adapter._reconcile_peer_sdk_alias(
        peer_kind=KIND,
        peer_id="w1",
        unique_id="widget-a",
        identifiers=("name",),
    )

    assert client.store.get(key="widget-a", kind=KIND, raise_when_missing=False) is node
    assert not client.schema.cache
