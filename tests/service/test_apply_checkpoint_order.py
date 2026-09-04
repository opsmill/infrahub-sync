"""A managed apply rehydrates its plan, then publishes in one fixed order.

The apply stage runs on a worker that never saw the plan. It resolves the plan from the
internal checkpoint and validates the whole archive before anything that could reach a
destination exists. After the write it publishes in the only legal order: final
ownership proof, applied sidecar, confirmed guard release, final checkpoint, product
success. Once dispatch may have started, every failure through product writeback is
ambiguous, and a published checkpoint never stands in for a success that did not land.
"""

from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

pytest.importorskip("prefect")
pytest.importorskip("opsmill_prefect_extras")

from infrahub_sync.configuration import ConfigurationPackage
from infrahub_sync.configuration.runtime import resolve_runtime_instance
from infrahub_sync.plan.checksum import source_snapshot_records
from infrahub_sync.plan.config_version import resolve_config_version
from infrahub_sync.plan.models import PlannedOperation, SourceSnapshotRecord
from infrahub_sync.plan.writer import write_plan_artifact
from infrahub_sync.product_store import PrefectExecutionLink, ProductRun, local_product_projection
from infrahub_sync.product_store.bundle import (
    FINAL_CHECKPOINT_ARTIFACT_ID,
    PLAN_CHECKPOINT_ARTIFACT_ID,
)
from infrahub_sync.runtime_schema import RuntimeModelPlan, RuntimeSideModels
from infrahub_sync.service import flow as service_flow
from infrahub_sync.service.checkpoints import FINAL_RUN_FILE_MEMBER, publish_plan_checkpoint
from infrahub_sync.service.flow import service_sync_run
from tests.configuration.validation_packages import package
from tests.plan.artifact_fixtures import operation_record
from tests.service.execution_fixtures import append_execution

if TYPE_CHECKING:
    from infrahub_sync.product_store import ProductProjection

FLOW_RUN_ID = "b41d5f3c-9a52-4a1b-8c77-1f0e6d2b8a45"
WORKER_ID = "5a9c8d71-3e2b-4f06-9d18-7c4b0a3e5f62"
SCHEMA_FINGERPRINT = "c" * 64
SYNC_NAME = "from-netbox"


@dataclass
class _Harness:
    """One registered apply run whose plan exists only as an internal checkpoint."""

    projection: Any
    binding: tuple[str, int, str]
    run_id: str
    checksum: str
    config_version: str
    events: list[str] = field(default_factory=list)
    dispatched: list[str] = field(default_factory=list)
    sidecar_at_publication: list[dict[str, Any]] = field(default_factory=list)


def _write_plan(run_directory: Path, run_id: str, *, binding: tuple[str, int, str], config_version: str) -> Any:  # noqa: ANN401
    """Write the plan the operator reviewed, with one source snapshot."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    side = run_directory / "A"
    side.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.table({"name": ["prod"], "_extract_ts": ["2026-09-04T12:00:00+00:00"]}), side / "tag.parquet")
    return write_plan_artifact(
        run_dir=run_directory,
        run_id=run_id,
        config_version=config_version,
        source_snapshot=[SourceSnapshotRecord(**record) for record in source_snapshot_records(run_directory)],
        deletes_computed=True,
        operations=[PlannedOperation.model_validate(operation_record(identity={"name": "prod"}))],
        configuration_binding=binding,
        schema_fingerprint=SCHEMA_FINGERPRINT,
    )


class _RecordingProjection:
    """Records publication and commit order, and the sidecar state at each point."""

    def __init__(self, inner: ProductProjection, harness_events: list[str], sidecars: list[dict[str, Any]]) -> None:
        self._inner = inner
        self._events = harness_events
        self._sidecars = sidecars
        self.run_directory: Path | None = None
        self.fail_final_publication = False
        self.fail_success_commit = False

    def publish_artifact(self, run_id: str, **kwargs: Any) -> Any:  # noqa: ANN401
        artifact_id = str(kwargs["artifact_id"])
        if artifact_id == FINAL_CHECKPOINT_ARTIFACT_ID:
            self._events.append("final-checkpoint")
            self._sidecars.append(self._sidecar_state())
            if self.fail_final_publication:
                msg = "object storage refused the final checkpoint"
                raise RuntimeError(msg)
        return self._inner.publish_artifact(run_id, **kwargs)

    def commit_claimed_execution(self, run_id: str, flow_run_id: str, **kwargs: Any) -> bool:  # noqa: ANN401
        outcome = str(kwargs.get("terminal_outcome"))
        self._events.append(f"commit-{outcome}")
        if self.fail_success_commit and outcome == "succeeded":
            msg = "the product store refused the success commit"
            raise RuntimeError(msg)
        return self._inner.commit_claimed_execution(run_id, flow_run_id, **kwargs)

    def _sidecar_state(self) -> dict[str, Any]:
        if self.run_directory is None or not (self.run_directory / "run.json").is_file():
            return {}
        return json.loads((self.run_directory / "run.json").read_text(encoding="utf-8"))

    def __getattr__(self, name: str) -> Any:  # noqa: ANN401
        return getattr(self._inner, name)


class _ReleaseRecordingSession:
    """A granting guard session that records when its release is confirmed."""

    events: list[str] = []  # noqa: RUF012 - rebound per harness below.

    def execute(self, query: str, params: object = None) -> Any:  # noqa: ANN401
        del params
        if "pg_locks" in query:
            return _Row((4242, True))
        if "pg_advisory_unlock" in query:
            self.events.append("release-confirmed")
            return _Row((True, 4242))
        return _Row((None,))

    def close(self) -> None:
        """Close the dedicated session."""


class _Row:
    def __init__(self, row: tuple[object, ...]) -> None:
        self._row = row

    def fetchone(self) -> tuple[object, ...]:
        return self._row


@pytest.fixture
def harness(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> _Harness:
    """A registered apply whose only plan copy is the published plan checkpoint."""
    monkeypatch.setenv("INFRAHUB_API_TOKEN", "apply-destination-canary")
    monkeypatch.setenv("NETBOX_TOKEN", "apply-source-canary")
    monkeypatch.setenv("PREFECT__WORKER_ID", WORKER_ID)
    run_id = "run-apply-from-checkpoint"
    inner = local_product_projection(tmp_path / "product")
    registered = inner.create_configuration(package())
    binding = (registered.config_id, registered.registry_version, registered.package_checksum)
    inner.create_run(
        ProductRun(
            run_id=run_id,
            operation="apply",
            configuration_reference=f"{binding[0]}@{binding[1]}",
            config_id=binding[0],
            registry_version=binding[1],
            package_checksum=binding[2],
            actor="owner",
            started_at=datetime.now(timezone.utc),
            phase="planned",
        )
    )
    append_execution(
        inner,
        run_id,
        PrefectExecutionLink(
            flow_run_id=FLOW_RUN_ID, purpose="apply", attempt=1, submitted_at=datetime.now(timezone.utc)
        ),
    )

    # The plan was computed on a worker that is gone; only its checkpoint survives.
    stored = inner.lookup_configuration_version(binding[0], binding[1]).value
    assert stored is not None
    runtime = resolve_runtime_instance(
        ConfigurationPackage.model_validate(stored.declared_content), directory=str(tmp_path)
    )
    runtime._configuration_binding = binding
    authored = tmp_path / "planning-worker" / runtime.name / run_id
    authored.mkdir(parents=True)
    manifest = _write_plan(authored, run_id, binding=binding, config_version=resolve_config_version(runtime))
    publish_plan_checkpoint(inner, run_id, run_directory=authored, manifest=manifest)

    events: list[str] = []
    sidecars: list[dict[str, Any]] = []
    projection = _RecordingProjection(inner, events, sidecars)
    built = _Harness(
        projection=projection,
        binding=binding,
        run_id=run_id,
        checksum=manifest.plan_checksum,
        config_version=resolve_config_version(runtime),
        events=events,
        sidecar_at_publication=sidecars,
    )

    class _RecordingDestination:
        def __init__(self, **_kwargs: object) -> None:
            events.append("destination-constructed")

        def new_peer_resolver(self) -> object:  # noqa: PLR6301
            """The per-apply resolver factory."""
            return object()

        def apply_planned_operation(self, *, operation: PlannedOperation, peers: Any) -> str:  # noqa: ANN401, PLR6301
            del peers
            events.append("dispatch")
            built.dispatched.append(operation.operation_id)
            return "node-1"

    session = _ReleaseRecordingSession
    session.events = events
    monkeypatch.setattr(service_flow, "service_guard_session", session)
    monkeypatch.setattr(service_flow, "service_guard_secrets", lambda: ())
    monkeypatch.setattr(service_flow, "_runtime", lambda: (str(tmp_path), projection))
    monkeypatch.setattr(service_flow, "_run_logger", lambda: (service_flow.logger, False))
    monkeypatch.setattr(service_flow, "_prefect_flow_run_id", lambda: FLOW_RUN_ID)
    monkeypatch.setattr(service_flow, "_require_current_worker_identity", lambda *_args: None)
    monkeypatch.setattr(
        service_flow,
        "build_runtime_model_plan",
        lambda **_kwargs: RuntimeModelPlan(
            branch="main",
            schema_fingerprint=SCHEMA_FINGERPRINT,
            destination=RuntimeSideModels(adapter_class=_RecordingDestination, models={}),
            source=None,
        ),
    )
    # The stage's own scratch is where the checkpoint lands; record it for the sidecar reads.
    original = service_flow.rehydrate_plan_checkpoint

    def rehydrate(projection_argument: Any, run: str, *, destination: Path) -> Path:  # noqa: ANN401
        events.append("plan-rehydrated")
        projection.run_directory = destination
        return original(projection_argument, run, destination=destination)

    monkeypatch.setattr(service_flow, "rehydrate_plan_checkpoint", rehydrate)
    return built


def _apply(harness: _Harness) -> dict[str, Any]:
    return service_sync_run.fn(
        harness.run_id,
        "apply",
        *harness.binding,
        expected_checksum=harness.checksum,
        confirm_writes=True,
    )


def test_apply_reads_its_plan_from_the_checkpoint_and_writes_the_reviewed_operation(harness: _Harness) -> None:
    """A worker that never saw the plan applies it from the internal checkpoint."""
    result = _apply(harness)

    assert result["outcome"] == "applied"
    assert len(harness.dispatched) == 1


def test_the_plan_is_validated_before_any_destination_exists(harness: _Harness) -> None:
    """Rehydration and validation both complete before the destination is constructed."""
    _apply(harness)

    assert harness.events.index("plan-rehydrated") < harness.events.index("destination-constructed")
    assert harness.events.index("destination-constructed") < harness.events.index("dispatch")


def test_the_publication_order_is_release_then_checkpoint_then_success(harness: _Harness) -> None:
    """Confirmed release, then the final checkpoint, then the product success commit."""
    _apply(harness)

    assert harness.events.index("release-confirmed") < harness.events.index("final-checkpoint")
    assert harness.events.index("final-checkpoint") < harness.events.index("commit-succeeded")
    assert harness.events.index("dispatch") < harness.events.index("release-confirmed")


def test_the_applied_sidecar_is_already_written_when_the_checkpoint_publishes(harness: _Harness) -> None:
    """The engine's local record of what it completed precedes the publication."""
    _apply(harness)

    assert harness.sidecar_at_publication
    assert harness.sidecar_at_publication[0]["status"] == "applied"
    assert harness.sidecar_at_publication[0]["mode"] == "apply"


def test_the_final_checkpoint_holds_only_the_applied_run_file(harness: _Harness) -> None:
    """Membership is the applied sidecar and the codec's manifest, nothing else."""
    _apply(harness)

    stored = harness.projection.lookup_internal_artifact(harness.run_id, FINAL_CHECKPOINT_ARTIFACT_ID)
    assert stored.value is not None
    with zipfile.ZipFile(BytesIO(stored.value)) as archive:
        names = sorted(archive.namelist())
    assert names == ["bundle-manifest.json", FINAL_RUN_FILE_MEMBER]
    assert harness.projection.lookup_artifact(harness.run_id, FINAL_CHECKPOINT_ARTIFACT_ID).value is None


def test_a_final_publication_failure_after_dispatch_is_ambiguous(harness: _Harness) -> None:
    """A write that may have landed cannot be reported as a clean failure."""
    harness.projection.fail_final_publication = True

    with pytest.raises(RuntimeError, match="object storage refused the final checkpoint"):
        _apply(harness)

    assert len(harness.dispatched) == 1
    stored = harness.projection.lookup_run(harness.run_id).value
    assert stored is not None
    assert stored.phase == "apply-interrupted"
    assert stored.outcome == "ambiguous"
    assert stored.reconciliation_required is True


def test_a_success_writeback_failure_after_dispatch_is_ambiguous_despite_the_checkpoint(
    harness: _Harness,
) -> None:
    """A published final checkpoint is evidence, never a substitute for stored success."""
    harness.projection.fail_success_commit = True

    with pytest.raises(RuntimeError, match="the product store refused the success commit"):
        _apply(harness)

    published = harness.projection.lookup_internal_reference(harness.run_id, FINAL_CHECKPOINT_ARTIFACT_ID)
    assert published.value is not None, "the checkpoint published before the commit was attempted"
    stored = harness.projection.lookup_run(harness.run_id).value
    assert stored is not None
    assert stored.phase == "apply-interrupted"
    assert stored.outcome == "ambiguous"
    assert stored.reconciliation_required is True
    assert stored.outcome != "succeeded"


def test_a_missing_plan_checkpoint_refuses_before_the_destination_is_constructed(
    harness: _Harness,
) -> None:
    """With no checkpoint there is nothing to apply, and nothing is contacted."""
    other = "run-apply-without-checkpoint"
    harness.projection.create_run(
        ProductRun(
            run_id=other,
            operation="apply",
            configuration_reference=f"{harness.binding[0]}@{harness.binding[1]}",
            config_id=harness.binding[0],
            registry_version=harness.binding[1],
            package_checksum=harness.binding[2],
            actor="owner",
            started_at=datetime.now(timezone.utc),
            phase="planned",
        )
    )
    append_execution(
        harness.projection,
        other,
        PrefectExecutionLink(
            flow_run_id=FLOW_RUN_ID, purpose="apply", attempt=1, submitted_at=datetime.now(timezone.utc)
        ),
    )

    with pytest.raises(RuntimeError, match=PLAN_CHECKPOINT_ARTIFACT_ID):
        service_sync_run.fn(
            other,
            "apply",
            *harness.binding,
            expected_checksum=harness.checksum,
            confirm_writes=True,
        )

    assert "destination-constructed" not in harness.events
    assert harness.dispatched == []
    stored = harness.projection.lookup_run(other).value
    assert stored is not None
    assert stored.phase == "apply-failed"
    assert stored.outcome == "failed"
    assert stored.reconciliation_required is False


def test_the_apply_stage_ignores_a_shared_cache_holding_the_same_run(
    harness: _Harness, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A stale copy of this run in a configured cache is neither read nor written."""
    stale = tmp_path / "shared-cache" / SYNC_NAME / harness.run_id
    stale.mkdir(parents=True)
    _write_plan(stale, harness.run_id, binding=harness.binding, config_version=harness.config_version)
    before = sorted(path.name for path in stale.iterdir())
    monkeypatch.setenv("INFRAHUB_SYNC_CACHE_DIR", str(tmp_path / "shared-cache"))

    _apply(harness)

    assert sorted(path.name for path in stale.iterdir()) == before
    assert json.loads((stale / "plan" / "manifest.json").read_text(encoding="utf-8"))["run_id"] == harness.run_id
    assert not (stale / "run.json").exists()
