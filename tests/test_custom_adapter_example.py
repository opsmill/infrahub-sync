"""The custom adapter example reads its mock database through the base-class read loop.

`examples/custom_adapter/custom_adapter_src/custom_adapter.py` implements `list_records` and
`record_to_data` only (spec 020 task T082). These tests load it from its file, as the
development plugin loader does, and read the five devices of `mock_db.json`.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

import pytest
import yaml

from infrahub_sync import SyncAdapter, SyncConfig

if TYPE_CHECKING:
    from types import ModuleType

EXAMPLE_DIR = Path(__file__).resolve().parent.parent / "examples" / "custom_adapter"
ADAPTER_FILE = EXAMPLE_DIR / "custom_adapter_src" / "custom_adapter.py"
MOCK_DB = EXAMPLE_DIR / "custom_adapter_src" / "mock_db.json"


def _load_example() -> ModuleType:
    spec = importlib.util.spec_from_file_location("custom_adapter_example", ADAPTER_FILE)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _config(filters: list[dict[str, Any]] | None = None) -> SyncConfig:
    data = yaml.safe_load((EXAMPLE_DIR / "config.yml").read_text(encoding="utf-8"))
    data["source"]["settings"]["db_path"] = str(MOCK_DB)
    if filters is not None:
        data["schema_mapping"][0]["filters"] = filters
    return SyncConfig(**data)


def _adapter(module: ModuleType, config: SyncConfig) -> Any:  # noqa: ANN401 - the example's adapter class
    class Device(module.MockdbModel):
        _modelname = "InfraDevice"
        _identifiers = ("name",)
        _attributes = ("type",)
        name: str
        type: str
        local_id: str | None = None

    class ExampleAdapter(module.MockdbAdapter):
        top_level: ClassVar[list[str]] = ["InfraDevice"]
        InfraDevice = Device

    return ExampleAdapter(target="source", adapter=SyncAdapter(**config.source.model_dump()), config=config)


@pytest.fixture(scope="module", name="example")
def example_module() -> ModuleType:
    return _load_example()


def test_example_reads_every_device_with_its_source_record_id(example: ModuleType) -> None:
    adapter = _adapter(example, _config())
    adapter.load()
    devices = {device.name: device for device in adapter.get_all("InfraDevice")}
    assert sorted(devices) == ["core01", "core02", "core03", "edge01", "edge02"]
    assert devices["core01"].type == "juniper mx204"
    assert devices["core01"].local_id == "core01"
    assert adapter.load_report.entries == []


def test_example_records_the_devices_a_filter_removes(example: ModuleType) -> None:
    adapter = _adapter(example, _config(filters=[{"field": "name", "operation": "contains", "value": "core"}]))
    adapter.load()
    assert sorted(device.name for device in adapter.get_all("InfraDevice")) == ["core01", "core02", "core03"]
    assert adapter.load_report.was_filtered("InfraDevice", "edge01")
