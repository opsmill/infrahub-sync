"""AR6: compatibility closes by consumed semantics, derived from the snapshot schema."""

from __future__ import annotations

import copy
from collections.abc import Callable
from dataclasses import dataclass, fields
from typing import Any

import pytest

from infrahub_sync import SchemaMappingField, SchemaMappingModel, SyncAdapter, SyncConfig
from infrahub_sync.runtime_schema import (
    NormalizedAttribute,
    NormalizedKind,
    NormalizedRelationship,
    compute_consumed_schema_fingerprint,
    normalize_destination_schema,
)

_SNAPSHOT: dict[str, Any] = {
    "InfraDevice": {
        "human_friendly_id": ["name__value", "site__name__value"],
        "uniqueness_constraints": [["name__value", "site__name__value"]],
        "attributes": {
            "name": {"kind": "Text", "optional": False, "default_value": None, "unique": True},
            "role": {"kind": "Dropdown", "optional": True, "default_value": "leaf", "unique": False},
            "asn": {"kind": "Number", "optional": False, "default_value": None, "unique": False},
        },
        "relationships": {
            "site": {"peer": "LocationSite", "cardinality": "one", "optional": True, "kind": "Attribute"},
            "tags": {"peer": "BuiltinTag", "cardinality": "many", "optional": True, "kind": "Generic"},
            "owner": {"peer": "CoreAccount", "cardinality": "one", "optional": False, "kind": "Attribute"},
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


def _fingerprint(snapshot: dict[str, Any], configuration: SyncConfig = _CONFIGURATION) -> str:
    return compute_consumed_schema_fingerprint(
        configuration=configuration, snapshot=normalize_destination_schema(snapshot)
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


@dataclass(frozen=True, slots=True)
class _SemanticMutation:
    """One declared projection property and the smallest input change that exercises it."""

    target: tuple[str, str]
    mutate: Callable[[dict[str, Any], SyncConfig], None]


def _set(path: str, *, value: object) -> Callable[[dict[str, Any], SyncConfig], None]:
    def mutate(snapshot: dict[str, Any], configuration: SyncConfig) -> None:
        del configuration
        target: Any = snapshot
        *parents, leaf = path.split(".")
        for step in parents:
            target = target[step]
        target[leaf] = value

    return mutate


def _rename(path: str, replacement: str) -> Callable[[dict[str, Any], SyncConfig], None]:
    def mutate(snapshot: dict[str, Any], configuration: SyncConfig) -> None:
        del configuration
        target: Any = snapshot
        *parents, leaf = path.split(".")
        for step in parents:
            target = target[step]
        target[replacement] = target.pop(leaf)

    return mutate


def _remove_consumed_kind(snapshot: dict[str, Any], configuration: SyncConfig) -> None:
    del configuration
    del snapshot["InfraDevice"]


def _change_effective_identifiers(snapshot: dict[str, Any], configuration: SyncConfig) -> None:
    del snapshot
    configuration.schema_mapping[0].identifiers = ["name", "role"]


_SEMANTIC_MUTATIONS = (
    _SemanticMutation(("consumed-kind", "existence"), _remove_consumed_kind),
    _SemanticMutation(("effective-kind", "identifiers"), _change_effective_identifiers),
    _SemanticMutation(
        ("kind", "human_friendly_id"),
        _set("InfraDevice.human_friendly_id", value=["site__name__value", "name__value"]),
    ),
    _SemanticMutation(
        ("kind", "uniqueness_constraints"),
        _set("InfraDevice.uniqueness_constraints", value=[["site__name__value", "name__value"]]),
    ),
    _SemanticMutation(("mapped-attribute", "name"), _rename("InfraDevice.attributes.role", "platform")),
    _SemanticMutation(("mapped-attribute", "kind"), _set("InfraDevice.attributes.role.kind", value="Number")),
    _SemanticMutation(("mapped-attribute", "optional"), _set("InfraDevice.attributes.role.optional", value=False)),
    _SemanticMutation(
        ("mapped-attribute", "default_value"), _set("InfraDevice.attributes.role.default_value", value="spine")
    ),
    _SemanticMutation(("mapped-attribute", "unique"), _set("InfraDevice.attributes.role.unique", value=True)),
    _SemanticMutation(("mapped-relationship", "name"), _rename("InfraDevice.relationships.site", "rack")),
    _SemanticMutation(
        ("mapped-relationship", "peer"), _set("InfraDevice.relationships.site.peer", value="LocationRegion")
    ),
    _SemanticMutation(
        ("mapped-relationship", "cardinality"), _set("InfraDevice.relationships.site.cardinality", value="many")
    ),
    _SemanticMutation(
        ("mapped-relationship", "optional"), _set("InfraDevice.relationships.site.optional", value=False)
    ),
    _SemanticMutation(("mapped-relationship", "kind"), _set("InfraDevice.relationships.site.kind", value="Component")),
    _SemanticMutation(("mandatory-attribute", "name"), _rename("InfraDevice.attributes.asn", "serial")),
    _SemanticMutation(("mandatory-attribute", "kind"), _set("InfraDevice.attributes.asn.kind", value="Text")),
    _SemanticMutation(("mandatory-attribute", "optional"), _set("InfraDevice.attributes.asn.optional", value=True)),
    _SemanticMutation(
        ("mandatory-attribute", "default_value"), _set("InfraDevice.attributes.asn.default_value", value=0)
    ),
    _SemanticMutation(("mandatory-attribute", "unique"), _set("InfraDevice.attributes.asn.unique", value=True)),
    _SemanticMutation(("mandatory-relationship", "name"), _rename("InfraDevice.relationships.owner", "tenant")),
    _SemanticMutation(
        ("mandatory-relationship", "peer"),
        _set("InfraDevice.relationships.owner.peer", value="CoreOrganization"),
    ),
    _SemanticMutation(
        ("mandatory-relationship", "cardinality"),
        _set("InfraDevice.relationships.owner.cardinality", value="many"),
    ),
    _SemanticMutation(
        ("mandatory-relationship", "optional"), _set("InfraDevice.relationships.owner.optional", value=True)
    ),
    _SemanticMutation(
        ("mandatory-relationship", "kind"), _set("InfraDevice.relationships.owner.kind", value="Component")
    ),
)


def _declared_semantic_domain() -> set[tuple[str, str]]:
    kind_properties = {field.name for field in fields(NormalizedKind)} - {"kind", "attributes", "relationships"}
    return {
        ("consumed-kind", "existence"),
        ("effective-kind", "identifiers"),
        *(("kind", property_name) for property_name in kind_properties),
        *(("mapped-attribute", field.name) for field in fields(NormalizedAttribute)),
        *(("mapped-relationship", field.name) for field in fields(NormalizedRelationship)),
        *(("mandatory-attribute", field.name) for field in fields(NormalizedAttribute)),
        *(("mandatory-relationship", field.name) for field in fields(NormalizedRelationship)),
    }


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


def test_the_mutation_table_covers_the_declared_projection_property_domain() -> None:
    targets = [mutation.target for mutation in _SEMANTIC_MUTATIONS]

    assert len(targets) == len(set(targets))
    assert set(targets) == _declared_semantic_domain()


@pytest.mark.parametrize(
    "mutation",
    _SEMANTIC_MUTATIONS,
    ids=lambda mutation: "-".join(mutation.target),
)
def test_every_declared_semantic_mutation_changes_the_fingerprint(mutation: _SemanticMutation) -> None:
    snapshot = copy.deepcopy(_SNAPSHOT)
    configuration = _CONFIGURATION.model_copy(deep=True)

    mutation.mutate(snapshot, configuration)

    assert _fingerprint(snapshot, configuration) != _fingerprint(_SNAPSHOT)
