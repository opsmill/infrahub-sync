"""Tests for the collision fixes in the shipped NetBox example.

The NetBox demo data reuses VLAN, rack, and device names:

* NetBox only enforces a unique VLAN name within a VLAN group, so the same name (for
  example `Data`) repeats across every group. `examples/netbox_to_infrahub/config.yml`
  renders the Infrahub VLAN name as `"{{ group.name }}-{{ name }}"` so it stays unique.
* NetBox lets several devices share a name across different sites. The demo data does this
  for patch panels (`PP:MDF` at more than one site), so devices and interfaces with `PP:`
  in the device name are filtered out together.
* The demo has a `Comms closet` rack at several sites. The rack name includes the site
  so Infrahub creates one rack per site.

These tests read the mapping and run its filters/transforms directly; they load nothing.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from infrahub_sync import DiffSyncModelMixin, SyncConfig
from infrahub_sync.adapters.utils import get_value

EXAMPLE_DIR = Path(__file__).resolve().parent.parent / "examples" / "netbox_to_infrahub"
CONFIG_PATH = EXAMPLE_DIR / "config.yml"


def _mapping(name: str, *, index: int = 0, config_path: Path = CONFIG_PATH):
    """Return the `index`-th schema_mapping entry named `name` (DcimDevice appears twice)."""
    content = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    config = SyncConfig(**(content.get("configuration", content)))
    matches = [mapping for mapping in config.schema_mapping if mapping.name == name]
    return matches[index]


# ---------------------------------------------------------------------------
# IpamVLAN: the rendered name carries the group, so it's unique the way
# Infrahub's name-only HFID needs it to be.
# ---------------------------------------------------------------------------


def test_netbox_example_vlan_name_carries_the_group() -> None:
    mapping = _mapping("IpamVLAN")
    name_field = next(field for field in mapping.fields if field.name == "name")
    assert name_field.mapping is not None

    record = {"name": "Data", "group": {"id": 1, "name": "Site A"}}
    transformed = DiffSyncModelMixin.transform_records(records=[record], schema_mapping=mapping)[0]

    assert get_value(transformed, name_field.mapping) == "Site A-Data"


def test_netbox_example_vlan_names_that_collide_in_netbox_dont_collide_in_infrahub() -> None:
    """The exact scenario in the demo data: one name, several groups."""
    mapping = _mapping("IpamVLAN")
    name_field = next(field for field in mapping.fields if field.name == "name")
    assert name_field.mapping is not None

    groups = [{"id": 1, "name": "Site A"}, {"id": 2, "name": "Site B"}, {"id": 3, "name": "Site C"}]
    rendered_names = set()
    for group in groups:
        record = {"name": "Data", "group": group}
        transformed = DiffSyncModelMixin.transform_records(records=[record], schema_mapping=mapping)[0]
        rendered_names.add(get_value(transformed, name_field.mapping))

    assert len(rendered_names) == len(groups)


@pytest.mark.parametrize("config_path", [CONFIG_PATH, EXAMPLE_DIR / "package.yml"])
def test_netbox_example_rack_name_carries_the_site(config_path: Path) -> None:
    mapping = _mapping("LocationRack", config_path=config_path)
    name_field = next(field for field in mapping.fields if field.name == "name")
    assert name_field.mapping is not None

    records = [
        {"id": 1, "name": "Comms closet", "site": {"id": 10, "name": "Site A"}},
        {"id": 2, "name": "Comms closet", "site": {"id": 20, "name": "Site B"}},
    ]
    transformed = DiffSyncModelMixin.transform_records(records=records, schema_mapping=mapping)

    assert [get_value(record, name_field.mapping) for record in transformed] == [
        "Site A-Comms closet",
        "Site B-Comms closet",
    ]


# ---------------------------------------------------------------------------
# DcimDevice: both entries use the same name condition as interfaces.
# ---------------------------------------------------------------------------

DEVICE_ENTRY_INDEXES = [0, 1]  # 0: rack-based devices, 1: site-based devices


@pytest.mark.parametrize("config_path", [CONFIG_PATH, EXAMPLE_DIR / "package.yml"])
@pytest.mark.parametrize(
    ("device_name", "expected_count"),
    [
        ("PP:MDF", 0),
        ("PP:IDF", 0),
        ("panel-1", 1),
        ("core-switch-01", 1),
        ("core-PP:01", 0),
    ],
)
def test_netbox_example_device_and_interface_filters_agree(
    config_path: Path, device_name: str, expected_count: int
) -> None:
    """A device excluded by name cannot leave any mapped interface behind."""
    for index in DEVICE_ENTRY_INDEXES:
        device = {
            "name": device_name,
            "parent_device": None,
            "rack": {"id": 1} if index == 0 else None,
        }
        kept = DiffSyncModelMixin.filter_records(
            records=[device], schema_mapping=_mapping("DcimDevice", index=index, config_path=config_path)
        )
        assert len(kept) == expected_count

    for mapping_name, interface_type in INTERFACE_MAPPING_TYPES.items():
        interface = {
            "name": "1",
            "type": {"value": interface_type, "label": ""},
            "device": {"id": 1, "name": device_name},
        }
        kept = DiffSyncModelMixin.filter_records(
            records=[interface], schema_mapping=_mapping(mapping_name, config_path=config_path)
        )
        assert len(kept) == expected_count


# ---------------------------------------------------------------------------
# Interfaces: use the same device-name condition as DcimDevice.
# ---------------------------------------------------------------------------

INTERFACE_MAPPING_TYPES = {
    "InterfacePhysical": "8p8c",
    "InterfaceVirtual": "virtual",
    "InterfaceLag": "lag",
}


@pytest.mark.parametrize("mapping_name", INTERFACE_MAPPING_TYPES)
@pytest.mark.parametrize("device_name", ["PP:MDF", "PP:IDF"])
def test_netbox_example_interface_mapping_excludes_patch_panel_interfaces(mapping_name: str, device_name: str) -> None:
    mapping = _mapping(mapping_name)
    record = {
        "name": "1",
        "type": {"value": INTERFACE_MAPPING_TYPES[mapping_name], "label": ""},
        "device": {"id": 1, "url": "", "display": device_name, "name": device_name, "description": ""},
    }

    filtered = DiffSyncModelMixin.filter_records(records=[record], schema_mapping=mapping)

    assert filtered == []


@pytest.mark.parametrize("mapping_name", INTERFACE_MAPPING_TYPES)
def test_netbox_example_interface_mapping_keeps_interfaces_of_other_devices(mapping_name: str) -> None:
    mapping = _mapping(mapping_name)
    record = {
        "name": "1",
        "type": {"value": INTERFACE_MAPPING_TYPES[mapping_name], "label": ""},
        "device": {"id": 2, "url": "", "display": "core-switch-01", "name": "core-switch-01", "description": ""},
    }

    filtered = DiffSyncModelMixin.filter_records(records=[record], schema_mapping=mapping)

    assert filtered == [record]
