"""A reference to a peer the source does not return is a plan problem (spec 009 T038, SC-010).

Each bundled adapter that resolves references while loading loads recorded API responses: a
device refers to a site the source does not return through the required relationship `site`,
and an IP address refers to a VRF the source does not return through the optional relationship
`vrf`. The plan finishes, lists two `peer_not_found_in_source` problems, leaves out the device,
keeps the IP address and writes no `vrf` change for it.

The adapters run through their own `record_to_data` and the shared loader of the base class;
only `list_records`, the call to the provider, returns the recorded responses.
"""

from __future__ import annotations

import copy
import importlib
import sys
import types
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

import pytest
from diffsync import Adapter
from diffsync.store.local import LocalStore

from infrahub_sync import DiffSyncMixin, SchemaMappingField, SchemaMappingModel, SyncAdapter, SyncInstance
from infrahub_sync.plan.derive import operations_from_diff
from infrahub_sync.plan.problems import find_problems, select_operations
from infrahub_sync.runtime_schema.domain import normalize_destination_schema
from infrahub_sync.runtime_schema.models import build_runtime_models
from infrahub_sync.runtime_schema.worker import bind_runtime_models
from tests.adapters.offline_holder import offline_adapter
from tests.plan.problem_fixtures import ProblemFixtureModel
from tests.plan.problem_plan import FixtureDestination

if TYPE_CHECKING:
    from collections.abc import Iterator

    from infrahub_sync.plan.models import PlannedOperation, PlanReport

pytest.importorskip("pynetbox")
pytest.importorskip("pynautobot")

from infrahub_sync.adapters.aci import AciAdapter, AciModel
from infrahub_sync.adapters.genericrestapi import GenericrestapiAdapter, GenericrestapiModel
from infrahub_sync.adapters.nautobot import NautobotAdapter, NautobotModel
from infrahub_sync.adapters.netbox import NetboxAdapter, NetboxModel

SLURPIT_MODULE = "infrahub_sync.adapters.slurpitsync"

ORDER = ["LocationSite", "IpamVRF", "DcimDevice", "IpamIPAddress"]


def _schema() -> dict[str, Any]:
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
    """One adapter, its model base and the API responses it recorded, by kind."""

    adapter: type[DiffSyncMixin]
    model_base: type[Any]
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
        NetboxAdapter, NetboxModel, _nested_responses(1, 10, 999, 998, [31, 32, 81, 82]), "999", "998"
    ),
    "nautobot": RecordedSource(
        NautobotAdapter,
        NautobotModel,
        _nested_responses(_uuid(1), _uuid(10), _uuid(999), _uuid(998), [_uuid(31), _uuid(32), _uuid(81), _uuid(82)]),
        _uuid(999),
        _uuid(998),
    ),
    "genericrestapi": RecordedSource(
        GenericrestapiAdapter,
        GenericrestapiModel,
        _nested_responses("site-1", "vrf-10", "site-999", "vrf-998", ["dev-31", "dev-32", "ip-81", "ip-82"]),
        "site-999",
        "vrf-998",
    ),
    # The APIC returns flat attribute objects; a reference is the peer's ID.
    "aci": RecordedSource(
        AciAdapter,
        AciModel,
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
    # Slurp'it states a reference as the peer's unique ID. Its classes are imported by the
    # `slurpit_module` fixture, because the Slurp'it SDK is an optional extra.
    "slurpitsync": RecordedSource(
        DiffSyncMixin,
        object,
        {
            "LocationSite": [{"id": 1, "name": "paris"}],
            "IpamVRF": [{"id": 10, "name": "default"}],
            "DcimDevice": [{"id": 31, "name": "r1", "site": "lyon"}, {"id": 32, "name": "r2", "site": "paris"}],
            "IpamIPAddress": [
                {"id": 81, "address": "10.0.0.9/32", "vrf": "blue"},
                {"id": 82, "address": "10.0.0.1/32", "vrf": "default"},
            ],
        },
        "lyon",
        "blue",
    ),
}


@pytest.fixture(name="slurpit_module")
def _slurpit_module() -> Iterator[types.ModuleType]:
    """Import the Slurp'it adapter, with a stub SDK when the optional extra is not installed."""
    adapters_package = importlib.import_module("infrahub_sync.adapters")
    prior_modules = frozenset(sys.modules)
    had_attr = hasattr(adapters_package, "slurpitsync")
    prior_attr = getattr(adapters_package, "slurpitsync", None)
    patch = pytest.MonkeyPatch()
    try:
        importlib.import_module("slurpit")
    except ImportError:
        sdk = types.ModuleType("slurpit")
        setattr(sdk, "api", object)  # noqa: B010 - the attribute the adapter module reads
        patch.setitem(sys.modules, "slurpit", sdk)
    try:
        yield importlib.import_module(SLURPIT_MODULE)
    finally:
        patch.undo()
        for added in set(sys.modules) - prior_modules:
            if added == SLURPIT_MODULE or added.startswith("slurpit"):
                sys.modules.pop(added, None)
        if had_attr:
            setattr(adapters_package, "slurpitsync", prior_attr)  # noqa: B010 - restore the import state
        elif hasattr(adapters_package, "slurpitsync"):
            delattr(adapters_package, "slurpitsync")


@pytest.fixture(name="recorded")
def _recorded(request: pytest.FixtureRequest) -> tuple[str, RecordedSource]:
    name: str = request.param
    if name != "slurpitsync":
        return name, SOURCES[name]
    module = request.getfixturevalue("slurpit_module")
    return name, replace(SOURCES[name], adapter=module.SlurpitsyncAdapter, model_base=module.SlurpitsyncModel)


def _recorded_adapter(recorded: RecordedSource, config: SyncInstance) -> DiffSyncMixin:
    adapter = offline_adapter(recorded.adapter, LocalStore())
    # DiffSync's own state, without the adapter's client.
    Adapter.__init__(adapter, name=config.source.name)  # ty: ignore[invalid-argument-type]
    adapter.config = config
    adapter.top_level = list(ORDER)  # ty: ignore[invalid-attribute-access]
    responses = copy.deepcopy(recorded.responses)
    adapter.list_records = lambda model_name, element: copy.deepcopy(responses[model_name])  # noqa: ARG005  # ty: ignore[invalid-assignment]
    snapshot = normalize_destination_schema(_schema())
    bind_runtime_models(
        adapter, build_runtime_models(snapshot=snapshot, configuration=config, model_base=recorded.model_base)
    )
    return adapter


def _plan(name: str, recorded: RecordedSource) -> tuple[PlanReport, list[PlannedOperation]]:
    config = _config(name)
    source = _recorded_adapter(recorded, config)
    snapshot = normalize_destination_schema(_schema())
    destination = FixtureDestination(config, {}, _schema())
    destination.top_level = list(ORDER)  # ty: ignore[invalid-attribute-access]
    bind_runtime_models(
        destination, build_runtime_models(snapshot=snapshot, configuration=config, model_base=ProblemFixtureModel)
    )
    DiffSyncMixin.load(source)
    destination.load()
    operations = operations_from_diff(
        destination.diff_from(source),  # ty: ignore[invalid-argument-type]
        config=config,
        tier_of=lambda _: 0,
        source_adapter=source,
        destination_adapter=destination,
    )
    report = find_problems(source.load_report, [], source, snapshot, operations, config=config, destination=destination)
    return report, select_operations(operations, report)


@pytest.mark.parametrize("recorded", sorted(SOURCES), indirect=True)
def test_a_peer_the_source_does_not_return_is_listed_and_the_plan_finishes(
    recorded: tuple[str, RecordedSource],
) -> None:
    name, source = recorded

    report, selected = _plan(name, source)
    problems = sorted(report.problems, key=lambda problem: problem.record.kind)

    assert [problem.problem_type for problem in problems] == ["peer_not_found_in_source"] * 2
    device, address = problems
    assert (device.record.kind, device.record.identity, device.field) == ("DcimDevice", None, "site")
    assert device.peer is not None
    assert (device.peer.kind, device.peer.source_id) == ("LocationSite", source.missing_site)
    assert (address.record.kind, address.record.identity, address.field) == (
        "IpamIPAddress",
        {"address": "10.0.0.9/32"},
        "vrf",
    )
    assert address.peer is not None
    assert (address.peer.kind, address.peer.source_id) == ("IpamVRF", source.missing_vrf)

    (left_out,) = report.left_out
    assert (left_out.record.kind, left_out.cause, left_out.problem_types) == (
        "DcimDevice",
        "has_problem",
        ["peer_not_found_in_source"],
    )
    (kept,) = report.kept
    assert (kept.record.identity, kept.cause, kept.relationship) == (
        {"address": "10.0.0.9/32"},
        "peer_not_found_in_source",
        "vrf",
    )

    (operation,) = [op for op in selected if op.kind == "IpamIPAddress" and op.identity == {"address": "10.0.0.9/32"}]
    assert all(reference.field != "vrf" for reference in operation.relationships or ())
    assert [op.identity for op in selected if op.kind == "DcimDevice"] == [{"name": "r2"}]
