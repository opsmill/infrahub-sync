"""The emptied-peer-set flush on the planned-write path, and its SDK-boundary tripwire.

`InfrahubAdapter.apply_planned_operation` reconciles every cardinality-many relationship
as an explicit replace-set after the convergent upsert and then flushes it with
`node.update(do_full_update=True)` (AD085, amending AD075). These tests exist because the
case that decides between a plain `node.save()` and that full update — a relationship
reconciled to the **empty** set — is invisible to any assertion made against a mock: the
reconciliation is purely in-memory, so a mock adapter call proves nothing about what
reached the destination.

Both tests therefore work against a real `InfrahubNodeSync` built over a real
`NodeSchemaAPI` and read the **rendered mutation**, and both run offline: no live Infrahub
is contacted and neither is `integration`-marked.
"""

from __future__ import annotations

import re
from typing import Any

from infrahub_sdk import Config, InfrahubClientSync
from infrahub_sdk.node import InfrahubNodeSync, RelationshipManagerSync
from infrahub_sdk.schema import NodeSchemaAPI
from infrahub_sdk.schema.main import (
    AttributeKind,
    AttributeSchemaAPI,
    BranchSchema,
    RelationshipKind,
    RelationshipSchemaAPI,
)

from infrahub_sync.adapters.infrahub import InfrahubAdapter, PeerResolver
from infrahub_sync.plan.identity import operation_id
from infrahub_sync.plan.models import PlannedOperation, RelationshipReference

THING_KIND = "TestThing"
MEMBER_KIND = "TestMember"
NODE_ID = "thing-1"

THING_SCHEMA = NodeSchemaAPI(
    id="thing-schema",
    name="Thing",
    namespace="Test",
    label="Thing",
    default_filter="name__value",
    human_friendly_id=["name__value"],
    attributes=[AttributeSchemaAPI(id="thing-name", name="name", kind=AttributeKind.TEXT, optional=False, unique=True)],
    relationships=[
        RelationshipSchemaAPI(
            id="thing-members",
            name="members",
            peer=MEMBER_KIND,
            cardinality="many",
            kind=RelationshipKind.GENERIC,
            optional=True,
            identifier="thing__member",
        )
    ],
)

MEMBER_SCHEMA = NodeSchemaAPI(
    id="member-schema",
    name="Member",
    namespace="Test",
    label="Member",
    default_filter="name__value",
    human_friendly_id=["name__value"],
    attributes=[
        AttributeSchemaAPI(id="member-name", name="name", kind=AttributeKind.TEXT, optional=False, unique=True)
    ],
    relationships=[],
)

# What the tripwire says when the SDK stops behaving the way the flush depends on.
SDK_BOUNDARY_MESSAGE = (
    "The infrahub-sdk's unmodified-field stripping has changed. `pyproject.toml` pins "
    "`infrahub-sdk[all]>=1.17,<2`, a range, and this behaviour is undocumented internals, so a "
    "permitted upgrade can move it without any other signal. AD085 depends on it: the planned-write "
    "flush in `InfrahubAdapter.apply_planned_operation` is `node.update(do_full_update=True)` — "
    "rather than a plain `node.save()` — precisely because the stripping drops a cardinality-many "
    "relationship reconciled to the empty set, and `do_full_update=True` turns the stripping off. "
    "Re-derive AD085 against the new SDK before changing this test."
)


class RecordingClient(InfrahubClientSync):
    """A real client whose three destination calls are recorded instead of issued.

    `schema.get`, `create` and the payload generation stay real — only the network edge is
    replaced — so the mutations recorded here are the ones the SDK would actually send.
    """

    def __init__(self) -> None:
        super().__init__(config=Config(address="http://localhost:8000", api_token="token"))  # noqa: S106
        self.schema.set_cache(
            BranchSchema(hash="fixture", nodes={THING_KIND: THING_SCHEMA, MEMBER_KIND: MEMBER_SCHEMA})
        )
        self.mutations: list[tuple[str, str]] = []
        self.reads: list[dict[str, Any]] = []
        self.existing_peer_ids: list[str] = []

    def execute_graphql(self, *args: Any, **kwargs: Any) -> dict[str, Any]:  # noqa: ANN401, ARG002
        """Record the rendered mutation and answer as a successful write would."""
        query = kwargs["query"]
        match = re.search(r"mutation\s*\{\s*(\w+)", query)
        if match is None:
            msg = f"Unrecognised mutation rendered by the SDK: {query!r}"
            raise AssertionError(msg)
        mutation_name = match.group(1)
        self.mutations.append((mutation_name, query))
        return {mutation_name: {"ok": True, "object": {"id": NODE_ID}}}

    def get(self, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401, ARG002
        """Answer the relationship re-read with the destination's seeded peer set."""
        self.reads.append(kwargs)
        return InfrahubNodeSync(
            client=self,
            schema=THING_SCHEMA,
            data={
                "id": kwargs.get("id"),
                "members": [{"id": peer_id, "__typename": MEMBER_KIND} for peer_id in self.existing_peer_ids],
            },
        )

    def filters(self, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401, ARG002, PLR6301
        """`fetch()` hydrates the peers it just read; nothing here reads their attributes."""
        return []


def _adapter(client: RecordingClient) -> InfrahubAdapter:
    """The adapter with only the state `apply_planned_operation` reads, and no network setup."""
    adapter = InfrahubAdapter.__new__(InfrahubAdapter)
    adapter.client = client
    adapter.source_node = None
    adapter.owner_node = None
    return adapter


def _operation(peers: list[dict[str, Any]]) -> PlannedOperation:
    """An update of one `TestThing` reconciling `members` to exactly `peers`."""
    identity = {"name": "thing-a"}
    return PlannedOperation(
        operation_id=operation_id("update", THING_KIND, identity),
        action="update",
        kind=THING_KIND,
        identity=identity,
        tier=0,
        payload={"name": "thing-a"},
        relationships=[RelationshipReference(field="members", peer_kind=MEMBER_KIND, cardinality="many", peers=peers)],
    )


def _flush_mutation(client: RecordingClient) -> tuple[str, str]:
    """The second recorded mutation: the flush that follows the convergent upsert."""
    expected_writes = 2
    assert len(client.mutations) == expected_writes, (
        f"Expected the convergent upsert and then one flush, got {[name for name, _ in client.mutations]}."
    )
    return client.mutations[1]


def _rendered_member_ids(query: str) -> list[str] | None:
    """The peer ids inside a rendered mutation's `members:` list, or None if it has no such key."""
    match = re.search(r"members:\s*\[(.*?)\]", query, flags=re.DOTALL)
    if match is None:
        return None
    return re.findall(r'id:\s*"([^"]+)"', match.group(1))


def test_emptied_peer_set_is_carried_by_the_issued_flush_mutation() -> None:
    """AD085: `peers: []` reaches the destination in the flush the adapter actually issues.

    The assertion is on the rendered GraphQL the SDK hands to the transport, not on a mock
    adapter call: an implementation that reconciles the manager to the empty set and then
    lets the unmodified-field stripping drop it renders an update carrying `id` alone, and
    the destination keeps the peers the plan says to remove.
    """
    client = RecordingClient()
    client.existing_peer_ids = ["member-1", "member-2"]
    adapter = _adapter(client)

    node_id = adapter.apply_planned_operation(operation=_operation(peers=[]), peers=PeerResolver(adapter))

    assert node_id == NODE_ID
    mutation_name, query = _flush_mutation(client)
    assert mutation_name == f"{THING_KIND}Update", (
        "The flush must be an update of the node the upsert converged on, not a second upsert."
    )
    assert _rendered_member_ids(query) == [], (
        f"The flush must carry an empty `members` list. Rendered mutation:\n{query}"
    )
    assert f'id: "{NODE_ID}"' in query, "The flush must target the node whose manager was reconciled."
    assert client.reads, "The reconciliation must re-read the destination peer set before comparing (AD065)."


def test_non_empty_replace_set_is_carried_by_the_issued_flush_mutation() -> None:
    """The path AD075 already covered stays intact: a non-empty replace renders in full."""
    client = RecordingClient()
    client.existing_peer_ids = ["member-1"]
    adapter = _adapter(client)
    peers = PeerResolver(adapter)
    peers.remember(MEMBER_KIND, {"name": "member-b"}, "member-2")

    adapter.apply_planned_operation(operation=_operation(peers=[{"name": "member-b"}]), peers=peers)

    mutation_name, query = _flush_mutation(client)
    assert mutation_name == f"{THING_KIND}Update"
    assert _rendered_member_ids(query) == ["member-2"], (
        f"The flush must carry exactly the reconciled peer set, with the surplus peer removed. "
        f"Rendered mutation:\n{query}"
    )


def test_sdk_still_strips_an_emptied_relationship_only_when_excluding_unmodified() -> None:
    """SDK-boundary tripwire for AD085: the stripping behaviour the flush is chosen for.

    `node.update(do_full_update=True)` renders with `exclude_unmodified=False`. This pins
    both halves of why that matters, straight at the SDK with no adapter code involved: with
    the stripping on, a cardinality-many relationship reconciled to the empty set is dropped
    because its rendered value equals the create payload's; with it off, the emptied set
    survives and `id` is still rendered, so the update targets the right node.
    """
    client = RecordingClient()
    create_data = client.schema.generate_payload_create(schema=THING_SCHEMA, data={"name": "thing-a", "members": []})
    assert create_data["members"] == [], "Precondition: the create payload writes an empty list for the field."

    node = InfrahubNodeSync(client=client, schema=THING_SCHEMA, data=create_data)
    node.id = NODE_ID
    node._existing = True

    manager = node.members
    assert isinstance(manager, RelationshipManagerSync)
    manager.add("member-1")
    manager.remove("member-1")
    assert manager.peer_ids == [], "Precondition: the manager is reconciled to the empty set."
    assert manager.has_update, "Precondition: reconciling the manager sets its update flag."

    stripped = node._generate_input_data(exclude_unmodified=True)["data"]["data"]
    retained = node._generate_input_data(exclude_unmodified=False)["data"]["data"]

    assert "members" not in stripped, SDK_BOUNDARY_MESSAGE
    assert retained.get("members") == [], SDK_BOUNDARY_MESSAGE
    assert retained.get("id") == NODE_ID, SDK_BOUNDARY_MESSAGE


def test_sdk_update_still_maps_do_full_update_onto_the_stripping() -> None:
    """SDK-boundary tripwire for AD085: `do_full_update` is what turns the stripping off.

    The other half of the boundary. If `update()` stopped inverting `do_full_update` into
    `exclude_unmodified`, or stopped rendering an update mutation, the flush would go back to
    dropping the emptied peer set with nothing else to notice it.
    """
    rendered: dict[bool, list[str] | None] = {}
    for do_full_update in (True, False):
        client = RecordingClient()
        create_data = client.schema.generate_payload_create(
            schema=THING_SCHEMA, data={"name": "thing-a", "members": []}
        )
        node = InfrahubNodeSync(client=client, schema=THING_SCHEMA, data=create_data)
        node.id = NODE_ID
        node._existing = True
        manager = node.members
        assert isinstance(manager, RelationshipManagerSync)
        manager.add("member-1")
        manager.remove("member-1")

        node.update(do_full_update=do_full_update)

        mutation_name, query = client.mutations[0]
        assert mutation_name == f"{THING_KIND}Update", SDK_BOUNDARY_MESSAGE
        rendered[do_full_update] = _rendered_member_ids(query)

    assert rendered[True] == [], SDK_BOUNDARY_MESSAGE
    assert rendered[False] is None, SDK_BOUNDARY_MESSAGE
