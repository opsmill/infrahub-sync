"""AR2: runtime model construction matches the generator over the supported domain."""

from __future__ import annotations

import importlib
import importlib.util
import json
import sys
import warnings
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import pytest
from diffsync import DiffSyncModel
from infrahub_sdk.schema import (
    AttributeSchema,
    NodeSchema,
    RelationshipKind,
    RelationshipSchema,
)
from infrahub_sdk.schema.main import AttributeKind

from infrahub_sync import (
    DiffSyncModelMixin,
    SchemaMappingField,
    SchemaMappingModel,
    SyncAdapter,
    SyncConfig,
    SyncInstance,
)
from infrahub_sync.adapters.infrahub import InfrahubModel
from infrahub_sync.configuration import capabilities as capabilities_module
from infrahub_sync.generator import ATTRIBUTE_KIND_MAP
from infrahub_sync.runtime_schema import (
    ATTRIBUTE_TYPE_DOMAIN,
    UnsupportedSchemaSemanticsError,
    build_runtime_models,
    mapped_attribute_kinds,
    normalize_destination_schema,
)
from infrahub_sync.utils import get_instance, render_adapter

if TYPE_CHECKING:
    from infrahub_sdk.schema import GenericSchema

REPO_ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT_DIR = REPO_ROOT / "tests" / "data" / "generator_schema_snapshots"
SchemaMapping = dict[str, "NodeSchema | GenericSchema"]


def _load_sdk_schema(snapshot_name: str) -> SchemaMapping:
    entries = json.loads((SNAPSHOT_DIR / snapshot_name).read_text(encoding="utf-8"))
    return {
        kind: getattr(importlib.import_module(entry["class_module"]), entry["class_name"]).model_validate(entry["data"])
        for kind, entry in entries.items()
    }


def _describe(model: type[DiffSyncModel]) -> dict[str, Any]:
    """The comparable surface of one model class."""
    return {
        "modelname": model._modelname,
        "identifiers": list(model._identifiers),
        "attributes": list(model._attributes),
        "children": dict(model._children),
        "base": model.__mro__[1].__mro__[1].__name__,
        "fields": {
            name: {"annotation": str(info.annotation).replace("typing.", ""), "default": repr(info.default)}
            for name, info in model.model_fields.items()
        },
    }


def _generated_models(
    instance: SyncInstance, schema: SchemaMapping, out_dir: Path, tag: str
) -> dict[str, type[DiffSyncModel]]:
    """Render the example with the shipping generator, then import what it wrote."""
    instance.directory = str(out_dir)
    render_adapter(sync_instance=instance, schema=schema)
    path = out_dir / instance.destination.name / "sync_models.py"
    spec = importlib.util.spec_from_file_location(f"runtime_parity_{tag}", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return {
        cast("type[DiffSyncModel]", obj)._modelname: cast("type[DiffSyncModel]", obj)
        for name, obj in vars(module).items()
        if isinstance(obj, type) and issubclass(obj, DiffSyncModel) and not name.startswith("_")
    }


def _runtime_models(
    configuration: SyncConfig, schema: SchemaMapping, base: type[DiffSyncModel]
) -> dict[str, type[DiffSyncModel]]:
    snapshot = normalize_destination_schema(capabilities_module._build_schema_snapshot(schema))
    return build_runtime_models(snapshot=snapshot, configuration=configuration, model_base=base)


def test_runtime_classes_match_the_generator_over_the_netbox_example(tmp_path: Path) -> None:
    instance = get_instance(name="from-netbox", directory=str(REPO_ROOT / "examples"))
    assert instance is not None
    schema = _load_sdk_schema("netbox_example_schema.json")

    runtime = _runtime_models(instance, schema, InfrahubModel)
    generated = _generated_models(instance, schema, tmp_path, "netbox")

    assert set(runtime) == set(generated)
    assert len(runtime) == 20
    assert {kind: _describe(model) for kind, model in runtime.items()} == {
        kind: _describe(model) for kind, model in generated.items()
    }


def test_a_mapped_component_relationship_stays_out_of_the_attributes(tmp_path: Path) -> None:
    node = NodeSchema(
        name="Device",
        namespace="Infra",
        attributes=[AttributeSchema(name="name", kind=AttributeKind.TEXT, unique=True)],
        relationships=[
            RelationshipSchema(name="interfaces", peer="InfraInterface", cardinality="many"),
            RelationshipSchema(name="site", peer="LocationSite", cardinality="one"),
        ],
    )
    node.relationships[0].kind = RelationshipKind.COMPONENT
    configuration = SyncConfig(
        name="component-example",
        source=SyncAdapter(name="netbox"),
        destination=SyncAdapter(name="infrahub"),
        schema_mapping=[
            SchemaMappingModel(
                name="InfraDevice",
                fields=[
                    SchemaMappingField(name="name"),
                    SchemaMappingField(name="interfaces"),
                    SchemaMappingField(name="site"),
                ],
            )
        ],
    )
    schema: SchemaMapping = {node.kind: node}

    runtime = _runtime_models(configuration, schema, InfrahubModel)
    generated = _generated_models(
        SyncInstance(**configuration.model_dump(), directory=str(tmp_path)), schema, tmp_path, "component"
    )

    assert "interfaces" in runtime["InfraDevice"].model_fields
    assert "interfaces" not in runtime["InfraDevice"]._attributes
    assert runtime["InfraDevice"]._children == {}
    assert _describe(runtime["InfraDevice"]) == _describe(generated["InfraDevice"])


class _SourceModelBase(DiffSyncModelMixin, DiffSyncModel):
    """Stands in for a source adapter's own model base, which needs no optional driver."""


def test_each_side_derives_from_its_own_resolved_model_base() -> None:
    instance = get_instance(name="from-netbox", directory=str(REPO_ROOT / "examples"))
    assert instance is not None
    schema = _load_sdk_schema("netbox_example_schema.json")

    source = _runtime_models(instance, schema, _SourceModelBase)
    destination = _runtime_models(instance, schema, InfrahubModel)

    assert issubclass(source["BuiltinTag"], _SourceModelBase)
    assert issubclass(destination["BuiltinTag"], InfrahubModel)
    assert source["BuiltinTag"] is not destination["BuiltinTag"]


def test_the_intermediate_base_only_declares_locals_the_resolved_base_lacks() -> None:
    instance = get_instance(name="from-netbox", directory=str(REPO_ROOT / "examples"))
    assert instance is not None
    schema = _load_sdk_schema("netbox_example_schema.json")

    with warnings.catch_warnings():
        # Declaring the locals on the base is the point of this case; the shadow warning
        # is what the generated-equivalent intermediate base exists to avoid repeating.
        warnings.simplefilter("ignore", UserWarning)

        class _CarriesLocals(InfrahubModel):
            local_id: str | None = None
            local_data: Any | None = None

    built = build_runtime_models(
        snapshot=normalize_destination_schema(capabilities_module._build_schema_snapshot(schema)),
        configuration=instance,
        model_base=_CarriesLocals,
    )

    intermediate = built["BuiltinTag"].__mro__[1]
    assert set(intermediate.__annotations__) == set()


def test_two_configurations_sharing_a_kind_get_distinct_classes() -> None:
    schema = _load_sdk_schema("netbox_example_schema.json")
    snapshot = normalize_destination_schema(capabilities_module._build_schema_snapshot(schema))

    def _configuration(fields: list[str]) -> SyncConfig:
        return SyncConfig(
            name="shared-kind",
            source=SyncAdapter(name="netbox"),
            destination=SyncAdapter(name="infrahub"),
            schema_mapping=[
                SchemaMappingModel(
                    name="BuiltinTag",
                    identifiers=["name"],
                    fields=[SchemaMappingField(name=field) for field in fields],
                )
            ],
        )

    first = build_runtime_models(snapshot=snapshot, configuration=_configuration(["name"]), model_base=InfrahubModel)
    second = build_runtime_models(
        snapshot=snapshot, configuration=_configuration(["name", "description"]), model_base=InfrahubModel
    )

    assert first["BuiltinTag"] is not second["BuiltinTag"]
    assert set(first["BuiltinTag"].model_fields) < set(second["BuiltinTag"].model_fields)


def test_an_attribute_kind_outside_the_closed_table_refuses_before_extraction() -> None:
    snapshot = normalize_destination_schema(
        {
            "InfraDevice": {
                "human_friendly_id": ["name__value"],
                "uniqueness_constraints": [["name__value"]],
                "attributes": {
                    "name": {"kind": "Text", "optional": False, "default_value": None, "unique": True},
                    "bandwidth": {"kind": "Bandwidth", "optional": True, "default_value": None, "unique": False},
                },
                "relationships": {},
            }
        }
    )
    configuration = SyncConfig(
        name="unknown-kind",
        source=SyncAdapter(name="netbox"),
        destination=SyncAdapter(name="infrahub"),
        schema_mapping=[
            SchemaMappingModel(
                name="InfraDevice",
                fields=[SchemaMappingField(name="name"), SchemaMappingField(name="bandwidth")],
            )
        ],
    )

    with pytest.raises(UnsupportedSchemaSemanticsError):
        build_runtime_models(snapshot=snapshot, configuration=configuration, model_base=InfrahubModel)


_KIND_DEFAULTS: dict[str, object] = {
    "Text": "a-default",
    "String": "a-default",
    "TextArea": "line one\nline two",
    "DateTime": "2026-08-30T00:00:00+00:00",
    "HashedPassword": "hashed",
    "Dropdown": "leaf",
    "MacAddress": "00:11:22:33:44:55",
    "IPHost": "10.0.0.1/32",
    "IPNetwork": "10.0.0.0/24",
    "Number": 7,
    "Integer": 7,
    "Boolean": True,
    "Checkbox": False,
    "List": ["alpha", "beta"],
}


# `Integer` is in the generator's own kind map, so the closed table keeps it, but the
# SDK's AttributeKind cannot express it — a live destination therefore never declares it,
# and no generator oracle can be rendered for it. It is asserted directly instead.
UNRENDERABLE_KINDS = frozenset({"Integer"})


def _matrix_schema() -> tuple[SchemaMapping, SyncConfig]:
    """Every admitted attribute kind in every required/optional/default state.

    Three attributes per kind — required, optional without a default, optional with one —
    plus the four relationship shapes, so the oracle covers the declared domain rather
    than whichever states one captured example happens to contain.
    """
    attributes = [AttributeSchema(name="key", kind=AttributeKind.TEXT, unique=True)]
    field_names = ["key"]
    for kind, default in sorted(_KIND_DEFAULTS.items()):
        if kind in UNRENDERABLE_KINDS:
            continue
        slug = kind.lower()
        attributes.extend(
            [
                AttributeSchema(name=f"{slug}_required", kind=AttributeKind(kind), optional=False),
                AttributeSchema(name=f"{slug}_optional", kind=AttributeKind(kind), optional=True),
                AttributeSchema(name=f"{slug}_default", kind=AttributeKind(kind), optional=True, default_value=default),
            ]
        )
        field_names.extend([f"{slug}_required", f"{slug}_optional", f"{slug}_default"])
    relationships = [
        RelationshipSchema(name="one_required", peer="LocationSite", cardinality="one", optional=False),
        RelationshipSchema(name="one_optional", peer="LocationSite", cardinality="one", optional=True),
        RelationshipSchema(name="many_required", peer="BuiltinTag", cardinality="many", optional=False),
        RelationshipSchema(name="many_optional", peer="BuiltinTag", cardinality="many", optional=True),
        RelationshipSchema(name="component_many", peer="InfraInterface", cardinality="many", optional=True),
    ]
    relationships[-1].kind = RelationshipKind.COMPONENT
    field_names.extend(relationship.name for relationship in relationships)
    node = NodeSchema(name="Device", namespace="Infra", attributes=attributes, relationships=relationships)
    configuration = SyncConfig(
        name="kind-matrix",
        source=SyncAdapter(name="netbox"),
        destination=SyncAdapter(name="infrahub"),
        schema_mapping=[
            SchemaMappingModel(name="InfraDevice", fields=[SchemaMappingField(name=name) for name in field_names])
        ],
    )
    return {node.kind: node}, configuration


def test_every_admitted_kind_and_state_matches_the_generator(tmp_path: Path) -> None:
    schema, configuration = _matrix_schema()

    runtime = _runtime_models(configuration, schema, InfrahubModel)
    generated = _generated_models(
        SyncInstance(**configuration.model_dump(), directory=str(tmp_path)), schema, tmp_path, "matrix"
    )

    described = _describe(runtime["InfraDevice"])
    assert described == _describe(generated["InfraDevice"])
    # The matrix really covers the declared domain and every state of it.
    assert set(_KIND_DEFAULTS) == set(ATTRIBUTE_TYPE_DOMAIN)
    assert {kind for kind in ATTRIBUTE_TYPE_DOMAIN if kind not in AttributeKind.__members__.values()} == (
        UNRENDERABLE_KINDS
    )
    for kind in set(ATTRIBUTE_TYPE_DOMAIN) - UNRENDERABLE_KINDS:
        slug = kind.lower()
        assert described["fields"][f"{slug}_required"]["default"] == "PydanticUndefined"
        assert described["fields"][f"{slug}_optional"]["default"] == "None"
        assert described["fields"][f"{slug}_default"]["default"] == repr(_KIND_DEFAULTS[kind])
    assert described["fields"]["one_required"]["annotation"] == "<class 'str'>"
    assert described["fields"]["many_required"]["default"] == "[]"
    assert "component_many" in described["fields"]
    assert "component_many" not in described["attributes"]


@pytest.mark.parametrize(
    "default_value",
    [
        pytest.param('a"b', id="double-quote"),
        pytest.param("a'b", id="single-quote"),
        pytest.param("a\"'b", id="both-quotes"),
        pytest.param("a\nb", id="newline"),
        pytest.param("a\\b", id="backslash"),
        pytest.param("a\tb", id="tab"),
        pytest.param("a\rb", id="carriage-return"),
        pytest.param("", id="empty"),
        pytest.param("caf\u00e9", id="non-ascii"),
        pytest.param("\x00", id="null-byte"),
    ],
)
def test_a_string_default_matches_the_generator_exactly(tmp_path: Path, default_value: str) -> None:
    # A default is inside the declared closed domain, so parity has to hold for every
    # string a destination can declare — not only the ones that need no escaping.
    node = NodeSchema(
        name="Device",
        namespace="Infra",
        attributes=[
            AttributeSchema(name="name", kind=AttributeKind.TEXT, unique=True),
            AttributeSchema(name="label", kind=AttributeKind.TEXT, optional=True, default_value=default_value),
        ],
    )
    configuration = SyncConfig(
        name="string-default",
        source=SyncAdapter(name="netbox"),
        destination=SyncAdapter(name="infrahub"),
        schema_mapping=[
            SchemaMappingModel(
                name="InfraDevice",
                fields=[SchemaMappingField(name="name"), SchemaMappingField(name="label")],
            )
        ],
    )
    schema: SchemaMapping = {node.kind: node}

    runtime = _runtime_models(configuration, schema, InfrahubModel)
    generated = _generated_models(
        SyncInstance(**configuration.model_dump(), directory=str(tmp_path)), schema, tmp_path, "default"
    )

    assert runtime["InfraDevice"].model_fields["label"].default == default_value
    assert _describe(runtime["InfraDevice"]) == _describe(generated["InfraDevice"])


@pytest.mark.parametrize(
    ("snapshot_name", "example_name"),
    [("netbox_example_schema.json", "from-netbox"), ("custom_example_schema.json", "custom-example")],
)
def test_every_captured_mapped_attribute_kind_is_inside_the_closed_table(snapshot_name: str, example_name: str) -> None:
    instance = get_instance(name=example_name, directory=str(REPO_ROOT / "examples"))
    assert instance is not None
    snapshot = normalize_destination_schema(capabilities_module._build_schema_snapshot(_load_sdk_schema(snapshot_name)))

    captured = mapped_attribute_kinds(snapshot, instance)

    assert captured
    assert captured <= set(ATTRIBUTE_TYPE_DOMAIN)


@pytest.mark.parametrize(
    ("optional", "default_value", "annotation", "default"),
    [
        pytest.param(False, None, "<class 'int'>", "PydanticUndefined", id="required"),
        pytest.param(True, None, "int | None", "None", id="optional"),
        pytest.param(True, 7, "int | None", "7", id="optional-with-default"),
    ],
)
def test_the_unrenderable_integer_kind_matches_the_generator_type_map(
    *, optional: bool, default_value: object, annotation: str, default: str
) -> None:
    # The SDK's AttributeKind cannot express `Integer`, so there is no rendered oracle to
    # compare against; the closed table's mapping is held to the generator's own map.
    assert {"Integer"} == UNRENDERABLE_KINDS
    assert ATTRIBUTE_KIND_MAP["Integer"] == "int"
    snapshot = normalize_destination_schema(
        {
            "InfraDevice": {
                "human_friendly_id": ["name__value"],
                "uniqueness_constraints": [["name__value"]],
                "attributes": {
                    "key": {"kind": "Text", "optional": False, "default_value": None, "unique": True},
                    "count": {
                        "kind": "Integer",
                        "optional": optional,
                        "default_value": default_value,
                        "unique": False,
                    },
                },
                "relationships": {},
            }
        }
    )
    configuration = SyncConfig(
        name="integer-kind",
        source=SyncAdapter(name="netbox"),
        destination=SyncAdapter(name="infrahub"),
        schema_mapping=[
            SchemaMappingModel(
                name="InfraDevice",
                fields=[SchemaMappingField(name="key"), SchemaMappingField(name="count")],
            )
        ],
    )

    built = build_runtime_models(snapshot=snapshot, configuration=configuration, model_base=InfrahubModel)

    info = built["InfraDevice"].model_fields["count"]
    assert str(info.annotation).replace("typing.", "") == annotation
    assert repr(info.default) == default
