"""A successful managed write leaves the durable source-count baseline behind.

The baseline is the configuration's last known-good source counts, kept in PostgreSQL
alongside the run that produced it. Only a write that actually succeeded sets it. Unit 3
populates it and reads nothing from it: there is no managed row-count refusal, no lookup
before dispatch, no override, and no filesystem authority left anywhere.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("prefect")

from infrahub_sync import potenda as potenda_module
from infrahub_sync.cache import sidecars
from infrahub_sync.plan.models import PlanManifest, SourceSnapshotRecord
from infrahub_sync.product_store import BaselineWriteback
from infrahub_sync.service import flow as service_flow


def test_the_engine_holds_no_filesystem_baseline_or_cadence_authority() -> None:
    """The dead filesystem baseline and cadence path is gone, with no fallback."""
    assert not hasattr(potenda_module.Potenda, "persist_baseline_counts")
    assert not hasattr(potenda_module.Potenda, "check_rowcount_guardrail")
    assert not hasattr(potenda_module.Potenda, "sync_in_tiers")
    assert not hasattr(sidecars, "RowcountsFile")
    assert not hasattr(sidecars, "RunCounterFile")


def test_no_filesystem_baseline_file_name_survives_in_the_engine() -> None:
    """Neither retired sidecar file is written or read by any remaining code."""
    engine_source = Path(potenda_module.__file__ or "").read_text(encoding="utf-8")
    sidecar_source = Path(sidecars.__file__ or "").read_text(encoding="utf-8")
    for name in ("last-successful-rowcounts.json", "run-counter.json"):
        assert name not in engine_source
        assert name not in sidecar_source


def test_the_service_never_looks_up_a_baseline_before_writing() -> None:
    """Unit 3 populates the baseline; nothing in the write path consults it."""
    flow_source = Path(service_flow.__file__ or "").read_text(encoding="utf-8")

    assert "lookup_configuration_baseline" not in flow_source
    assert "rowcount" not in flow_source.lower()


@pytest.mark.parametrize(
    ("snapshot", "expected"),
    [
        pytest.param([], {}, id="no-source-snapshot"),
        pytest.param([("A/tag.parquet", 3)], {"tag": 3}, id="one-resource"),
        pytest.param(
            [("A/device.parquet", 12), ("A/interface.parquet", 48)],
            {"device": 12, "interface": 48},
            id="two-resources",
        ),
    ],
)
def test_the_baseline_records_the_plans_source_counts_as_a_full_extract(
    snapshot: list[tuple[str, int]], expected: dict[str, int]
) -> None:
    """Counts come from the snapshots the plan was computed against, per resource.

    The supported service path always extracts in full, so the cadence fact it records is
    fixed rather than derived from a counter.
    """
    manifest = _manifest(snapshot)

    baseline = service_flow._baseline_writeback(manifest)

    assert isinstance(baseline, BaselineWriteback)
    assert baseline.source_row_counts == expected
    assert baseline.full_extract is True


def _manifest(snapshot: list[tuple[str, int]]) -> PlanManifest:
    return PlanManifest(
        format_version=2,
        run_id="run-baseline",
        created_at="2026-09-04T12:00:00+00:00",
        config_version="configuration-v1",
        source_snapshot=[SourceSnapshotRecord(path=path, digest="a" * 64, row_count=count) for path, count in snapshot],
        operations_count=0,
        delete_operations_computed=True,
        plan_checksum="b" * 64,
    )
