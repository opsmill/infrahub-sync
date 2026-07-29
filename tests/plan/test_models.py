"""T015 — the plan artifact's record types (FR-002, FR-026, FR-027, FR-028).

The AD042 guard is asserted **at the model level**, not only where the derivation builds a
payload: an operation whose payload came from diffsync's `get_attrs()` alone carries none of
the identity components, so it would validate cleanly and produce an unkeyed upsert that
duplicates on every re-apply. The type has to refuse it, or the guard is only as good as
whichever caller remembered it.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from infrahub_sync.plan.errors import UnsupportedOperationActionError
from infrahub_sync.plan.identity import operation_id
from infrahub_sync.plan.models import (
    ACTIONS,
    CHECKSUM_EXCLUDED_FIELDS,
    PLAN_FORMAT_VERSION,
    SC006_MASKED_FIELDS,
    SUPPORTED_FORMAT_VERSIONS,
    ApplyRecord,
    PlanManifest,
    PlannedOperation,
    PlanSummary,
    RelationshipReference,
    SourceSnapshotRecord,
    VerificationFailure,
)

SITE_PEER: dict[str, Any] = {"name": "dc1"}

# Distinguishes "the caller did not mention payload" from "the caller passed None",
# because `None` is itself one of the values under test.
_UNSET = object()


def _operation(  # noqa: PLR0913 — one builder per record field keeps each case to its own concern
    *,
    action: str = "create",
    kind: str = "BuiltinTag",
    identity: dict[str, Any] | None = None,
    payload: object = _UNSET,
    relationships: list[dict[str, Any]] | None = None,
    tier: int = 0,
    op_id: str | None = None,
) -> dict[str, Any]:
    """Build a raw operation mapping with a correctly derived identifier by default.

    `payload` defaults to "whatever this action requires", so a case that is not about the
    payload rule does not have to restate it.
    """
    effective_identity = {"name": "prod"} if identity is None else identity
    default_payload = None if action == "delete" else dict(effective_identity)
    effective_payload = default_payload if payload is _UNSET else payload
    record: dict[str, Any] = {
        "operation_id": op_id if op_id is not None else operation_id(action, kind, effective_identity),
        "action": action,
        "kind": kind,
        "identity": effective_identity,
        "tier": tier,
        "payload": effective_payload,
    }
    if relationships is not None:
        record["relationships"] = relationships
    return record


def _reference(
    field: str = "site", *, cardinality: str = "one", peers: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    """Build a raw relationship-reference mapping."""
    return {
        "field": field,
        "peer_kind": "LocationSite",
        "cardinality": cardinality,
        "peers": [SITE_PEER] if peers is None else peers,
    }


# ======================================================================================
# Constants
# ======================================================================================


def test_declared_constants() -> None:
    """The constants nine later outcomes read are exactly as the data model fixes them."""
    assert PLAN_FORMAT_VERSION == 2
    assert frozenset({2}) == SUPPORTED_FORMAT_VERSIONS
    assert ACTIONS == ("create", "update", "delete")
    assert CHECKSUM_EXCLUDED_FIELDS == ("plan_checksum", "run_id", "created_at")
    assert SC006_MASKED_FIELDS == ("run_id", "created_at")


def test_the_two_masks_are_deliberately_different() -> None:
    """`plan_checksum` needs no SC-006 mask: it is a function of the checksummed bytes."""
    assert set(SC006_MASKED_FIELDS) < set(CHECKSUM_EXCLUDED_FIELDS)
    assert set(CHECKSUM_EXCLUDED_FIELDS) - set(SC006_MASKED_FIELDS) == {"plan_checksum"}


# ======================================================================================
# `payload` is None if and only if `action == "delete"`
# ======================================================================================


@pytest.mark.parametrize("action", ["create", "update"])
def test_non_delete_requires_a_payload(action: str) -> None:
    """A create or update with no payload is refused — there is nothing to write."""
    with pytest.raises(ValidationError):
        PlannedOperation(**_operation(action=action, payload=None))


@pytest.mark.parametrize("action", ["create", "update"])
def test_non_delete_accepts_a_payload(action: str) -> None:
    """The other half of the biconditional."""
    operation = PlannedOperation(**_operation(action=action))
    assert operation.payload is not None


def test_delete_must_carry_no_payload() -> None:
    """A delete names an object to remove; a payload on it is a corrupt record."""
    with pytest.raises(ValidationError):
        PlannedOperation(**_operation(action="delete", payload={"name": "prod"}))


def test_delete_with_no_payload_validates() -> None:
    """The other half, and the one case where the identity guard does not apply."""
    operation = PlannedOperation(**_operation(action="delete"))
    assert operation.payload is None


def test_delete_with_an_empty_payload_is_still_refused() -> None:
    """`{}` is not `None`: an empty mapping is a payload and a delete carries none."""
    with pytest.raises(ValidationError):
        PlannedOperation(**_operation(action="delete", payload={}))


def test_a_delete_identity_need_not_appear_in_any_payload() -> None:
    """A delete's identity components are not payload-bound — it has no payload."""
    identity = {"name": "rack-a", "site": {"peer_kind": "LocationSite", "identity": SITE_PEER}}
    operation = PlannedOperation(**_operation(action="delete", kind="LocationRack", identity=identity))
    assert set(operation.identity) == {"name", "site"}


# ======================================================================================
# `operation_id` is recomputed on construction
# ======================================================================================


def test_operation_id_is_recomputed_and_agrees_with_the_derivation() -> None:
    """The stored identifier must equal the one its own triple derives."""
    operation = PlannedOperation(**_operation(action="create", kind="BuiltinTag", identity={"name": "prod"}))
    assert operation.operation_id == operation_id("create", "BuiltinTag", {"name": "prod"})


@pytest.mark.parametrize(
    "stored",
    [
        pytest.param("op_0000000000000000", id="well-formed but wrong"),
        pytest.param(operation_id("update", "BuiltinTag", {"name": "prod"}), id="right shape, wrong action"),
        pytest.param(operation_id("create", "BuiltinRole", {"name": "prod"}), id="right shape, wrong kind"),
        pytest.param(operation_id("create", "BuiltinTag", {"name": "staging"}), id="right shape, wrong identity"),
    ],
)
def test_mismatched_stored_operation_id_is_rejected(stored: str) -> None:
    """A stored identifier that does not match its triple means the record is corrupt."""
    with pytest.raises(ValidationError):
        PlannedOperation(**_operation(op_id=stored))


@pytest.mark.parametrize(
    "stored",
    [
        pytest.param("3531c0d83d698fd1", id="no op_ prefix"),
        pytest.param("op_3531C0D83D698FD1", id="uppercase hex"),
        pytest.param("op_3531c0d83d698fd", id="fifteen characters"),
        pytest.param("op_3531c0d83d698fd12", id="seventeen characters"),
        pytest.param("op_zzzzzzzzzzzzzzzz", id="not hex"),
        pytest.param("", id="empty"),
    ],
)
def test_malformed_operation_id_is_rejected(stored: str) -> None:
    """The identifier's shape is enforced too, not only its value."""
    with pytest.raises(ValidationError):
        PlannedOperation(**_operation(op_id=stored))


def test_identity_key_order_does_not_change_the_accepted_identifier() -> None:
    """The identity is canonicalised before the identifier is checked."""
    identity = {"site": {"peer_kind": "LocationSite", "identity": SITE_PEER}, "name": "rack-a"}
    record = _operation(
        action="update",
        kind="LocationRack",
        identity=identity,
        payload={"name": "rack-a"},
        relationships=[_reference("site")],
    )
    operation = PlannedOperation(**record)
    assert list(operation.identity) == ["name", "site"]


# ======================================================================================
# The AD042 identity-in-payload guard, at the model level
# ======================================================================================


@pytest.mark.parametrize("action", ["create", "update"])
def test_identity_component_in_the_payload_satisfies_the_guard(action: str) -> None:
    """A direct identity component may be carried by the payload."""
    operation = PlannedOperation(
        **_operation(
            action=action, kind="BuiltinTag", identity={"name": "prod"}, payload={"name": "prod", "description": "x"}
        )
    )
    assert operation.payload is not None
    assert "name" in operation.payload


@pytest.mark.parametrize("action", ["create", "update"])
def test_identity_component_as_a_relationship_reference_satisfies_the_guard(action: str) -> None:
    """A reference-valued identity component is carried by `relationships`, not the payload."""
    identity = {"name": "rack-a", "site": {"peer_kind": "LocationSite", "identity": SITE_PEER}}
    operation = PlannedOperation(
        **_operation(
            action=action,
            kind="LocationRack",
            identity=identity,
            payload={"name": "rack-a"},
            relationships=[_reference("site")],
        )
    )
    assert [reference.field for reference in operation.relationships or []] == ["site"]


@pytest.mark.parametrize("action", ["create", "update"])
def test_identity_component_in_neither_is_rejected(action: str) -> None:
    """AD042: the payload from `source_attrs` alone. This is the case that must not pass.

    `get_attrs()` excludes `_identifiers`, so a payload built from it carries no identity
    component; the upsert would be unkeyed and every re-apply would duplicate.
    """
    with pytest.raises(ValidationError) as excinfo:
        PlannedOperation(
            **_operation(action=action, kind="BuiltinTag", identity={"name": "prod"}, payload={"description": "x"})
        )
    assert "name" in str(excinfo.value)


@pytest.mark.parametrize("action", ["create", "update"])
def test_reference_component_in_neither_is_rejected(action: str) -> None:
    """The same for a reference-valued component with no matching relationship reference."""
    identity = {"name": "rack-a", "site": {"peer_kind": "LocationSite", "identity": SITE_PEER}}
    with pytest.raises(ValidationError) as excinfo:
        PlannedOperation(
            **_operation(action=action, kind="LocationRack", identity=identity, payload={"name": "rack-a"})
        )
    assert "site" in str(excinfo.value)


@pytest.mark.parametrize("action", ["create", "update"])
def test_a_non_matching_relationship_reference_does_not_satisfy_the_guard(action: str) -> None:
    """The reference must be for **that** field; another field's reference does not count."""
    identity = {"name": "rack-a", "site": {"peer_kind": "LocationSite", "identity": SITE_PEER}}
    with pytest.raises(ValidationError):
        PlannedOperation(
            **_operation(
                action=action,
                kind="LocationRack",
                identity=identity,
                payload={"name": "rack-a"},
                relationships=[_reference("tags", cardinality="many", peers=[SITE_PEER])],
            )
        )


@pytest.mark.parametrize("action", ["create", "update"])
def test_the_guard_covers_every_identity_component_not_merely_one(action: str) -> None:
    """A two-component identity with one component supplied is still unkeyed."""
    identity = {"name": "eth0", "device": {"peer_kind": "DcimDevice", "identity": {"name": "dev1"}}, "index": 1}
    with pytest.raises(ValidationError) as excinfo:
        PlannedOperation(
            **_operation(
                action=action,
                kind="InterfacePhysical",
                identity=identity,
                payload={"name": "eth0"},
                relationships=[_reference("device")],
            )
        )
    assert "index" in str(excinfo.value)


def test_the_guard_message_names_the_missing_components() -> None:
    """The message names what is missing, so the operator can fix the mapping."""
    identity = {"alpha": 1, "beta": 2, "gamma": 3}
    with pytest.raises(ValidationError) as excinfo:
        PlannedOperation(**_operation(kind="Thing", identity=identity, payload={"beta": 2}))
    message = str(excinfo.value)
    assert "alpha" in message
    assert "gamma" in message


def test_an_empty_identity_passes_the_guard_vacuously() -> None:
    """No components means nothing to require; the guard adds no rule of its own."""
    operation = PlannedOperation(**_operation(identity={}, payload={"description": "x"}))
    assert operation.identity == {}


# ======================================================================================
# MIN-015 — one unambiguous source per field
# ======================================================================================


def test_duplicate_relationship_reference_fields_are_rejected() -> None:
    """Two references for one field make the replace-set write depend on reference order.

    Which peer set the apply flushes would be whichever reference the loop visits last, so
    the record is ambiguous and is refused at validation (MIN-015).
    """
    with pytest.raises(ValidationError) as excinfo:
        PlannedOperation(
            **_operation(
                kind="OrgTeam",
                identity={"name": "team-a"},
                payload={"name": "team-a"},
                relationships=[
                    _reference("members", cardinality="many", peers=[{"name": "alice"}]),
                    _reference("members", cardinality="many", peers=[{"name": "bob"}]),
                ],
            )
        )
    assert "members" in str(excinfo.value)


def test_duplicate_reference_fields_are_rejected_even_when_the_references_are_identical() -> None:
    """Byte-identical duplicates are still two sources; deduplicating would guess intent."""
    with pytest.raises(ValidationError):
        PlannedOperation(
            **_operation(
                kind="OrgTeam",
                identity={"name": "team-a"},
                payload={"name": "team-a"},
                relationships=[
                    _reference("members", cardinality="many", peers=[{"name": "alice"}]),
                    _reference("members", cardinality="many", peers=[{"name": "alice"}]),
                ],
            )
        )


def test_a_field_in_both_payload_and_relationships_is_rejected() -> None:
    """One field, two competing write sources: the upsert value and the flush value."""
    with pytest.raises(ValidationError) as excinfo:
        PlannedOperation(
            **_operation(
                kind="LocationRack",
                identity={"name": "rack-a"},
                payload={"name": "rack-a", "site": "dc1"},
                relationships=[_reference("site")],
            )
        )
    assert "site" in str(excinfo.value)


def test_a_non_identity_field_in_both_payload_and_relationships_is_rejected_too() -> None:
    """The dual-source ambiguity does not depend on the field being an identity component."""
    with pytest.raises(ValidationError) as excinfo:
        PlannedOperation(
            **_operation(
                kind="BuiltinTag",
                identity={"name": "prod"},
                payload={"name": "prod", "tags": ["x"]},
                relationships=[_reference("tags", cardinality="many", peers=[SITE_PEER])],
            )
        )
    assert "tags" in str(excinfo.value)


def test_distinct_reference_fields_beside_a_disjoint_payload_stay_accepted() -> None:
    """The rule refuses duplication, not relationships: the ordinary shape still validates."""
    identity = {"name": "rack-a", "site": {"peer_kind": "LocationSite", "identity": SITE_PEER}}
    operation = PlannedOperation(
        **_operation(
            kind="LocationRack",
            identity=identity,
            payload={"name": "rack-a"},
            relationships=[
                _reference("site"),
                _reference("tags", cardinality="many", peers=[SITE_PEER]),
            ],
        )
    )
    assert [reference.field for reference in operation.relationships or []] == ["site", "tags"]


# ======================================================================================
# FIX-013 — the identity reviewed and hashed must agree with the value written
# ======================================================================================


def test_a_scalar_identity_component_disagreeing_with_its_payload_value_is_rejected() -> None:
    """`identity={"name": "reviewed"}` beside `payload={"name": "actually-written"}`.

    Review, the operation id and the write would each describe a different object: apply
    builds the mutation from the payload, writes the other object, then memoizes the result
    under the reviewed identity — so a later same-run reference is wired to the wrong node.
    """
    with pytest.raises(ValidationError) as excinfo:
        PlannedOperation(**_operation(identity={"name": "reviewed"}, payload={"name": "actually-written"}))
    message = str(excinfo.value)
    assert "reviewed" in message
    assert "actually-written" in message


def test_a_one_peer_identity_component_disagreeing_with_its_reference_peer_is_rejected() -> None:
    """The identity names `dc1` while the matching reference names `dc2`."""
    identity = {"name": "rack-a", "site": {"peer_kind": "LocationSite", "identity": {"name": "dc1"}}}
    with pytest.raises(ValidationError) as excinfo:
        PlannedOperation(
            **_operation(
                kind="LocationRack",
                identity=identity,
                payload={"name": "rack-a"},
                relationships=[_reference("site", cardinality="one", peers=[{"name": "dc2"}])],
            )
        )
    assert "site" in str(excinfo.value)


def test_a_reference_whose_peer_kind_disagrees_with_the_identity_component_is_rejected() -> None:
    """Same peer identity, different peer kind: still two different objects."""
    identity = {"name": "rack-a", "site": {"peer_kind": "LocationRegion", "identity": SITE_PEER}}
    with pytest.raises(ValidationError):
        PlannedOperation(
            **_operation(
                kind="LocationRack",
                identity=identity,
                payload={"name": "rack-a"},
                relationships=[_reference("site")],  # peer_kind LocationSite
            )
        )


def test_a_matching_scalar_identity_component_stays_accepted() -> None:
    """The positive half FIX-013 must not break: agreement validates."""
    operation = PlannedOperation(**_operation(identity={"name": "prod"}, payload={"name": "prod", "color": "red"}))
    assert operation.identity == {"name": "prod"}


def test_a_matching_one_peer_identity_component_stays_accepted() -> None:
    """A cardinality-one reference whose single peer is the identity's named peer."""
    identity = {"name": "rack-a", "site": {"peer_kind": "LocationSite", "identity": SITE_PEER}}
    operation = PlannedOperation(
        **_operation(
            kind="LocationRack",
            identity=identity,
            payload={"name": "rack-a"},
            relationships=[_reference("site", cardinality="one", peers=[SITE_PEER])],
        )
    )
    assert operation.identity["site"] == {"peer_kind": "LocationSite", "identity": SITE_PEER}


def test_a_matching_many_peer_identity_component_stays_accepted() -> None:
    """The many-peer shape `derive` records: a list of pairs, one per peer, in peer order."""
    members = [{"name": "alice"}, {"name": "bob"}]
    identity = {
        "name": "team-a",
        "members": [{"peer_kind": "OrgPerson", "identity": peer} for peer in members],
    }
    operation = PlannedOperation(
        **_operation(
            kind="OrgTeam",
            identity=identity,
            payload={"name": "team-a"},
            relationships=[{"field": "members", "peer_kind": "OrgPerson", "cardinality": "many", "peers": members}],
        )
    )
    assert operation.identity["members"] == [{"peer_kind": "OrgPerson", "identity": peer} for peer in members]


def test_a_many_peer_identity_component_disagreeing_with_the_reference_peers_is_rejected() -> None:
    """A many-valued identity component must name exactly the reference's peers, in order."""
    identity = {
        "name": "team-a",
        "members": [{"peer_kind": "OrgPerson", "identity": {"name": "alice"}}],
    }
    with pytest.raises(ValidationError):
        PlannedOperation(
            **_operation(
                kind="OrgTeam",
                identity=identity,
                payload={"name": "team-a"},
                relationships=[
                    {
                        "field": "members",
                        "peer_kind": "OrgPerson",
                        "cardinality": "many",
                        "peers": [{"name": "alice"}, {"name": "mallory"}],
                    }
                ],
            )
        )


def test_value_agreement_compares_canonical_values_not_raw_ones() -> None:
    """A payload value in a pre-canonical shape agrees once normalized (PD-002)."""
    moment = datetime(2026, 7, 26, 18, 4, 11, tzinfo=timezone.utc)
    identity = {"seen_at": moment.isoformat()}
    operation = PlannedOperation(**_operation(kind="AuditEntry", identity=identity, payload={"seen_at": moment}))
    assert operation.identity["seen_at"] == "2026-07-26T18:04:11+00:00"


# ======================================================================================
# Action vocabulary
# ======================================================================================


@pytest.mark.parametrize("action", list(ACTIONS))
def test_every_declared_action_is_accepted(action: str) -> None:
    """The closed vocabulary is exactly `ACTIONS`."""
    assert PlannedOperation(**_operation(action=action)).action == action


@pytest.mark.parametrize(
    "action",
    [
        pytest.param("upsert", id="plausible but undeclared"),
        pytest.param("CREATE", id="wrong case"),
        pytest.param("", id="empty"),
        pytest.param("noop", id="a later version's action"),
    ],
)
def test_an_action_outside_the_vocabulary_raises_the_named_error(action: str) -> None:
    """FR-017's genuinely-unsupported class, refused while reading (AD055).

    Not pydantic's `ValidationError`: the taxonomy names its own class, and the message
    names the recorded identifier, the action found and the recognized set (AD059).
    """
    record = _operation(action="create")
    record["action"] = action
    with pytest.raises(UnsupportedOperationActionError) as excinfo:
        PlannedOperation(**record)
    message = str(excinfo.value)
    assert record["operation_id"] in message
    assert repr(action) in message
    for recognized in ACTIONS:
        assert recognized in message
    assert excinfo.value.next_action


# ======================================================================================
# `RelationshipReference` cardinality
# ======================================================================================


@pytest.mark.parametrize("peer_count", [0, 2, 3])
def test_cardinality_one_requires_exactly_one_peer(peer_count: int) -> None:
    """A `one` reference naming zero or several peers is a corrupt record."""
    peers = [{"name": f"dc{index}"} for index in range(peer_count)]
    with pytest.raises(ValidationError):
        RelationshipReference(**_reference(cardinality="one", peers=peers))


def test_cardinality_one_accepts_exactly_one_peer() -> None:
    """The other half."""
    reference = RelationshipReference(**_reference(cardinality="one", peers=[SITE_PEER]))
    assert len(reference.peers) == 1


@pytest.mark.parametrize("peer_count", [0, 1, 2, 5])
def test_cardinality_many_accepts_any_peer_count(peer_count: int) -> None:
    """A `many` reference may name none: that is a deliberately empty peer set."""
    peers = [{"name": f"dc{index}"} for index in range(peer_count)]
    reference = RelationshipReference(**_reference(cardinality="many", peers=peers))
    assert len(reference.peers) == peer_count


def test_cardinality_is_a_closed_vocabulary() -> None:
    """Only `one` and `many` exist."""
    with pytest.raises(ValidationError):
        RelationshipReference(**_reference(cardinality="zero_or_one"))


def test_relationship_reference_rejects_unknown_fields() -> None:
    """A reference is a closed field set, like the operation that carries it."""
    record: dict[str, Any] = _reference()
    record["batch"] = "b1"
    with pytest.raises(ValidationError):
        RelationshipReference(**record)


# ======================================================================================
# Absent `relationships` and an empty list are distinct (FR-028.2)
# ======================================================================================


def test_absent_relationships_and_an_empty_list_are_distinct() -> None:
    """`None` means "carries no reference at all"; `[]` means "the peer set is empty"."""
    absent = PlannedOperation(**_operation())
    empty = PlannedOperation(**_operation(relationships=[]))

    assert absent.relationships is None
    assert empty.relationships == []
    assert absent.relationships != empty.relationships
    assert absent.model_dump() != empty.model_dump()


def test_neither_case_is_emitted_for_the_other_on_a_round_trip() -> None:
    """The distinction survives the JSON encoding the artifact actually uses."""
    absent = PlannedOperation(**_operation())
    empty = PlannedOperation(**_operation(relationships=[]))

    absent_json = json.loads(absent.model_dump_json())
    empty_json = json.loads(empty.model_dump_json())

    assert absent_json["relationships"] is None
    assert empty_json["relationships"] == []

    assert PlannedOperation(**absent_json).relationships is None
    assert PlannedOperation(**empty_json).relationships == []


def test_an_empty_many_reference_is_not_the_same_as_an_absent_reference() -> None:
    """One level down: an empty `peers` under `many` is a real, replace-to-empty value."""
    with_empty_peers = PlannedOperation(**_operation(relationships=[_reference("tags", cardinality="many", peers=[])]))
    without_any = PlannedOperation(**_operation())
    assert with_empty_peers.relationships is not None
    assert with_empty_peers.relationships[0].peers == []
    assert without_any.relationships is None


# ======================================================================================
# FR-026 — no field at either level groups operations into write units
# ======================================================================================

# The permitted field sets, enumerated so a later addition fails this test rather than
# quietly introducing a grouping key the reader would honour.
PLANNED_OPERATION_FIELDS = {"operation_id", "action", "kind", "identity", "tier", "payload", "relationships"}
RELATIONSHIP_REFERENCE_FIELDS = {"field", "peer_kind", "cardinality", "peers"}
PLAN_MANIFEST_FIELDS = {
    "format_version",
    "run_id",
    "created_at",
    "config_version",
    "source_snapshot",
    "operations_count",
    "delete_operations_computed",
    "plan_checksum",
    # Additive, FIX-005 (spec 002): the effective destination the plan is bound to. An
    # identity to compare at apply time, not a grouping of operations into write units.
    "destination_binding",
}

# Vocabulary a grouping field would plausibly be named with. Belt and braces beside the
# exact field sets above: an added field named `batch_id` fails twice.
GROUPING_VOCABULARY = (
    "batch",
    "group",
    "chunk",
    "bundle",
    "wave",
    "transaction",
    "txn",
    "unit",
    "commit",
    "partition",
    "stage",
)


@pytest.mark.parametrize(
    ("model", "permitted"),
    [
        pytest.param(PlannedOperation, PLANNED_OPERATION_FIELDS, id="PlannedOperation"),
        pytest.param(RelationshipReference, RELATIONSHIP_REFERENCE_FIELDS, id="RelationshipReference"),
        pytest.param(PlanManifest, PLAN_MANIFEST_FIELDS, id="PlanManifest"),
    ],
)
def test_field_set_is_exactly_the_permitted_one(model: type[BaseModel], permitted: set[str]) -> None:
    """FR-026 at the type level: the field set is enumerated, so an addition trips this."""
    assert set(model.model_fields) == permitted


@pytest.mark.parametrize(
    "model",
    [
        pytest.param(PlannedOperation, id="PlannedOperation"),
        pytest.param(RelationshipReference, id="RelationshipReference"),
        pytest.param(PlanManifest, id="PlanManifest"),
    ],
)
def test_no_field_name_expresses_a_grouping(model: type[BaseModel]) -> None:
    """No field at either level groups operations into write units (FR-026)."""
    offenders = [name for name in model.model_fields if any(token in name.lower() for token in GROUPING_VOCABULARY)]
    assert offenders == []


def test_tier_is_an_ordering_not_a_grouping() -> None:
    """`tier` orders operations; nothing reads it as a write-unit boundary.

    It is a plain non-negative integer on the operation with no companion field — no count,
    no barrier, no membership list — so there is nothing for a consumer to group on.
    """
    assert PlannedOperation.model_fields["tier"].annotation is int
    with pytest.raises(ValidationError):
        PlannedOperation(**_operation(tier=-1))
    assert PlannedOperation(**_operation(tier=7)).tier == 7


# ======================================================================================
# Extra-field asymmetry: the manifest tolerates, the operation does not
# ======================================================================================


def _manifest(**overrides: Any) -> dict[str, Any]:  # noqa: ANN401 — a raw manifest mapping is heterogeneous
    """A valid raw manifest mapping."""
    record: dict[str, Any] = {
        "format_version": PLAN_FORMAT_VERSION,
        "run_id": "20260726T1804-9f3ac210",
        "created_at": "2026-07-26T18:04:11.512034+00:00",
        "config_version": "5f2c",
        "source_snapshot": [{"path": "A/BuiltinTag.parquet", "digest": "7e10", "row_count": 12}],
        "operations_count": 2,
        "delete_operations_computed": True,
        "plan_checksum": "a91c",
    }
    record.update(overrides)
    return record


def test_plan_manifest_tolerates_unknown_fields() -> None:
    """FR-027's forward-compatibility carve-out: a later outcome adds a field here."""
    manifest = PlanManifest(**_manifest(schema_fingerprint="fp-1"))
    assert manifest.schema_fingerprint == "fp-1"  # ty: ignore[unresolved-attribute]


def test_plan_manifest_preserves_unknown_fields_verbatim() -> None:
    """Tolerated is not enough — the value must survive the round trip unchanged."""
    manifest = PlanManifest(**_manifest(schema_fingerprint={"kinds": ["BuiltinTag"], "hash": "abc"}))
    dumped = manifest.model_dump()
    assert dumped["schema_fingerprint"] == {"kinds": ["BuiltinTag"], "hash": "abc"}


def test_planned_operation_rejects_unknown_fields() -> None:
    """A closed field set: an operation this version cannot fully interpret is torn."""
    record: dict[str, Any] = _operation()
    record["schema_fingerprint"] = "fp-1"
    with pytest.raises(ValidationError):
        PlannedOperation(**record)


def test_plan_manifest_still_requires_its_declared_fields() -> None:
    """`extra="allow"` does not make the declared fields optional.

    `destination_binding` is the one exception by design: it is FIX-005's **additive**
    field, and manifests written before it existed must keep validating (spec 002).
    """
    for missing in PLAN_MANIFEST_FIELDS - {"destination_binding"}:
        record = _manifest()
        del record[missing]
        with pytest.raises(ValidationError):
            PlanManifest(**record)


def test_plan_manifest_config_version_must_be_printable_ascii() -> None:
    """The manifest enforces the same opaque-value rule the config-version module does."""
    with pytest.raises(ValidationError):
        PlanManifest(**_manifest(config_version=""))
    with pytest.raises(ValidationError):
        PlanManifest(**_manifest(config_version="café"))


def test_operations_count_and_row_count_are_non_negative() -> None:
    """Counts are counts."""
    with pytest.raises(ValidationError):
        PlanManifest(**_manifest(operations_count=-1))
    with pytest.raises(ValidationError):
        SourceSnapshotRecord(path="A/x.parquet", digest="d", row_count=-1)


# ======================================================================================
# The remaining record types
# ======================================================================================


def test_plan_summary_requires_both_disclosure_fields() -> None:
    """AD056: without `delete_operations_computed`, "no deletes" and "never computed" merge."""
    for missing in ("delete_operations_computed", "deletes_not_executed"):
        record: dict[str, Any] = {
            "by_action": {"create": 1},
            "by_kind": {"BuiltinTag": 1},
            "total": 1,
            "delete_operations_computed": True,
            "deletes_not_executed": 0,
        }
        del record[missing]
        with pytest.raises(ValidationError):
            PlanSummary(**record)


def test_plan_summary_accepts_a_complete_record() -> None:
    """The shape a review renders."""
    summary = PlanSummary(
        by_action={"create": 2, "delete": 1},
        by_kind={"BuiltinTag": 3},
        total=3,
        delete_operations_computed=True,
        deletes_not_executed=1,
    )
    assert summary.deletes_not_executed == 1
    assert summary.delete_operations_computed is True


def test_verification_failure_requires_a_non_empty_next_action() -> None:
    """AD059: every failure states what to do next."""
    with pytest.raises(ValidationError):
        VerificationFailure(check="plan_checksum", run_id="r1", next_action="")


def test_verification_failure_check_is_a_closed_vocabulary() -> None:
    """A check name outside the declared set is not a verification failure this code knows."""
    with pytest.raises(ValidationError):
        VerificationFailure(check="freshness", run_id="r1", next_action="do something")  # ty: ignore[invalid-argument-type]


# ======================================================================================
# ApplyRecord — the count is derived state, and cannot contradict the list it counts
# ======================================================================================


def test_an_apply_record_agreeing_with_itself_constructs() -> None:
    """The shape every in-repo construction site builds, and the defaults `ApplyRecord()` uses."""
    record = ApplyRecord(
        applied_operations=("op_a",), skipped_delete_operations=("op_b", "op_c"), skipped_delete_count=2
    )

    assert record.as_summary_keys() == {
        "applied_operations": ["op_a"],
        "skipped_delete_operations": ["op_b", "op_c"],
        "skipped_delete_count": 2,
    }
    assert ApplyRecord().as_summary_keys() == {
        "applied_operations": [],
        "skipped_delete_operations": [],
        "skipped_delete_count": 0,
    }


@pytest.mark.parametrize(
    ("skipped_operations", "count"),
    [
        (("op_a", "op_b", "op_c"), 0),
        ((), -7),
        ((), 3),
        (("op_a",), 2),
    ],
    ids=["three-counted-as-zero", "negative-count", "count-without-identifiers", "undercount"],
)
def test_a_count_that_contradicts_the_skipped_list_is_refused(skipped_operations: tuple[str, ...], count: int) -> None:
    """The count is the length of the list, so a record that disagrees cannot be built.

    `skipped_delete_count` is one of the three serialized keys (AD062), so it stays a field
    rather than being derived at render time — but a record built with a count that disagrees
    with its list has already lost which of the two is true, and it is a run record: the only
    account of what an apply did. The contradiction is refused where it is introduced.
    """
    with pytest.raises(ValueError, match="skipped_delete_count"):
        ApplyRecord(skipped_delete_operations=skipped_operations, skipped_delete_count=count)
