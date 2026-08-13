from __future__ import annotations

from pathlib import Path

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
