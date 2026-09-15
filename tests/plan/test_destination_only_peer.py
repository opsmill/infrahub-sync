"""A peer absent from the loaded source store is recorded as a literal identity.

The saved-plan path derives a reference's peer kind from the **source** store, so a peer no
candidate kind holds there refuses the plan (`SourcePeerUnresolvedError.absent`). Infrahub's
built-in `default` IP namespace is the case the rule exists for: a prefix with no VRF names
`default`, and a NetBox instance normally has no VRF of that name. Where one does, the
mapping loads it and the stored-peer path applies instead — a case pinned below — so the rule
turns on absence from the loaded store, not on what the source could in principle hold.

The narrow rule under test records such a peer literally, and only where a literal identity
is provably the identity the destination matches on: one candidate peer kind, one direct
single-field identifier declared identically by every mapping entry for that kind, a
destination human-friendly ID that is exactly that one field, and a value that survives
canonicalisation as a scalar. Every other missing-peer case keeps refusing, which is what the
refusal cases below hold in place — the rule is a narrowing of one arm, not a fallback.

The doubles come from the two modules that already own them rather than being restated here,
so a change to the comparison fakes or to the planned-write client is felt in one place.
"""

from __future__ import annotations

import logging
import math
from typing import TYPE_CHECKING, Any, ClassVar

import pytest
from diffsync import Adapter, DiffSyncModel

from infrahub_sync import SchemaMappingField, SchemaMappingModel
from infrahub_sync.plan.canonical import canonical_json_bytes
from infrahub_sync.plan.derive import operations_from_diff, warn_missing_convergence_key
from infrahub_sync.plan.errors import PeerNotFoundError, SourcePeerUnresolvedError
from infrahub_sync.utils import get_instance
from tests.adapters.test_infrahub_planned_write import (
    PeerResolver,
    RecordingClient,
    make_adapter,
    server_operation,
)
from tests.test_generated_examples import REPO_ROOT, _load_snapshot
from tests.test_potenda_plan_artifact import (
    _FakeAdapter,
    _FakeDiff,
    _FakeElement,
    _FakeRecord,
    build_config,
    mapping_entry,
    references_by_field,
    resolver,
    schema_node,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from infrahub_sync import SyncInstance
    from infrahub_sync.plan.models import PlannedOperation

# The three kinds the NetBox namespace mapping puts in play: the namespace a prefix is
# identified by, the VRF it is also loaded as, and the prefix itself.
NAMESPACE_KINDS: tuple[str, ...] = ("IpamNamespace", "IpamVRF", "IpamPrefix")

# What Infrahub declares for the built-in namespace: one human-friendly-ID component, naming
# the one attribute the mapping identifies it by.
NAMESPACE_SCHEMA: dict[str, Any] = {
    "IpamNamespace": schema_node(human_friendly_id=["name__value"], uniqueness_constraints=[["name__value"]]),
    "IpamPrefix": schema_node(
        human_friendly_id=["prefix__value", "ip_namespace__name__value"],
        uniqueness_constraints=[["prefix__value", "ip_namespace__name__value"]],
    ),
}

BUILT_IN_NAMESPACE = "default"

# The rule warns from its own module rather than from `derive`.
RULE_LOGGER = "infrahub_sync.plan.destination_only_peer"


def literal_peer_warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    """Every warning the destination-only-peer rule emitted."""
    return [
        record.getMessage()
        for record in caplog.records
        if record.name == RULE_LOGGER and record.levelno >= logging.WARNING
    ]


def namespace_entry(
    *,
    identifiers: Sequence[str] = ("name",),
    fields: Mapping[str, str | None] | None = None,
) -> SchemaMappingModel:
    """One `IpamNamespace` mapping entry, identified by `name` unless a case says otherwise."""
    return mapping_entry(
        "IpamNamespace",
        identifiers=list(identifiers),
        fields=dict(fields if fields is not None else {"name": None, "description": None}),
    )


def namespace_config(
    *,
    namespace_entries: Sequence[SchemaMappingModel] | None = None,
    prefix_references: Sequence[str] = ("IpamNamespace",),
) -> SyncInstance:
    """A configuration mapping `IpamPrefix.ip_namespace` onto the namespace kind.

    `prefix_references` declares one `IpamPrefix` entry per reference, which is how the
    qualified path produces more than one candidate peer kind for a single field.
    """
    return build_config(
        order=NAMESPACE_KINDS,
        schema_mapping=[
            *(namespace_entries if namespace_entries is not None else [namespace_entry()]),
            mapping_entry("IpamVRF", identifiers=["name"], fields={"name": None, "description": None}),
            *(
                mapping_entry(
                    "IpamPrefix",
                    identifiers=["prefix", "ip_namespace"],
                    fields={"prefix": None, "description": None, "ip_namespace": reference},
                )
                for reference in prefix_references
            ),
        ],
    )


def prefix_element(namespace: Any, *, prefix: str = "10.0.0.0/24") -> _FakeElement:  # noqa: ANN401
    """A create of one prefix whose `ip_namespace` names `namespace`."""
    return _FakeElement(
        kind="IpamPrefix",
        name=f"{prefix}__{namespace}",
        keys={"prefix": prefix, "ip_namespace": namespace},
        source_attrs={"description": "example"},
    )


def derive(
    *,
    elements: Sequence[_FakeElement],
    config: SyncInstance | None = None,
    source_records: Sequence[_FakeRecord] = (),
    destination_schema: Mapping[str, Any] | None = None,
) -> list[PlannedOperation]:
    """Derive the operations one comparison proposes, against a destination with a schema.

    `destination_schema` defaults to the namespace schema; passing `None` explicitly is a
    different case — a destination exposing no schema at all (AD052) — and is spelled by the
    `no-accessor` case below rather than reached by accident.
    """
    source = _FakeAdapter("source", source_records)
    destination = _FakeAdapter("destination", schema=destination_schema)
    return operations_from_diff(
        _FakeDiff({"IpamPrefix": list(elements)}),
        config=config if config is not None else namespace_config(),
        tier_of=resolver(top_level=NAMESPACE_KINDS),
        source_adapter=source,
        destination_adapter=destination,
    )


# =======================================================================================
# The rule: a literal identity, recorded once, warned about once
# =======================================================================================


def test_a_destination_only_peer_is_recorded_as_a_literal_identity(caplog: pytest.LogCaptureFixture) -> None:
    """The peer the source cannot hold is named by the one field its destination key uses."""
    with caplog.at_level(logging.DEBUG, logger=RULE_LOGGER):
        operations = derive(
            elements=[prefix_element(BUILT_IN_NAMESPACE)],
            destination_schema=NAMESPACE_SCHEMA,
        )

    (operation,) = operations
    assert operation.identity == {
        "prefix": "10.0.0.0/24",
        "ip_namespace": {"peer_kind": "IpamNamespace", "identity": {"name": BUILT_IN_NAMESPACE}},
    }
    reference = references_by_field(operation)["ip_namespace"]
    assert reference.peer_kind == "IpamNamespace"
    assert reference.peers == [{"name": BUILT_IN_NAMESPACE}]


def test_the_literal_peer_is_warned_about_naming_the_kind_field_and_value(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The operator has to be able to find which reference was recorded without a probe."""
    with caplog.at_level(logging.DEBUG, logger=RULE_LOGGER):
        derive(elements=[prefix_element(BUILT_IN_NAMESPACE)], destination_schema=NAMESPACE_SCHEMA)

    (message,) = literal_peer_warnings(caplog)
    assert "IpamPrefix" in message
    assert "ip_namespace" in message
    assert "IpamNamespace" in message
    assert BUILT_IN_NAMESPACE in message


def test_two_operations_naming_one_literal_peer_warn_once(caplog: pytest.LogCaptureFixture) -> None:
    """Every no-VRF prefix names the same namespace, so a real plan would flood the log."""
    with caplog.at_level(logging.DEBUG, logger=RULE_LOGGER):
        operations = derive(
            elements=[
                prefix_element(BUILT_IN_NAMESPACE, prefix="10.0.0.0/24"),
                prefix_element(BUILT_IN_NAMESPACE, prefix="10.0.1.0/24"),
            ],
            destination_schema=NAMESPACE_SCHEMA,
        )

    assert len(operations) == 2
    assert len(literal_peer_warnings(caplog)) == 1


def test_literal_values_with_one_python_equality_but_distinct_encodings_warn_separately(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The de-duplication key is the canonical encoding, not the Python value.

    `False == 0` and `True == 1 == 1.0` in Python, so a `set` of raw values would collapse
    two references the artifact encodes — and the destination therefore matches — as
    different literals, and the second would go unmentioned.
    """
    false, zero = False, 0
    with caplog.at_level(logging.DEBUG, logger=RULE_LOGGER):
        derive(
            elements=[
                prefix_element(false, prefix="10.0.0.0/24"),
                prefix_element(zero, prefix="10.0.1.0/24"),
            ],
            destination_schema=NAMESPACE_SCHEMA,
        )

    messages = literal_peer_warnings(caplog)
    assert len(messages) == 2, f"`False` and `0` encode differently, so both must be named: {messages}"
    assert canonical_json_bytes(false) != canonical_json_bytes(zero)


@pytest.mark.parametrize(
    "value",
    [pytest.param(0, id="zero"), pytest.param(False, id="false")],
)
def test_a_falsy_but_usable_literal_is_recorded(value: Any, caplog: pytest.LogCaptureFixture) -> None:  # noqa: ANN401
    """Falsiness is not the test: the destination matches on `0` and `False` perfectly well."""
    with caplog.at_level(logging.DEBUG, logger=RULE_LOGGER):
        (operation,) = derive(elements=[prefix_element(value)], destination_schema=NAMESPACE_SCHEMA)

    assert operation.identity["ip_namespace"] == {"peer_kind": "IpamNamespace", "identity": {"name": value}}


# =======================================================================================
# Every other missing-peer case still refuses
# =======================================================================================


def test_more_than_one_candidate_peer_kind_still_refuses() -> None:
    """With two candidates the mapping does not say which kind a literal would name."""
    config = namespace_config(prefix_references=("IpamNamespace", "IpamVRF"))

    with pytest.raises(SourcePeerUnresolvedError):
        derive(elements=[prefix_element(BUILT_IN_NAMESPACE)], config=config, destination_schema=NAMESPACE_SCHEMA)


def test_a_composite_identifier_on_the_peer_kind_still_refuses() -> None:
    """A two-component identity cannot be formed from the one value the field carries."""
    config = namespace_config(
        namespace_entries=[namespace_entry(identifiers=("name", "description"))],
    )

    with pytest.raises(SourcePeerUnresolvedError):
        derive(elements=[prefix_element(BUILT_IN_NAMESPACE)], config=config, destination_schema=NAMESPACE_SCHEMA)


def test_a_reference_bearing_identifier_on_the_peer_kind_still_refuses() -> None:
    """An identifier that is itself a reference names a peer, not a literal value."""
    config = namespace_config(
        namespace_entries=[namespace_entry(fields={"name": "IpamVRF", "description": None})],
    )

    with pytest.raises(SourcePeerUnresolvedError):
        derive(elements=[prefix_element(BUILT_IN_NAMESPACE)], config=config, destination_schema=NAMESPACE_SCHEMA)


def test_two_entries_for_the_peer_kind_with_divergent_identifiers_still_refuse() -> None:
    """The mapping may declare a kind more than once, and the entries must agree.

    `reference_candidates` aggregates every entry for a kind, and the shipped NetBox example
    declares `DcimDevice` twice — so reading the identifier off whichever entry came first
    would make the recorded identity depend on mapping order.
    """
    config = namespace_config(
        namespace_entries=[
            namespace_entry(identifiers=("name",)),
            namespace_entry(identifiers=("description",)),
        ],
    )

    with pytest.raises(SourcePeerUnresolvedError):
        derive(elements=[prefix_element(BUILT_IN_NAMESPACE)], config=config, destination_schema=NAMESPACE_SCHEMA)


def test_no_mapping_entry_for_the_peer_kind_still_refuses() -> None:
    """A candidate kind the mapping never describes declares no identifier to use."""
    config = namespace_config(namespace_entries=[])

    with pytest.raises(SourcePeerUnresolvedError):
        derive(elements=[prefix_element(BUILT_IN_NAMESPACE)], config=config, destination_schema=NAMESPACE_SCHEMA)


@pytest.mark.parametrize(
    "human_friendly_id",
    [
        pytest.param(["description__value"], id="names-another-attribute"),
        pytest.param(["name__value", "description__value"], id="composite"),
        pytest.param(None, id="none-declared"),
    ],
)
def test_a_destination_key_that_is_not_exactly_the_identifier_still_refuses(
    human_friendly_id: list[str] | None,
) -> None:
    """The literal is only the identity the destination matches on when the two coincide."""
    schema = {
        "IpamNamespace": schema_node(human_friendly_id=human_friendly_id, uniqueness_constraints=[["name__value"]])
    }

    with pytest.raises(SourcePeerUnresolvedError):
        derive(elements=[prefix_element(BUILT_IN_NAMESPACE)], destination_schema=schema)


def test_a_destination_exposing_no_schema_still_refuses() -> None:
    """Without a destination schema there is no key to check the identifier against (AD052)."""
    with pytest.raises(SourcePeerUnresolvedError):
        derive(elements=[prefix_element(BUILT_IN_NAMESPACE)], destination_schema=None)


def test_a_destination_schema_without_the_peer_kind_still_refuses() -> None:
    """A kind the destination does not declare exposes no human-friendly ID to match."""
    schema = {"IpamPrefix": NAMESPACE_SCHEMA["IpamPrefix"]}

    with pytest.raises(SourcePeerUnresolvedError):
        derive(elements=[prefix_element(BUILT_IN_NAMESPACE)], destination_schema=schema)


@pytest.mark.parametrize(
    "value",
    [
        pytest.param({"name": "default"}, id="mapping"),
        pytest.param([["default"]], id="nested-sequence"),
        pytest.param("", id="empty-string"),
        pytest.param("   ", id="whitespace-only-string"),
        pytest.param(float("nan"), id="nan"),
        pytest.param(float("inf"), id="infinity"),
        pytest.param(float("-inf"), id="negative-infinity"),
    ],
)
def test_a_value_that_is_not_a_usable_canonical_scalar_still_refuses(value: Any) -> None:  # noqa: ANN401
    """The literal reaches `operation_id` and the artifact, so it must encode there.

    A non-finite float passes the canonical-value table and the usability check but is
    refused by `canonical_json_bytes`, which would move the failure from this refusal to the
    identifier derivation, where nothing names the reference that caused it.
    """
    with pytest.raises(SourcePeerUnresolvedError):
        derive(elements=[prefix_element(value)], destination_schema=NAMESPACE_SCHEMA)


def test_a_list_valued_reference_stays_the_cardinality_many_path(caplog: pytest.LogCaptureFixture) -> None:
    """A list is a peer **set**, so each member is judged on its own — including this rule.

    The scalar check applies to the value naming one peer, which is what a member is; the
    nested-sequence case above is the one where a member is itself not a scalar.
    """
    with caplog.at_level(logging.DEBUG, logger=RULE_LOGGER):
        (operation,) = derive(elements=[prefix_element([BUILT_IN_NAMESPACE])], destination_schema=NAMESPACE_SCHEMA)

    reference = references_by_field(operation)["ip_namespace"]
    assert reference.cardinality == "many"
    assert reference.peers == [{"name": BUILT_IN_NAMESPACE}]


def test_the_non_finite_floats_this_rule_refuses_are_the_ones_the_encoder_refuses() -> None:
    """The predicate and the encoder must agree, or the refusal moves somewhere unhelpful."""
    for value in (float("nan"), float("inf"), float("-inf")):
        assert not math.isfinite(value)
        with pytest.raises(Exception, match="non-finite float"):
            canonical_json_bytes(value)


# =======================================================================================
# The stored-peer path is untouched
# =======================================================================================


def stored_peer_operations(namespace: str) -> list[PlannedOperation]:
    """Derive one prefix whose namespace **is** in the source store."""
    return derive(
        elements=[prefix_element(namespace)],
        source_records=[_FakeRecord("IpamNamespace", {"name": namespace}, {"description": "from the source"})],
        destination_schema=NAMESPACE_SCHEMA,
    )


def test_a_peer_present_in_the_source_store_is_recorded_from_the_store(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The ordinary path derives as before and says nothing."""
    with caplog.at_level(logging.DEBUG, logger=RULE_LOGGER):
        (operation,) = stored_peer_operations("mgmt")

    assert operation.identity["ip_namespace"] == {"peer_kind": "IpamNamespace", "identity": {"name": "mgmt"}}
    assert literal_peer_warnings(caplog) == [], (
        "A stored peer is not a destination-only peer and must not be warned about."
    )


def test_a_source_namespace_literally_named_default_resolves_as_a_stored_peer(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A NetBox VRF named `default` is loaded as a namespace, so the rule is never entered.

    The mapping's accepted limitation — such a VRF shares Infrahub's built-in namespace —
    holds because the probe finds it, not because the literal happens to match.
    """
    with caplog.at_level(logging.DEBUG, logger=RULE_LOGGER):
        (stored,) = stored_peer_operations(BUILT_IN_NAMESPACE)

    assert literal_peer_warnings(caplog) == []
    with caplog.at_level(logging.DEBUG, logger=RULE_LOGGER):
        (literal,) = derive(elements=[prefix_element(BUILT_IN_NAMESPACE)], destination_schema=NAMESPACE_SCHEMA)

    assert stored.operation_id == literal.operation_id, (
        "Both name the same destination object, so the recorded identity — and the identifier "
        "derived from it — must not depend on which path recorded it."
    )


def test_the_recorded_identity_bytes_are_unchanged_for_a_stored_peer() -> None:
    """The rule adds a branch to one arm; it must not re-encode the ordinary result."""
    (operation,) = stored_peer_operations("mgmt")

    assert canonical_json_bytes(operation.identity) == (
        b'{"ip_namespace":{"identity":{"name":"mgmt"},"peer_kind":"IpamNamespace"},"prefix":"10.0.0.0/24"}'
    )


# =======================================================================================
# Apply resolves the literal by destination key, and refuses a typo without writing
# =======================================================================================


def test_a_literal_identity_the_destination_cannot_match_fails_before_any_mutation() -> None:
    """A mistyped literal is caught by the resolver, which is why a literal is safe to record.

    The apply-time contract is unchanged by this rule — `PeerResolver` already refuses zero
    and multiple matches — so this case guards it rather than exercising anything new.
    """
    client = RecordingClient()
    client.filter_results = [[]]
    adapter = make_adapter(client)

    with pytest.raises(PeerNotFoundError):
        adapter.apply_planned_operation(
            operation=server_operation("server-a", site_name="defualt"),
            peers=PeerResolver(adapter),
        )

    assert client.mutations == [], "No mutation may be issued once a recorded peer cannot be resolved."


# =======================================================================================
# The shipped NetBox example, against its own committed destination schema
# =======================================================================================

# The example's two namespaced kinds, with the NetBox field each is keyed on and a value.
EXAMPLE_NAMESPACED_KINDS = [
    pytest.param("IpamPrefix", "prefix", "10.0.0.0/24", id="prefix"),
    pytest.param("IpamIPAddress", "address", "10.0.0.1/32", id="address"),
]


def example_config() -> SyncInstance:
    """The shipped `from-netbox` configuration, parsed as the engine parses it."""
    instance = get_instance(name="from-netbox", directory=str(REPO_ROOT / "examples"))
    assert instance is not None
    return instance


def example_destination() -> _FakeAdapter:
    """A destination exposing the example's committed schema snapshot.

    The snapshot is the capture the generator is pinned against, so the human-friendly IDs
    the rule reads here are Infrahub's own rather than a double's.
    """
    return _FakeAdapter("destination", schema=_load_snapshot("netbox_example_schema.json"))


def example_element(kind: str, key_field: str, key_value: str, namespace: str) -> _FakeElement:
    """A create of one prefix or address in `namespace`, as the comparison yields it."""
    return _FakeElement(
        kind=kind,
        name=f"{key_value}__{namespace}",
        keys={key_field: key_value, "ip_namespace": namespace},
        source_attrs={"description": "from the demo dataset", "status": "active"},
    )


def derive_example(kind: str, key_field: str, key_value: str, namespace: str, *, in_source: bool) -> PlannedOperation:
    """Derive that one operation, with the namespace present in the source store or not."""
    records = [_FakeRecord("IpamNamespace", {"name": namespace}, {"description": "a VRF"})] if in_source else []
    source = _FakeAdapter("source", records)
    (operation,) = operations_from_diff(
        _FakeDiff({kind: [example_element(kind, key_field, key_value, namespace)]}),
        config=example_config(),
        tier_of=resolver(top_level=("IpamNamespace", "IpamVRF", kind)),
        source_adapter=source,
        destination_adapter=example_destination(),
    )
    return operation


@pytest.mark.parametrize(("kind", "key_field", "key_value"), EXAMPLE_NAMESPACED_KINDS)
def test_the_example_records_the_built_in_namespace_literally_for_a_record_with_no_vrf(
    kind: str,
    key_field: str,
    key_value: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The case that refused before: no NetBox VRF means no source `IpamNamespace/default`."""
    with caplog.at_level(logging.DEBUG, logger=RULE_LOGGER):
        operation = derive_example(kind, key_field, key_value, BUILT_IN_NAMESPACE, in_source=False)

    assert operation.identity["ip_namespace"] == {
        "peer_kind": "IpamNamespace",
        "identity": {"name": BUILT_IN_NAMESPACE},
    }
    assert len(literal_peer_warnings(caplog)) == 1


@pytest.mark.parametrize(("kind", "key_field", "key_value"), EXAMPLE_NAMESPACED_KINDS)
def test_the_example_records_a_vrf_backed_namespace_from_the_source_store(
    kind: str,
    key_field: str,
    key_value: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A record in a VRF names a namespace the mapping loads, so it resolves as a stored peer."""
    with caplog.at_level(logging.DEBUG, logger=RULE_LOGGER):
        operation = derive_example(kind, key_field, key_value, "mgmt", in_source=True)

    assert operation.identity["ip_namespace"] == {"peer_kind": "IpamNamespace", "identity": {"name": "mgmt"}}
    assert literal_peer_warnings(caplog) == []


@pytest.mark.parametrize(("kind", "key_field", "key_value"), EXAMPLE_NAMESPACED_KINDS)
def test_the_example_create_keys_itself_with_the_literal_namespace(
    kind: str,
    key_field: str,
    key_value: str,
) -> None:
    """The create proof is where `UnkeyedCreateRefusedError` is raised, so it has to run.

    Deriving the operation is not enough: the destination matches a create on the components
    its identity supplies, and the example's human-friendly ID for both kinds crosses the
    namespace relationship. A literal that did not satisfy that key would be refused here.
    """
    operation = derive_example(kind, key_field, key_value, BUILT_IN_NAMESPACE, in_source=False)

    warn_missing_convergence_key(destination=example_destination(), operations=[operation])


# =======================================================================================
# The installed DiffSync store, not a permissive double
# =======================================================================================

# `_FakeStore` answers `get` for any identifier, which the real store does not: `BaseStore`
# uses a `str` identifier as the uid directly and expands anything else as the mapping of
# identifier keys (`create_unique_id(**identifier)`). A bool, a number or a sequence is
# neither, so the real store raises `TypeError` instead of reporting the peer missing. The
# cases below therefore drive derivation against the installed store, so what reaches the
# rule is what reaches it in a real plan.


class _RealNamespace(DiffSyncModel):
    """The peer kind, as the generator emits it."""

    _modelname = "IpamNamespace"
    _identifiers = ("name",)
    _attributes = ("description",)
    name: str
    description: str | None = None


class _RealVRF(DiffSyncModel):
    """A second candidate kind, for the ambiguous arm."""

    _modelname = "IpamVRF"
    _identifiers = ("name",)
    _attributes = ("description",)
    name: str
    description: str | None = None


class _RealSource(Adapter):
    """A source adapter carrying the installed `LocalStore`."""

    IpamNamespace = _RealNamespace
    IpamVRF = _RealVRF
    top_level: ClassVar[list[str]] = ["IpamNamespace", "IpamVRF"]


def derive_against_real_store(
    namespace: Any,  # noqa: ANN401
    *,
    stored: Sequence[DiffSyncModel] = (),
    config: SyncInstance | None = None,
    destination_schema: Mapping[str, Any] | None = None,
) -> list[PlannedOperation]:
    """Derive one prefix operation with the installed store on the source side."""
    source = _RealSource()
    for record in stored:
        source.add(record)
    return operations_from_diff(
        _FakeDiff({"IpamPrefix": [prefix_element(namespace)]}),
        config=config if config is not None else namespace_config(),
        tier_of=resolver(top_level=NAMESPACE_KINDS),
        source_adapter=source,
        destination_adapter=_FakeAdapter(
            "destination",
            schema=NAMESPACE_SCHEMA if destination_schema is None else destination_schema,
        ),
    )


@pytest.mark.parametrize(
    "value",
    [pytest.param(0, id="zero"), pytest.param(False, id="false"), pytest.param(1.5, id="finite-float")],
)
def test_a_canonical_scalar_the_real_store_cannot_look_up_is_recorded_literally(
    value: Any,  # noqa: ANN401
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Brief condition 4 holds against the installed store, not only against a double.

    These values cannot key a store lookup at all, so the peer is absent in the only sense
    the store can report — and absent is exactly the arm the rule narrows.
    """
    with caplog.at_level(logging.DEBUG, logger=RULE_LOGGER):
        (operation,) = derive_against_real_store(value)

    assert operation.identity["ip_namespace"] == {"peer_kind": "IpamNamespace", "identity": {"name": value}}
    assert len(literal_peer_warnings(caplog)) == 1


@pytest.mark.parametrize(
    "value",
    [
        pytest.param([["default"]], id="nested-sequence"),
        pytest.param(float("nan"), id="nan"),
        pytest.param(float("inf"), id="infinity"),
        pytest.param(float("-inf"), id="negative-infinity"),
        pytest.param({"name": "default"}, id="mapping"),
    ],
)
def test_a_value_the_rule_refuses_keeps_the_absent_error_against_the_real_store(
    value: Any,  # noqa: ANN401
) -> None:
    """The refusal must stay `SourcePeerUnresolvedError.absent`, not escape as a `TypeError`."""
    with pytest.raises(SourcePeerUnresolvedError):
        derive_against_real_store(value)


def test_a_string_peer_in_the_real_store_still_resolves_from_it() -> None:
    """The ordinary lookup is untouched: a `str` identifier is the store's own uid."""
    (operation,) = derive_against_real_store("mgmt", stored=[_RealNamespace(name="mgmt", description="a VRF")])

    assert operation.identity["ip_namespace"] == {"peer_kind": "IpamNamespace", "identity": {"name": "mgmt"}}


def test_a_mapping_identifier_still_reaches_the_real_store_and_resolves() -> None:
    """A mapping is a valid store identifier, so it must keep being looked up rather than
    classified away: `BaseStore` expands it into `create_unique_id(**identifier)`."""
    (operation,) = derive_against_real_store(
        {"name": "mgmt"}, stored=[_RealNamespace(name="mgmt", description="a VRF")]
    )

    assert operation.identity["ip_namespace"] == {"peer_kind": "IpamNamespace", "identity": {"name": "mgmt"}}


def test_an_absent_string_peer_the_rule_does_not_admit_still_refuses_against_the_real_store() -> None:
    """A missing string peer with no usable destination key is still the absent refusal."""
    schema = {"IpamNamespace": schema_node(human_friendly_id=None, uniqueness_constraints=[["name__value"]])}

    with pytest.raises(SourcePeerUnresolvedError):
        derive_against_real_store("default", destination_schema=schema)


def test_the_ambiguous_arm_still_raises_against_the_real_store() -> None:
    """A peer held under two candidate kinds cannot be named, rule or no rule."""
    config = namespace_config(prefix_references=("IpamNamespace", "IpamVRF"))

    with pytest.raises(SourcePeerUnresolvedError):
        derive_against_real_store(
            "shared",
            stored=[_RealNamespace(name="shared"), _RealVRF(name="shared")],
            config=config,
        )


# =======================================================================================
# The direct-identifier proof rejects a conflicting duplicate declaration
# =======================================================================================


def conflicting_duplicate_entry(references: Sequence[str | None]) -> SchemaMappingModel:
    """One entry declaring `name` once per item in `references`, each with that `reference`.

    `SchemaMappingModel` accepts a `fields` list that names one field twice and package
    validation does not reject it, so a proof over those declarations cannot let list order
    decide which one it reads.
    """
    return SchemaMappingModel(
        name="IpamNamespace",
        mapping="ipam.vrfs",
        identifiers=["name"],
        fields=[SchemaMappingField(name="name", mapping="name", reference=reference) for reference in references],
    )


@pytest.mark.parametrize(
    "references",
    [
        pytest.param((None, "IpamVRF"), id="direct-first"),
        pytest.param(("IpamVRF", None), id="reference-first"),
    ],
)
def test_a_conflicting_duplicate_identifier_declaration_refuses_in_either_order(
    references: Sequence[str | None],
) -> None:
    """Order must not decide it: one of the two declarations makes the identifier a peer."""
    config = namespace_config(namespace_entries=[conflicting_duplicate_entry(references)])

    with pytest.raises(SourcePeerUnresolvedError):
        derive(elements=[prefix_element(BUILT_IN_NAMESPACE)], config=config, destination_schema=NAMESPACE_SCHEMA)
