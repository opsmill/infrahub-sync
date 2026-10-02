"""Benchmark IP updates are visible through the real NetBox source projection."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

from development.netbox.datasets import change_netbox as change
from development.netbox.datasets.tier_data import build_dataset
from infrahub_sync import SyncAdapter, SyncConfig

pytest.importorskip("pynetbox")

from infrahub_sync.adapters.netbox import NetboxAdapter, NetboxModel


class _IPAddress(NetboxModel):
    """The address attributes needed to check the benchmark description update."""

    _modelname = "IpamIPAddress"
    _identifiers = ("address",)
    _attributes = ("description", "status")
    address: str
    description: str
    status: str


class _Interface(NetboxModel):
    """The interface fields needed to check NetBox's missing inverse relationship."""

    _modelname = "InterfacePhysical"
    _identifiers = ("name",)
    _attributes = ("ip_addresses",)
    name: str
    ip_addresses: list[str] = []  # noqa: RUF012 -- NetboxModel.is_list reads this default


def test_ip_updates_project_description_with_netbox_47_interface_response(monkeypatch: pytest.MonkeyPatch) -> None:
    """Use mapped descriptions without relying on an absent interface IP list."""
    config_path = Path(__file__).resolve().parents[2] / "examples/netbox_to_infrahub/config.yml"
    config = SyncConfig(**yaml.safe_load(config_path.read_text(encoding="utf-8")))
    address_mapping = next(entry for entry in config.schema_mapping if entry.name == "IpamIPAddress")
    interface_mapping = next(entry for entry in config.schema_mapping if entry.name == "InterfacePhysical")
    # Use the shipped field mappings, projecting only the attributes under test.
    address_mapping = address_mapping.model_copy(
        update={
            "fields": [field for field in address_mapping.fields if field.name in {"address", "description", "status"}]
        }
    )
    interface_mapping = interface_mapping.model_copy(
        update={"fields": [field for field in interface_mapping.fields if field.name in {"name", "ip_addresses"}]}
    )
    monkeypatch.setattr(NetboxAdapter, "_create_netbox_client", lambda _self, _adapter: MagicMock())
    adapter = NetboxAdapter(target="source", adapter=SyncAdapter(name="netbox"), config=config)

    # NetBox 4.7 returns a scalar count, not the ip_addresses list the mapping asks for.
    # Changing that count after an IP deletion still projects the same empty list.
    interface = {"id": 1, "name": "eth0", "device": {"id": 1, "name": "dev-01"}, "count_ipaddresses": 3}
    before_interface = adapter.netbox_obj_to_diffsync(interface, interface_mapping, _Interface)
    after_interface = adapter.netbox_obj_to_diffsync(
        interface | {"count_ipaddresses": 2}, interface_mapping, _Interface
    )
    assert before_interface == after_interface == {"local_id": "1", "name": "eth0", "ip_addresses": []}

    data = build_dataset("M")
    baselines = {change.identifier("ipam/ip-addresses", row.fields, data): row for row in data["ipam/ip-addresses"]}
    updates = [
        row for row in change.plan_changes("M") if row["action"] == "update" and row["kind"] == "ipam/ip-addresses"
    ]
    assert len(updates) == 17
    for update in updates:
        baseline = baselines[update["identifier"]]
        # IP responses keep assigned_object_id scalar and status nested.
        record = baseline.fields | {"id": baseline.id, "status": {"value": baseline.fields["status"]}}
        assert set(update["fields"]) == {"description"}
        before = adapter.netbox_obj_to_diffsync(record, address_mapping, _IPAddress)
        after = adapter.netbox_obj_to_diffsync(record | update["fields"], address_mapping, _IPAddress)
        assert after == before | {"description": update["fields"]["description"]}
        assert after["description"] != before["description"]
    assert any("assigned_object_id" in baselines[update["identifier"]].fields for update in updates)
