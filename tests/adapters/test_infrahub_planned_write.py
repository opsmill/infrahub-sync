"""The Infrahub destination's planned-write surface, offline.

`InfrahubAdapter.apply_planned_operation` is the saved-plan write surface (FR-013) and
`PeerResolver` its apply-time peer resolution (FR-014). Everything runs against a real
`InfrahubClientSync` whose **transport edge alone** is replaced, so the mutations recorded
here are the ones the SDK would actually send. No live Infrahub is contacted.

Recording the rendered mutation rather than a mock call is load-bearing, because three
properties under test are invisible to an assertion made against a `MagicMock`: **keyedness**
is a property of the rendered mutation and not of the assembled `data` (AD054, AD066); the
**replace-set** is only real if it is *issued*, and surplus-peer removal rests on the
destination's replace semantics, which only the live shrink test can pin (AD075); and **one
write per operation** is a claim about the mutations that reach the transport, which only the
recorded mutation names can settle (AD085). That the upsert names no *unmapped* field is
asserted in `tests/plan/test_apply_conformance.py`, against a fixture kind declaring one.
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
from infrahub_sdk.exceptions import AuthenticationError, GraphQLError, NodeNotFoundError, ServerNotResponsiveError
from infrahub_sdk.node import InfrahubNodeSync
from infrahub_sdk.node.attribute import Attribute
from infrahub_sdk.schema import NodeSchemaAPI
from infrahub_sdk.schema.main import (
    AttributeKind,
    AttributeSchemaAPI,
    BranchSchema,
    RelationshipCardinality,
    RelationshipKind,
    RelationshipSchemaAPI,
)

from infrahub_sync.adapters.infrahub import (
    IMPORT_ACCOUNT_QUERY,
    ImportAccount,
    InfrahubAdapter,
    InfrahubModel,
    LineageNotResolvedError,
    NamedLineageNodeNotFoundError,
    PeerResolver,
    ResolvedLineage,
    ResolvedLineageNode,
    clear_lineage_on_pool_edges,
)
from infrahub_sync.configuration.capabilities import InfrahubLineageSettings, NamedLineageNode
from infrahub_sync.plan.derive import operations_from_diff
from infrahub_sync.plan.errors import (
    ApplyRecordInvariantError,
    NullRelationshipValueError,
    OperationApplyFailedError,
    PeerAmbiguousError,
    PeerNotFoundError,
    PlanVerificationError,
    ReviewedPayloadFieldMissingError,
    UnkeyedCreateRefusedError,
)
from infrahub_sync.plan.identity import canonical_identity, operation_id
from infrahub_sync.plan.models import ApplyRecord, PlannedOperation, RelationshipReference
from infrahub_sync.plan.review import read_saved_plan
from infrahub_sync.plan.write_surface import PlannedWriteDestination
from infrahub_sync.potenda import Potenda
from tests.plan.artifact_fixtures import CONFIG_VERSION, SYNC_NAME, operation_record
from tests.plan.artifact_fixtures import write_artifact as write_fixture_artifact
from tests.plan.ownership_fixtures import granted_ownership
from tests.test_potenda_plan_artifact import (
    _FakeAdapter,
    _FakeDiff,
    _FakeElement,
    build_config,
    qualified_source,
    resolver,
)

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

ADAPTER_LOGGER = "infrahub_sync.adapters.infrahub"
ENGINE_LOGGER = "infrahub_sync.potenda"

APPLY_RUN_ID = "20260727T1130-77e0b4c2"

SITE_KIND = "TestSite"
TAG_KIND = "TestTag"
DEVICE_KIND = "TestDevice"
SERVER_KIND = "TestServer"
KEYLESS_KIND = "TestKeyless"
ORPHAN_KIND = "TestOrphan"
TEAM_KIND = "TestTeam"
GROUP_KIND = "TestGroup"

NODE_ID = "written-node-1"
# The account the import account query answers for at the start of an apply.
IMPORT_ACCOUNT_ID = "import-account-1"
# The destination id a fixture update carries unless its case is about the id itself.
DEFAULT_DESTINATION_ID = "18d52a8a-7e7d-9bf5-3967-c51149d169da"
# The lineage a saved plan records: the account the import account query answers for, so an
# apply runs with the account the plan was made with (FR-012).
RECORDED_LINEAGE: dict[str, Any] = {
    "import_account": {"name": "import-account", "id": IMPORT_ACCOUNT_ID},
    "source": {"kind": "CoreAccount", "name": "import-account"},
    "owner": None,
    "protect": False,
}


def write_artifact(run_directory: Path, records: Any = (), **kwargs: Any) -> dict[str, Any]:  # noqa: ANN401
    """Write a saved plan as the fixture builder does, recording `RECORDED_LINEAGE` unless a case sets its own."""
    kwargs.setdefault("lineage", RECORDED_LINEAGE)
    return write_fixture_artifact(run_directory, records, **kwargs)


def _text(attr_id: str, name: str, *, optional: bool = True) -> AttributeSchemaAPI:
    """One text attribute, spelled once rather than at every fixture site."""
    return AttributeSchemaAPI(id=attr_id, name=name, kind=AttributeKind.TEXT, optional=optional, unique=not optional)


def _many(rel_id: str, name: str, peer: str) -> RelationshipSchemaAPI:
    """One optional cardinality-many relationship."""
    return RelationshipSchemaAPI(
        id=rel_id,
        name=name,
        peer=peer,
        cardinality=RelationshipCardinality.MANY,
        kind=RelationshipKind.GENERIC,
        optional=True,
        identifier=f"{name}__{peer}".lower(),
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

# A human-friendly ID that **crosses a relationship**: the client cannot form an `hfid` from a
# peer supplied as a resolved node id, so this kind renders no key on the wire. The server
# matches a create on the components in `data`, so it converges anyway (AD067 closed).
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
            cardinality=RelationshipCardinality.ONE,
            kind=RelationshipKind.ATTRIBUTE,
            optional=False,
            identifier="device__site",
        )
    ],
)

# An all-direct human-friendly ID on a kind that still carries a cardinality-one relationship,
# so peer resolution is exercised on a kind whose render is keyed.
SERVER_SCHEMA = NodeSchemaAPI(
    id="server-schema",
    name="Server",
    namespace="Test",
    label="Server",
    default_filter="name__value",
    human_friendly_id=["name__value"],
    attributes=[_text("server-name", "name", optional=False)],
    relationships=[
        RelationshipSchemaAPI(
            id="server-site",
            name="site",
            peer=SITE_KIND,
            cardinality=RelationshipCardinality.ONE,
            kind=RelationshipKind.ATTRIBUTE,
            optional=False,
            identifier="server__site",
        )
    ],
)

# No human-friendly ID at all, so no convergence key can be rendered for it and the keyedness
# gate refuses its planned write.
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
# the shape the SDK's whole-node render nulls, so it is what makes "the upsert names no unmapped
# destination field" decidable. Do not remove it.
TEAM_SCHEMA = NodeSchemaAPI(
    id="team-schema",
    name="Team",
    namespace="Test",
    label="Team",
    default_filter="name__value",
    human_friendly_id=["name__value"],
    attributes=[_text("team-name", "name", optional=False), _text("team-description", "description")],
    relationships=[
        _many("team-members", "members", TAG_KIND),
        RelationshipSchemaAPI(
            id="team-owner",
            name="owner",
            peer=TAG_KIND,
            cardinality=RelationshipCardinality.ONE,
            kind=RelationshipKind.ATTRIBUTE,
            optional=True,
            identifier="team__owner",
        ),
    ],
)

# Two cardinality-many relationships, so "one write per operation, not one per
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
    SERVER_KIND: SERVER_SCHEMA,
    KEYLESS_KIND: KEYLESS_SCHEMA,
    ORPHAN_KIND: ORPHAN_SCHEMA,
    TEAM_KIND: TEAM_SCHEMA,
    GROUP_KIND: GROUP_SCHEMA,
}


class RecordingClient(InfrahubClientSync):
    """A real client whose destination calls are recorded on one ordered event log.

    One log rather than three lists: that the operation makes exactly one write, and the
    **absence** of any destination read on the planned-write path, are both read off the same
    log, so neither can be satisfied by an unrelated call.
    """

    def __init__(self) -> None:
        super().__init__(config=Config(address="http://localhost:8000", api_token="token"))  # noqa: S106
        self.live_schema = BranchSchema(hash="fixture", nodes=dict(SCHEMAS))
        self.schema_responses: list[BranchSchema] = []
        self.schema_fetches = 0
        self.schema.set_cache(self.live_schema)
        self.schema._fetch = self._fetch_schema  # ty: ignore[invalid-assignment]
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

    def _fetch_schema(self, branch: str, namespaces: list[str] | None = None) -> BranchSchema:
        """Return the current test schema when apply refreshes the SDK cache."""
        _ = branch, namespaces
        self.schema_fetches += 1
        return self.schema_responses.pop(0) if self.schema_responses else self.live_schema

    # -- the transport edge ------------------------------------------------------------

    def execute_graphql(self, *args: Any, **kwargs: Any) -> dict[str, Any]:  # noqa: ANN401, ARG002
        """Record the rendered mutation and answer as a successful write would.

        The import account query that starts an apply is answered with the import account, and
        the version query of a plan with the pinned Infrahub version.
        """
        query = kwargs["query"]
        if query == IMPORT_ACCOUNT_QUERY:
            self.events.append(("account_profile", None))
            return {"AccountProfile": {"id": IMPORT_ACCOUNT_ID, "name": {"value": "import-account"}}}
        if "InfrahubInfo" in query:
            # The server version a plan reads after resolving its lineage.
            self.events.append(("version", None))
            return {"InfrahubInfo": {"version": "1.11.9"}}
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

        The planned-write path issues **no** such read, so the
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
        # The SDK selects every cardinality-one relationship that is not excluded, so a seeded
        # cardinality-one peer is answered without being named in `include`.
        excluded = set(kwargs.get("exclude") or ())
        for rel in schema.relationships:
            seeded = self.existing_peers.get((kind, rel.name))
            if rel.cardinality == RelationshipCardinality.ONE and rel.name not in excluded and seeded:
                data[rel.name] = {"id": seeded[0], "__typename": rel.peer}
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


def make_adapter(
    client: RecordingClient, *, source: str = IMPORT_ACCOUNT_ID, owner: str | None = None, protect: bool = False
) -> InfrahubAdapter:
    """The adapter with only the state the planned-write surface reads, and no network setup.

    The lineage is resolved as an apply resolves it before its first write: `source` is the
    import account and the source of every value, with the given owner and protection.
    """
    adapter = InfrahubAdapter.__new__(InfrahubAdapter)
    adapter.client = client
    adapter.lineage = resolved_lineage(source=source, owner=owner, protect=protect)
    adapter.schema = dict(SCHEMAS)
    return adapter


def resolved_lineage(
    *, source: str = IMPORT_ACCOUNT_ID, owner: str | None = None, protect: bool = False
) -> ResolvedLineage:
    """A lineage as `resolve_lineage` returns it: the import account `source` as source."""
    return ResolvedLineage(
        import_account=ImportAccount(id=source, name="source-account"),
        source=ResolvedLineageNode(kind="CoreAccount", name="source-account", id=source),
        owner=None if owner is None else ResolvedLineageNode(kind="CoreAccountGroup", name="owners", id=owner),
        protect=protect,
    )


def make_operation(
    *,
    kind: str,
    identity: dict[str, Any],
    payload: dict[str, Any],
    action: str = "create",
    relationships: list[RelationshipReference] | None = None,
) -> PlannedOperation:
    """One planned operation, with its identifier derived the way the artifact derives it.

    An update carries `DEFAULT_DESTINATION_ID`: plan format 3 requires one, in process exactly
    as on disk, so a case whose subject is something else does not have to restate it.
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
        destination_id=DEFAULT_DESTINATION_ID if action == "update" else None,
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


def server_operation(name: str, site_name: str = "site-a") -> PlannedOperation:
    """A `TestServer` create: a keyed render that still resolves a cardinality-one peer."""
    return make_operation(
        kind=SERVER_KIND,
        identity={"name": name},
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

    The planned-write path must issue none: the upsert carries the plan's peer set directly,
    and surplus-peer removal is the destination Upsert mutation's replace semantics, pinned
    live — not a fetch-and-reconcile round trip.
    """
    return [payload for name, payload in client.events if name == "get"]


@pytest.fixture(autouse=True)
def _forbid_live_sync_update() -> Iterator[None]:
    """Saved-plan apply must not dispatch through the inherited model update."""

    def forbidden(*_args: object, **_kwargs: object) -> None:
        """Fail if saved-plan apply reaches the model's in-memory update method."""
        msg = "InfrahubModel.update was reached during saved-plan apply."
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
# Payload cases
# ---------------------------------------------------------------------------------------


def test_the_write_is_client_create_then_an_upsert_of_payload_plus_resolved_peer_ids() -> None:
    """FR-013: `data` is the payload plus resolved peer ids, issued as a convergent upsert."""
    client = RecordingClient()
    adapter = make_adapter(client)
    peers = PeerResolver(adapter)
    peers.remember(SITE_KIND, {"name": "site-a"}, "site-id-1")

    node_id = adapter.apply_planned_operation(operation=server_operation("server-a"), peers=peers)

    assert node_id == NODE_ID
    assert client.mutation_names == [f"{SERVER_KIND}Upsert"], (
        "A create with no cardinality-many relationship is exactly one convergent upsert."
    )
    _, query = client.mutations[0]
    assert 'value: "server-a"' in query, f"The payload's attributes must reach the write. Rendered:\n{query}"
    assert rendered_related_id(query, "site") == "site-id-1", (
        f"The relationship must reach the write as the resolved destination node id. Rendered:\n{query}"
    )


def test_a_write_before_the_lineage_is_resolved_is_refused_with_no_request() -> None:
    """Without a resolved lineage the write would carry no source, owner or protection, so it is refused."""
    client = RecordingClient()
    adapter = make_adapter(client)
    adapter.lineage = None
    peers = PeerResolver(adapter)
    peers.remember(SITE_KIND, {"name": "site-a"}, "site-id-1")

    with pytest.raises(LineageNotResolvedError, match="No destination write was attempted"):
        adapter.apply_planned_operation(operation=server_operation("server-a"), peers=peers)

    assert client.events == [], f"A refused write must send no request; the client recorded {client.events}."


def test_no_unmapped_destination_field_is_written() -> None:
    """FR-013: the payload is authoritative for the fields it carries and touches no other."""
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


def _site_schema_without_description() -> BranchSchema:
    """A live schema after the reviewed optional direct field was removed."""
    site = SITE_SCHEMA.model_copy(update={"attributes": [SITE_SCHEMA.attributes[0], SITE_SCHEMA.attributes[2]]})
    return BranchSchema(hash="without-description", nodes={**SCHEMAS, SITE_KIND: site})


def _site_schema_with_relationship_instead_of_description() -> BranchSchema:
    """A live schema that reused a reviewed scalar name for a relationship."""
    site = SITE_SCHEMA.model_copy(
        update={
            "attributes": [SITE_SCHEMA.attributes[0], SITE_SCHEMA.attributes[2]],
            "relationships": [
                RelationshipSchemaAPI(
                    id="site-description-peer",
                    name="description",
                    peer=TAG_KIND,
                    cardinality=RelationshipCardinality.ONE,
                    kind=RelationshipKind.ATTRIBUTE,
                    optional=True,
                    identifier="site__description",
                )
            ],
        }
    )
    return BranchSchema(hash="description-as-relationship", nodes={**SCHEMAS, SITE_KIND: site})


def _site_schema_with_read_only_description() -> BranchSchema:
    """A live schema where the reviewed scalar can no longer be written."""
    description = SITE_SCHEMA.attributes[1].model_copy(update={"read_only": True})
    site = SITE_SCHEMA.model_copy(
        update={"attributes": [SITE_SCHEMA.attributes[0], description, SITE_SCHEMA.attributes[2]]}
    )
    return BranchSchema(hash="read-only-description", nodes={**SCHEMAS, SITE_KIND: site})


def test_direct_apply_refuses_the_entire_plan_before_any_write_when_a_reviewed_field_is_removed(
    tmp_path: Path,
) -> None:
    """A later operation's missing field must stop the first operation too."""
    directory = apply_run_dir(tmp_path)
    first = operation_record(kind=SITE_KIND, identity={"name": "site-a"})
    second = operation_record(
        kind=SITE_KIND,
        identity={"name": "site-b"},
        payload={"name": "site-b", "description": "reviewed"},
    )
    write_artifact(directory, [first, second], run_id=APPLY_RUN_ID, source_snapshot=[])
    client = RecordingClient()
    client.live_schema = _site_schema_without_description()

    state, outcome = apply_and_record_state(engine_over(directory, make_adapter(client)))

    assert state == "failed"
    assert isinstance(outcome, ReviewedPayloadFieldMissingError)
    assert SITE_KIND in str(outcome)
    assert "description" in str(outcome)
    assert "no destination write was attempted" in str(outcome)
    assert "Create a new plan" in str(outcome)
    assert client.mutations == []


@pytest.mark.parametrize(
    "drifted_schema",
    [_site_schema_with_relationship_instead_of_description(), _site_schema_with_read_only_description()],
    ids=["scalar-became-relationship", "scalar-became-read-only"],
)
def test_direct_apply_refuses_unwritable_reviewed_field_before_any_write(
    tmp_path: Path, drifted_schema: BranchSchema
) -> None:
    """A later field that is no longer a writable scalar stops the whole plan."""
    directory = apply_run_dir(tmp_path)
    first = operation_record(kind=SITE_KIND, identity={"name": "site-a"})
    second = operation_record(
        kind=SITE_KIND,
        identity={"name": "site-b"},
        payload={"name": "site-b", "description": "reviewed"},
    )
    write_artifact(directory, [first, second], run_id=APPLY_RUN_ID, source_snapshot=[])
    client = RecordingClient()
    client.live_schema = drifted_schema

    state, outcome = apply_and_record_state(engine_over(directory, make_adapter(client)))

    assert state == "failed"
    assert isinstance(outcome, ReviewedPayloadFieldMissingError)
    assert SITE_KIND in str(outcome)
    assert "description" in str(outcome)
    assert "Create a new plan" in str(outcome)
    assert client.mutations == []


def test_null_scalar_changed_to_relationship_refuses_whole_plan_and_operation(tmp_path: Path) -> None:
    """A reviewed null remains a direct field even if its name becomes a relationship."""
    directory = apply_run_dir(tmp_path)
    first = operation_record(kind=SITE_KIND, identity={"name": "site-a"})
    second = operation_record(
        kind=SITE_KIND,
        identity={"name": "site-b"},
        payload={"name": "site-b", "description": None},
    )
    write_artifact(directory, [first, second], run_id=APPLY_RUN_ID, source_snapshot=[])
    client = RecordingClient()
    client.live_schema = _site_schema_with_relationship_instead_of_description()
    adapter = make_adapter(client)

    state, outcome = apply_and_record_state(engine_over(directory, adapter))

    assert state == "failed"
    assert isinstance(outcome, ReviewedPayloadFieldMissingError)
    assert "description" in str(outcome)
    assert client.mutations == []

    operation = make_operation(
        kind=SITE_KIND,
        identity={"name": "site-b"},
        payload={"name": "site-b", "description": None},
    )
    with pytest.raises(ReviewedPayloadFieldMissingError, match="description"):
        adapter.apply_planned_operation(operation=operation, peers=PeerResolver(adapter))
    assert client.mutations == []


def test_preflight_schema_transport_failure_names_the_run_without_a_write(tmp_path: Path) -> None:
    """A failed schema refresh reports the run and leaves the destination untouched."""
    directory = apply_run_dir(tmp_path)
    operation = operation_record(kind=SITE_KIND, identity={"name": "site-a"})
    write_artifact(directory, [operation], run_id=APPLY_RUN_ID, source_snapshot=[])
    client = RecordingClient()
    failure = ServerNotResponsiveError(url="http://localhost:8000/graphql/main", timeout=10)

    with patch.object(client.schema, "_fetch", side_effect=failure):
        state, outcome = apply_and_record_state(engine_over(directory, make_adapter(client)))

    assert state == "failed"
    assert isinstance(outcome, PlanVerificationError)
    assert APPLY_RUN_ID in str(outcome)
    assert "destination timeout" in str(outcome)
    assert "Nothing was written" in str(outcome)
    assert outcome.__cause__ is failure
    assert client.mutations == []


def test_operation_boundary_schema_drift_keeps_an_accurate_partial_record(tmp_path: Path) -> None:
    """A cached schema change after preflight stops the affected operation."""
    directory = apply_run_dir(tmp_path)
    first = operation_record(kind=SITE_KIND, identity={"name": "site-a"})
    second = operation_record(
        kind=SITE_KIND,
        identity={"name": "site-b"},
        payload={"name": "site-b", "description": "reviewed"},
    )
    write_artifact(directory, [first, second], run_id=APPLY_RUN_ID, source_snapshot=[])
    client = RecordingClient()
    execute = client.execute_graphql

    def change_cached_schema_after_first_write(*args: Any, **kwargs: Any) -> dict[str, Any]:  # noqa: ANN401
        response = execute(*args, **kwargs)
        client.schema.set_cache(_site_schema_without_description())
        return response

    with patch.object(client, "execute_graphql", change_cached_schema_after_first_write):
        state, outcome = apply_and_record_state(engine_over(directory, make_adapter(client)))

    assert state == "failed"
    assert isinstance(outcome, OperationApplyFailedError)
    assert isinstance(outcome.__cause__, ReviewedPayloadFieldMissingError)
    assert SITE_KIND in str(outcome)
    assert "description" in str(outcome)
    assert "Create a new plan" in str(outcome)
    assert outcome.apply_record.applied_operations == (first["operation_id"],)
    assert outcome.apply_record.failed_operation == second["operation_id"]
    assert outcome.apply_record.failed_operation_wrote is False
    assert len(client.mutations) == 1
    assert client.schema_fetches == 1


def test_operation_refuses_a_kind_removed_from_the_cached_schema() -> None:
    """A removed kind reports its reviewed fields before any mutation."""
    client = RecordingClient()
    adapter = make_adapter(client)
    client.schema.set_cache(
        BranchSchema(hash="without-site", nodes={k: v for k, v in SCHEMAS.items() if k != SITE_KIND})
    )
    operation = make_operation(kind=SITE_KIND, identity={"name": "site-a"}, payload={"name": "site-a"})

    with pytest.raises(ReviewedPayloadFieldMissingError) as caught:
        adapter.apply_planned_operation(operation=operation, peers=PeerResolver(adapter))

    assert SITE_KIND in str(caught.value)
    assert "name" in str(caught.value)
    assert "No write was made" in str(caught.value)
    assert client.mutations == []


def test_sdk_payload_omission_refuses_the_operation_before_the_mutation() -> None:
    """A renderer regression cannot silently discard a reviewed scalar field."""
    client = RecordingClient()
    adapter = make_adapter(client)
    operation = make_operation(
        kind=SITE_KIND,
        identity={"name": "site-a"},
        payload={"name": "site-a", "description": "reviewed"},
    )
    render = client.schema.generate_payload_create

    def omit_description(**kwargs: Any) -> dict[str, Any]:  # noqa: ANN401
        payload = render(**kwargs)
        payload.pop("description")
        return payload

    with (
        patch.object(client.schema, "generate_payload_create", omit_description),
        pytest.raises(ReviewedPayloadFieldMissingError) as caught,
    ):
        adapter.apply_planned_operation(operation=operation, peers=PeerResolver(adapter))

    assert SITE_KIND in str(caught.value)
    assert "description" in str(caught.value)
    assert "No write was made" in str(caught.value)
    assert client.mutations == []


def test_sdk_node_input_omission_refuses_the_operation_before_the_mutation() -> None:
    """A field present in the SDK payload must also reach the node mutation input."""
    client = RecordingClient()
    adapter = make_adapter(client)
    operation = make_operation(
        kind=SITE_KIND,
        identity={"name": "site-a"},
        payload={"name": "site-a", "description": "reviewed"},
    )
    drifted_site = _site_schema_without_description().nodes[SITE_KIND]

    def create_with_drifted_node(*, kind: str, data: dict[str, Any]) -> InfrahubNodeSync:
        assert kind == SITE_KIND
        return InfrahubNodeSync(client=client, schema=drifted_site, data=data)

    with (
        patch.object(client, "create", side_effect=create_with_drifted_node),
        pytest.raises(ReviewedPayloadFieldMissingError) as caught,
    ):
        adapter.apply_planned_operation(operation=operation, peers=PeerResolver(adapter))

    assert SITE_KIND in str(caught.value)
    assert "description" in str(caught.value)
    assert "SDK mutation" in str(caught.value)
    assert client.mutations == []


@pytest.mark.parametrize("action", ["create", "update"])
def test_null_direct_scalar_is_written_and_read_back(action: str) -> None:
    """A reviewed null is sent explicitly and remains null in the SDK's saved node."""
    client = RecordingClient()
    adapter = make_adapter(client)
    operation = make_operation(
        kind=SITE_KIND,
        action=action,
        identity={"name": "site-a"},
        payload={"name": "site-a", "description": None},
    )

    node_id = adapter.apply_planned_operation(operation=operation, peers=PeerResolver(adapter))

    assert len(client.mutations) == 1
    assert re.search(r"description:\s*\{\s*value:\s*null", client.mutations[0][1])
    saved_node = client.store.get(key=node_id, kind=SITE_KIND)
    assert isinstance(saved_node, InfrahubNodeSync)
    assert isinstance(saved_node.description, Attribute)
    assert saved_node.description.value is None


def test_null_direct_scalar_on_later_operation_does_not_fail_apply(tmp_path: Path) -> None:
    """A null scalar in a later operation cannot leave a partial apply record."""
    directory = apply_run_dir(tmp_path)
    first = operation_record(kind=SITE_KIND, identity={"name": "site-a"})
    second = operation_record(
        kind=SITE_KIND,
        identity={"name": "site-b"},
        payload={"name": "site-b", "description": None},
    )
    write_artifact(directory, [first, second], run_id=APPLY_RUN_ID, source_snapshot=[])
    client = RecordingClient()

    state, outcome = apply_and_record_state(engine_over(directory, make_adapter(client)))

    assert state == "applied"
    assert isinstance(outcome, ApplyRecord)
    assert outcome.applied_operations == (first["operation_id"], second["operation_id"])
    assert len(client.mutations) == 2
    assert re.search(r"description:\s*\{\s*value:\s*null", client.mutations[1][1])


def test_sdk_omission_in_later_operation_refuses_plan_before_any_write(tmp_path: Path) -> None:
    """Preflight renders all reviewed direct values before dispatching the first write."""
    directory = apply_run_dir(tmp_path)
    first = operation_record(kind=SITE_KIND, identity={"name": "site-a"})
    second = operation_record(
        kind=SITE_KIND,
        identity={"name": "site-b"},
        payload={"name": "site-b", "description": "reviewed"},
    )
    write_artifact(directory, [first, second], run_id=APPLY_RUN_ID, source_snapshot=[])
    client = RecordingClient()
    render = client.schema.generate_payload_create

    def omit_description(**kwargs: Any) -> dict[str, Any]:  # noqa: ANN401
        payload = render(**kwargs)
        payload.pop("description", None)
        return payload

    with patch.object(client.schema, "generate_payload_create", omit_description):
        state, outcome = apply_and_record_state(engine_over(directory, make_adapter(client)))

    assert state == "failed"
    assert isinstance(outcome, ReviewedPayloadFieldMissingError)
    assert SITE_KIND in str(outcome)
    assert "description" in str(outcome)
    assert client.mutations == []


def test_direct_apply_writes_every_reviewed_field_without_drift(tmp_path: Path) -> None:
    """The checked no-drift path still writes the optional field and records success."""
    directory = apply_run_dir(tmp_path)
    operation = operation_record(
        kind=SITE_KIND,
        identity={"name": "site-a"},
        payload={"name": "site-a", "description": "reviewed"},
    )
    write_artifact(directory, [operation], run_id=APPLY_RUN_ID, source_snapshot=[])
    client = RecordingClient()

    state, outcome = apply_and_record_state(engine_over(directory, make_adapter(client)))

    assert state == "applied"
    assert isinstance(outcome, ApplyRecord)
    assert outcome.applied_operations == (operation["operation_id"],)
    assert len(client.mutations) == 1
    assert re.search(r'description:\s*\{\s*value:\s*"reviewed"', client.mutations[0][1])
    assert client.schema_fetches == 1


def test_apply_resolves_lineage_before_the_first_write_and_writes_with_it(tmp_path: Path) -> None:
    """An apply reads the import account before its first write, and the write carries it as source."""
    directory = apply_run_dir(tmp_path)
    operation = operation_record(kind=SITE_KIND, identity={"name": "site-a"}, payload={"name": "site-a"})
    write_artifact(directory, [operation], run_id=APPLY_RUN_ID, source_snapshot=[])
    client = RecordingClient()
    adapter = make_adapter(client)

    state, _ = apply_and_record_state(engine_over(directory, adapter))

    assert state == "applied"
    names = [name for name, _ in client.events]
    assert names.count("account_profile") == 1
    assert names.index("account_profile") < names.index("mutation")
    assert adapter.lineage is not None
    assert adapter.lineage.source.id == IMPORT_ACCOUNT_ID
    assert f'source: "{IMPORT_ACCOUNT_ID}"' in client.mutations[0][1]


def test_an_apply_of_several_operations_reads_the_import_account_once(tmp_path: Path) -> None:
    """FR-001: one import account query per apply run, however many operations it writes."""
    directory = apply_run_dir(tmp_path)
    records = [
        operation_record(kind=SITE_KIND, identity={"name": name}, payload={"name": name})
        for name in ("site-a", "site-b", "site-c")
    ]
    write_artifact(directory, records, run_id=APPLY_RUN_ID, source_snapshot=[])
    client = RecordingClient()

    state, _ = apply_and_record_state(engine_over(directory, make_adapter(client)))

    assert state == "applied"
    names = [name for name, _ in client.events]
    assert names.count("mutation") == 3
    assert names.count("account_profile") == 1


def test_apply_resolves_lineage_again_rather_than_reusing_what_was_set(tmp_path: Path) -> None:
    """Lineage already held by the adapter is replaced by the apply's own resolution."""
    directory = apply_run_dir(tmp_path)
    operation = operation_record(kind=SITE_KIND, identity={"name": "site-a"}, payload={"name": "site-a"})
    write_artifact(directory, [operation], run_id=APPLY_RUN_ID, source_snapshot=[])
    client = RecordingClient()
    adapter = make_adapter(client, source="stale-account")

    state, _ = apply_and_record_state(engine_over(directory, adapter))

    assert state == "applied"
    assert adapter.lineage is not None
    assert adapter.lineage.source.id == IMPORT_ACCOUNT_ID
    assert "stale-account" not in client.mutations[0][1]


def test_a_named_lineage_node_that_does_not_exist_stops_the_apply_before_any_write(tmp_path: Path) -> None:
    """A named owner the destination does not have stops the apply with nothing written, and no fallback."""
    directory = apply_run_dir(tmp_path)
    operation = operation_record(kind=SITE_KIND, identity={"name": "site-a"}, payload={"name": "site-a"})
    write_artifact(directory, [operation], run_id=APPLY_RUN_ID, source_snapshot=[])
    client = RecordingClient()
    group = NodeSchemaAPI(id="group-schema", name="AccountGroup", namespace="Core", attributes=[], relationships=[])
    client.live_schema = BranchSchema(hash="fixture", nodes={**SCHEMAS, "CoreAccountGroup": group})
    client.schema.set_cache(client.live_schema)
    adapter = make_adapter(client)
    adapter.lineage_settings = InfrahubLineageSettings(owner=NamedLineageNode(kind="CoreAccountGroup", name="missing"))

    def _no_such_node(**kwargs: Any) -> Any:  # noqa: ANN401
        client.events.append(("get", kwargs))
        raise NodeNotFoundError(identifier={"name__value": ["missing"]}, node_type="CoreAccountGroup")

    client.get = _no_such_node  # ty: ignore[invalid-assignment]

    with pytest.raises(NamedLineageNodeNotFoundError):
        engine_over(directory, adapter).apply_plan(ownership=granted_ownership(), config_version=CONFIG_VERSION)

    assert client.mutations == []
    assert [payload for name, payload in client.events if name == "get"] == [
        {"kind": "CoreAccountGroup", "name__value": "missing", "include": ["name"], "exclude": []}
    ]


@pytest.mark.parametrize("action", ["create", "update"])
def test_ambiguous_null_optional_relationship_is_refused(action: str) -> None:
    """A payload null cannot be assumed to be a relationship after schema drift."""
    client = RecordingClient()
    adapter = make_adapter(client)
    operation = make_operation(
        kind=TEAM_KIND,
        action=action,
        identity={"name": "team-a"},
        payload={"name": "team-a", "description": None, "owner": None},
    )

    with pytest.raises(ReviewedPayloadFieldMissingError, match="owner"):
        adapter.apply_planned_operation(operation=operation, peers=PeerResolver(adapter))
    assert client.mutations == []


def test_a_null_mandatory_cardinality_one_relationship_is_refused_before_any_write() -> None:
    """A mandatory to-one null never reaches SDK rendering or the destination."""
    client = RecordingClient()
    adapter = make_adapter(client)
    operation = make_operation(
        kind=DEVICE_KIND,
        action="update",
        identity={"name": "device-a"},
        payload={"name": "device-a", "site": None},
    )

    with record_payload_create(client) as calls, pytest.raises(NullRelationshipValueError) as excinfo:
        adapter.apply_planned_operation(operation=operation, peers=PeerResolver(adapter))

    message = str(excinfo.value)
    assert operation.operation_id in message
    assert DEVICE_KIND in message
    assert "site" in message
    assert "mandatory" in message
    assert calls == [], "The refusal must happen before SDK payload generation."
    assert client.mutations == [], "The refusal must happen before any destination mutation."


@pytest.mark.parametrize(
    ("protect", "is_protected"),
    [pytest.param(False, None, id="protect-off"), pytest.param(True, True, id="protect-on")],
)
def test_generate_payload_create_receives_the_source_owner_and_protection_arguments(
    *, protect: bool, is_protected: bool | None
) -> None:
    """The planned create passes the source, the owner and, only when `protect` is on, the protection flag."""
    client = RecordingClient()
    adapter = make_adapter(client, source="source-account-1", owner="owner-account-1", protect=protect)

    operation = make_operation(kind=SITE_KIND, identity={"name": "site-a"}, payload={"name": "site-a"})
    with record_payload_create(client) as calls:
        adapter.apply_planned_operation(operation=operation, peers=PeerResolver(adapter))

    assert len(calls) == 1
    call = calls[0]
    assert call["schema"] is SITE_SCHEMA
    assert call["source"] == "source-account-1"
    assert call["owner"] == "owner-account-1"
    assert call["is_protected"] is is_protected
    assert call["data"] == {"name": "site-a"}, "`data` is the payload plus resolved peer ids, and nothing else."


def _created_with_default_lineage(operation: PlannedOperation) -> str:
    """Apply one create with the import account as source, no owner and no protection; return the mutation."""
    client = RecordingClient()
    adapter = make_adapter(client, source=IMPORT_ACCOUNT_ID)
    peers = PeerResolver(adapter)
    peers.remember(TAG_KIND, {"name": "tag-b"}, "tag-id-2")
    peers.remember(TAG_KIND, {"name": "tag-c"}, "tag-id-3")
    peers.remember(SITE_KIND, {"name": "site-a"}, "site-id-1")

    adapter.apply_planned_operation(operation=operation, peers=peers)

    assert len(client.mutations) == 1
    return client.mutations[0][1]


TEAM_CREATE = make_operation(
    kind=TEAM_KIND,
    identity={"name": "team-a"},
    payload={"name": "team-a", "description": "core"},
    relationships=[
        RelationshipReference(
            field="members", peer_kind=TAG_KIND, cardinality="many", peers=[{"name": "tag-b"}, {"name": "tag-c"}]
        )
    ],
)


@pytest.mark.parametrize(
    ("operation", "attributes", "edges"),
    [
        pytest.param(TEAM_CREATE, 2, 2, id="attributes-and-many-edges"),
        pytest.param(server_operation("server-a"), 1, 1, id="attribute-and-one-edge"),
    ],
)
def test_a_create_sends_the_source_on_every_attribute_and_edge_and_no_owner_or_protection(
    operation: PlannedOperation, attributes: int, edges: int
) -> None:
    """FR-001, FR-006: with no owner and no `protect`, a create sends the source and nothing else."""
    rendered = _created_with_default_lineage(operation)

    attribute_sources = re.findall(rf'(?<!_relation__)\bsource:\s*"{IMPORT_ACCOUNT_ID}"', rendered)
    assert len(attribute_sources) == attributes, rendered
    assert rendered.count(f'_relation__source: "{IMPORT_ACCOUNT_ID}"') == edges, rendered
    assert "is_protected" not in rendered, f"No protection key may be sent:\n{rendered}"
    # An owner property is a node id string; the `owner` relationship of `TestTeam` is not mapped.
    assert re.search(r'\bowner:\s*"', rendered) is None, f"No owner may be sent:\n{rendered}"
    assert "_relation__owner" not in rendered, rendered


def _applied_with_protection(operation: PlannedOperation, *, protect: bool) -> str:
    """Apply one operation with the import account as source, no owner and `protect` as given; return the mutation."""
    client = RecordingClient()
    adapter = make_adapter(client, source=IMPORT_ACCOUNT_ID, protect=protect)
    peers = PeerResolver(adapter)
    peers.remember(TAG_KIND, {"name": "tag-b"}, "tag-id-2")
    peers.remember(TAG_KIND, {"name": "tag-c"}, "tag-id-3")
    peers.remember(SITE_KIND, {"name": "site-a"}, "site-id-1")

    adapter.apply_planned_operation(operation=operation, peers=peers)

    assert len(client.mutations) == 1
    return client.mutations[0][1]


TEAM_UPDATE = make_operation(
    kind=TEAM_KIND,
    action="update",
    identity={"name": "team-a"},
    payload={"name": "team-a", "description": "edge"},
    relationships=[
        RelationshipReference(
            field="members", peer_kind=TAG_KIND, cardinality="many", peers=[{"name": "tag-b"}, {"name": "tag-c"}]
        )
    ],
)

PROTECTION_CASES = [
    pytest.param(TEAM_CREATE, 2, 2, id="create-attributes-and-many-edges"),
    pytest.param(server_operation("server-a"), 1, 1, id="create-attribute-and-one-edge"),
    pytest.param(TEAM_UPDATE, 2, 2, id="update-attributes-and-many-edges"),
]


@pytest.mark.parametrize(("operation", "attributes", "edges"), PROTECTION_CASES)
def test_protect_on_without_an_owner_protects_every_attribute_and_edge_written(
    operation: PlannedOperation, attributes: int, edges: int
) -> None:
    """FR-006: with `protect` on and no owner, creates and updates protect every value they write."""
    rendered = _applied_with_protection(operation, protect=True)

    attribute_flags = re.findall(r"(?<!_relation__)\bis_protected:\s*true", rendered)
    assert len(attribute_flags) == attributes, f"Every attribute written must be protected:\n{rendered}"
    assert rendered.count("_relation__is_protected: true") == edges, (
        f"Every relationship edge written must be protected:\n{rendered}"
    )
    assert re.search(r'\bowner:\s*"', rendered) is None, f"No owner may be sent:\n{rendered}"
    assert "_relation__owner" not in rendered, rendered


@pytest.mark.parametrize(("operation", "attributes", "edges"), PROTECTION_CASES)
def test_protect_off_sends_no_protection_key_on_a_create_or_an_update(
    operation: PlannedOperation, attributes: int, edges: int
) -> None:
    """FR-006: with `protect` off, neither a create nor an update sends `is_protected`, so earlier flags stay."""
    rendered = _applied_with_protection(operation, protect=False)

    assert "is_protected" not in rendered, f"No protection key may be sent:\n{rendered}"
    # The source still reaches every value written, so the case is not vacuous.
    attribute_sources = re.findall(rf'(?<!_relation__)\bsource:\s*"{IMPORT_ACCOUNT_ID}"', rendered)
    assert len(attribute_sources) == attributes, rendered
    assert rendered.count(f'_relation__source: "{IMPORT_ACCOUNT_ID}"') == edges, rendered


def test_a_payload_missing_an_identity_component_is_refused_before_any_write() -> None:
    """AD042: a create whose identity does not name every HFID component cannot key itself.

    `TestOrphan`'s human-friendly ID is `code`, which its identity does not name, so the
    payload assembled from that identity carries no value the destination can match on. The
    refusal names the component rather than merely reporting that the write was unkeyed.
    """
    client = RecordingClient()
    adapter = make_adapter(client)

    operation = make_operation(kind=ORPHAN_KIND, identity={"name": "orphan-a"}, payload={"name": "orphan-a"})

    with pytest.raises(UnkeyedCreateRefusedError) as excinfo:
        adapter.apply_planned_operation(operation=operation, peers=PeerResolver(adapter))

    message = str(excinfo.value)
    assert "code__value" in message, "The refusal must name the human-friendly-ID component that is missing."
    assert ORPHAN_KIND in message
    assert operation.operation_id in message
    assert not client.mutations, "Nothing may reach the destination once the payload is refused."


# ---------------------------------------------------------------------------------------
# Replace-set cases
# ---------------------------------------------------------------------------------------


def test_the_upsert_carries_exactly_the_plans_peer_set_with_no_destination_read() -> None:
    """AD038/AD085: the plan's peer set reaches the destination on the one convergent upsert."""
    client = RecordingClient()
    client.existing_peers[TEAM_KIND, "members"] = ["tag-id-1", "tag-id-2"]
    adapter = make_adapter(client)
    peers = PeerResolver(adapter)
    peers.remember(TAG_KIND, {"name": "tag-b"}, "tag-id-2")
    peers.remember(TAG_KIND, {"name": "tag-c"}, "tag-id-3")

    adapter.apply_planned_operation(operation=team_operation(["tag-b", "tag-c"]), peers=peers)

    assert client.mutation_names == [f"{TEAM_KIND}Upsert"], (
        "A planned operation is exactly one destination write, and it is the convergent upsert."
    )
    _, upsert = client.mutations[0]
    assert rendered_relationship_ids(upsert, "members") == ["tag-id-2", "tag-id-3"], (
        f"The upsert must carry exactly the plan's peer set. Rendered:\n{upsert}"
    )
    assert issued_reads(client) == [], (
        "The planned-write path issues no destination read: the SDK renders no removal directive "
        "either way, so surplus-peer removal is the destination Upsert mutation's replace semantics "
        "rather than anything a fetch-and-reconcile round trip could have computed."
    )


def test_an_empty_peer_list_empties_the_set_in_the_issued_upsert() -> None:
    """AD085: `peers: []` under `cardinality: many` reaches the destination."""
    client = RecordingClient()
    client.existing_peers[TEAM_KIND, "members"] = ["tag-id-1", "tag-id-2"]
    adapter = make_adapter(client)

    adapter.apply_planned_operation(operation=team_operation([]), peers=PeerResolver(adapter))

    assert client.mutation_names == [f"{TEAM_KIND}Upsert"]
    _, upsert = client.mutations[0]
    assert rendered_relationship_ids(upsert, "members") == [], (
        f"The upsert must carry an empty `members` list, not omit the key. Rendered:\n{upsert}"
    )


def test_a_peer_set_the_destination_already_holds_is_written_unchanged() -> None:
    """AD038: when the destination already holds the plan's set, the upsert is a no-op write."""
    client = RecordingClient()
    client.existing_peers[TEAM_KIND, "members"] = ["tag-id-2"]
    adapter = make_adapter(client)
    peers = PeerResolver(adapter)
    peers.remember(TAG_KIND, {"name": "tag-b"}, "tag-id-2")

    adapter.apply_planned_operation(operation=team_operation(["tag-b"]), peers=peers)

    assert client.mutation_names == [f"{TEAM_KIND}Upsert"]
    _, upsert = client.mutations[0]
    assert rendered_relationship_ids(upsert, "members") == ["tag-id-2"]
    assert issued_reads(client) == []


def test_the_upsert_carries_the_per_peer_write_metadata() -> None:
    """The convergent upsert alone carries every peer's `is_protected`, `source` and `owner`.

    Read off `client.mutations[0]` and nothing else: what makes the per-peer write metadata
    reach the destination is the upsert's own peer list, not any later write.
    """
    client = RecordingClient()
    adapter = make_adapter(client, source="source-account-1", owner="owner-account-1", protect=True)
    peers = PeerResolver(adapter)
    peers.remember(TAG_KIND, {"name": "tag-b"}, "tag-id-2")

    adapter.apply_planned_operation(operation=team_operation(["tag-b"]), peers=peers)

    assert client.mutation_names == [f"{TEAM_KIND}Upsert"]
    _, upsert = client.mutations[0]
    members_block = re.search(r"members:\s*\[(.*?)\]", upsert, flags=re.DOTALL)
    assert members_block is not None, f"The upsert must render the `members` peer list:\n{upsert}"
    rendered = members_block.group(1)
    assert "_relation__is_protected: true" in rendered, (
        f"The upsert must carry the peer's protection flag. Rendered:\n{upsert}"
    )
    assert '_relation__source: "source-account-1"' in rendered, (
        f"The upsert must carry the peer's source attribution. Rendered:\n{upsert}"
    )
    assert '_relation__owner: "owner-account-1"' in rendered, (
        f"The upsert must carry the peer's owner attribution. Rendered:\n{upsert}"
    )


def test_one_write_is_issued_per_operation_not_one_per_relationship() -> None:
    """V40: two cardinality-many relationships are still one destination write."""
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

    assert client.mutation_names == [f"{GROUP_KIND}Upsert"], (
        "Two cardinality-many relationships are still one write: the convergent upsert carries both."
    )
    _, upsert = client.mutations[0]
    assert rendered_relationship_ids(upsert, "members") == ["tag-id-2"]
    assert rendered_relationship_ids(upsert, "watchers") == ["tag-id-3"]


# ---------------------------------------------------------------------------------------
# Memo cases
# ---------------------------------------------------------------------------------------


def test_a_completed_operation_resolves_a_later_reference_with_no_destination_query() -> None:
    """FR-014: an operation's own result resolves the operations that refer to it."""
    client = RecordingClient()
    adapter = make_adapter(client)
    peers = PeerResolver(adapter)

    site = make_operation(kind=SITE_KIND, identity={"name": "site-a"}, payload={"name": "site-a"})
    adapter.apply_planned_operation(operation=site, peers=peers)
    adapter.apply_planned_operation(operation=server_operation("server-a"), peers=peers)

    assert client.resolver_queries == [], (
        "The peer was created by this same apply, so its identity must resolve from the memo."
    )
    (site_mutation, _site_query), (server_mutation, server_query) = client.mutations
    assert [site_mutation, server_mutation] == [f"{SITE_KIND}Upsert", f"{SERVER_KIND}Upsert"], (
        "Each operation is one convergent upsert, issued in the order the operations were applied."
    )
    assert rendered_related_id(server_query, "site") == NODE_ID


def test_a_failed_lookup_is_not_memoized_and_the_next_reference_reattempts() -> None:
    """AD036: a negative result is never cached."""
    client = RecordingClient()
    client.filter_results = [[], [make_node(client, SITE_KIND, "site-id-9")]]
    adapter = make_adapter(client)
    peers = PeerResolver(adapter)

    with pytest.raises(PeerNotFoundError):
        adapter.apply_planned_operation(operation=server_operation("server-a"), peers=peers)
    adapter.apply_planned_operation(operation=server_operation("server-b"), peers=peers)

    assert len(client.resolver_queries) == 2, (
        "The failed lookup must not be cached, so the second reference queries the destination again."
    )
    _, query = client.mutations[0]
    assert rendered_related_id(query, "site") == "site-id-9"


def test_a_failed_write_is_not_memoized_and_a_later_reference_queries_the_destination() -> None:
    """AD036: only a **completed** write is remembered."""
    client = RecordingClient()
    client.write_error = RuntimeError("the destination rejected the write")
    adapter = make_adapter(client)
    peers = PeerResolver(adapter)

    site = make_operation(kind=SITE_KIND, identity={"name": "site-a"}, payload={"name": "site-a"})
    with pytest.raises(RuntimeError):
        adapter.apply_planned_operation(operation=site, peers=peers)

    client.write_error = None
    client.filter_results = [[make_node(client, SITE_KIND, "site-id-7")]]
    adapter.apply_planned_operation(operation=server_operation("server-a"), peers=peers)

    assert len(client.resolver_queries) == 1, (
        "The failed write must leave no memo entry, so the reference falls through to the destination."
    )
    _, query = client.mutations[-1]
    assert rendered_related_id(query, "site") == "site-id-7"


def test_the_resolver_never_reads_the_client_store() -> None:
    """FR-014: the resolver has no store dependency."""
    client = RecordingClient()
    client.filter_results = [[make_node(client, SITE_KIND, "site-id-3")]]
    adapter = make_adapter(client)
    resolver = PeerResolver(adapter)

    with patch.object(client, "store", PoisonedStore()):
        node_id = resolver.resolve(peer_kind=SITE_KIND, identity={"name": "site-a"}, referring_operation_id="op_0000")

    assert node_id == "site-id-3"
    assert len(client.resolver_queries) == 1


def test_an_unknown_peer_kind_is_refused_loudly_before_any_destination_query() -> None:
    """A peer kind the destination schema does not declare raises, like the operation path."""
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
    """An empty filter set refuses, even when exactly one node of the kind exists."""
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
    # The next action is this condition's own, not `PeerNotFoundError`'s class-level one.
    # "Create the peer at the destination" is actively dangerous here — nothing established the
    # peer is absent, only that no filter could be derived to look for it, so the peer very
    # likely exists and creating it would duplicate it. The remedy is the identity (AD082).
    next_action = excinfo.value.next_action
    assert next_action != PeerNotFoundError.next_action, (
        f"The class-level remedy tells the operator to create the peer, which would duplicate it: {message}"
    )
    assert "do not create" in next_action.lower(), f"The next action must warn against creating the peer: {message}"
    assert "identifiers" in next_action, f"The next action must point at the identity that derived no filter: {message}"


def test_a_direct_resolve_on_a_partial_identity_filters_on_the_components_it_has(
    captured_logs: pytest.LogCaptureFixture,
) -> None:
    """The resolver alone still builds a filter from the components supplied; it no longer warns.

    Planned apply refuses a partial peer key before lookup (`_refuse_partial_key_peer_filter`),
    so this covers only the direct-resolver path.
    """
    client = RecordingClient()
    client.filter_results = [[make_node(client, DEVICE_KIND, "device-id-1")]]
    resolver = PeerResolver(make_adapter(client))

    resolved = resolver.resolve(peer_kind=DEVICE_KIND, identity={"name": "device-a"}, referring_operation_id="op_0002")

    assert resolved == "device-id-1"
    assert len(client.resolver_queries) == 1
    assert "name__value" in client.resolver_queries[0]
    assert "site__name__value" not in client.resolver_queries[0]
    assert not [record for record in captured_logs.records if "PARTIAL filter" in record.getMessage()]


# ---------------------------------------------------------------------------------------
# SC-016's local half
# ---------------------------------------------------------------------------------------


def test_a_zero_match_peer_refuses_the_operation_and_dispatches_nothing() -> None:
    """SC-016: an unresolvable peer fails the run rather than being silently skipped."""
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
    """SC-016: an ambiguous peer refuses too, with the count that makes it actionable."""
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
    `RuntimeError` is a defect and deliberately escapes unwrapped.
    """

    def __init__(self, *, reject_at: int | None = None, rejection: Exception | None = None) -> None:
        self.dispatched: list[str] = []
        self.reject_at = reject_at
        self.rejection = rejection

    def new_peer_resolver(self) -> object:  # noqa: PLR6301
        """The per-apply resolver factory; nothing below this double's surface reads it."""
        return object()

    def apply_planned_operation(self, *, operation: PlannedOperation, peers: Any) -> str:  # noqa: ANN401
        _ = peers
        if self.reject_at is not None and len(self.dispatched) == self.reject_at:
            if self.rejection is not None:
                raise self.rejection
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

    The state rule is the CLI's, mirrored rather than described: `infrahub_sync/cli.py`
    records `applied` when `apply_plan` returns and `failed` when it raises. Reading the state
    through this helper is what makes "the run ends `applied`" an assertion a `failed` run
    fails, rather than an inference from the absence of an exception (AD055).
    """
    try:
        record = engine.apply_plan(ownership=granted_ownership(), config_version=CONFIG_VERSION)
    except Exception as exc:  # noqa: BLE001 — the CLI catches exactly this broadly
        return "failed", exc
    return "applied", record


@pytest.fixture
def engine_logs(caplog: pytest.LogCaptureFixture) -> Iterator[pytest.LogCaptureFixture]:
    """Capture the engine's logger at DEBUG, so an `INFO` emission is captured and fails."""
    with caplog.at_level(logging.DEBUG, logger=ENGINE_LOGGER):
        yield caplog


# ---------------------------------------------------------------------------------------
# SC-007's local half: a delete-bearing plan, and the class that does still fail
# ---------------------------------------------------------------------------------------


def test_a_delete_bearing_plan_applies_every_non_delete_and_ends_applied(
    tmp_path: Path,
    engine_logs: pytest.LogCaptureFixture,
) -> None:
    """SC-007: a recorded delete is collected, never dispatched, and the run still succeeds."""
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
        f"(infrahub_sync/cli.py); it was emitted at {warnings[0].levelname}."
    )
    assert "2" in warnings[0].getMessage(), "The warning must name the count of deletes it did not execute."


def test_a_mid_apply_rejection_surfaces_the_rejection_not_the_knowability_invariant(tmp_path: Path) -> None:
    """AD062: the invariant is checked on a **completed** apply and nowhere else."""
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
    assert "destination GraphQL rejection" in str(outcome)


# ---------------------------------------------------------------------------------------
# SC-005's apply half: the reviewed set equals the applied record, in order
#
# The engine's own apply-loop matrix — stored order, the surfaceless refusal, the empty plan,
# the partial record on a rejection — lives at `tests/cache/test_apply_plan.py`, against the
# engine rather than through this adapter.
# ---------------------------------------------------------------------------------------


def test_a_refused_operation_stops_the_apply_and_leaves_the_prior_write_recorded(tmp_path: Path) -> None:
    """Apply is sequential: an earlier keyed write stands, and no later operation executes.

    The middle operation is refused by the create guard's coverage arm: `TestOrphan`'s
    human-friendly ID is `code`, which its identity does not name at all, so the create cannot
    be proven keyed and is refused before `client.create`.
    """
    directory = apply_run_dir(tmp_path)
    keyed = operation_record(kind=SITE_KIND, identity={"name": "site-a"})
    refused = operation_record(kind=ORPHAN_KIND, identity={"name": "orphan-a"})
    later = operation_record(kind=TAG_KIND, identity={"name": "tag-z"})
    write_artifact(directory, [keyed, refused, later], run_id=APPLY_RUN_ID, source_snapshot=[])

    client = RecordingClient()
    adapter = make_adapter(client)
    state, outcome = apply_and_record_state(engine_over(directory, adapter))

    assert state == "failed"
    assert isinstance(outcome, OperationApplyFailedError)
    assert client.mutation_names == [f"{SITE_KIND}Upsert"], (
        "The keyed operation wrote, the refused one made zero mutation calls, and the operation after it never ran."
    )
    record = outcome.apply_record
    assert list(record.applied_operations) == [keyed["operation_id"]]
    assert record.failed_operation == refused["operation_id"]


def test_a_refusal_that_wrote_nothing_says_so_rather_than_claiming_a_possible_partial(tmp_path: Path) -> None:
    """S6 reaches the operator's words, not only the run record.

    The record and the message are what an operator acts on together, so a refusal the record
    marks as having written nothing must not be described as one that may have written part
    of its change — that is the sentence that sends someone to reconcile a destination this
    operation never touched.
    """
    directory = apply_run_dir(tmp_path)
    refused = operation_record(kind=ORPHAN_KIND, identity={"name": "orphan-a"})
    write_artifact(directory, [refused], run_id=APPLY_RUN_ID, source_snapshot=[])

    client = RecordingClient()
    _state, outcome = apply_and_record_state(engine_over(directory, make_adapter(client)))

    assert isinstance(outcome, OperationApplyFailedError)
    assert outcome.apply_record.failed_operation_wrote is False
    assert "attempted no destination write" in str(outcome)
    assert "may itself have written" not in str(outcome)
    assert "wrote nothing" in outcome.next_action
    assert not client.mutations


def test_a_failure_of_unknown_reach_still_warns_that_it_may_have_written(tmp_path: Path) -> None:
    """The unchanged half: a destination rejection mid-write keeps the partial-write warning."""
    directory = apply_run_dir(tmp_path)
    write_artifact(
        directory,
        [operation_record(kind=SITE_KIND, identity={"name": "site-a"})],
        run_id=APPLY_RUN_ID,
        source_snapshot=[],
    )

    client = RecordingClient()
    client.write_error = GraphQLError([{"message": "the destination rejected this object"}], query="mutation { }")
    _state, outcome = apply_and_record_state(engine_over(directory, make_adapter(client)))

    assert isinstance(outcome, OperationApplyFailedError)
    assert outcome.apply_record.failed_operation_wrote is None
    assert "may itself have written" in str(outcome)


def test_a_uniqueness_refusal_is_not_described_as_something_re_applying_fixes(tmp_path: Path) -> None:
    """The one destination rejection where "re-apply, it converges" is a loop, not a remedy.

    The plan proposes a create; the destination already holds an object the constraint collides
    with. Applying the same plan again proposes the same create and is refused identically, so
    the advice has to be a fresh `diff` — or reconciling the object that is in the way.
    """
    directory = apply_run_dir(tmp_path)
    write_artifact(
        directory,
        [operation_record(kind=SITE_KIND, identity={"name": "site-a"})],
        run_id=APPLY_RUN_ID,
        source_snapshot=[],
    )
    client = RecordingClient()
    client.write_error = GraphQLError(
        [
            {
                "message": "Violates uniqueness constraint 'name-scope'",
                "extensions": {"code": "UNDEFINED_ERROR", "http_status": 422},
            }
        ],
        query="mutation { }",
    )

    _state, outcome = apply_and_record_state(engine_over(directory, make_adapter(client)))

    assert isinstance(outcome, OperationApplyFailedError)
    assert "re-applying the plan converges it" not in str(outcome)
    assert "refused the same way" in str(outcome)
    assert "re-run `diff`" in outcome.next_action
    assert "converges rather than duplicating" not in outcome.next_action
    assert "uniqueness constraint 'name-scope'" in str(outcome), "The classifier still names the constraint."


def test_an_ordinary_destination_rejection_still_advises_re_applying(tmp_path: Path) -> None:
    """The unchanged majority: a rejection of unknown reach converges on re-apply (AD033)."""
    directory = apply_run_dir(tmp_path)
    write_artifact(
        directory,
        [operation_record(kind=SITE_KIND, identity={"name": "site-a"})],
        run_id=APPLY_RUN_ID,
        source_snapshot=[],
    )
    client = RecordingClient()
    client.write_error = GraphQLError([{"message": "the destination rejected this object"}], query="mutation { }")

    _state, outcome = apply_and_record_state(engine_over(directory, make_adapter(client)))

    assert isinstance(outcome, OperationApplyFailedError)
    assert "re-applying the plan converges it" in str(outcome)
    assert "converges rather than duplicating" in outcome.next_action


def test_the_reviewed_operation_identifiers_equal_the_applied_record_in_the_same_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SC-005: what a review showed is exactly what an apply then wrote."""
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
    """The factory is what replaced the engine's cast to this class (AD086)."""
    adapter = make_adapter(RecordingClient())

    resolver = adapter.new_peer_resolver()

    assert isinstance(resolver, PeerResolver)
    assert resolver._adapter is adapter
    assert adapter.new_peer_resolver() is not resolver


def test_a_destination_missing_only_the_resolver_factory_is_refused_before_any_write(tmp_path: Path) -> None:
    """FR-023/AD086: the surface is both members, and the refusal still names the adapter."""
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
    assert "infrahub-sync diff" in message
    assert destination.dispatched == [], "Refused before any write, not part-way through one."


def test_the_gate_verifies_member_presence_only_and_is_no_stronger_than_hasattr(tmp_path: Path) -> None:
    """AD086's honesty clause, asserted rather than described."""
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
# The transport and auth edges on the two live-calling surfaces
# ---------------------------------------------------------------------------------------
#
# Constitution V asks for adapter edge-case tests covering timeouts and 401/403. The two
# surfaces this outcome adds both issue live destination calls during an apply — the planned
# write itself (`client.create` → `save(allow_upsert=True)`) and the
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
        "authentication",
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
    assert type(injected).__name__ in message, (
        "The operator-facing error must identify the failure category without rendering the SDK's "
        f"untrusted detail. Message was: {message!r}"
    )
    assert str(injected) not in message, "The raw SDK message must stay out of normal operator output."
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


def test_a_graphql_query_and_variables_never_reach_the_operator_message(tmp_path: Path) -> None:
    """SDK GraphQL errors include their query, so apply output must use a safe summary."""
    directory = apply_run_dir(tmp_path)
    operation = operation_record(kind=TAG_KIND, identity={"name": "tag-a"}, payload={"name": "tag-a"})
    write_artifact(directory, [operation], run_id=APPLY_RUN_ID, source_snapshot=[])
    secret = "SYNTHETIC_SECRET_CANARY"  # noqa: S105 - a deliberate redaction canary
    rejection = GraphQLError(
        [{"message": "the destination rejected this object"}],
        query=f'mutation {{ create(api_token: "{secret}") }}',
        variables={"api_token": secret},
    )

    with pytest.raises(OperationApplyFailedError) as caught:
        engine_over(directory, RecordingApplyDestination(reject_at=0, rejection=rejection)).apply_plan(
            ownership=granted_ownership(), config_version=CONFIG_VERSION
        )

    assert secret not in str(caught.value)
    assert "GraphQLError" in str(caught.value)
    assert caught.value.__cause__ is not None


@pytest.mark.parametrize(("injected", "expected_fragment"), LIVE_CALL_EDGES)
def test_a_failing_transport_or_auth_on_the_write_is_named_and_actionable(
    tmp_path: Path, injected: Exception, expected_fragment: str
) -> None:
    """The planned-write surface's transport and auth edges (Constitution V, plan.md's Principle V row)."""
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
    """The apply-time peer resolver's transport and auth edges."""
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


# ---------------------------------------------------------------------------------------
# The owner: set on creates and on the relationship edges an update adds, never changed (FR-002)
# ---------------------------------------------------------------------------------------

OWNER_ID = "owner-group-1"
OWNER_GROUP = NamedLineageNode(kind="CoreAccountGroup", name="network-team")


def _owner_count(rendered: str) -> tuple[int, int]:
    """How many attributes and how many relationship edges of a rendered mutation carry the owner."""
    attributes = len(re.findall(rf'(?<!_relation__)\bowner:\s*"{OWNER_ID}"', rendered))
    edges = rendered.count(f'_relation__owner: "{OWNER_ID}"')
    return attributes, edges


def _edge_block(rendered: str, peer_id: str) -> str:
    """The rendered input object of the relationship edge to `peer_id`."""
    match = re.search(rf'\{{\s*id:\s*"{peer_id}"[^{{}}]*\}}', rendered)
    assert match is not None, f"No edge to {peer_id!r} was rendered:\n{rendered}"
    return match.group(0)


def _applied_with_owner(operation: PlannedOperation, client: RecordingClient | None = None) -> RecordingClient:
    """Apply one operation with the import account as source and `OWNER_ID` as the named owner."""
    client = client or RecordingClient()
    adapter = make_adapter(client, source=IMPORT_ACCOUNT_ID, owner=OWNER_ID)
    peers = PeerResolver(adapter)
    peers.remember(TAG_KIND, {"name": "tag-b"}, "tag-id-2")
    peers.remember(TAG_KIND, {"name": "tag-c"}, "tag-id-3")
    peers.remember(SITE_KIND, {"name": "site-a"}, "site-id-1")
    peers.remember(SITE_KIND, {"name": "site-b"}, "site-id-2")
    adapter.apply_planned_operation(operation=operation, peers=peers)
    assert len(client.mutations) == 1
    return client


@pytest.mark.parametrize(
    ("operation", "attributes", "edges"),
    [
        pytest.param(TEAM_CREATE, 2, 2, id="attributes-and-many-edges"),
        pytest.param(server_operation("server-a"), 1, 1, id="attribute-and-one-edge"),
    ],
)
def test_a_create_sends_the_named_owner_on_every_attribute_and_edge(
    operation: PlannedOperation, attributes: int, edges: int
) -> None:
    """FR-002: every value a create writes is a value the run creates, so each carries the owner."""
    client = _applied_with_owner(operation)

    assert _owner_count(client.mutations[0][1]) == (attributes, edges), client.mutations[0][1]
    assert issued_reads(client) == [], "A create adds every edge, so it reads no current peers."


def test_an_update_sends_no_owner_on_attributes() -> None:
    """The attributes of an existing node keep the owner they have, including an empty one."""
    operation = make_operation(
        kind=TEAM_KIND,
        action="update",
        identity={"name": "team-a"},
        payload={"name": "team-a", "description": "core"},
    )

    client = _applied_with_owner(operation)

    rendered = client.mutations[0][1]
    assert _owner_count(rendered) == (0, 0), rendered
    assert f'source: "{IMPORT_ACCOUNT_ID}"' in rendered, "The update still writes the source."
    assert issued_reads(client) == [], "An update with no relationship reference adds no edge, so it reads nothing."


def test_an_update_sends_the_owner_only_on_the_cardinality_many_edges_it_adds() -> None:
    """An edge to a peer the node already has is written without the owner key."""
    client = RecordingClient()
    client.existing_peers[TEAM_KIND, "members"] = ["tag-id-2"]
    operation = team_operation(["tag-b", "tag-c"]).model_copy(
        update={"payload": {"name": "team-a", "description": "core"}}
    )

    _applied_with_owner(operation, client)

    rendered = client.mutations[0][1]
    assert _owner_count(rendered) == (0, 1), rendered
    assert "_relation__owner" not in _edge_block(rendered, "tag-id-2"), rendered
    assert f'_relation__owner: "{OWNER_ID}"' in _edge_block(rendered, "tag-id-3"), rendered
    reads = issued_reads(client)
    assert len(reads) == 1, "One read of the node's current peers for the update."
    assert reads[0]["id"] == DEFAULT_DESTINATION_ID
    assert reads[0]["include"] == ["members"]
    assert {"name", "description"} <= set(reads[0]["exclude"]), "The read selects no attribute value."


@pytest.mark.parametrize(
    ("current_site", "planned_site", "owner_on_edge"),
    [
        pytest.param("site-id-1", "site-a", False, id="same-peer"),
        pytest.param("site-id-1", "site-b", True, id="other-peer"),
        pytest.param(None, "site-a", True, id="no-current-peer"),
    ],
)
def test_an_update_sends_the_owner_on_a_cardinality_one_edge_only_when_the_peer_changes(
    current_site: str | None, planned_site: str, *, owner_on_edge: bool
) -> None:
    """A cardinality-one edge is added when the planned peer differs from the current one."""
    client = RecordingClient()
    if current_site is not None:
        client.existing_peers[SERVER_KIND, "site"] = [current_site]
    operation = server_operation("server-a", site_name=planned_site).model_copy(
        update={"action": "update", "destination_id": DEFAULT_DESTINATION_ID}
    )

    _applied_with_owner(operation, client)

    rendered = client.mutations[0][1]
    assert _owner_count(rendered) == (0, 1 if owner_on_edge else 0), rendered
    reads = issued_reads(client)
    assert len(reads) == 1
    assert reads[0]["include"] == [], "A cardinality-one relationship is selected without being included."
    assert "site" not in reads[0]["exclude"]


def test_an_update_with_no_named_owner_reads_no_current_peers() -> None:
    """With no owner named there is nothing to decide, so apply makes no extra read."""
    client = RecordingClient()
    client.existing_peers[TEAM_KIND, "members"] = ["tag-id-2"]
    adapter = make_adapter(client, source=IMPORT_ACCOUNT_ID)
    peers = PeerResolver(adapter)
    peers.remember(TAG_KIND, {"name": "tag-c"}, "tag-id-3")

    adapter.apply_planned_operation(operation=team_operation(["tag-c"]), peers=peers)

    assert issued_reads(client) == []
    assert "_relation__owner" not in client.mutations[0][1]


class NamedOwnerClient(RecordingClient):
    """A destination where the group the `owner` setting names exists with id `OWNER_ID`."""

    def __init__(self) -> None:
        super().__init__()
        self.group_schema = NodeSchemaAPI(
            id="group-schema", name="AccountGroup", namespace="Core", attributes=[], relationships=[]
        )
        self.live_schema = BranchSchema(hash="fixture", nodes={**SCHEMAS, OWNER_GROUP.kind: self.group_schema})
        self.schema.set_cache(self.live_schema)

    def get(self, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        if kwargs.get("kind") == OWNER_GROUP.kind:
            self.events.append(("owner_lookup", kwargs))
            return InfrahubNodeSync(client=self, schema=self.group_schema, data={"id": OWNER_ID})
        return super().get(*args, **kwargs)


def test_an_update_that_changes_nothing_is_not_planned() -> None:
    """Spec edge case: a node whose values equal the destination's gives no operation to apply."""
    unchanged = _FakeElement(
        kind="BuiltinTag",
        name="prod",
        keys={"name": "prod"},
        source_attrs={"description": "production", "slug": "prod"},
        dest_attrs={"description": "production", "slug": "prod"},
    )

    operations = operations_from_diff(
        _FakeDiff({"BuiltinTag": [unchanged]}),
        config=build_config(),
        tier_of=resolver(),
        source_adapter=qualified_source(),
        destination_adapter=_FakeAdapter("destination", []),
    )

    assert operations == []


def test_an_apply_with_a_named_owner_and_nothing_to_change_makes_no_write_call(tmp_path: Path) -> None:
    """Spec edge case: with nothing to change, apply writes nothing, so no owner changes."""
    directory = apply_run_dir(tmp_path)
    owner_record = {"kind": OWNER_GROUP.kind, "name": OWNER_GROUP.name}
    write_artifact(
        directory, [], run_id=APPLY_RUN_ID, source_snapshot=[], lineage={**RECORDED_LINEAGE, "owner": owner_record}
    )
    client = NamedOwnerClient()
    adapter = make_adapter(client)
    adapter.lineage_settings = InfrahubLineageSettings(owner=OWNER_GROUP)

    state, _ = apply_and_record_state(engine_over(directory, adapter))

    assert state == "applied"
    assert adapter.lineage is not None
    assert adapter.lineage.owner is not None
    assert adapter.lineage.owner.id == OWNER_ID, "Precondition: the owner was resolved for this apply."
    assert client.mutations == []
    assert issued_reads(client) == [], "No update was applied, so no current peers were read."


POOL_ID = "pool-1"


def _lineage_keys(edge: dict[str, Any]) -> set[str]:
    """The lineage properties an edge input carries."""
    return {key for key in ("source", "owner", "is_protected") if key in edge}


def test_an_edge_allocated_from_a_pool_carries_no_source_owner_or_protection() -> None:
    """The pool stays the source of an edge it allocates; other edges keep their lineage."""
    client = RecordingClient()
    create_data = client.schema.generate_payload_create(
        schema=TEAM_SCHEMA,
        data={"name": "team-a", "members": ["tag-id-2", "tag-id-3"]},
        source=IMPORT_ACCOUNT_ID,
        owner=OWNER_ID,
        is_protected=True,
    )
    create_data["members"][0] = {"from_pool": {"id": POOL_ID}, **create_data["members"][0]}
    del create_data["members"][0]["id"]

    clear_lineage_on_pool_edges(create_data, TEAM_SCHEMA)

    assert create_data["members"][0] == {"from_pool": {"id": POOL_ID}}
    assert _lineage_keys(create_data["members"][1]) == {"source", "owner", "is_protected"}
    assert _lineage_keys(create_data["name"]) == {"source", "owner", "is_protected"}, "Attributes keep their lineage."


def test_a_cardinality_one_edge_allocated_from_a_pool_carries_no_lineage() -> None:
    """The pool rule holds for a cardinality-one edge too."""
    create_data: dict[str, Any] = {
        "name": {"value": "server-a", "source": IMPORT_ACCOUNT_ID},
        "site": {"from_pool": {"id": POOL_ID}, "source": IMPORT_ACCOUNT_ID, "owner": OWNER_ID, "is_protected": True},
    }

    clear_lineage_on_pool_edges(create_data, SERVER_SCHEMA)

    assert create_data["site"] == {"from_pool": {"id": POOL_ID}}
    assert create_data["name"]["source"] == IMPORT_ACCOUNT_ID


class _StopAtCreateError(Exception):
    """Raised by the spy once the payload handed to `client.create` is recorded."""


def test_the_write_path_clears_lineage_on_pool_edges_before_the_node_is_built() -> None:
    """A write path that allocates from a pool hands `client.create` no lineage on that edge."""
    client = RecordingClient()
    adapter = make_adapter(client, source=IMPORT_ACCOUNT_ID, owner=OWNER_ID, protect=True)
    peers = PeerResolver(adapter)
    peers.remember(TAG_KIND, {"name": "tag-b"}, "tag-id-2")
    peers.remember(TAG_KIND, {"name": "tag-c"}, "tag-id-3")
    real = client.schema.generate_payload_create
    handed: list[dict[str, Any]] = []

    def allocate_first_member(**kwargs: Any) -> dict[str, Any]:  # noqa: ANN401
        payload = real(**kwargs)
        payload["members"][0]["from_pool"] = {"id": POOL_ID}
        return payload

    def record_create(*, kind: str, data: dict[str, Any], **_: Any) -> InfrahubNodeSync:  # noqa: ANN401
        del kind
        handed.append(data)
        raise _StopAtCreateError

    with (
        patch.object(client.schema, "generate_payload_create", allocate_first_member),
        patch.object(client, "create", record_create),
        pytest.raises(_StopAtCreateError),
    ):
        adapter.apply_planned_operation(operation=TEAM_CREATE, peers=peers)

    members = handed[0]["members"]
    assert _lineage_keys(members[0]) == set(), members
    assert _lineage_keys(members[1]) == {"source", "owner", "is_protected"}, members
