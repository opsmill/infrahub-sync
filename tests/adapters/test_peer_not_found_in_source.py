"""A reference to a peer that the source does not return is a plan problem (spec 019 T014, FR-002, SC-010).

Each bundled adapter that resolves references while it reads (NetBox, Nautobot, ACI, generic REST)
reads recorded API responses: a device refers to a site the source does not return through the
required relationship `site`, and an IP address refers to a VRF the source does not return through
the optional relationship `vrf`. The plan finishes and lists two `peer_not_found_in_source`
problems, leaves out the device, keeps the IP address and writes no `vrf` value for it.

Each adapter runs through its own `record_to_data` and the base-class read loop; only
`list_records`, the call to the provider, is replaced on the instance and returns the recorded
responses. The adapter is built without its `__init__`, which connects to the provider.

Spec 019 T017 to T021 implement the read: `test_the_read_records_each_missing_peer` checks the
load report each adapter writes. The plan problems, left-out and kept entries come with the problem
pass and plan format 4 (T025 to T028); until then the plan test is an expected failure, because the
plan writer writes no `plan/report.jsonl` (`FileNotFoundError`).
"""

from __future__ import annotations

import copy
import importlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import pytest
from diffsync import Adapter

from infrahub_sync import (
    DiffSyncMixin,
    SchemaMappingField,
    SchemaMappingModel,
    SyncAdapter,
    SyncInstance,
    is_not_in_source_placeholder,
)
from infrahub_sync.adapters.infrahub import InfrahubModel
from infrahub_sync.cache.paths import cache_root_for
from infrahub_sync.plan.errors import NotInSourcePeerInPlanError
from infrahub_sync.potenda import Potenda
from infrahub_sync.runtime_schema.domain import normalize_destination_schema
from infrahub_sync.runtime_schema.models import build_runtime_models
from infrahub_sync.runtime_schema.worker import bind_runtime_models
from tests.plan.problem_plan import FixtureDestination, finish_plan, load_both_sides, operations_of, record_key

if TYPE_CHECKING:
    from pathlib import Path

ORDER = ["LocationSite", "IpamVRF", "DcimDevice", "IpamIPAddress"]

RUN_ID = "20261111T120000-01900014"


def _schema() -> dict[str, Any]:
    """The destination schema record of the four kinds: `site` is required, `vrf` is optional."""

    def attribute(kind: str = "Text") -> dict[str, Any]:
        return {"kind": kind, "optional": False, "default_value": None, "unique": False, "read_only": False}

    def relationship(peer: str, *, optional: bool) -> dict[str, Any]:
        return {"peer": peer, "cardinality": "one", "optional": optional, "kind": "Attribute", "read_only": False}

    def kind(attributes: dict[str, Any], relationships: dict[str, Any] | None = None) -> dict[str, Any]:
        return {
            "human_friendly_id": [f"{next(iter(attributes))}__value"],
            "uniqueness_constraints": [],
            "attributes": attributes,
            "relationships": relationships or {},
        }

    return {
        "LocationSite": kind({"name": attribute()}),
        "IpamVRF": kind({"name": attribute()}),
        "DcimDevice": kind({"name": attribute()}, {"site": relationship("LocationSite", optional=False)}),
        "IpamIPAddress": kind({"address": attribute("IPHost")}, {"vrf": relationship("IpamVRF", optional=True)}),
    }


def _config(adapter: str) -> SyncInstance:
    def entry(kind: str, resource: str, fields: dict[str, str | None]) -> SchemaMappingModel:
        return SchemaMappingModel(
            name=kind,
            mapping=resource,
            identifiers=[next(iter(fields))],
            fields=[SchemaMappingField(name=name, mapping=name, reference=ref) for name, ref in fields.items()],
        )

    return SyncInstance(
        name=f"peer-not-found-{adapter}",
        directory=".",
        source=SyncAdapter(name=adapter, settings={"url": "https://source.invalid"}),
        destination=SyncAdapter(name="infrahub", settings={"url": "https://destination.invalid"}),
        order=list(ORDER),
        schema_mapping=[
            entry("LocationSite", "dcim.sites", {"name": None}),
            entry("IpamVRF", "ipam.vrfs", {"name": None}),
            entry("DcimDevice", "dcim.devices", {"name": None, "site": "LocationSite"}),
            entry("IpamIPAddress", "ipam.ip-addresses", {"address": None, "vrf": "IpamVRF"}),
        ],
    )


@dataclass(frozen=True)
class RecordedSource:
    """One adapter by module and class names, and the API responses it recorded, by kind."""

    module: str
    adapter: str
    model_base: str
    requires: str | None
    responses: dict[str, list[dict[str, Any]]]
    missing_site: str
    missing_vrf: str


def _nested(source_id: Any) -> dict[str, Any]:  # noqa: ANN401 - each provider has its own ID type
    """A NetBox, Nautobot or generic REST nested reference."""
    return {"id": source_id, "url": f"https://source.invalid/api/{source_id}/"}


def _nested_responses(site: Any, vrf: Any, missing_site: Any, missing_vrf: Any, ids: list[Any]) -> dict[str, Any]:  # noqa: ANN401
    return {
        "LocationSite": [{"id": site, "name": "paris", "slug": "paris"}],
        "IpamVRF": [{"id": vrf, "name": "default", "rd": None}],
        "DcimDevice": [
            {"id": ids[0], "name": "r1", "site": _nested(missing_site)},
            {"id": ids[1], "name": "r2", "site": _nested(site)},
        ],
        "IpamIPAddress": [
            {"id": ids[2], "address": "10.0.0.9/32", "vrf": _nested(missing_vrf)},
            {"id": ids[3], "address": "10.0.0.1/32", "vrf": _nested(vrf)},
        ],
    }


def _uuid(number: int) -> str:
    return f"aaaaaaaa-0000-0000-0000-{number:012d}"


SOURCES: dict[str, RecordedSource] = {
    "netbox": RecordedSource(
        "infrahub_sync.adapters.netbox",
        "NetboxAdapter",
        "NetboxModel",
        "pynetbox",
        _nested_responses(1, 10, 999, 998, [31, 32, 81, 82]),
        "999",
        "998",
    ),
    "nautobot": RecordedSource(
        "infrahub_sync.adapters.nautobot",
        "NautobotAdapter",
        "NautobotModel",
        "pynautobot",
        _nested_responses(_uuid(1), _uuid(10), _uuid(999), _uuid(998), [_uuid(31), _uuid(32), _uuid(81), _uuid(82)]),
        _uuid(999),
        _uuid(998),
    ),
    "genericrestapi": RecordedSource(
        "infrahub_sync.adapters.genericrestapi",
        "GenericrestapiAdapter",
        "GenericrestapiModel",
        None,
        _nested_responses("site-1", "vrf-10", "site-999", "vrf-998", ["dev-31", "dev-32", "ip-81", "ip-82"]),
        "site-999",
        "vrf-998",
    ),
    # The APIC returns flat attribute objects; a reference is the peer's ID.
    "aci": RecordedSource(
        "infrahub_sync.adapters.aci",
        "AciAdapter",
        "AciModel",
        None,
        {
            "LocationSite": [{"id": "1", "name": "paris"}],
            "IpamVRF": [{"id": "10", "name": "default"}],
            "DcimDevice": [{"id": "31", "name": "r1", "site": "999"}, {"id": "32", "name": "r2", "site": "1"}],
            "IpamIPAddress": [
                {"id": "81", "address": "10.0.0.9/32", "vrf": "998"},
                {"id": "82", "address": "10.0.0.1/32", "vrf": "10"},
            ],
        },
        "999",
        "998",
    ),
}


def _recorded_adapter(name: str, recorded: RecordedSource, config: SyncInstance) -> DiffSyncMixin:
    """The adapter of `recorded`, built without its client, listing the recorded responses."""
    if recorded.requires is not None:
        pytest.importorskip(recorded.requires)
    module = importlib.import_module(recorded.module)
    adapter_class = getattr(module, recorded.adapter)
    # Spec 019 T018 to T020: the adapter keeps only `list_records` and `record_to_data`, and
    # reads through the base-class loop.
    assert "model_loader" not in vars(adapter_class), f"{name}: the adapter still has its own read loop"
    assert "record_to_data" in vars(adapter_class), f"{name}: the adapter has no record_to_data"

    adapter = adapter_class.__new__(adapter_class)
    Adapter.__init__(adapter, name=config.source.name)
    adapter.target = "source"
    adapter.config = config
    adapter.top_level = list(ORDER)
    responses = copy.deepcopy(recorded.responses)
    adapter.list_records = lambda model_name, element: copy.deepcopy(responses[model_name])  # noqa: ARG005
    snapshot = normalize_destination_schema(_schema())
    bind_runtime_models(
        adapter,
        build_runtime_models(snapshot=snapshot, configuration=config, model_base=getattr(module, recorded.model_base)),
    )
    return adapter


def _potenda(name: str, recorded: RecordedSource) -> Potenda:
    """Both sides of `recorded`: the adapter as the source, an empty Infrahub as the destination."""
    config = _config(name)
    source = _recorded_adapter(name, recorded, config)
    snapshot = normalize_destination_schema(_schema())
    destination = FixtureDestination(config=config, nodes={}, schema=snapshot.kinds)
    bind_runtime_models(
        destination, build_runtime_models(snapshot=snapshot, configuration=config, model_base=InfrahubModel)
    )
    run_dir = cache_root_for(config.name) / RUN_ID
    run_dir.mkdir(parents=True, exist_ok=True)
    return Potenda(
        source=source,  # ty: ignore[invalid-argument-type]
        destination=destination,
        config=config,
        top_level=list(ORDER),
        run_dir=run_dir,
        run_id=RUN_ID,
        show_progress=False,
        concurrent_load=False,
    )


@pytest.mark.parametrize("name", sorted(SOURCES))
def test_the_read_records_each_missing_peer(name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The device is not loaded; the address is loaded with a placeholder `vrf`; both are in the load report."""
    monkeypatch.setenv("INFRAHUB_SYNC_CACHE_DIR", str(tmp_path / "cache"))
    recorded = SOURCES[name]
    potenda = _potenda(name, recorded)

    load_both_sides(potenda)

    source = potenda.source
    entries = sorted(source.load_report.entries, key=lambda entry: entry.record.kind)  # ty: ignore[unresolved-attribute]
    assert [
        (entry.reason, entry.record.kind, entry.field, entry.peer.kind, entry.peer.source_id, entry.loaded)
        for entry in entries
        if entry.peer is not None
    ] == [
        ("peer_not_found_in_source", "DcimDevice", "site", "LocationSite", recorded.missing_site, False),
        ("peer_not_found_in_source", "IpamIPAddress", "vrf", "IpamVRF", recorded.missing_vrf, True),
    ]
    assert [device.model_dump()["name"] for device in source.get_all("DcimDevice")] == ["r2"]
    vrfs = {row["address"]: row["vrf"] for row in (item.model_dump() for item in source.get_all("IpamIPAddress"))}
    assert set(vrfs) == {"10.0.0.9/32", "10.0.0.1/32"}
    assert is_not_in_source_placeholder(vrfs["10.0.0.9/32"])
    assert not is_not_in_source_placeholder(vrfs["10.0.0.1/32"])


@pytest.mark.xfail(
    strict=True,
    raises=NotInSourcePeerInPlanError,
    reason=(
        "spec 019 T028 not implemented: Potenda.write_plan does not run the selection step yet, so the "
        "writer refuses the placeholder of the peer the source does not return"
    ),
)
@pytest.mark.parametrize("name", sorted(SOURCES))
def test_a_peer_the_source_does_not_return_is_listed_and_the_plan_finishes(
    name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two `peer_not_found_in_source` problems; the device is left out, the address is kept without `vrf`."""
    monkeypatch.setenv("INFRAHUB_SYNC_CACHE_DIR", str(tmp_path / "cache"))
    recorded = SOURCES[name]
    potenda = _potenda(name, recorded)

    load_both_sides(potenda)
    planned = finish_plan(potenda)

    problems = sorted(planned.problems, key=lambda problem: problem["record"]["kind"])
    assert [problem["problem_type"] for problem in problems] == ["peer_not_found_in_source"] * 2
    device, address = problems
    assert (device["record"]["kind"], device["field"]) == ("DcimDevice", "site")
    assert (device["peer"]["kind"], device["peer"]["source_id"], device["peer"]["identity"]) == (
        "LocationSite",
        recorded.missing_site,
        None,
    )
    assert (address["record"]["kind"], address["record"]["identity"], address["field"]) == (
        "IpamIPAddress",
        {"address": "10.0.0.9/32"},
        "vrf",
    )
    assert (address["peer"]["kind"], address["peer"]["source_id"]) == ("IpamVRF", recorded.missing_vrf)

    (left_out,) = planned.left_out
    assert (record_key(left_out)[0], left_out["cause"], left_out["problem_types"]) == (
        "DcimDevice",
        "has_problem",
        ["peer_not_found_in_source"],
    )
    (kept,) = planned.kept
    assert (kept["record"]["identity"], kept["cause"], kept["relationship"]) == (
        {"address": "10.0.0.9/32"},
        "peer_not_found_in_source",
        "vrf",
    )

    operations = planned.operations
    (address_operation,) = [
        operation
        for operation in operations_of(operations, "IpamIPAddress")
        if operation.identity == {"address": "10.0.0.9/32"}
    ]
    assert all(reference.field != "vrf" for reference in address_operation.relationships or ())
    assert [operation.identity for operation in operations_of(operations, "DcimDevice")] == [{"name": "r2"}]
    assert planned.manifest["report"]["problems"] == 2
