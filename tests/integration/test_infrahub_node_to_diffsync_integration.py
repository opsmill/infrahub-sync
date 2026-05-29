"""Integration tests for InfrahubAdapter.infrahub_node_to_diffsync.

The unit tests in ``tests/adapters/`` use lightweight ``FakeNode`` stand-ins
that match the shape ``infrahub_node_to_diffsync`` reads. That catches the
behavior under test but can't catch drift between our fixture and the real
``InfrahubNodeSync`` shape returned by the SDK — e.g. if a future SDK
version starts returning ``DateTime`` as a real ``datetime`` object instead
of an ISO string, the unit tests would still pass but the contract with
downstream DiffSync models would break.

These integration tests exercise the same code path against a live
Infrahub. They apply a throwaway schema with one attribute per kind,
create a node, run the adapter, feed the result into a Pydantic-typed
DiffSync model, and tear everything down.

Skipped automatically when ``INFRAHUB_ADDRESS`` + ``INFRAHUB_API_TOKEN``
are not set. Run locally with::

    INFRAHUB_ADDRESS=http://localhost:8000 \\
    INFRAHUB_API_TOKEN=<token> \\
    pytest tests/integration -m integration
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Any

import pytest
import requests
from diffsync import DiffSyncModel

from infrahub_sync import (
    DiffSyncModelMixin,
    SchemaMappingField,
    SchemaMappingModel,
    SyncAdapter,
    SyncConfig,
)
from infrahub_sync.adapters.infrahub import InfrahubAdapter

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Setup / teardown helpers
# ---------------------------------------------------------------------------


_SCHEMA = {
    "version": "1.0",
    "nodes": [
        {
            "name": "AdapterProbe",
            "namespace": "Test",
            "include_in_menu": False,
            "attributes": [
                {"name": "name", "kind": "Text", "unique": True},
                {"name": "tags", "kind": "List", "optional": True},
                {"name": "port", "kind": "Number", "optional": True},
                {"name": "enabled", "kind": "Boolean", "optional": True},
                {"name": "issued_at", "kind": "DateTime", "optional": True},
                {"name": "payload", "kind": "JSON", "optional": True},
                {"name": "address", "kind": "IPHost", "optional": True},
            ],
        },
    ],
}

_NODE_VALUES: dict[str, Any] = {
    "name": "adapter-probe-1",
    "tags": ["alpha", "beta"],
    "port": 443,
    "enabled": True,
    "issued_at": "2027-08-21T08:21:16Z",
    "payload": {"k": "v", "n": 7},
    "address": "10.10.10.10/24",
}


def _env_or_skip() -> tuple[str, str]:
    address = os.environ.get("INFRAHUB_ADDRESS")
    token = os.environ.get("INFRAHUB_API_TOKEN")
    if not address or not token:
        pytest.skip("INFRAHUB_ADDRESS and INFRAHUB_API_TOKEN must be set")
    return address, token


def _graphql(address: str, token: str, query: str) -> dict[str, Any]:
    response = requests.post(
        f"{address}/graphql",
        headers={"X-INFRAHUB-KEY": token, "Content-Type": "application/json"},
        json={"query": query},
        timeout=30,
    )
    response.raise_for_status()
    body = response.json()
    if body.get("errors"):
        raise RuntimeError(f"GraphQL errors: {body['errors']}")
    return body


@pytest.fixture
def live_probe_node() -> Iterator[tuple[str, str, str]]:
    """Apply the throwaway schema, create one node, yield ids for the test, tear down."""
    address, token = _env_or_skip()

    # Load the test schema via the schema API (matches what infrahubctl does).
    schema_response = requests.post(
        f"{address}/api/schema/load?branch=main",
        headers={"X-INFRAHUB-KEY": token, "Content-Type": "application/json"},
        json={"schemas": [_SCHEMA]},
        timeout=60,
    )
    schema_response.raise_for_status()

    # Create one probe node with one attribute of each kind populated.
    inputs = ", ".join(
        f"{name}: {{value: {_graphql_literal(value)}}}"
        for name, value in _NODE_VALUES.items()
    )
    created = _graphql(
        address,
        token,
        f"mutation {{ TestAdapterProbeCreate(data: {{{inputs}}}) {{ ok object {{ id }} }} }}",
    )
    node_id = created["data"]["TestAdapterProbeCreate"]["object"]["id"]

    try:
        yield address, token, node_id
    finally:
        _graphql(
            address,
            token,
            f'mutation {{ TestAdapterProbeDelete(data: {{id: "{node_id}"}}) {{ ok }} }}',
        )


def _graphql_literal(value: Any) -> str:
    """Render a Python value as a GraphQL literal for our input shape.

    Handles only the kinds the test fixture uses; the dict / list / scalar
    cases are sufficient. Keeps the fixture free of an external GraphQL
    library dependency.
    """
    if isinstance(value, str):
        return f'"{value}"'
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return "[" + ", ".join(_graphql_literal(v) for v in value) + "]"
    if isinstance(value, dict):
        items = ", ".join(f'{k}: {_graphql_literal(v)}' for k, v in value.items())
        return "{" + items + "}"
    raise TypeError(f"Unsupported literal kind: {type(value).__name__}")


# ---------------------------------------------------------------------------
# Pydantic-typed DiffSync model — the actual consumer of the adapter output.
# ---------------------------------------------------------------------------


class AdapterProbe(DiffSyncModelMixin, DiffSyncModel):
    _modelname = "TestAdapterProbe"
    _identifiers = ("name",)
    _attributes = ("tags", "port", "enabled", "issued_at", "payload", "address")

    name: str
    tags: list[str]
    port: int
    enabled: bool
    issued_at: str
    payload: dict[str, Any]
    address: str
    local_id: str | None = None
    local_data: Any | None = None


# ---------------------------------------------------------------------------
# The test
# ---------------------------------------------------------------------------


def test_real_sdk_node_round_trips_into_typed_diffsync_model(
    live_probe_node: tuple[str, str, str],
) -> None:
    """End-to-end: load a real SDK node, run it through the adapter, feed
    into a Pydantic-typed DiffSync model. Asserts both per-field type and
    value survive the trip — that's the contract ``InfrahubAdapter.load``
    relies on at runtime.
    """
    address, token, _ = live_probe_node

    cfg = SyncConfig(
        name="integration",
        source=SyncAdapter(name="src", adapter="x:x"),
        destination=SyncAdapter(
            name="dst", adapter="x:x", settings={"url": address, "token": token},
        ),
        order=["TestAdapterProbe"],
        schema_mapping=[
            SchemaMappingModel(
                name="TestAdapterProbe",
                mapping="TestAdapterProbe",
                identifiers=["name"],
                fields=[
                    SchemaMappingField(name=n, mapping=n)
                    for n in ("name", "tags", "port", "enabled", "issued_at", "payload", "address")
                ],
            ),
        ],
    )

    # Wire a real InfrahubAdapter against the lab. We don't need a real
    # source — the adapter loads from Infrahub itself, which is the side
    # under test. ``__new__`` skips the source-node/owner setup that
    # would otherwise require a CoreAccount lookup.
    adapter = InfrahubAdapter.__new__(InfrahubAdapter)
    adapter.config = cfg
    adapter.target = "dst"
    adapter.client = _make_infrahub_client(address, token)
    adapter.schema = adapter.client.schema.all(branch="main")
    adapter.store = type("S", (), {"get": lambda self, **k: None})()
    adapter.source_node = None
    adapter.owner_node = None

    nodes = adapter.client.filters(
        kind="TestAdapterProbe",
        name__value=_NODE_VALUES["name"],
        populate_store=True,
    )
    assert nodes, "Probe node should be visible to the SDK after creation"
    node = nodes[0]

    data = adapter.infrahub_node_to_diffsync(node=node)

    # Pydantic-typed DiffSync model construction is the consumer contract
    # under test. Construction must not raise.
    instance = AdapterProbe(**data)

    # Value + type checks per kind. Pydantic ``list[str]`` and
    # ``dict[str, Any]`` fields reject string inputs outright, so both
    # halves of the contract (value AND Python type) are asserted here.
    assert instance.name == _NODE_VALUES["name"]
    assert instance.tags == _NODE_VALUES["tags"]
    assert isinstance(instance.tags, list)
    assert instance.port == _NODE_VALUES["port"]
    assert isinstance(instance.port, int)
    assert instance.enabled is _NODE_VALUES["enabled"]
    assert isinstance(instance.enabled, bool)
    assert instance.issued_at == _NODE_VALUES["issued_at"]
    assert isinstance(instance.issued_at, str)
    assert instance.payload == _NODE_VALUES["payload"]
    assert isinstance(instance.payload, dict)
    # IPHost intentionally stringifies — the SDK returns an
    # ``IPv4Interface``; the adapter normalises to its str form.
    assert instance.address == _NODE_VALUES["address"]
    assert isinstance(instance.address, str)


def _make_infrahub_client(address: str, token: str) -> Any:
    """Build a sync Infrahub client. Imported lazily so unit-only test
    runs aren't forced to install the SDK extras."""
    from infrahub_sdk import Config, InfrahubClientSync

    return InfrahubClientSync(config=Config(address=address, api_token=token))
