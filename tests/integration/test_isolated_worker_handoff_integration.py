"""Opt-in proof that a run survives worker replacement with no shared Sync filesystem.

The unit suites drive the stage boundary in one process against local providers. What they
cannot show is the fact the whole cutover rests on: that a plan computed by one worker
process is verified and applied by *different* processes, on real PostgreSQL and real
S3-compatible storage, after the planning worker is gone and its private scratch directory
has been removed.

Each stage below runs in its own interpreter. Nothing is shared but the product store, so
a stage that needed a file from its predecessor could only get it from the internal
checkpoint. The configuration directory is outside the repository checkout, and no worker
receives a cache setting at all.

WARNING: point the settings only at disposable, single-purpose backends. These tests
create configurations, runs, artifacts, and advisory locks, and the control case deletes an
object from the bucket.

Opt in with ``-m integration`` and reachable settings::

    INFRAHUB_SYNC_STORAGE_INTEGRATION_DATABASE_URL="postgresql://postgres:probe@127.0.0.1:55433/storeprobe" \\
    INFRAHUB_SYNC_STORAGE_INTEGRATION_S3_ENDPOINT_URL="http://127.0.0.1:9010" \\
    INFRAHUB_SYNC_STORAGE_INTEGRATION_S3_BUCKET="infrahub-sync-preview" \\
        uv run pytest -m integration tests/integration/test_isolated_worker_handoff_integration.py
"""

from __future__ import annotations

import json
import os
import subprocess  # noqa: S404 - the worker processes run a fixed interpreter and inline script.
import sys
import textwrap
from datetime import datetime, timezone
from hashlib import sha256
from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest

pytest.importorskip("boto3")
pytest.importorskip("psycopg")
pytest.importorskip("prefect")

from infrahub_sync.product_store import MutationReceipt, PrefectExecutionLink, ProductRun
from infrahub_sync.product_store.bundle import (
    FINAL_CHECKPOINT_ARTIFACT_ID,
    PLAN_CHECKPOINT_ARTIFACT_ID,
)
from infrahub_sync.service.service import PLAN_ARTIFACT_ID
from infrahub_sync.service.storage import service_product_projection
from tests.configuration.validation_packages import package

if TYPE_CHECKING:
    from infrahub_sync.product_store import ProductProjection

pytestmark = pytest.mark.integration

_REQUIRED_ENVIRONMENT = (
    "INFRAHUB_SYNC_STORAGE_INTEGRATION_DATABASE_URL",
    "INFRAHUB_SYNC_STORAGE_INTEGRATION_S3_BUCKET",
    "INFRAHUB_SYNC_STORAGE_INTEGRATION_S3_ENDPOINT_URL",
)
_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_FINGERPRINT = "f" * 64


def _settings_or_skip() -> dict[str, str]:
    """Return disposable-store settings, or skip before any network client exists."""
    values = {name: os.environ.get(name, "") for name in _REQUIRED_ENVIRONMENT}
    if missing := [name for name, value in values.items() if not value]:
        pytest.skip(f"isolated worker qualification requires explicit settings; missing: {', '.join(missing)}")
    return {
        "INFRAHUB_SYNC_DATABASE_URL": values["INFRAHUB_SYNC_STORAGE_INTEGRATION_DATABASE_URL"],
        "INFRAHUB_SYNC_S3_BUCKET": values["INFRAHUB_SYNC_STORAGE_INTEGRATION_S3_BUCKET"],
        "INFRAHUB_SYNC_S3_ENDPOINT_URL": values["INFRAHUB_SYNC_STORAGE_INTEGRATION_S3_ENDPOINT_URL"],
        "INFRAHUB_SYNC_S3_PREFIX": os.environ.get(
            "INFRAHUB_SYNC_STORAGE_INTEGRATION_S3_PREFIX", "isolated-worker-qualification"
        ),
        "INFRAHUB_SYNC_S3_REGION": os.environ.get("INFRAHUB_SYNC_STORAGE_INTEGRATION_S3_REGION", "us-east-1"),
    }


# The worker process. Everything Unit 3 owns runs for real here: the private scratch, the
# plan artifact, the bundle codec, the S3 publication and rehydration, the PostgreSQL guard
# and baseline, the apply engine, and the local applied sidecar. Two things are doubled,
# because this environment provides neither: source extraction and the destination adapter,
# and Prefect's worker-identity lookup.
_WORKER = '''
import json, os, sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, {repository_root!r})

from infrahub_sync.plan.checksum import source_snapshot_records
from infrahub_sync.plan.config_version import resolve_config_version
from infrahub_sync.plan.models import PlannedOperation, SourceSnapshotRecord
from infrahub_sync.plan.review import SavedPlan, read_saved_plan
from infrahub_sync.plan.writer import write_plan_artifact
from infrahub_sync.runtime_schema import RuntimeModelPlan, RuntimeSideModels
from infrahub_sync.service import flow as service_flow
from infrahub_sync.service.scratch import stage_scratch as real_stage_scratch
from tests.plan.artifact_fixtures import operation_record

REPORT = Path({report!r})
STAGE = {stage!r}
RUN_ID = {run_id!r}
BINDING = tuple({binding!r})
FLOW_RUN_ID = {flow_run_id!r}
WORKER_ID = {worker_id!r}
SCHEMA_FINGERPRINT = {fingerprint!r}

report = {{"stage": STAGE, "pid": os.getpid(), "dispatched": [], "scratch": None}}


def write_report():
    REPORT.write_text(json.dumps(report), encoding="utf-8")


class RecordingDestination:
    """The one adapter an apply constructs; no destination service exists here."""

    def __init__(self, **kwargs):
        report["destination_constructed"] = True

    def new_peer_resolver(self):
        return object()

    def apply_planned_operation(self, *, operation, peers):
        report["dispatched"].append(operation.operation_id)
        return "node-" + str(len(report["dispatched"]))


def scratch(stage):
    """Record the private root this stage works in, then behave exactly as usual."""
    from contextlib import contextmanager

    @contextmanager
    def wrapped():
        with real_stage_scratch(stage) as value:
            report["scratch"] = str(value.root)
            write_report()
            yield value

    return wrapped()


def authored_plan(instance, **kwargs):
    """Stand in for extraction: write the plan artifact this run would have produced."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    base = Path(kwargs["base_directory"])
    assert base.is_absolute(), base
    directory = base / instance.name / RUN_ID
    (directory / "A").mkdir(parents=True, exist_ok=True)
    pq.write_table(
        pa.table({{"name": ["prod"], "_extract_ts": ["2026-09-04T12:00:00+00:00"]}}),
        directory / "A" / "tag.parquet",
    )
    write_plan_artifact(
        run_dir=directory,
        run_id=RUN_ID,
        config_version=resolve_config_version(instance),
        source_snapshot=[SourceSnapshotRecord(**record) for record in source_snapshot_records(directory)],
        deletes_computed=True,
        operations=[PlannedOperation.model_validate(operation_record(identity={{"name": "prod"}}))],
        configuration_binding=BINDING,
        schema_fingerprint=SCHEMA_FINGERPRINT,
    )
    report["planned_in"] = str(directory)
    return read_saved_plan(sync_name=instance.name, run_id=RUN_ID, config=instance, base_directory=base)


def models(**kwargs):
    return RuntimeModelPlan(
        branch="main",
        schema_fingerprint=SCHEMA_FINGERPRINT,
        destination=RuntimeSideModels(adapter_class=RecordingDestination, models={{}}),
        source=None,
    )


service_flow.stage_scratch = scratch
service_flow.build_runtime_model_plan = models
service_flow._prefect_flow_run_id = lambda: FLOW_RUN_ID
service_flow._require_current_worker_identity = lambda *args: None
if STAGE in ("plan", "sync"):
    # Only extraction is doubled. The plan artifact, its snapshots, the checkpoint, the
    # guard, the apply engine and the sidecar are all the real ones.
    service_flow._plan = authored_plan
    service_flow.execute_run_original = service_flow.execute_run

    def execute_run(instance, **kwargs):
        if kwargs.get("operation") == "plan":
            return authored_plan(instance, **kwargs)
        return service_flow.execute_run_original(instance, **kwargs)

    service_flow.execute_run = execute_run

os.environ["PREFECT__WORKER_ID"] = WORKER_ID

try:
    result = service_flow.service_sync_run.fn(
        RUN_ID,
        STAGE,
        BINDING[0],
        BINDING[1],
        BINDING[2],
        None,
        {expected_checksum!r},
        {confirm_writes!r},
    )
    report["outcome"] = result["outcome"]
    report["ok"] = True
except BaseException as failure:
    report["ok"] = False
    report["error_type"] = type(failure).__name__
    report["error"] = str(failure)

write_report()
sys.exit(0 if report["ok"] else 1)
'''


def _run_worker(  # noqa: PLR0913 - one parameter per input the worker process needs
    stage: str,
    *,
    run_id: str,
    binding: tuple[str, int, str],
    settings: dict[str, str],
    config_directory: Path,
    flow_run_id: str,
    expected_checksum: str | None = None,
    confirm_writes: bool = False,
    report_root: Path,
) -> dict[str, Any]:
    """Run one stage in its own interpreter and return the report it wrote."""
    report = report_root / f"{stage}-{uuid4().hex}.json"
    script = _WORKER.format(
        repository_root=str(_REPOSITORY_ROOT),
        report=str(report),
        stage=stage,
        run_id=run_id,
        binding=list(binding),
        flow_run_id=flow_run_id,
        worker_id=str(uuid4()),
        fingerprint=SCHEMA_FINGERPRINT,
        expected_checksum=expected_checksum,
        confirm_writes=confirm_writes,
    )
    environment = {
        **os.environ,
        **settings,
        "INFRAHUB_SYNC_CONFIG_DIRECTORY": str(config_directory),
        "INFRAHUB_API_TOKEN": "qualification-destination-token",
        "NETBOX_TOKEN": "qualification-source-token",
    }
    # No worker receives a cache setting: a stage that read one would be reaching for a
    # filesystem it is not allowed to share.
    environment.pop("INFRAHUB_SYNC_CACHE_DIR", None)
    completed = subprocess.run(  # noqa: S603 - fixed interpreter, inline script, no shell.
        [sys.executable, "-c", textwrap.dedent(script)],
        capture_output=True,
        text=True,
        env=environment,
        cwd=str(report_root),
        check=False,
    )
    if not report.is_file():
        pytest.fail(f"stage {stage!r} wrote no report; stderr: {completed.stderr[-3000:]}")
    written: dict[str, Any] = json.loads(report.read_text(encoding="utf-8"))
    written["returncode"] = completed.returncode
    written["stderr"] = completed.stderr[-3000:]
    written["detail"] = f"{written.get('error_type')}: {written.get('error')}\n{written['stderr']}"
    return written


@pytest.fixture
def qualification(tmp_path: Path) -> dict[str, Any]:
    """One registered configuration, its run, and a configuration directory off-checkout."""
    settings = _settings_or_skip()
    projection = service_product_projection(environ=settings)
    version = projection.create_configuration(package())
    binding = (version.config_id, version.registry_version, version.package_checksum)
    # Outside the repository checkout on purpose: a worker's configuration directory is
    # configuration data, never flow source.
    config_directory = tmp_path / "configuration-data"
    config_directory.mkdir()
    assert _REPOSITORY_ROOT not in config_directory.parents
    return {
        "settings": settings,
        "projection": projection,
        "binding": binding,
        "config_directory": config_directory,
        "reports": tmp_path,
    }


def _create_run(
    projection: ProductProjection,
    run_id: str,
    binding: tuple[str, int, str],
    *,
    operation: str,
    phase: str,
) -> None:
    projection.create_run(
        ProductRun(
            run_id=run_id,
            operation=operation,  # ty: ignore[invalid-argument-type]
            configuration_reference=f"{binding[0]}@{binding[1]}",
            config_id=binding[0],
            registry_version=binding[1],
            package_checksum=binding[2],
            actor="qualification",
            started_at=datetime.now(timezone.utc),
            phase=phase,
        )
    )


def _admit(projection: ProductProjection, run_id: str, purpose: str) -> str:
    """Reserve this stage's own receipt, then append the execution through it.

    Identifiers are unique per call rather than per process: this store is a real database
    that outlives one test run, so a shared counter would collide with an earlier receipt.
    The receipt is resolved with its stored response exactly as the API resolves one, so
    the next stage of the same run can win its own reservation.

    Returns the Prefect flow-run identifier the admitted worker must claim.
    """
    ordinal = uuid4().hex
    receipt_id = f"m-qualification-{ordinal}"
    now = datetime.now(timezone.utc)
    reserved, _created = projection.reserve_mutation(
        MutationReceipt(
            receipt_id=receipt_id,
            actor="qualification",
            key_digest=sha256(f"qualification-key-{ordinal}".encode()).hexdigest(),
            operation=purpose,
            target_run_id=run_id,
            request_fingerprint=sha256(f"{purpose}:{run_id}:{ordinal}".encode()).hexdigest(),
            reason="isolated worker qualification",
            resource_id=run_id,
            run_id=run_id,
            prefect_key=sha256(f"prefect:{receipt_id}".encode()).hexdigest(),
            created_at=now,
            updated_at=now,
        ),
        admit_write=purpose in {"apply", "sync"},
    )
    flow_run_id = str(uuid4())
    projection.add_prefect_execution(
        run_id,
        PrefectExecutionLink(flow_run_id=flow_run_id, purpose=purpose, attempt=1, submitted_at=now),
        receipt_id=reserved.receipt_id,
    )
    projection.complete_mutation(
        reserved.receipt_id,
        response_status=202,
        response_body={"run_id": run_id, "operation": purpose},
        flow_run_id=flow_run_id,
    )
    return flow_run_id


def test_a_run_survives_worker_replacement_across_plan_verify_and_apply(  # noqa: PLR0914, PLR0915
    qualification: dict[str, Any],
) -> None:
    """Three worker processes, no shared Sync filesystem, one durable run.

    The planning worker exits and its private scratch is removed before the verifying
    worker starts, and again before the applying worker starts. Each later stage can only
    have obtained the plan from the internal checkpoint in object storage.
    """
    projection: ProductProjection = qualification["projection"]
    binding: tuple[str, int, str] = qualification["binding"]
    run_id = f"handoff-{uuid4().hex[:12]}"
    _create_run(projection, run_id, binding, operation="plan", phase="accepted")
    plan_flow_run = _admit(projection, run_id, "plan")

    planned = _run_worker(
        "plan",
        run_id=run_id,
        binding=binding,
        flow_run_id=plan_flow_run,
        settings=qualification["settings"],
        config_directory=qualification["config_directory"],
        report_root=qualification["reports"],
    )
    assert planned["ok"], planned["detail"]
    assert planned["outcome"] == "planned"

    # The planning worker is gone. Its private scratch went with it, and the parent
    # removes the path as well so nothing can depend on it existing.
    planning_scratch = Path(planned["scratch"])
    assert not planning_scratch.exists()
    assert Path(planned["planned_in"]).is_relative_to(planning_scratch)

    # The internal handoff and the public review are both durable.
    checkpoint = projection.lookup_internal_reference(run_id, PLAN_CHECKPOINT_ARTIFACT_ID).value
    assert checkpoint is not None
    assert projection.lookup_artifact(run_id, PLAN_ARTIFACT_ID).value is not None
    stored_bytes = projection.lookup_internal_artifact(run_id, PLAN_CHECKPOINT_ARTIFACT_ID).value
    assert stored_bytes is not None
    assert len(stored_bytes) == checkpoint.size

    verify_flow_run = _admit(projection, run_id, "verify")
    verified = _run_worker(
        "verify",
        run_id=run_id,
        binding=binding,
        flow_run_id=verify_flow_run,
        settings=qualification["settings"],
        config_directory=qualification["config_directory"],
        report_root=qualification["reports"],
    )
    assert verified["ok"], verified["detail"]
    assert verified["outcome"] == "verified"
    assert verified["pid"] != planned["pid"]
    verifying_scratch = Path(verified["scratch"])
    assert verifying_scratch != planning_scratch
    assert not verifying_scratch.exists()

    manifest_checksum = json.loads((projection.lookup_artifact(run_id, PLAN_ARTIFACT_ID).value or b"{}").decode())[
        "checksum"
    ]

    apply_flow_run = _admit(projection, run_id, "apply")
    applied = _run_worker(
        "apply",
        run_id=run_id,
        binding=binding,
        flow_run_id=apply_flow_run,
        settings=qualification["settings"],
        config_directory=qualification["config_directory"],
        expected_checksum=manifest_checksum,
        confirm_writes=True,
        report_root=qualification["reports"],
    )
    assert applied["ok"], applied["detail"]
    assert applied["outcome"] == "applied"
    assert applied["pid"] not in {planned["pid"], verified["pid"]}
    assert applied["destination_constructed"] is True
    assert len(applied["dispatched"]) == 1
    applying_scratch = Path(applied["scratch"])
    assert applying_scratch not in {planning_scratch, verifying_scratch}
    assert not applying_scratch.exists()

    # PostgreSQL holds the product result and the configuration baseline.
    stored = projection.lookup_run(run_id).value
    assert stored is not None
    assert (stored.phase, stored.outcome) == ("applied", "applied")
    assert stored.reconciliation_required is False
    baseline = projection.lookup_configuration_baseline(binding[0]).value
    assert baseline is not None
    assert baseline.source_row_counts == {"tag": 1}
    assert baseline.runs_since_full_extract == 0

    # Object storage holds both checkpoints, each with committed digest evidence.
    final = projection.lookup_internal_reference(run_id, FINAL_CHECKPOINT_ARTIFACT_ID).value
    assert final is not None
    assert final.digest != checkpoint.digest
    final_bytes = projection.lookup_internal_artifact(run_id, FINAL_CHECKPOINT_ARTIFACT_ID).value
    assert final_bytes is not None
    assert len(final_bytes) == final.size
    assert projection.lookup_artifact(run_id, FINAL_CHECKPOINT_ARTIFACT_ID).value is None


def test_a_separate_managed_sync_publishes_both_checkpoints_and_the_baseline(
    qualification: dict[str, Any],
) -> None:
    """One worker plans, writes, and publishes under one guard hold on real backends."""
    projection: ProductProjection = qualification["projection"]
    binding: tuple[str, int, str] = qualification["binding"]
    run_id = f"sync-{uuid4().hex[:12]}"
    _create_run(projection, run_id, binding, operation="sync", phase="accepted")
    sync_flow_run = _admit(projection, run_id, "sync")

    synced = _run_worker(
        "sync",
        run_id=run_id,
        binding=binding,
        flow_run_id=sync_flow_run,
        settings=qualification["settings"],
        config_directory=qualification["config_directory"],
        confirm_writes=True,
        report_root=qualification["reports"],
    )

    assert synced["ok"], synced["detail"]
    assert len(synced["dispatched"]) == 1
    assert not Path(synced["scratch"]).exists()
    assert projection.lookup_internal_reference(run_id, PLAN_CHECKPOINT_ARTIFACT_ID).value is not None
    assert projection.lookup_internal_reference(run_id, FINAL_CHECKPOINT_ARTIFACT_ID).value is not None
    stored = projection.lookup_run(run_id).value
    assert stored is not None
    assert stored.outcome == "applied"
    baseline = projection.lookup_configuration_baseline(binding[0]).value
    assert baseline is not None
    assert baseline.source_row_counts == {"tag": 1}


def test_breaking_the_bundle_handoff_stops_the_apply_on_a_new_worker(
    qualification: dict[str, Any],
) -> None:
    """The control: with the checkpoint's bytes gone, the apply refuses and writes nothing.

    This is the mutation that shows the previous cases prove what they claim. The plan is
    computed and published exactly as before, the planning worker's scratch is gone, and
    the only thing changed is that the checkpoint object is removed from the bucket. A
    stage that still had a shared filesystem, or that inferred anything from the committed
    reference alone, would apply regardless.
    """
    projection: ProductProjection = qualification["projection"]
    binding: tuple[str, int, str] = qualification["binding"]
    run_id = f"broken-{uuid4().hex[:12]}"
    _create_run(projection, run_id, binding, operation="plan", phase="accepted")
    plan_flow_run = _admit(projection, run_id, "plan")

    planned = _run_worker(
        "plan",
        run_id=run_id,
        binding=binding,
        flow_run_id=plan_flow_run,
        settings=qualification["settings"],
        config_directory=qualification["config_directory"],
        report_root=qualification["reports"],
    )
    assert planned["ok"], planned["detail"]
    reference = projection.lookup_internal_reference(run_id, PLAN_CHECKPOINT_ARTIFACT_ID).value
    assert reference is not None
    manifest_checksum = json.loads((projection.lookup_artifact(run_id, PLAN_ARTIFACT_ID).value or b"{}").decode())[
        "checksum"
    ]

    _delete_stored_object(qualification["settings"], reference.object_key)

    apply_flow_run = _admit(projection, run_id, "apply")
    applied = _run_worker(
        "apply",
        run_id=run_id,
        binding=binding,
        flow_run_id=apply_flow_run,
        settings=qualification["settings"],
        config_directory=qualification["config_directory"],
        expected_checksum=manifest_checksum,
        confirm_writes=True,
        report_root=qualification["reports"],
    )

    assert applied["ok"] is False
    assert applied["returncode"] == 1
    assert PLAN_CHECKPOINT_ARTIFACT_ID in applied["error"]
    assert applied["dispatched"] == []
    assert "destination_constructed" not in applied
    stored = projection.lookup_run(run_id).value
    assert stored is not None
    # Refused before dispatch, so this is an ordinary failure and not an ambiguous one.
    assert (stored.phase, stored.outcome) == ("apply-failed", "failed")
    assert stored.reconciliation_required is False
    assert projection.lookup_internal_reference(run_id, FINAL_CHECKPOINT_ARTIFACT_ID).value is None


def _delete_stored_object(settings: dict[str, str], object_key: str) -> None:
    """Remove one published object from the disposable bucket.

    The client is resolved by name rather than imported: the Python 3.10 profile installs
    no object-store client at all, and this module is only reached after its settings check.
    """
    boto3 = import_module("boto3")

    client = boto3.client(
        "s3",
        endpoint_url=settings["INFRAHUB_SYNC_S3_ENDPOINT_URL"],
        region_name=settings["INFRAHUB_SYNC_S3_REGION"],
    )
    prefix = settings["INFRAHUB_SYNC_S3_PREFIX"]
    key = f"{prefix}/{object_key}" if prefix else object_key
    client.delete_object(Bucket=settings["INFRAHUB_SYNC_S3_BUCKET"], Key=key)
