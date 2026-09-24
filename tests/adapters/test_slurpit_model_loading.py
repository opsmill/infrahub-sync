"""Exercise documented Slurp'it mappings with real SDK models and signatures."""

from __future__ import annotations

import importlib
import sys
import types
from collections.abc import Iterator
from typing import cast
from unittest import mock

import pytest

import infrahub_sync.adapters as adapters_package

requires_service_profile = pytest.mark.skipif(
    sys.version_info < (3, 11), reason="the service profile requires Python 3.11 or newer"
)


@pytest.fixture
def sdk_and_adapter() -> Iterator[tuple[types.ModuleType, types.ModuleType]]:
    """Restore only modules added by this real SDK import."""
    full_name = "infrahub_sync.adapters.slurpitsync"
    prior_modules = frozenset(sys.modules)
    had_attr = hasattr(adapters_package, "slurpitsync")
    prior_attr = getattr(adapters_package, "slurpitsync", None)
    cleanup = pytest.MonkeyPatch()
    try:
        sdk = importlib.import_module("slurpit")
        adapter = importlib.import_module(full_name)
        yield sdk, adapter
    finally:
        for added_name in set(sys.modules) - prior_modules:
            if added_name in {full_name, "slurpit"} or added_name.startswith("slurpit."):
                cleanup.delitem(sys.modules, added_name, raising=False)
        if had_attr:
            cleanup.setattr(adapters_package, "slurpitsync", prior_attr)
        else:
            cleanup.delattr(adapters_package, "slurpitsync", raising=False)


def _device() -> object:
    device_module = importlib.import_module("slurpit.models.device")
    return device_module.Device(
        id=1, hostname="edge1", fqdn="edge1.example", device_os="ios", disabled=0, brand="Cisco", device_type="router"
    )


def _planning(name: str) -> object:
    planning_module = importlib.import_module("slurpit.models.planning")
    return planning_module.Planning(
        id=42, name=name, comment=0, disabled="0", columns=[], createddate="", changeddate=""
    )


def _adapter(sdk: types.ModuleType, module: types.ModuleType) -> types.SimpleNamespace:
    instance = cast("types.SimpleNamespace", module.SlurpitsyncAdapter.__new__(module.SlurpitsyncAdapter))
    instance.client = sdk.api(url="https://slurpit.example", api_key="test-api-key")
    instance.skipped = []
    instance.filtered_networks = [{"normalized_prefix": "10.0.0.0/24", "Vrf": "default"}]
    return instance


@requires_service_profile
@pytest.mark.parametrize("mapping", ["unique_vendors", "unique_device_type", "device.get_devices"])
def test_device_mappings_load_synchronous_sdk_models(sdk_and_adapter, mapping: str) -> None:
    sdk, module = sdk_and_adapter
    device_api = importlib.import_module("slurpit.apis.deviceapi").DeviceAPI
    instance = _adapter(sdk, module)
    loaded: list[dict[str, object]] = []
    instance.add = loaded.append
    instance.config = types.SimpleNamespace(
        schema_mapping=[types.SimpleNamespace(name="Thing", mapping=mapping)],
        source=types.SimpleNamespace(name="other"),
    )
    instance.slurpit_obj_to_diffsync = lambda obj, mapping, model: obj  # noqa: ARG005
    with mock.patch.object(device_api, "get_devices", autospec=True, return_value=[_device()]) as get_devices:
        module.SlurpitsyncAdapter.model_loader(instance, "Thing", dict)
    get_devices.assert_called_once_with(instance.client.device)
    assert len(loaded) == 1
    if mapping == "unique_vendors":
        assert loaded[0]["brand"] == "Cisco"
    elif mapping == "unique_device_type":
        assert loaded[0] == {"brand": "Cisco", "device_type": "router", "device_os": "ios"}
    else:
        assert loaded[0]["hostname"] == "edge1"


@requires_service_profile
@pytest.mark.parametrize(
    "mapping",
    [
        "planning_results.hardware-info",
        "planning_results.software-versions",
        "planning_results.vlans",
        "planning_results.routing-table",
        "planning_results.interfaces",
        "filter_networks",
        "filter_interfaces",
    ],
)
def test_planning_mappings_load_synchronous_sdk_models(sdk_and_adapter, mapping: str) -> None:
    sdk, module = sdk_and_adapter
    planning_api = importlib.import_module("slurpit.apis.planningapi").PlanningAPI
    instance = _adapter(sdk, module)
    loaded: list[dict[str, object]] = []
    instance.add = loaded.append
    instance.config = types.SimpleNamespace(
        schema_mapping=[types.SimpleNamespace(name="Thing", mapping=mapping)],
        source=types.SimpleNamespace(name="other"),
    )
    instance.slurpit_obj_to_diffsync = lambda obj, mapping, model: obj  # noqa: ARG005
    planning_name = mapping.rsplit(".", maxsplit=1)[-1]
    if mapping == "filter_networks":
        planning_name = "routing-table"
        rows = [{"id": 1, "Network": "10.0.0.0", "Mask": "24"}]
    elif mapping == "filter_interfaces":
        planning_name = "interfaces"
        rows = [{"id": 1, "IP": "10.0.0.1", "Vrf": "default"}]
    else:
        rows = [{"id": 1, "Name": "example"}]
    with (
        mock.patch.object(planning_api, "get_plannings", autospec=True, return_value=[_planning(planning_name)]),
        mock.patch.object(planning_api, "search_plannings", autospec=True, return_value=rows) as search,
    ):
        module.SlurpitsyncAdapter.model_loader(instance, "Thing", dict)
    search.assert_called_once_with(instance.client.planning, {"planning_id": 42, "unique_results": True})
    assert len(loaded) == 1
    if mapping == "filter_networks":
        assert loaded[0]["normalized_prefix"] == "10.0.0.0/24"
    elif mapping == "filter_interfaces":
        assert loaded[0]["normalized_address"] == "10.0.0.1/32"
    else:
        assert loaded[0]["Name"] == "example"


@requires_service_profile
def test_missing_planning_name_raises_index_error(sdk_and_adapter) -> None:
    sdk, module = sdk_and_adapter
    planning_api = importlib.import_module("slurpit.apis.planningapi").PlanningAPI
    instance = _adapter(sdk, module)
    with (
        mock.patch.object(planning_api, "get_plannings", autospec=True, return_value=[_planning("other")]),
        pytest.raises(IndexError, match="No planning found"),
    ):
        instance.planning_results("hardware-info")
