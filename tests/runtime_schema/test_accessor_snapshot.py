"""The bundled Infrahub accessor delivers the properties the runtime path consumes."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from infrahub_sdk.schema.main import AttributeKind, NodeSchemaAPI

from infrahub_sync.configuration import capabilities as capabilities_module
from infrahub_sync.configuration.capabilities import DestinationSchemaReadError
from infrahub_sync.runtime_schema import normalize_destination_schema

_NODE: dict[str, Any] = {
    "name": "Device",
    "namespace": "Infra",
    "human_friendly_id": ["name__value"],
    "uniqueness_constraints": [["name__value"]],
    "attributes": [
        {"name": "name", "kind": AttributeKind.TEXT, "optional": False, "unique": True},
        {"name": "role", "kind": AttributeKind.DROPDOWN, "optional": True, "default_value": "leaf"},
    ],
    "relationships": [
        {"name": "site", "peer": "LocationSite", "cardinality": "one", "optional": False, "kind": "Attribute"},
        {
            "name": "interfaces",
            "peer": "InfraInterface",
            "cardinality": "many",
            "optional": True,
            "kind": "Component",
        },
    ],
}


@pytest.fixture(name="snapshot")
def _snapshot() -> dict[str, Any]:
    node = NodeSchemaAPI.model_validate(_NODE)
    return dict(capabilities_module._build_schema_snapshot({node.kind: node}))


def test_the_snapshot_carries_the_kind_identity_paths(snapshot: dict[str, Any]) -> None:
    assert snapshot["InfraDevice"]["human_friendly_id"] == ["name__value"]
    assert snapshot["InfraDevice"]["uniqueness_constraints"] == [["name__value"]]


def test_the_snapshot_carries_every_attribute_and_relationship_property(snapshot: dict[str, Any]) -> None:
    assert snapshot["InfraDevice"]["attributes"]["role"] == {
        "kind": "Dropdown",
        "optional": True,
        "default_value": "leaf",
        "unique": False,
    }
    assert snapshot["InfraDevice"]["relationships"]["interfaces"] == {
        "peer": "InfraInterface",
        "cardinality": "many",
        "optional": True,
        "kind": "Component",
    }


def test_the_delivered_snapshot_normalizes_into_the_closed_domain(snapshot: dict[str, Any]) -> None:
    normalized = normalize_destination_schema(snapshot)

    assert normalized.kinds["InfraDevice"].human_friendly_id == ("name__value",)


class _NonStringPathNode:
    """A node whose identity paths are not the strings the SDK contract promises."""

    human_friendly_id = ("name__value", 7)
    uniqueness_constraints = ()
    attributes: tuple[object, ...] = ()
    relationships: tuple[object, ...] = ()


def test_a_non_string_identity_path_is_refused_at_the_adapter_boundary() -> None:
    # Refused rather than coerced: `str()` on a third-party object would let its own text
    # into the snapshot, and everything derived from one.
    with pytest.raises(DestinationSchemaReadError):
        capabilities_module._build_schema_snapshot({"InfraDevice": _NonStringPathNode()})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        pytest.param("human_friendly_id", "name__value", id="human-friendly-id"),
        pytest.param("uniqueness_constraints", "name__value", id="constraint-collection"),
        pytest.param("uniqueness_constraints", ["name__value"], id="constraint-path"),
    ],
)
def test_a_scalar_string_identity_path_container_is_refused_at_the_adapter_boundary(field: str, value: object) -> None:
    node = SimpleNamespace(
        human_friendly_id=(),
        uniqueness_constraints=(),
        attributes=(),
        relationships=(),
    )
    setattr(node, field, value)

    with pytest.raises(DestinationSchemaReadError) as caught:
        capabilities_module._build_schema_snapshot({"InfraDevice": node})

    assert caught.value.reason == "rejected"


@pytest.mark.parametrize(
    ("member_group", "member"),
    [
        pytest.param(
            "attributes",
            SimpleNamespace(name="name", kind="Text", optional="false", default_value=None, unique=False),
            id="attribute-optional",
        ),
        pytest.param(
            "attributes",
            SimpleNamespace(name="name", kind="Text", optional=False, default_value=None, unique=1),
            id="attribute-unique",
        ),
        pytest.param(
            "relationships",
            SimpleNamespace(name="site", peer="LocationSite", cardinality="one", optional="false", kind="Attribute"),
            id="relationship-optional",
        ),
    ],
)
def test_a_non_boolean_member_flag_is_refused_at_the_adapter_boundary(member_group: str, member: object) -> None:
    node = SimpleNamespace(
        human_friendly_id=(),
        uniqueness_constraints=(),
        attributes=(),
        relationships=(),
    )
    setattr(node, member_group, (member,))

    with pytest.raises(DestinationSchemaReadError) as caught:
        capabilities_module._build_schema_snapshot({"InfraDevice": node})

    assert caught.value.reason == "rejected"


class _NonFiniteDefaultAttribute:
    name = "asn"
    kind = "Number"
    optional = True
    default_value = float("inf")
    unique = False


class _NonFiniteDefaultNode:
    """A node declaring a default no JSON encoding can carry."""

    human_friendly_id = ()
    uniqueness_constraints = ()
    attributes = (_NonFiniteDefaultAttribute(),)
    relationships: tuple[object, ...] = ()


def test_a_non_finite_declared_default_is_refused_at_the_adapter_boundary() -> None:
    with pytest.raises(DestinationSchemaReadError):
        capabilities_module._build_schema_snapshot({"InfraDevice": _NonFiniteDefaultNode()})


# =======================================================================================
# AR4 — one member name means one member (parent envelope section 6 hostile probes)
# =======================================================================================


def _node(*, attributes: list[dict[str, Any]], relationships: list[dict[str, Any]]) -> NodeSchemaAPI:
    """A typed installed-SDK node carrying the members a case is about."""
    return NodeSchemaAPI.model_validate(
        {
            "name": "Device",
            "namespace": "Infra",
            "human_friendly_id": ["name__value"],
            "uniqueness_constraints": [["name__value"]],
            "attributes": attributes,
            "relationships": relationships,
        }
    )


_TEXT = {"kind": AttributeKind.TEXT, "optional": True}
_PEER = {"peer": "LocationSite", "cardinality": "one", "optional": True, "kind": "Attribute"}


@pytest.mark.parametrize(
    "node",
    [
        pytest.param(
            _node(
                attributes=[{"name": "name", **_TEXT}, {"name": "name", "kind": AttributeKind.NUMBER}],
                relationships=[],
            ),
            id="duplicate-attribute-name",
        ),
        pytest.param(
            _node(attributes=[], relationships=[{"name": "site", **_PEER}, {"name": "site", **_PEER}]),
            id="duplicate-relationship-name",
        ),
        pytest.param(
            _node(attributes=[{"name": "site", **_TEXT}], relationships=[{"name": "site", **_PEER}]),
            id="attribute-relationship-conflict",
        ),
    ],
)
def test_an_ambiguous_member_name_is_refused_at_the_adapter_boundary(node: NodeSchemaAPI) -> None:
    """One name must mean one member: a dict comprehension would keep the last silently.

    The typed SDK admits all three shapes, so the snapshot would be built from whichever
    declaration arrived last — making the model, the fingerprint, and every planned write
    depend on response ordering rather than on the schema.
    """
    with pytest.raises(DestinationSchemaReadError) as caught:
        capabilities_module._build_schema_snapshot({"InfraDevice": node})

    assert caught.value.reason == "rejected"


@pytest.mark.parametrize(
    "name",
    [
        pytest.param("na\x00me", id="null"),
        pytest.param("name\n", id="newline"),
        pytest.param("na\x07me", id="bell"),
        pytest.param("name\x1b[31m", id="escape"),
        pytest.param("na\u200bme", id="zero-width"),
        pytest.param("", id="empty"),
    ],
)
def test_a_control_bearing_member_name_is_refused_at_the_adapter_boundary(name: str) -> None:
    """A member name reaches model fields, plan payload keys, logs and refusal text."""
    with pytest.raises(DestinationSchemaReadError) as caught:
        capabilities_module._build_schema_snapshot(
            {"InfraDevice": _node(attributes=[{"name": name, **_TEXT}], relationships=[])}
        )

    assert caught.value.reason == "rejected"


def test_the_refusal_carries_no_text_from_the_rejected_member() -> None:
    """The fixed message: a hostile name puts nothing into what the operator is shown."""
    hostile = "canary\x1b[31m-member-name"

    with pytest.raises(DestinationSchemaReadError) as caught:
        capabilities_module._build_schema_snapshot(
            {"InfraDevice": _node(attributes=[{"name": hostile, **_TEXT}], relationships=[])}
        )

    assert "canary" not in str(caught.value)


def test_distinct_member_names_across_both_groups_are_still_delivered() -> None:
    """Preservation: the rule is one name per member, not one name per kind."""
    snapshot = capabilities_module._build_schema_snapshot(
        {"InfraDevice": _node(attributes=[{"name": "name", **_TEXT}], relationships=[{"name": "site", **_PEER}])}
    )

    assert set(snapshot["InfraDevice"]["attributes"]) == {"name"}
    assert set(snapshot["InfraDevice"]["relationships"]) == {"site"}
