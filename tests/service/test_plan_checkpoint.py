"""Plan hands verify its work through the internal checkpoint, in a fixed order.

The plan checkpoint is durable state a later stage on another worker consumes. It is
published before the run's plan becomes publicly reviewable, and a stage that consumes
it validates the whole archive before it constructs anything that could talk to a
destination.
"""

from __future__ import annotations

import zipfile
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from uuid import NAMESPACE_URL, uuid5

import pytest

pytest.importorskip("prefect")

from infrahub_sync.plan.checksum import source_snapshot_records
from infrahub_sync.plan.models import PlannedOperation, SourceSnapshotRecord
from infrahub_sync.plan.review import SavedPlan
from infrahub_sync.plan.writer import write_plan_artifact
from infrahub_sync.product_store import PrefectExecutionLink, ProductRun, local_product_projection
from infrahub_sync.product_store.bundle import (
    MAX_BUNDLE_BYTES,
    PLAN_CHECKPOINT_ARTIFACT_ID,
    BundleFormatError,
)
from infrahub_sync.service import flow as service_flow
from infrahub_sync.service.checkpoints import CheckpointUnavailableError, rehydrate_plan_checkpoint
from infrahub_sync.service.flow import service_sync_run
from infrahub_sync.service.scratch import stage_scratch
from infrahub_sync.service.service import PLAN_ARTIFACT_ID
from tests.configuration.validation_packages import package
from tests.plan.artifact_fixtures import operation_record
from tests.service.execution_fixtures import append_execution, stage_root

if TYPE_CHECKING:
    from infrahub_sync.product_store import ProductProjection

WORKER_ID = "7c2f4b90-4c1e-4a58-8b6a-2d9e1f0c7b34"
SYNC_NAME = "inventory"


def _product_run(cache: Path, run_id: str, *, operation: str = "plan", phase: str = "accepted") -> Any:  # noqa: ANN401
    projection = local_product_projection(cache)
    version = projection.create_configuration(package())
    projection.create_run(
        ProductRun(
            run_id=run_id,
            operation=operation,  # ty: ignore[invalid-argument-type]
            configuration_reference=f"{version.config_id}@{version.registry_version}",
            config_id=version.config_id,
            registry_version=version.registry_version,
            package_checksum=version.package_checksum,
            actor="owner",
            started_at=datetime.now(timezone.utc),
            phase=phase,
            summary={"sync_name": SYNC_NAME},
        )
    )
    return projection


def _binding(projection: Any, run_id: str) -> tuple[str, int, str]:  # noqa: ANN401
    run = projection.lookup_run(run_id).value
    assert run is not None
    assert run.configuration_binding is not None
    return run.configuration_binding


@pytest.fixture
def claimed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give the flow one real durable claim without a Prefect runtime."""

    def claim(projection: Any, run_id: str) -> tuple[str, str]:  # noqa: ANN401
        flow_run_id = str(uuid5(NAMESPACE_URL, run_id))
        run = projection.lookup_run(run_id).value
        if run is not None and not any(link.flow_run_id == flow_run_id for link in run.prefect_executions):
            append_execution(
                projection,
                run_id,
                PrefectExecutionLink(flow_run_id=flow_run_id, purpose="test", attempt=1),
            )
            assert projection.claim_execution(run_id, flow_run_id, worker_id=WORKER_ID)
        return flow_run_id, WORKER_ID

    monkeypatch.setattr(service_flow, "_claim_current_execution", claim)


def _instance() -> SimpleNamespace:
    return SimpleNamespace(
        name=SYNC_NAME,
        source=SimpleNamespace(settings={}),
        destination=SimpleNamespace(settings={}),
        store=None,
        _configuration_binding=None,
        _runtime_models=None,
    )


def _write_plan(run_directory: Path, run_id: str, *, snapshots: int = 1) -> Any:  # noqa: ANN401
    """Write one real plan artifact, with source snapshots, into `run_directory`."""
    side = run_directory / "A"
    side.mkdir(parents=True, exist_ok=True)
    for index in range(snapshots):
        _write_parquet(side / f"resource{index}.parquet")
    return write_plan_artifact(
        run_dir=run_directory,
        run_id=run_id,
        config_version="configuration-v1",
        source_snapshot=[SourceSnapshotRecord(**record) for record in source_snapshot_records(run_directory)],
        deletes_computed=True,
        operations=[PlannedOperation.model_validate(operation_record(identity={"name": "prod"}))],
    )


def _write_parquet(path: Path) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    pq.write_table(pa.table({"name": ["prod"], "_extract_ts": ["2026-09-04T12:00:00+00:00"]}), path)


@pytest.fixture
def planning_stage(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    """Make the plan stage write a real plan artifact into its own scratch."""
    directories: list[Path] = []

    def execute_run(instance: Any, **kwargs: Any) -> SavedPlan:  # noqa: ANN401
        run_id = str(kwargs["run_id"])
        from infrahub_sync.cache.paths import run_dir

        directory = run_dir(instance.name, run_id, base_directory=stage_root(kwargs))
        directory.mkdir(parents=True, exist_ok=True)
        manifest = _write_plan(directory, run_id)
        directories.append(directory)
        return SavedPlan(manifest=manifest, operations=[], checksum_ok=True, verification_notes=[])

    monkeypatch.setattr(service_flow, "execute_run", execute_run)
    monkeypatch.setattr(service_flow, "resolve_runtime_instance", lambda *_a, **_k: _instance())
    monkeypatch.setattr(service_flow, "build_runtime_model_plan", lambda *_a, **_k: None)
    monkeypatch.setattr(service_flow, "_run_logger", lambda: (service_flow.logger, False))
    monkeypatch.setattr(service_flow, "resolve_config_version", lambda _instance: "configuration-v1")
    return directories


class _OrderedProjection:
    """A projection that records the order and visibility of every publication."""

    def __init__(self, inner: ProductProjection) -> None:
        self._inner = inner
        self.publications: list[tuple[str, str]] = []

    def publish_artifact(self, run_id: str, **kwargs: Any) -> Any:  # noqa: ANN401
        self.publications.append((str(kwargs["artifact_id"]), str(kwargs.get("visibility", "public"))))
        return self._inner.publish_artifact(run_id, **kwargs)

    def __getattr__(self, name: str) -> Any:  # noqa: ANN401
        return getattr(self._inner, name)


@pytest.mark.usefixtures("claimed")
def test_the_plan_checkpoint_publishes_before_the_public_review(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, planning_stage: list[Path]
) -> None:
    """A run becomes publicly reviewable only after its internal handoff is durable."""
    run_id = "run-plan-checkpoint-order"
    projection = _OrderedProjection(_product_run(tmp_path / "product", run_id))
    monkeypatch.setattr(service_flow, "_runtime", lambda: (str(tmp_path), projection))

    service_sync_run.fn(run_id, "plan", *_binding(projection, run_id))

    assert projection.publications == [
        (PLAN_CHECKPOINT_ARTIFACT_ID, "internal"),
        (PLAN_ARTIFACT_ID, "public"),
    ]
    assert planning_stage


@pytest.mark.usefixtures("claimed", "planning_stage")
def test_the_plan_checkpoint_carries_the_plan_and_its_declared_source_snapshots(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Membership is the plan files plus every snapshot the manifest accounts for."""
    run_id = "run-plan-checkpoint-members"
    projection = _product_run(tmp_path / "product", run_id)
    monkeypatch.setattr(service_flow, "_runtime", lambda: (str(tmp_path), projection))

    service_sync_run.fn(run_id, "plan", *_binding(projection, run_id))

    stored = projection.lookup_internal_artifact(run_id, PLAN_CHECKPOINT_ARTIFACT_ID)
    assert stored.value is not None
    with zipfile.ZipFile(BytesIO(stored.value)) as archive:
        names = sorted(name for name in archive.namelist() if name != "bundle-manifest.json")
    assert names == ["A/resource0.parquet", "plan/manifest.json", "plan/operations.jsonl"]
    reference = projection.lookup_internal_reference(run_id, PLAN_CHECKPOINT_ARTIFACT_ID).value
    assert reference is not None
    assert reference.size == len(stored.value)
    assert projection.lookup_artifact(run_id, PLAN_CHECKPOINT_ARTIFACT_ID).value is None


@pytest.mark.usefixtures("claimed")
def test_a_failed_checkpoint_publication_leaves_no_review_and_no_planned_result(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, planning_stage: list[Path]
) -> None:
    """The public review artifact and the planned verdict both depend on the handoff."""
    run_id = "run-plan-checkpoint-failure"
    projection = _product_run(tmp_path / "product", run_id)
    monkeypatch.setattr(service_flow, "_runtime", lambda: (str(tmp_path), projection))

    def refuse(*_args: object, **kwargs: object) -> None:
        if kwargs.get("visibility") == "internal":
            msg = "object storage refused the checkpoint"
            raise RuntimeError(msg)
        published_public = "the public review artifact was published without a durable checkpoint"
        raise AssertionError(published_public)

    monkeypatch.setattr(projection, "publish_artifact", refuse)

    with pytest.raises(RuntimeError, match="object storage refused the checkpoint"):
        service_sync_run.fn(run_id, "plan", *_binding(projection, run_id))

    stored = projection.lookup_run(run_id).value
    assert stored is not None
    assert stored.phase == "plan-failed"
    assert stored.outcome == "failed"
    assert projection.lookup_artifact(run_id, PLAN_ARTIFACT_ID).value is None
    assert planning_stage


def _published_plan_checkpoint(projection: Any, run_id: str, tmp_path: Path) -> bytes:  # noqa: ANN401
    """Publish one valid plan checkpoint and return its exact bytes."""
    from infrahub_sync.service.checkpoints import publish_plan_checkpoint

    directory = tmp_path / "authored" / SYNC_NAME / run_id
    directory.mkdir(parents=True)
    manifest = _write_plan(directory, run_id)
    publish_plan_checkpoint(projection, run_id, run_directory=directory, manifest=manifest)
    stored = projection.lookup_internal_artifact(run_id, PLAN_CHECKPOINT_ARTIFACT_ID)
    assert stored.value is not None
    return stored.value


def test_rehydration_places_the_whole_plan_or_nothing(tmp_path: Path) -> None:
    """A valid checkpoint extracts completely into a directory that did not exist."""
    run_id = "run-rehydrate-valid"
    projection = _product_run(tmp_path / "product", run_id)
    _published_plan_checkpoint(projection, run_id, tmp_path)

    with stage_scratch("verify") as scratch:
        destination = scratch.run_directory(SYNC_NAME, run_id)
        assert not destination.exists()

        rehydrate_plan_checkpoint(projection, run_id, destination=destination)

        assert (destination / "plan" / "manifest.json").is_file()
        assert (destination / "plan" / "operations.jsonl").is_file()
        assert (destination / "A" / "resource0.parquet").is_file()


def test_rehydration_refuses_a_checkpoint_that_was_never_published(tmp_path: Path) -> None:
    """A missing checkpoint is a named refusal, not an empty directory."""
    run_id = "run-rehydrate-missing"
    projection = _product_run(tmp_path / "product", run_id)

    with stage_scratch("verify") as scratch:
        destination = scratch.run_directory(SYNC_NAME, run_id)
        with pytest.raises(CheckpointUnavailableError) as failure:
            rehydrate_plan_checkpoint(projection, run_id, destination=destination)

        assert failure.value.artifact_id == PLAN_CHECKPOINT_ARTIFACT_ID
        assert not destination.exists()


def test_rehydration_refuses_another_runs_checkpoint(tmp_path: Path) -> None:
    """A checkpoint is resolved on the run that owns it, never on a neighbour."""
    owner = "run-rehydrate-owner"
    other = "run-rehydrate-other"
    projection = _product_run(tmp_path / "product", owner)
    version = projection.lookup_run(owner).value
    assert version is not None
    projection.create_run(
        ProductRun(
            run_id=other,
            operation="verify",
            configuration_reference=version.configuration_reference,
            config_id=version.config_id,
            registry_version=version.registry_version,
            package_checksum=version.package_checksum,
            actor="owner",
            started_at=datetime.now(timezone.utc),
            phase="accepted",
        )
    )
    _published_plan_checkpoint(projection, owner, tmp_path)

    with stage_scratch("verify") as scratch:
        destination = scratch.run_directory(SYNC_NAME, other)
        with pytest.raises(CheckpointUnavailableError):
            rehydrate_plan_checkpoint(projection, other, destination=destination)

        assert not destination.exists()


@pytest.mark.parametrize(
    "corrupt",
    [
        pytest.param(lambda data: data[: len(data) // 2], id="truncated"),
        pytest.param(lambda _data: b"not a zip archive at all", id="not-an-archive"),
        pytest.param(lambda data: data[:60] + bytes([data[60] ^ 0xFF]) + data[61:], id="flipped-member-byte"),
    ],
)
def test_rehydration_refuses_a_corrupt_archive_before_writing_anything(tmp_path: Path, corrupt: Any) -> None:  # noqa: ANN401
    """Structural validation covers the whole archive before extraction begins."""
    run_id = "run-rehydrate-corrupt"
    projection = _product_run(tmp_path / "product", run_id)
    data = _published_plan_checkpoint(projection, run_id, tmp_path)

    with stage_scratch("verify") as scratch:
        destination = scratch.run_directory(SYNC_NAME, run_id)
        with pytest.raises((BundleFormatError, CheckpointUnavailableError)):
            _extract_directly(corrupt(data), destination)

        assert not destination.exists()


def _extract_directly(data: bytes, destination: Path) -> None:
    """Extract bytes that never went through the store, to reach the codec's refusals."""
    from infrahub_sync.product_store.bundle import extract_bundle

    extract_bundle(data, destination)


def test_rehydration_refuses_a_member_whose_digest_does_not_match_the_manifest(tmp_path: Path) -> None:
    """A member rewritten after the bundle manifest was computed is refused."""
    run_id = "run-rehydrate-digest"
    projection = _product_run(tmp_path / "product", run_id)
    data = _published_plan_checkpoint(projection, run_id, tmp_path)
    tampered = BytesIO()
    with zipfile.ZipFile(BytesIO(data)) as source, zipfile.ZipFile(tampered, "w", zipfile.ZIP_STORED) as target:
        for entry in source.infolist():
            payload = source.read(entry.filename)
            if entry.filename == "plan/operations.jsonl":
                payload += b" "
            target.writestr(entry, payload)

    with stage_scratch("verify") as scratch:
        destination = scratch.run_directory(SYNC_NAME, run_id)
        with pytest.raises(BundleFormatError) as failure:
            _extract_directly(tampered.getvalue(), destination)

        assert failure.value.reason in {"bundle-member-integrity-failed", "bundle-member-mismatch"}
        assert not destination.exists()


def test_an_oversized_checkpoint_is_refused_from_its_committed_reference(tmp_path: Path) -> None:
    """The recorded size refuses an oversized artifact before any transfer happens."""
    run_id = "run-rehydrate-oversized"
    projection = _product_run(tmp_path / "product", run_id)
    _published_plan_checkpoint(projection, run_id, tmp_path)
    reference = projection.lookup_internal_reference(run_id, PLAN_CHECKPOINT_ARTIFACT_ID).value
    assert reference is not None

    bounded = projection.lookup_internal_artifact(run_id, PLAN_CHECKPOINT_ARTIFACT_ID, limit=reference.size - 1)

    assert bounded.value is None
    assert bounded.reason == "artifact-too-large"
    assert reference.size <= MAX_BUNDLE_BYTES


@pytest.mark.usefixtures("claimed")
def test_the_verify_stage_refuses_a_missing_checkpoint_in_its_own_scratch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, planning_stage: list[Path]
) -> None:
    """Verify reads its input from the checkpoint, not from a directory it inherited.

    A shared cache holding this run's plan is present and ignored: the stage that
    consumes a plan resolves it from product storage or refuses.
    """
    run_id = "run-verify-no-checkpoint"
    projection = _product_run(tmp_path / "product", run_id, operation="verify", phase="planned")
    stale = tmp_path / "shared-cache" / SYNC_NAME / run_id
    stale.mkdir(parents=True)
    _write_plan(stale, run_id)
    monkeypatch.setenv("INFRAHUB_SYNC_CACHE_DIR", str(tmp_path / "shared-cache"))
    monkeypatch.setattr(service_flow, "_runtime", lambda: (str(tmp_path), projection))

    with pytest.raises(RuntimeError) as failure:
        service_sync_run.fn(run_id, "verify", *_binding(projection, run_id))

    assert PLAN_CHECKPOINT_ARTIFACT_ID in str(failure.value)
    assert planning_stage == []


@pytest.mark.usefixtures("claimed")
def test_the_verify_stage_reads_the_checkpoint_it_rehydrated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The engine verifies the plan the checkpoint placed in this stage's scratch."""
    run_id = "run-verify-from-checkpoint"
    projection = _product_run(tmp_path / "product", run_id, operation="verify", phase="planned")
    _published_plan_checkpoint(projection, run_id, tmp_path)
    seen: list[Path] = []

    def execute_run(instance: Any, **kwargs: Any) -> SavedPlan:  # noqa: ANN401
        from infrahub_sync.cache.paths import run_dir

        directory = run_dir(instance.name, str(kwargs["run_id"]), base_directory=stage_root(kwargs))
        assert (directory / "plan" / "manifest.json").is_file()
        assert (directory / "A" / "resource0.parquet").is_file()
        seen.append(directory)
        manifest = _manifest_of(directory)
        return SavedPlan(manifest=manifest, operations=[], checksum_ok=True, verification_notes=[])

    monkeypatch.setattr(service_flow, "execute_run", execute_run)
    monkeypatch.setattr(service_flow, "resolve_runtime_instance", lambda *_a, **_k: _instance())
    monkeypatch.setattr(service_flow, "build_runtime_model_plan", lambda *_a, **_k: None)
    monkeypatch.setattr(service_flow, "_run_logger", lambda: (service_flow.logger, False))
    monkeypatch.setattr(service_flow, "_runtime", lambda: (str(tmp_path), projection))

    result = service_sync_run.fn(run_id, "verify", *_binding(projection, run_id))

    assert result["outcome"] == "verified"
    assert len(seen) == 1
    assert not seen[0].exists(), "the rehydrated plan is scratch, released with the stage"


def _manifest_of(run_directory: Path) -> Any:  # noqa: ANN401
    """Parse the manifest the checkpoint placed, as the engine would read it."""
    from infrahub_sync.plan.reader import parse_plan_artifact, read_plan_artifact_bytes

    return parse_plan_artifact(read_plan_artifact_bytes(run_directory), run_id=run_directory.name).manifest


@pytest.mark.usefixtures("claimed")
def test_a_read_stage_advances_no_baseline(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Verify never advances the safety reference; only a successful write does."""
    run_id = "run-verify-no-baseline"
    projection = _product_run(tmp_path / "product", run_id, operation="verify", phase="planned")
    _published_plan_checkpoint(projection, run_id, tmp_path)
    binding = _binding(projection, run_id)

    def execute_run(instance: Any, **kwargs: Any) -> SavedPlan:  # noqa: ANN401
        from infrahub_sync.cache.paths import run_dir

        directory = run_dir(instance.name, str(kwargs["run_id"]), base_directory=stage_root(kwargs))
        return SavedPlan(manifest=_manifest_of(directory), operations=[], checksum_ok=True, verification_notes=[])

    monkeypatch.setattr(service_flow, "execute_run", execute_run)
    monkeypatch.setattr(service_flow, "resolve_runtime_instance", lambda *_a, **_k: _instance())
    monkeypatch.setattr(service_flow, "build_runtime_model_plan", lambda *_a, **_k: None)
    monkeypatch.setattr(service_flow, "_run_logger", lambda: (service_flow.logger, False))
    monkeypatch.setattr(service_flow, "_runtime", lambda: (str(tmp_path), projection))

    service_sync_run.fn(run_id, "verify", *binding)

    assert projection.lookup_configuration_baseline(binding[0]).value is None
