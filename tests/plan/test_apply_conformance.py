"""The offline apply-conformance harness (FR-013, FR-028.4, AD054, AD068; AD067 closed).

What this file measures that nothing else can: **the mutation the SDK renders**. AD042's
defect class — a payload assembled from source attributes alone, so the convergent write goes
out carrying none of the components the destination matches on and every re-apply duplicates
the object — is invisible to an assertion against the assembled `data`, because a
relationship-crossing identity component is already a resolved node-id string by then. So the
harness runs a **real** `InfrahubNodeSync` built from the committed schema fixture with only
the transport edge replaced; no server is contacted.

The harness checks that updates render their recorded `id`, relationship-crossing creates
carry complete identity components, partial relationship identities are refused, and repeated
applies reuse one node. It also checks replace-set behavior, unmapped fields, and repeat-render
identity for direct-attribute operations.

The stateful transport fixture also models the destination's key lookup so two applies must
return the same node id. The live server remains the final check of its matching behavior.
"""

from __future__ import annotations

import json
import re
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest
from infrahub_sdk import Config, InfrahubClientSync
from infrahub_sdk.node import InfrahubNodeSync
from infrahub_sdk.schema import NodeSchemaAPI
from infrahub_sdk.schema.main import BranchSchema

from infrahub_sync.adapters.infrahub import InfrahubAdapter, PeerResolver
from infrahub_sync.plan.errors import UnaccountedIdentityComponentError
from infrahub_sync.plan.identity import canonical_identity, operation_id
from infrahub_sync.plan.models import PlannedOperation, RelationshipReference

if TYPE_CHECKING:
    from collections.abc import Iterator

CONFORMANCE_DESTINATION_ID = "18d52a8a-7e7d-9bf5-3967-c51149d169da"

SCHEMA_FIXTURE = Path(__file__).resolve().parents[1] / "data" / "apply_conformance_schemas.json"

SITE_KIND = "ConfSite"
TAG_KIND = "ConfTag"
TEAM_KIND = "ConfTeam"
DEVICE_KIND = "ConfDevice"

# The kinds whose every human-friendly-ID component is a direct attribute. Assertion 1 is a
# the set assertion 5's byte-identity quantifies over; assertion 2 carries `ConfDevice` alone,
# the kind whose human-friendly ID crosses a relationship.
ALL_DIRECT_KINDS = (SITE_KIND, TAG_KIND, TEAM_KIND)

# `ConfTeam`'s two relationships: the cardinality-many one the replace-set reconciles, and the
# optional cardinality-one one no operation here maps — assertion 4's subject.
REPLACED_RELATIONSHIP = "members"
UNMAPPED_RELATIONSHIP = "owner"

NODE_ID = "conformance-node-1"

# What assertion 4 says when the convergent upsert names a field the plan never mapped.
UNMAPPED_FIELD_MESSAGE = (
    "The convergent upsert wrote a destination field the plan never mapped. This defect class comes from "
    "depending on how the infrahub-sdk renders a node, and `pyproject.toml` pins "
    "`infrahub-sdk[all]>=1.17,<2` — a range — so a permitted upgrade can move that rendering with no other "
    "signal. `InfrahubNodeBase._generate_input_data` emits `data[<rel>] = None` for every uninitialized "
    "OPTIONAL CARDINALITY-ONE relationship once the node is marked existing "
    "(`infrahub_sdk/node/node.py`, 'to allow clearing relationships'). The upsert render is safe only "
    "because it runs while the node is still marked new: `_process_mutation_result` marks it existing "
    "afterwards. So either the library's render behaviour has changed, or a render now runs after the "
    "upsert. FR-013 — the payload is authoritative for the fields it carries and touches no other — "
    "depends on this, and so does the emptied peer set surviving as `[]`. Re-derive both against the new "
    "SDK before changing this test."
)


def _load_schemas() -> dict[str, NodeSchemaAPI]:
    """Load the committed destination schema, keyed by kind.

    A committed data file rather than schemas spelled in Python: the harness asserts what the
    SDK renders, so its input has to be a fixed, reviewable artifact.
    """
    payload = json.loads(SCHEMA_FIXTURE.read_text(encoding="utf-8"))
    schemas = [NodeSchemaAPI(**node) for node in payload["nodes"]]
    return {schema.kind: schema for schema in schemas}


SCHEMAS = _load_schemas()


class ConformanceClient(InfrahubClientSync):
    """A real client whose transport edge alone is replaced, recording one ordered event log.

    One log rather than separate lists: that the operation makes exactly one write, and the
    **absence** of any destination read on the planned-write path, are both read off the same
    log, so neither can be satisfied by an unrelated call.
    """

    def __init__(self) -> None:
        super().__init__(config=Config(address="http://localhost:8000", api_token="token"))  # noqa: S106
        self.live_schema = BranchSchema(hash="conformance-fixture", nodes=dict(SCHEMAS))
        self.schema.set_cache(self.live_schema)
        self.schema._fetch = self._fetch_schema  # ty: ignore[invalid-assignment]
        self.events: list[tuple[str, Any]] = []
        self.existing_peers: dict[tuple[str, str], list[str]] = {}

    def _fetch_schema(self, branch: str, namespaces: list[str] | None = None) -> BranchSchema:
        """Return the committed fixture for apply-time schema refreshes."""
        _ = branch, namespaces
        return self.live_schema

    def execute_graphql(self, *args: Any, **kwargs: Any) -> dict[str, Any]:  # noqa: ANN401, ARG002
        """Record the rendered mutation exactly as the SDK handed it to the transport."""
        query = kwargs["query"]
        match = re.search(r"mutation\s*\{\s*(\w+)", query)
        if match is None:
            msg = f"Unrecognised mutation rendered by the SDK: {query!r}"
            raise AssertionError(msg)
        self.events.append(("mutation", (match.group(1), query)))
        return {match.group(1): {"ok": True, "object": {"id": NODE_ID}}}

    def get(self, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401, ARG002
        """Answer a destination read with the seeded peer set — and record that it happened.

        The planned-write path issues no such read; assertion 3 reads the absence
        off the event log, and the seeded peer set differing from the plan's is what keeps
        that assertion honest.
        """
        self.events.append(("get", kwargs))
        kind = kwargs["kind"]
        schema = SCHEMAS[kind]
        data: dict[str, Any] = {"id": kwargs.get("id")}
        for rel_name in kwargs.get("include") or ():
            peer_kind = next(rel.peer for rel in schema.relationships if rel.name == rel_name)
            data[rel_name] = [
                {"id": peer_id, "__typename": peer_kind} for peer_id in self.existing_peers.get((kind, rel_name), [])
            ]
        return InfrahubNodeSync(client=self, schema=schema, data=data)

    def filters(self, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401, ARG002, PLR6301
        """The SDK's peer-hydration batch. Every peer here is memoized, so nothing else calls it."""
        return []

    @property
    def mutations(self) -> list[tuple[str, str]]:
        """Every rendered mutation, in the order it was issued."""
        return [payload for name, payload in self.events if name == "mutation"]

    @property
    def mutation_names(self) -> list[str]:
        """Just the names, which is what separates the convergent upsert from any other write."""
        return [name for name, _ in self.mutations]


class ConvergingClient(ConformanceClient):
    """Record real SDK queries and model a destination keyed by their identity fields."""

    def __init__(self) -> None:
        super().__init__()
        self.nodes: dict[tuple[str, ...], str] = {}

    def execute_graphql(self, *args: Any, **kwargs: Any) -> dict[str, Any]:  # noqa: ANN401
        response = super().execute_graphql(*args, **kwargs)
        query = kwargs["query"]
        if f"{DEVICE_KIND}Upsert" not in query:
            return response
        name = re.search(r'name:\s*\{\s*value: "([^"]+)"', query)
        site = re.search(r'site:\s*\{\s*id: "([^"]+)"', query)
        # Without both components, each write creates a new node.
        key = (site.group(1), name.group(1)) if site and name else (f"unkeyed-{len(self.nodes) + 1}",)
        node_id = self.nodes.setdefault(key, f"device-{len(self.nodes) + 1}")
        response[f"{DEVICE_KIND}Upsert"]["object"]["id"] = node_id
        return response


def make_adapter(client: ConformanceClient) -> InfrahubAdapter:
    """The adapter with only the state the planned-write surface reads, and no network setup."""
    adapter = InfrahubAdapter.__new__(InfrahubAdapter)
    adapter.client = client
    adapter.source_node = None
    adapter.owner_node = None
    adapter.schema = dict(SCHEMAS)
    return adapter


def make_operation(
    *,
    kind: str,
    identity: dict[str, Any],
    payload: dict[str, Any],
    action: str = "create",
    relationships: list[RelationshipReference] | None = None,
) -> PlannedOperation:
    """One planned operation, with its identifier derived the way the artifact derives it.

    An update carries `CONFORMANCE_DESTINATION_ID`, because plan format 3 records one for
    every update and the apply keys the write by it.
    """
    canonical = canonical_identity(identity, kind=kind)
    return PlannedOperation(
        operation_id=operation_id(action, kind, canonical),
        action=action,  # ty: ignore[invalid-argument-type]
        kind=kind,
        identity=canonical,
        tier=0,
        payload=payload,
        relationships=relationships,
        destination_id=CONFORMANCE_DESTINATION_ID if action == "update" else None,
    )


def device_operation() -> PlannedOperation:
    """A `ConfDevice` create — the kind whose human-friendly ID crosses `site`."""
    return make_operation(
        kind=DEVICE_KIND,
        identity={"name": "device-a", "site": {"peer_kind": SITE_KIND, "identity": {"name": "site-a"}}},
        payload={"name": "device-a"},
        relationships=[
            RelationshipReference(field="site", peer_kind=SITE_KIND, cardinality="one", peers=[{"name": "site-a"}])
        ],
    )


def team_operation(peer_names: list[str], *, action: str = "update") -> PlannedOperation:
    """A `ConfTeam` operation reconciling `members` to exactly `peer_names`."""
    return make_operation(
        kind=TEAM_KIND,
        action=action,
        identity={"name": "team-a"},
        payload={"name": "team-a"},
        relationships=[
            RelationshipReference(
                field="members",
                peer_kind=TAG_KIND,
                cardinality="many",
                peers=[{"name": peer_name} for peer_name in peer_names],
            )
        ],
    )


# Every all-direct create and update. Assertion 1 quantifies over the updates alone —
# only an update carries a recorded destination id — while the byte-identity assertion
# below still quantifies over both.
ALL_DIRECT_OPERATIONS: tuple[tuple[str, PlannedOperation], ...] = (
    ("create a site", make_operation(kind=SITE_KIND, identity={"name": "site-a"}, payload={"name": "site-a"})),
    (
        "update a site",
        make_operation(
            kind=SITE_KIND,
            action="update",
            identity={"name": "site-b"},
            payload={"name": "site-b", "description": "recorded"},
        ),
    ),
    ("create a tag", make_operation(kind=TAG_KIND, identity={"name": "tag-a"}, payload={"name": "tag-a"})),
    ("update a team", team_operation(["tag-a"])),
)

# The updates alone: only an update carries a recorded destination id to key on.
ALL_DIRECT_UPDATES: tuple[tuple[str, PlannedOperation], ...] = tuple(
    (description, operation) for description, operation in ALL_DIRECT_OPERATIONS if operation.action == "update"
)


@contextmanager
def record_rendered_inputs() -> Iterator[list[tuple[str, dict[str, Any]]]]:
    """Record the **mutation input** the SDK renders, per node kind.

    `_generate_input_data` is where the SDK renders a whole node, and its `["data"]["data"]`
    is the mapping the convergent upsert is built from. Recording here rather than
    regex-scraping the rendered query is what makes "carries `id` or `hfid`" a decidable claim:
    `id:` also occurs inside every relationship value, so a text search cannot tell a keyed
    mutation from an unkeyed one that happens to carry a resolved peer.

    Assertion 4 reads the upsert off the wire rather than through this spy, because its claim is
    about the fields the issued mutation **names**, which is what the destination acts on.
    """
    rendered: list[tuple[str, dict[str, Any]]] = []
    real = InfrahubNodeSync._generate_input_data

    def spy(self: InfrahubNodeSync, *args: Any, **kwargs: Any) -> dict[str, dict]:  # noqa: ANN401
        result = real(self, *args, **kwargs)
        rendered.append((self._schema.kind, dict(result["data"]["data"])))
        return result

    with patch.object(InfrahubNodeSync, "_generate_input_data", spy):
        yield rendered


def issued_reads(client: ConformanceClient) -> list[dict[str, Any]]:
    """Every destination read (`client.get`) on the client's event log."""
    return [payload for name, payload in client.events if name == "get"]


def keys_of(rendered: list[tuple[str, dict[str, Any]]], kind: str) -> list[set[str]]:
    """The key set of every mutation input rendered for `kind`."""
    return [set(data) for node_kind, data in rendered if node_kind == kind]


def rendered_relationship_ids(query: str, rel_name: str) -> list[str] | None:
    """The peer ids inside a rendered mutation's `<rel>:` list, or None if it has no such key."""
    match = re.search(rf"\b{rel_name}:\s*\[(.*?)\]", query, flags=re.DOTALL)
    if match is None:
        return None
    return re.findall(r'id:\s*"([^"]+)"', match.group(1))


def top_level_scalar_id(query: str) -> str | None:
    """The scalar `id` of a rendered mutation's top-level `data` block, if it has one.

    Depth is what makes this decidable: a relationship peer's `id` sits deeper and inside
    braces, so it cannot be mistaken for the object's own write key.
    """
    match = re.search(r'^ {12}id:\s*"([^"]+)"\s*$', query, flags=re.MULTILINE)
    return match.group(1) if match else None


def mutation_input_fields(query: str) -> list[str]:
    """The field names inside a rendered mutation's top-level `data: { ... }` input block.

    `Mutation.render` puts the mutation name at four spaces, `data: {` at eight and each field
    of that block at twelve (`infrahub_sdk/graphql/query.py` with
    `render_input_block`), so the field names are the keys at exactly that depth. Reading them
    off the wire rather than off `_generate_input_data` is the point: assertion 4's claim is
    about the mutation the planned write **issues**, not about any intermediate mapping.
    """
    return re.findall(r"^ {12}(\w+):", query, flags=re.MULTILINE)


def seeded_adapter(**existing: list[str]) -> tuple[ConformanceClient, InfrahubAdapter, PeerResolver]:
    """A client, adapter and resolver with `ConfTeam`'s destination peer sets seeded.

    The resolver is pre-memoized for the tags and the site, so no destination query is needed
    and every peer id in a rendered mutation is a fixed, comparable value.
    """
    client = ConformanceClient()
    for relationship, peer_ids in existing.items():
        client.existing_peers[TEAM_KIND, relationship] = peer_ids
    adapter = make_adapter(client)
    peers = PeerResolver(adapter)
    peers.remember(SITE_KIND, {"name": "site-a"}, "conf-site-id-1")
    for index, name in enumerate(("tag-a", "tag-b", "tag-c"), start=1):
        peers.remember(TAG_KIND, {"name": name}, f"conf-tag-id-{index}")
    return client, adapter, peers


# ---------------------------------------------------------------------------------------
# The committed fixture itself — a harness assertion, not a test of the SDK
# ---------------------------------------------------------------------------------------


def test_the_committed_fixture_holds_the_shapes_every_assertion_needs() -> None:
    """The precondition every assertion below rests on (Trap 4).

    A fixture drifting to all-direct kinds only would leave assertion 2 vacuous — nothing would
    exercise a human-friendly ID that crosses a relationship, which is the shape the server
    matches component-wise and the one AD051 resolves through a peer identity — while the suite
    stayed green. So the shapes are asserted rather than assumed.

    The same holds for the relationship shapes on `ConfTeam`. Assertion 4 is vacuous unless the
    kind under replace-set also carries an **optional cardinality-one** relationship no
    operation maps: that is the shape the destination library nulls, and a fixture without it
    is why the defect it guards against was invisible here for a whole delivery.
    """
    for kind in ALL_DIRECT_KINDS:
        components = SCHEMAS[kind].human_friendly_id or []
        assert components, f"{kind} must declare a human-friendly ID"
        assert all("__" not in component.removesuffix("__value") for component in components), (
            f"{kind} is in ALL_DIRECT_KINDS, so every component must be a direct attribute, got {components}"
        )

    crossing = SCHEMAS[DEVICE_KIND].human_friendly_id or []
    assert any("__" in component.removesuffix("__value") for component in crossing), (
        f"{DEVICE_KIND} must carry a human-friendly-ID component that crosses a relationship, got {crossing}"
    )

    team_relationships = {rel.name: rel for rel in SCHEMAS[TEAM_KIND].relationships}
    assert team_relationships[REPLACED_RELATIONSHIP].cardinality == "many", (
        f"{TEAM_KIND}.{REPLACED_RELATIONSHIP} must be the cardinality-many relationship the replace-set acts on."
    )
    unmapped = team_relationships[UNMAPPED_RELATIONSHIP]
    shape_message = (
        f"{TEAM_KIND}.{UNMAPPED_RELATIONSHIP} must be an OPTIONAL CARDINALITY-ONE relationship — the shape "
        f"assertion 4 exists for — got cardinality={unmapped.cardinality!r} optional={unmapped.optional!r}."
    )
    assert unmapped.cardinality == "one", shape_message
    assert unmapped.optional, shape_message
    assert not any(
        reference.field == UNMAPPED_RELATIONSHIP
        for _description, operation in ALL_DIRECT_OPERATIONS
        for reference in operation.relationships or ()
    ), f"No operation in this harness may map {TEAM_KIND}.{UNMAPPED_RELATIONSHIP}; that is what makes it unmapped."


# ---------------------------------------------------------------------------------------
# Assertion 1 — an update is keyed by its recorded destination id, on the wire
# ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("description", "operation"),
    ALL_DIRECT_UPDATES,
    ids=[description for description, _ in ALL_DIRECT_UPDATES],
)
def test_an_update_renders_a_scalar_top_level_id_equal_to_its_destination_id(
    description: str, operation: PlannedOperation
) -> None:
    """The write key, read off the issued mutation rather than off any intermediate mapping.

    Asserted at the wire because the SDK's pre-save private render and the mutation it
    actually sends stopped agreeing: on 1.23.2 the render reports an `hfid` the wire does
    not carry. What the destination acts on is the mutation.
    """
    _ = description
    client, adapter, peers = seeded_adapter(members=[])

    adapter.apply_planned_operation(operation=operation, peers=peers)

    _name, query = client.mutations[0]
    assert top_level_scalar_id(query) == CONFORMANCE_DESTINATION_ID, (
        f"The update must key on its recorded destination id. Rendered:\n{query}"
    )


# ---------------------------------------------------------------------------------------
# Assertion 2 — a relationship-crossing human-friendly ID converges (AD067 closed)
# ---------------------------------------------------------------------------------------


def test_a_relationship_crossing_kind_is_keyed_and_repeated_apply_reuses_the_node() -> None:
    """Complete identity fields make two creates converge on the same destination node."""
    client = ConvergingClient()
    adapter = make_adapter(client)
    peer = InfrahubNodeSync(client=client, schema=SCHEMAS[SITE_KIND], data={"id": "conf-site-id-1"})
    with patch.object(client, "filters", return_value=[peer]) as lookup:
        results = [
            adapter.apply_planned_operation(operation=device_operation(), peers=PeerResolver(adapter)) for _ in range(2)
        ]

    assert lookup.call_count == 2
    assert all(call.kwargs["name__value"] == "site-a" for call in lookup.call_args_list)

    assert client.mutation_names == [f"{DEVICE_KIND}Upsert"] * 2
    assert set(client.nodes) == {("conf-site-id-1", "device-a")}
    assert all("hfid:" not in query for _, query in client.mutations)
    assert results == ["device-1", "device-1"]
    assert len(client.nodes) == 1


@pytest.mark.parametrize("lookup_outcome", ["missing", "found"])
def test_partial_relationship_filter_is_refused_before_lookup(lookup_outcome: str) -> None:
    """An incomplete peer key is diagnosed whether or not lookup would find a peer."""
    client = ConformanceClient()
    adapter = make_adapter(client)
    peers = PeerResolver(adapter)
    peer = InfrahubNodeSync(client=client, schema=SCHEMAS[SITE_KIND], data={"id": "conf-site-id-1"})
    operation = make_operation(
        kind=DEVICE_KIND,
        identity={"name": "device-a", "site": {"peer_kind": SITE_KIND, "identity": {"code": "site-a"}}},
        payload={"name": "device-a"},
        relationships=[
            RelationshipReference(field="site", peer_kind=SITE_KIND, cardinality="one", peers=[{"code": "site-a"}])
        ],
    )

    with (
        patch.object(client, "filters", return_value=[peer] if lookup_outcome == "found" else []) as lookup,
        pytest.raises(UnaccountedIdentityComponentError, match="name__value"),
    ):
        adapter.apply_planned_operation(operation=operation, peers=peers)

    lookup.assert_not_called()
    assert client.mutations == []


def test_partial_peer_filter_cannot_key_a_relationship_create() -> None:
    """A complete parent HFID still needs a fully identified relationship peer."""
    client = ConformanceClient()
    site_schema = SCHEMAS[SITE_KIND].model_copy(update={"human_friendly_id": ["name__value", "region__value"]})
    adapter = make_adapter(client)
    adapter.schema[SITE_KIND] = site_schema
    peers = PeerResolver(adapter)
    peer = InfrahubNodeSync(client=client, schema=site_schema, data={"id": "conf-site-id-1"})

    with (
        patch.object(client, "filters", return_value=[peer]) as lookup,
        pytest.raises(UnaccountedIdentityComponentError, match="region__value"),
    ):
        adapter.apply_planned_operation(operation=device_operation(), peers=peers)

    lookup.assert_not_called()
    assert client.mutations == []


@pytest.mark.parametrize("action", ["create", "update"])
def test_every_non_key_relationship_peer_needs_a_complete_filter(action: str) -> None:
    """A partial second peer cannot bind a wrong relationship on create or update."""
    client = ConformanceClient()
    adapter = make_adapter(client)
    adapter.schema[TAG_KIND] = SCHEMAS[TAG_KIND].model_copy(
        update={"human_friendly_id": ["name__value", "code__value"]}
    )
    operation = make_operation(
        kind=TEAM_KIND,
        action=action,
        identity={"name": "team-a"},
        payload={"name": "team-a"},
        relationships=[
            RelationshipReference(
                field=REPLACED_RELATIONSHIP,
                peer_kind=TAG_KIND,
                cardinality="many",
                peers=[{"name": "tag-a", "code": "a"}, {"name": "tag-b"}],
            )
        ],
    )

    with (
        patch.object(client, "filters") as lookup,
        pytest.raises(UnaccountedIdentityComponentError, match="code__value"),
    ):
        adapter.apply_planned_operation(operation=operation, peers=PeerResolver(adapter))

    lookup.assert_not_called()
    assert client.mutations == []


# ---------------------------------------------------------------------------------------
# Assertion 3 — the replace-set is the one write, and no destination read
# ---------------------------------------------------------------------------------------


def test_the_replace_set_is_one_upsert_with_no_destination_read() -> None:
    """AD054/AD085: the plan's peer set reaches the destination on the one convergent upsert."""
    client, adapter, peers = seeded_adapter(members=["conf-tag-id-9", "conf-tag-id-2"])

    adapter.apply_planned_operation(operation=team_operation(["tag-b", "tag-c"]), peers=peers)

    assert client.mutation_names == [f"{TEAM_KIND}Upsert"], (
        "A planned operation is exactly one destination write, and it is the convergent upsert."
    )
    _, upsert = client.mutations[0]
    assert rendered_relationship_ids(upsert, "members") == ["conf-tag-id-2", "conf-tag-id-3"], (
        f"The upsert must carry exactly the plan's peer set. Rendered:\n{upsert}"
    )
    assert issued_reads(client) == [], (
        "The planned-write path issues no destination read: the fetch-and-reconcile "
        "round trips added nothing, because the SDK renders no removal directive either way."
    )


def test_an_empty_peer_list_is_issued_as_an_emptied_set() -> None:
    """AD085: `peers: []` under `cardinality: many` reaches the destination as `[]`."""
    client, adapter, peers = seeded_adapter(members=["conf-tag-id-1", "conf-tag-id-2"])

    adapter.apply_planned_operation(operation=team_operation([]), peers=peers)

    assert client.mutation_names == [f"{TEAM_KIND}Upsert"]
    _, upsert = client.mutations[0]
    assert rendered_relationship_ids(upsert, "members") == [], (
        f"The upsert must carry an empty `members` list, not omit the key. Rendered:\n{upsert}"
    )


# ---------------------------------------------------------------------------------------
# Assertion 4 — the upsert touches no unmapped destination field
# ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("peer_names", [["tag-b", "tag-c"], []], ids=["a non-empty replace", "an emptied set"])
def test_the_upsert_names_only_the_mapped_fields_and_its_key(peer_names: list[str]) -> None:
    """The upsert's input is the plan's mapped destination fields plus the SDK key, and no more.

    `owner` is the fixture's optional cardinality-one relationship that no operation maps — the
    shape a whole-node re-render nulls. The upsert must never name it, under a non-empty replace
    and under an emptied set alike.

    `team_operation` is an update, so the key it names is the recorded destination `id`.
    """
    client, adapter, peers = seeded_adapter(members=["conf-tag-id-9"])

    adapter.apply_planned_operation(operation=team_operation(peer_names), peers=peers)

    assert client.mutation_names == [f"{TEAM_KIND}Upsert"]
    _, upsert = client.mutations[0]
    fields = mutation_input_fields(upsert)
    assert sorted(fields) == sorted(["name", REPLACED_RELATIONSHIP, "id"]), (
        f"{UNMAPPED_FIELD_MESSAGE}\n\nThe upsert named {sorted(fields)}; it may name only the mapped "
        f"destination fields 'name' and {REPLACED_RELATIONSHIP!r} plus the key 'id'. Rendered "
        f"mutation:\n{upsert}"
    )
    assert f"{UNMAPPED_RELATIONSHIP}:" not in upsert, f"{UNMAPPED_FIELD_MESSAGE}\n\nRendered mutation:\n{upsert}"


# ---------------------------------------------------------------------------------------
# Assertion 5 — repeat-render identity (AD068)
# ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("description", "operation"),
    ALL_DIRECT_OPERATIONS,
    ids=[description for description, _ in ALL_DIRECT_OPERATIONS],
)
def test_applying_the_same_operation_twice_renders_byte_identical_inputs(
    description: str,
    operation: PlannedOperation,
) -> None:
    """AD068: two applies of one operation render the same bytes — the same `data`, the same key."""
    _ = description
    renders: list[list[tuple[str, dict[str, Any]]]] = []
    queries: list[list[bytes]] = []
    for _attempt in range(2):
        client, adapter, peers = seeded_adapter(members=[])
        with record_rendered_inputs() as rendered:
            adapter.apply_planned_operation(operation=operation, peers=peers)
        renders.append(rendered)
        queries.append([query.encode() for _, query in client.mutations])

    assert renders[0] == renders[1], (
        f"The two applies rendered different mutation inputs for {operation.kind}:\n"
        f"first:  {renders[0]}\nsecond: {renders[1]}"
    )
    assert queries[0] == queries[1], (
        f"The two applies issued different mutation bytes for {operation.kind}:\n"
        f"first:  {queries[0]}\nsecond: {queries[1]}"
    )
    # Byte-identity is only worth having under assertion 1's condition: the repeated
    # render must also be a keyed one. What keys it depends on the action. An update
    # carries its recorded destination id. A create never does, and on SDK 1.23.2 the
    # private render no longer reports a synthetic `hfid` either — 1.18.1 did — so what
    # is checked is what a create has always keyed on at the wire: the destination
    # kind's human-friendly-ID components in `data`, which the server matches on.
    for kind, data in renders[0]:
        if operation.action == "update":
            assert "id" in data, f"the repeated render of this {kind} update carries no destination id"
            continue
        components = [component.removesuffix("__value") for component in SCHEMAS[kind].human_friendly_id or []]
        assert components, f"{kind} must declare a human-friendly ID"
        missing = [component for component in components if component not in data]
        assert not missing, (
            f"the repeated render of this {kind} create is unkeyed: "
            f"human-friendly-ID component(s) {missing} are absent from {sorted(data)}"
        )
