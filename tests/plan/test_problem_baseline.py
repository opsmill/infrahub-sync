"""Three problem types that spec 019 changes, through the real NetBox read (T001, research R1).

T001 pinned what a source read did before spec 019 with a duplicate source identity, a value of
the wrong type and a Dropdown value outside its choices. T017 rewrote the first two: the read
records the record in the load report and continues. The Dropdown test still pins the behavior
before T023 and T026. Each test drives the NetBox adapter, with a mocked client, over records of
the shared problem fixture (`tests/plan/conftest.py`).
"""

from __future__ import annotations

from collections import UserDict
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest

from infrahub_sync import SyncAdapter
from infrahub_sync.plan.derive import operations_from_diff
from infrahub_sync.runtime_schema.worker import bind_runtime_models

if TYPE_CHECKING:
    from tests.plan.conftest import PlanProblemFixture

pytest.importorskip("pynetbox")

from infrahub_sync.adapters.netbox import NetboxAdapter, NetboxModel


class _Record(UserDict):
    """Make a fixture record behave like a pynetbox record under `dict(record)`."""


def _netbox_adapter(
    monkeypatch: pytest.MonkeyPatch,
    fixture: PlanProblemFixture,
    *,
    target: str,
    records: dict[str, list[dict[str, Any]]],
) -> NetboxAdapter:
    """A NetBox adapter bound to the fixture's runtime models, listing `records` by kind."""
    monkeypatch.setattr(NetboxAdapter, "_create_netbox_client", lambda _self, _adapter: MagicMock())
    config = fixture.config()
    adapter = NetboxAdapter(target=target, adapter=SyncAdapter(name="netbox"), config=config)
    adapter.top_level = fixture.order  # ty: ignore[invalid-attribute-access]
    bind_runtime_models(adapter, fixture.build_models(model_base=NetboxModel, config=config))
    for element in config.schema_mapping:
        assert element.mapping is not None
        endpoint = adapter._resolve_endpoint(element.mapping)
        endpoint.all.return_value = [_Record(record) for record in records.get(element.name, [])]
    return adapter


def _load(adapter: NetboxAdapter, kind: str) -> None:
    adapter.model_loader(kind, getattr(adapter, kind))


def test_duplicate_source_identity_is_recorded_and_the_read_continues(
    monkeypatch: pytest.MonkeyPatch, plan_problems: PlanProblemFixture
) -> None:
    """The second `LocationSite` named `dup` is `duplicate_source_identity`; the store keeps the first.

    FR-002 (`duplicate_source_identity`), implemented by T017. Before spec 019 the read raised
    `ObjectAlreadyExists` and stopped.
    """
    records = {
        "LocationSite": [site for site in plan_problems.source_records()["LocationSite"] if site["name"] == "dup"]
    }
    assert [site["id"] for site in records["LocationSite"]] == [4, 5]
    adapter = _netbox_adapter(monkeypatch, plan_problems, target="source", records=records)

    _load(adapter, "LocationSite")

    assert [site.model_dump()["local_id"] for site in adapter.get_all("LocationSite")] == ["4"]
    (entry,) = adapter.load_report.entries
    assert (entry.reason, entry.record.source_id, entry.existing.source_id if entry.existing else None) == (
        "duplicate_source_identity",
        "5",
        "4",
    )


def test_number_given_a_float_is_recorded_and_the_read_continues(
    monkeypatch: pytest.MonkeyPatch, plan_problems: PlanProblemFixture
) -> None:
    """`position` 2.5 for a `Number` attribute is `wrong_value_type`; the device is not stored.

    FR-002 (`wrong_value_type`), implemented by T017. Before spec 019 the read raised
    `ValidationError` (`int_from_float`) and stopped.
    """
    source = plan_problems.source_records()
    records = {
        "LocationSite": [site for site in source["LocationSite"] if site["name"] == "paris"],
        "DcimDevice": [device for device in source["DcimDevice"] if device["name"] == "core-1"],
    }
    adapter = _netbox_adapter(monkeypatch, plan_problems, target="source", records=records)
    _load(adapter, "LocationSite")

    _load(adapter, "DcimDevice")

    assert [(entry.reason, entry.record.kind, entry.field) for entry in adapter.load_report.entries] == [
        ("wrong_value_type", "DcimDevice", "position")
    ]
    assert adapter.get_all("DcimDevice") == []


def test_dropdown_value_outside_its_choices_is_planned(
    monkeypatch: pytest.MonkeyPatch, plan_problems: PlanProblemFixture
) -> None:
    """A `LocationSite` status outside the Dropdown's choices is read and planned as a create.

    The runtime schema maps a Dropdown to `str` and records no choices, so the plan accepts
    `decommissioning`, and Infrahub refuses the value only during apply (#371).

    FR-002 (`dropdown_value_not_in_choices`): T010 changes this expectation, and T023 and T026
    implement the change (the record becomes a plan problem and is left out).
    """
    assert "decommissioning" not in plan_problems.schema_record["LocationSite"]["attributes"]["status"]["choices"]
    records = {
        "LocationSite": [
            site for site in plan_problems.source_records()["LocationSite"] if site["name"] in {"paris", "berlin"}
        ]
    }
    source = _netbox_adapter(monkeypatch, plan_problems, target="source", records=records)
    _load(source, "LocationSite")
    destination = _netbox_adapter(monkeypatch, plan_problems, target="destination", records={})

    operations = operations_from_diff(
        destination.diff_from(source),
        config=source.config,
        tier_of=lambda _kind: 0,
        source_adapter=source,
        destination_adapter=destination,
    )

    assert sorted(
        (operation.action, operation.kind, (operation.payload or {}).get("status")) for operation in operations
    ) == [
        ("create", "LocationSite", "active"),
        ("create", "LocationSite", "decommissioning"),
    ]
