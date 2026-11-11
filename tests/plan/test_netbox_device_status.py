"""The device status translation of the NetBox example (spec 019 User Story 6, T071, research R18).

NetBox has seven device statuses (`DeviceStatusChoices` in `netbox/dcim/choices.py`, NetBox
v4.7). The `DcimDevice.status` Dropdown of the pinned schema library has five choices. The two
`DcimDevice` entries of `examples/netbox_to_infrahub/package.yml` (racked and unracked devices)
translate each NetBox status to one of those choices.

The choices are read from the snapshot of the schema library that the `from-netbox` check
loads (`tests/data/nightly_schema/`), not written here, so a change of the pinned schema fails
these tests.

A status the translation does not know is kept as it is. The plan then lists the device as a
`dropdown_value_not_in_choices` problem and leaves it out; the plan does not fail. The second
test proves that through a real plan of the shared problem fixture, with the example's status
field and transforms added to the fixture's `DcimDevice` mapping.
"""

from __future__ import annotations

import copy
import dataclasses
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
import yaml

from infrahub_sync import DiffSyncModelMixin, SyncConfig
from infrahub_sync.adapters.utils import get_value
from tests.plan.conftest import Variant
from tests.plan.problem_plan import operations_of, plan_fixture

if TYPE_CHECKING:
    from infrahub_sync import SchemaMappingModel
    from tests.plan.conftest import PlanProblemFixture

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_PATH = REPO_ROOT / "examples" / "netbox_to_infrahub" / "package.yml"
PINNED_DCIM_SCHEMA = REPO_ROOT / "tests" / "data" / "nightly_schema" / "traditional-infrastructure-sot" / "dcim.yml"

# NetBox `DeviceStatusChoices` (netbox/dcim/choices.py, NetBox v4.7).
NETBOX_DEVICE_STATUSES = ("offline", "active", "planned", "staged", "failed", "inventory", "decommissioning")

UNKNOWN_STATUS = "not-a-netbox-status"


def _pinned_device_status_choices() -> list[str]:
    """The choices of `DcimDevice.status` in the pinned schema library snapshot."""
    schema = yaml.safe_load(PINNED_DCIM_SCHEMA.read_text(encoding="utf-8"))
    (device,) = [node for node in schema["nodes"] if node["namespace"] == "Dcim" and node["name"] == "Device"]
    (status,) = [attribute for attribute in device["attributes"] if attribute["name"] == "status"]
    assert status["kind"] == "Dropdown"
    return [choice["name"] for choice in status["choices"]]


def _device_entries() -> list[SchemaMappingModel]:
    """The two `DcimDevice` entries of the shipped package, racked first."""
    configuration = yaml.safe_load(PACKAGE_PATH.read_text(encoding="utf-8"))["configuration"]
    entries = [entry for entry in SyncConfig(**configuration).schema_mapping if entry.name == "DcimDevice"]
    assert [entry.filters[1].operation for entry in entries if entry.filters] == ["is_not_empty", "is_empty"]
    return entries


def _status_value(entry: SchemaMappingModel, netbox_status: str) -> Any:  # noqa: ANN401 - the mapped value
    """The value the entry maps to `status` for a NetBox device with `netbox_status`."""
    (status_field,) = [mapped for mapped in entry.fields if mapped.name == "status"]
    assert status_field.mapping is not None
    item = {"name": "rtr1", "status": {"value": netbox_status, "label": netbox_status.title()}}
    DiffSyncModelMixin.apply_transforms(item=item, transforms=entry.transforms or [])
    return get_value(item, status_field.mapping)


ENTRY_IDS = ("racked", "unracked")


@pytest.mark.parametrize("entry_index", [0, 1], ids=ENTRY_IDS)
@pytest.mark.parametrize("netbox_status", NETBOX_DEVICE_STATUSES)
def test_each_netbox_device_status_maps_to_a_pinned_choice(entry_index: int, netbox_status: str) -> None:
    """Every NetBox device status becomes one of the pinned schema's `DcimDevice.status` choices."""
    value = _status_value(_device_entries()[entry_index], netbox_status)

    assert value in _pinned_device_status_choices()


@pytest.mark.parametrize("entry_index", [0, 1], ids=ENTRY_IDS)
def test_an_unknown_status_is_kept_as_it_is(entry_index: int) -> None:
    """The translation does not fail on a status it does not know; it passes the value through."""
    assert _status_value(_device_entries()[entry_index], UNKNOWN_STATUS) == UNKNOWN_STATUS


def _fixture_with_example_status(fixture: PlanProblemFixture, entry: SchemaMappingModel) -> PlanProblemFixture:
    """The shared fixture whose `DcimDevice` has the pinned status Dropdown and the example's status mapping."""
    schema_record = copy.deepcopy(fixture.schema_record)
    schema_record["DcimDevice"]["attributes"]["status"] = {
        "kind": "Dropdown",
        "optional": False,
        "default_value": None,
        "unique": False,
        "read_only": False,
        "choices": _pinned_device_status_choices(),
    }
    config_data = copy.deepcopy(fixture.config_data)
    (device,) = [mapped for mapped in config_data["schema_mapping"] if mapped["name"] == "DcimDevice"]
    (status_field,) = [mapped for mapped in entry.fields if mapped.name == "status"]
    device["fields"].append(status_field.model_dump(exclude_none=True))
    device["transforms"] = [transform.model_dump(exclude_none=True) for transform in entry.transforms or []]
    return dataclasses.replace(fixture, schema_record=schema_record, config_data=config_data)


@pytest.mark.parametrize("entry_index", [0, 1], ids=ENTRY_IDS)
def test_an_unknown_status_becomes_a_plan_problem(
    plan_problems: PlanProblemFixture,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    entry_index: int,
) -> None:
    """A device with a translated status is planned; one with an unknown status is listed, not a crash."""
    monkeypatch.setenv("INFRAHUB_SYNC_CACHE_DIR", str(tmp_path / "cache"))
    fixture = _fixture_with_example_status(plan_problems, _device_entries()[entry_index])
    records = fixture.source_records(Variant.NO_PROBLEMS)
    statuses = {"leaf-1": "planned", "leaf-2": UNKNOWN_STATUS}
    for device in records["DcimDevice"]:
        device["status"] = {"value": statuses[device["name"]], "label": statuses[device["name"]].title()}

    planned = plan_fixture(fixture, records=records)

    (problem,) = planned.problems
    assert problem["problem_type"] == "dropdown_value_not_in_choices"
    assert (problem["record"]["kind"], problem["record"]["source_id"]) == ("DcimDevice", "32")
    assert problem["field"] == "status"
    assert problem["value"] == UNKNOWN_STATUS
    assert sorted(problem["allowed"]) == sorted(_pinned_device_status_choices())
    devices = {operation.identity["name"]: operation for operation in operations_of(planned.operations, "DcimDevice")}
    assert set(devices) == {"leaf-1"}
    assert (devices["leaf-1"].payload or {}).get("status") == "provisioning"
