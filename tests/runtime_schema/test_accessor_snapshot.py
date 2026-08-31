"""The bundled Infrahub accessor delivers the properties the runtime path consumes."""

from __future__ import annotations

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
