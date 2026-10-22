"""The `lineage` field of a saved plan's manifest (FR-012, `contracts/plan-last-changes.md`).

The plan records the import account it was computed with, and the source it writes, so an
apply can compare the account it runs with. The field is additive: the format version stays
3, a plan written without it carries no field at all, and the plan checksum covers it.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from infrahub_sync.plan.checksum import compute_plan_checksum
from infrahub_sync.plan.models import (
    PLAN_FORMAT_VERSION,
    LineageAccountRecord,
    LineageNodeRecord,
    PlanLineageRecord,
    PlanManifest,
)
from infrahub_sync.plan.writer import write_plan_artifact
from tests.plan.artifact_fixtures import RUN_ID, manifest_path

if TYPE_CHECKING:
    from pathlib import Path

LINEAGE = PlanLineageRecord(
    import_account=LineageAccountRecord(name="netbox", id="account-1"),
    source=LineageNodeRecord(kind="CoreAccount", name="netbox"),
    owner=None,
    protect=False,
)


def _written_manifest(tmp_path: Path, lineage: PlanLineageRecord | None) -> dict[str, Any]:
    directory = tmp_path / RUN_ID
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
