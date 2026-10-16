"""Dropdown choices in the destination schema record and the consumed-schema fingerprint.

Spec 009 research R6: the schema read keeps the names of a Dropdown attribute's choices, the
normalized schema carries them, and a change to the choices of a mapped Dropdown changes the
consumed-schema fingerprint, so `PlanSchemaChangedError` refuses a plan reviewed against the
old choices.
"""

from __future__ import annotations

import copy
from types import SimpleNamespace
from typing import Any

import pytest

from infrahub_sync import SchemaMappingField, SchemaMappingModel, SyncAdapter, SyncConfig
from infrahub_sync.configuration.capabilities import (
    DestinationSchemaReadError,
    _attribute_shape,  # noqa: PLC2701 - the schema read under test is private.
)
from infrahub_sync.runtime_schema import (
    UnsupportedSchemaSemanticsError,
    canonical_consumed_schema_projection,
    compute_consumed_schema_fingerprint,
    normalize_destination_schema,
)
from tests.plan.problem_fixtures import SITE_STATUS_CHOICES, schema_record


def _sdk_attribute(kind: str, *, choices: list[dict[str, Any]] | None = None) -> SimpleNamespace:
    """An attribute as the Infrahub SDK returns it, with the properties `_attribute_shape` reads."""
    return SimpleNamespace(
        name="status",
        kind=kind,
        optional=True,
        default_value=None,
        unique=False,
        read_only=False,
        computed_attribute=None,
        choices=choices,
    )


def test_the_schema_read_keeps_the_choice_names_of_a_dropdown() -> None:
    attribute = _sdk_attribute(
        "Dropdown",
        choices=[
            {"name": "active", "label": "Active", "color": "#00ff00", "description": None},
            {"name": "planned", "label": "Planned", "color": None, "description": "Not built yet"},
        ],
    )

    assert _attribute_shape(attribute)["choices"] == ["active", "planned"]


def test_the_schema_read_records_no_choices_for_another_attribute_kind() -> None:
    assert "choices" not in _attribute_shape(_sdk_attribute("Text"))


def test_a_dropdown_without_choices_is_read_as_an_empty_list() -> None:
    assert _attribute_shape(_sdk_attribute("Dropdown", choices=None))["choices"] == []


def test_a_dropdown_choice_without_a_name_is_refused() -> None:
    with pytest.raises(DestinationSchemaReadError):
        _attribute_shape(_sdk_attribute("Dropdown", choices=[{"label": "Active"}]))


def test_the_normalized_attribute_holds_the_choices_of_a_dropdown_only() -> None:
    snapshot = normalize_destination_schema(schema_record())
    site = {attribute.name: attribute for attribute in snapshot.kinds["LocationSite"].attributes}

    assert site["status"].choices == SITE_STATUS_CHOICES
    assert site["name"].choices is None


def test_a_snapshot_without_choices_normalizes_to_none() -> None:
    record = schema_record()
    del record["LocationSite"]["attributes"]["status"]["choices"]
    snapshot = normalize_destination_schema(record)
    status = next(item for item in snapshot.kinds["LocationSite"].attributes if item.name == "status")

    assert status.choices is None


@pytest.mark.parametrize("choices", ["active", [1, 2], ["active", None]])
def test_choices_that_are_not_a_list_of_text_are_refused(choices: object) -> None:
    record = schema_record()
    record["LocationSite"]["attributes"]["status"]["choices"] = choices

    with pytest.raises(UnsupportedSchemaSemanticsError, match="outside the supported domain"):
        normalize_destination_schema(record)


# ---------------------------------------------------------------------------------------
# Consumed-schema fingerprint
# ---------------------------------------------------------------------------------------


def _dropdown(choices: list[str], *, optional: bool = True) -> dict[str, Any]:
    return {"kind": "Dropdown", "optional": optional, "default_value": None, "unique": False, "choices": choices}


_SNAPSHOT: dict[str, Any] = {
    "LocationSite": {
        "human_friendly_id": ["name__value"],
        "uniqueness_constraints": [],
        "attributes": {
            "name": {"kind": "Text", "optional": False, "default_value": None, "unique": True},
            # Mapped by the configuration below.
            "status": _dropdown(["active", "planned"]),
            # Optional and not mapped: compatible growth.
            "color": _dropdown(["red", "blue"]),
        },
        "relationships": {},
    }
}

_CONFIGURATION = SyncConfig(
    name="dropdown-choices",
    source=SyncAdapter(name="netbox"),
    destination=SyncAdapter(name="infrahub"),
    schema_mapping=[
        SchemaMappingModel(
            name="LocationSite", fields=[SchemaMappingField(name="name"), SchemaMappingField(name="status")]
        )
    ],
)


def _fingerprint(snapshot: dict[str, Any]) -> str:
    return compute_consumed_schema_fingerprint(
        configuration=_CONFIGURATION, snapshot=normalize_destination_schema(snapshot)
    )


def _with_choices(attribute: str, choices: list[str]) -> dict[str, Any]:
    snapshot = copy.deepcopy(_SNAPSHOT)
    snapshot["LocationSite"]["attributes"][attribute]["choices"] = choices
    return snapshot


def test_a_new_choice_of_a_mapped_dropdown_changes_the_fingerprint() -> None:
    assert _fingerprint(_with_choices("status", ["active", "planned", "retired"])) != _fingerprint(_SNAPSHOT)


def test_a_removed_choice_of_a_mapped_dropdown_changes_the_fingerprint() -> None:
    assert _fingerprint(_with_choices("status", ["active"])) != _fingerprint(_SNAPSHOT)


def test_the_order_the_destination_lists_the_choices_in_does_not_change_the_fingerprint() -> None:
    assert _fingerprint(_with_choices("status", ["planned", "active"])) == _fingerprint(_SNAPSHOT)


def test_a_choice_change_of_an_unmapped_dropdown_does_not_change_the_fingerprint() -> None:
    assert _fingerprint(_with_choices("color", ["red", "blue", "green"])) == _fingerprint(_SNAPSHOT)


def test_the_projection_names_the_choices_of_a_mapped_dropdown_and_none_for_other_fields() -> None:
    projection = canonical_consumed_schema_projection(
        configuration=_CONFIGURATION, snapshot=normalize_destination_schema(_SNAPSHOT)
    )
    fields = {field["name"]: field for field in projection[0]["fields"]}

    assert fields["status"]["choices"] == ["active", "planned"]
    assert "choices" not in fields["name"]
