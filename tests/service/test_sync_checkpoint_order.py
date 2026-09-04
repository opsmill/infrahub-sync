"""A managed sync publishes its plan before it writes, and its result after release.

Sync plans and applies inside one guard hold. The plan it generated has to be durable
before the first destination operation, so a sync that is interrupted mid-write leaves
the plan an operator reconciles against. The final checkpoint follows confirmed release,
and after the first dispatch every failure through product writeback is ambiguous.
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
from infrahub_sync.execution import RunResult
from infrahub_sync.plan.checksum import source_snapshot_records
from infrahub_sync.plan.config_version import resolve_config_version
from infrahub_sync.plan.models import PlannedOperation, SourceSnapshotRecord
from infrahub_sync.plan.review import SavedPlan, read_saved_plan
from infrahub_sync.plan.writer import write_plan_artifact
from infrahub_sync.product_store import PrefectExecutionLink, ProductRun, local_product_projection
from infrahub_sync.product_store.bundle import (
    FINAL_CHECKPOINT_ARTIFACT_ID,
    PLAN_CHECKPOINT_ARTIFACT_ID,
)
from infrahub_sync.runtime_schema import RuntimeModelPlan, RuntimeSideModels
from infrahub_sync.service import flow as service_flow
from infrahub_sync.service.checkpoints import FINAL_RUN_FILE_MEMBER
from infrahub_sync.service.flow import service_sync_run
from infrahub_sync.service.service import PLAN_ARTIFACT_ID
from tests.configuration.validation_packages import package
from tests.plan.artifact_fixtures import operation_record
from tests.service.execution_fixtures import append_execution, stage_root, write_applied_sidecar

if TYPE_CHECKING:
    from infrahub_sync.product_store import ProductProjection

FLOW_RUN_ID = "c73e1a4d-8b26-4f19-9e35-0a2c7d41b8f6"
WORKER_ID = "9e1f2a08-6c74-4b3d-8a51-2f7e0c9b46d3"
SCHEMA_FINGERPRINT = "e" * 64
RUN_ID = "run-managed-sync"


@dataclass
class _SyncStage:
    """One registered sync run driven through the production stage boundary."""

    projection: Any
    binding: tuple[str, int, str]
    events: list[str] = field(default_factory=list)
    sidecar_at_publication: list[dict[str, Any]] = field(default_factory=list)


class _RecordingProjection:
    """Records publication and commit order, and can refuse either."""

    def __init__(self, inner: ProductProjection, events: list[str], sidecars: list[dict[str, Any]]) -> None:
        self._inner = inner
        self._events = events
        self._sidecars = sidecars
        self.run_directory: Path | None = None
        self.fail_final_publication = False

    def publish_artifact(self, run_id: str, **kwargs: Any) -> Any:  # noqa: ANN401
        artifact_id = str(kwargs["artifact_id"])
        self._events.append(f"publish:{artifact_id}")
        if artifact_id == FINAL_CHECKPOINT_ARTIFACT_ID:
            self._sidecars.append(self._sidecar_state())
            if self.fail_final_publication:
                msg = "object storage refused the final checkpoint"
                raise RuntimeError(msg)
        return self._inner.publish_artifact(run_id, **kwargs)

    def commit_claimed_execution(self, run_id: str, flow_run_id: str, **kwargs: Any) -> bool:  # noqa: ANN401
        self._events.append(f"commit:{kwargs.get('terminal_outcome')}")
        return self._inner.commit_claimed_execution(run_id, flow_run_id, **kwargs)

    def _sidecar_state(self) -> dict[str, Any]:
        if self.run_directory is None or not (self.run_directory / "run.json").is_file():
            return {}
        return json.loads((self.run_directory / "run.json").read_text(encoding="utf-8"))

    def __getattr__(self, name: str) -> Any:  # noqa: ANN401
        return getattr(self._inner, name)


class _ReleaseRecordingSession:
    """A granting guard session that records the confirmed release."""

    events: list[str] = []  # noqa: RUF012 - rebound per harness.

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
def stage(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> _SyncStage:
    """A registered sync whose plan is generated and applied under one guard hold."""
    monkeypatch.setenv("INFRAHUB_API_TOKEN", "sync-destination-canary")
    monkeypatch.setenv("NETBOX_TOKEN", "sync-source-canary")
    monkeypatch.setenv("PREFECT__WORKER_ID", WORKER_ID)
    inner = local_product_projection(tmp_path / "product")
    registered = inner.create_configuration(package())
    binding = (registered.config_id, registered.registry_version, registered.package_checksum)
    inner.create_run(
        ProductRun(
            run_id=RUN_ID,
            operation="sync",
            configuration_reference=f"{binding[0]}@{binding[1]}",
            config_id=binding[0],
            registry_version=binding[1],
            package_checksum=binding[2],
            actor="owner",
            started_at=datetime.now(timezone.utc),
            phase="accepted",
        )
    )
    append_execution(
        inner,
        RUN_ID,
        PrefectExecutionLink(
            flow_run_id=FLOW_RUN_ID, purpose="sync", attempt=1, submitted_at=datetime.now(timezone.utc)
        ),
    )
    events: list[str] = []
    sidecars: list[dict[str, Any]] = []
    projection = _RecordingProjection(inner, events, sidecars)
    stored = inner.lookup_configuration_version(binding[0], binding[1]).value
    assert stored is not None
    runtime = resolve_runtime_instance(
        ConfigurationPackage.model_validate(stored.declared_content), directory=str(tmp_path)
    )
    runtime._configuration_binding = binding

    def plan(_instance: Any, **kwargs: Any) -> SavedPlan:  # noqa: ANN401
        """Extract and plan, writing the real artifact into this stage's scratch."""
        events.append("plan")
        base = stage_root(kwargs)
        directory = base / runtime.name / RUN_ID
        directory.mkdir(parents=True, exist_ok=True)
        projection.run_directory = directory
        _write_plan(directory, binding=binding, config_version=resolve_config_version(runtime))
        return read_saved_plan(
            sync_name=runtime.name,
            run_id=RUN_ID,
            config=runtime,
            base_directory=base,
        )

    def execute_run(_instance: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        operation = kwargs.get("operation")
        base = stage_root(kwargs)
        if operation == "verify":
            events.append("verify")
            return read_saved_plan(sync_name=runtime.name, run_id=RUN_ID, config=runtime, base_directory=base)
        # The engine proves its hold and marks the dispatch through this boundary.
        ownership = kwargs["ownership"]
        ownership.before_operation()
        events.append("dispatch")
        ownership.after_final_operation()
        write_applied_sidecar(base / runtime.name / RUN_ID, mode="sync")
        return RunResult(
            sync_name=runtime.name,
            operation="apply",
            run_id=RUN_ID,
            status="no-change",
            changed=False,
            summary={"create": 0, "update": 0, "delete": 0},
            artifact_path=str(base / runtime.name / RUN_ID),
        )

    session = _ReleaseRecordingSession
    session.events = events
    monkeypatch.setattr(service_flow, "service_guard_session", session)
    monkeypatch.setattr(service_flow, "service_guard_secrets", lambda: ())
    monkeypatch.setattr(service_flow, "_runtime", lambda: (str(tmp_path), projection))
    monkeypatch.setattr(service_flow, "_run_logger", lambda: (service_flow.logger, False))
    monkeypatch.setattr(service_flow, "_prefect_flow_run_id", lambda: FLOW_RUN_ID)
    monkeypatch.setattr(service_flow, "_require_current_worker_identity", lambda *_args: None)
    monkeypatch.setattr(service_flow, "_plan", plan)
    monkeypatch.setattr(service_flow, "execute_run", execute_run)
    monkeypatch.setattr(
        service_flow,
        "build_runtime_model_plan",
        lambda **_kwargs: RuntimeModelPlan(
            branch="main",
            schema_fingerprint=SCHEMA_FINGERPRINT,
            destination=RuntimeSideModels(adapter_class=object, models={}),
            source=None,
        ),
    )
    return _SyncStage(projection=projection, binding=binding, events=events, sidecar_at_publication=sidecars)


def _write_plan(run_directory: Path, *, binding: tuple[str, int, str], config_version: str) -> Any:  # noqa: ANN401
    import pyarrow as pa
    import pyarrow.parquet as pq

    side = run_directory / "A"
    side.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.table({"name": ["prod"], "_extract_ts": ["2026-09-04T12:00:00+00:00"]}), side / "tag.parquet")
    return write_plan_artifact(
        run_dir=run_directory,
        run_id=RUN_ID,
        config_version=config_version,
        source_snapshot=[SourceSnapshotRecord(**record) for record in source_snapshot_records(run_directory)],
        deletes_computed=True,
        operations=[PlannedOperation.model_validate(operation_record(identity={"name": "prod"}))],
        configuration_binding=binding,
        schema_fingerprint=SCHEMA_FINGERPRINT,
    )


def _sync(stage: _SyncStage) -> dict[str, Any]:
    return service_sync_run.fn(RUN_ID, "sync", *stage.binding, confirm_writes=True)


def test_sync_publishes_its_plan_checkpoint_before_the_first_dispatch(stage: _SyncStage) -> None:
    """The plan a sync generated is durable before it writes anything to a destination."""
    _sync(stage)

    checkpoint = stage.events.index(f"publish:{PLAN_CHECKPOINT_ARTIFACT_ID}")
    assert checkpoint < stage.events.index("dispatch")
    assert stage.events.index("plan") < checkpoint


def test_sync_publishes_the_internal_checkpoint_before_the_public_review(stage: _SyncStage) -> None:
    """Internal handoff first, exactly as the plan stage orders it."""
    _sync(stage)

    assert stage.events.index(f"publish:{PLAN_CHECKPOINT_ARTIFACT_ID}") < stage.events.index(
        f"publish:{PLAN_ARTIFACT_ID}"
    )


def test_sync_publishes_the_final_checkpoint_after_release_and_before_success(stage: _SyncStage) -> None:
    """Confirmed release, then the final checkpoint, then the product success commit."""
    _sync(stage)

    release = stage.events.index("release-confirmed")
    final = stage.events.index(f"publish:{FINAL_CHECKPOINT_ARTIFACT_ID}")
    assert stage.events.index("dispatch") < release
    assert release < final
    assert final < stage.events.index("commit:succeeded")


def test_the_sync_final_checkpoint_holds_only_the_applied_run_file(stage: _SyncStage) -> None:
    """Membership does not change with the stage that publishes it."""
    _sync(stage)

    stored = stage.projection.lookup_internal_artifact(RUN_ID, FINAL_CHECKPOINT_ARTIFACT_ID)
    assert stored.value is not None
    with zipfile.ZipFile(BytesIO(stored.value)) as archive:
        assert sorted(archive.namelist()) == ["bundle-manifest.json", FINAL_RUN_FILE_MEMBER]
    assert stage.sidecar_at_publication[0]["status"] == "applied"
    assert stage.sidecar_at_publication[0]["mode"] == "sync"


def test_a_sync_final_publication_failure_after_dispatch_is_ambiguous(stage: _SyncStage) -> None:
    """Sync classifies a post-dispatch publication failure exactly as apply does."""
    stage.projection.fail_final_publication = True

    with pytest.raises(RuntimeError, match="object storage refused the final checkpoint"):
        _sync(stage)

    stored = stage.projection.lookup_run(RUN_ID).value
    assert stored is not None
    assert stored.phase == "sync-interrupted"
    assert stored.outcome == "ambiguous"
    assert stored.reconciliation_required is True


def test_sync_works_in_a_stage_root_it_does_not_outlive(
    stage: _SyncStage, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A managed sync plans and writes inside a root released with the stage.

    This harness doubles the engine, so it can see where the stage *put* its run and that
    the root is gone afterwards, but not what the engine itself touched. Whether the real
    engine -- its pipeline lock included -- stays inside that root is
    `tests/service/test_stage_scratch.py::test_the_managed_sync_planning_leg_touches_neither_canary`,
    which drives the real `execute_run`.
    """
    shared = tmp_path / "shared-cache"
    shared.mkdir()
    monkeypatch.setenv("INFRAHUB_SYNC_CACHE_DIR", str(shared))

    _sync(stage)

    assert stage.projection.run_directory is not None
    assert shared not in stage.projection.run_directory.parents
    assert not stage.projection.run_directory.exists()


def test_a_successful_sync_replaces_the_configuration_baseline(stage: _SyncStage) -> None:
    """A managed sync writes the same durable reference an apply does."""
    _sync(stage)

    baseline = stage.projection.lookup_configuration_baseline(stage.binding[0]).value

    assert baseline is not None
    assert baseline.source_row_counts == {"tag": 1}
    assert baseline.runs_since_full_extract == 0


def test_an_ambiguous_sync_advances_no_baseline(stage: _SyncStage) -> None:
    """Ambiguity leaves the configuration's previous reference untouched."""
    stage.projection.fail_final_publication = True

    with pytest.raises(RuntimeError, match="object storage refused the final checkpoint"):
        _sync(stage)

    assert stage.projection.lookup_configuration_baseline(stage.binding[0]).value is None
