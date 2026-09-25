"""A create needs a complete destination identity that survives its mutation."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from infrahub_sync.plan.derive import warn_missing_convergence_key
from infrahub_sync.plan.errors import UnkeyedCreateRefusedError
from infrahub_sync.plan.identity import canonical_identity, operation_id
from infrahub_sync.plan.keying import unkeyed_create_reason, writable_convergence_reason
from infrahub_sync.plan.models import PlannedOperation


def _node(*, constraint: list[str] | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        kind="TestRule",
        attributes=[
            SimpleNamespace(name="rule_id", read_only=True),
            SimpleNamespace(name="hostname", read_only=False),
            SimpleNamespace(name="site", read_only=False),
        ],
        relationships=[],
        human_friendly_id=["rule_id__value"],
        uniqueness_constraints=[constraint] if constraint else [],
    )


def _create(identity: dict[str, Any]) -> PlannedOperation:
    key = canonical_identity(identity, kind="TestRule")
    return PlannedOperation(
        operation_id=operation_id("create", "TestRule", key),
        action="create",
        kind="TestRule",
        identity=key,
        tier=0,
        payload={**identity, "payload": "bad-case"},
        relationships=None,
        destination_id=None,
    )


def test_server_allocated_identity_is_refused_before_plan_creation() -> None:
    node = _node()

    reason = writable_convergence_reason(node=node, identity_fields={"rule_id"}, mapped_fields={"rule_id", "payload"})
    assert reason is not None
    assert "rule_id__value" in reason
    assert "server-allocated" in reason
    assert "Read-only values" in reason

    with pytest.raises(UnkeyedCreateRefusedError, match="rule_id__value"):
        warn_missing_convergence_key(
            destination=SimpleNamespace(schema={"TestRule": node}), operations=[_create({"rule_id": 999})]
        )


def test_complete_writable_alternative_accepts_computed_display_identity() -> None:
    node = _node(constraint=["hostname__value"])
    node.attributes[0].read_only = False
    node.attributes[0].computed_attribute = SimpleNamespace(kind="Jinja2")
    identity = {"hostname": "edge-1"}

    assert (
        writable_convergence_reason(node=node, identity_fields=identity, mapped_fields=identity, identity=identity)
        is None
    )
    warn_missing_convergence_key(destination=SimpleNamespace(schema={"TestRule": node}), operations=[_create(identity)])


def test_computed_only_identity_is_refused() -> None:
    node = _node()
    node.attributes[0].read_only = False
    node.attributes[0].computed_attribute = SimpleNamespace(kind="Jinja2")

    with pytest.raises(UnkeyedCreateRefusedError, match="rule_id__value"):
        warn_missing_convergence_key(
            destination=SimpleNamespace(schema={"TestRule": node}), operations=[_create({"rule_id": 999})]
        )


def test_user_computed_identity_is_writable() -> None:
    node = _node()
    node.attributes[0].read_only = False
    node.attributes[0].computed_attribute = SimpleNamespace(kind="User")
    identity = {"rule_id": 999}

    assert writable_convergence_reason(node=node, identity_fields=identity, mapped_fields=identity) is None
    warn_missing_convergence_key(destination=SimpleNamespace(schema={"TestRule": node}), operations=[_create(identity)])


def test_partial_alternative_is_refused_even_when_one_component_is_writable() -> None:
    node = _node(constraint=["hostname__value", "site__value"])
    identity = {"hostname": "edge-1"}

    reason = writable_convergence_reason(node=node, identity_fields=identity, mapped_fields=identity)
    assert reason is not None
    assert "site__value" in reason
    with pytest.raises(UnkeyedCreateRefusedError, match="site__value"):
        warn_missing_convergence_key(
            destination=SimpleNamespace(schema={"TestRule": node}), operations=[_create(identity)]
        )


def test_blank_writable_alternative_does_not_key_a_create() -> None:
    node = _node(constraint=["hostname__value"])

    with pytest.raises(UnkeyedCreateRefusedError, match="hostname__value"):
        warn_missing_convergence_key(
            destination=SimpleNamespace(schema={"TestRule": node}), operations=[_create({"hostname": " "})]
        )


def test_unselected_writable_component_has_no_read_only_explanation() -> None:
    node = _node(constraint=["hostname__value", "site__value"])
    node.human_friendly_id = []

    reason = writable_convergence_reason(node=node, identity_fields={"hostname"}, mapped_fields={"hostname"})

    assert reason is not None
    assert "site__value" in reason
    assert "server-allocated" not in reason
    assert ". Map and select" in reason


def test_unselected_hfid_refusal_does_not_claim_the_identity_names_it() -> None:
    node = _node(constraint=["hostname__value"])
    node.attributes[0].read_only = False
    node.attributes[2].read_only = True
    node.uniqueness_constraints.append(["site__value"])
    identity = {"hostname": "edge-1"}

    assert writable_convergence_reason(node=node, identity_fields=identity, mapped_fields=identity) is None
    reason = unkeyed_create_reason(_create(identity), node=node)

    assert reason is not None
    assert "rule_id__value" in reason
    assert "names every" not in reason


def test_relationship_crossing_key_checks_the_peer_attribute() -> None:
    node = _node(constraint=["owner__rule_id__value"])
    node.relationships = [SimpleNamespace(name="owner", peer="AbstractRule")]
    identity = {"owner": {"peer_kind": "TestRule", "identity": {"rule_id": 999}}}

    reason = writable_convergence_reason(
        node=node, identity_fields=identity, mapped_fields=identity, schemas={"TestRule": node}, identity=identity
    )
    assert reason is not None
    assert "owner__rule_id__value" in reason

    early_reason = writable_convergence_reason(
        node=node,
        identity_fields=identity,
        mapped_fields=identity,
        schemas={"TestRule": node},
        references={"TestRule": {"owner": "TestRule"}},
    )
    assert early_reason is not None
    assert "owner__rule_id__value" in early_reason


@pytest.mark.parametrize("component", ["owner", "owner__hostname__value"])
def test_read_only_relationship_cannot_supply_a_key(component: str) -> None:
    node = _node(constraint=[component])
    node.human_friendly_id = []
    node.relationships = [SimpleNamespace(name="owner", peer="TestRule", read_only=True)]
    identity = {"owner": {"peer_kind": "TestRule", "identity": {"hostname": "edge-1"}}}

    reason = writable_convergence_reason(
        node=node, identity_fields=identity, mapped_fields=identity, schemas={"TestRule": node}, identity=identity
    )

    assert reason is not None
    assert component in reason
