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
from infrahub_sync.plan.identity import canonical_identity, operation_id
from infrahub_sync.plan.models import PlannedOperation
from tests.adapters.test_infrahub_planned_write import (
    DEVICE_KIND,
    KEYLESS_KIND,
    NODE_ID,
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


def node_not_found_error() -> GraphQLError:
    """The server's answer to an upsert carrying an id that matches no object (spike M4b)."""
    return GraphQLError(
        [
            {
                "message": f"Unable to find the node {STALE_ID} / {SITE_KIND} in the database.",
                "extensions": {"code": "NODE_NOT_FOUND", "http_status": 404},
                "path": [f"{SITE_KIND}Upsert"],
            }
        ],
        query="mutation { ... }",
    )


def test_a_stale_destination_id_is_refused_as_a_named_error() -> None:
    """The server creates nothing on an unknown id, so this refusal names the id and re-plans."""
    from infrahub_sync.plan.errors import StaleDestinationIdError

    client, adapter, peers = keyed_adapter()
    client.write_error = node_not_found_error()
    operation = update_operation(
        kind=SITE_KIND, identity={"name": "site-a"}, payload={"name": "site-a"}, destination_id=STALE_ID
    )

    with pytest.raises(StaleDestinationIdError) as excinfo:
        adapter.apply_planned_operation(operation=operation, peers=peers)

    assert STALE_ID in str(excinfo.value)
    assert SITE_KIND in str(excinfo.value)


def test_a_stale_destination_id_is_marked_as_having_written_nothing() -> None:
    """S6: the server's own not-found proves no mutation landed, so this is not ambiguous."""
    from infrahub_sync.plan.errors import StaleDestinationIdError

    client, adapter, peers = keyed_adapter()
    client.write_error = node_not_found_error()
    operation = update_operation(
        kind=SITE_KIND, identity={"name": "site-a"}, payload={"name": "site-a"}, destination_id=STALE_ID
    )

    with pytest.raises(StaleDestinationIdError) as excinfo:
        adapter.apply_planned_operation(operation=operation, peers=peers)

    assert excinfo.value.wrote is False


def test_a_transport_failure_during_save_stays_ambiguous() -> None:
    """A mutation can commit before its transport fails, so this one may have written."""
    client, adapter, peers = keyed_adapter()
    client.write_error = GraphQLError([{"message": "the destination rejected this object"}], query="mutation { ... }")
    operation = update_operation(kind=SITE_KIND, identity={"name": "site-a"}, payload={"name": "site-a"})

    with pytest.raises(Exception) as excinfo:  # noqa: PT011 — the class is the operational wrapper, not the claim
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
    from infrahub_sync.plan.models import RelationshipReference

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
    client, adapter, peers = keyed_adapter()
    operation = update_operation(kind=KEYLESS_KIND, identity={"name": "keyless-a"}, payload={"name": "keyless-a"})

    assert adapter.apply_planned_operation(operation=operation, peers=peers) == NODE_ID
