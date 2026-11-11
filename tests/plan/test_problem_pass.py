"""The problem pass, the selection step and the format 4 writer, driven without `Potenda.write_plan` (spec 019 T024 to T027).

`tests/plan/test_problems.py`, `test_left_out_walk.py`, `test_format_4.py` and
`test_stores_unchanged.py` read the result from a plan that `Potenda.write_plan` saves, which
runs the problem pass only once T028 wires it in. This module runs the same steps a plan runs
before it is written, on the shared problem fixture loaded through a real `Potenda`:

1. derive the operations (`operations_from_diff`, `derive_deletes`)
2. collect the identity groups (`warn_missing_convergence_key`, T024)
3. run the problem pass and the selection step (`run_problem_pass`, T026)
4. write the plan (`write_plan_artifact` with the report, T027)

It also proves that no placeholder of a peer the source does not return (T025) reaches a
written plan operation.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest

from infrahub_sync.plan.derive import derive_deletes, operations_from_diff, warn_missing_convergence_key
from infrahub_sync.plan.errors import NotInSourcePeerInPlanError, PlanVerificationError
from infrahub_sync.plan.models import (
    NOT_IN_SOURCE_PEER_KEY,
    NOT_IN_SOURCE_VALUE_PREFIX,
    PlannedOperation,
    holds_not_in_source_peer,
)
from infrahub_sync.plan.problems import ProblemPassResult, run_problem_pass
from infrahub_sync.plan.reader import load_plan_artifact, read_plan_artifact_bytes
from infrahub_sync.plan.verify import verify_plan
from infrahub_sync.plan.writer import (
    MANIFEST_FILE_NAME,
    OPERATIONS_FILE_NAME,
    PLAN_DIR_NAME,
    REPORT_FILE_NAME,
    write_plan_artifact,
)
from infrahub_sync.runtime_schema.worker import planned_dropdown_choices
from tests.plan.artifact_fixtures import CONFIG_VERSION as ARTIFACT_CONFIG_VERSION
from tests.plan.artifact_fixtures import RUN_ID as ARTIFACT_RUN_ID
from tests.plan.artifact_fixtures import operation_record, write_artifact
from tests.plan.conftest import Variant
from tests.plan.problem_plan import build_potenda, load_both_sides

if TYPE_CHECKING:
    from pathlib import Path

    from infrahub_sync.potenda import Potenda
    from tests.plan.conftest import PlanProblemFixture

CONFIG_VERSION = "5f2c9b1e7a4d3c8f"
RUN_ID = "20261111T120000-01900027"


@pytest.fixture(autouse=True)
def _cache_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INFRAHUB_SYNC_CACHE_DIR", str(tmp_path / "cache"))


def _loaded(fixture: PlanProblemFixture, variant: Variant = Variant.ALL, **options: Any) -> Potenda:  # noqa: ANN401
    potenda = build_potenda(fixture, variant, **options)
    load_both_sides(potenda)
    return potenda


def _derived(potenda: Potenda, *, with_deletes: bool = False) -> list[PlannedOperation]:
    operations = operations_from_diff(
        potenda.diff(),
        config=potenda.config,
        tier_of=lambda _kind: 0,
        source_adapter=potenda.source,
        destination_adapter=potenda.destination,
    )
    if with_deletes:
        operations.extend(
            derive_deletes(
                kinds=list(potenda.top_level),
                source_adapter=potenda.source,
                destination_adapter=potenda.destination,
                config=potenda.config,
                tier_of=lambda _kind: 0,
                destination_full_extract=True,
            )
        )
    return operations


def _pass(potenda: Potenda, operations: list[PlannedOperation]) -> ProblemPassResult:
    groups = warn_missing_convergence_key(
        destination=potenda.destination, operations=operations, source_adapter=potenda.source, config=potenda.config
    )
    return run_problem_pass(
        source=potenda.source,
        destination=potenda.destination,
        operations=operations,
        config=potenda.config,
        identity_groups=groups,
        dropdown_choices=planned_dropdown_choices(potenda.config),
    )


def _write(run_dir: Path, result: ProblemPassResult) -> None:
    write_plan_artifact(
        run_dir=run_dir,
        run_id=RUN_ID,
        config_version=CONFIG_VERSION,
        source_snapshot=[],
        deletes_computed=True,
        operations=result.operations,
        report=result.report,
        dropdown_values_checked=result.dropdown_values_checked,
    )


def _key(reference: Any) -> tuple[str, str | None]:  # noqa: ANN401 - a source record reference
    return reference.kind, reference.source_id


def test_the_pass_finds_every_expected_problem(plan_problems: PlanProblemFixture) -> None:
    """The ten problem entries of `expected.json`, one per record or group (T024, T026)."""
    potenda = _loaded(plan_problems)
    result = _pass(potenda, _derived(potenda))

    found = sorted(
        (
            problem.problem_type,
            problem.record.kind,
            sorted([problem.record.source_id, *(other.source_id for other in problem.other_records)]),
            problem.field,
        )
        for problem in result.report.problems
    )
    expected = sorted(
        (item["problem_type"], item["kind"], sorted(item["source_ids"]), item["field"])
        for item in plan_problems.expected_problems()
    )
    assert found == expected
    assert all(problem.reason for problem in result.report.problems)
    assert result.dropdown_values_checked is True


def test_the_pass_leaves_out_the_dependents_and_keeps_the_optional_referrers(plan_problems: PlanProblemFixture) -> None:
    potenda = _loaded(plan_problems)
    result = _pass(potenda, _derived(potenda))

    dependents = sorted(
        (*_key(item.record), _key(item.depends_on))
        for item in result.report.left_out
        if item.cause == "depends_on_left_out"
    )
    assert dependents == sorted(
        (item["kind"], item["source_id"], (item["depends_on"]["kind"], item["depends_on"]["source_id"]))
        for item in plan_problems.expected_left_out_dependents()
    )
    kept = sorted(
        (*_key(item.record), item.cause, item.relationship, _key(item.left_out_peer)) for item in result.report.kept
    )
    assert kept == sorted(
        (
            item["kind"],
            item["source_id"],
            item["cause"],
            item["relationship"],
            (item["peer"]["kind"], item["peer"]["source_id"]),
        )
        for item in plan_problems.expected_kept()
    )


def test_the_identity_groups_are_returned_not_raised(plan_problems: PlanProblemFixture) -> None:
    """T024: devices 33 and 34 collide, racks 21 and 22 merge; the check returns both groups."""
    potenda = _loaded(plan_problems)
    operations = _derived(potenda)

    groups = warn_missing_convergence_key(
        destination=potenda.destination, operations=operations, source_adapter=potenda.source, config=potenda.config
    )

    assert {(group.problem_type, group.kind) for group in groups} == {
        ("destination_identity_collision", "DcimDevice"),
        ("identities_merge_in_destination", "LocationRack"),
    }


def test_derivation_names_a_peer_not_in_the_source_with_a_placeholder(plan_problems: PlanProblemFixture) -> None:
    """T025: address 82's VRF 998 is a placeholder pair, never a literal destination identity."""
    potenda = _loaded(plan_problems)
    operations = _derived(potenda)

    address = next(operation for operation in operations if operation.identity == {"address": "10.0.0.9/32"})
    (vrf,) = [reference for reference in address.relationships or [] if reference.field == "vrf"]
    assert vrf.peers == [{NOT_IN_SOURCE_PEER_KEY: {"source_id": "998"}}]
    assert NOT_IN_SOURCE_VALUE_PREFIX not in json.dumps(address.model_dump())


def test_no_placeholder_reaches_a_written_plan_operation(plan_problems: PlanProblemFixture, tmp_path: Path) -> None:
    """After the selection step no operation names a peer the source does not return, and the file holds none."""
    potenda = _loaded(plan_problems)
    operations = _derived(potenda, with_deletes=True)
    assert any(holds_not_in_source_peer(operation.model_dump()) for operation in operations)

    result = _pass(potenda, operations)
    _write(tmp_path, result)

    assert not any(holds_not_in_source_peer(operation.model_dump()) for operation in result.operations)
    operations_bytes = (tmp_path / PLAN_DIR_NAME / OPERATIONS_FILE_NAME).read_bytes()
    assert operations_bytes
    assert NOT_IN_SOURCE_VALUE_PREFIX.encode() not in operations_bytes
    assert NOT_IN_SOURCE_PEER_KEY.encode() not in operations_bytes
    address = next(operation for operation in result.operations if operation.identity == {"address": "10.0.0.9/32"})
    assert "vrf" not in {reference.field for reference in address.relationships or []}


@pytest.mark.parametrize(
    "peer",
    [
        pytest.param({NOT_IN_SOURCE_PEER_KEY: {"source_id": "998"}}, id="placeholder-pair"),
        # The shape a saved plan held before T025: the reference value as a literal identity.
        pytest.param({"name": f"{NOT_IN_SOURCE_VALUE_PREFIX}IpamVRF:998"}, id="literal-reference-value"),
    ],
)
def test_the_writer_refuses_an_operation_that_names_a_peer_not_in_the_source(
    tmp_path: Path, peer: dict[str, Any]
) -> None:
    """The writer refuses before any file is written, so no artifact exists."""
    identity = {"address": "10.0.0.9/32"}
    operation = PlannedOperation.model_validate(
        {
            "operation_id": _operation_id("create", "IpamIPAddress", identity),
            "action": "create",
            "kind": "IpamIPAddress",
            "identity": identity,
            "tier": 0,
            "payload": {"address": "10.0.0.9/32"},
            "relationships": [{"field": "vrf", "peer_kind": "IpamVRF", "cardinality": "one", "peers": [peer]}],
        }
    )

    with pytest.raises(NotInSourcePeerInPlanError):
        write_plan_artifact(
            run_dir=tmp_path,
            run_id=RUN_ID,
            config_version=CONFIG_VERSION,
            source_snapshot=[],
            deletes_computed=False,
            operations=[operation],
        )

    assert not (tmp_path / PLAN_DIR_NAME).exists()


def _operation_id(action: str, kind: str, identity: dict[str, Any]) -> str:
    from infrahub_sync.plan.identity import operation_id

    return operation_id(action, kind, identity)


def test_the_selection_step_removes_left_out_records_and_withholds_deletes(plan_problems: PlanProblemFixture) -> None:
    """FR-008: no operation of a left-out record; no delete of a kind with a left-out record without identity."""
    nodes = plan_problems.destination_records()
    nodes["DcimDevice"] = [
        {"local_id": "node-old-dev", "name": "old-dev", "serial": "O1", "position": None, "site": "twin"}
    ]
    nodes["IpamIPAddress"] = [{"local_id": "node-old-ip", "address": "10.9.9.9/32", "device": None, "vrf": None}]
    potenda = _loaded(plan_problems, destination=nodes)
    operations = _derived(potenda, with_deletes=True)
    deletes = {
        (operation.kind, json.dumps(operation.identity, sort_keys=True))
        for operation in operations
        if operation.action == "delete"
    }
    assert any(kind == "DcimDevice" for kind, _ in deletes)
    assert any(kind == "IpamIPAddress" for kind, _ in deletes)

    result = _pass(potenda, operations)

    withheld = result.report.deletes_withheld_by_kind()
    # Spine-1 (36) and the filtered interface's record have no identity; the twin site is ambiguous.
    assert {"DcimDevice", "InterfacePhysical", "IpamVRF", "LocationSite"} <= set(withheld)
    assert "IpamIPAddress" not in withheld
    selected_deletes = {operation.kind for operation in result.operations if operation.action == "delete"}
    assert "DcimDevice" not in selected_deletes
    assert "IpamIPAddress" in selected_deletes
    left_out = {
        (item.record.kind, json.dumps(item.record.identity, sort_keys=True))
        for item in result.report.left_out
        if item.record.identity is not None
    }
    assert not [
        operation
        for operation in result.operations
        if (operation.kind, json.dumps(operation.identity, sort_keys=True)) in left_out
    ]


def test_a_run_without_a_schema_snapshot_does_not_check_dropdown_values(plan_problems: PlanProblemFixture) -> None:
    potenda = _loaded(plan_problems, schema_snapshot=False)
    result = _pass(potenda, _derived(potenda))

    assert result.dropdown_values_checked is False
    assert "dropdown_value_not_in_choices" not in {problem.problem_type for problem in result.report.problems}


def test_the_pass_changes_neither_store(plan_problems: PlanProblemFixture) -> None:
    potenda = _loaded(plan_problems)

    def stores() -> list[list[str]]:
        return [
            sorted(json.dumps(item.model_dump(), sort_keys=True, default=str) for item in adapter.get_all(kind))
            for adapter in (potenda.source, potenda.destination)
            for kind in plan_problems.order
        ]

    before = stores()
    _pass(potenda, _derived(potenda))

    assert stores() == before


def test_the_written_report_is_read_back_and_checked(plan_problems: PlanProblemFixture, tmp_path: Path) -> None:
    """T027: the reader returns the report lines; a changed report fails the reader and the verifier."""
    potenda = _loaded(plan_problems)
    result = _pass(potenda, _derived(potenda))
    _write(tmp_path, result)

    loaded = load_plan_artifact(tmp_path)
    assert loaded.manifest.format_version == 4
    assert loaded.manifest.report is not None
    assert loaded.manifest.report.problems == len(result.report.problems) == 10
    assert len(loaded.report) == len(result.report.problems) + len(result.report.left_out) + len(result.report.kept)
    assert verify_plan(artifact=read_plan_artifact_bytes(tmp_path), run_id=RUN_ID, config_version=CONFIG_VERSION) == []

    report_path = tmp_path / PLAN_DIR_NAME / REPORT_FILE_NAME
    report_path.write_bytes(report_path.read_bytes().replace(b"has_problem", b"has_Problem", 1))

    with pytest.raises(PlanVerificationError):
        load_plan_artifact(tmp_path)
    failures = verify_plan(artifact=read_plan_artifact_bytes(tmp_path), run_id=RUN_ID, config_version=CONFIG_VERSION)
    assert [failure.check for failure in failures] == ["report"]


def test_a_format_4_manifest_without_its_report_record_is_refused(tmp_path: Path) -> None:
    write_plan_artifact(
        run_dir=tmp_path,
        run_id=RUN_ID,
        config_version=CONFIG_VERSION,
        source_snapshot=[],
        deletes_computed=False,
        operations=[],
    )
    manifest_path = tmp_path / PLAN_DIR_NAME / MANIFEST_FILE_NAME
    manifest = json.loads(manifest_path.read_bytes())
    del manifest["report"]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(PlanVerificationError, match="no 'report' record"):
        load_plan_artifact(tmp_path)


def _walk_records(fixture: PlanProblemFixture) -> dict[str, list[dict[str, Any]]]:
    """Site `berlin` (a Dropdown problem, built), a device at it, an interface on it and an address of it."""
    berlin = next(site for site in fixture.source_records(Variant.ALL)["LocationSite"] if site["id"] == 3)
    return {
        "LocationSite": [berlin],
        "DcimDevice": [{"id": 90, "name": "edge-9", "serial": "X9", "position": 1, "role": "leaf", "site": {"id": 3}}],
        "InterfacePhysical": [{"id": 91, "name": "eth9", "device": {"id": 90}}],
        "IpamIPAddress": [{"id": 92, "address": "10.0.0.92/32", "device": {"id": 90}, "vrf": None}],
    }


def test_the_walk_leaves_out_the_dependents_of_a_record_the_read_built(plan_problems: PlanProblemFixture) -> None:
    """A record left out by the pass, not by the read: its dependents follow through the store's references."""
    potenda = _loaded(plan_problems, records=_walk_records(plan_problems), destination={})
    result = _pass(potenda, _derived(potenda))

    assert sorted(
        (*_key(item.record), item.cause, None if item.depends_on is None else _key(item.depends_on), item.relationship)
        for item in result.report.left_out
    ) == [
        ("DcimDevice", "90", "depends_on_left_out", ("LocationSite", "3"), "site"),
        ("InterfacePhysical", "91", "depends_on_left_out", ("DcimDevice", "90"), "device"),
        ("LocationSite", "3", "has_problem", None, None),
    ]
    assert [
        (*_key(item.record), item.cause, item.relationship, _key(item.left_out_peer)) for item in result.report.kept
    ] == [("IpamIPAddress", "92", "peer_left_out", "device", ("DcimDevice", "90"))]
    (address,) = result.operations
    assert (address.action, address.identity) == ("create", {"address": "10.0.0.92/32"})
    assert "device" not in {reference.field for reference in address.relationships or []}


def test_an_update_that_changes_only_a_dropped_relationship_is_not_planned(plan_problems: PlanProblemFixture) -> None:
    """Infrahub holds the address without a device: the only change is the left-out device, so no update."""
    nodes = {"IpamIPAddress": [{"local_id": "node-92", "address": "10.0.0.92/32", "device": None, "vrf": None}]}
    potenda = _loaded(plan_problems, records=_walk_records(plan_problems), destination=nodes)
    operations = _derived(potenda)
    assert [(operation.action, operation.kind) for operation in operations if operation.kind == "IpamIPAddress"] == [
        ("update", "IpamIPAddress")
    ]

    result = _pass(potenda, operations)

    assert [item.relationship for item in result.report.kept] == ["device"]
    assert [operation for operation in result.operations if operation.kind == "IpamIPAddress"] == []


def test_a_format_3_plan_reads_without_a_report(tmp_path: Path) -> None:
    """A format 3 plan has no report: it stays readable, with no report lines, and verifies clean."""
    write_artifact(tmp_path, [operation_record()], format_version=3)

    loaded = load_plan_artifact(tmp_path)

    assert loaded.manifest.format_version == 3
    assert loaded.manifest.report is None
    assert loaded.report == ()
    assert not (tmp_path / PLAN_DIR_NAME / REPORT_FILE_NAME).exists()
    failures = verify_plan(
        artifact=read_plan_artifact_bytes(tmp_path), run_id=ARTIFACT_RUN_ID, config_version=ARTIFACT_CONFIG_VERSION
    )
    assert [failure.check for failure in failures if failure.check == "report"] == []
