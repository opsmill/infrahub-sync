"""AR6: compatibility closes by consumed semantics, derived from the snapshot schema."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from infrahub_sync import SchemaMappingField, SchemaMappingModel, SyncAdapter, SyncConfig
from infrahub_sync.runtime_schema import compute_consumed_schema_fingerprint, normalize_destination_schema

_SNAPSHOT: dict[str, Any] = {
    "InfraDevice": {
        "human_friendly_id": ["name__value"],
        "uniqueness_constraints": [["name__value"]],
        "attributes": {
            "name": {"kind": "Text", "optional": False, "default_value": None, "unique": True},
            "role": {"kind": "Dropdown", "optional": True, "default_value": "leaf", "unique": False},
            "asn": {"kind": "Number", "optional": False, "default_value": None, "unique": False},
        },
        "relationships": {
            "site": {"peer": "LocationSite", "cardinality": "one", "optional": True, "kind": "Attribute"},
            "tags": {"peer": "BuiltinTag", "cardinality": "many", "optional": True, "kind": "Generic"},
        },
    },
    "LocationSite": {
        "human_friendly_id": ["name__value"],
        "uniqueness_constraints": [["name__value"]],
        "attributes": {"name": {"kind": "Text", "optional": False, "default_value": None, "unique": True}},
        "relationships": {},
    },
}

_CONFIGURATION = SyncConfig(
    name="fingerprint-example",
    source=SyncAdapter(name="netbox"),
    destination=SyncAdapter(name="infrahub"),
    schema_mapping=[
        SchemaMappingModel(
            name="InfraDevice",
            fields=[
                SchemaMappingField(name="name"),
                SchemaMappingField(name="role"),
                SchemaMappingField(name="site"),
            ],
        )
    ],
)


def _fingerprint(snapshot: dict[str, Any]) -> str:
    return compute_consumed_schema_fingerprint(
        configuration=_CONFIGURATION, snapshot=normalize_destination_schema(snapshot)
    )


def _mutated(**changes: object) -> dict[str, Any]:
    snapshot = copy.deepcopy(_SNAPSHOT)
    entry = snapshot["InfraDevice"]
    for path, value in changes.items():
        target: Any = entry
        *parents, leaf = path.split(".")
        for step in parents:
            target = target[step]
        target[leaf] = value
    return snapshot


def test_the_fingerprint_is_a_full_sha256_digest() -> None:
    fingerprint = _fingerprint(_SNAPSHOT)

    assert len(fingerprint) == 64
    assert set(fingerprint) <= set("0123456789abcdef")


def test_the_fingerprint_is_stable_across_repeated_projections() -> None:
    assert _fingerprint(_SNAPSHOT) == _fingerprint(copy.deepcopy(_SNAPSHOT))


@pytest.mark.parametrize(
    "snapshot",
    [
        pytest.param(
            {kind: _SNAPSHOT[kind] for kind in reversed(list(_SNAPSHOT))},
            id="kind-delivery-order",
        ),
        pytest.param(
            _mutated(attributes=dict(reversed(list(_SNAPSHOT["InfraDevice"]["attributes"].items())))),
            id="attribute-delivery-order",
        ),
        pytest.param(
            _mutated(relationships=dict(reversed(list(_SNAPSHOT["InfraDevice"]["relationships"].items())))),
            id="relationship-delivery-order",
        ),
        pytest.param(
            {
                **_SNAPSHOT,
                "InfraInterface": {
                    "human_friendly_id": ["name__value"],
                    "uniqueness_constraints": [],
                    "attributes": {"name": {"kind": "Text", "optional": True, "default_value": None, "unique": False}},
                    "relationships": {},
                },
            },
            id="unmapped-kind-added",
        ),
        pytest.param(
            _mutated(
                **{
                    "attributes.description": {
                        "kind": "TextArea",
                        "optional": True,
                        "default_value": None,
                        "unique": False,
                    }
                }
            ),
            id="optional-unmapped-attribute-added",
        ),
    ],
)
def test_compatible_change_retains_the_fingerprint(snapshot: dict[str, Any]) -> None:
    assert _fingerprint(snapshot) == _fingerprint(_SNAPSHOT)


@pytest.mark.parametrize(
    "snapshot",
    [
        pytest.param({"LocationSite": _SNAPSHOT["LocationSite"]}, id="consumed-kind-removed"),
        pytest.param(_mutated(human_friendly_id=["name__value", "site__name__value"]), id="human-friendly-id"),
        pytest.param(
            _mutated(uniqueness_constraints=[["name__value", "site__name__value"]]), id="uniqueness-constraint"
        ),
        pytest.param(_mutated(**{"attributes.name.unique": False}), id="identifier-uniqueness"),
        pytest.param(_mutated(**{"attributes.role.kind": "Number"}), id="mapped-attribute-kind"),
        pytest.param(_mutated(**{"attributes.role.optional": False}), id="mapped-attribute-required"),
        pytest.param(_mutated(**{"attributes.role.default_value": "spine"}), id="mapped-attribute-default"),
        pytest.param(_mutated(**{"attributes.role.unique": True}), id="mapped-attribute-uniqueness"),
        pytest.param(_mutated(**{"relationships.site.peer": "LocationRegion"}), id="mapped-relationship-peer"),
        pytest.param(_mutated(**{"relationships.site.cardinality": "many"}), id="mapped-relationship-cardinality"),
        pytest.param(_mutated(**{"relationships.site.optional": False}), id="mapped-relationship-optional"),
        pytest.param(_mutated(**{"relationships.site.kind": "Component"}), id="mapped-relationship-kind"),
        pytest.param(
            _mutated(
                **{
                    "attributes.serial": {
                        "kind": "Text",
                        "optional": False,
                        "default_value": None,
                        "unique": False,
                    }
                }
            ),
            id="mandatory-unmapped-attribute-added",
        ),
        pytest.param(
            _mutated(
                **{
                    "relationships.owner": {
                        "peer": "CoreAccount",
                        "cardinality": "one",
                        "optional": False,
                        "kind": "Attribute",
                    }
                }
            ),
            id="mandatory-unmapped-relationship-added",
        ),
        pytest.param(_mutated(**{"attributes.asn.optional": True}), id="mandatory-unmapped-attribute-relaxed"),
        pytest.param(_mutated(**{"attributes.asn.kind": "Text"}), id="mandatory-unmapped-attribute-kind"),
    ],
)
def test_incompatible_change_changes_the_fingerprint(snapshot: dict[str, Any]) -> None:
    assert _fingerprint(snapshot) != _fingerprint(_SNAPSHOT)
