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
