"""The Infrahub destination's planned-write surface, offline.

`InfrahubAdapter.apply_planned_operation` is the saved-plan write surface (FR-013) and
`PeerResolver` is its apply-time peer resolution (FR-014). Everything here runs against a
real `InfrahubClientSync` whose **transport edge alone** is replaced: `schema.get`,
`generate_payload_create`, `client.create`, the node classes and the GraphQL rendering all
stay real, so the mutations recorded here are the ones the SDK would actually send. No live
Infrahub is contacted and nothing here is `integration`-marked.

Recording the rendered mutation rather than a mock adapter call is not decoration. Three of
the properties under test are invisible to an assertion made against a `MagicMock`:

- **keyedness** is a property of the rendered mutation, not of the assembled `data` — by the
  time `data` is complete a relationship-crossing human-friendly-ID component is a resolved
  node-id string, so "every component present in `data`" holds while the mutation goes out
  with neither `id` nor `hfid` (AD054, AD066);
- the **replace-set** is only real if it is *issued* — nothing about a peer set reaches the
  destination except through a write, so an assertion on any in-memory peer list is satisfied
  in full by a helper that writes nothing at a real destination (AD075). Surplus-peer
  *removal* relies on the destination Update mutation's replace semantics, which no offline
  assertion can pin — the live shrink test
  (`tests/integration/test_infrahub_replace_set_shrink_integration.py`) pins it (FIX-001);
- the **flush** that carries it is a targeted `<kind>Update` naming `id` plus only the replaced
  relationship fields, and only the rendered mutation *name* separates that from a second
  `save(allow_upsert=True)` (AD075, AD085, AD088). That the flush names no **unmapped** field is
  asserted in `tests/plan/test_apply_conformance.py`, against a fixture kind declaring one.

Covers T050 (payload cases), T051 (replace-set cases), T052 (memo cases), T053 (SC-016's
local half), T054 (SC-007's local half), T055 (the apply-loop cases) and the apply half of
T056 (SC-005). The last three drive `Potenda.apply_plan` over a stored artifact against a
recording fake destination, because what they measure — stored order, the collected delete,
the returned record — is the **engine's** contract over the write surface above.
"""

from __future__ import annotations

import logging
import re
from contextlib import contextmanager
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest
from infrahub_sdk import Config, InfrahubClientSync
from infrahub_sdk.exceptions import AuthenticationError, GraphQLError, ServerNotResponsiveError
from infrahub_sdk.node import InfrahubNodeSync, RelationshipManagerSync
from infrahub_sdk.schema import NodeSchemaAPI
from infrahub_sdk.schema.main import (
    AttributeKind,
    AttributeSchemaAPI,
    BranchSchema,
    RelationshipKind,
    RelationshipSchemaAPI,
)

from infrahub_sync.adapters.infrahub import (
    InfrahubAdapter,
    InfrahubModel,
    PeerResolver,
    update_node,
)
from infrahub_sync.plan.errors import (
    ApplyRecordInvariantError,
    OperationApplyFailedError,
    PeerAmbiguousError,
    PeerNotFoundError,
    PlanVerificationError,
    UnaccountedIdentityComponentError,
    UnkeyedWriteRefusedError,
)
from infrahub_sync.plan.identity import canonical_identity, operation_id
from infrahub_sync.plan.models import ApplyRecord, PlannedOperation, RelationshipReference
from infrahub_sync.plan.review import read_saved_plan
from infrahub_sync.plan.write_surface import PlannedWriteDestination
from infrahub_sync.potenda import Potenda
from tests.plan.artifact_fixtures import CONFIG_VERSION, SYNC_NAME, operation_record, write_artifact

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

ADAPTER_LOGGER = "infrahub_sync.adapters.infrahub"
ENGINE_LOGGER = "infrahub_sync.potenda"

APPLY_RUN_ID = "20260727T1130-77e0b4c2"

SITE_KIND = "TestSite"
TAG_KIND = "TestTag"
DEVICE_KIND = "TestDevice"
KEYLESS_KIND = "TestKeyless"
ORPHAN_KIND = "TestOrphan"
TEAM_KIND = "TestTeam"
GROUP_KIND = "TestGroup"

NODE_ID = "written-node-1"


def _text(attr_id: str, name: str, *, optional: bool = True) -> AttributeSchemaAPI:
    """One text attribute, spelled once rather than at every fixture site."""
    return AttributeSchemaAPI(id=attr_id, name=name, kind=AttributeKind.TEXT, optional=optional, unique=not optional)


def _many(rel_id: str, name: str, peer: str) -> RelationshipSchemaAPI:
    """One optional cardinality-many relationship."""
    return RelationshipSchemaAPI(
        id=rel_id,
        name=name,
        peer=peer,
        cardinality="many",
        kind=RelationshipKind.GENERIC,
        optional=True,
        identifier=f"{name}__{peer}",
    )


# An all-direct human-friendly ID. `comment` exists in the destination schema and is carried
# by no payload here, which is what makes "no unmapped destination field is written" a
# decidable assertion.
SITE_SCHEMA = NodeSchemaAPI(
    id="site-schema",
    name="Site",
    namespace="Test",
    label="Site",
    default_filter="name__value",
    human_friendly_id=["name__value"],
    attributes=[
        _text("site-name", "name", optional=False),
        _text("site-desc", "description"),
        _text("site-comment", "comment"),
    ],
    relationships=[],
)

TAG_SCHEMA = NodeSchemaAPI(
    id="tag-schema",
    name="Tag",
    namespace="Test",
    label="Tag",
    default_filter="name__value",
    human_friendly_id=["name__value"],
    attributes=[_text("tag-name", "name", optional=False)],
    relationships=[],
)

# A human-friendly ID that **crosses a relationship**: the client cannot form the `hfid`
# from a peer supplied as a resolved node id, so this kind renders unkeyed today (AD066).
DEVICE_SCHEMA = NodeSchemaAPI(
    id="device-schema",
    name="Device",
    namespace="Test",
    label="Device",
    default_filter="name__value",
    human_friendly_id=["site__name__value", "name__value"],
    attributes=[_text("device-name", "name", optional=False)],
    relationships=[
        RelationshipSchemaAPI(
            id="device-site",
            name="site",
            peer=SITE_KIND,
            cardinality="one",
            kind=RelationshipKind.ATTRIBUTE,
            optional=False,
            identifier="device__site",
        )
    ],
)

# No human-friendly ID at all — the gate's third arm (AD076). FR-024 permits such a kind and
# requires the run to survive it.
KEYLESS_SCHEMA = NodeSchemaAPI(
    id="keyless-schema",
    name="Keyless",
    namespace="Test",
    label="Keyless",
    default_filter="name__value",
    attributes=[_text("keyless-name", "name", optional=False)],
    relationships=[],
)

# An all-direct human-friendly ID whose component (`code`) a payload can omit while the
# operation record still validates, because the plan's identity is keyed on `name`.
ORPHAN_SCHEMA = NodeSchemaAPI(
    id="orphan-schema",
    name="Orphan",
    namespace="Test",
    label="Orphan",
    default_filter="name__value",
    human_friendly_id=["code__value"],
    attributes=[_text("orphan-name", "name", optional=False), _text("orphan-code", "code")],
    relationships=[],
)

# One cardinality-many relationship: the replace-set cases. `owner` is mapped by no
# operation here and is **load-bearing**: an unmapped optional cardinality-one relationship is
# the shape the SDK's whole-node render nulls, which is what the AD088 tripwire at the end of
# this module pins. Do not remove it.
TEAM_SCHEMA = NodeSchemaAPI(
    id="team-schema",
    name="Team",
    namespace="Test",
    label="Team",
    default_filter="name__value",
    human_friendly_id=["name__value"],
    attributes=[_text("team-name", "name", optional=False)],
    relationships=[
        _many("team-members", "members", TAG_KIND),
        RelationshipSchemaAPI(
            id="team-owner",
            name="owner",
            peer=TAG_KIND,
            cardinality="one",
            kind=RelationshipKind.ATTRIBUTE,
            optional=True,
            identifier="team__owner",
        ),
    ],
)

# Two cardinality-many relationships, so "one flush per operation, not one per
# relationship" (V40) is decidable.
GROUP_SCHEMA = NodeSchemaAPI(
    id="group-schema",
    name="Group",
    namespace="Test",
    label="Group",
    default_filter="name__value",
    human_friendly_id=["name__value"],
    attributes=[_text("group-name", "name", optional=False)],
    relationships=[_many("group-members", "members", TAG_KIND), _many("group-watchers", "watchers", TAG_KIND)],
)

SCHEMAS: dict[str, NodeSchemaAPI] = {
    SITE_KIND: SITE_SCHEMA,
    TAG_KIND: TAG_SCHEMA,
    DEVICE_KIND: DEVICE_SCHEMA,
    KEYLESS_KIND: KEYLESS_SCHEMA,
    ORPHAN_KIND: ORPHAN_SCHEMA,
    TEAM_KIND: TEAM_SCHEMA,
    GROUP_KIND: GROUP_SCHEMA,
}


class RecordingClient(InfrahubClientSync):
    """A real client whose destination calls are recorded on one ordered event log.

    One log rather than three lists: the flush's ordering after the upsert (AD075) and the
    **absence** of any destination read on the planned-write path (FIX-001's simplification)
    are both read off the same log, so neither can be satisfied by an unrelated call.
    """

    def __init__(self) -> None:
        super().__init__(config=Config(address="http://localhost:8000", api_token="token"))  # noqa: S106
        self.schema.set_cache(BranchSchema(hash="fixture", nodes=dict(SCHEMAS)))
        self.events: list[tuple[str, Any]] = []
        # Destination peer sets answered by the relationship re-read, per (kind, relationship).
        self.existing_peers: dict[tuple[str, str], list[str]] = {}
        # Successive answers to the resolver's destination query; the last one repeats.
        self.filter_results: list[list[InfrahubNodeSync]] = [[]]
        self.write_error: Exception | None = None
        # How many mutations succeed before `write_error` starts firing. `0` — the default —
        # fails the first one, which is what every pre-existing case here expects. A higher
        # value lets a case apply some operations and then fail, so "the operations applied
        # before it stay written" is decidable rather than vacuous.
        self.write_error_after_mutations = 0
        # Raised instead of answering the resolver's destination query, so the resolver's own
        # transport and auth edges are reachable offline and separately from the write's.
        self.filter_error: Exception | None = None

    # -- the transport edge ------------------------------------------------------------

    def execute_graphql(self, *args: Any, **kwargs: Any) -> dict[str, Any]:  # noqa: ANN401, ARG002
        """Record the rendered mutation and answer as a successful write would."""
        query = kwargs["query"]
        match = re.search(r"mutation\s*\{\s*(\w+)", query)
        if match is None:
            msg = f"Unrecognised mutation rendered by the SDK: {query!r}"
            raise AssertionError(msg)
        mutation_name = match.group(1)
        self.events.append(("mutation", (mutation_name, query)))
        if self.write_error is not None and len(self.mutations) > self.write_error_after_mutations:
            raise self.write_error
        return {mutation_name: {"ok": True, "object": {"id": NODE_ID}}}

    def get(self, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401, ARG002
        """Answer a destination read with the seeded peer set — and record that it happened.

        The planned-write path issues **no** such read (FIX-001's simplification), so the
        replace-set cases assert this event is absent from the log; seeding `existing_peers`
        to differ from the plan's set is what keeps that assertion honest.
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

    def filters(self, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401, ARG002
        """The resolver's destination query, and `fetch()`'s peer hydration.

        The two are told apart by `populate_store`: the resolver passes `False` because a
        saved-plan apply loads neither side, while the SDK's hydration batch passes `True`.
        Only the resolver's queries are recorded, so a memo assertion cannot be satisfied by
        an unrelated hydration call.
        """
        if kwargs.get("populate_store"):
            return []
        self.events.append(("filters", kwargs))
        if self.filter_error is not None:
            raise self.filter_error
        if len(self.filter_results) > 1:
            return self.filter_results.pop(0)
        return self.filter_results[0]

    # -- readers -----------------------------------------------------------------------

    @property
    def mutations(self) -> list[tuple[str, str]]:
        """Every rendered mutation, in the order the SDK handed it to the transport."""
        return [payload for name, payload in self.events if name == "mutation"]

    @property
    def mutation_names(self) -> list[str]:
        """Just the mutation names, which is what separates an upsert from an update."""
        return [name for name, _ in self.mutations]

    @property
    def resolver_queries(self) -> list[dict[str, Any]]:
        """Every destination query the peer resolver issued."""
        return [payload for name, payload in self.events if name == "filters"]


class PoisonedStore:
    """A `client.store` that fails loudly on any access (FR-014).

    A saved plan is applied without loading either side, so there is no store for the
    resolver to read: `resolve_peer_node`'s store dependency is exactly the one this path
    cannot inherit.
    """

    def __getattr__(self, name: str) -> Any:  # noqa: ANN401
        msg = f"The peer resolver read client.store.{name}, which a saved-plan apply cannot populate."
        raise AssertionError(msg)


def make_node(client: RecordingClient, kind: str, node_id: str) -> InfrahubNodeSync:
    """A destination node of `kind`, as a query result would return it."""
    return InfrahubNodeSync(client=client, schema=SCHEMAS[kind], data={"id": node_id})


def make_adapter(client: RecordingClient, *, source: str | None = None, owner: str | None = None) -> InfrahubAdapter:
    """The adapter with only the state the planned-write surface reads, and no network setup."""
    adapter = InfrahubAdapter.__new__(InfrahubAdapter)
    adapter.client = client
    adapter.source_node = make_node(client, SITE_KIND, source) if source else None
    adapter.owner_node = make_node(client, SITE_KIND, owner) if owner else None
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


def device_operation(name: str, site_name: str = "site-a") -> PlannedOperation:
    """A `TestDevice` create whose `site` is named by identity, never by destination id."""
    return make_operation(
        kind=DEVICE_KIND,
        identity={"name": name, "site": {"peer_kind": SITE_KIND, "identity": {"name": site_name}}},
        payload={"name": name},
        relationships=[
            RelationshipReference(field="site", peer_kind=SITE_KIND, cardinality="one", peers=[{"name": site_name}])
        ],
    )


def team_operation(peer_names: list[str]) -> PlannedOperation:
    """A `TestTeam` update reconciling `members` to exactly `peer_names`."""
    return make_operation(
        kind=TEAM_KIND,
        action="update",
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


def rendered_relationship_ids(query: str, rel_name: str) -> list[str] | None:
    """The peer ids inside a rendered mutation's `<rel>:` list, or None if it has no such key."""
    match = re.search(rf"\b{rel_name}:\s*\[(.*?)\]", query, flags=re.DOTALL)
    if match is None:
        return None
    return re.findall(r'id:\s*"([^"]+)"', match.group(1))


def rendered_related_id(query: str, rel_name: str) -> str | None:
    """The peer id inside a rendered mutation's cardinality-one `<rel>:` object."""
    match = re.search(rf"\b{rel_name}:\s*\{{\s*id:\s*\"([^\"]+)\"", query, flags=re.DOTALL)
    return match.group(1) if match else None


@contextmanager
def record_payload_create(client: RecordingClient) -> Iterator[list[dict[str, Any]]]:
    """Record `generate_payload_create`'s arguments while still calling the real one."""
    calls: list[dict[str, Any]] = []
    real = client.schema.generate_payload_create

    def spy(**kwargs: Any) -> dict[str, Any]:  # noqa: ANN401
        calls.append(kwargs)
        return real(**kwargs)

    with patch.object(client.schema, "generate_payload_create", spy):
        yield calls


def issued_reads(client: RecordingClient) -> list[dict[str, Any]]:
    """Every destination read (`client.get`) on the client's event log.

    The planned-write path must issue none (FIX-001): the flush writes the plan's peer set
    directly, and surplus-peer removal is the destination Update mutation's replace
    semantics, pinned live — not a fetch-and-reconcile round trip.
    """
    return [payload for name, payload in client.events if name == "get"]


def unkeyed_reports(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    """The keyedness gate's reports among the captured records (AD078)."""
    return [record for record in caplog.records if "carries neither 'id' nor 'hfid'" in record.getMessage()]


@pytest.fixture(autouse=True)
def _forbid_live_sync_update() -> Iterator[None]:
    """`InfrahubModel.update` is never reached by the planned-write path (FR-013).

    That method opens with `client.get(id=self.local_id, …)` and `local_id` is populated only
    by a destination load, which FR-012 forbids a saved-plan apply from performing — so a
    planned update routed through it would key the read on `None`.
    """

    def forbidden(self: InfrahubModel, attrs: dict) -> None:  # noqa: ARG001
        msg = "InfrahubModel.update was reached on the planned-write path; it needs a destination load (FR-012)."
        raise AssertionError(msg)

    with patch.object(InfrahubModel, "update", forbidden):
        yield


@pytest.fixture
def captured_logs(caplog: pytest.LogCaptureFixture) -> Iterator[pytest.LogCaptureFixture]:
    """Capture the adapter's logger at DEBUG.

    Deliberately below `WARNING`: an `INFO` emission has to be *captured* for the level
    assertions to fail against it rather than pass vacuously on an empty record list.
    """
    with caplog.at_level(logging.DEBUG, logger=ADAPTER_LOGGER):
        yield caplog


# ---------------------------------------------------------------------------------------
# T050 — payload cases
# ---------------------------------------------------------------------------------------


def test_the_write_is_client_create_then_an_upsert_of_payload_plus_resolved_peer_ids() -> None:
    """FR-013: `data` is the payload plus resolved peer ids, issued as a convergent upsert.

    The mutation *name* is the discriminating observable for the write shape:
    `client.create(...)` then `save(allow_upsert=True)` renders `<kind>Upsert`, whereas a
    route through `InfrahubModel.update` would render `<kind>Update` against a node the
    saved-plan path cannot key.
    """
    client = RecordingClient()
    adapter = make_adapter(client)
    peers = PeerResolver(adapter)
    peers.remember(SITE_KIND, {"name": "site-a"}, "site-id-1")

    node_id = adapter.apply_planned_operation(operation=device_operation("device-a"), peers=peers)

    assert node_id == NODE_ID
    assert client.mutation_names == [f"{DEVICE_KIND}Upsert"], (
        "A create with no cardinality-many relationship is exactly one convergent upsert."
    )
    _, query = client.mutations[0]
    assert 'value: "device-a"' in query, f"The payload's attributes must reach the write. Rendered:\n{query}"
    assert rendered_related_id(query, "site") == "site-id-1", (
        f"The relationship must reach the write as the resolved destination node id. Rendered:\n{query}"
    )


def test_no_unmapped_destination_field_is_written() -> None:
    """FR-013: the payload is authoritative for the fields it carries and touches no other.

    `TestSite` declares `comment` and no payload here carries it, so an implementation that
    filled the destination's declared fields from anywhere but the payload would write it.
    """
    client = RecordingClient()
    adapter = make_adapter(client)

    operation = make_operation(
        kind=SITE_KIND,
        identity={"name": "site-a"},
        payload={"name": "site-a", "description": "recorded"},
    )
    adapter.apply_planned_operation(operation=operation, peers=PeerResolver(adapter))

    _, query = client.mutations[0]
    assert "comment" not in query, (
        f"`comment` is declared by the destination kind and carried by no payload, so it must not be "
        f"written. Rendered mutation:\n{query}"
    )


def test_generate_payload_create_receives_the_source_owner_and_protection_arguments() -> None:
    """FR-013: lineage parity with the live `sync` create path.

    `InfrahubModel.create` passes `source`, `owner` and `is_protected=True`; a planned write
    that dropped them would produce objects whose provenance metadata differs from the ones
    the same configuration writes through `sync`.
    """
    client = RecordingClient()
    adapter = make_adapter(client, source="source-account-1", owner="owner-account-1")

    operation = make_operation(kind=SITE_KIND, identity={"name": "site-a"}, payload={"name": "site-a"})
    with record_payload_create(client) as calls:
        adapter.apply_planned_operation(operation=operation, peers=PeerResolver(adapter))

    assert len(calls) == 1
    call = calls[0]
    assert call["schema"] is SITE_SCHEMA
    assert call["source"] == "source-account-1"
    assert call["owner"] == "owner-account-1"
    assert call["is_protected"] is True
    assert call["data"] == {"name": "site-a"}, "`data` is the payload plus resolved peer ids, and nothing else."


def test_a_payload_missing_an_identity_component_is_refused_before_any_write() -> None:
    """AD042/AD051: a payload assembled from attributes alone leaves the upsert unkeyed.

    The payload is `keys` union `source_attrs` precisely so it carries the components the
    convergent write keys on. A payload built from `get_attrs()` alone excludes
    `_identifiers`, so the write goes out with no key and **every re-apply duplicates the
    object** — silently, because the destination accepts each one.

    The refusal must name *which* component is unaccounted for: an operator told only that
    "the write would be unkeyed" has nothing to act on.
    """
    client = RecordingClient()
    adapter = make_adapter(client)

    operation = make_operation(kind=ORPHAN_KIND, identity={"name": "orphan-a"}, payload={"name": "orphan-a"})

    with pytest.raises(UnaccountedIdentityComponentError) as excinfo:
        adapter.apply_planned_operation(operation=operation, peers=PeerResolver(adapter))

    message = str(excinfo.value)
    assert "code__value" in message, "The refusal must name the human-friendly-ID component that is missing."
    assert ORPHAN_KIND in message
    assert operation.operation_id in message
    assert not client.mutations, "Nothing may reach the destination once the payload is refused."


def test_an_unkeyed_render_for_an_all_direct_hfid_kind_raises_naming_lost_components() -> None:
    """AD066: the gate's raising arm reads the **rendered mutation**, not the assembled data.

    For a kind whose every human-friendly-ID component is a direct attribute, a render
    carrying neither `id` nor `hfid` can only mean the payload lost its identity components,
    so this arm refuses rather than warns — and the message has to name that cause.
    """
    client = RecordingClient()
    adapter = make_adapter(client)
    node = client.create(kind=SITE_KIND, data={"description": {"value": "carries no name at all"}})

    with pytest.raises(UnkeyedWriteRefusedError) as excinfo:
        adapter._report_unkeyed_render(node=node, node_schema=SITE_SCHEMA)

    message = str(excinfo.value)
    assert "name__value" in message
    assert "lost its identity components" in message
    assert not client.mutations, "The gate runs before the write is issued."


def test_a_kind_declaring_no_human_friendly_id_is_written_and_never_refused(
    captured_logs: pytest.LogCaptureFixture,
) -> None:
    """AD076: the gate's third arm — no human-friendly ID is a schema fact, not a defect.

    FR-024 permits such a kind and requires the run to survive it. An implementation that
    reads an **empty** component list as "every component is direct" — `all(...)` over an
    empty sequence is `True` — refuses the operation instead, and that refusal is the
    failure this case exists to catch. The report must name the **no-convergence-key**
    condition: routing an operator at "the payload lost its identity components" points at
    a cause that is not there, which is the defect AD059 exists to remove.
    """
    client = RecordingClient()
    adapter = make_adapter(client)

    operation = make_operation(kind=KEYLESS_KIND, identity={"name": "keyless-a"}, payload={"name": "keyless-a"})
    node_id = adapter.apply_planned_operation(operation=operation, peers=PeerResolver(adapter))

    assert node_id == NODE_ID
    assert client.mutation_names == [f"{KEYLESS_KIND}Upsert"], (
        "An absent human-friendly ID must not refuse the operation: the write is issued."
    )
    reports = unkeyed_reports(captured_logs)
    assert len(reports) == 1
    message = reports[0].getMessage()
    assert "declares no human-friendly ID" in message
    assert "no convergence key" in message
    assert "lost its identity components" not in message, (
        "The kind declares no convergence key at all; naming a lost payload component sends the "
        "operator after a cause that does not exist."
    )


def test_the_unkeyed_render_is_reported_once_per_kind_at_warning_level(
    captured_logs: pytest.LogCaptureFixture,
) -> None:
    """AD078: one report per destination kind, at `WARNING`, saying what to watch for.

    `apply_planned_operation` is entered once per operation, so a per-operation report would
    put one line per row into a four-thousand-operation apply. The level is pinned rather
    than described: `--quiet` floors the package logger at `WARNING`
    (`infrahub_sync/cli.py:29`), so an `INFO` emission satisfies every text-only assertion
    and then vanishes for exactly the scripted invocations where this report and the run
    record are the only signals.
    """
    client = RecordingClient()
    adapter = make_adapter(client)
    peers = PeerResolver(adapter)
    peers.remember(SITE_KIND, {"name": "site-a"}, "site-id-1")

    for name in ("device-a", "device-b"):
        adapter.apply_planned_operation(operation=device_operation(name), peers=peers)

    assert client.mutation_names == [f"{DEVICE_KIND}Upsert"] * 2, "Both operations are written."

    reports = unkeyed_reports(captured_logs)
    assert len(reports) == 1, (
        f"Two operations of one kind must produce exactly one report, got {[r.getMessage() for r in reports]}."
    )
    record = reports[0]
    assert record.levelno >= logging.WARNING, (
        f"The report is pinned to WARNING because --quiet floors the logger there; it was emitted at "
        f"{record.levelname}."
    )
    message = record.getMessage()
    assert DEVICE_KIND in message, "The report must name the destination kind."
    assert "The write is issued anyway" in message, (
        "The report must say the write is not withheld — in the present tense, because it is emitted "
        "from the render gate, before the upsert and the relationship flush it precedes."
    )
    assert "Watch for a duplicate" in message, "The report must say what to watch for at the destination."
    assert "crosses a relationship" in message, "The report must name the condition that produced it."


def test_the_unkeyed_render_report_is_deduplicated_per_kind_not_per_run(
    captured_logs: pytest.LogCaptureFixture,
) -> None:
    """AD078: two unkeyed kinds produce two reports.

    The counterpart to the case above, and the one that separates per-kind deduplication
    from per-run suppression — an implementation that reports only the first unkeyed render
    of the whole apply passes that case and fails this one, while silently withdrawing the
    disclosure for every other kind in the plan.
    """
    client = RecordingClient()
    adapter = make_adapter(client)
    peers = PeerResolver(adapter)
    peers.remember(SITE_KIND, {"name": "site-a"}, "site-id-1")

    adapter.apply_planned_operation(operation=device_operation("device-a"), peers=peers)
    keyless = make_operation(kind=KEYLESS_KIND, identity={"name": "keyless-a"}, payload={"name": "keyless-a"})
    adapter.apply_planned_operation(operation=keyless, peers=peers)

    reports = unkeyed_reports(captured_logs)
    assert len(reports) == 2, (
        f"Each unkeyed destination kind is reported once, got {[r.getMessage() for r in reports]}."
    )
    assert {DEVICE_KIND, KEYLESS_KIND} == {
        kind for kind in (DEVICE_KIND, KEYLESS_KIND) if any(kind in r.getMessage() for r in reports)
    }
    assert all(record.levelno >= logging.WARNING for record in reports)


def test_the_dedup_set_lives_for_one_apply_and_not_for_the_adapter_instance(
    captured_logs: pytest.LogCaptureFixture,
) -> None:
    """T102 / AD078: "once per kind" is once per **apply**, and the state's lifetime says so.

    Two applies through one adapter instance, each entered the way the engine enters one — by
    asking the destination for a resolver — and each discloses the kind. With the set allocated
    for the instance instead, the second apply finds every kind already reported and says
    nothing: the disclosure is withdrawn silently, on a run whose operator has no other signal,
    and the per-kind case above still passes because it only ever runs one apply.
    """
    client = RecordingClient()
    adapter = make_adapter(client)

    for _ in range(2):
        peers = adapter.new_peer_resolver()
        peers.remember(SITE_KIND, {"name": "site-a"}, "site-id-1")
        adapter.apply_planned_operation(operation=device_operation("device-a"), peers=peers)

    reports = unkeyed_reports(captured_logs)
    assert len(reports) == 2, f"Each apply discloses the kind once, got {[record.getMessage() for record in reports]}."
    assert all(DEVICE_KIND in record.getMessage() for record in reports)
    assert all(record.levelno >= logging.WARNING for record in reports)


# ---------------------------------------------------------------------------------------
# T051 — replace-set cases
# ---------------------------------------------------------------------------------------


def test_the_flush_carries_exactly_the_plans_peer_set_with_no_destination_read() -> None:
    """AD038/AD075/AD085 + FIX-001: the plan's peer set reaches the destination as the flush.

    The destination's set is seeded to *differ* from the plan's, which is what makes the
    no-read assertion honest: an implementation that still fetch-and-reconciles would issue a
    `client.get` here. It must not — the flush writes the plan's peer set directly, and
    surplus-peer removal (`tag-id-1` here) relies on the destination Update mutation's
    replace semantics, pinned by the live shrink test
    (`tests/integration/test_infrahub_replace_set_shrink_integration.py`).

    The flush must be `<kind>Update`, not a second `<kind>Upsert`: an upsert flush would
    carry the full peer list too, so the peer list alone cannot separate the two and the
    **mutation name** is the discriminating observable.
    """
    client = RecordingClient()
    client.existing_peers[TEAM_KIND, "members"] = ["tag-id-1", "tag-id-2"]
    adapter = make_adapter(client)
    peers = PeerResolver(adapter)
    peers.remember(TAG_KIND, {"name": "tag-b"}, "tag-id-2")
    peers.remember(TAG_KIND, {"name": "tag-c"}, "tag-id-3")

    adapter.apply_planned_operation(operation=team_operation(["tag-b", "tag-c"]), peers=peers)

    assert client.mutation_names == [f"{TEAM_KIND}Upsert", f"{TEAM_KIND}Update"], (
        "The convergent upsert, then exactly one flush, and the flush is an update."
    )
    _, flush = client.mutations[1]
    assert rendered_relationship_ids(flush, "members") == ["tag-id-2", "tag-id-3"], (
        f"The flush must carry exactly the plan's peer set. Rendered:\n{flush}"
    )
    assert f'id: "{NODE_ID}"' in flush, "The flush must target the node the upsert converged on (AD075)."
    assert issued_reads(client) == [], (
        "The planned-write path issues no destination read: the fetch-and-reconcile round trips were "
        "simplified away because the SDK renders no removal directive either way, and removal is the "
        "destination Update mutation's replace semantics (FIX-001/OQ-4)."
    )


def test_an_empty_peer_list_empties_the_set_in_the_issued_flush() -> None:
    """AD085: `peers: []` under `cardinality: many` reaches the destination.

    The case that decides the flush's form. A plain `node.save()` renders with
    unmodified-field stripping on; the create payload already wrote `[]` for the same field, so
    the rendered value matches, the key is popped, and the emptied set never leaves the process
    while the mutation name stays identical. The rendered relationship value is therefore the
    only observable that separates them — and it is what the targeted flush AD088 specifies
    writes explicitly rather than leaving to survive a comparison.
    """
    client = RecordingClient()
    client.existing_peers[TEAM_KIND, "members"] = ["tag-id-1", "tag-id-2"]
    adapter = make_adapter(client)

    adapter.apply_planned_operation(operation=team_operation([]), peers=PeerResolver(adapter))

    assert client.mutation_names == [f"{TEAM_KIND}Upsert", f"{TEAM_KIND}Update"]
    _, flush = client.mutations[1]
    assert rendered_relationship_ids(flush, "members") == [], (
        f"The flush must carry an empty `members` list, not omit the key. Rendered:\n{flush}"
    )


def test_a_peer_set_the_destination_already_holds_is_flushed_unchanged() -> None:
    """AD038: when the destination already holds the plan's set, the flush is a no-op write.

    The flush goes out carrying the same set the destination holds — under replace semantics
    that changes nothing, which is what keeps the write idempotent — and no destination read
    was needed to decide anything (FIX-001).
    """
    client = RecordingClient()
    client.existing_peers[TEAM_KIND, "members"] = ["tag-id-2"]
    adapter = make_adapter(client)
    peers = PeerResolver(adapter)
    peers.remember(TAG_KIND, {"name": "tag-b"}, "tag-id-2")

    adapter.apply_planned_operation(operation=team_operation(["tag-b"]), peers=peers)

    assert client.mutation_names == [f"{TEAM_KIND}Upsert", f"{TEAM_KIND}Update"]
    _, flush = client.mutations[1]
    assert rendered_relationship_ids(flush, "members") == ["tag-id-2"]
    assert issued_reads(client) == []


def test_the_flush_retains_the_peer_lineage_metadata_the_upsert_carried() -> None:
    """MIN-010: planned-apply-managed peers keep their lineage metadata through the flush.

    The upsert's create payload renders every cardinality-many peer with the adapter's
    `source`/`owner`/`is_protected` metadata (`generate_payload_create` with
    `is_protected=True`, lineage parity with the live `sync` path). The old
    fetch-and-reconcile flush re-rendered peers as bare `{id: ...}`, so kinds with
    cardinality-many relationships lost that metadata on exactly the write that stuck —
    only on the planned-apply path. The FIX-001 simplification renders the create payload's
    own managers, so the metadata survives; this pins it.
    """
    client = RecordingClient()
    adapter = make_adapter(client, source="source-account-1", owner="owner-account-1")
    peers = PeerResolver(adapter)
    peers.remember(TAG_KIND, {"name": "tag-b"}, "tag-id-2")

    adapter.apply_planned_operation(operation=team_operation(["tag-b"]), peers=peers)

    assert client.mutation_names == [f"{TEAM_KIND}Upsert", f"{TEAM_KIND}Update"]
    for role, (_, query) in zip(("upsert", "flush"), client.mutations):
        members_block = re.search(r"members:\s*\[(.*?)\]", query, flags=re.DOTALL)
        assert members_block is not None, f"The {role} must render the `members` peer list:\n{query}"
        rendered = members_block.group(1)
        assert "_relation__is_protected: true" in rendered, (
            f"The {role} must carry the peer's protection flag (MIN-010). Rendered:\n{query}"
        )
        assert '_relation__source: "source-account-1"' in rendered, (
            f"The {role} must carry the peer's source attribution (MIN-010). Rendered:\n{query}"
        )
        assert '_relation__owner: "owner-account-1"' in rendered, (
            f"The {role} must carry the peer's owner attribution (MIN-010). Rendered:\n{query}"
        )


def test_one_flush_is_issued_per_operation_not_one_per_relationship() -> None:
    """V40: the flush follows the whole reconciliation loop, once.

    Two cardinality-many relationships on one operation, both reconciled against a
    destination set that differs from the plan's, and one update carrying both.
    """
    client = RecordingClient()
    client.existing_peers[GROUP_KIND, "members"] = ["tag-id-1"]
    client.existing_peers[GROUP_KIND, "watchers"] = ["tag-id-9"]
    adapter = make_adapter(client)
    peers = PeerResolver(adapter)
    peers.remember(TAG_KIND, {"name": "tag-b"}, "tag-id-2")
    peers.remember(TAG_KIND, {"name": "tag-c"}, "tag-id-3")

    operation = make_operation(
        kind=GROUP_KIND,
        action="update",
        identity={"name": "group-a"},
        payload={"name": "group-a"},
        relationships=[
            RelationshipReference(field="members", peer_kind=TAG_KIND, cardinality="many", peers=[{"name": "tag-b"}]),
            RelationshipReference(field="watchers", peer_kind=TAG_KIND, cardinality="many", peers=[{"name": "tag-c"}]),
        ],
    )
    adapter.apply_planned_operation(operation=operation, peers=peers)

    assert client.mutation_names == [f"{GROUP_KIND}Upsert", f"{GROUP_KIND}Update"], (
        "Two cardinality-many relationships are still one flush, issued after the loop."
    )
    _, flush = client.mutations[1]
    assert rendered_relationship_ids(flush, "members") == ["tag-id-2"]
    assert rendered_relationship_ids(flush, "watchers") == ["tag-id-3"]


# ---------------------------------------------------------------------------------------
# T052 — memo cases
# ---------------------------------------------------------------------------------------


def test_a_completed_operation_resolves_a_later_reference_with_no_destination_query() -> None:
    """FR-014: an operation's own result resolves the operations that refer to it.

    Dependency-tier ordering puts the peer's create before its referrer, so the memo is what
    keeps a plan-internal reference from costing a destination round trip per row.
    """
    client = RecordingClient()
    adapter = make_adapter(client)
    peers = PeerResolver(adapter)

    site = make_operation(kind=SITE_KIND, identity={"name": "site-a"}, payload={"name": "site-a"})
    adapter.apply_planned_operation(operation=site, peers=peers)
    adapter.apply_planned_operation(operation=device_operation("device-a"), peers=peers)

    assert client.resolver_queries == [], (
        "The peer was created by this same apply, so its identity must resolve from the memo."
    )
    _, query = client.mutations[1]
    assert rendered_related_id(query, "site") == NODE_ID


def test_a_failed_lookup_is_not_memoized_and_the_next_reference_reattempts() -> None:
    """AD036: a negative result is never cached.

    A peer absent when the first referring operation ran may have been created by an
    operation in between, so inheriting the failure would refuse a plan that is applicable.
    """
    client = RecordingClient()
    client.filter_results = [[], [make_node(client, SITE_KIND, "site-id-9")]]
    adapter = make_adapter(client)
    peers = PeerResolver(adapter)

    with pytest.raises(PeerNotFoundError):
        adapter.apply_planned_operation(operation=device_operation("device-a"), peers=peers)
    adapter.apply_planned_operation(operation=device_operation("device-b"), peers=peers)

    assert len(client.resolver_queries) == 2, (
        "The failed lookup must not be cached, so the second reference queries the destination again."
    )
    _, query = client.mutations[0]
    assert rendered_related_id(query, "site") == "site-id-9"


def test_a_failed_write_is_not_memoized_and_a_later_reference_queries_the_destination() -> None:
    """AD036: only a **completed** write is remembered.

    Memoizing an operation whose write was rejected would hand later operations a node id
    for an object that does not exist.
    """
    client = RecordingClient()
    client.write_error = RuntimeError("the destination rejected the write")
    adapter = make_adapter(client)
    peers = PeerResolver(adapter)

    site = make_operation(kind=SITE_KIND, identity={"name": "site-a"}, payload={"name": "site-a"})
    with pytest.raises(RuntimeError):
        adapter.apply_planned_operation(operation=site, peers=peers)

    client.write_error = None
    client.filter_results = [[make_node(client, SITE_KIND, "site-id-7")]]
    adapter.apply_planned_operation(operation=device_operation("device-a"), peers=peers)

    assert len(client.resolver_queries) == 1, (
        "The failed write must leave no memo entry, so the reference falls through to the destination."
    )
    _, query = client.mutations[-1]
    assert rendered_related_id(query, "site") == "site-id-7"


def test_the_resolver_never_reads_the_client_store() -> None:
    """FR-014: the resolver has no store dependency.

    `resolve_peer_node` on the live `sync` path resolves peers out of `client.store`, which
    a destination load populates. A saved plan is applied without loading either side, so a
    resolver that fell back to the store would read an empty one and refuse every peer.
    """
    client = RecordingClient()
    client.filter_results = [[make_node(client, SITE_KIND, "site-id-3")]]
    adapter = make_adapter(client)
    resolver = PeerResolver(adapter)

    with patch.object(client, "store", PoisonedStore()):
        node_id = resolver.resolve(peer_kind=SITE_KIND, identity={"name": "site-a"}, referring_operation_id="op_0000")

    assert node_id == "site-id-3"
    assert len(client.resolver_queries) == 1


def test_an_unknown_peer_kind_is_refused_loudly_before_any_destination_query() -> None:
    """MIN-012: a peer kind the destination schema does not declare raises, like the operation path.

    `self._adapter.schema` is a plain mapping, so `.get()` on an unknown kind returns `None` —
    and `getattr(None, "human_friendly_id", ...)` reads as "no human-friendly ID", silently
    degrading the resolver to the scalar fallback and querying a kind that does not exist.
    The operation path raises before writing an unknown kind; resolving a peer against one is
    the same condition and must be as loud.

    It states the diagnosis and **no remedy** (RF-2). `ValueError` is deliberately outside
    `OPERATIONAL_APPLY_FAILURES`, so FIX-011 routes it to the CLI's defect arm, which already
    tells the operator not to re-plan on the assumption the destination is at fault; a
    "re-plan" instruction here reached them in the same ERROR line as its own contradiction.
    The message ends without a full stop because that arm's format string supplies one.
    """
    client = RecordingClient()
    client.filter_results = [[make_node(client, SITE_KIND, "site-id-1")]]
    adapter = make_adapter(client)
    resolver = PeerResolver(adapter)

    with pytest.raises(ValueError, match="declares no kind 'TestNowhere'") as excinfo:
        resolver.resolve(peer_kind="TestNowhere", identity={"name": "site-a"}, referring_operation_id="op_0000")

    message = str(excinfo.value)
    assert "re-plan" not in message.lower(), (
        "The defect arm tells the operator NOT to re-plan; the message must not tell them to."
    )
    assert not message.endswith("."), "The defect arm's format string supplies the full stop."
    assert client.resolver_queries == [], "No destination query may be issued for a kind the schema lacks."


def test_a_reference_only_identity_is_refused_before_querying_not_silently_bound() -> None:
    """FIX-002: an empty filter set refuses, even when exactly one node of the kind exists.

    A peer identity every value of which is reference-shaped derives no filter kwargs at all,
    so `client.filters(kind=...)` would list **every** node of the kind — and with exactly one
    at the destination, return it. That is the one shape the zero- and multi-match refusals
    cannot catch: the wrong peer is bound, memoized (AD036) and reused for the rest of the
    apply, silently. The destination here is seeded with exactly that single-node state, so an
    implementation that still queries binds it and fails this test.
    """
    client = RecordingClient()
    client.filter_results = [[make_node(client, SITE_KIND, "the-only-site")]]
    adapter = make_adapter(client)
    resolver = PeerResolver(adapter)

    identity = {"parent": {"peer_kind": SITE_KIND, "identity": {"name": "site-a"}}}
    with pytest.raises(PeerNotFoundError) as excinfo:
        resolver.resolve(peer_kind=SITE_KIND, identity=identity, referring_operation_id="op_0001")

    message = str(excinfo.value)
    assert "No usable destination filter" in message, "The refusal must state that no filter could be derived."
    assert SITE_KIND in message, "The refusal must name the peer kind."
    assert "op_0001" in message, "The refusal must name the referring operation."
    assert client.resolver_queries == [], (
        "The refusal must come BEFORE the query: an unfiltered query lists every node of the kind "
        "and, with exactly one at the destination, silently binds it."
    )
    # RF-3: the next action is this condition's own, not `PeerNotFoundError`'s class-level one.
    # "Create the peer at the destination" is actively dangerous here — nothing established the
    # peer is absent, only that no filter could be derived to look for it, so the peer very
    # likely exists and creating it would duplicate it. The remedy is the identity (AD082).
    next_action = excinfo.value.next_action
    assert next_action != PeerNotFoundError.next_action, (
        f"The class-level remedy tells the operator to create the peer, which would duplicate it: {message}"
    )
    assert "do not create" in next_action.lower(), f"The next action must warn against creating the peer: {message}"
    assert "identifiers" in next_action, f"The next action must point at the identity that derived no filter: {message}"


def test_a_partial_filter_warns_once_per_kind_naming_the_dropped_components(
    captured_logs: pytest.LogCaptureFixture,
) -> None:
    """FIX-002: silently skipped HFID components are disclosed at apply time, per kind.

    `TestDevice`'s destination human-friendly ID is `[site__name__value, name__value]`. An
    identity supplying only `name` drops the crossing component and queries on a strict subset
    of the convergence key — FR-024's plan-time degraded mode, which nothing signalled at apply
    time. The warning must name the dropped component; it is deduplicated per kind for the
    resolver's one-apply lifetime, the same rule as the unkeyed-render report (AD078).
    """
    client = RecordingClient()
    client.filter_results = [
        [make_node(client, DEVICE_KIND, "device-id-1")],
        [make_node(client, DEVICE_KIND, "device-id-2")],
    ]
    adapter = make_adapter(client)
    resolver = PeerResolver(adapter)

    first = resolver.resolve(peer_kind=DEVICE_KIND, identity={"name": "device-a"}, referring_operation_id="op_0002")
    second = resolver.resolve(peer_kind=DEVICE_KIND, identity={"name": "device-b"}, referring_operation_id="op_0003")

    assert (first, second) == ("device-id-1", "device-id-2"), "Partial filters warn; they do not refuse (OQ-5)."
    assert len(client.resolver_queries) == 2, "Both resolutions must still query the destination."
    assert all("name__value" in query and "site__name__value" not in query for query in client.resolver_queries)

    warnings = [record for record in captured_logs.records if "PARTIAL filter" in record.getMessage()]
    assert len(warnings) == 1, f"One warning per kind per apply, got {[record.getMessage() for record in warnings]}."
    record = warnings[0]
    assert record.levelno >= logging.WARNING, (
        f"The report is pinned to WARNING because --quiet floors the logger there; it was emitted at "
        f"{record.levelname}."
    )
    message = record.getMessage()
    assert DEVICE_KIND in message, "The warning must name the destination kind."
    assert "site__name__value" in message, "The warning must name the dropped component."


# ---------------------------------------------------------------------------------------
# T053 — SC-016's local half
# ---------------------------------------------------------------------------------------


def test_a_zero_match_peer_refuses_the_operation_and_dispatches_nothing() -> None:
    """SC-016: an unresolvable peer fails the run rather than being silently skipped.

    Writing the object without the relationship would leave the destination holding a
    half-applied object that no later run detects, because the plan records the reference as
    applied.
    """
    client = RecordingClient()
    adapter = make_adapter(client)
    operation = device_operation("device-a")

    with pytest.raises(PeerNotFoundError) as excinfo:
        adapter.apply_planned_operation(operation=operation, peers=PeerResolver(adapter))

    message = str(excinfo.value)
    assert SITE_KIND in message, "The refusal must name the peer kind."
    assert "site-a" in message, "The refusal must name the peer identity."
    assert operation.operation_id in message, "The refusal must name the referring operation."
    assert not client.mutations, "The operation must not be dispatched."


def test_a_multi_match_peer_refuses_naming_the_match_count() -> None:
    """SC-016: an ambiguous peer refuses too, with the count that makes it actionable.

    Picking the first match would bind the relationship to an arbitrary one of several
    destination objects, and nothing downstream would record which.
    """
    client = RecordingClient()
    client.filter_results = [[make_node(client, SITE_KIND, "site-id-1"), make_node(client, SITE_KIND, "site-id-2")]]
    adapter = make_adapter(client)
    operation = device_operation("device-a")

    with pytest.raises(PeerAmbiguousError) as excinfo:
        adapter.apply_planned_operation(operation=operation, peers=PeerResolver(adapter))

    message = str(excinfo.value)
    assert SITE_KIND in message, "The refusal must name the peer kind."
    assert "site-a" in message, "The refusal must name the peer identity."
    assert "2 objects" in message, "The refusal must name the match count."
    assert not client.mutations, "The operation must not be dispatched."


def test_the_live_sync_write_path_still_warns_and_continues_on_an_unresolvable_peer(
    captured_logs: pytest.LogCaptureFixture,
) -> None:
    """AD048: the refusal is scoped to the planned-write resolver and has not leaked out.

    `update_node` is the live `sync` write path's relationship handler. It warns and
    continues on a peer it cannot resolve, and this brief does not authorize changing that:
    turning it into a refusal would make `infrahub-sync sync` start failing runs that
    complete today.
    """
    client = RecordingClient()
    node = InfrahubNodeSync(client=client, schema=DEVICE_SCHEMA, data={"id": "device-1", "name": {"value": "device-a"}})

    returned = update_node(node=node, attrs={"name": "device-a", "site": "a-key-no-store-holds"})

    assert returned is node, "The live path returns the node it was given rather than raising."
    warnings = [record for record in captured_logs.records if "Ignored" in record.getMessage()]
    assert warnings, "The live path warns about the peer it could not resolve."
    assert not client.mutations, "`update_node` itself issues no write; its caller saves."


def test_the_live_sync_write_path_still_drops_an_unresolvable_cardinality_many_peer() -> None:
    """AD048: the cardinality-many arm of the live path is unchanged too.

    A peer absent from the store is dropped from the new set and the run continues. The
    planned-write resolver refuses the same condition; that difference is deliberate and
    scoped to the new path.
    """
    client = RecordingClient()
    client.existing_peers[TEAM_KIND, "members"] = []
    node = InfrahubNodeSync(
        client=client, schema=TEAM_SCHEMA, data={"id": "team-1", "name": {"value": "team-a"}, "members": []}
    )

    update_node(node=node, attrs={"members": ["a-key-no-store-holds"]})

    assert not client.mutations, "Nothing is written and nothing is raised."


# ---------------------------------------------------------------------------------------
# The engine over the write surface — shared scaffolding for T054, T055 and T056
# ---------------------------------------------------------------------------------------

# A run.json written before the apply, so "apply_plan leaves it untouched" is a comparison
# of bytes rather than the weaker "no file was created" (AD069).
SENTINEL_RUN_FILE = '{"status": "running", "mode": "apply", "summary": {}, "finished_at": null}'


class RecordingApplyDestination:
    """A destination implementing the planned-write surface, recording every dispatch.

    A plain object rather than a `MagicMock`: a mock answers every attribute lookup, so it
    satisfies the write-surface protocol's presence check for free and the missing-surface
    case T055 has to be able to fail on cannot be expressed against one.

    Both protocol members are defined, because the pre-write gate is an `isinstance` check
    against the protocol and a destination missing either one is refused (AD086).

    `reject_at` is the 0-based dispatch index the destination rejects, which is how a
    mid-plan rejection is driven without reaching a real destination. The rejection is the
    SDK's own `GraphQLError` rather than a stand-in `RuntimeError`, because the engine's
    operational boundary is defined by the destination library's error base: a bare
    `RuntimeError` is a defect and deliberately escapes unwrapped (FIX-011).
    """

    def __init__(self, *, reject_at: int | None = None) -> None:
        self.dispatched: list[str] = []
        self.reject_at = reject_at

    def new_peer_resolver(self) -> object:  # noqa: PLR6301
        """The per-apply resolver factory; nothing below this double's surface reads it."""
        return object()

    def apply_planned_operation(self, *, operation: PlannedOperation, peers: Any) -> str:  # noqa: ANN401
        _ = peers
        if self.reject_at is not None and len(self.dispatched) == self.reject_at:
            raise GraphQLError([{"message": "the destination rejected this object"}])
        self.dispatched.append(operation.operation_id)
        return f"node-{len(self.dispatched)}"


def apply_run_dir(tmp_path: Path, *, run_id: str = APPLY_RUN_ID) -> Path:
    """A run directory under the sync's cache layout, so review and apply see one artifact."""
    directory = tmp_path / SYNC_NAME / run_id
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def engine_over(run_directory: Path, destination: object, *, run_id: str = APPLY_RUN_ID) -> Potenda:
    """A `Potenda` bound to `run_directory` with no configuration and no source load."""
    return Potenda(
        source=SimpleNamespace(top_level=[]),  # ty: ignore[invalid-argument-type]
        destination=destination,  # ty: ignore[invalid-argument-type]
        config=None,  # ty: ignore[invalid-argument-type]
        top_level=["BuiltinTag"],
        run_dir=run_directory,
        run_id=run_id,
    )


def apply_and_record_state(engine: Potenda) -> tuple[str, ApplyRecord | Exception]:
    """Apply, and return the run state the CLI would record, with what the apply produced.

    The state rule is the CLI's, mirrored rather than described: `infrahub_sync/cli.py:343-349`
    records `applied` when `apply_plan` returns and `failed` when it raises. Reading the state
    through this helper is what makes "the run ends `applied`" an assertion a `failed` run
    fails, rather than an inference from the absence of an exception (AD055).
    """
    try:
        record = engine.apply_plan(config_version=CONFIG_VERSION)
    except Exception as exc:  # noqa: BLE001 — the CLI catches exactly this broadly
        return "failed", exc
    return "applied", record


@pytest.fixture
def engine_logs(caplog: pytest.LogCaptureFixture) -> Iterator[pytest.LogCaptureFixture]:
    """Capture the engine's logger at DEBUG, so an `INFO` emission is captured and fails."""
    with caplog.at_level(logging.DEBUG, logger=ENGINE_LOGGER):
        yield caplog


# ---------------------------------------------------------------------------------------
# T054 — SC-007's local half: a delete-bearing plan, and the class that does still fail
# ---------------------------------------------------------------------------------------


def test_a_delete_bearing_plan_applies_every_non_delete_and_ends_applied(
    tmp_path: Path,
    engine_logs: pytest.LogCaptureFixture,
) -> None:
    """SC-007: a recorded delete is collected, never dispatched, and the run still succeeds.

    **A run state of `failed` fails this test.** Not executing a delete is a designed
    limitation of this release, and reporting a designed limitation as a fault is the defect
    AD055 corrects: an operator whose delete-bearing plan reports `failed` cannot tell it
    from a plan whose creates were rejected, and the creates *were* applied.

    Every value is read off the **returned** record, because `apply_plan` writes no run file
    (AD069). T065 asserts the same three read back from `run.json` after the CLI has merged
    them, which is what proves the merge happens.
    """
    directory = apply_run_dir(tmp_path)
    create = operation_record(identity={"name": "prod"})
    update = operation_record(action="update", identity={"name": "staging"})
    first_delete = operation_record(action="delete", identity={"name": "retired"})
    second_delete = operation_record(action="delete", kind="LocationSite", identity={"name": "closed"})
    records = [create, first_delete, update, second_delete]
    write_artifact(directory, records, run_id=APPLY_RUN_ID, source_snapshot=[])
    run_file = directory / "run.json"
    run_file.write_text(SENTINEL_RUN_FILE, encoding="utf-8")

    destination = RecordingApplyDestination()
    state, outcome = apply_and_record_state(engine_over(directory, destination))

    assert state == "applied", (
        f"A delete-bearing plan is a designed limitation, not a fault: the run must end 'applied', "
        f"got {state!r} from {outcome!r}."
    )
    assert isinstance(outcome, ApplyRecord)
    record = outcome
    assert destination.dispatched == [create["operation_id"], update["operation_id"]], (
        "Every non-delete is applied, in stored order, and neither delete is dispatched."
    )
    assert first_delete["operation_id"] not in destination.dispatched
    assert second_delete["operation_id"] not in destination.dispatched

    assert record.skipped_delete_count == 2, "The count is the plan's delete count."
    assert record.skipped_delete_operations == (first_delete["operation_id"], second_delete["operation_id"]), (
        "Exactly the delete identifiers, in stored order."
    )

    # DBR-016's knowability invariant, as a recorded value rather than an inference: the
    # reviewed set minus the applied set has to be readable, not deduced.
    planned = {str(entry["operation_id"]) for entry in records}
    assert set(record.applied_operations) | set(record.skipped_delete_operations) == planned
    assert len(record.applied_operations) + len(record.skipped_delete_operations) == len(records)

    assert run_file.read_text(encoding="utf-8") == SENTINEL_RUN_FILE, (
        "`apply_plan` is not the run file's writer; the CLI merges the record and saves it (AD069)."
    )

    warnings = [entry for entry in engine_logs.records if "delete" in entry.getMessage()]
    assert len(warnings) == 1, f"One report for the whole apply, got {[w.getMessage() for w in warnings]}."
    assert warnings[0].levelno >= logging.WARNING, (
        f"The report is pinned to WARNING because --quiet floors the package logger there "
        f"(infrahub_sync/cli.py:29); it was emitted at {warnings[0].levelname}."
    )
    assert "2" in warnings[0].getMessage(), "The warning must name the count of deletes it did not execute."


def test_a_mid_apply_rejection_surfaces_the_rejection_not_the_knowability_invariant(tmp_path: Path) -> None:
    """AD062: the invariant is checked on a **completed** apply and nowhere else.

    A partial apply breaks both of its clauses by construction, so an implementation that
    checked it unconditionally would replace a clear destination-rejection message with an
    internal invariant error and send the operator after the wrong cause.
    """
    directory = apply_run_dir(tmp_path)
    records = [operation_record(identity={"name": "prod"}), operation_record(identity={"name": "staging"})]
    write_artifact(directory, records, run_id=APPLY_RUN_ID, source_snapshot=[])

    destination = RecordingApplyDestination(reject_at=1)
    state, outcome = apply_and_record_state(engine_over(directory, destination))

    assert state == "failed"
    assert not isinstance(outcome, ApplyRecordInvariantError), (
        f"A rejection must surface as the rejection, got {outcome!r}."
    )
    assert isinstance(outcome, OperationApplyFailedError)
    assert "the destination rejected this object" in str(outcome)


# ---------------------------------------------------------------------------------------
# T056 — SC-005's apply half: the reviewed set equals the applied record, in order
#
# The engine's own apply-loop matrix — stored order, the surfaceless refusal, the empty plan,
# the partial record on a rejection — lives at `tests/cache/test_apply_plan.py`, against the
# engine rather than through this adapter.
# ---------------------------------------------------------------------------------------


def test_the_reviewed_operation_identifiers_equal_the_applied_record_in_the_same_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SC-005: what a review showed is exactly what an apply then wrote.

    The review-side set comes from `read_saved_plan`'s per-object output — the same call the
    command-line review mode renders — and the apply-side set from the FR-020 record on the
    apply result. The comparison is **ordered**, so an implementation recording the applied
    identifiers as a set or sorting them fails here: an operator reconciling a review against
    a partial apply reads the record positionally, and an unordered record cannot answer
    "where did it stop".

    **The fixture carries no delete**, asserted before comparing. A delete is reviewed but
    never applied, so its identifier lands in `skipped_delete_operations` rather than
    `applied_operations`, and an ordered equality over a delete-bearing plan would fail for a
    reason that has nothing to do with SC-005. T054 is where a delete-bearing plan is
    exercised.
    """
    monkeypatch.setenv("INFRAHUB_SYNC_CACHE_DIR", str(tmp_path))
    directory = apply_run_dir(tmp_path)
    records = [
        operation_record(identity={"name": "zulu"}),
        operation_record(action="update", kind="LocationSite", identity={"name": "dc1"}, tier=1),
        operation_record(identity={"name": "alpha"}),
    ]
    write_artifact(directory, records, run_id=APPLY_RUN_ID, source_snapshot=[])

    reviewed_operations = read_saved_plan(sync_name=SYNC_NAME, run_id=APPLY_RUN_ID).operations()
    assert all(operation.action != "delete" for operation in reviewed_operations), (
        "SC-005's precondition: a delete is reviewed but never applied, so a delete-bearing "
        "fixture would fail this comparison for an unrelated reason (FR-016, FR-017)."
    )
    reviewed = [operation.operation_id for operation in reviewed_operations]
    assert reviewed != sorted(reviewed), (
        "the fixture must not already be in sorted order, or an unordered record would pass"
    )

    destination = RecordingApplyDestination()
    state, outcome = apply_and_record_state(engine_over(directory, destination))

    assert state == "applied"
    assert isinstance(outcome, ApplyRecord)
    assert list(outcome.applied_operations) == reviewed, (
        f"The reviewed set and the applied record must agree per operation and in order: reviewed "
        f"{reviewed}, applied {list(outcome.applied_operations)}."
    )
    assert destination.dispatched == reviewed, "And the destination saw exactly that sequence."
    assert outcome.skipped_delete_operations == ()


# ---------------------------------------------------------------------------------------
# AD086 — the write-surface protocol boundary, and what it does not verify
# ---------------------------------------------------------------------------------------


def _statically_conforms(adapter: InfrahubAdapter) -> PlannedWriteDestination:
    """`ty` checks this return, so the adapter's conformance is a type error when it lapses.

    The engine narrows `self.destination` with `isinstance`, which tells the type checker
    nothing about the Infrahub adapter itself. This function is where the other direction is
    asserted: drop `new_peer_resolver`, or change either member's signature, and
    `uv run ty check .` fails here rather than at some later call site.
    """
    return adapter


class DestinationWithoutThePeerResolverFactory:
    """A destination with the write surface and **no** resolver factory (AD086).

    Both members are the surface, so this destination is not one — and is refused in the same
    pre-write gate as a destination with no write surface at all.
    """

    def __init__(self) -> None:
        self.dispatched: list[str] = []

    def apply_planned_operation(self, *, operation: PlannedOperation, peers: Any) -> str:  # noqa: ANN401
        _ = peers
        self.dispatched.append(operation.operation_id)
        return f"node-{len(self.dispatched)}"


class DuckTypedDestination:
    """Both member **names**, neither member's signature (AD086).

    This is the destination the honesty test below is about: it is not a planned-write
    destination in any sense that would let an apply succeed, and the pre-write gate accepts
    it anyway.
    """

    def new_peer_resolver(self) -> str:  # noqa: PLR6301
        return "not a resolver"

    def apply_planned_operation(self) -> None:  # noqa: PLR6301 — takes neither `operation` nor `peers`
        raise AssertionError


def test_the_infrahub_adapter_is_a_planned_write_destination() -> None:
    """The one adapter of nine that carries the surface satisfies both of its members."""
    adapter = make_adapter(RecordingClient())

    assert isinstance(adapter, PlannedWriteDestination)
    assert _statically_conforms(adapter) is adapter


def test_the_adapters_factory_builds_a_resolver_bound_to_that_adapter() -> None:
    """The factory is what replaced the engine's cast to this class (AD086).

    A fresh resolver per call, each bound to the adapter that built it, so the memo's lifetime
    is one apply and two applies never share one (FR-014).
    """
    adapter = make_adapter(RecordingClient())

    resolver = adapter.new_peer_resolver()

    assert isinstance(resolver, PeerResolver)
    assert resolver._adapter is adapter
    assert adapter.new_peer_resolver() is not resolver


def test_a_destination_missing_only_the_resolver_factory_is_refused_before_any_write(tmp_path: Path) -> None:
    """FR-023/AD086: the surface is both members, and the refusal still names the adapter.

    A destination carrying `apply_planned_operation` alone would have passed the single-method
    `hasattr` gate this protocol replaced, then died where the engine built its resolver —
    after the gate that exists to keep exactly that from happening.
    """
    directory = apply_run_dir(tmp_path)
    write_artifact(directory, [operation_record()], run_id=APPLY_RUN_ID, source_snapshot=[])

    destination = DestinationWithoutThePeerResolverFactory()
    state, outcome = apply_and_record_state(engine_over(directory, destination))

    assert state == "failed"
    assert isinstance(outcome, PlanVerificationError)
    message = str(outcome)
    assert "write_surface" in message
    assert "DestinationWithoutThePeerResolverFactory" in message, (
        "The refusal names the adapter it refused, which is why the verifier receives a name (AD058)."
    )
    assert "infrahub-sync sync" in message
    assert destination.dispatched == [], "Refused before any write, not part-way through one."


def test_the_gate_verifies_member_presence_only_and_is_no_stronger_than_hasattr(tmp_path: Path) -> None:
    """AD086's honesty clause, asserted rather than described.

    `isinstance` against a `runtime_checkable` protocol checks that the members **exist**. It
    does not check their signatures, so a destination whose members have the right names and
    the wrong shapes is accepted by the pre-write gate and fails later, mid-apply — exactly
    where the `hasattr` gate this replaced would have failed. FR-023's refusal is still
    presence-checking, and this test is here so nobody reads the protocol as having hardened
    it. Making the refusal real at runtime needs an explicit opt-in — ABC inheritance or a
    class-level marker — and is a separate design decision that AD086 does not take.

    What the protocol did fix is static, and no runtime assertion can show it: `ty` checks the
    dispatch and the resolver factory at every call site, and the untyped `getattr` dispatch
    and the cast to `InfrahubAdapter` are gone.
    """
    directory = apply_run_dir(tmp_path)
    write_artifact(directory, [operation_record()], run_id=APPLY_RUN_ID, source_snapshot=[])
    duck = DuckTypedDestination()

    assert isinstance(duck, PlannedWriteDestination), (
        "Member presence is all the protocol checks, so this destination passes the gate."
    )
    assert hasattr(duck, "apply_planned_operation"), "And it would have passed the `hasattr` gate too."

    state, outcome = apply_and_record_state(engine_over(directory, duck))

    assert state == "failed", "It fails — but mid-apply, not in the pre-write gate."
    assert not isinstance(outcome, PlanVerificationError), (
        "The failure is not a refusal: the gate accepted this destination. If this ever becomes a "
        "PlanVerificationError, the runtime enforcement AD086 deferred has been implemented, and "
        "AD086's honesty clause needs revisiting rather than this assertion loosening."
    )


# ---------------------------------------------------------------------------------------
# T107 — the transport and auth edges on the two live-calling surfaces
# ---------------------------------------------------------------------------------------
#
# Constitution V asks for adapter edge-case tests covering timeouts and 401/403. The two
# surfaces this outcome adds both issue live destination calls during an apply — the planned
# write itself (`client.create` → `save(allow_upsert=True)` → the targeted flush) and the
# apply-time peer resolver (`client.filters`) — and their *resolution* edges are covered above
# (a zero-match and a multi-match peer each refuse and dispatch nothing), while their
# transport and auth edges were not. plan.md's Principle V row disclosed that as owed; these
# close it.
#
# The property under test is the operator's, so it is asserted where the operator meets it:
# through `Potenda.apply_plan`, which is the only caller of these surfaces in the product. The
# adapter deliberately does not catch these errors — a timeout is not a designed refusal and
# has no adapter-level remedy — so the guarantee is that the engine converts whatever the
# library raised into `OperationApplyFailedError`, which names the failing operation, the run,
# how many operations stay written, and a next action, and which chains the original exception
# so the library traceback is still there for a maintainer. A bare `ServerNotResponsiveError`
# reaching the operator would name none of that.

# The SDK's own two edges, not invented ones: `ServerNotResponsiveError` is what
# `infrahub_sdk.client` raises on a read timeout, and `AuthenticationError` is what it raises
# for both 401 and 403 (`if exc.response.status_code in {401, 403}`).
LIVE_CALL_EDGES = (
    pytest.param(
        ServerNotResponsiveError(url="http://localhost:8000/graphql/main", timeout=10),
        "timeout",
        id="transport-timeout",
    ),
    pytest.param(
        AuthenticationError("Authentication failed: 401 Unauthorized"),
        "401",
        id="auth-401",
    ),
)


def _tag_then_team_plan(directory: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """A two-operation plan: a plain create, then one carrying a relationship reference.

    The order matters for both cases below. The first operation touches neither surface's
    failing edge, so it applies cleanly and gives "the operations applied before it stay
    written" something to be true *of* — an assertion that a single-operation plan could only
    make vacuously. The second carries a `members` reference, which is what routes it through
    the peer resolver before its own write.

    The peer is `tag-b`, deliberately **not** the `tag-a` the first operation creates. A peer
    the same plan created is answered from the resolver's memo without any destination query
    at all, so pointing the reference at `tag-a` would leave the resolver's transport edge
    unreachable and the case below would pass while nothing was exercised.
    """
    first = operation_record(kind=TAG_KIND, identity={"name": "tag-a"}, payload={"name": "tag-a"})
    second = operation_record(
        kind=TEAM_KIND,
        action="update",
        identity={"name": "team-a"},
        payload={"name": "team-a"},
        relationships=[
            {"field": "members", "peer_kind": TAG_KIND, "cardinality": "many", "peers": [{"name": "tag-b"}]}
        ],
    )
    write_artifact(directory, [first, second], run_id=APPLY_RUN_ID, source_snapshot=[])
    return first, second


def _assert_named_and_actionable(
    outcome: ApplyRecord | Exception,
    *,
    injected: Exception,
    failing_operation_id: str,
    applied_before: tuple[str, ...],
) -> OperationApplyFailedError:
    """The operator-facing guarantee, asserted once for all four cases.

    Every clause here is something a bare library exception would not carry. Returns the
    narrowed failure so a caller can make its own further assertions on it.
    """
    assert isinstance(outcome, OperationApplyFailedError), (
        f"The library error must be converted into the named taxonomy failure, not reach the "
        f"operator raw; got {type(outcome).__name__}: {outcome!r}."
    )
    message = str(outcome)
    assert failing_operation_id in message, "The failure must name the operation that failed."
    assert APPLY_RUN_ID in message, "…and the run it failed in, which is what the operator re-plans."
    assert str(injected) in message, (
        f"…and the underlying cause, or the operator cannot tell a timeout from a rejection. Message was: {message!r}"
    )
    assert outcome.next_action, "AD059: every taxonomy failure names the operator's next action."
    assert "Next action:" in message, "And it is carried in the rendered message, not only as an attribute."
    assert outcome.__cause__ is injected, (
        "The original exception must be chained, so the library traceback survives for a maintainer "
        "even though the operator sees the named message."
    )
    assert outcome.apply_record.applied_operations == applied_before, (
        f"Nothing is rolled back, so the record must account for what was written before the failure; "
        f"expected {applied_before}, got {outcome.apply_record.applied_operations}."
    )
    return outcome


@pytest.mark.parametrize(("injected", "expected_fragment"), LIVE_CALL_EDGES)
def test_a_failing_transport_or_auth_on_the_write_is_named_and_actionable(
    tmp_path: Path, injected: Exception, expected_fragment: str
) -> None:
    """The planned-write surface's transport and auth edges (Constitution V, plan.md's Principle V row).

    The write is armed to fail on the *second* mutation, so the first operation's upsert
    succeeds and the failure lands on the second operation's own write rather than before any
    write at all.
    """
    directory = apply_run_dir(tmp_path)
    first, second = _tag_then_team_plan(directory)
    client = RecordingClient()
    client.filter_results = [[make_node(client, TAG_KIND, "tag-1")]]
    client.write_error = injected
    client.write_error_after_mutations = 1

    state, outcome = apply_and_record_state(engine_over(directory, make_adapter(client)))

    assert state == "failed", "A transport or auth failure mid-apply is a genuine failure, not a skip."
    _assert_named_and_actionable(
        outcome,
        injected=injected,
        failing_operation_id=str(second["operation_id"]),
        applied_before=(str(first["operation_id"]),),
    )
    assert expected_fragment in str(outcome).lower() or expected_fragment in str(outcome), (
        f"The message must carry enough of the cause to identify the edge; {expected_fragment!r} is absent."
    )


@pytest.mark.parametrize(("injected", "expected_fragment"), LIVE_CALL_EDGES)
def test_a_failing_transport_or_auth_on_peer_resolution_is_named_and_actionable(
    tmp_path: Path, injected: Exception, expected_fragment: str
) -> None:
    """The apply-time peer resolver's transport and auth edges.

    Distinct from the case above, and not a duplicate of it: the resolver's destination query
    is a **read** issued from a different call site (`client.filters`, not the mutation
    transport), it happens *before* the operation's own write, and its two designed refusals —
    `PeerNotFoundError` and `PeerAmbiguousError` — are the paths a naive implementation would
    route a timeout into. A timeout is neither: nothing is missing and nothing is ambiguous, so
    reporting it as either would send the operator to create a peer that already exists. The
    assertion is therefore that it stays a transport/auth failure and is named as one.
    """
    directory = apply_run_dir(tmp_path)
    first, second = _tag_then_team_plan(directory)
    client = RecordingClient()
    # The first operation carries no relationship, so the first — and only — resolver query is
    # the second operation's. No counter is needed to place the failure.
    client.filter_error = injected

    state, outcome = apply_and_record_state(engine_over(directory, make_adapter(client)))

    assert state == "failed"
    assert client.resolver_queries, "Precondition: the resolver must actually have issued its query."
    failure = _assert_named_and_actionable(
        outcome,
        injected=injected,
        failing_operation_id=str(second["operation_id"]),
        applied_before=(str(first["operation_id"]),),
    )
    assert not isinstance(failure.__cause__, (PeerNotFoundError, PeerAmbiguousError)), (
        "A transport or auth failure must not be reported as a peer resolution refusal: nothing is "
        "absent and nothing is ambiguous, and both of those remedies are wrong for this condition."
    )
    assert expected_fragment in str(outcome).lower() or expected_fragment in str(outcome), (
        f"The message must carry enough of the cause to identify the edge; {expected_fragment!r} is absent."
    )
    assert len(client.mutations) == 1, (
        "The failing operation's own write must not have been attempted: the resolver runs first, so "
        "only the preceding operation's upsert reached the transport."
    )


# ======================================================================================
# The SDK-boundary tripwire for AD088 (folded in from test_infrahub_empty_peer_set_flush)
# ======================================================================================

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


def test_the_sdk_nulls_an_unmapped_optional_relationship_under_both_render_modes() -> None:
    """SDK-boundary tripwire for AD088: why no re-render of the node can be the flush.

    Straight at the SDK, no adapter code involved. Rendering a node the SDK considers existing
    emits `owner: None` for the unmapped optional cardinality-one relationship, and it does so
    **under both render modes** — which is what makes the defect AD088 fixes older than AD085
    and independent of it:

    - stripping **on** (`exclude_unmodified=True`, a plain `node.save()`): the field survives
      both stripping loops. The first does not pop it — the pop needs a non-optional
      `RelatedNodeBase` or a `RelationshipManagerBase`, and an uninitialized optional
      cardinality-one relationship is neither. The second never visits it, because an unmapped
      field is absent from the original data the comparison walks.
    - stripping **off** (`exclude_unmodified=False`, `node.update(do_full_update=True)`):
      nothing is stripped at all.

    If either arm stops holding, AD088's ground has moved.
    """
    client = RecordingClient()
    create_data = client.schema.generate_payload_create(schema=TEAM_SCHEMA, data={"name": "team-a", "members": []})
    assert "owner" not in create_data, "Precondition: the plan maps no `owner`, so the payload carries none."

    node = InfrahubNodeSync(client=client, schema=TEAM_SCHEMA, data=create_data)
    node.id = NODE_ID
    node._existing = True

    manager = node.members
    assert isinstance(manager, RelationshipManagerSync)
    manager.add("tag-id-1")
    assert manager.peer_ids == ["tag-id-1"], "Precondition: the manager is reconciled."

    for exclude_unmodified in (True, False):
        rendered = node._generate_input_data(exclude_unmodified=exclude_unmodified)["data"]["data"]
        message = (
            f"{SDK_BOUNDARY_MESSAGE}\n\nWith exclude_unmodified={exclude_unmodified} the render produced "
            f"{rendered!r}, which no longer nulls the unmapped optional cardinality-one relationship."
        )
        assert "owner" in rendered, message
        assert rendered["owner"] is None, message
