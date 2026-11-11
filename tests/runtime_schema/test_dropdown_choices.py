"""The choices of a Dropdown attribute in the runtime schema and its fingerprint (spec 019 T023, research R4).

The plan checks each mapped Dropdown value against the choices the run's destination schema
snapshot records, so the snapshot keeps them, the consumed-schema fingerprint covers them, and
the run's runtime model plan carries the choices of each mapped Dropdown attribute.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from infrahub_sync import SchemaMappingField, SchemaMappingModel, SyncAdapter, SyncConfig, SyncInstance
from infrahub_sync.runtime_schema import (
    UnsupportedSchemaSemanticsError,
    compute_consumed_schema_fingerprint,
    normalize_destination_schema,
)
from infrahub_sync.runtime_schema.worker import (
    RuntimeModelPlan,
    RuntimeSideModels,
    mapped_dropdown_choices,
    planned_dropdown_choices,
)

_SNAPSHOT: dict[str, Any] = {
    "InfraDevice": {
        "human_friendly_id": ["name__value"],
        "uniqueness_constraints": [],
        "attributes": {
            "name": {"kind": "Text", "optional": False, "default_value": None, "unique": True},
            "role": {
                "kind": "Dropdown",
                "optional": True,
                "default_value": None,
                "unique": False,
                "choices": ["spine", "leaf"],
            },
            "status": {
                "kind": "Dropdown",
                "optional": True,
                "default_value": None,
                "unique": False,
                "choices": ["active"],
            },
        },
        "relationships": {},
    },
}

_CONFIGURATION = SyncConfig(
    name="dropdown-choices",
    source=SyncAdapter(name="netbox"),
    destination=SyncAdapter(name="infrahub"),
    schema_mapping=[
        SchemaMappingModel(
            name="InfraDevice", fields=[SchemaMappingField(name="name"), SchemaMappingField(name="role")]
        )
    ],
)


def _with_choices(attribute: str, choices: list[str] | None) -> dict[str, Any]:
    snapshot = copy.deepcopy(_SNAPSHOT)
    if choices is None:
        snapshot["InfraDevice"]["attributes"][attribute].pop("choices")
    else:
        snapshot["InfraDevice"]["attributes"][attribute]["choices"] = choices
    return snapshot


def _fingerprint(raw: dict[str, Any]) -> str:
    return compute_consumed_schema_fingerprint(configuration=_CONFIGURATION, snapshot=normalize_destination_schema(raw))


def test_the_normalized_attribute_holds_the_sorted_choices() -> None:
    """The choices are sorted, so the order Infrahub returns them in changes nothing."""
    attributes = {item.name: item for item in normalize_destination_schema(_SNAPSHOT).kinds["InfraDevice"].attributes}

    assert attributes["role"].choices == ("leaf", "spine")
    assert attributes["name"].choices is None


def test_a_choice_that_is_not_a_string_is_refused() -> None:
    with pytest.raises(UnsupportedSchemaSemanticsError, match="non-string choice"):
        normalize_destination_schema(_with_choices("role", ["leaf", 3]))  # ty: ignore[invalid-argument-type]


def test_the_fingerprint_covers_the_choices_of_a_mapped_dropdown() -> None:
    """Adding a choice to a mapped Dropdown changes the fingerprint; reordering does not."""
    base = _fingerprint(_SNAPSHOT)

    assert _fingerprint(_with_choices("role", ["spine", "leaf", "border"])) != base
    assert _fingerprint(_with_choices("role", ["leaf", "spine"])) == base


def test_an_unmapped_dropdown_leaves_the_fingerprint_unchanged() -> None:
    """`status` is not mapped and optional, so its choices are not consumed."""
    assert _fingerprint(_with_choices("status", ["active", "retired"])) == _fingerprint(_SNAPSHOT)


def test_a_snapshot_without_choices_keeps_the_fingerprint_of_an_attribute_without_choices() -> None:
    """An attribute that records no choices projects as before spec 019."""
    without = _with_choices("role", None)

    assert _fingerprint(without) != _fingerprint(_SNAPSHOT)
    assert normalize_destination_schema(without).kinds["InfraDevice"].attributes[1].choices is None


def test_the_runtime_model_plan_carries_the_choices_of_each_mapped_dropdown() -> None:
    snapshot = normalize_destination_schema(_SNAPSHOT)
    choices = mapped_dropdown_choices(snapshot, _CONFIGURATION)
    assert choices == {"InfraDevice": {"role": ("leaf", "spine")}}

    configuration = SyncInstance(**_CONFIGURATION.model_dump(), directory=".")
    assert planned_dropdown_choices(configuration) is None
    side = RuntimeSideModels(adapter_class=object, models={})
    configuration._runtime_models = RuntimeModelPlan(
        branch="main",
        schema_fingerprint="0" * 64,
        destination=side,
        source=side,
        dropdown_choices=choices,
    )
    assert planned_dropdown_choices(configuration) == choices
