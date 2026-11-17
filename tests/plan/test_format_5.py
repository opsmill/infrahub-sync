"""Saved plan format 5 (spec 019 FR-007, FR-010 to FR-012, contracts/plan-format-5.md).

Format 5 is format 4 with one change: a one-peer reference of an update can hold no peer, which
means "the relationship becomes empty". Every plan this release writes is saved in format 5.

- An empty one-peer reference is accepted on an update and refused on a create, on a delete and
  on a field that is part of the operation identity.
- In a format 4 plan a one-peer reference still holds exactly one peer.
- The plan checksum covers the emptied reference and the format version.
- Format 4 plans are still read, verified, approved by checksum, applied and resumed (FR-012).
- A format 6 plan is refused by `runs plan`, `verify`, apply and resume before any operation is
  read, with a message that names the version found and the supported versions (FR-011).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest
from pydantic import ValidationError

from infrahub_sync.plan import reader as plan_reader
from infrahub_sync.plan.errors import PlanArtifactTornError, PlanFormatVersionError, PlanVerificationError
from infrahub_sync.plan.models import (
    PLAN_FORMAT_VERSION,
    SUPPORTED_FORMAT_VERSIONS,
    PlannedOperation,
    RelationshipReference,
)
from infrahub_sync.plan.reader import read_plan_artifact_bytes
from infrahub_sync.plan.review import read_saved_plan
from infrahub_sync.plan.verify import verify_plan
from tests.plan.artifact_fixtures import (
    CONFIG_VERSION,
    RUN_ID,
    SYNC_NAME,
    manifest_path,
    operation_record,
    operations_path,
    write_artifact,
)
from tests.plan.test_plan_format_3 import (
    CountingDestination,
    RecordingOwnership,
    RefusingDestination,
    engine_over,
    parse,
    run_dir,
    update_record,
)

if TYPE_CHECKING:
    from pathlib import Path


FUTURE_FORMAT_VERSION = 6
SUPPORTED_TEXT = "2, 3, 4, 5"
LAG_PEER = {"name": "ae0"}


def _lag(peers: list[dict[str, Any]]) -> dict[str, Any]:
    """The `lag` reference of an interface, holding `peers`."""
    return {"field": "lag", "peer_kind": "InterfaceLag", "cardinality": "one", "peers": peers}


def _emptied_update() -> dict[str, Any]:
    """An update that empties the LAG of an interface, as contracts/plan-format-5.md shows it."""
    return operation_record(action="update", relationships=[_lag([])])


def _emptied_create() -> dict[str, Any]:
    return operation_record(action="create", relationships=[_lag([])])


def _emptied_identity_update() -> dict[str, Any]:
    """An update whose identity names the device by reference, and whose `device` reference is empty."""
    device = {"peer_kind": "DcimDevice", "identity": {"name": "edge-01"}}
    return operation_record(
        action="update",
        kind="InterfacePhysical",
        identity={"name": "et-0/0/1", "device": device},
        payload={"name": "et-0/0/1"},
        relationships=[{"field": "device", "peer_kind": "DcimDevice", "cardinality": "one", "peers": []}],
    )


# ---------------------------------------------------------------------------------------
# The format constants
# ---------------------------------------------------------------------------------------


def test_this_release_writes_format_5_and_reads_formats_2_to_5() -> None:
    assert PLAN_FORMAT_VERSION == 5
    assert frozenset({2, 3, 4, 5}) == SUPPORTED_FORMAT_VERSIONS


# ---------------------------------------------------------------------------------------
# The emptied one-peer reference (FR-007)
# ---------------------------------------------------------------------------------------


def test_an_emptied_one_peer_reference_is_accepted_on_an_update() -> None:
    operation = PlannedOperation.model_validate(_emptied_update(), context={"format_version": 5})

    assert operation.relationships is not None
    assert operation.relationships[0].cardinality == "one"
    assert operation.relationships[0].peers == []


def test_an_emptied_one_peer_reference_is_accepted_on_an_update_built_in_process() -> None:
    """With no reader context the record is built in process, so the current format applies."""
    operation = PlannedOperation.model_validate(_emptied_update())

    assert operation.relationships is not None
    assert operation.relationships[0].peers == []


def test_a_one_peer_reference_with_one_peer_is_still_accepted_in_format_5() -> None:
    record = operation_record(action="update", relationships=[_lag([LAG_PEER])])

    operation = PlannedOperation.model_validate(record, context={"format_version": 5})

    assert operation.relationships is not None
    assert operation.relationships[0].peers == [LAG_PEER]


@pytest.mark.parametrize("format_version", [4, 5])
def test_a_one_peer_reference_with_two_peers_is_refused(format_version: int) -> None:
    record = operation_record(action="update", relationships=[_lag([LAG_PEER, {"name": "ae1"}])])

    with pytest.raises(ValidationError, match="got 2"):
        PlannedOperation.model_validate(record, context={"format_version": format_version})


def test_an_emptied_one_peer_reference_is_refused_on_a_create() -> None:
    with pytest.raises(ValidationError, match="a create cannot empty relationship `lag`"):
        PlannedOperation.model_validate(_emptied_create(), context={"format_version": 5})


def test_an_emptied_one_peer_reference_is_refused_on_a_create_built_in_process() -> None:
    with pytest.raises(ValidationError, match="a create cannot empty relationship `lag`"):
        PlannedOperation.model_validate(_emptied_create())


def test_an_emptied_one_peer_reference_is_refused_on_a_delete() -> None:
    record = operation_record(action="delete", relationships=[_lag([])])

    with pytest.raises(ValidationError, match="a delete cannot empty relationship `lag`"):
        PlannedOperation.model_validate(record, context={"format_version": 5})


def test_an_emptied_one_peer_reference_is_refused_on_an_identity_field() -> None:
    with pytest.raises(ValidationError, match="identity field `device`"):
        PlannedOperation.model_validate(_emptied_identity_update(), context={"format_version": 5})


def test_an_emptied_many_reference_is_unchanged_on_a_create() -> None:
    """The create rule is about a one-peer reference; an empty many set is a replace-set write."""
    record = operation_record(
        action="create",
        relationships=[{"field": "tags", "peer_kind": "BuiltinTag", "cardinality": "many", "peers": []}],
    )

    operation = PlannedOperation.model_validate(record, context={"format_version": 5})

    assert operation.relationships is not None
    assert operation.relationships[0].peers == []


def test_an_emptied_one_peer_reference_is_refused_in_a_format_4_record() -> None:
    with pytest.raises(ValidationError, match="must name exactly one peer, got 0"):
        PlannedOperation.model_validate(_emptied_update(), context={"format_version": 4})


def test_a_standalone_emptied_reference_follows_the_format_of_its_context() -> None:
    reference = RelationshipReference.model_validate(_lag([]))
    assert reference.peers == []

    with pytest.raises(ValidationError, match="must name exactly one peer, got 0"):
        RelationshipReference.model_validate(_lag([]), context={"format_version": 4})


# ---------------------------------------------------------------------------------------
# Reading a saved plan that empties a relationship
# ---------------------------------------------------------------------------------------


def test_a_format_5_plan_with_an_emptied_reference_is_read(tmp_path: Path) -> None:
    directory = run_dir(tmp_path)
    manifest = write_artifact(directory, [_emptied_update()], source_snapshot=[])

    loaded = parse(directory)

    assert manifest["format_version"] == 5
    assert loaded.manifest.format_version == 5
    [operation] = loaded.operations
    assert operation.relationships is not None
    assert operation.relationships[0].peers == []


def test_a_format_4_plan_with_an_emptied_reference_is_refused_when_read(tmp_path: Path) -> None:
    directory = run_dir(tmp_path)
    write_artifact(directory, [_emptied_update()], source_snapshot=[], format_version=4)

    with pytest.raises(PlanArtifactTornError, match="exactly one peer"):
        parse(directory)


@pytest.mark.parametrize("record", [_emptied_create(), _emptied_identity_update()], ids=["create", "identity"])
def test_a_format_5_plan_with_a_misplaced_emptied_reference_is_refused_when_read(
    tmp_path: Path, record: dict[str, Any]
) -> None:
    directory = run_dir(tmp_path)
    write_artifact(directory, [record], source_snapshot=[])

    with pytest.raises(PlanArtifactTornError):
        parse(directory)


# ---------------------------------------------------------------------------------------
# The checksum covers format 5 in full (FR-010)
# ---------------------------------------------------------------------------------------


def test_the_checksum_covers_an_emptied_reference(tmp_path: Path) -> None:
    """Giving the emptied reference a peer after the plan was saved fails the checksum."""
    directory = run_dir(tmp_path)
    write_artifact(directory, [_emptied_update()])
    operations_file = operations_path(directory)
    operations_file.write_bytes(
        operations_file.read_bytes().replace(b'"peers":[]', b'"peers":[' + json.dumps(LAG_PEER).encode() + b"]")
    )
    assert b'"peers":[{' in operations_file.read_bytes(), "The tamper must change the operation."

    plan = read_saved_plan(sync_name=SYNC_NAME, run_id=RUN_ID, base_directory=tmp_path)

    assert plan.checksum_ok is False


def test_the_checksum_covers_the_format_version(tmp_path: Path) -> None:
    """A format 5 manifest relabelled as format 4 without a new checksum fails verification."""
    directory = run_dir(tmp_path)
    write_artifact(directory, [update_record()], source_snapshot=[])
    path = manifest_path(directory)
    mapping = json.loads(path.read_bytes())
    mapping["format_version"] = 4
    path.write_text(json.dumps(mapping), encoding="utf-8")

    failures = verify_plan(artifact=read_plan_artifact_bytes(directory), run_id=RUN_ID, config_version=CONFIG_VERSION)

    assert [failure.check for failure in failures] == ["plan_checksum"]


# ---------------------------------------------------------------------------------------
# Format 4 plans are still read, verified and applied (FR-012)
# ---------------------------------------------------------------------------------------


def _format_4_plan(tmp_path: Path) -> Path:
    directory = run_dir(tmp_path)
    write_artifact(
        directory,
        [
            update_record(),
            operation_record(action="update", identity={"name": "core"}, relationships=[_lag([LAG_PEER])]),
        ],
        source_snapshot=[],
        format_version=4,
    )
    return directory


def test_a_format_4_plan_is_still_read(tmp_path: Path) -> None:
    directory = _format_4_plan(tmp_path)

    plan = read_saved_plan(sync_name=SYNC_NAME, run_id=RUN_ID, base_directory=tmp_path)

    assert plan.checksum_ok is True
    assert [operation.action for operation in plan.operations()] == ["update", "update"]
    assert parse(directory).manifest.format_version == 4


def test_a_format_4_plan_is_still_verified(tmp_path: Path) -> None:
    directory = _format_4_plan(tmp_path)

    failures = verify_plan(artifact=read_plan_artifact_bytes(directory), run_id=RUN_ID, config_version=CONFIG_VERSION)

    assert failures == []


def test_a_format_4_plan_is_still_approved_by_checksum(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The Sync API approves a format 4 plan by the checksum of its saved plan (FR-012)."""
    pytest.importorskip("prefect")
    pytest.importorskip("fastapi")
    # The Sync service's apply guard imports psycopg, which the Python 3.10 profile does not install.
    pytest.importorskip("psycopg")
    from fastapi.testclient import TestClient

    from infrahub_sync.product_store import local_product_projection
    from infrahub_sync.service import flow as service_flow
    from infrahub_sync.service.app import create_app
    from infrahub_sync.service.auth import PRINCIPALS_ENV, EnvironmentPrincipalResolver
    from infrahub_sync.service.service import PLAN_ARTIFACT_ID, RunService
    from tests.service import test_approval

    _format_4_plan(tmp_path)
    saved = read_saved_plan(sync_name=SYNC_NAME, run_id=RUN_ID, base_directory=tmp_path)
    assert saved.manifest.format_version == 4
    assert saved.checksum_ok is True
    checksum = saved.manifest.plan_checksum

    monkeypatch.setenv(PRINCIPALS_ENV, json.dumps({"owner": {"token": test_approval.OWNER_TOKEN}}))
    resolver = EnvironmentPrincipalResolver.from_environment()
    projection = local_product_projection((tmp_path / "service").resolve())
    version = projection.create_configuration(test_approval._registered_package())
    test_approval._stored_run(projection, version, publish=False)
    projection.publish_artifact(
        test_approval.RUN_ID,
        artifact_id=PLAN_ARTIFACT_ID,
        kind="saved-plan-review",
        media_type="application/json",
        data=service_flow._review_document(test_approval.RUN_ID, saved).model_dump_json().encode(),
    )
    service = RunService(projection, test_approval._RecordingOrchestration(), secrets=resolver.secret_values)
    client = TestClient(create_app(service, resolver))

    response = client.post(
        f"/runs/{test_approval.RUN_ID}/approve",
        headers={"Authorization": f"Bearer {test_approval.OWNER_TOKEN}", "Idempotency-Key": "approve-format-4"},
        json={"expected_checksum": checksum, "reason": "reviewed format 4 plan"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["run"]["approval"]["plan_checksum"] == checksum
    stored = projection.lookup_run(test_approval.RUN_ID).value
    assert stored is not None
    assert stored.approval is not None
    assert stored.approval.plan_checksum == checksum


@pytest.mark.parametrize("start_after", [None, 0], ids=["apply", "resume"])
def test_a_format_4_plan_is_still_applied_and_resumed(tmp_path: Path, start_after: int | None) -> None:
    directory = _format_4_plan(tmp_path)
    destination = CountingDestination()

    record = engine_over(directory, destination=destination).apply_plan(
        ownership=RecordingOwnership(), config_version=CONFIG_VERSION, start_after=start_after
    )

    expected = 2 if start_after is None else 1
    assert len(destination.applied) == expected
    assert len(record.applied_operations) == expected


# ---------------------------------------------------------------------------------------
# A format 6 plan is refused by every reader (FR-011)
# ---------------------------------------------------------------------------------------


def _future_plan(tmp_path: Path) -> Path:
    """A format 6 plan whose operations file is not even JSON.

    A reader that refuses it with a version message did so before it read any operation.
    """
    directory = run_dir(tmp_path)
    write_artifact(directory, [update_record()], source_snapshot=[], format_version=FUTURE_FORMAT_VERSION)
    operations_path(directory).write_bytes(b"not an operation\n")
    return directory


def test_runs_plan_refuses_a_format_6_plan(tmp_path: Path) -> None:
    _future_plan(tmp_path)

    with pytest.raises(PlanFormatVersionError) as raised:
        read_saved_plan(sync_name=SYNC_NAME, run_id=RUN_ID, base_directory=tmp_path)

    message = str(raised.value)
    assert f"format version {FUTURE_FORMAT_VERSION}" in message
    assert f"Supported plan format versions: {SUPPORTED_TEXT}." in message


def test_verify_refuses_a_format_6_plan(tmp_path: Path) -> None:
    directory = _future_plan(tmp_path)

    failures = verify_plan(artifact=read_plan_artifact_bytes(directory), run_id=RUN_ID, config_version=CONFIG_VERSION)

    assert [failure.check for failure in failures] == ["format_version"]
    [failure] = failures
    assert failure.found == str(FUTURE_FORMAT_VERSION)
    assert failure.expected == f"one of the supported plan format versions: {SUPPORTED_TEXT}"


@pytest.mark.parametrize("start_after", [None, 0], ids=["apply", "resume"])
def test_apply_and_resume_refuse_a_format_6_plan_before_any_write(tmp_path: Path, start_after: int | None) -> None:
    directory = _future_plan(tmp_path)
    ownership = RecordingOwnership()

    with pytest.raises(PlanVerificationError) as raised:
        engine_over(directory, destination=RefusingDestination()).apply_plan(
            ownership=ownership, config_version=CONFIG_VERSION, start_after=start_after
        )

    message = str(raised.value)
    assert f"format_version: expected one of the supported plan format versions: {SUPPORTED_TEXT}" in message
    assert f"found {FUTURE_FORMAT_VERSION}." in message
    assert list(raised.value.checks) == ["format_version"]
    assert ownership.proofs == 0, "No hold was proven, so no operation was dispatched."


def test_a_release_that_reads_up_to_format_4_refuses_a_format_5_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The version gate of an earlier release already names the version it does not read."""
    directory = run_dir(tmp_path)
    write_artifact(directory, [_emptied_update()], source_snapshot=[])
    monkeypatch.setattr(plan_reader, "SUPPORTED_FORMAT_VERSIONS", frozenset({2, 3, 4}))

    with pytest.raises(PlanFormatVersionError) as raised:
        parse(directory)

    message = str(raised.value)
    assert "format version 5" in message
    assert "Supported plan format versions: 2, 3, 4." in message
