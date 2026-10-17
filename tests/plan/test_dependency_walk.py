"""The dependency walk leaves out dependents and keeps optional referrers (spec 009 T032, research R7)."""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING, Any

from infrahub_sync import SchemaMappingField
from infrahub_sync.plan.derive import operations_from_diff
from infrahub_sync.plan.problems import find_problems
from infrahub_sync.runtime_schema.domain import normalize_destination_schema
from infrahub_sync.runtime_schema.models import build_runtime_models
from infrahub_sync.runtime_schema.worker import bind_runtime_models
from tests.plan.problem_fixtures import (
    MISSING_DEVICE_SOURCE_ID,
    MISSING_VRF_SOURCE_ID,
    PROBLEM_DEVICE_DEPENDENTS,
    PROBLEM_DEVICE_SOURCE_ID,
    ProblemFixtureAdapter,
    ProblemFixtureModel,
    Variant,
    schema_record,
    source_records,
)
from tests.plan.problem_plan import FixtureDestination, fixture_config, plan_fixture

if TYPE_CHECKING:
    from pathlib import Path

    from infrahub_sync import SyncInstance
    from infrahub_sync.plan.models import LeftOutRecord, PlanReport

CORE_1 = {"name": "core-1", "serial": "C1"}


def _left_out(report: PlanReport, kind: str, source_id: str) -> LeftOutRecord:
    (item,) = [entry for entry in report.left_out if (entry.record.kind, entry.record.source_id) == (kind, source_id)]
    return item


def test_the_device_with_a_problem_and_its_five_interfaces_are_left_out(tmp_path: Path) -> None:
    """Scenario 2: each interface depends on the device through `device`; six records in all."""
    report = plan_fixture(tmp_path / "run").report
    device = _left_out(report, "DcimDevice", PROBLEM_DEVICE_SOURCE_ID)
    interfaces = [
        entry for entry in report.left_out if entry.depends_on is not None and entry.depends_on.identity == CORE_1
    ]

    assert device.cause == "has_problem"
    assert device.loaded is False
    assert len(interfaces) == PROBLEM_DEVICE_DEPENDENTS
    for entry in interfaces:
        assert entry.record.kind == "InterfacePhysical"
        assert entry.cause == "depends_on_left_out"
        assert entry.relationship == "device"
        assert entry.depends_on is not None
        assert (entry.depends_on.kind, entry.depends_on.source_id) == ("DcimDevice", PROBLEM_DEVICE_SOURCE_ID)
        assert entry.loaded is True
        identity = entry.record.identity or {}
        assert identity == {"device": {"peer_kind": "DcimDevice", "identity": CORE_1}, "name": identity.get("name")}
    assert len([device, *interfaces]) == 6


def test_a_three_level_chain_leaves_out_every_record_naming_its_direct_dependency(tmp_path: Path) -> None:
    report = plan_fixture(tmp_path / "run", Variant.DEPENDENCY_CHAIN).report

    device = _left_out(report, "DcimDevice", PROBLEM_DEVICE_SOURCE_ID)
    interface = _left_out(report, "InterfacePhysical", "40")
    virtual = _left_out(report, "InterfaceVirtual", "71")

    assert len(report.left_out) == 3
    assert device.cause == "has_problem"
    assert (interface.cause, interface.relationship) == ("depends_on_left_out", "device")
    assert interface.depends_on == device.record
    assert (virtual.cause, virtual.relationship) == ("depends_on_left_out", "parent")
    assert virtual.depends_on == interface.record
    assert report.kept == []


def test_the_ip_address_with_an_optional_reference_to_the_left_out_device_is_kept(tmp_path: Path) -> None:
    """Scenario 7: kept with `cause: peer_left_out`, and not counted as left out."""
    planned = plan_fixture(tmp_path / "run")
    (kept,) = [entry for entry in planned.report.kept if entry.cause == "peer_left_out"]

    assert (kept.record.kind, kept.record.identity, kept.record.source_id) == (
        "IpamIPAddress",
        {"address": "10.0.0.5/32"},
        "81",
    )
    assert kept.relationship == "device"
    assert (kept.left_out_peer.kind, kept.left_out_peer.identity) == ("DcimDevice", CORE_1)
    assert all(entry.record != kept.record for entry in planned.report.left_out)
    (address,) = [
        operation
        for operation in planned.operations
        if operation.kind == "IpamIPAddress" and operation.identity == {"address": "10.0.0.5/32"}
    ]
    assert address.action == "create"
    assert address.relationships is None


def test_a_record_whose_only_problem_is_an_optional_peer_not_in_the_source_is_kept(tmp_path: Path) -> None:
    """Scenario 8: a problem entry and a kept entry, not left out, and the walk does not start from it."""
    planned = plan_fixture(tmp_path / "run")
    record = {"address": "10.0.0.9/32"}
    problems = [problem for problem in planned.report.problems if problem.record.identity == record]
    (kept,) = [entry for entry in planned.report.kept if entry.record.identity == record]

    assert [problem.problem_type for problem in problems] == ["peer_not_found_in_source"]
    assert kept.cause == "peer_not_found_in_source"
    assert kept.relationship == "vrf"
    assert (kept.left_out_peer.kind, kept.left_out_peer.identity, kept.left_out_peer.source_id) == (
        "IpamVRF",
        None,
        MISSING_VRF_SOURCE_ID,
    )
    assert all(entry.record.identity != record for entry in planned.report.left_out)
    assert all(entry.depends_on is None or entry.depends_on.identity != record for entry in planned.report.left_out)
    assert planned.manifest.report is not None
    assert planned.manifest.report.left_out == len(planned.report.left_out)


def test_a_reference_that_is_a_human_friendly_id_component_leaves_the_record_out_even_when_optional() -> None:
    """`device` is optional on `IpamIPAddress`, but here it is part of the destination's human-friendly ID."""
    schema = copy.deepcopy(schema_record())
    schema["IpamIPAddress"]["human_friendly_id"] = ["device__name__value", "address__value"]
    snapshot = normalize_destination_schema(schema)
    config = fixture_config()
    models = build_runtime_models(snapshot=snapshot, configuration=config, model_base=ProblemFixtureModel)
    records = source_records(Variant.ALL)
    source = ProblemFixtureAdapter(target="source", adapter=config.source, config=config, records=records)
    bind_runtime_models(source, models)
    destination = FixtureDestination(config, {}, schema)
    bind_runtime_models(destination, models)
    source.load()
    destination.load()
    operations = operations_from_diff(
        destination.diff_from(source),
        config=config,
        tier_of=lambda _: 0,
        source_adapter=source,
        destination_adapter=destination,
    )

    report = find_problems(source.load_report, [], source, snapshot, operations, config=config, destination=destination)
    address = _left_out(report, "IpamIPAddress", "81")

    assert (address.cause, address.relationship) == ("depends_on_left_out", "device")
    assert address.depends_on is not None
    assert address.depends_on.identity == CORE_1
    assert all(entry.record.source_id != "81" for entry in report.kept)


def _config_with_an_optional_interface_reference() -> tuple[Any, dict[str, Any]]:
    config = fixture_config()
    entry = next(item for item in config.schema_mapping if item.name == "IpamIPAddress")
    entry.fields.append(SchemaMappingField(name="interface", mapping="interface", reference="InterfacePhysical"))
    schema = copy.deepcopy(schema_record())
    schema["IpamIPAddress"]["relationships"]["interface"] = {
        "peer": "InterfacePhysical",
        "cardinality": "one",
        "optional": True,
        "kind": "Attribute",
        "read_only": False,
    }
    return config, schema


def test_a_record_whose_peer_was_not_loaded_and_has_no_identity_is_kept_without_the_optional_relationship(
    tmp_path: Path,
) -> None:
    """`eth9` was not built and has no identity; the address is kept without `interface` (FR-005)."""
    config, schema = _config_with_an_optional_interface_reference()
    records = source_records(Variant.ALL)
    records["IpamIPAddress"].append(
        {"id": 83, "address": "10.0.0.7/32", "device": None, "vrf": None, "interface": {"id": 61}}
    )

    planned = plan_fixture(tmp_path / "run", records=records, config=config, schema=schema)
    report = planned.report
    (kept,) = [entry for entry in report.kept if entry.record.source_id == "83"]

    assert all(entry.record.source_id != "83" for entry in report.left_out)
    assert (kept.record.kind, kept.record.identity) == ("IpamIPAddress", {"address": "10.0.0.7/32"})
    assert (kept.cause, kept.relationship) == ("peer_left_out", "interface")
    assert (kept.left_out_peer.kind, kept.left_out_peer.identity, kept.left_out_peer.source_id) == (
        "InterfacePhysical",
        None,
        "61",
    )
    assert all(problem.record.source_id != "83" for problem in report.problems)
    (address,) = [
        operation
        for operation in planned.operations
        if operation.kind == "IpamIPAddress" and operation.identity == {"address": "10.0.0.7/32"}
    ]
    assert address.action == "create"
    assert all(reference.field != "interface" for reference in address.relationships or ())


def _config_with_device_in_the_address_identifiers() -> SyncInstance:
    """`device` stays optional in Infrahub and out of its human-friendly ID, but forms the mapping's identity."""
    config = fixture_config()
    entry = next(item for item in config.schema_mapping if item.name == "IpamIPAddress")
    entry.identifiers = ["address", "device"]
    return config


def _addresses_with_a_device(records: dict[str, list[dict[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
    """Keep the addresses that name a device: an identifier has a value on every record."""
    records["IpamIPAddress"] = [record for record in records["IpamIPAddress"] if record["device"] is not None]
    return records


def test_a_record_whose_identity_holds_an_optional_reference_to_a_left_out_record_is_left_out(
    tmp_path: Path,
) -> None:
    """Review H4: `10.0.0.5/32` names the left-out `core-1` in its identity, so it cannot be kept without it."""
    records = _addresses_with_a_device(source_records(Variant.ALL))

    planned = plan_fixture(tmp_path / "run", records=records, config=_config_with_device_in_the_address_identifiers())
    address = _left_out(planned.report, "IpamIPAddress", "81")

    assert (address.cause, address.relationship, address.loaded) == ("depends_on_left_out", "device", True)
    assert address.depends_on is not None
    assert address.depends_on.identity == CORE_1
    assert all(entry.record.source_id != "81" for entry in planned.report.kept)
    assert all(
        operation.identity.get("address") != "10.0.0.5/32"
        for operation in planned.operations
        if operation.kind == "IpamIPAddress"
    )


def test_a_record_whose_identity_holds_an_optional_reference_to_a_peer_not_in_the_source_is_left_out(
    tmp_path: Path,
) -> None:
    """Review H4: the peer `999` is not in the source; the record is left out with its problem."""
    records = _addresses_with_a_device(source_records(Variant.ALL))
    records["IpamIPAddress"].append(
        {"id": 84, "address": "10.0.0.8/32", "device": {"id": int(MISSING_DEVICE_SOURCE_ID)}, "vrf": None}
    )

    planned = plan_fixture(tmp_path / "run", records=records, config=_config_with_device_in_the_address_identifiers())
    address = _left_out(planned.report, "IpamIPAddress", "84")

    assert (address.cause, address.problem_types) == ("has_problem", ["peer_not_found_in_source"])
    assert all(entry.record.source_id != "84" for entry in planned.report.kept)
    assert all(
        operation.identity.get("address") != "10.0.0.8/32"
        for operation in planned.operations
        if operation.kind == "IpamIPAddress"
    )


def test_a_record_whose_identity_holds_an_optional_reference_to_a_peer_with_no_identity_is_left_out(
    tmp_path: Path,
) -> None:
    """Review H4: `eth9` was not built and has no identity; the address cannot form its identity without it."""
    config, schema = _config_with_an_optional_interface_reference()
    entry = next(item for item in config.schema_mapping if item.name == "IpamIPAddress")
    entry.identifiers = ["address", "interface"]
    records = source_records(Variant.ALL)
    # An identifier has a value on every record, so only the address with an interface stays.
    records["IpamIPAddress"] = [
        {"id": 83, "address": "10.0.0.7/32", "device": None, "vrf": None, "interface": {"id": 61}}
    ]

    planned = plan_fixture(tmp_path / "run", records=records, config=config, schema=schema)
    address = _left_out(planned.report, "IpamIPAddress", "83")

    assert (address.cause, address.relationship, address.loaded) == ("depends_on_left_out", "interface", False)
    assert address.depends_on is not None
    assert (address.depends_on.kind, address.depends_on.source_id) == ("InterfacePhysical", "61")
    assert all(entry.record.source_id != "83" for entry in planned.report.kept)
    assert all(
        operation.identity.get("address") != "10.0.0.7/32"
        for operation in planned.operations
        if operation.kind == "IpamIPAddress"
    )


class _OptionalReferencesSource(ProblemFixtureAdapter):
    """An adapter that resolves every reference as optional, identifiers included."""

    def _resolve_peer(
        self, peer_kind: str, peer_source_id: str, *, owner: tuple[str, str], field: str, required: bool
    ) -> str | None:
        del required
        return super()._resolve_peer(peer_kind, peer_source_id, owner=owner, field=field, required=False)


def test_an_identifier_reference_resolved_as_optional_to_a_peer_not_in_the_source_still_leaves_the_record_out(
    tmp_path: Path,
) -> None:
    """Review H4: the plan side does not rely on the adapter; the record loads but is left out with its problem."""
    records = _addresses_with_a_device(source_records(Variant.ALL))
    records["IpamIPAddress"].append(
        {"id": 84, "address": "10.0.0.8/32", "device": {"id": int(MISSING_DEVICE_SOURCE_ID)}, "vrf": None}
    )

    planned = plan_fixture(
        tmp_path / "run",
        records=records,
        config=_config_with_device_in_the_address_identifiers(),
        source_class=_OptionalReferencesSource,
    )
    address = _left_out(planned.report, "IpamIPAddress", "84")

    assert (address.cause, address.problem_types, address.loaded) == ("has_problem", ["peer_not_found_in_source"], True)
    assert all(entry.record.source_id != "84" for entry in planned.report.kept)
    assert all(
        operation.identity.get("address") != "10.0.0.8/32"
        for operation in planned.operations
        if operation.kind == "IpamIPAddress"
    )
