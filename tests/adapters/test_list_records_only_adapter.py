"""An adapter that implements only `list_records` and `record_to_data` (spec 009 T011).

The problem fixture's adapter (`tests/plan/problem_fixtures.py`) has no loop of its own. It
loads the full fixture through the base class, and its load report equals the load report of
the NetBox adapter on the same records (quickstart scenario 5b).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest

from infrahub_sync.runtime_schema.worker import bind_runtime_models
from tests.plan.problem_fixtures import (
    MISSING_DEVICE_SOURCE_ID,
    MISSING_VRF_SOURCE_ID,
    ORDER,
    PROBLEM_DEVICE_DEPENDENTS,
    PROBLEM_DEVICE_SOURCE_ID,
    UNMAPPED_VRF_SOURCE_ID,
    ProblemFixtureAdapter,
    Variant,
    build_config,
    build_source_adapter,
    source_records,
)

if TYPE_CHECKING:
    from infrahub_sync import LoadReportEntry

# (kind, source ID, reason, field, peer source ID, loaded) of each load report entry.
EXPECTED_LOAD_REPORT = [
    ("LocationSite", "5", "duplicate_source_identity", None, None, False),
    ("IpamVRF", UNMAPPED_VRF_SOURCE_ID, "transform_failed", "name", None, False),
    ("DcimDevice", PROBLEM_DEVICE_SOURCE_ID, "wrong_value_type", "position", None, False),
    ("InterfacePhysical", "60", "required_peer_excluded_by_filter", "device", "35", False),
    ("InterfacePhysical", "61", "peer_not_found_in_source", "device", MISSING_DEVICE_SOURCE_ID, False),
    ("IpamIPAddress", "82", "peer_not_found_in_source", "vrf", MISSING_VRF_SOURCE_ID, True),
]


def _summary(report: list[LoadReportEntry]) -> list[tuple[Any, ...]]:
    return [
        (
            entry.kind,
            entry.source_id,
            entry.reason,
            entry.field,
            entry.peer.source_id if entry.peer else None,
            entry.loaded,
        )
        for entry in report
    ]


def test_the_fixture_adapter_has_no_loop_of_its_own() -> None:
    """The adapter implements only the two adapter methods; the loop is the base class's."""
    own = set(vars(ProblemFixtureAdapter))
    assert {"list_records", "record_to_data"} <= own
    assert own.isdisjoint({"model_loader", "load_record", "load", "peer_unique_id"})


def test_list_records_only_adapter_loads_through_the_shared_step() -> None:
    """The full fixture loads; records with problems are in the load report, not in the store."""
    adapter = build_source_adapter(Variant.ALL)

    adapter.load()

    assert _summary(adapter.load_report) == EXPECTED_LOAD_REPORT
    by_id = {entry.source_id: entry for entry in adapter.load_report}
    assert by_id[PROBLEM_DEVICE_SOURCE_ID].identity == {"name": "core-1", "serial": "C1"}
    assert by_id[PROBLEM_DEVICE_SOURCE_ID].error_type == "int_from_float"
    assert by_id["5"].existing is not None
    assert by_id["5"].existing.source_id == "4"
    assert by_id["82"].identity == {"address": "10.0.0.9/32"}
    # The dependents of the problem device load with the real reference (research R4).
    interfaces = {item.local_id: item for item in adapter.get_all("InterfacePhysical")}  # ty: ignore[unresolved-attribute]
    dependents = [str(40 + index) for index in range(PROBLEM_DEVICE_DEPENDENTS)]
    assert all(interfaces[source_id].device == "core-1__C1" for source_id in dependents)  # ty: ignore[unresolved-attribute]
    assert {"60", "61"}.isdisjoint(interfaces)
    assert [item.local_id for item in adapter.get_all("DcimDevice")] == ["31", "32", "33", "34"]  # ty: ignore[unresolved-attribute]


def _netbox_adapter(monkeypatch: pytest.MonkeyPatch) -> Any:  # noqa: ANN401
    pytest.importorskip("pynetbox")
    from infrahub_sync.adapters.netbox import NetboxAdapter, NetboxModel

    monkeypatch.setattr(NetboxAdapter, "_create_netbox_client", lambda _self, _adapter: MagicMock())
    config = build_config()
    adapter = NetboxAdapter(target="source", adapter=config.source, config=config)
    adapter.top_level = list(ORDER)  # ty: ignore[invalid-attribute-access]
    bind_runtime_models(adapter, _netbox_models(config, NetboxModel))
    for element in config.schema_mapping:
        endpoint = adapter._resolve_endpoint(element.mapping or "")
        endpoint.all.return_value = [dict(record) for record in source_records(Variant.ALL)[element.name]]
    return adapter


def _netbox_models(config: Any, model_base: Any) -> dict[str, Any]:  # noqa: ANN401
    from infrahub_sync.runtime_schema.domain import normalize_destination_schema
    from infrahub_sync.runtime_schema.models import build_runtime_models
    from tests.plan.problem_fixtures import schema_record

    return build_runtime_models(
        snapshot=normalize_destination_schema(schema_record()), configuration=config, model_base=model_base
    )


def test_list_records_only_adapter_reports_like_the_netbox_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    """On the same records, the fixture adapter and the NetBox adapter produce the same load report.

    Passes once the NetBox adapter loads through `list_records` and `record_to_data` (T016).
    """
    fixture = build_source_adapter(Variant.ALL)
    fixture.load()
    netbox = _netbox_adapter(monkeypatch)

    netbox.load()

    assert netbox.load_report == fixture.load_report
