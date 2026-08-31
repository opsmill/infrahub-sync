"""R2: a class-valued entry point resolves both the adapter class and the model base.

A distribution publishing `myplugin = myplugin.adapters:MyAdapter` is ordinary; the
entry point names the adapter, and the model base has to come from the module that
adapter is defined in.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from diffsync import Adapter, DiffSyncModel

from infrahub_sync import SyncAdapter, SyncInstance
from infrahub_sync.plugin_loader import (
    PluginLoadError,
    resolve_installed_adapter_class,
    resolve_installed_model_base,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

ENTRY_POINT = "class_valued_entry_point"


def _plugin_module(*, with_model: bool) -> types.ModuleType:
    """One installed plugin distribution's adapter module."""
    module = types.ModuleType("class_valued_plugin")

    class PluginAdapter(Adapter):
        type = "ClassValuedPlugin"

    PluginAdapter.__module__ = module.__name__
    module.PluginAdapter = PluginAdapter  # ty: ignore[unresolved-attribute]
    if with_model:

        class PluginModel(DiffSyncModel):
            _modelname = "PluginModel"
            _identifiers = ("name",)
            name: str

        PluginModel.__module__ = module.__name__
        module.PluginModel = PluginModel  # ty: ignore[unresolved-attribute]
    return module


class _EntryPoint:
    def __init__(self, name: str, target: object) -> None:
        self.name = name
        self._target = target

    def load(self) -> object:
        return self._target


class _EntryPoints:
    def __init__(self, entry_point: _EntryPoint) -> None:
        self._entry_point = entry_point

    def select(self, *, group: str, name: str) -> tuple[_EntryPoint, ...]:
        if group == "infrahub_sync.adapters" and name == self._entry_point.name:
            return (self._entry_point,)
        return ()


def _publish(monkeypatch: pytest.MonkeyPatch, module: types.ModuleType, *, value: object) -> None:
    """Publish `value` under the plugin entry-point group, with its module importable."""
    monkeypatch.setitem(sys.modules, module.__name__, module)
    monkeypatch.setattr(
        "infrahub_sync.plugin_loader.entry_points", lambda: _EntryPoints(_EntryPoint(ENTRY_POINT, value))
    )


@pytest.fixture(name="adapter")
def _adapter() -> SyncAdapter:
    return SyncAdapter(name="plugin", adapter=ENTRY_POINT)


def _instance(adapter: SyncAdapter) -> SyncInstance:
    return SyncInstance(
        name="entry-point-resolution", source=adapter, destination=SyncAdapter(name="infrahub"), directory="/none"
    )


def test_a_class_valued_entry_point_resolves_its_adapter_class(
    monkeypatch: pytest.MonkeyPatch, adapter: SyncAdapter
) -> None:
    module = _plugin_module(with_model=True)
    _publish(monkeypatch, module, value=module.PluginAdapter)

    assert resolve_installed_adapter_class(adapter) is module.PluginAdapter


def test_a_class_valued_entry_point_resolves_the_model_base_from_its_module(
    monkeypatch: pytest.MonkeyPatch, adapter: SyncAdapter
) -> None:
    # The entry point names the adapter; the model base is the DiffSync model its
    # defining module declares, not the adapter class itself.
    module = _plugin_module(with_model=True)
    _publish(monkeypatch, module, value=module.PluginAdapter)

    resolved = resolve_installed_model_base(adapter)

    assert resolved is module.PluginModel
    assert resolved is not module.PluginAdapter


def test_a_class_valued_entry_point_without_a_model_refuses(
    monkeypatch: pytest.MonkeyPatch, adapter: SyncAdapter
) -> None:
    module = _plugin_module(with_model=False)
    _publish(monkeypatch, module, value=module.PluginAdapter)

    with pytest.raises(PluginLoadError):
        resolve_installed_model_base(adapter)


def test_a_model_valued_entry_point_resolves_the_adapter_from_its_module(
    monkeypatch: pytest.MonkeyPatch, adapter: SyncAdapter
) -> None:
    # The mirror case: whichever class the entry point names, the other one comes from
    # the same module.
    module = _plugin_module(with_model=True)
    _publish(monkeypatch, module, value=module.PluginModel)

    assert resolve_installed_adapter_class(adapter) is module.PluginAdapter
    assert resolve_installed_model_base(adapter) is module.PluginModel


def test_a_module_valued_entry_point_still_resolves_both(monkeypatch: pytest.MonkeyPatch, adapter: SyncAdapter) -> None:
    module = _plugin_module(with_model=True)
    _publish(monkeypatch, module, value=module)

    assert resolve_installed_adapter_class(adapter) is module.PluginAdapter
    assert resolve_installed_model_base(adapter) is module.PluginModel


def test_an_entry_point_naming_an_unusable_object_refuses(
    monkeypatch: pytest.MonkeyPatch, adapter: SyncAdapter
) -> None:
    module = _plugin_module(with_model=True)
    _publish(monkeypatch, module, value=object())

    with pytest.raises(PluginLoadError):
        resolve_installed_adapter_class(adapter)


def test_a_class_valued_entry_point_reaches_a_runtime_model_plan(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The execution-level case: a plan built on a class-valued entry-point source."""
    from infrahub_sync.configuration import parse_configuration_package
    from infrahub_sync.configuration.runtime import resolve_runtime_instance
    from infrahub_sync.runtime_schema import build_runtime_model_plan
    from infrahub_sync.runtime_schema import worker as worker_module
    from tests.configuration.validation_packages import package_data

    snapshot = {
        "BuiltinTag": {
            "human_friendly_id": ["name__value"],
            "uniqueness_constraints": [["name__value"]],
            "attributes": {"name": {"kind": "Text", "optional": False, "default_value": None, "unique": True}},
            "relationships": {},
        }
    }
    module = _plugin_module(with_model=True)
    _publish(monkeypatch, module, value=module.PluginAdapter)
    monkeypatch.setattr(worker_module, "read_destination_schema_snapshot", lambda _package, _branch: snapshot)
    monkeypatch.setenv("NETBOX_TOKEN", "entry-point-canary")
    monkeypatch.setenv("INFRAHUB_API_TOKEN", "entry-point-canary")
    content = package_data()
    content["configuration"]["schema_mapping"] = [
        {"name": "BuiltinTag", "mapping": "extras.tags", "fields": [{"name": "name", "mapping": "name"}]}
    ]
    content["configuration"]["source"]["adapter"] = ENTRY_POINT
    package = parse_configuration_package(content)
    instance = resolve_runtime_instance(package, directory=str(tmp_path))

    plan = build_runtime_model_plan(package=package, instance=instance, run_branch=None, scope="both")

    assert plan.source is not None
    assert plan.source.adapter_class is module.PluginAdapter
    assert issubclass(plan.source.models["BuiltinTag"], module.PluginModel)


@pytest.fixture(autouse=True)
def _no_module_leak() -> Iterator[None]:
    yield
    sys.modules.pop("class_valued_plugin", None)
