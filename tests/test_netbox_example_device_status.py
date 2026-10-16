"""Tests for the device status translation in the shipped NetBox example.

NetBox device statuses include `planned`, `staged`, `offline`, `failed`, `inventory` and
`decommissioning`, which are not among the five choices of the Infrahub `DcimDevice.status`
Dropdown. Both `DcimDevice` entries of `examples/netbox_to_infrahub/package.yml` translate
them with a `device_status` transform (FR-031, #374). These tests read the mapping and run
its transforms directly; they load nothing.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from infrahub_sync import DiffSyncModelMixin, SyncConfig
from infrahub_sync.adapters.utils import get_value

PACKAGE_PATH = Path(__file__).resolve().parent.parent / "examples" / "netbox_to_infrahub" / "package.yml"

DESTINATION_CHOICES = {"active", "reserved", "provisioning", "maintenance", "deprecated"}

EXPECTED_TRANSLATION = {
    "active": "active",
    "planned": "provisioning",
    "staged": "provisioning",
    "offline": "maintenance",
    "failed": "maintenance",
    "inventory": "reserved",
    "decommissioning": "deprecated",
}


def _device_mappings():
    """Return both `DcimDevice` schema_mapping entries of the package."""
    content = yaml.safe_load(PACKAGE_PATH.read_text(encoding="utf-8"))
    config = SyncConfig(**(content.get("configuration", content)))
    mappings = [mapping for mapping in config.schema_mapping if mapping.name == "DcimDevice"]
    assert len(mappings) == 2
    return mappings


@pytest.mark.parametrize("index", [0, 1], ids=["racked", "site-only"])
def test_device_status_maps_through_the_device_status_transform(index: int) -> None:
    """The `status` attribute reads the `device_status` field the transform writes."""
    mapping = _device_mappings()[index]
    status_field = next(field for field in mapping.fields if field.name == "status")
    assert status_field.mapping == "device_status"
    assert any(transform.field == "device_status" for transform in mapping.transforms or [])


@pytest.mark.parametrize("index", [0, 1], ids=["racked", "site-only"])
@pytest.mark.parametrize(("netbox_status", "infrahub_status"), sorted(EXPECTED_TRANSLATION.items()))
def test_device_status_translates_to_a_destination_choice(index: int, netbox_status: str, infrahub_status: str) -> None:
    """Each NetBox status becomes the expected one of the five destination choices."""
    mapping = _device_mappings()[index]
    record = {"name": "dev-01", "status": {"value": netbox_status, "label": netbox_status.title()}}
    transformed = DiffSyncModelMixin.transform_records(records=[record], schema_mapping=mapping)[0]

    result = get_value(transformed, "device_status")
    assert result == infrahub_status
    assert result in DESTINATION_CHOICES
