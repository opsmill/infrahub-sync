from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from infrahub_sync.product_store import ProductRun, local_product_projection

# Decimal bytes repeat VAL-8's reported MB/KB figures. Each tuple is
# (artifact id, byte count); baseline/current model the two retained sides.
WORKLOADS = {
    "val8-88k": (
        ("baseline-payload", 3_270_000),
        ("current-payload", 3_270_000),
        ("baseline-index", 8_230_000),
        ("current-index", 8_230_000),
        ("plan", 15_700),
    ),
    "representative-10k": (
        ("baseline-payload", 390_000),
        ("current-payload", 390_000),
        ("baseline-index", 970_000),
        ("current-index", 970_000),
        ("plan", 2_700),
    ),
}


@pytest.mark.parametrize(("name", "artifacts"), list(WORKLOADS.items()))
def test_durable_profile_sizing(name: str, artifacts: tuple[tuple[str, int], ...], tmp_path: Path) -> None:
    root = tmp_path / name
    projection = local_product_projection(root)
    projection.create_run(
        ProductRun(
            run_id=name,
            operation="plan",
            configuration_reference="synthetic-fixed-density-v1",
            started_at=datetime(2026, 8, 8, tzinfo=timezone.utc),
            phase="planning",
        )
    )
    for artifact_id, size in artifacts:
        projection.publish_artifact(
            name,
            artifact_id=artifact_id,
            kind="plan" if artifact_id == "plan" else "snapshot-sidecar",
            media_type="application/vnd.apache.parquet",
            data=b"x" * size,
        )
    projection.finish_run(
        name,
        phase="planned",
        outcome="succeeded",
        summary={"records": 88_117 if name == "val8-88k" else 10_051},
        results={},
    )

    files = [path for path in root.rglob("*") if path.is_file()]
    payload_bytes = sum(size for _, size in artifacts)
    stored_bytes = sum(path.stat().st_size for path in files)
    manifest_bytes = sum(path.stat().st_size for path in files if path.name == "manifest.json")
    print(  # noqa: T201 - this test is also the reproducible sizing command.
        f"{name}: payload_bytes={payload_bytes} manifest_bytes={manifest_bytes} "
        f"relational_bytes={(root / 'product-records.sqlite3').stat().st_size} "
        f"stored_bytes={stored_bytes} files={len(files)}"
    )

    stored = projection.lookup_run(name).value
    assert stored is not None
    assert sum(reference.size for reference in stored.artifact_refs) == payload_bytes
    assert len(stored.artifact_refs) == 5
    assert len(files) == 11  # five data + five manifests + one relational database
    assert stored_bytes > payload_bytes
