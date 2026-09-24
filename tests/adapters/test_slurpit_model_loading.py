"""Exercise documented mappings against the current async Slurp'it API shape."""

# These stubs keep the SDK's positional signatures, including its boolean flags
# and `format` parameter, so autospecced calls catch adapter API mismatches.
# ruff: noqa: PLR0913, PLR0917, FBT001, FBT002, A002

from __future__ import annotations

import asyncio
import importlib
import re
import sys
import types
from collections import UserDict
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast
from unittest import mock

import httpx
import pytest
from packaging.requirements import Requirement
from packaging.version import Version

import infrahub_sync.adapters as adapters_package
from infrahub_sync import SyncAdapter
from infrahub_sync.potenda import Potenda


class Device:
    def __init__(self) -> None:
        self.brand = "Cisco"
        self.device_type = "router"
        self.device_os = "ios"

    def to_dict(self) -> dict[str, object]:
        return {"id": 1, "hostname": "edge1", "brand": self.brand}


class Planning:
    def __init__(self, slug: str) -> None:
        self.slug = slug

    def to_dict(self) -> dict[str, object]:
        return {"id": 42, "slug": self.slug}


class Site:
    def __init__(self) -> None:
        self.sitename = "HQ"

    def to_dict(self) -> dict[str, object]:
        return {"id": 7, "sitename": self.sitename}


class DeviceAPI:
    async def get_devices(
        self,
        offset: int = 0,
        limit: int = 1000,
        export_csv: bool = False,
        export_df: bool = False,
        format: str = "json",
        include_raw_json: bool = False,
    ) -> list[Device]:
        _ = (self, offset, limit, export_csv, export_df, format, include_raw_json)
        return []


class PlanningAPI:
    async def get_plannings(
        self,
        offset: int = 0,
        limit: int = 1000,
        export_csv: bool = False,
        export_df: bool = False,
        format: str = "json",
    ) -> list[Planning]:
        _ = (self, offset, limit, export_csv, export_df, format)
        return []

    async def search_plannings(
        self,
        search_data: dict[str, object],
        offset: int = 0,
        limit: int = 1000,
        export_csv: bool = False,
        export_df: bool = False,
        format: str = "json",
    ) -> list[dict[str, object]]:
        _ = (self, search_data, offset, limit, export_csv, export_df, format)
        return []


class SiteAPI:
    async def get_sites(
        self,
        offset: int = 0,
        limit: int = 1000,
        export_csv: bool = False,
        export_df: bool = False,
        format: str = "json",
    ) -> list[Site]:
        _ = (self, offset, limit, export_csv, export_df, format)
        return []


class Client:
    def __init__(self, url: str, api_key: str | None = None, verify: bool = True) -> None:
        self.url = url
        self.api_key = api_key
        self.verify = verify
        self.device = DeviceAPI()
        self.planning = PlanningAPI()
        self.site = SiteAPI()


class MappedRecord(UserDict[str, object]):
    @staticmethod
    def filter_records(records: list[dict[str, object]], schema_mapping: object) -> list[dict[str, object]]:
        _ = schema_mapping
        return records

    @staticmethod
    def transform_records(records: list[dict[str, object]], schema_mapping: object) -> list[dict[str, object]]:
        _ = schema_mapping
        return records


@pytest.fixture
def adapter_module(monkeypatch: pytest.MonkeyPatch) -> Iterator[types.ModuleType]:
    """Load the adapter with an async SDK stub and restore import state afterward."""
    full_name = "infrahub_sync.adapters.slurpitsync"
    prior_modules = frozenset(sys.modules)
    had_attr = hasattr(adapters_package, "slurpitsync")
    prior_attr = getattr(adapters_package, "slurpitsync", None)
    sdk = cast("Any", types.ModuleType("slurpit"))
    sdk.api = Client
    monkeypatch.setitem(sys.modules, "slurpit", sdk)
    monkeypatch.delitem(sys.modules, full_name, raising=False)
    module = importlib.import_module(full_name)
    loops: list[asyncio.AbstractEventLoop] = []
    monkeypatch.setattr(module, "_test_loops", loops, raising=False)
    try:
        yield module
    finally:
        for loop in loops:
            loop.close()
        cleanup = pytest.MonkeyPatch()
        for added_name in set(sys.modules) - prior_modules:
            if added_name == full_name or added_name.startswith("slurpit."):
                cleanup.delitem(sys.modules, added_name, raising=False)
        if had_attr:
            cleanup.setattr(adapters_package, "slurpitsync", prior_attr)
        else:
            cleanup.delattr(adapters_package, "slurpitsync", raising=False)


def _adapter(module: types.ModuleType, mapping: str, target: str = "source") -> tuple[Any, list[dict[str, object]]]:
    instance = module.SlurpitsyncAdapter.__new__(module.SlurpitsyncAdapter)
    instance._loop = asyncio.new_event_loop()
    module._test_loops.append(instance._loop)
    instance.name = "Slurpit"
    instance.target = target
    instance.client = Client("https://slurpit.example", "test-api-key")
    instance.skipped = []
    instance.filtered_networks = [{"normalized_prefix": "10.0.0.0/24", "Vrf": "default"}]
    loaded: list[dict[str, object]] = []
    instance.add = loaded.append
    instance.config = types.SimpleNamespace(
        schema_mapping=[types.SimpleNamespace(name="Thing", mapping=mapping)],
        source=types.SimpleNamespace(name="other"),
    )
    instance.slurpit_obj_to_diffsync = lambda obj, mapping, model: obj  # noqa: ARG005
    return instance, loaded


def test_run_async_reuses_loop_for_shared_httpx_transport_and_closes_it(adapter_module: types.ModuleType) -> None:
    instance, _ = _adapter(adapter_module, "device.get_devices")
    first_loop: asyncio.AbstractEventLoop | None = None
    requests = 0

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal first_loop, requests
        current_loop = asyncio.get_running_loop()
        if first_loop is None:
            first_loop = current_loop
        assert current_loop is first_loop
        requests += 1
        return httpx.Response(200, request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    try:
        for _ in range(2):
            response = instance.run_async(client.get("https://slurpit.example/devices"))
            assert response.status_code == 200
        assert requests == 2
    finally:
        instance.run_async(client.aclose())
        loop = instance._loop
        instance.close()
    assert loop.is_closed()


@pytest.mark.parametrize("load_fails", [False, True])
def test_loading_teardown_closes_adapter_loop(adapter_module: types.ModuleType, load_fails: bool) -> None:
    instance, _ = _adapter(adapter_module, "device.get_devices")
    loader = types.SimpleNamespace(
        source=instance,
        load_one_side=mock.Mock(side_effect=RuntimeError("load failed") if load_fails else None),
        _write_side_snapshot=mock.Mock(),
    )

    if load_fails:
        with pytest.raises(ValueError, match="load failed"):
            Potenda.source_load(cast("Any", loader))
    else:
        Potenda.source_load(cast("Any", loader))

    assert instance._loop.is_closed()


@pytest.mark.parametrize("mapping", ["unique_vendors", "unique_device_type", "device.get_devices"])
def test_device_mappings_load_async_sdk_models(adapter_module: types.ModuleType, mapping: str) -> None:
    instance, loaded = _adapter(adapter_module, mapping)
    with mock.patch.object(DeviceAPI, "get_devices", autospec=True, return_value=[Device()]) as get_devices:
        adapter_module.SlurpitsyncAdapter.model_loader(instance, "Thing", MappedRecord)
    get_devices.assert_awaited_once_with(instance.client.device)
    assert len(loaded) == 1
    if mapping == "unique_vendors":
        assert loaded[0]["brand"] == "Cisco"
    elif mapping == "unique_device_type":
        assert loaded[0] == {"brand": "Cisco", "device_type": "router", "device_os": "ios"}
    else:
        assert loaded[0]["hostname"] == "edge1"


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
def test_planning_mappings_load_async_sdk_models(adapter_module: types.ModuleType, mapping: str) -> None:
    instance, loaded = _adapter(adapter_module, mapping)
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
        mock.patch.object(PlanningAPI, "get_plannings", autospec=True, return_value=[Planning(planning_name)]) as get,
        mock.patch.object(PlanningAPI, "search_plannings", autospec=True, return_value=rows) as search,
    ):
        adapter_module.SlurpitsyncAdapter.model_loader(instance, "Thing", MappedRecord)
    get.assert_awaited_once_with(instance.client.planning)
    search.assert_awaited_once_with(instance.client.planning, {"planning_id": 42, "unique_results": True}, limit=30000)
    assert len(loaded) == 1
    if mapping == "filter_networks":
        assert loaded[0]["normalized_prefix"] == "10.0.0.0/24"
    elif mapping == "filter_interfaces":
        assert loaded[0]["normalized_address"] == "10.0.0.1/32"
    else:
        assert loaded[0]["Name"] == "example"


def test_site_mapping_loads_async_sdk_models(adapter_module: types.ModuleType) -> None:
    instance, loaded = _adapter(adapter_module, "site.get_sites")
    with mock.patch.object(SiteAPI, "get_sites", autospec=True, return_value=[Site()]) as get_sites:
        adapter_module.SlurpitsyncAdapter.model_loader(instance, "Thing", MappedRecord)
    get_sites.assert_awaited_once_with(instance.client.site)
    assert loaded == [{"id": 7, "sitename": "HQ"}]


@pytest.mark.parametrize("target", ["source", "destination"])
def test_model_loading_applies_mapping_rules_only_for_source(adapter_module: types.ModuleType, target: str) -> None:
    instance, loaded = _adapter(adapter_module, "site.get_sites", target=target)
    with (
        mock.patch.object(SiteAPI, "get_sites", autospec=True, return_value=[Site()]),
        mock.patch.object(MappedRecord, "filter_records", wraps=MappedRecord.filter_records) as filter_records,
        mock.patch.object(MappedRecord, "transform_records", wraps=MappedRecord.transform_records) as transform_records,
    ):
        adapter_module.SlurpitsyncAdapter.model_loader(instance, "Thing", MappedRecord)
    assert loaded == [{"id": 7, "sitename": "HQ"}]
    assert filter_records.call_count == (1 if target == "source" else 0)
    assert transform_records.call_count == (1 if target == "source" else 0)


def test_missing_planning_slug_raises_index_error(adapter_module: types.ModuleType) -> None:
    instance, _ = _adapter(adapter_module, "planning_results.hardware-info")
    with (
        mock.patch.object(PlanningAPI, "get_plannings", autospec=True, return_value=[Planning("other")]),
        pytest.raises(IndexError, match="No planning found"),
    ):
        instance.planning_results("hardware-info")


@pytest.mark.parametrize("verify", [True, False])
def test_client_passes_verify_to_async_sdk(adapter_module: types.ModuleType, verify: bool) -> None:
    instance, _ = _adapter(adapter_module, "device.get_devices")
    settings = {"url": "https://slurpit.example", "api_key": "test-api-key", "verify_ssl": verify}
    with mock.patch.object(DeviceAPI, "get_devices", autospec=True, return_value=[]) as get_devices:
        client = instance._create_slurpit_client(SyncAdapter(name="slurpitsync", settings=settings))
    get_devices.assert_awaited_once_with(client.device)
    assert client.verify is verify
    assert settings["verify_ssl"] is verify


def test_declared_sdk_floor_excludes_synchronous_release() -> None:
    pyproject = (Path(__file__).parents[2] / "pyproject.toml").read_text()
    match = re.search(r'"(slurpit-sdk[^"\n]+)"', pyproject)
    assert match is not None
    requirement = Requirement(match.group(1).split(";", maxsplit=1)[0])
    assert Version("0.9.32") not in requirement.specifier
    assert Version("0.9.47") not in requirement.specifier
    assert Version("0.9.52") in requirement.specifier
