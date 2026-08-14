from __future__ import annotations

import warnings
from pathlib import Path
from typing import Any, cast

import pytest
from infrahub_sdk.schema import AttributeSchema, NodeSchema
from infrahub_sdk.schema.main import AttributeKind

from infrahub_sync import SchemaMappingField, SchemaMappingModel, SyncAdapter, SyncConfig
from infrahub_sync.generator import render_template


@pytest.mark.parametrize(
    ("adapter_paths", "loader_call"),
    [
        (None, "PluginLoader.from_env_and_args(adapter_paths=[])"),
        (["/opt/sync-adapters"], "PluginLoader.from_env_and_args(adapter_paths=['/opt/sync-adapters'])"),
    ],
)
def test_generated_models_are_reproducible_and_valid_python(
    tmp_path: Path,
    adapter_paths: list[str] | None,
    loader_call: str,
) -> None:
    adapter = SyncAdapter(name="custom", adapter="package.module:CustomModel")
    config = SyncConfig(
        name="generator-test",
        source=adapter,
        destination=SyncAdapter(name="infrahub"),
        adapters_path=adapter_paths,
        schema_mapping=[
            SchemaMappingModel(
                name="InfraDevice",
                identifiers=["name"],
                fields=[SchemaMappingField(name="name"), SchemaMappingField(name="type")],
            )
        ],
    )
    node = NodeSchema(
        name="Device",
        namespace="Infra",
        attributes=[
            AttributeSchema(name="type", kind=AttributeKind.TEXT),
            AttributeSchema(name="name", kind=AttributeKind.TEXT, unique=True),
        ],
    )

    render_template(
        template_file=Path("diffsync_models.j2"),
        output_dir=tmp_path,
        output_file=Path("sync_models.py"),
        context={"schema": {node.kind: node}, "adapter": adapter, "config": config},
    )

    generated = (tmp_path / "sync_models.py").read_text(encoding="utf-8")
    compile(generated, "sync_models.py", "exec")

    assert "from typing import Any\n" in generated
    assert "from typing import Any, List" not in generated
    assert f"_loader = {loader_call}" in generated
    assert '_spec = "package.module"' in generated
    assert "except Exception:  # noqa: BLE001 -- generated adapters need a safe import fallback" in generated
    assert "    type: str\n    name: str\n" in generated
    assert "class _GeneratedModelBase(_ModelBaseClass):\n" in generated
    assert '    if "local_id" not in getattr(_ModelBaseClass, "model_fields", {}):\n' in generated
    assert "class InfraDevice(_GeneratedModelBase):\n" in generated

    namespace: dict[str, object] = {}
    exec(compile(generated, "sync_models.py", "exec"), namespace)  # noqa: S102
    model_class = cast("type[Any]", namespace["InfraDevice"])
    model_class.model_rebuild(_types_namespace=namespace)
    instance = model_class(name="leaf01", type="switch", local_id="node-1", local_data={"source": "fixture"})
    assert instance.local_id == "node-1"
    assert instance.local_data == {"source": "fixture"}


def test_generated_models_do_not_shadow_adapter_model_fields(tmp_path: Path) -> None:
    adapter = SyncAdapter(name="infrahub")
    config = SyncConfig(
        name="generator-test",
        source=adapter,
        destination=SyncAdapter(name="infrahub"),
        schema_mapping=[
            SchemaMappingModel(
                name="InfraDevice",
                identifiers=["name"],
                fields=[SchemaMappingField(name="name"), SchemaMappingField(name="type")],
            )
        ],
    )
    node = NodeSchema(
        name="Device",
        namespace="Infra",
        attributes=[
            AttributeSchema(name="type", kind=AttributeKind.TEXT),
            AttributeSchema(name="name", kind=AttributeKind.TEXT, unique=True),
        ],
    )

    render_template(
        template_file=Path("diffsync_models.j2"),
        output_dir=tmp_path,
        output_file=Path("sync_models.py"),
        context={"schema": {node.kind: node}, "adapter": adapter, "config": config},
    )
    generated = (tmp_path / "sync_models.py").read_text(encoding="utf-8")
    namespace: dict[str, object] = {}

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        exec(compile(generated, "sync_models.py", "exec"), namespace)  # noqa: S102

    assert not [warning for warning in caught if "shadows an attribute" in str(warning.message)]
    model_class = cast("type[Any]", namespace["InfraDevice"])
    model_class.model_rebuild(_types_namespace=namespace)
    instance = model_class(name="leaf01", type="switch", local_id="node-1", local_data={"source": "fixture"})
    assert instance.local_id == "node-1"
    assert instance.local_data == {"source": "fixture"}
