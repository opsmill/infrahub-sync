"""Keyed writes at the destination: recorded id on update, proven HFID on create.

Every assertion here is made against the mutation the SDK actually **issues**, recorded at
the public `execute_graphql` edge by `RecordingClient`. That matters more than usual for
this change: the spike measured SDK 1.23.2 rendering *no* key for a create-shaped upsert
whose pre-save private render still claimed one, so a gate reading the private render can
report a keyed write that is unkeyed on the wire. The wire is the only account that binds.

The update key is a **scalar top-level `id`** inside the mutation's `data` block. It gets
there because `InfrahubNodeBase._generate_input_data` writes `data["id"] = self.id` when
`self.id` is set and only otherwise considers `hfid`
(`infrahub_sdk/node/node.py:295-298`, SDK 1.18.1) — so setting `node.id` after
`client.create` and before `save(allow_upsert=True)` is what keys the upsert. `id` is never
put into the `data` mapping handed to `client.create`, which would render it as the
attribute-shaped `id: {}` instead.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

import pytest
from infrahub_sdk.exceptions import GraphQLError
from infrahub_sdk.schema.main import BranchSchema, NodeSchemaAPI

from infrahub_sync.adapters.infrahub import InfrahubAdapter, PeerResolver
from infrahub_sync.plan.errors import UnaccountedIdentityComponentError
from infrahub_sync.plan.identity import canonical_identity, operation_id
from infrahub_sync.plan.models import PlannedOperation, RelationshipReference
from tests.adapters.test_infrahub_planned_write import (
    DEVICE_KIND,
    KEYLESS_KIND,
    NODE_ID,
    ORPHAN_KIND,
    SCHEMAS,
    SITE_KIND,
    RecordingClient,
    _text,
    make_adapter,
    make_operation,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

DESTINATION_ID = "18d52a8a-7e7d-9bf5-3967-c51149d169da"
STALE_ID = "18d52a8a-7e7d-9bf5-3967-c51149d169d0"

# A kind with no human-friendly ID but a declared two-field uniqueness constraint. G1 M8
# measured the server refusing a duplicate on such a kind (transport 200,
# `extensions.http_status` 422), so a create whose identity covers the constraint is safe to
# attempt; G1 M9 measured a kind with neither duplicating silently, which is what
# `TestKeyless` stands for.
CONSTRAINED_KIND = "TestConstrained"
CONSTRAINED_SCHEMA = NodeSchemaAPI(
    id="constrained-schema",
    name="Constrained",
    namespace="Test",
    label="Constrained",
    default_filter="name__value",
    uniqueness_constraints=[["name__value", "scope__value"]],
    attributes=[_text("constrained-name", "name", optional=False), _text("constrained-scope", "scope")],
    relationships=[],
)

ALL_SCHEMAS: dict[str, NodeSchemaAPI] = {**SCHEMAS, CONSTRAINED_KIND: CONSTRAINED_SCHEMA}


def keyed_adapter() -> tuple[RecordingClient, InfrahubAdapter, PeerResolver]:
    """A recording client and adapter that also know the constrained fixture kind."""
    client = RecordingClient()
    client.schema.set_cache(BranchSchema(hash="fixture", nodes=dict(ALL_SCHEMAS)))
    adapter = make_adapter(client)
    adapter.schema = dict(ALL_SCHEMAS)
    return client, adapter, PeerResolver(adapter)


def update_operation(
    *,
    kind: str,
    identity: Mapping[str, Any],
    payload: Mapping[str, Any],
    destination_id: str | None = DESTINATION_ID,
    relationships: list[RelationshipReference] | None = None,
) -> PlannedOperation:
    """One update operation carrying the destination id recorded for it at plan time."""
    canonical = canonical_identity(dict(identity), kind=kind)
    return PlannedOperation(
        operation_id=operation_id("update", kind, canonical),
        action="update",
        kind=kind,
        identity=canonical,
        tier=0,
        payload=dict(payload),
        relationships=relationships,
        destination_id=destination_id,
    )


def top_level_scalar_id(query: str) -> str | None:
    """The scalar `id` of a rendered mutation's top-level `data` block, if it has one.

    `Mutation.render` indents each field of the `data` block by twelve spaces, so a key at
    exactly that depth is a top-level input field. A relationship peer's `id` sits deeper
    and inside braces, so it cannot be mistaken for the object's own key.
    """
    match = re.search(r'^ {12}id:\s*"([^"]+)"\s*$', query, flags=re.MULTILINE)
    return match.group(1) if match else None


# ---------------------------------------------------------------------------------------
# Test 1 — an update is keyed by the recorded id, on the wire
# ---------------------------------------------------------------------------------------


def test_an_update_renders_a_scalar_top_level_id_equal_to_the_recorded_destination_id() -> None:
    """The recorded id reaches the wire as the object's own key, not as a peer reference."""
    client, adapter, peers = keyed_adapter()
    operation = update_operation(kind=SITE_KIND, identity={"name": "site-a"}, payload={"name": "site-a"})

    adapter.apply_planned_operation(operation=operation, peers=peers)

    assert client.mutation_names == [f"{SITE_KIND}Upsert"], "One convergent upsert, as for any operation."
    _name, query = client.mutations[0]
    assert top_level_scalar_id(query) == DESTINATION_ID


def test_an_update_of_a_relationship_crossing_hfid_kind_is_keyed_by_its_recorded_id() -> None:
    """AD067 closes: a kind whose HFID crosses a relationship is keyed by id on update."""
    client, adapter, peers = keyed_adapter()
    peers.remember(SITE_KIND, {"name": "site-a"}, "site-id-1")
    operation = update_operation(
        kind=DEVICE_KIND,
        identity={"name": "device-a", "site": {"peer_kind": SITE_KIND, "identity": {"name": "site-a"}}},
        payload={"name": "device-a"},
        relationships=[
            RelationshipReference(field="site", peer_kind=SITE_KIND, cardinality="one", peers=[{"name": "site-a"}])
        ],
    )

    adapter.apply_planned_operation(operation=operation, peers=peers)

    _name, query = client.mutations[0]
    assert top_level_scalar_id(query) == DESTINATION_ID


def test_a_no_hfid_kind_updates_by_its_recorded_id() -> None:
    """G1 M10: a recorded id is the universal update key, even where nothing else matches."""
    client, adapter, peers = keyed_adapter()
    operation = update_operation(kind=KEYLESS_KIND, identity={"name": "keyless-a"}, payload={"name": "keyless-a"})

    adapter.apply_planned_operation(operation=operation, peers=peers)

    _name, query = client.mutations[0]
    assert top_level_scalar_id(query) == DESTINATION_ID


def test_a_create_renders_no_top_level_id() -> None:
    """A create carries no recorded id, so it must key on its HFID components alone."""
    client, adapter, peers = keyed_adapter()
    operation = make_operation(kind=SITE_KIND, identity={"name": "site-a"}, payload={"name": "site-a"})

    adapter.apply_planned_operation(operation=operation, peers=peers)

    _name, query = client.mutations[0]
    assert top_level_scalar_id(query) is None


# ---------------------------------------------------------------------------------------
# Test 6 — a stale recorded id is a loud, proven-not-written refusal
# ---------------------------------------------------------------------------------------


def not_found_error(
    *, code: str | None = "NODE_NOT_FOUND", http_status: int | None = 404, split: bool = False
) -> GraphQLError:
    """The server's answer to an upsert carrying an id that matches no object (spike M4b).

    The measured signature is `code` **and** `http_status` in **one** error's extensions.
    The keyword arguments exist to build the near misses: a wrong status, an absent status,
    and the two halves split across two errors, none of which prove the server wrote nothing.
    """
    extensions: dict[str, Any] = {}
    if code is not None:
        extensions["code"] = code
    if http_status is not None:
        extensions["http_status"] = http_status
    errors: list[dict[str, Any]] = (
        [
            {"message": "one", "extensions": {"code": code}},
            {"message": "two", "extensions": {"http_status": http_status}},
        ]
        if split
        else [
            {
                "message": f"Unable to find the node {STALE_ID} / {SITE_KIND} in the database.",
                "extensions": extensions,
                "path": [f"{SITE_KIND}Upsert"],
            }
        ]
    )
    return GraphQLError(errors, query="mutation { ... }")


def node_not_found_error() -> GraphQLError:
    """The full, measured not-found signature."""
    return not_found_error()


def stale_update() -> PlannedOperation:
    """One update keyed by an id the destination will not find."""
    return update_operation(
        kind=SITE_KIND, identity={"name": "site-a"}, payload={"name": "site-a"}, destination_id=STALE_ID
    )


def test_a_stale_destination_id_is_refused_as_a_named_error() -> None:
    """The server creates nothing on an unknown id, so this refusal names the id and re-plans."""
    from infrahub_sync.plan.errors import StaleDestinationIdError

    client, adapter, peers = keyed_adapter()
    client.write_error = node_not_found_error()

    with pytest.raises(StaleDestinationIdError) as excinfo:
        adapter.apply_planned_operation(operation=stale_update(), peers=peers)

    assert STALE_ID in str(excinfo.value)
    assert SITE_KIND in str(excinfo.value)


def test_a_stale_destination_id_is_marked_as_having_written_nothing() -> None:
    """S6: the server's own not-found proves no mutation landed, so this is not ambiguous."""
    from infrahub_sync.plan.errors import StaleDestinationIdError

    client, adapter, peers = keyed_adapter()
    client.write_error = node_not_found_error()

    with pytest.raises(StaleDestinationIdError) as excinfo:
        adapter.apply_planned_operation(operation=stale_update(), peers=peers)

    assert excinfo.value.wrote is False


@pytest.mark.parametrize(
    ("description", "error"),
    [
        ("the application status is absent", not_found_error(http_status=None)),
        ("the application status is not 404", not_found_error(http_status=500)),
        ("the code and the status are in different errors", not_found_error(split=True)),
    ],
    ids=["absent-status", "wrong-status", "split-across-errors"],
)
def test_a_partial_not_found_signature_is_not_classified_as_proven_not_written(
    description: str, error: GraphQLError
) -> None:
    """Half the signature does not prove the server wrote nothing, so it stays ambiguous.

    `NODE_NOT_FOUND` alone is not the measured evidence: what proves the id path created
    nothing is that **and** `extensions.http_status` 404, in the same error. Anything short
    of it must reach the operator as the ordinary GraphQL failure, whose reach is unknown —
    claiming otherwise suppresses a reconciliation the operator needs.
    """
    _ = description
    client, adapter, peers = keyed_adapter()
    client.write_error = error

    with pytest.raises(GraphQLError) as caught:
        adapter.apply_planned_operation(operation=stale_update(), peers=peers)

    assert getattr(caught.value, "wrote", None) is not False, (
        "Only the full measured signature may be marked proven-not-written."
    )


def test_a_transport_failure_during_save_stays_ambiguous() -> None:
    """A mutation can commit before its transport fails, so this one may have written."""
    client, adapter, peers = keyed_adapter()
    client.write_error = GraphQLError([{"message": "the destination rejected this object"}], query="mutation { ... }")
    operation = update_operation(kind=SITE_KIND, identity={"name": "site-a"}, payload={"name": "site-a"})

    with pytest.raises(Exception) as excinfo:
        adapter.apply_planned_operation(operation=operation, peers=peers)

    assert getattr(excinfo.value, "wrote", None) is not False, (
        "Only a refusal proven not to have written may be marked so."
    )


# ---------------------------------------------------------------------------------------
# Tests 2 and 4 — the apply-side create guard, mirroring the plan-time rule
# ---------------------------------------------------------------------------------------


def test_a_create_whose_identity_omits_an_hfid_component_is_refused_with_no_mutation() -> None:
    """`[F5-R1]` identity coverage: `site` is resolvable from the payload yet absent from identity."""
    from infrahub_sync.plan.errors import UnkeyedCreateRefusedError

    client, adapter, peers = keyed_adapter()
    peers.remember(SITE_KIND, {"name": "site-a"}, "site-id-1")
    operation = make_operation(
        kind=DEVICE_KIND,
        identity={"name": "device-a"},
        payload={"name": "device-a"},
        relationships=[
            RelationshipReference(field="site", peer_kind=SITE_KIND, cardinality="one", peers=[{"name": "site-a"}])
        ],
    )

    with pytest.raises(UnkeyedCreateRefusedError):
        adapter.apply_planned_operation(operation=operation, peers=peers)

    assert client.mutation_names == [], "A refused create attempts no destination mutation."


def test_a_create_on_a_no_hfid_kind_without_a_covered_constraint_is_refused_with_no_mutation() -> None:
    """G1 M9: a kind with neither an HFID nor a constraint duplicates on every write."""
    from infrahub_sync.plan.errors import UnkeyedCreateRefusedError

    client, adapter, peers = keyed_adapter()
    operation = make_operation(kind=KEYLESS_KIND, identity={"name": "keyless-a"}, payload={"name": "keyless-a"})

    with pytest.raises(UnkeyedCreateRefusedError):
        adapter.apply_planned_operation(operation=operation, peers=peers)

    assert client.mutation_names == [], "A refused create attempts no destination mutation."


def test_a_create_on_a_no_hfid_kind_with_a_covered_constraint_is_written() -> None:
    """G1 M8: the server refuses the duplicate, so the first create is safe to attempt."""
    client, adapter, peers = keyed_adapter()
    operation = make_operation(
        kind=CONSTRAINED_KIND,
        identity={"name": "c-a", "scope": "s-a"},
        payload={"name": "c-a", "scope": "s-a"},
    )

    assert adapter.apply_planned_operation(operation=operation, peers=peers) == NODE_ID
    assert client.mutation_names == [f"{CONSTRAINED_KIND}Upsert"]


def test_a_create_on_a_no_hfid_kind_with_a_partially_covered_constraint_is_refused() -> None:
    """Half a uniqueness constraint constrains nothing: the write could still duplicate."""
    from infrahub_sync.plan.errors import UnkeyedCreateRefusedError

    client, adapter, peers = keyed_adapter()
    operation = make_operation(kind=CONSTRAINED_KIND, identity={"name": "c-a"}, payload={"name": "c-a"})

    with pytest.raises(UnkeyedCreateRefusedError):
        adapter.apply_planned_operation(operation=operation, peers=peers)

    assert client.mutation_names == []


def test_a_no_hfid_kind_update_is_allowed_where_its_create_is_refused() -> None:
    """The asymmetry is the point: `id` keys an update that no HFID could key."""
    _client, adapter, peers = keyed_adapter()
    operation = update_operation(kind=KEYLESS_KIND, identity={"name": "keyless-a"}, payload={"name": "keyless-a"})

    assert adapter.apply_planned_operation(operation=operation, peers=peers) == NODE_ID


# ---------------------------------------------------------------------------------------
# Test 2, apply side — coverage first, then AD051 for values
# ---------------------------------------------------------------------------------------
#
# The two arms are different mechanisms and must stay so. Identity coverage answers "does
# the operation even name every component", which is a question about the plan. AD051's
# `_assert_identity_components_accounted_for` answers "does each named component arrive with
# a value", which is a question about the assembled write — and it is the only check that
# can say *which* component is missing. A guard that answered both would make AD051
# unreachable and lose that diagnosis.


def test_a_create_whose_nested_peer_identity_omits_the_component_value_is_refused_by_ad051() -> None:
    """The relationship-crossing value case: identity names `site`, the peer supplies no name."""
    client, adapter, peers = keyed_adapter()
    peers.remember(SITE_KIND, {"code": "site-a"}, "site-id-1")
    operation = make_operation(
        kind=DEVICE_KIND,
        identity={"name": "device-a", "site": {"peer_kind": SITE_KIND, "identity": {"code": "site-a"}}},
        payload={"name": "device-a"},
        relationships=[
            RelationshipReference(field="site", peer_kind=SITE_KIND, cardinality="one", peers=[{"code": "site-a"}])
        ],
    )

    with pytest.raises(UnaccountedIdentityComponentError) as excinfo:
        adapter.apply_planned_operation(operation=operation, peers=peers)

    assert "site__name__value" in str(excinfo.value), "AD051 names the component, which is why it is kept."
    assert client.mutation_names == [], "A refused create attempts no destination mutation."


def test_a_create_whose_direct_component_value_is_empty_is_refused_by_ad051() -> None:
    """A present-but-empty component keys nothing, and AD051 is what names it."""
    client, adapter, peers = keyed_adapter()
    operation = make_operation(kind=SITE_KIND, identity={"name": ""}, payload={"name": ""})

    with pytest.raises(UnaccountedIdentityComponentError) as excinfo:
        adapter.apply_planned_operation(operation=operation, peers=peers)

    assert "name__value" in str(excinfo.value)
    assert client.mutation_names == [], "A refused create attempts no destination mutation."


def test_identity_coverage_is_refused_before_ad051_ever_runs() -> None:
    """Coverage is the earlier question: a component the identity never names is not AD051's.

    `TestOrphan`'s human-friendly ID is `code`, which its identity does not name at all, so
    the operation cannot be proven keyed before any value question arises.
    """
    from infrahub_sync.plan.errors import UnkeyedCreateRefusedError

    client, adapter, peers = keyed_adapter()
    operation = make_operation(kind=ORPHAN_KIND, identity={"name": "orphan-a"}, payload={"name": "orphan-a"})

    with pytest.raises(UnkeyedCreateRefusedError):
        adapter.apply_planned_operation(operation=operation, peers=peers)

    assert client.mutation_names == []


# ---------------------------------------------------------------------------------------
# Recorded-id updates do not require complete human-friendly-ID components.
# ---------------------------------------------------------------------------------------


def test_an_update_missing_an_hfid_component_still_reaches_the_recorded_id_write() -> None:
    """`TestOrphan`'s HFID is `code`, which the payload does not carry — and need not.

    The recorded id names the object. Refusing here would make an id-keyed update depend on a
    key it does not use.
    """
    client, adapter, peers = keyed_adapter()
    operation = update_operation(kind=ORPHAN_KIND, identity={"name": "orphan-a"}, payload={"name": "orphan-a"})

    assert adapter.apply_planned_operation(operation=operation, peers=peers) == NODE_ID
    assert client.mutation_names == [f"{ORPHAN_KIND}Upsert"], "The update is written, not refused."
    _name, query = client.mutations[0]
    assert top_level_scalar_id(query) == DESTINATION_ID


def test_an_update_whose_hfid_component_is_blank_still_reaches_the_recorded_id_write() -> None:
    """A blank component keys nothing — which is exactly why the id is what keys this write.

    Identity and payload agree on the blank value, as the record type requires; what is under
    test is that a blank **HFID component** no longer stops an id-keyed write. The create with
    this identical shape is refused below.
    """
    client, adapter, peers = keyed_adapter()
    operation = update_operation(kind=SITE_KIND, identity={"name": "  "}, payload={"name": "  "})

    assert adapter.apply_planned_operation(operation=operation, peers=peers) == NODE_ID
    _name, query = client.mutations[0]
    assert top_level_scalar_id(query) == DESTINATION_ID


def test_an_update_of_a_relationship_crossing_kind_missing_its_peer_component_is_written() -> None:
    """A crossing component the peer identity cannot supply: the recorded id keys the write."""
    client, adapter, peers = keyed_adapter()
    peers.remember(SITE_KIND, {"code": "site-a"}, "site-id-1")
    operation = update_operation(
        kind=DEVICE_KIND,
        identity={"name": "device-a", "site": {"peer_kind": SITE_KIND, "identity": {"code": "site-a"}}},
        payload={"name": "device-a"},
        relationships=[
            RelationshipReference(field="site", peer_kind=SITE_KIND, cardinality="one", peers=[{"code": "site-a"}])
        ],
    )

    assert adapter.apply_planned_operation(operation=operation, peers=peers) == NODE_ID
    _name, query = client.mutations[0]
    assert top_level_scalar_id(query) == DESTINATION_ID


def test_the_same_blank_component_as_a_create_is_still_refused_by_ad051() -> None:
    """The same kind and payload as the update above: a create is refused where an update is not.

    A create is matched on the components it carries, so a blank one is refused; an update is
    keyed by its recorded id, so the same blank value is irrelevant to it.
    """
    client, adapter, peers = keyed_adapter()
    operation = make_operation(kind=SITE_KIND, identity={"name": "  "}, payload={"name": "  "})

    with pytest.raises(UnaccountedIdentityComponentError) as excinfo:
        adapter.apply_planned_operation(operation=operation, peers=peers)

    assert "name__value" in str(excinfo.value), "AD051 still names the component for a create."
    assert client.mutation_names == [], "A refused create attempts no destination mutation."
