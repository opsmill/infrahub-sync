"""Tests for infrahub_sync.generator annotation filters and the generated model template.

These functions decide the pydantic type annotation for each generated DiffSync
model field. They must work on whatever object `client.schema.all()` hands back
-- infrahub-sdk's read-side schema classes (AttributeSchemaAPI /
RelationshipSchemaAPI) -- not just on the write-side AttributeSchema /
RelationshipSchema classes used to build schema payloads.

SDK read-side classes need not inherit from the write-side ones: a newer infrahub-sdk
detached AttributeSchemaAPI from AttributeSchema (see #187), while older versions -- including
the one currently pinned -- still share that inheritance. These filters must therefore rely on
the attribute/relationship shape alone and never on isinstance() against either hierarchy, and
the fakes below keep the detached case covered even where the SDK still shares it.
"""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from infrahub_sdk.schema import AttributeSchema, RelationshipSchema
from infrahub_sdk.schema.main import (
    AttributeKind,
    AttributeSchemaAPI,
    NodeSchemaAPI,
    RelationshipSchemaAPI,
)
from pydantic import ValidationError

from infrahub_sync import SchemaMappingField, SchemaMappingModel, SyncAdapter, SyncConfig
from infrahub_sync.generator import (
    get_attribute_type_annotation,
    get_relationship_type_annotation,
    render_template,
)

if TYPE_CHECKING:
    from types import ModuleType


@dataclass
class _FakeAttribute:
    """Duck-types an attribute schema without subclassing any SDK class."""

    kind: str
    optional: bool = False
    default_value: Any = None


@dataclass
class _FakeRelationship:
    """Duck-types a relationship schema without subclassing any SDK class."""

    cardinality: str
    optional: bool = False


def _render_sync_models(tmp_path: Path, node: NodeSchemaAPI) -> ModuleType:
    """Render diffsync_models.j2 through the production render_template and import the result."""
    field_names = [attr.name for attr in node.attributes] + [rel.name for rel in node.relationships]
    config = SyncConfig(
        name="generator-test",
        source=SyncAdapter(name="infrahub"),
        destination=SyncAdapter(name="infrahub"),
        schema_mapping=[
            SchemaMappingModel(name=node.kind, fields=[SchemaMappingField(name=name) for name in field_names])
        ],
    )
    render_template(
        template_file=Path("diffsync_models.j2"),
        output_dir=tmp_path,
        output_file=Path("sync_models.py"),
        context={"schema": {node.kind: node}, "adapter": config.source, "config": config},
    )

    module_name = f"sync_models_{tmp_path.name}"
    spec = importlib.util.spec_from_file_location(module_name, tmp_path / "sync_models.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def test_fake_schema_objects_do_not_subclass_sdk_schema_classes() -> None:
    """Pins down the premise the other tests rely on.

    If this starts failing, the fakes below stopped simulating the
    decoupled-class scenario and no longer guard the regression.
    """
    assert not isinstance(_FakeAttribute(kind="Text"), AttributeSchema)
    assert not isinstance(_FakeRelationship(cardinality="one"), RelationshipSchema)


@pytest.mark.parametrize(
    ("attribute", "expected"),
    [
        (_FakeAttribute(kind="Text"), "str"),
        (_FakeAttribute(kind="Number"), "int"),
        (_FakeAttribute(kind="Boolean"), "bool"),
        (_FakeAttribute(kind="SomeUnmappedKind"), "str"),
        (_FakeAttribute(kind="Text", optional=True), "str | None = None"),
        (_FakeAttribute(kind="Text", optional=True, default_value="foo"), "str | None = 'foo'"),
        (_FakeAttribute(kind="Number", optional=True, default_value=5), "int | None = 5"),
        (_FakeAttribute(kind="Boolean", optional=True, default_value=True), "bool | None = True"),
    ],
)
def test_get_attribute_type_annotation_for_attribute_shaped_objects(attribute: _FakeAttribute, expected: str) -> None:
    """Attribute kind, optionality and default value together decide the generated annotation."""
    assert get_attribute_type_annotation(attribute) == expected


@pytest.mark.parametrize(
    ("relationship", "expected"),
    [
        (_FakeRelationship(cardinality="one"), "str"),
        (_FakeRelationship(cardinality="one", optional=True), "str | None = None"),
        (_FakeRelationship(cardinality="many"), "list[str] = []"),
        (_FakeRelationship(cardinality="many", optional=True), "list[str] | None = []"),
    ],
)
def test_get_relationship_type_annotation_for_relationship_shaped_objects(
    relationship: _FakeRelationship, expected: str
) -> None:
    """Relationship cardinality and optionality together decide the generated annotation."""
    assert get_relationship_type_annotation(relationship) == expected


def test_get_attribute_type_annotation_against_real_sdk_read_side_schema_classes() -> None:
    """Sanity check against the actual classes `client.schema.all()` returns."""
    optional_attr = AttributeSchemaAPI(id="1", name="description", kind=AttributeKind.TEXT, optional=True)
    assert get_attribute_type_annotation(optional_attr) == "str | None = None"

    required_attr = AttributeSchemaAPI(id="2", name="name", kind=AttributeKind.TEXT, optional=False)
    assert get_attribute_type_annotation(required_attr) == "str"

    number_attr = AttributeSchemaAPI(id="3", name="mtu", kind=AttributeKind.NUMBER, optional=True, default_value=1500)
    assert get_attribute_type_annotation(number_attr) == "int | None = 1500"

    boolean_attr = AttributeSchemaAPI(id="4", name="enabled", kind=AttributeKind.BOOLEAN, optional=False)
    assert get_attribute_type_annotation(boolean_attr) == "bool"


def test_get_relationship_type_annotation_against_real_sdk_read_side_schema_classes() -> None:
    """Sanity check against the actual classes `client.schema.all()` returns."""
    many_rel = RelationshipSchemaAPI(id="3", name="tags", peer="BuiltinTag", cardinality="many", optional=True)
    assert get_relationship_type_annotation(many_rel) == "list[str] | None = []"

    one_rel = RelationshipSchemaAPI(id="4", name="status", peer="StatusGeneric", cardinality="one", optional=False)
    assert get_relationship_type_annotation(one_rel) == "str"


def test_rendered_model_preserves_read_side_schema_semantics(tmp_path: Path) -> None:
    """The generated model keeps optionality, scalar defaults, many relationships and identifiers."""
    node = NodeSchemaAPI(
        name="Widget",
        namespace="Testing",
        attributes=[
            AttributeSchemaAPI(id="a1", name="name", kind=AttributeKind.TEXT, unique=True, optional=False),
            AttributeSchemaAPI(id="a2", name="description", kind=AttributeKind.TEXT, optional=True),
            AttributeSchemaAPI(id="a3", name="mtu", kind=AttributeKind.NUMBER, optional=True, default_value=1500),
            AttributeSchemaAPI(id="a4", name="enabled", kind=AttributeKind.BOOLEAN, optional=True, default_value=False),
        ],
        relationships=[
            RelationshipSchemaAPI(id="r1", name="tags", peer="BuiltinTag", cardinality="many", optional=True),
            RelationshipSchemaAPI(id="r2", name="status", peer="StatusGeneric", cardinality="one", optional=True),
        ],
    )

    model = _render_sync_models(tmp_path, node).TestingWidget
    assert model._identifiers == ("name",)
    assert {name: model.model_fields[name].annotation for name in model._identifiers + model._attributes} == {
        "name": str,
        "description": str | None,
        "mtu": int | None,
        "enabled": bool | None,
        "tags": list[str] | None,
        "status": str | None,
    }

    widget = model(name="widget-1")
    assert widget.description is None
    assert widget.mtu == 1500
    assert widget.enabled is False
    assert widget.tags == []
    assert widget.status is None

    with pytest.raises(ValidationError):
        model()


@pytest.mark.parametrize(
    "default_value",
    [
        "plain",
        "",
        'double "quoted"',
        "single 'quoted'",
        "back\\slash",
        "escape\\tsequence",
        "trailing\\",
        "first\nsecond",
        "mixed \"both\" 'kinds' \\ and\nnewline",
    ],
)
def test_rendered_model_round_trips_string_defaults(tmp_path: Path, default_value: str) -> None:
    """A string default must render as a Python literal that reproduces the schema value exactly."""
    node = NodeSchemaAPI(
        name="Widget",
        namespace="Testing",
        attributes=[
            AttributeSchemaAPI(id="a1", name="name", kind=AttributeKind.TEXT, unique=True, optional=False),
            AttributeSchemaAPI(
                id="a2", name="label", kind=AttributeKind.TEXT, optional=True, default_value=default_value
            ),
        ],
    )

    model = _render_sync_models(tmp_path, node).TestingWidget
    assert model(name="widget-1").label == default_value
