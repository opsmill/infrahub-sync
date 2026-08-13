"""Tests for source/owner attribution on relationships in ``update_node``.

Regression coverage for the bug where ``update_node`` stamped ``source``/
``owner`` metadata onto updated **attributes** but not onto updated
**relationships** (opsmill/infrahub-sync#142). A relationship changed by a sync
must now carry the same attribution as an attribute changed in the same update,
matching the create path.

The tests use lightweight stand-ins where sufficient and real SDK nodes for the
cardinality-one path, where retaining the resolved peer controls resource-pool
allocation behavior. ``resolve_peer_node`` is monkeypatched so no network
plumbing is needed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, cast
from unittest.mock import MagicMock

import pytest
from infrahub_sdk.node import InfrahubNodeSync, RelatedNodeSync
from infrahub_sdk.schema.main import NodeSchemaAPI, RelationshipSchemaAPI

from infrahub_sync.adapters import infrahub as infrahub_adapter
from infrahub_sync.adapters.infrahub import _relationship_input_data, update_node  # noqa: PLC2701

SOURCE_ID = "source-account-id"
OWNER_ID = "owner-account-id"


# ---------------------------------------------------------------------------
# Lightweight stand-ins for ``InfrahubNodeSync`` and its schema
# ---------------------------------------------------------------------------


@dataclass
class FakeAttr:
    """Attribute manager stand-in — ``update_node`` sets ``value``/``source``/``owner``."""

    value: Any = None
    source: Any = None
    owner: Any = None


@dataclass
class FakeRelSchema:
    """Relationship schema stand-in."""

    name: str
    peer: str
    cardinality: str


@dataclass
class FakeSchema:
    attribute_names: list[str] = field(default_factory=list)
    relationships: list[FakeRelSchema] = field(default_factory=list)
    relationship_names: list[str] = field(default_factory=list)


class FakeSchemaClient:
    """Stand-in for ``client.schema`` — only ``all()`` is used."""

    def __init__(self, peers: dict[str, object]) -> None:
        self._peers = peers

    def all(self, branch: str | None = None) -> dict[str, object]:  # noqa: ARG002
        return self._peers


class FakeClient:
    def __init__(self, peers: dict[str, object]) -> None:
        self.schema = FakeSchemaClient(peers)
        self.store = object()


class FakeRelManager:
    """Cardinality-many manager stand-in — records add/remove calls."""

    def __init__(self, existing_ids: list[str] | None = None) -> None:
        self.peer_ids = list(existing_ids or [])
        self.initialized = True
        self.added: list[object] = []
        self.removed: list[str] = []

    def fetch(self) -> None:
        self.initialized = True

    def add(self, data: object) -> None:
        self.added.append(data)

    def remove(self, peer_id: str) -> None:
        self.removed.append(peer_id)


class FakeNode:
    """Stand-in for ``InfrahubNodeSync`` exposing only what ``update_node`` reads."""

    def __init__(
        self,
        schema: FakeSchema,
        client: FakeClient,
        attr_holders: dict[str, FakeAttr] | None = None,
        many_managers: dict[str, FakeRelManager] | None = None,
    ) -> None:
        self._schema = schema
        self._client = client
        self._branch = "main"
        for name, holder in (attr_holders or {}).items():
            setattr(self, name, holder)
        for name, manager in (many_managers or {}).items():
            setattr(self, name, manager)


def _run_update(node: FakeNode, attrs: dict[str, object], source: str | None = None, owner: str | None = None) -> None:
    """Call ``update_node`` on a duck-typed fake node (single scoped type suppression).

    ``update_node`` is annotated for ``InfrahubNodeSync`` but only touches members
    ``FakeNode`` provides, so the type mismatch is suppressed here once rather than
    at every call site (mirrors ``_serialise`` in test_infrahub_node_to_diffsync).
    """
    update_node(node, attrs, source=source, owner=owner)  # ty: ignore[invalid-argument-type]


def _make_sdk_relationship_nodes(*, resource_pool: bool = False) -> tuple[InfrahubNodeSync, InfrahubNodeSync]:
    """Build real SDK nodes for a cardinality-one update without network access."""
    relationship_schema = RelationshipSchemaAPI(name="location", peer="LocationRack", cardinality="one")
    node_schema = NodeSchemaAPI(name="Device", namespace="Test", relationships=[relationship_schema])
    peer_schema = NodeSchemaAPI(
        name="RackPool" if resource_pool else "Rack",
        namespace="Location",
        inherit_from=["CoreResourcePool"] if resource_pool else [],
    )
    client = MagicMock()
    client.default_branch = "main"
    client.request_context = None
    client.schema.all.return_value = {relationship_schema.peer: peer_schema}
    node = InfrahubNodeSync(client=client, schema=node_schema, data={"id": "device-id"})
    peer = InfrahubNodeSync(client=client, schema=peer_schema, data={"id": "pool-id" if resource_pool else "rack-id"})
    return node, peer


@pytest.fixture
def patch_resolve_peer(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make ``resolve_peer_node`` return a peer whose ``id`` echoes the lookup key."""

    def _fake_resolve(key: str, **_kwargs: object) -> MagicMock:
        peer = MagicMock()
        peer.id = key
        return peer

    monkeypatch.setattr(infrahub_adapter, "resolve_peer_node", _fake_resolve)


# ---------------------------------------------------------------------------
# _relationship_input_data — the helper
# ---------------------------------------------------------------------------


def test_relationship_input_data_includes_source_and_owner() -> None:
    assert _relationship_input_data("peer-id", SOURCE_ID, OWNER_ID) == {
        "id": "peer-id",
        "source": SOURCE_ID,
        "owner": OWNER_ID,
    }


@pytest.mark.parametrize(
    ("source", "owner", "expected"),
    [
        (None, None, {"id": "peer-id"}),
        (SOURCE_ID, None, {"id": "peer-id", "source": SOURCE_ID}),
        (None, OWNER_ID, {"id": "peer-id", "owner": OWNER_ID}),
    ],
)
def test_relationship_input_data_omits_unset_attribution(
    source: str | None, owner: str | None, expected: dict[str, str]
) -> None:
    assert _relationship_input_data("peer-id", source, owner) == expected


def test_helper_output_serialises_attribution_via_real_sdk() -> None:
    """A real RelatedNodeSync built from the helper emits ``_relation__source/owner``."""
    from infrahub_sdk.node.related_node import RelatedNodeSync

    data = _relationship_input_data("peer-id", SOURCE_ID, OWNER_ID)
    rel = RelatedNodeSync(client=None, branch="main", schema=MagicMock(), data=data)  # ty: ignore[invalid-argument-type]
    assert rel._generate_input_data() == {
        "id": "peer-id",
        "_relation__source": SOURCE_ID,
        "_relation__owner": OWNER_ID,
    }


def test_helper_output_without_attribution_has_no_relation_metadata() -> None:
    """With no source/owner, the mutation input carries only the peer id."""
    from infrahub_sdk.node.related_node import RelatedNodeSync

    data = _relationship_input_data("peer-id", None, None)
    rel = RelatedNodeSync(client=None, branch="main", schema=MagicMock(), data=data)  # ty: ignore[invalid-argument-type]
    assert rel._generate_input_data() == {"id": "peer-id"}


# ---------------------------------------------------------------------------
# update_node — attributes (regression: unchanged behaviour)
# ---------------------------------------------------------------------------


def test_update_node_attribute_gets_source_and_owner() -> None:
    holder = FakeAttr()
    schema = FakeSchema(attribute_names=["position"])
    node = FakeNode(schema=schema, client=FakeClient(peers={}), attr_holders={"position": holder})

    _run_update(node, {"position": 5}, source=SOURCE_ID, owner=OWNER_ID)

    assert holder.value == 5
    assert holder.source.id == SOURCE_ID
    assert holder.owner.id == OWNER_ID


def test_update_node_attribute_no_attribution_when_unset() -> None:
    holder = FakeAttr()
    schema = FakeSchema(attribute_names=["position"])
    node = FakeNode(schema=schema, client=FakeClient(peers={}), attr_holders={"position": holder})

    _run_update(node, {"position": 5})

    assert holder.value == 5
    assert holder.source is None
    assert holder.owner is None


# ---------------------------------------------------------------------------
# update_node — cardinality-one relationship (the fix)
# ---------------------------------------------------------------------------


def test_update_node_relationship_one_gets_attribution(monkeypatch: pytest.MonkeyPatch) -> None:
    node, peer = _make_sdk_relationship_nodes()
    monkeypatch.setattr(infrahub_adapter, "resolve_peer_node", lambda **_kwargs: peer)

    update_node(node, {"location": "rack-uid"}, source=SOURCE_ID, owner=OWNER_ID)

    relationship = cast("RelatedNodeSync", node.location)
    assert relationship.peer is peer
    assert vars(relationship)["source"] == SOURCE_ID
    assert vars(relationship)["owner"] == OWNER_ID
    assert relationship._generate_input_data() == {
        "id": "rack-id",
        "_relation__source": SOURCE_ID,
        "_relation__owner": OWNER_ID,
    }


def test_update_node_relationship_one_no_attribution_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    node, peer = _make_sdk_relationship_nodes()
    monkeypatch.setattr(infrahub_adapter, "resolve_peer_node", lambda **_kwargs: peer)

    update_node(node, {"location": "rack-uid"})

    relationship = cast("RelatedNodeSync", node.location)
    assert relationship.peer is peer
    assert vars(relationship)["source"] is None
    assert vars(relationship)["owner"] is None
    assert relationship._generate_input_data() == {"id": "rack-id"}


def test_update_node_relationship_one_preserves_resource_pool_allocation(monkeypatch: pytest.MonkeyPatch) -> None:
    node, pool = _make_sdk_relationship_nodes(resource_pool=True)
    monkeypatch.setattr(infrahub_adapter, "resolve_peer_node", lambda **_kwargs: pool)

    update_node(node, {"location": "pool-uid"}, source=SOURCE_ID, owner=OWNER_ID)

    relationship = cast("RelatedNodeSync", node.location)
    assert relationship.peer is pool
    assert relationship.is_resource_pool is True
    assert node._generate_input_data()["data"]["data"]["location"] == {"from_pool": {"id": "pool-id"}}


# ---------------------------------------------------------------------------
# update_node — cardinality-many relationship (the fix)
# ---------------------------------------------------------------------------


def test_update_node_relationship_many_add_gets_attribution(patch_resolve_peer: None) -> None:  # noqa: ARG001
    rel = FakeRelSchema(name="tags", peer="BuiltinTag", cardinality="many")
    schema = FakeSchema(relationships=[rel], relationship_names=["tags"])
    manager = FakeRelManager(existing_ids=["old-uid"])
    node = FakeNode(
        schema=schema,
        client=FakeClient(peers={"BuiltinTag": object()}),
        many_managers={"tags": manager},
    )

    _run_update(node, {"tags": ["t1-uid", "t2-uid"]}, source=SOURCE_ID, owner=OWNER_ID)

    # Stale peer removed; new peers added WITH attribution.
    assert manager.removed == ["old-uid"]
    assert manager.added == [
        {"id": "t1-uid", "source": SOURCE_ID, "owner": OWNER_ID},
        {"id": "t2-uid", "source": SOURCE_ID, "owner": OWNER_ID},
    ]


def test_update_node_relationship_many_no_attribution_when_unset(patch_resolve_peer: None) -> None:  # noqa: ARG001
    rel = FakeRelSchema(name="tags", peer="BuiltinTag", cardinality="many")
    schema = FakeSchema(relationships=[rel], relationship_names=["tags"])
    manager = FakeRelManager(existing_ids=[])
    node = FakeNode(
        schema=schema,
        client=FakeClient(peers={"BuiltinTag": object()}),
        many_managers={"tags": manager},
    )

    _run_update(node, {"tags": ["t1-uid"]})

    assert manager.added == [{"id": "t1-uid"}]
