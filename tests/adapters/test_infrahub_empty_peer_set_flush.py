"""The emptied-peer-set flush on the planned-write path, and its SDK-boundary tripwire.

`InfrahubAdapter.apply_planned_operation` reconciles every cardinality-many relationship as an
explicit replace-set after the convergent upsert and then flushes it with a **targeted
relationship write** — `id` plus only the fields being replaced (AD088, amending AD085's
amendment of AD075). These tests exist because the case that decides the flush's form — a
relationship reconciled to the **empty** set — is invisible to any assertion made against a
mock: the reconciliation is purely in-memory, so a mock adapter call proves nothing about what
reached the destination.

Every test here therefore works against a real `InfrahubNodeSync` built over a real
`NodeSchemaAPI` and reads the **rendered mutation**, and all of them run offline: no live
Infrahub is contacted and none is `integration`-marked.

`THING_SCHEMA` carries an optional cardinality-one `owner` that no operation here maps, because
that is the shape the SDK's whole-node render nulls. The assertion that no unmapped field
reaches the flush lives in the conformance harness, against the committed schema fixture
(`tests/plan/test_apply_conformance.py`, assertion 4); what lives here is the SDK-boundary
tripwire for the render behaviour that assertion depends on.
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
        ),
        # Unmapped by every operation here, and load-bearing: this is the shape the SDK's
        # whole-node render nulls on an existing node (AD088). Do not remove it.
        RelationshipSchemaAPI(
            id="thing-owner",
            name="owner",
            peer=MEMBER_KIND,
            cardinality="one",
            kind=RelationshipKind.ATTRIBUTE,
            optional=True,
            identifier="thing__owner",
        ),
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
    "The infrahub-sdk's node render behaviour has changed. `pyproject.toml` pins "
    "`infrahub-sdk[all]>=1.17,<2`, a range, and this behaviour is undocumented internals, so a "
    "permitted upgrade can move it without any other signal — and this one render has already "
    "produced two defects on the planned-write flush, which is why it is pinned here. AD088 "
    "depends on it: the flush in `InfrahubAdapter.apply_planned_operation` is a targeted "
    "relationship write, issuing `id` plus only the cardinality-many fields being replaced, "
    "precisely because rendering the whole node emits `<rel>: null` for every unmapped optional "
    "cardinality-one relationship and so clears destination fields the plan never mapped. AD075 "
    "(the flush exists at all) and AD085 (the emptied peer set must survive it) depend on the same "
    "render. Re-derive AD088 against the new SDK before changing this test."
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


def test_sdk_nulls_an_unmapped_optional_relationship_under_both_render_modes() -> None:
    """SDK-boundary tripwire for AD088: why no re-render of the node can be the flush.

    Straight at the SDK, no adapter code involved. Rendering a node the SDK considers existing
    emits `owner: None` for the unmapped optional cardinality-one relationship
    (`infrahub_sdk/node/node.py:260-266`), and it does so **under both render modes** — which is
    what makes the defect this decision fixes older than AD085 and independent of it:

    - stripping **on** (`exclude_unmodified=True`, a plain `node.save()`): the field survives
      both stripping loops. The first does not pop it — the pop needs a non-optional
      `RelatedNodeBase` or a `RelationshipManagerBase`, and an uninitialized optional
      cardinality-one relationship is neither. The second never visits it, because an unmapped
      field is absent from the original data the comparison walks.
    - stripping **off** (`exclude_unmodified=False`, `node.update(do_full_update=True)`): nothing
      is stripped at all.

    So the null was latent in AD075's original flush from the day it shipped; AD085 changed which
    of the two modes is used and left the null exactly where it was. If either arm of this stops
    holding, AD088's ground has moved.
    """
    client = RecordingClient()
    create_data = client.schema.generate_payload_create(schema=THING_SCHEMA, data={"name": "thing-a", "members": []})
    assert "owner" not in create_data, "Precondition: the plan maps no `owner`, so the payload carries none."

    node = InfrahubNodeSync(client=client, schema=THING_SCHEMA, data=create_data)
    node.id = NODE_ID
    node._existing = True

    manager = node.members
    assert isinstance(manager, RelationshipManagerSync)
    manager.add("member-1")
    assert manager.peer_ids == ["member-1"], "Precondition: the manager is reconciled."

    for exclude_unmodified in (True, False):
        rendered = node._generate_input_data(exclude_unmodified=exclude_unmodified)["data"]["data"]
        message = (
            f"{SDK_BOUNDARY_MESSAGE}\n\nWith exclude_unmodified={exclude_unmodified} the render produced "
            f"{rendered!r}, which no longer nulls the unmapped optional cardinality-one relationship."
        )
        assert "owner" in rendered, message
        assert rendered["owner"] is None, message
