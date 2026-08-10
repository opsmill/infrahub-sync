"""Optional managed-to-standalone DB-003 record/artifact equivalence proof."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

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
from tests.conformance.oracle import CanonicalEnvelope, Surface, assert_equivalent


def test_managed_and_standalone_plan_records_and_artifacts_are_canonically_equal(
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

    def envelope(surface: Surface, record: ProductRun, artifact: bytes) -> CanonicalEnvelope:
        return CanonicalEnvelope(
            surface=surface,
            operation="plan",
            plan_fingerprint=saved.manifest.plan_checksum,
            counts={"create": 0, "update": 0, "delete": 0},
            outcome="no-change",
            destination_effects={"created": 0, "updated": 0, "deleted": 0},
            product_record=record.model_dump(mode="json"),
            result=record.results,
            artifact_references=[item.model_dump(mode="json") for item in record.artifact_refs],
            artifact_semantics=json.loads(artifact),
        )

    assert_equivalent(
        [
            envelope("python", standalone_record, standalone_artifact),
            envelope("managed", managed_record, managed_artifact),
        ]
    )
