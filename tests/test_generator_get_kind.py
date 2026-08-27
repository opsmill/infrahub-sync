"""Tests for infrahub_sync.generator.get_kind.

get_kind() decides the pydantic type annotation for each generated DiffSync
model field. It must work on whatever object `client.schema.all()` hands back
-- infrahub-sdk's read-side schema classes (AttributeSchemaAPI /
RelationshipSchemaAPI) -- not just on the write-side AttributeSchema /
RelationshipSchema classes used to build schema payloads.

Those two class families are not guaranteed to share a common base: at
infrahub-sdk 1.18.1 the read-side classes happened to subclass the write-side
ones, so an `isinstance(item, AttributeSchema)` check worked; at 1.23.0 that
inheritance was removed, and the check silently stopped matching anything,
making get_kind() fall through to its "str" default for every attribute and
relationship (see https://github.com/opsmill/infrahub-sync/issues/187).

`_FakeAttribute`/`_FakeRelationship` below deliberately do NOT inherit from
either SDK schema family. They exist so this suite fails if get_kind() is
ever changed back to an isinstance-based check -- regardless of which
infrahub-sdk version the lockfile happens to resolve at the time.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from infrahub_sdk.schema import AttributeSchema, RelationshipSchema
from infrahub_sdk.schema.main import AttributeKind, AttributeSchemaAPI, RelationshipSchemaAPI

from infrahub_sync.generator import get_kind


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
        (_FakeAttribute(kind="Text", optional=True, default_value="foo"), 'str | None = "foo"'),
        (_FakeAttribute(kind="Number", optional=True, default_value=5), "int | None = 5"),
        (_FakeAttribute(kind="Boolean", optional=True, default_value=True), "bool | None = True"),
    ],
)
def test_get_kind_for_attribute_shaped_objects(attribute: _FakeAttribute, expected: str) -> None:
    assert get_kind(attribute) == expected


@pytest.mark.parametrize(
    ("relationship", "expected"),
    [
        (_FakeRelationship(cardinality="one"), "str"),
        (_FakeRelationship(cardinality="one", optional=True), "str | None = None"),
        (_FakeRelationship(cardinality="many"), "list[str] = []"),
        (_FakeRelationship(cardinality="many", optional=True), "list[str] | None = []"),
    ],
)
def test_get_kind_for_relationship_shaped_objects(relationship: _FakeRelationship, expected: str) -> None:
    assert get_kind(relationship) == expected


def test_get_kind_against_real_sdk_read_side_schema_classes() -> None:
    """Sanity check against the actual classes `client.schema.all()` returns.

    Complements the fakes above with the real infrahub-sdk read-side types,
    so a shape drift in the SDK itself (a renamed/removed field) is caught
    here too, not just a reintroduced isinstance check.
    """
    optional_attr = AttributeSchemaAPI(id="1", name="description", kind=AttributeKind.TEXT, optional=True)
    assert get_kind(optional_attr) == "str | None = None"

    required_attr = AttributeSchemaAPI(id="2", name="name", kind=AttributeKind.TEXT, optional=False)
    assert get_kind(required_attr) == "str"

    many_rel = RelationshipSchemaAPI(id="3", name="tags", peer="BuiltinTag", cardinality="many", optional=True)
    assert get_kind(many_rel) == "list[str] | None = []"

    one_rel = RelationshipSchemaAPI(id="4", name="status", peer="StatusGeneric", cardinality="one", optional=False)
    assert get_kind(one_rel) == "str"
