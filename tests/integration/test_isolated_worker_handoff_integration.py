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
import subprocess  # noqa: S404 - the worker processes run a fixed interpreter and script.
import sys
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
# One stage against reachable backends is seconds' work; this bound exists so an
# unreachable-but-accepting backend fails the case instead of hanging the session.
_WORKER_TIMEOUT_SECONDS = 300


def _captured(stream: str | bytes | None) -> str:
    """Render whatever a finished or timed-out worker managed to write."""
    if stream is None:
        return "<none>"
    text = stream.decode("utf-8", "replace") if isinstance(stream, bytes) else stream
    return text[-3000:] or "<empty>"


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


_WORKER_PROGRAM = Path(__file__).resolve().parent / "isolated_worker_program.py"


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
    input_path = report_root / f"{stage}-{uuid4().hex}-input.json"
    input_path.write_text(
        json.dumps(
            {
                "report": str(report),
                "stage": stage,
                "run_id": run_id,
                "binding": list(binding),
                "flow_run_id": flow_run_id,
                "worker_id": str(uuid4()),
                "fingerprint": SCHEMA_FINGERPRINT,
                "expected_checksum": expected_checksum,
                "confirm_writes": confirm_writes,
            }
        ),
        encoding="utf-8",
    )
    environment = {
        **os.environ,
        **settings,
        "INFRAHUB_SYNC_CONFIG_DIRECTORY": str(config_directory),
        "INFRAHUB_API_TOKEN": "qualification-destination-token",
        "NETBOX_TOKEN": "qualification-source-token",
    }
    # The upgraded operator's shell: the retired cache setting is still exported, and the
    # worker is started somewhere that is not its stage root. Both are left in place on
    # purpose -- a stage that reaches for either is what this qualification catches.
    legacy_cache = report_root / "legacy-cache-canary"
    legacy_cache.mkdir(exist_ok=True)
    working_directory = report_root / "cwd-canary"
    working_directory.mkdir(exist_ok=True)
    environment["INFRAHUB_SYNC_CACHE_DIR"] = str(legacy_cache)
    try:
        completed = subprocess.run(  # noqa: S603 - fixed interpreter, fixed script path, no shell.
            [sys.executable, str(_WORKER_PROGRAM), str(input_path)],
            capture_output=True,
            text=True,
            env=environment,
            cwd=str(working_directory),
            check=False,
            timeout=_WORKER_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as expired:
        # The settings check proves only that they are non-empty. A backend that accepts
        # the connection and then stalls would otherwise hang the session with no output.
        pytest.fail(
            f"stage {stage!r} did not finish within {_WORKER_TIMEOUT_SECONDS}s; stderr: {_captured(expired.stderr)}"
        )
    if not report.is_file():
        pytest.fail(f"stage {stage!r} wrote no report; stderr: {_captured(completed.stderr)}")
    written: dict[str, Any] = json.loads(report.read_text(encoding="utf-8"))
    written["returncode"] = completed.returncode
    written["stderr"] = _captured(completed.stderr)
    written["detail"] = f"{written.get('error_type')}: {written.get('error')}\n{written['stderr']}"
    written["legacy_cache_contents"] = sorted(str(path.relative_to(legacy_cache)) for path in legacy_cache.rglob("*"))
    written["cwd_contents"] = sorted(str(path.relative_to(working_directory)) for path in working_directory.rglob("*"))
    # Proof the child actually received this call's working directory and read this call's
    # serialized input, rather than some stale or shared state.
    assert (written.get("cwd"), written.get("input_path")) == (str(working_directory), str(input_path)), written
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
    assert planned["legacy_cache_contents"] == [], planned["legacy_cache_contents"]
    assert planned["cwd_contents"] == [], planned["cwd_contents"]
    # The real engine entry point derived this run's directory inside the stage root.
    assert Path(planned["engine_built_in"]).is_relative_to(Path(planned["scratch"]))

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
    assert verified["legacy_cache_contents"] == []
    assert verified["cwd_contents"] == []
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
    assert applied["legacy_cache_contents"] == []
    assert applied["cwd_contents"] == []
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
    assert synced["legacy_cache_contents"] == [], synced["legacy_cache_contents"]
    assert synced["cwd_contents"] == [], synced["cwd_contents"]
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
