"""Optional managed-to-standalone DB-003 record/artifact equivalence proof."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import cast

import pytest

pytest.importorskip("prefect")
pytest.importorskip("opsmill_prefect_extras")

from infrahub_sync import SyncAdapter, SyncInstance
from infrahub_sync.managed import flow as managed_flow
from infrahub_sync.plan.config_version import resolve_config_version
from infrahub_sync.plan.models import PlanManifest
from infrahub_sync.plan.review import SavedPlan
from infrahub_sync.product_store import ProductRun, local_product_projection
from infrahub_sync.product_store.standalone import execute_standalone


def test_managed_and_standalone_plan_product_projection_seams_match(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    run_id = "run-cross-interface-plan"
    instance = SyncInstance(
        name="inventory",
        directory=str(tmp_path),
        source=SyncAdapter(name="source", settings={}),
        destination=SyncAdapter(name="destination", settings={}),
    )
    configuration_reference = resolve_config_version(instance)
    saved = SavedPlan(
        manifest=PlanManifest(
            format_version=2,
            run_id=run_id,
            created_at="2026-08-10T12:00:00+00:00",
            config_version=configuration_reference,
            source_snapshot=[],
            operations_count=0,
            delete_operations_computed=True,
            plan_checksum="a" * 64,
        ),
        operations=[],
        checksum_ok=True,
        verification_notes=[],
    )
    standalone_cache = (tmp_path / "standalone").resolve()
    managed_cache = (tmp_path / "managed").resolve()
    managed_projection = local_product_projection(managed_cache)
    managed_projection.create_run(
        ProductRun(
            run_id=run_id,
            operation="plan",
            configuration_reference=configuration_reference,
            started_at=datetime.now(timezone.utc),
            phase="accepted",
            summary={"sync_name": "inventory"},
        )
    )

    monkeypatch.setattr("infrahub_sync.product_store.standalone.execute_run", lambda *_args, **_kwargs: saved)
    execute_standalone(
        instance,
        operation="plan",
        run_id=run_id,
        product_cache_location=standalone_cache,
        _return_saved_plan=True,
    )

    monkeypatch.setattr(managed_flow, "_runtime", lambda: (str(tmp_path), managed_projection))
    monkeypatch.setattr(managed_flow, "_run_logger", lambda: (logging.getLogger("test-conformance"), False))
    monkeypatch.setattr(managed_flow, "resolve_sync_instance", lambda *_args, **_kwargs: instance)
    monkeypatch.setattr(managed_flow, "collect_secret_values", lambda _instance=None: ())
    monkeypatch.setattr(managed_flow, "_plan", lambda *_args, **_kwargs: saved)
    managed_flow.managed_sync_run.fn(run_id, "inventory", "plan", configuration_reference)

    standalone_projection = local_product_projection(standalone_cache)
    standalone_record = standalone_projection.lookup_run(run_id).value
    managed_record = managed_projection.lookup_run(run_id).value
    standalone_artifact = standalone_projection.lookup_artifact(run_id, "plan-review").value
    managed_artifact = managed_projection.lookup_artifact(run_id, "plan-review").value
    assert standalone_record is not None
    assert managed_record is not None
    assert standalone_artifact is not None
    assert managed_artifact is not None

    def stable_product_record(record: ProductRun) -> dict[str, object]:
        data = record.model_dump(mode="json")
        data["started_at"] = "<generated>"
        data["finished_at"] = "<generated>"
        references = cast("list[dict[str, object]]", data["artifact_refs"])
        for reference in references:
            reference["created_at"] = "<generated>"
        return data

    assert stable_product_record(standalone_record) == stable_product_record(managed_record)
    assert standalone_artifact == managed_artifact
