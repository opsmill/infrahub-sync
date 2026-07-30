"""T011 — canonical identity and operation-identifier derivation (FR-003, FR-028.3).

The four worked vectors are copied verbatim from
`dev/specs/archive/001-plan-artifact-saved-apply/contracts/plan-artifact-format.md`. Both halves of
each vector are asserted: the exact canonical **input bytes** and the resulting identifier.
Asserting only the identifier would let an input-shape change (say, a JSON object instead of
PD-001's array) pass unnoticed as long as both sides changed together.
"""

from __future__ import annotations

import re
from typing import Any

import pytest

from infrahub_sync.plan.canonical import canonical_json_bytes
from infrahub_sync.plan.identity import OPERATION_ID_PATTERN, canonical_identity, operation_id

# --------------------------------------------------------------------------------------
# The contract's worked test vectors: canonical input string -> identifier.
# --------------------------------------------------------------------------------------

CONTRACT_VECTORS: list[tuple[str, str, str, dict[str, Any], str, str]] = [
    (
        "create BuiltinTag",
        "create",
        "BuiltinTag",
        {"name": "prod"},
        '["create","BuiltinTag",{"name":"prod"}]',
        "op_3531c0d83d698fd1",
    ),
    (
        "update LocationRack with a reference component",
        "update",
        "LocationRack",
        {"name": "dc1-rack-a", "site": {"peer_kind": "LocationSite", "identity": {"name": "dc1"}}},
        '["update","LocationRack",{"name":"dc1-rack-a","site":{"identity":{"name":"dc1"},"peer_kind":"LocationSite"}}]',
        "op_42d8e04c060f61b8",
    ),
    (
        "create InterfacePhysical with a recursive reference component",
        "create",
        "InterfacePhysical",
        {
            "name": "Ethernet1",
            "device": {
                "peer_kind": "DcimDevice",
                "identity": {
                    "name": "dev1",
                    "location": {
                        "peer_kind": "LocationRack",
                        "identity": {
                            "name": "rack-a",
                            "site": {"peer_kind": "LocationSite", "identity": {"name": "dc1"}},
                        },
                    },
                },
            },
        },
        '["create","InterfacePhysical",{"device":{"identity":{"location":{"identity":{"name":"rack-a",'
        '"site":{"identity":{"name":"dc1"},"peer_kind":"LocationSite"}},"peer_kind":"LocationRack"},'
        '"name":"dev1"},"peer_kind":"DcimDevice"},"name":"Ethernet1"}]',
        "op_5dfe0d0bdc714b36",
    ),
    (
        "delete BuiltinTag obeys the same rule (AD049)",
        "delete",
        "BuiltinTag",
        {"name": "retired"},
        '["delete","BuiltinTag",{"name":"retired"}]',
        "op_ba078d0eae6c9fc3",
    ),
]

_VECTOR_PARAMS = [
    pytest.param(action, kind, identity, canonical_input, expected_id, id=label)
    for label, action, kind, identity, canonical_input, expected_id in CONTRACT_VECTORS
]


@pytest.mark.parametrize(("action", "kind", "identity", "canonical_input", "expected_id"), _VECTOR_PARAMS)
def test_contract_vector_hash_input_bytes(
    action: str,
    kind: str,
    identity: dict[str, Any],
    canonical_input: str,
    expected_id: str,  # noqa: ARG001 — this case asserts the input half only
) -> None:
    """The hash input is the contract's exact byte string: a JSON array, no whitespace."""
    produced = canonical_json_bytes([action, kind, canonical_identity(identity, kind=kind)])
    assert produced == canonical_input.encode("utf-8")


@pytest.mark.parametrize(("action", "kind", "identity", "canonical_input", "expected_id"), _VECTOR_PARAMS)
def test_contract_vector_identifier(
    action: str,
    kind: str,
    identity: dict[str, Any],
    canonical_input: str,  # noqa: ARG001 — this case asserts the identifier half only
    expected_id: str,
) -> None:
    """The identifier is the contract's exact value for that triple."""
    assert operation_id(action, kind, identity) == expected_id


def test_hash_input_is_an_array_not_an_object() -> None:
    """PD-001 fixes the input as a JSON array in the order (action, kind, identity)."""
    encoded = canonical_json_bytes(["create", "BuiltinTag", canonical_identity({"name": "prod"})])
    assert encoded.startswith(b'["create","BuiltinTag",{')
    assert encoded.endswith(b"}]")


def test_operand_order_is_action_kind_identity() -> None:
    """Swapping action and kind changes the identifier, so the order is load-bearing."""
    assert operation_id("create", "BuiltinTag", {"name": "prod"}) != operation_id(
        "BuiltinTag", "create", {"name": "prod"}
    )


# --------------------------------------------------------------------------------------
# Stability and sensitivity.
# --------------------------------------------------------------------------------------


def test_identifier_is_stable_across_re_derivation() -> None:
    """Re-deriving from an equal triple yields the same identifier."""
    first = operation_id(
        "create", "DcimDevice", {"name": "dev1", "location": {"peer_kind": "LocationSite", "identity": {"name": "dc1"}}}
    )
    second = operation_id(
        "create", "DcimDevice", {"name": "dev1", "location": {"peer_kind": "LocationSite", "identity": {"name": "dc1"}}}
    )
    assert first == second


def test_identifier_is_unchanged_when_only_the_payload_changes() -> None:
    """The payload is not an input (AD002): the identifier names the logical operation.

    `operation_id` takes no payload argument at all, so the check is that two operations
    over the same triple — which is what differing payloads reduce to — collide by design.
    """
    identity = {"name": "dev1"}
    before = operation_id("update", "DcimDevice", identity)
    after = operation_id("update", "DcimDevice", identity)
    assert before == after
    assert "payload" not in operation_id.__code__.co_varnames


@pytest.mark.parametrize(
    ("action", "kind", "identity"),
    [
        pytest.param("update", "BuiltinTag", {"name": "prod"}, id="action changed"),
        pytest.param("create", "BuiltinRole", {"name": "prod"}, id="kind changed"),
        pytest.param("create", "BuiltinTag", {"name": "staging"}, id="identity value changed"),
        pytest.param("create", "BuiltinTag", {"label": "prod"}, id="identity key changed"),
        pytest.param("create", "BuiltinTag", {"name": "prod", "extra": "x"}, id="identity component added"),
        pytest.param("create", "BuiltinTag", {}, id="identity emptied"),
    ],
)
def test_identifier_changes_when_any_component_changes(action: str, kind: str, identity: dict[str, Any]) -> None:
    """Any change to action, kind or identity changes the identifier."""
    baseline = operation_id("create", "BuiltinTag", {"name": "prod"})
    assert operation_id(action, kind, identity) != baseline


def test_nested_peer_identity_value_change_changes_the_identifier() -> None:
    """Sensitivity reaches into a recursive reference component (AD043)."""
    base = {"name": "rack-a", "site": {"peer_kind": "LocationSite", "identity": {"name": "dc1"}}}
    other = {"name": "rack-a", "site": {"peer_kind": "LocationSite", "identity": {"name": "dc2"}}}
    assert operation_id("update", "LocationRack", base) != operation_id("update", "LocationRack", other)


def test_nested_peer_kind_change_changes_the_identifier() -> None:
    """A peer's kind is part of the identity, so changing it changes the identifier (AD046)."""
    as_site = {"name": "dev1", "location": {"peer_kind": "LocationSite", "identity": {"name": "dc1"}}}
    as_rack = {"name": "dev1", "location": {"peer_kind": "LocationRack", "identity": {"name": "dc1"}}}
    assert operation_id("create", "DcimDevice", as_site) != operation_id("create", "DcimDevice", as_rack)


# --------------------------------------------------------------------------------------
# Shape, and identity key-order insensitivity.
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("action", "kind", "identity"),
    [
        pytest.param("create", "BuiltinTag", {"name": "prod"}, id="create"),
        pytest.param("update", "DcimDevice", {"name": "dev1"}, id="update"),
        pytest.param("delete", "LocationSite", {"name": "dc1"}, id="delete"),
        pytest.param("create", "BuiltinTag", {}, id="empty identity"),
        pytest.param(
            "create",
            "LocationRack",
            {"name": "r", "site": {"peer_kind": "LocationSite", "identity": {"name": "dc1"}}},
            id="nested reference",
        ),
    ],
)
def test_identifier_matches_the_declared_pattern(action: str, kind: str, identity: dict[str, Any]) -> None:
    """Every identifier is `op_` plus sixteen lowercase hex characters."""
    produced = operation_id(action, kind, identity)
    assert re.fullmatch(OPERATION_ID_PATTERN, produced), produced
    assert len(produced) == len("op_") + 16


def test_identity_key_order_does_not_affect_the_identifier() -> None:
    """Two identities differing only in insertion order derive the same identifier."""
    forward = {"name": "dc1-rack-a", "site": {"peer_kind": "LocationSite", "identity": {"name": "dc1"}}}
    reverse = {"site": {"identity": {"name": "dc1"}, "peer_kind": "LocationSite"}, "name": "dc1-rack-a"}
    assert list(forward) != list(reverse)
    assert operation_id("update", "LocationRack", forward) == operation_id("update", "LocationRack", reverse)


def test_canonical_identity_sorts_keys_recursively() -> None:
    """`canonical_identity` is the one representation FR-028.3 fixes."""
    produced = canonical_identity({"z": 1, "a": {"y": 2, "b": 3}})
    assert list(produced) == ["a", "z"]
    assert list(produced["a"]) == ["b", "y"]


def test_canonical_identity_refuses_a_non_mapping() -> None:
    """An identity is a mapping of attribute name to value, never a bare unique-id string."""
    with pytest.raises(TypeError):
        canonical_identity("dc1__rack-a")  # ty: ignore[invalid-argument-type]
