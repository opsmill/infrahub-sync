"""Executable CLI, Python, managed-worker, and managed HTTP/Prefect matrix."""

from __future__ import annotations

import json
import logging
import traceback
from collections.abc import Callable, Mapping
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, cast
from uuid import uuid4

import pytest
from typer.testing import CliRunner

pytest.importorskip("prefect")
pytest.importorskip("opsmill_prefect_extras")
pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from infrahub_sync import SyncAdapter, SyncInstance
from infrahub_sync.api import v1 as api
from infrahub_sync.api.v1 import _operations as api_operations  # noqa: PLC2701 - executable surface seam.
from infrahub_sync.cli import app
from infrahub_sync.execution import Operation, RunResult
from infrahub_sync.managed import flow as managed_flow
from infrahub_sync.managed.app import create_app
from infrahub_sync.managed.auth import PRINCIPALS_ENV, EnvironmentPrincipalResolver
from infrahub_sync.managed.flow import managed_sync_run
from infrahub_sync.managed.orchestration import Observation, Submission
from infrahub_sync.managed.service import ManagedRunService
from infrahub_sync.plan.config_version import resolve_config_version
from infrahub_sync.plan.models import PlanManifest
from infrahub_sync.plan.review import SavedPlan, read_saved_plan, resolve_run_directory
from infrahub_sync.plan.writer import write_plan_artifact
from infrahub_sync.product_store import ProductProjection, ProductRun, local_product_projection
from infrahub_sync.product_store.standalone import execute_standalone
from tests.conformance.interface_adapters import product_envelope, serialized_boundaries
from tests.conformance.oracle import CanonicalEnvelope, Surface, assert_equivalent

SENTINEL = "db006-interface-sentinel-credential"
AUTH_TOKEN = "db006-managed-owner-token"  # noqa: S105 - deliberate non-secret canary.


def _instance(tmp_path: Path) -> SyncInstance:
    return SyncInstance(
        name="inventory",
        directory=str(tmp_path),
        source=SyncAdapter(name="source", settings={"token": SENTINEL}),
        destination=SyncAdapter(name="destination", settings={}),
    )


def _saved(run_id: str, instance: SyncInstance) -> SavedPlan:
    return SavedPlan(
        manifest=PlanManifest(
            format_version=2,
            run_id=run_id,
            created_at="2026-08-10T12:00:00+00:00",
            config_version=resolve_config_version(instance),
            source_snapshot=[],
            operations_count=0,
            delete_operations_computed=True,
            plan_checksum="a" * 64,
        ),
        operations=[],
        checksum_ok=True,
        verification_notes=[],
    )


def _result(run_id: str, operation: Operation, tmp_path: Path) -> RunResult:
    return RunResult(
        sync_name="inventory",
        operation=operation,
        run_id=run_id,
        status="no-change",
        changed=False,
        summary={"create": 0, "update": 0, "delete": 0},
        artifact_path=str(tmp_path / run_id),
    )


def _observation(
    surface: Surface,
    operation: str,
    saved: SavedPlan,
    projection: ProductProjection,
    run_id: str,
) -> CanonicalEnvelope:
    record = projection.lookup_run(run_id).value
    artifact = projection.lookup_artifact(run_id, "plan-review").value
    assert record is not None
    assert artifact is not None
    return product_envelope(
        surface=surface,
        operation=operation,
        saved=saved,
        record=record,
        artifact=artifact,
        destination_effects={"created": 0, "updated": 0, "deleted": 0},
    )


def _seed_plan(instance: SyncInstance, saved: SavedPlan, projection_root: Path) -> None:
    execute_standalone(
        instance,
        operation="plan",
        run_id=saved.manifest.run_id,
        product_cache_location=projection_root,
        _return_saved_plan=True,
        _core_executor=lambda *_args, **_kwargs: saved,
    )


def _run_cli(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    operation: Operation,
    run_id: str,
) -> CanonicalEnvelope:
    instance = _instance(tmp_path)
    saved = _saved(run_id, instance)
    root = (tmp_path / "cli-products").resolve()
    if operation == "apply":
        _seed_plan(instance, saved, root)

    def core(_instance: object, *, operation: Operation, **kwargs: object) -> RunResult:
        if operation == "sync":
            callback = cast("Callable[[], None]", kwargs["_plan_committed"])
            callback()
        return _result(run_id, operation, tmp_path)

    monkeypatch.setattr("infrahub_sync.cli.get_instance", lambda **_kwargs: instance)
    monkeypatch.setattr("infrahub_sync.cli.execute_run", core)
    monkeypatch.setattr("infrahub_sync.product_store.standalone.generate_run_id", lambda: run_id)
    monkeypatch.setattr("infrahub_sync.product_store.standalone.read_saved_plan", lambda **_kwargs: saved)
    common = ["--name", "inventory", "--product-cache-location", str(root)]
    if operation == "plan":
        arguments = ["diff", *common, "--run-id", run_id]
    elif operation == "apply":
        arguments = ["apply", *common, "--run-id", run_id, "--expected-checksum", saved.manifest.plan_checksum]
    else:
        arguments = ["sync", *common, "--no-parallel"]

    invoked = CliRunner().invoke(app, arguments)
    assert invoked.exit_code == 0, invoked.output
    return _observation("cli", operation, saved, local_product_projection(root), run_id)


def _run_python(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    operation: Operation,
    run_id: str,
) -> CanonicalEnvelope:
    instance = _instance(tmp_path)
    saved = _saved(run_id, instance)
    root = (tmp_path / "python-products").resolve()
    if operation == "apply":
        _seed_plan(instance, saved, root)

    def core(_instance: object, *, operation: Operation, **_kwargs: object) -> SavedPlan | RunResult:
        return saved if operation in {"plan", "verify"} else _result(run_id, "apply", tmp_path)

    monkeypatch.setattr(api_operations, "generate_run_id", lambda: run_id)
    monkeypatch.setattr(api_operations, "resolve_sync_instance", lambda *_args, **_kwargs: instance)
    monkeypatch.setattr(api_operations, "resolve_run_directory", lambda *_args, **_kwargs: tmp_path / run_id)
    monkeypatch.setattr(api_operations, "read_saved_plan", lambda **_kwargs: saved)
    monkeypatch.setattr(api_operations, "execute_run", core)
    monkeypatch.setattr(api_operations, "bounded_run_lock", lambda *_args, **_kwargs: nullcontext())
    if operation == "plan":
        api.plan(
            api.PlanRequest(sync_name="inventory", config_directory=str(tmp_path), product_cache_location=str(root))
        )
    elif operation == "apply":
        api.apply(
            api.ApplyRequest(
                sync_name="inventory",
                config_directory=str(tmp_path),
                run_id=run_id,
                expected_checksum=saved.manifest.plan_checksum,
                product_cache_location=str(root),
            )
        )
    else:
        api.sync(
            api.SyncRequest(
                sync_name="inventory",
                config_directory=str(tmp_path),
                confirm_writes=True,
                product_cache_location=str(root),
            )
        )
    return _observation("python", operation, saved, local_product_projection(root), run_id)


def _run_managed_worker(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    operation: Operation,
    run_id: str,
) -> CanonicalEnvelope:
    instance = _instance(tmp_path)
    saved = _saved(run_id, instance)
    root = (tmp_path / "managed-products").resolve()
    projection = local_product_projection(root)
    projection.create_run(
        ProductRun(
            run_id=run_id,
            operation="plan" if operation == "apply" else operation,
            configuration_reference=resolve_config_version(instance),
            started_at=datetime.now(timezone.utc),
            phase="accepted",
            summary={"sync_name": "inventory"},
        )
    )
    monkeypatch.setattr(managed_flow, "_runtime", lambda: (str(tmp_path), projection))
    monkeypatch.setattr(managed_flow, "_run_logger", lambda: (logging.getLogger("db006-matrix"), False))
    monkeypatch.setattr(managed_flow, "resolve_sync_instance", lambda *_args, **_kwargs: instance)
    monkeypatch.setattr(managed_flow, "collect_secret_values", lambda _instance=None: ())
    monkeypatch.setattr(managed_flow, "bounded_run_lock", lambda *_args, **_kwargs: nullcontext())
    monkeypatch.setattr(managed_flow, "_plan", lambda *_args, **_kwargs: saved)

    def core(_instance: object, *, operation: Operation, **_kwargs: object) -> SavedPlan | RunResult:
        return saved if operation == "verify" else _result(run_id, "apply", tmp_path)

    monkeypatch.setattr(managed_flow, "execute_run", core)
    if operation == "apply":
        managed_sync_run.fn(run_id, "inventory", "plan", resolve_config_version(instance))
        managed_sync_run.fn(
            run_id,
            "inventory",
            "apply",
            resolve_config_version(instance),
            expected_checksum=saved.manifest.plan_checksum,
            confirm_writes=True,
        )
    else:
        managed_sync_run.fn(
            run_id,
            "inventory",
            operation,
            resolve_config_version(instance),
            confirm_writes=operation == "sync",
        )
    return _observation("managed", operation, saved, projection, run_id)


@pytest.mark.parametrize("operation", ["plan", "apply", "sync"])
def test_executed_three_interface_product_envelopes_are_equal(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    operation: Operation,
) -> None:
    run_id = f"run-matrix-{operation}"
    observations: list[CanonicalEnvelope] = []
    for surface_runner in (_run_cli, _run_python, _run_managed_worker):
        with monkeypatch.context() as scoped:
            observations.append(surface_runner(scoped, tmp_path / surface_runner.__name__, operation, run_id))
    assert_equivalent(observations)


def test_python_and_managed_verify_are_equal_and_cli_review_consumes_the_same_plan(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    run_id = "run-matrix-verify"
    observations: list[CanonicalEnvelope] = []
    for surface in ("python", "managed"):
        root = (tmp_path / surface).resolve()
        instance = _instance(root)
        saved = _saved(run_id, instance)
        projection = local_product_projection(root / "products")
        _seed_plan(instance, saved, root / "products")
        with monkeypatch.context() as scoped:
            if surface == "python":
                scoped.setattr(
                    api_operations,
                    "resolve_sync_instance",
                    lambda *_args, _instance=instance, **_kwargs: _instance,
                )
                scoped.setattr(
                    api_operations,
                    "resolve_run_directory",
                    lambda *_args, _root=root, **_kwargs: _root / run_id,
                )
                scoped.setattr(
                    api_operations,
                    "execute_run",
                    lambda *_args, _saved=saved, **_kwargs: _saved,
                )
                api.verify(
                    api.VerifyRequest(
                        sync_name="inventory",
                        config_directory=str(root),
                        run_id=run_id,
                        product_cache_location=str(root / "products"),
                    )
                )
            else:
                scoped.setattr(
                    managed_flow,
                    "_runtime",
                    lambda _root=root, _projection=projection: (str(_root), _projection),
                )
                scoped.setattr(managed_flow, "_run_logger", lambda: (logging.getLogger("db006-matrix"), False))
                scoped.setattr(
                    managed_flow,
                    "resolve_sync_instance",
                    lambda *_args, _instance=instance, **_kwargs: _instance,
                )
                scoped.setattr(managed_flow, "collect_secret_values", lambda _instance=None: ())
                scoped.setattr(
                    managed_flow,
                    "execute_run",
                    lambda *_args, _saved=saved, **_kwargs: _saved,
                )
                managed_sync_run.fn(run_id, "inventory", "verify", resolve_config_version(instance))
        observations.append(_observation(surface, "verify", saved, projection, run_id))
    assert_equivalent(observations)

    cli_root = (tmp_path / "cli-review").resolve()
    cli_instance = _instance(cli_root)
    cli_saved = _saved(run_id, cli_instance)
    _seed_plan(cli_instance, cli_saved, cli_root / "products")
    with monkeypatch.context() as scoped:
        scoped.setattr("infrahub_sync.cli.get_instance", lambda **_kwargs: cli_instance)
        scoped.setattr("infrahub_sync.cli.execute_run", lambda *_args, **_kwargs: cli_saved)
        reviewed = CliRunner().invoke(
            app,
            [
                "diff",
                "--name",
                "inventory",
                "--from-plan",
                run_id,
                "--product-cache-location",
                str(cli_root / "products"),
            ],
        )
    assert reviewed.exit_code == 0, "".join(traceback.format_exception(reviewed.exception))
    assert "plan checksum" in reviewed.output
    assert local_product_projection(cli_root / "products").lookup_artifact(run_id, "plan-review").value is not None


def test_cli_plan_python_verify_and_cli_review_read_the_same_saved_plan_bytes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    run_id = "run-shared-saved-plan"
    instance = _instance(tmp_path)
    product_root = (tmp_path / "products").resolve()
    monkeypatch.setenv("INFRAHUB_SYNC_CACHE_DIR", str((tmp_path / "run-cache").resolve()))

    def write_plan(_instance: object, *, operation: Operation, **_kwargs: object) -> RunResult:
        assert operation == "plan"
        run_directory = resolve_run_directory(instance.name, run_id)
        manifest = write_plan_artifact(
            run_dir=run_directory,
            run_id=run_id,
            config_version=resolve_config_version(instance),
            source_snapshot=[],
            deletes_computed=True,
            operations=[],
        )
        assert manifest.run_id == run_id
        return _result(run_id, "plan", tmp_path)

    with monkeypatch.context() as scoped:
        scoped.setattr("infrahub_sync.cli.get_instance", lambda **_kwargs: instance)
        scoped.setattr("infrahub_sync.cli.execute_run", write_plan)
        planned = CliRunner().invoke(
            app,
            [
                "diff",
                "--name",
                "inventory",
                "--run-id",
                run_id,
                "--product-cache-location",
                str(product_root),
            ],
        )
    assert planned.exit_code == 0, "".join(traceback.format_exception(planned.exception))
    exact_bytes = (resolve_run_directory(instance.name, run_id) / "plan" / "manifest.json").read_bytes()

    with monkeypatch.context() as scoped:
        scoped.setattr(api_operations, "resolve_sync_instance", lambda *_args, **_kwargs: instance)
        verified = api.verify(
            api.VerifyRequest(
                sync_name="inventory",
                config_directory=str(tmp_path),
                run_id=run_id,
                product_cache_location=str(product_root),
            )
        )
    assert verified.outcome == "verified"
    assert (resolve_run_directory(instance.name, run_id) / "plan" / "manifest.json").read_bytes() == exact_bytes

    with monkeypatch.context() as scoped:
        scoped.setattr("infrahub_sync.cli.get_instance", lambda **_kwargs: instance)
        reviewed = CliRunner().invoke(
            app,
            [
                "diff",
                "--name",
                "inventory",
                "--from-plan",
                run_id,
                "--product-cache-location",
                str(product_root),
            ],
        )
    assert reviewed.exit_code == 0, "".join(traceback.format_exception(reviewed.exception))
    reread = read_saved_plan(sync_name=instance.name, run_id=run_id, config=instance)
    assert reread.manifest.plan_checksum in reviewed.output
    assert (resolve_run_directory(instance.name, run_id) / "plan" / "manifest.json").read_bytes() == exact_bytes


class _Orchestration:
    def __init__(self) -> None:
        self.submissions: list[dict[str, object]] = []

    async def submit(self, parameters: dict[str, object], *, idempotency_key: str) -> Submission:  # noqa: ARG002
        self.submissions.append(parameters)
        return Submission(flow_run_id=str(uuid4()), state="pending")

    async def observe(self, flow_run_id: str) -> Observation:  # noqa: ARG002, PLR6301
        return Observation(available=True, state="running")

    async def cancel(self, flow_run_id: str) -> Observation:  # noqa: ARG002, PLR6301
        return Observation(available=True, state="cancelling")


def _execute_submission(parameters: Mapping[str, object]) -> dict[str, object]:
    return managed_sync_run.fn(
        run_id=cast("str", parameters["run_id"]),
        sync_name=cast("str", parameters["sync_name"]),
        stage=cast('Literal["plan", "verify", "apply", "sync"]', parameters["stage"]),
        configuration_reference=cast("str", parameters["configuration_reference"]),
        branch=cast("str | None", parameters["branch"]),
        expected_checksum=cast("str | None", parameters["expected_checksum"]),
        confirm_writes=cast("bool", parameters["confirm_writes"]),
    )


def test_managed_http_prefect_parameters_and_all_worker_operations_are_executed_and_secret_safe(  # noqa: PLR0914
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv(PRINCIPALS_ENV, json.dumps({"owner": {"token": AUTH_TOKEN}}))
    resolver = EnvironmentPrincipalResolver.from_environment()
    projection = local_product_projection((tmp_path / "products").resolve())
    orchestration = _Orchestration()
    client = TestClient(
        create_app(ManagedRunService(projection, orchestration, secrets=resolver.secret_values), resolver)
    )
    instance = _instance(tmp_path)
    configuration_reference = resolve_config_version(instance)
    headers = {"Authorization": f"Bearer {AUTH_TOKEN}"}

    with caplog.at_level(logging.INFO):
        planned = client.post(
            "/runs",
            headers={**headers, "Idempotency-Key": "matrix-plan"},
            json={
                "sync_name": "inventory",
                "operation": "plan",
                "configuration_reference": configuration_reference,
                "reason": "matrix plan",
            },
        )
        assert planned.status_code == 202
        plan_parameters = orchestration.submissions[-1]
        plan_run_id = str(plan_parameters["run_id"])
        saved = _saved(plan_run_id, instance)
        monkeypatch.setattr(managed_flow, "_runtime", lambda: (str(tmp_path), projection))
        monkeypatch.setattr(managed_flow, "_run_logger", lambda: (logging.getLogger("db006-http-matrix"), False))
        monkeypatch.setattr(managed_flow, "resolve_sync_instance", lambda *_args, **_kwargs: instance)
        monkeypatch.setattr(managed_flow, "collect_secret_values", lambda _instance=None: (SENTINEL,))
        monkeypatch.setattr(managed_flow, "_plan", lambda *_args, **_kwargs: saved)
        monkeypatch.setattr(
            managed_flow,
            "execute_run",
            lambda *_args, operation, **_kwargs: (
                saved if operation == "verify" else _result(plan_run_id, "apply", tmp_path)
            ),
        )
        _execute_submission(plan_parameters)

        verified = client.post(
            f"/runs/{plan_run_id}/verify",
            headers={**headers, "Idempotency-Key": "matrix-verify"},
            json={"reason": "matrix verify"},
        )
        assert verified.status_code == 202
        _execute_submission(orchestration.submissions[-1])

        refused = client.post(
            f"/runs/{plan_run_id}/apply",
            headers={**headers, "Idempotency-Key": "matrix-apply-refused"},
            json={
                "reason": "matrix refused checksum",
                "confirm_writes": True,
                "expected_checksum": "b" * 64,
            },
        )
        assert refused.status_code == 409

        applied = client.post(
            f"/runs/{plan_run_id}/apply",
            headers={**headers, "Idempotency-Key": "matrix-apply"},
            json={
                "reason": "matrix apply",
                "confirm_writes": True,
                "expected_checksum": saved.manifest.plan_checksum,
            },
        )
        assert applied.status_code == 202
        _execute_submission(orchestration.submissions[-1])

        synced = client.post(
            "/runs",
            headers={**headers, "Idempotency-Key": "matrix-sync"},
            json={
                "sync_name": "inventory",
                "operation": "sync",
                "configuration_reference": configuration_reference,
                "reason": "matrix sync",
                "confirm_writes": True,
            },
        )
        assert synced.status_code == 202
        sync_parameters = orchestration.submissions[-1]
        sync_run_id = str(sync_parameters["run_id"])
        sync_saved = _saved(sync_run_id, instance)
        monkeypatch.setattr(managed_flow, "_plan", lambda *_args, **_kwargs: sync_saved)
        monkeypatch.setattr(
            managed_flow,
            "execute_run",
            lambda *_args, operation, **_kwargs: (
                sync_saved if operation == "verify" else _result(sync_run_id, "apply", tmp_path)
            ),
        )
        monkeypatch.setattr(managed_flow, "bounded_run_lock", lambda *_args, **_kwargs: nullcontext())
        sync_result = _execute_submission(sync_parameters)
        assert sync_result["operation"] == "sync"

    assert [parameters["stage"] for parameters in orchestration.submissions] == ["plan", "verify", "apply", "sync"]
    assert all(SENTINEL not in repr(parameters) for parameters in orchestration.submissions)
    records = [projection.lookup_run(plan_run_id).value, projection.lookup_run(sync_run_id).value]
    artifacts = [
        projection.lookup_artifact(plan_run_id, "plan-review").value,
        projection.lookup_artifact(sync_run_id, "plan-review").value,
    ]
    boundary = serialized_boundaries(
        planned.json(),
        verified.json(),
        refused.json(),
        applied.json(),
        synced.json(),
        orchestration.submissions,
        records,
        artifacts,
        caplog.text,
    )
    assert AUTH_TOKEN.encode() not in boundary
    assert SENTINEL.encode() not in boundary

    # HTTP necessarily adds actor/audit/Prefect correlation fields. The lossless oracle
    # retains them, so direct worker and HTTP records are not falsely declared equal.
    assert records[0] is not None
    assert records[0].actor == "owner"
    assert records[0].audit_links
    assert records[0].prefect_executions
