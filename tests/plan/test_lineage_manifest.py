"""The `lineage` field of a saved plan's manifest (FR-012, `contracts/plan-last-changes.md`).

The plan records the import account it was computed with, and the source it writes, so an
apply can compare the account it runs with. The field is additive: the format version stays
3, a plan written without it carries no field at all, and the plan checksum covers it.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner

from infrahub_sync.cli import OWNER_SCOPE_NOTICE, app
from infrahub_sync.client import PlanLineageResource, PlanResource, PlanSummaryResource, SyncClient
from infrahub_sync.plan.checksum import compute_plan_checksum
from infrahub_sync.plan.models import (
    PLAN_FORMAT_VERSION,
    LineageAccountRecord,
    LineageNodeRecord,
    PlanLineageRecord,
    PlanManifest,
)
from infrahub_sync.plan.review import read_saved_plan, resolve_run_directory
from infrahub_sync.plan.writer import write_plan_artifact
from tests.plan.artifact_fixtures import RUN_ID, SYNC_NAME, manifest_path

if TYPE_CHECKING:
    from pathlib import Path

LINEAGE = PlanLineageRecord(
    import_account=LineageAccountRecord(name="netbox", id="account-1"),
    source=LineageNodeRecord(kind="CoreAccount", name="netbox"),
    owner=None,
    protect=False,
)

OWNER = LineageNodeRecord(kind="CoreAccountGroup", name="network-team")


def _written_manifest(tmp_path: Path, lineage: PlanLineageRecord | None) -> dict[str, Any]:
    directory = resolve_run_directory(SYNC_NAME, RUN_ID, base_directory=tmp_path)
    write_plan_artifact(
        run_dir=directory,
        run_id=RUN_ID,
        config_version="cfg-version",
        source_snapshot=[],
        deletes_computed=True,
        operations=[],
        lineage=lineage,
    )
    return json.loads(manifest_path(directory).read_bytes())


def test_the_manifest_records_the_import_account_and_the_source(tmp_path: Path) -> None:
    """The shape of `contracts/plan-last-changes.md`: the account's name and id, the source's kind and name."""
    written = _written_manifest(tmp_path, LINEAGE)

    assert written["lineage"]["import_account"] == {"name": "netbox", "id": "account-1"}
    assert written["lineage"]["source"] == {"kind": "CoreAccount", "name": "netbox"}
    assert written["lineage"]["protect"] is False


def test_the_manifest_model_reads_the_recorded_lineage_back(tmp_path: Path) -> None:
    written = _written_manifest(tmp_path, LINEAGE)

    assert PlanManifest.model_validate(written).lineage == LINEAGE


def test_a_named_source_is_recorded_by_kind_and_name(tmp_path: Path) -> None:
    lineage = LINEAGE.model_copy(update={"source": LineageNodeRecord(kind="CoreAccountGroup", name="network-team")})

    written = _written_manifest(tmp_path, lineage)

    assert written["lineage"]["source"] == {"kind": "CoreAccountGroup", "name": "network-team"}
    assert written["lineage"]["import_account"] == {"name": "netbox", "id": "account-1"}


def test_a_plan_written_without_lineage_carries_no_field(tmp_path: Path) -> None:
    """Absent, not `null`: a destination that records no lineage writes the shape older plans carry."""
    written = _written_manifest(tmp_path, None)

    assert "lineage" not in written
    manifest = PlanManifest.model_validate(written)
    assert manifest.lineage is None
    assert "lineage" not in manifest.model_dump()


def test_the_checksum_changes_when_the_lineage_changes(tmp_path: Path) -> None:
    """The field is written before the checksum is computed, so it is covered by it."""
    written = _written_manifest(tmp_path, LINEAGE)
    other_account = dict(written, lineage=dict(written["lineage"], import_account={"name": "other", "id": "account-2"}))
    other_source = dict(written, lineage=dict(written["lineage"], source={"kind": "CoreAccountGroup", "name": "team"}))

    assert compute_plan_checksum(other_account, b"") != written["plan_checksum"]
    assert compute_plan_checksum(other_source, b"") != written["plan_checksum"]


def test_the_format_version_stays_3(tmp_path: Path) -> None:
    written = _written_manifest(tmp_path, LINEAGE)

    assert PLAN_FORMAT_VERSION == 3
    assert written["format_version"] == 3


def test_the_manifest_records_the_named_owner_by_kind_and_name(tmp_path: Path) -> None:
    """FR-009: `lineage.owner` is the owner the package names."""
    written = _written_manifest(tmp_path, LINEAGE.model_copy(update={"owner": OWNER}))

    assert written["lineage"]["owner"] == {"kind": "CoreAccountGroup", "name": "network-team"}
    assert PlanManifest.model_validate(written).lineage == LINEAGE.model_copy(update={"owner": OWNER})


def test_the_manifest_records_no_owner_as_null(tmp_path: Path) -> None:
    """FR-009: with no owner named, `lineage.owner` is present and `null`."""
    written = _written_manifest(tmp_path, LINEAGE)

    assert "owner" in written["lineage"]
    assert written["lineage"]["owner"] is None


def _plan_resource(lineage: dict[str, Any] | None) -> PlanResource:
    return PlanResource(
        run_id=RUN_ID,
        checksum="a" * 64,
        checksum_ok=True,
        verification_notes=(),
        summary=PlanSummaryResource(
            by_action={}, by_kind={}, total=0, delete_operations_computed=True, deletes_not_executed=0
        ),
        operations=(),
        lineage=None if lineage is None else PlanLineageResource.model_validate(lineage),
    )


@pytest.mark.parametrize(
    ("owner", "notice_shown"),
    [
        pytest.param(OWNER.model_dump(), True, id="owner-named"),
        pytest.param(None, False, id="no-owner"),
    ],
)
def test_the_plan_summary_shows_the_owner_notice_exactly_when_an_owner_is_named(
    owner: dict[str, str] | None, *, notice_shown: bool
) -> None:
    """FR-009, D-8: `runs plan` says the owner setting covers only values the run creates."""
    client = MagicMock(spec=SyncClient)
    client.get_plan.return_value = _plan_resource(LINEAGE.model_dump(mode="json") | {"owner": owner})

    result = CliRunner().invoke(app, ["runs", "plan", RUN_ID], obj={"client": client})

    assert result.exit_code == 0, result.output
    assert OWNER_SCOPE_NOTICE == "The owner setting applies only to values this run creates."
    assert (OWNER_SCOPE_NOTICE in result.output) is notice_shown, result.output


def test_a_plan_with_no_lineage_shows_no_owner_notice() -> None:
    """A plan whose destination records no lineage has no owner, so no notice."""
    client = MagicMock(spec=SyncClient)
    client.get_plan.return_value = _plan_resource(None)

    result = CliRunner().invoke(app, ["runs", "plan", RUN_ID], obj={"client": client})

    assert result.exit_code == 0, result.output
    assert OWNER_SCOPE_NOTICE not in result.output


@pytest.mark.parametrize("owner", [OWNER, None], ids=["owner-named", "no-owner"])
def test_the_published_plan_review_carries_the_recorded_lineage(
    tmp_path: Path, owner: LineageNodeRecord | None
) -> None:
    """The service fills `PlanResource.lineage` from the manifest, so `runs plan` reads `lineage.owner`."""
    pytest.importorskip("prefect")
    pytest.importorskip("opsmill_prefect_extras")
    from infrahub_sync.service import flow as service_flow

    lineage = LINEAGE.model_copy(update={"owner": owner})
    _written_manifest(tmp_path, lineage)
    saved = read_saved_plan(sync_name=SYNC_NAME, run_id=RUN_ID, base_directory=tmp_path)

    document = service_flow._review_document(RUN_ID, saved)

    assert document.lineage is not None
    assert document.lineage.model_dump(mode="json") == lineage.model_dump(mode="json")
    assert json.loads(document.model_dump_json())["lineage"]["owner"] == (None if owner is None else owner.model_dump())


def test_the_published_plan_review_has_no_lineage_when_the_plan_records_none(tmp_path: Path) -> None:
    pytest.importorskip("prefect")
    pytest.importorskip("opsmill_prefect_extras")
    from infrahub_sync.service import flow as service_flow

    _written_manifest(tmp_path, None)
    saved = read_saved_plan(sync_name=SYNC_NAME, run_id=RUN_ID, base_directory=tmp_path)

    assert service_flow._review_document(RUN_ID, saved).lineage is None
