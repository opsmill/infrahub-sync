"""Cisco SD-WAN adapter loading: shared mapping behavior, record preparation and the example project."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import Mock

import pytest
import yaml

from infrahub_sync import SyncConfig
from infrahub_sync.adapters.ciscosdwan import LOCAL_ID_KEY, CiscosdwanAdapter, CiscosdwanModel
from infrahub_sync.adapters.ciscosdwan_client import CiscosdwanClient, CiscosdwanError

if TYPE_CHECKING:
    from types import ModuleType

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "ciscosdwan"
EXAMPLE = ROOT / "examples" / "cisco_sdwan_to_infrahub"


class Device(CiscosdwanModel):
    _modelname = "Device"
    _identifiers = ("name",)
    _attributes = ("label", "site", "enabled")
    name: str
    label: str | None = None
    site: str | None = None
    enabled: bool | None = None
    local_id: str | None = None


class Site(CiscosdwanModel):
    _modelname = "Site"
    _identifiers = ("site_id",)
    _attributes = ("label",)
    site_id: str
    label: str | None = None
    local_id: str | None = None


MODELS: dict[str, type[CiscosdwanModel]] = {"Device": Device, "Site": Site}


@pytest.fixture(autouse=True)
def _credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CISCO_SDWAN_URL", raising=False)
    monkeypatch.setenv("CISCO_SDWAN_USERNAME", "invented-user")
    monkeypatch.setenv("CISCO_SDWAN_PASSWORD", "invented-login-value")


def make_adapter(mappings: list[dict[str, Any]], records: dict[str, list[dict[str, Any]]]) -> CiscosdwanAdapter:
    """Build an adapter class the way `generate` does, with a mock client serving `records`."""
    config = SyncConfig.model_validate(
        {
            "name": "sdwan-test",
            "source": {"name": "ciscosdwan", "settings": {"url": "https://manager.example.com"}},
            "destination": {"name": "infrahub"},
            "schema_mapping": mappings,
        }
    )
    order = [mapping["name"] for mapping in mappings if mapping["name"] in MODELS]
    sync_class = type("TestSync", (CiscosdwanAdapter,), {"top_level": order, **MODELS})
    adapter = sync_class("source", config.source, config)
    fake = Mock(spec=CiscosdwanClient)
    fake.records.side_effect = lambda endpoint: [dict(record) for record in records[endpoint]]
    adapter.client = fake
    return adapter


def mock_client(adapter: CiscosdwanAdapter) -> Mock:
    return cast("Mock", adapter.client)


def loaded(adapter: CiscosdwanAdapter, model: type[CiscosdwanModel]) -> list[Any]:
    return list(adapter.get_all(model))


def device_mapping(**extra: object) -> dict[str, Any]:
    return {
        "name": "Device",
        "mapping": "device",
        "fields": [{"name": "name", "mapping": "host-name"}, {"name": "label", "mapping": "label"}],
        **extra,
    }


def test_destination_target_is_rejected() -> None:
    config = SyncConfig.model_validate(
        {"name": "x", "source": {"name": "infrahub"}, "destination": {"name": "ciscosdwan"}}
    )
    with pytest.raises(ValueError, match="only be used as a source"):
        CiscosdwanAdapter("destination", config.destination, config)


def test_hyphenated_keys_work_in_mappings_and_transforms() -> None:
    adapter = make_adapter(
        [
            device_mapping(
                transforms=[{"field": "label", "expression": "{{ host_name | upper }}-{{ system_ip }}"}],
                filters=[{"field": "device-type", "operation": "==", "value": "vedge"}],
            )
        ],
        {
            "device": [
                {"uuid": "u1", "host-name": "Edge1", "system-ip": "10.0.0.1", "device-type": "vedge"},
                {"uuid": "u2", "host-name": "Manager01", "system-ip": "100.0.0.1", "device-type": "vmanage"},
            ]
        },
    )
    adapter.model_loader("Device", Device)
    assert [(item.name, item.label) for item in loaded(adapter, Device)] == [("Edge1", "EDGE1-10.0.0.1")]


@pytest.mark.parametrize(
    ("record", "local_id"),
    [
        ({"id": "native", "uuid": "u1", "host-name": "a"}, "native"),
        ({"uuid": "u1", "deviceId": "10.0.0.1", "host-name": "a"}, "u1"),
        ({"deviceId": "10.0.0.1", "host-name": "a"}, "10.0.0.1"),
        ({"recordId": "0:Node:1:2", "host-name": "a"}, "0:Node:1:2"),
    ],
)
def test_local_id_prefers_native_identifiers(record: dict[str, Any], local_id: str) -> None:
    adapter = make_adapter([device_mapping()], {"device": [record]})
    adapter.model_loader("Device", Device)
    assert loaded(adapter, Device)[0].local_id == local_id


def test_records_without_identifiers_receive_a_stable_source_id() -> None:
    first = CiscosdwanAdapter._prepare({"version": "20.18.2.1"}, "client/about", 0)
    again = CiscosdwanAdapter._prepare({"version": "20.18.2.1"}, "client/about", 0)
    assert first[LOCAL_ID_KEY] == again[LOCAL_ID_KEY]
    assert len(first[LOCAL_ID_KEY]) == 64
    assert next(iter(first)) == LOCAL_ID_KEY


def test_identical_duplicates_load_once_and_conflicts_stop() -> None:
    record = {"uuid": "u1", "host-name": "Edge1", "label": "same"}
    adapter = make_adapter([device_mapping()], {"device": [record, {**record, "uuid": "u2"}]})
    adapter.model_loader("Device", Device)
    assert len(loaded(adapter, Device)) == 1

    conflicting = make_adapter([device_mapping()], {"device": [record, {**record, "label": "other"}]})
    with pytest.raises(ValueError, match="share the identifier 'Edge1' with different values"):
        conflicting.model_loader("Device", Device)


def test_missing_identifier_value_is_reported() -> None:
    adapter = make_adapter([device_mapping()], {"device": [{"uuid": "u1", "host-name": ""}]})
    with pytest.raises(ValueError, match="no value for identifier name"):
        adapter.model_loader("Device", Device)


def test_falsy_static_values_are_kept() -> None:
    mapping = device_mapping()
    mapping["fields"].append({"name": "enabled", "static": False})
    adapter = make_adapter([mapping], {"device": [{"uuid": "u1", "host-name": "Edge1"}]})
    adapter.model_loader("Device", Device)
    assert loaded(adapter, Device)[0].enabled is False


def test_shared_endpoint_is_fetched_once_and_transforms_stay_per_mapping() -> None:
    site = {
        "name": "Site",
        "mapping": "device",
        "identifiers": ["site_id"],
        "transforms": [{"field": "label", "expression": "SITE_{{ site_id }}"}],
        "fields": [{"name": "site_id", "mapping": "site-id"}, {"name": "label", "mapping": "label"}],
    }
    records = {"device": [{"uuid": "u1", "host-name": "Edge1", "site-id": "1", "label": "original"}]}
    adapter = make_adapter([site, device_mapping()], records)
    adapter.model_loader("Site", Site)
    adapter.model_loader("Device", Device)
    assert mock_client(adapter).records.call_count == 1
    assert loaded(adapter, Site)[0].label == "SITE_1"
    assert loaded(adapter, Device)[0].label == "original"


def test_session_closes_after_the_last_mapping() -> None:
    records = {"device": [{"uuid": "u1", "host-name": "Edge1", "site-id": "1"}]}
    site = {"name": "Site", "mapping": "device", "fields": [{"name": "site_id", "mapping": "site-id"}]}
    adapter = make_adapter([site, device_mapping()], records)
    adapter.model_loader("Site", Site)
    mock_client(adapter).close.assert_not_called()
    adapter.model_loader("Device", Device)
    mock_client(adapter).close.assert_called_once()


def test_session_closes_when_a_fetch_fails() -> None:
    adapter = make_adapter([device_mapping(), {"name": "Site", "mapping": "site", "fields": []}], {})
    mock_client(adapter).records.side_effect = CiscosdwanError("Cisco SD-WAN: device: failed")
    with pytest.raises(CiscosdwanError):
        adapter.model_loader("Device", Device)
    mock_client(adapter).close.assert_called_once()


@pytest.mark.parametrize(
    ("mapping", "message"),
    [({"name": "Other", "mapping": "device"}, "no schema mapping"), (device_mapping(mapping=None), "must name a GET")],
)
def test_mapping_errors(mapping: dict[str, Any], message: str) -> None:
    adapter = make_adapter([mapping], {})
    with pytest.raises(ValueError, match=message):
        adapter.model_loader("Device", Device)


def load_example_models(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "ciscosdwan_example_models", EXAMPLE / "ciscosdwan" / "sync_models.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Pydantic resolves the generated postponed annotations through the registered module.
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module


def fixture_records(name: str) -> list[dict[str, Any]]:
    payload = json.loads((FIXTURES / name).read_text())
    records = payload["data"] if isinstance(payload, dict) and "data" in payload else payload
    return records if isinstance(records, list) else [records]


def test_example_project_maps_manager_inventory(monkeypatch: pytest.MonkeyPatch) -> None:
    config = SyncConfig.model_validate(yaml.safe_load((EXAMPLE / "config.yml").read_text()))
    models = load_example_models(monkeypatch)
    order = config.compute_order()
    sync_class = type(
        "ExampleSync", (CiscosdwanAdapter,), {"top_level": order, **{name: getattr(models, name) for name in order}}
    )
    adapter = sync_class("source", config.source, config)
    payloads = {
        "client/about": fixture_records("about.json"),
        "v1/network-hierarchy": fixture_records("network-hierarchy.json"),
        "v1/config-group": fixture_records("config-group.json"),
        "system/device/vedges": fixture_records("vedges.json"),
    }
    fake = Mock(spec=CiscosdwanClient)
    fake.records.side_effect = lambda endpoint: payloads[endpoint]
    adapter.client = fake
    adapter.load()

    assert order == ["SdwanInstance", "SdwanConfigurationGroup", "SdwanSite", "SdwanRouter"]
    fake.close.assert_called_once()
    attrs = {name: {obj.get_unique_id(): obj.get_attrs() for obj in adapter.get_all(name)} for name in order}
    assert attrs["SdwanInstance"] == {"sdwan-manager": {"manager_version": "20.18.2.1"}}
    assert attrs["SdwanSite"] == {
        "1": {"instance": "sdwan-manager"},
        "2": {"instance": "sdwan-manager"},
        "100": {"instance": "sdwan-manager"},
    }
    assert attrs["SdwanConfigurationGroup"] == {
        "edge_basic": {
            "instance": "sdwan-manager",
            "description": "Do not modify!",
            "system_profile": "edge_basic_system",
            "transport_profile": "edge_basic_wan",
            "service_profile": "edge_basic_lan",
            "cli_profile": "edge_basic_cli",
            "policy_object_profile": "Default_Policy_Object_Profile",
        }
    }
    assert attrs["SdwanRouter"] == {
        "C8K-3D1A8960-6E76-532C-DA93-50626FC5797E": {
            "site": "1",
            "model": "C8000V",
            "configuration_group": "edge_basic",
        },
        "C8K-D4CE7174-5261-7E6F-91EA-4926BCF4C2DD": {"site": "2", "model": "C8000V", "configuration_group": None},
    }
