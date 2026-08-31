"""The closed normalized destination-schema domain the runtime model path consumes."""

from __future__ import annotations

from typing import Any, cast

import pytest

from infrahub_sync.runtime_schema import (
    UnsupportedSchemaSemanticsError,
    normalize_destination_schema,
)

_SNAPSHOT: dict[str, Any] = {
    "InfraDevice": {
        "human_friendly_id": ["name__value"],
        "uniqueness_constraints": [["name__value"], ["site__name__value", "name__value"]],
        "attributes": {
            "name": {"kind": "Text", "optional": False, "default_value": None, "unique": True},
            "role": {"kind": "Dropdown", "optional": True, "default_value": "leaf", "unique": False},
        },
        "relationships": {
            "site": {"peer": "LocationSite", "cardinality": "one", "optional": False, "kind": "Attribute"},
            "interfaces": {"peer": "InfraInterface", "cardinality": "many", "optional": True, "kind": "Component"},
        },
    },
}


def test_normalized_kind_carries_every_consumed_property() -> None:
    snapshot = normalize_destination_schema(_SNAPSHOT)

    kind = snapshot.kinds["InfraDevice"]
    assert kind.kind == "InfraDevice"
    assert kind.human_friendly_id == ("name__value",)
    assert kind.uniqueness_constraints == (("name__value",), ("site__name__value", "name__value"))

    name, role = kind.attributes
    assert (name.name, name.kind, name.optional, name.default_value, name.unique) == (
        "name",
        "Text",
        False,
        None,
        True,
    )
    assert (role.name, role.kind, role.optional, role.default_value, role.unique) == (
        "role",
        "Dropdown",
        True,
        "leaf",
        False,
    )

    interfaces, site = kind.relationships
    assert (site.name, site.peer, site.cardinality, site.optional, site.kind) == (
        "site",
        "LocationSite",
        "one",
        False,
        "Attribute",
    )
    assert interfaces.kind == "Component"


def test_normalization_orders_members_by_name_so_delivery_order_is_irrelevant() -> None:
    reordered = {
        "InfraDevice": {
            **_SNAPSHOT["InfraDevice"],
            "attributes": dict(reversed(list(_SNAPSHOT["InfraDevice"]["attributes"].items()))),
            "relationships": dict(reversed(list(_SNAPSHOT["InfraDevice"]["relationships"].items()))),
        }
    }

    assert normalize_destination_schema(reordered) == normalize_destination_schema(_SNAPSHOT)


@pytest.mark.parametrize(
    "default_value",
    [
        pytest.param(float("nan"), id="nan"),
        pytest.param(float("inf"), id="inf"),
        pytest.param(float("-inf"), id="-inf"),
    ],
)
def test_a_non_finite_default_refuses_before_it_can_reach_the_fingerprint(default_value: float) -> None:
    # A non-finite float is not JSON, so it cannot survive canonical encoding. Refusing it
    # here keeps the closed domain the one place that answers "unsupported semantics".
    entry = {
        **_SNAPSHOT["InfraDevice"],
        "attributes": {"asn": {"kind": "Number", "optional": True, "default_value": default_value, "unique": False}},
    }

    with pytest.raises(UnsupportedSchemaSemanticsError):
        normalize_destination_schema({"InfraDevice": entry})


@pytest.mark.parametrize(
    "mutation",
    [
        pytest.param({"attributes": {"name": {"kind": "Text"}}}, id="attribute-missing-property"),
        pytest.param(
            {
                "relationships": {
                    "site": {"peer": "LocationSite", "cardinality": "several", "optional": False, "kind": "Attribute"}
                }
            },
            id="unknown-cardinality",
        ),
        pytest.param({"human_friendly_id": ["name__value", 7]}, id="non-string-hfid-component"),
        pytest.param({"uniqueness_constraints": ["name__value"]}, id="uniqueness-constraint-not-a-path-list"),
    ],
)
def test_unusable_snapshot_members_refuse_with_a_typed_error(mutation: dict[str, Any]) -> None:
    entry = {**_SNAPSHOT["InfraDevice"], **mutation}

    with pytest.raises(UnsupportedSchemaSemanticsError):
        normalize_destination_schema({"InfraDevice": entry})


# --- F7: the snapshot is immutable, so nothing derived from one can be edited under it --


def test_the_snapshot_kind_mapping_cannot_be_reassigned() -> None:
    snapshot = normalize_destination_schema(_SNAPSHOT)

    with pytest.raises(TypeError):
        snapshot.kinds["InfraDevice"] = snapshot.kinds["InfraDevice"]  # ty: ignore[invalid-assignment]


def test_a_container_default_is_immutable_in_the_snapshot() -> None:
    entry = {
        **_SNAPSHOT["InfraDevice"],
        "attributes": {
            "tags": {
                "kind": "List",
                "optional": True,
                "default_value": ["alpha", {"nested": ["beta"]}],
                "unique": False,
            }
        },
    }

    default = normalize_destination_schema({"InfraDevice": entry}).kinds["InfraDevice"].attributes[0].default_value

    assert default == ("alpha", {"nested": ("beta",)})
    with pytest.raises((TypeError, AttributeError)):
        default.append("gamma")
    with pytest.raises(TypeError):
        default[1]["nested"] = ()


def test_an_immutable_container_default_still_reaches_a_model_and_canonical_json() -> None:
    from diffsync import DiffSyncModel

    from infrahub_sync import SchemaMappingField, SchemaMappingModel, SyncAdapter, SyncConfig
    from infrahub_sync.plan.canonical import canonical_json_bytes
    from infrahub_sync.runtime_schema import build_runtime_models, compute_consumed_schema_fingerprint

    entry = {
        **_SNAPSHOT["InfraDevice"],
        "attributes": {
            "name": {"kind": "Text", "optional": False, "default_value": None, "unique": True},
            "tags": {"kind": "List", "optional": True, "default_value": ["alpha"], "unique": False},
        },
    }
    snapshot = normalize_destination_schema({"InfraDevice": entry})
    configuration = SyncConfig(
        name="immutable-default",
        source=SyncAdapter(name="netbox"),
        destination=SyncAdapter(name="infrahub"),
        schema_mapping=[
            SchemaMappingModel(
                name="InfraDevice",
                fields=[SchemaMappingField(name="name"), SchemaMappingField(name="tags")],
            )
        ],
    )

    device = cast(
        "Any",
        build_runtime_models(snapshot=snapshot, configuration=configuration, model_base=DiffSyncModel)["InfraDevice"],
    )
    instance = device(name="leaf01")

    assert instance.tags == ["alpha"]
    instance.tags.append("beta")
    assert device(name="leaf02").tags == ["alpha"]
    assert canonical_json_bytes(snapshot.kinds["InfraDevice"].attributes[1].default_value) == b'["alpha"]'
    assert len(compute_consumed_schema_fingerprint(configuration=configuration, snapshot=snapshot)) == 64
