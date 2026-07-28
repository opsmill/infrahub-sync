"""T081 — the offline apply-conformance harness (FR-013, FR-028.4, AD054, AD067, AD068).

What this file measures that nothing else can: **the mutation the SDK renders**. The class of
defect AD042 named — a payload assembled from source attributes alone, so the convergent write
goes out unkeyed and every re-apply duplicates the object — is invisible to an assertion made
against the assembled `data`, because by the time `data` is complete a relationship-crossing
identity component is already a resolved node-id **string**. "Every component present in
`data`" can hold while the mutation goes out with neither `id` nor `hfid`.

So the harness is built against a **real** `InfrahubNodeSync`, constructed from the committed
`NodeSchemaAPI` fixture at `tests/data/apply_conformance_schemas.json`, with only the transport
edge replaced. Everything between the write surface and the wire is the SDK's own: the
`hfid`/`id` selection (`infrahub_sdk/node/node.py:295-298`), the upsert render's
`exclude_hfid=False` (`:1843-1846`), the update render's `do_full_update` (`:1870-1872`), and
the GraphQL rendering. No server is contacted and nothing here is `integration`-marked.

Four assertions, because keyedness splits in two (AD067):

1. every operation on an **all-direct** human-friendly-ID kind renders keyed;
2. the same assertion on the kind whose human-friendly ID **crosses a relationship**, marked
   `xfail(strict=True)` — it cannot pass today, and the day the write surface closes the hole
   it becomes an xpass and fails the suite, so the limitation retires itself;
3. the replace-set is issued for every cardinality-many relationship including `peers: []`, a
   destination **read** was issued before the peer set it compares against was read, and the
   **flush** is an issued `node.update(do_full_update=True)`;
4. applying the same operation twice renders **byte-identical** mutation inputs.

What it deliberately does **not** assert: "two applies produce one object". A fixture holds no
destination state, there is no operation-level dedup in this design and none is wanted, so two
applies simply issue two creates and that assertion could only ever pass for the wrong reason.
Byte-identity is the strongest claim decidable offline, and it is the property convergence
rests on. This harness is no substitute for SC-002, SC-003 and SC-008, which stay deferred; for
a relationship-crossing convergence key the offline signal is a recorded expected failure, not
evidence (V39, AD051).
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
from infrahub_sdk.node.relationship import RelationshipManagerBase
from infrahub_sdk.schema import NodeSchemaAPI
from infrahub_sdk.schema.main import BranchSchema

from infrahub_sync.adapters.infrahub import InfrahubAdapter, PeerResolver
from infrahub_sync.plan.identity import canonical_identity, operation_id
from infrahub_sync.plan.models import PlannedOperation, RelationshipReference

if TYPE_CHECKING:
    from collections.abc import Iterator

SCHEMA_FIXTURE = Path(__file__).resolve().parents[1] / "data" / "apply_conformance_schemas.json"

SITE_KIND = "ConfSite"
TAG_KIND = "ConfTag"
TEAM_KIND = "ConfTeam"
DEVICE_KIND = "ConfDevice"

# The kinds whose every human-friendly-ID component is a direct attribute. Assertion 1 is a
# universal over these and **only** these; assertion 2 carries `ConfDevice` alone (AD067).
ALL_DIRECT_KINDS = (SITE_KIND, TAG_KIND, TEAM_KIND)

NODE_ID = "conformance-node-1"

XFAIL_REASON = (
    "A human-friendly ID that crosses a relationship cannot render keyed today — the Material "
    "risk row in plan.md. The plan carries no destination UUID (FR-012 forbids the load that "
    "would supply one) so data['id'] is never set; the resolved relationship renders as "
    "{'id': ...} with no __typename, so RelatedNodeSync.get() raises rather than consulting the "
    "store, get_path_value catches that and returns None, and one None nulls the whole hfid. "
    "Strict, so the day the write surface closes the hole this xpasses and fails the suite."
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

    One log rather than separate lists, because two properties under test are about **order**:
    that the relationship re-read was issued before the peer set was compared (AD065), and that
    the flush followed the reconciliation (AD075).
    """

    def __init__(self) -> None:
        super().__init__(config=Config(address="http://localhost:8000", api_token="token"))  # noqa: S106
        self.schema.set_cache(BranchSchema(hash="conformance-fixture", nodes=dict(SCHEMAS)))
        self.events: list[tuple[str, Any]] = []
        self.existing_peers: dict[tuple[str, str], list[str]] = {}

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
        """Answer the relationship re-read with the destination's seeded peer set."""
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
        """Just the names, which is what separates an upsert flush from an update flush."""
        return [name for name, _ in self.mutations]


def make_adapter(client: ConformanceClient) -> InfrahubAdapter:
    """The adapter with only the state the planned-write surface reads, and no network setup."""
    adapter = InfrahubAdapter.__new__(InfrahubAdapter)
    adapter.client = client
    adapter.source_node = None
    adapter.owner_node = None
    adapter.schema = dict(SCHEMAS)
    adapter._unkeyed_render_reported = set()
    return adapter


def make_operation(
    *,
    kind: str,
    identity: dict[str, Any],
    payload: dict[str, Any],
    action: str = "create",
    relationships: list[RelationshipReference] | None = None,
) -> PlannedOperation:
    """One planned operation, with its identifier derived the way the artifact derives it."""
    canonical = canonical_identity(identity, kind=kind)
    return PlannedOperation(
        operation_id=operation_id(action, kind, canonical),
        action=action,  # ty: ignore[invalid-argument-type]
        kind=kind,
        identity=canonical,
        tier=0,
        payload=payload,
        relationships=relationships,
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


# The operations assertion 1 quantifies over: a create and an update on every all-direct kind.
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


@contextmanager
def record_rendered_inputs() -> Iterator[list[tuple[str, dict[str, Any]]]]:
    """Record the **mutation input** the SDK renders, per node kind.

    `_generate_input_data` is the single point every write path renders through — the upsert
    (`exclude_hfid=False`) and the `do_full_update` flush alike — and its `["data"]["data"]`
    is the mapping the mutation is built from. Recording here rather than regex-scraping the
    rendered query is what makes "carries `id` or `hfid`" a decidable claim: `id:` also occurs
    inside every relationship value, so a text search cannot tell a keyed mutation from an
    unkeyed one that happens to carry a resolved peer.
    """
    rendered: list[tuple[str, dict[str, Any]]] = []
    real = InfrahubNodeSync._generate_input_data

    def spy(self: InfrahubNodeSync, *args: Any, **kwargs: Any) -> dict[str, dict]:  # noqa: ANN401
        result = real(self, *args, **kwargs)
        rendered.append((self._schema.kind, dict(result["data"]["data"])))
        return result

    with patch.object(InfrahubNodeSync, "_generate_input_data", spy):
        yield rendered


@contextmanager
def record_peer_set_reads(client: ConformanceClient) -> Iterator[None]:
    """Log every read of `RelationshipManagerBase.peer_ids` onto the client's event log.

    AD065's observable is an ordering, and "the manager was fetched" is the weaker observable
    the broken implementation also satisfies: a manager built from the write payload reports
    `initialized = True`, so `fetch()` returns through its own guard
    (`infrahub_sdk/node/relationship.py:286-288`) without reaching the `client.get` inside it,
    and `peer_ids` is still the desired set.
    """
    original = RelationshipManagerBase.__dict__["peer_ids"]

    def read(self: RelationshipManagerBase) -> list[str]:
        client.events.append(("peer_ids", self.schema.name))
        return original.fget(self)

    with patch.object(RelationshipManagerBase, "peer_ids", property(read)):
        yield


def keys_of(rendered: list[tuple[str, dict[str, Any]]], kind: str) -> list[set[str]]:
    """The key set of every mutation input rendered for `kind`."""
    return [set(data) for node_kind, data in rendered if node_kind == kind]


def rendered_relationship_ids(query: str, rel_name: str) -> list[str] | None:
    """The peer ids inside a rendered mutation's `<rel>:` list, or None if it has no such key."""
    match = re.search(rf"\b{rel_name}:\s*\[(.*?)\]", query, flags=re.DOTALL)
    if match is None:
        return None
    return re.findall(r'id:\s*"([^"]+)"', match.group(1))


def event_index(client: ConformanceClient, name: str, predicate: Any = None) -> int:  # noqa: ANN401
    """The index of the first matching event on the client's log, or -1."""
    for index, (event_name, payload) in enumerate(client.events):
        if event_name == name and (predicate is None or predicate(payload)):
            return index
    return -1


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


def test_the_committed_fixture_holds_both_human_friendly_id_shapes() -> None:
    """The precondition every assertion below rests on (Trap 4, AD067).

    A fixture drifting to all-direct kinds only would leave assertion 2 vacuous and remove the
    one thing that exercises AD051's second arm — while the suite stayed green. So the shapes
    are asserted rather than assumed.
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


# ---------------------------------------------------------------------------------------
# Assertion 1 — keyedness, all-direct kinds
# ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("description", "operation"),
    ALL_DIRECT_OPERATIONS,
    ids=[description for description, _ in ALL_DIRECT_OPERATIONS],
)
def test_an_all_direct_kind_renders_a_keyed_mutation(description: str, operation: PlannedOperation) -> None:
    """AD042's regression detector: the **rendered** mutation carries `id` or `hfid`.

    Not "every identity component is present in `data`" — that assertion holds for a mutation
    that goes out with no key at all, which is exactly the defect. The SDK sets `data["id"]`
    when the node has one and otherwise `data["hfid"]` when `exclude_hfid` is false
    (`infrahub_sdk/node/node.py:295-298`), and the upsert path renders with `exclude_hfid=False`
    (`:1843-1846`). A payload assembled from `source_attrs` alone loses the identity components,
    `get_human_friendly_id()` returns `None`, neither key is set, and the destination accepts a
    duplicate on every re-apply — silently.
    """
    _ = description
    _client, adapter, peers = seeded_adapter(members=[])

    with record_rendered_inputs() as rendered:
        adapter.apply_planned_operation(operation=operation, peers=peers)

    keyed = keys_of(rendered, operation.kind)
    assert keyed, f"nothing was rendered for {operation.kind}"
    for index, keys in enumerate(keyed):
        assert "id" in keys or "hfid" in keys, (
            f"Render {index} for {operation.kind} carries neither 'id' nor 'hfid', so the convergent "
            f"write is unkeyed and every re-apply duplicates the object. Rendered keys: {sorted(keys)}."
        )


# ---------------------------------------------------------------------------------------
# Assertion 2 — keyedness, the relationship-crossing kind (AD067)
# ---------------------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason=XFAIL_REASON)
def test_a_relationship_crossing_kind_renders_a_keyed_mutation() -> None:
    """The *same* assertion as above, on the kind that cannot satisfy it today.

    Writing assertion 1 as a universal over the whole fixture is what made the earlier form of
    this task unsatisfiable: an implementer would have dropped the relationship-crossing kind —
    losing the only thing that exercises AD051's second arm — or quietly weakened the claim.
    The split keeps the kind in the fixture and records the limitation as an expected failure,
    and `strict=True` means the day the write surface closes the hole this becomes an xpass and
    fails the suite. The limitation retires itself.
    """
    client, adapter, peers = seeded_adapter()

    with record_rendered_inputs() as rendered:
        adapter.apply_planned_operation(operation=device_operation(), peers=peers)

    assert client.mutation_names == [f"{DEVICE_KIND}Upsert"]
    keyed = keys_of(rendered, DEVICE_KIND)
    assert keyed
    for keys in keyed:
        assert "id" in keys or "hfid" in keys


# ---------------------------------------------------------------------------------------
# Assertion 3 — the re-read, and the flush
# ---------------------------------------------------------------------------------------


def test_the_replace_set_re_reads_the_destination_and_flushes_an_update() -> None:
    """AD054/AD065/AD075/AD085: the reconciled peer set reaches the destination.

    Three separable failures, each with its own observable:

    - **no re-read.** The observable is an *issued* `client.get` scoped to the relationship,
      ordered before the peer set is read. "The manager was fetched" is satisfied by an
      implementation that reads nothing: `fetch()` self-guards on `initialized`, and a manager
      built from the write payload reports `initialized = True`, so `peer_ids` *is* the desired
      set and the comparison finds nothing to do.
    - **no flush.** `RelationshipManagerSync` has no `save`; `add()` and `remove()` only mutate
      a list and set a flag. "The surplus is removed" is therefore true of an in-memory list
      that is then discarded, so the observable is the issued mutation.
    - **the wrong flush.** An `<kind>Upsert` would carry the full peer list too, so the peer
      list cannot separate a correct flush from a second upsert; the **mutation name** can.
    """
    client, adapter, peers = seeded_adapter(members=["conf-tag-id-9", "conf-tag-id-2"])

    with record_peer_set_reads(client):
        adapter.apply_planned_operation(operation=team_operation(["tag-b", "tag-c"]), peers=peers)

    assert client.mutation_names == [f"{TEAM_KIND}Upsert", f"{TEAM_KIND}Update"], (
        "The convergent upsert, then exactly one flush, and the flush is an update rather than a "
        "second upsert (infrahub_sdk/node/node.py:1872 against :1845)."
    )
    _, flush = client.mutations[1]
    assert rendered_relationship_ids(flush, "members") == ["conf-tag-id-2", "conf-tag-id-3"], (
        f"The flush must carry exactly the plan's peer set, with 'conf-tag-id-9' removed. Rendered:\n{flush}"
    )
    assert f'id: "{NODE_ID}"' in flush, "The flush must target the node whose manager was reconciled."

    read_index = event_index(client, "get", lambda kwargs: kwargs.get("include") == ["members"])
    assert read_index >= 0, "A destination read scoped to the relationship must be ISSUED (AD065)."
    compare_index = event_index(client, "peer_ids", lambda rel: rel == "members")
    assert 0 <= read_index < compare_index, (
        f"The destination read must precede the peer-set comparison, got read at {read_index} and "
        f"comparison at {compare_index}."
    )


def test_an_empty_peer_list_is_issued_as_an_emptied_set() -> None:
    """AD085: `peers: []` under `cardinality: many` survives the flush as `[]`.

    The case that separates `node.update(do_full_update=True)` from a plain `node.save()`. A
    plain save renders with unmodified-field stripping on; the create payload already wrote
    `[]` for the same field, so the rendered value matches, the key is popped, and the emptied
    set never leaves the process **while the mutation name stays identical**. The rendered
    relationship value is the only observable that tells them apart.
    """
    client, adapter, peers = seeded_adapter(members=["conf-tag-id-1", "conf-tag-id-2"])

    adapter.apply_planned_operation(operation=team_operation([]), peers=peers)

    assert client.mutation_names == [f"{TEAM_KIND}Upsert", f"{TEAM_KIND}Update"]
    _, flush = client.mutations[1]
    assert rendered_relationship_ids(flush, "members") == [], (
        f"The flush must carry an empty `members` list, not omit the key. Rendered:\n{flush}"
    )


# ---------------------------------------------------------------------------------------
# Assertion 4 — repeat-render identity (AD068)
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
    """AD068: two applies of one operation render the same bytes — the same `data`, the same key.

    This is the strongest convergence claim decidable offline. "Two applies yield one object"
    is not assertable against a fixture that holds no destination state — two applies simply
    issue two creates — so byte-identity stands in its place, and it regresses whenever
    assertion 1 or assertion 3 regresses. Anything time-dependent, counter-dependent or
    carried over from the first apply's state shows up here and nowhere else offline.

    The two applies use **separate** clients and resolvers, so the second render cannot inherit
    a node id or a memo entry the first produced: the claim is about the operation record
    rendering the same way, not about a warm cache making it look that way.
    """
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
    for _kind, data in renders[0]:
        assert "id" in data or "hfid" in data, (
            "Byte-identity is only worth having under assertion 1's condition: the repeated render "
            "must also be a keyed one."
        )
