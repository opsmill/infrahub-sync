"""Executable CLI, Python, managed-worker, and managed HTTP/Prefect matrix."""

from __future__ import annotations

import json
import logging
import traceback
from collections.abc import Mapping
from contextlib import nullcontext
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, cast
from uuid import NAMESPACE_URL, uuid4, uuid5

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
from infrahub_sync.managed.orchestration import CancellationResult, Observation, PoolStatus, Submission
from infrahub_sync.managed.service import ManagedRunService
from infrahub_sync.plan.config_version import resolve_config_version
from infrahub_sync.plan.identity import operation_id
from infrahub_sync.plan.models import PlanManifest, PlannedOperation
from infrahub_sync.plan.review import SavedPlan, read_saved_plan, resolve_run_directory
from infrahub_sync.plan.writer import write_plan_artifact
from infrahub_sync.product_store import PrefectExecutionLink, ProductProjection, ProductRun, local_product_projection
from infrahub_sync.product_store.standalone import execute_standalone
from tests.configuration.validation_packages import package, package_data
from tests.conformance.interface_adapters import (
    cli_product_envelope,
    managed_product_envelope,
    python_product_envelope,
    serialized_boundaries,
)
from tests.conformance.oracle import CanonicalEnvelope, assert_equivalent

if TYPE_CHECKING:
    from collections.abc import Callable
    from typing import Literal

SENTINEL = "db006-interface-sentinel-credential"
AUTH_TOKEN = "db006-managed-owner-token"  # noqa: S105 - deliberate non-secret canary.


@pytest.fixture(autouse=True)
def _executor_only_claim(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give direct managed-worker matrix calls one real durable claim."""
    worker_id = "8c1da53d-0e6b-4d3d-a0f1-97b6a9ccebf0"

    def claim(projection: ProductProjection, run_id: str) -> tuple[str, str]:
        run = projection.lookup_run(run_id).value
        assert run is not None
        link = next(
            (
                candidate
                for candidate in reversed(run.prefect_executions)
                if candidate.claimed_at is None
                and candidate.terminal_at is None
                and candidate.cancellation_requested_at is None
            ),
            None,
        )
        if link is None:
            ordinal = len(run.prefect_executions) + 1
            link = projection.add_prefect_execution(
                run_id,
                PrefectExecutionLink(
                    flow_run_id=str(uuid5(NAMESPACE_URL, f"{run_id}:{ordinal}")),
                    purpose=f"matrix-{ordinal}",
                    attempt=1,
                ),
            )
        assert projection.claim_execution(run_id, link.flow_run_id, worker_id=worker_id)
        return link.flow_run_id, worker_id

    monkeypatch.setattr(managed_flow, "_claim_current_execution", claim)


@dataclass
class _DestinationProbe:
    created_objects: set[str] = field(default_factory=set)
    updated_objects: set[str] = field(default_factory=set)
    deleted_objects: set[str] = field(default_factory=set)

    def apply(self, saved: SavedPlan) -> None:
        """Mutate isolated destination state from the reviewed operations, not the return."""
        for operation in saved.operations():
            targets = {
                "create": self.created_objects,
                "update": self.updated_objects,
                "delete": self.deleted_objects,
            }[operation.action]
            targets.add(operation.operation_id)

    def measure(self) -> Mapping[str, int]:
        return {
            "created": len(self.created_objects),
            "updated": len(self.updated_objects),
            "deleted": len(self.deleted_objects),
        }

    def snapshot(self) -> _DestinationProbe:
        return type(self)(
            created_objects=set(self.created_objects),
            updated_objects=set(self.updated_objects),
            deleted_objects=set(self.deleted_objects),
        )


def _instance(tmp_path: Path) -> SyncInstance:
    return SyncInstance(
        name="inventory",
        directory=str(tmp_path),
        source=SyncAdapter(name="source", settings={"token": SENTINEL}),
        destination=SyncAdapter(name="destination", settings={}),
    )


def _saved(run_id: str, instance: SyncInstance, binding: tuple[str, int, str] | None = None) -> SavedPlan:
    identity = {"name": "edge-1"}
    operation = PlannedOperation(
        operation_id=operation_id("create", "DcimDevice", identity),
        action="create",
        kind="DcimDevice",
        identity=identity,
        tier=0,
        payload=identity,
    )
    return SavedPlan(
        manifest=PlanManifest(
            format_version=2,
            run_id=run_id,
            created_at="2026-08-10T12:00:00+00:00",
            config_version=resolve_config_version(instance),
            source_snapshot=[],
            operations_count=1,
            delete_operations_computed=True,
            plan_checksum="a" * 64,
            config_id=None if binding is None else binding[0],
            registry_version=None if binding is None else binding[1],
            package_checksum=None if binding is None else binding[2],
        ),
        operations=[operation],
        checksum_ok=True,
        verification_notes=[],
    )


def _register_inventory(projection: ProductProjection) -> tuple[str, int, str]:
    declared = package_data()
    declared["configuration"]["name"] = "inventory"
    registered = projection.create_configuration(package(declared))
    return registered.config_id, registered.registry_version, registered.package_checksum


def _result(run_id: str, operation: Operation, tmp_path: Path) -> RunResult:
    outcome = "planned" if operation == "plan" else "applied"
    return RunResult(
        sync_name="inventory",
        operation=operation,
        run_id=run_id,
        status=outcome,
        changed=True,
        summary={"create": 1, "update": 0, "delete": 0},
        artifact_path=str(tmp_path / run_id),
    )


def _product_parts(
    projection: ProductProjection,
    run_id: str,
) -> tuple[ProductRun, bytes]:
    record = projection.lookup_run(run_id).value
    artifact = projection.lookup_artifact(run_id, "plan-review").value
    assert record is not None
    assert artifact is not None
    return record, artifact


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
    caplog: pytest.LogCaptureFixture,
) -> CanonicalEnvelope:
    instance = _instance(tmp_path)
    saved = _saved(run_id, instance)
    root = (tmp_path / "cli-products").resolve()
    destination = _DestinationProbe()
    captured: list[RunResult] = []
    if operation == "apply":
        _seed_plan(instance, saved, root)

    def core(_instance: object, *, operation: Operation, **kwargs: object) -> RunResult:
        if operation == "sync":
            callback = cast("Callable[[], None]", kwargs["_plan_committed"])
            callback()
        result = _result(run_id, operation, tmp_path)
        captured.append(result)
        if operation in {"apply", "sync"}:
            destination.apply(saved)
        return result

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

    caplog.clear()
    with caplog.at_level(logging.INFO):
        invoked = CliRunner().invoke(app, arguments)
    assert invoked.exit_code == 0, invoked.output
    assert len(captured) == 1
    rendering = "\n".join((invoked.output, *(record.getMessage() for record in caplog.records)))
    record, artifact = _product_parts(local_product_projection(root), run_id)
    return cli_product_envelope(
        core_result=captured[0],
        exit_code=invoked.exit_code,
        rendering=rendering,
        record=record,
        artifact=artifact,
        destination=destination,
    )


def _run_python(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    operation: Operation,
    run_id: str,
) -> CanonicalEnvelope:
    instance = _instance(tmp_path)
    saved = _saved(run_id, instance)
    root = (tmp_path / "python-products").resolve()
    destination = _DestinationProbe()
    if operation == "apply":
        _seed_plan(instance, saved, root)

    def core(_instance: object, *, operation: Operation, **_kwargs: object) -> SavedPlan | RunResult:
        if operation in {"plan", "verify"}:
            return saved
        result = _result(run_id, "apply", tmp_path)
        destination.apply(saved)
        return result

    monkeypatch.setattr(api_operations, "generate_run_id", lambda: run_id)
    monkeypatch.setattr(api_operations, "resolve_sync_instance", lambda *_args, **_kwargs: instance)
    monkeypatch.setattr(api_operations, "resolve_run_directory", lambda *_args, **_kwargs: tmp_path / run_id)
    monkeypatch.setattr(api_operations, "read_saved_plan", lambda **_kwargs: saved)
    monkeypatch.setattr(api_operations, "execute_run", core)
    monkeypatch.setattr(api_operations, "bounded_run_lock", lambda *_args, **_kwargs: nullcontext())
    if operation == "plan":
        public_result = api.plan(
            api.PlanRequest(sync_name="inventory", config_directory=str(tmp_path), product_cache_location=str(root))
        )
    elif operation == "apply":
        public_result = api.apply(
            api.ApplyRequest(
                sync_name="inventory",
                config_directory=str(tmp_path),
                run_id=run_id,
                expected_checksum=saved.manifest.plan_checksum,
                product_cache_location=str(root),
            )
        )
    else:
        public_result = api.sync(
            api.SyncRequest(
                sync_name="inventory",
                config_directory=str(tmp_path),
                confirm_writes=True,
                product_cache_location=str(root),
            )
        )
    record, artifact = _product_parts(local_product_projection(root), run_id)
    return python_product_envelope(
        public_result=public_result,
        record=record,
        artifact=artifact,
        destination=destination,
    )


def _run_managed_worker(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    operation: Operation,
    run_id: str,
) -> CanonicalEnvelope:
    instance = _instance(tmp_path)
    saved = _saved(run_id, instance)
    root = (tmp_path / "managed-products").resolve()
    destination = _DestinationProbe()
    projection = local_product_projection(root)
    binding = _register_inventory(projection)
    saved = _saved(run_id, instance, binding)
    projection.create_run(
        ProductRun(
            run_id=run_id,
            operation="plan" if operation == "apply" else operation,
            configuration_reference=f"{binding[0]}@{binding[1]}",
            config_id=binding[0],
            registry_version=binding[1],
            package_checksum=binding[2],
            started_at=datetime.now(timezone.utc),
            phase="accepted",
            summary={"sync_name": "inventory"},
        )
    )
    monkeypatch.setattr(managed_flow, "_runtime", lambda: (str(tmp_path), projection))
    monkeypatch.setattr(managed_flow, "_run_logger", lambda: (logging.getLogger("db006-matrix"), False))
    monkeypatch.setattr(managed_flow, "resolve_runtime_instance", lambda *_args, **_kwargs: instance)
    monkeypatch.setattr(managed_flow, "collect_secret_values", lambda _instance=None: ())
    monkeypatch.setattr(managed_flow, "bounded_run_lock", lambda *_args, **_kwargs: nullcontext())
    monkeypatch.setattr(managed_flow, "_plan", lambda *_args, **_kwargs: saved)
    monkeypatch.setattr(managed_flow, "_verify_registered_apply", lambda **_kwargs: None)

    def core(_instance: object, *, operation: Operation, **_kwargs: object) -> SavedPlan | RunResult:
        if operation == "verify":
            return saved
        result = _result(run_id, "apply", tmp_path)
        destination.apply(saved)
        return result

    monkeypatch.setattr(managed_flow, "execute_run", core)
    if operation == "apply":
        managed_sync_run.fn(run_id, "plan", *binding)
        worker_result = managed_sync_run.fn(
            run_id,
            "apply",
            *binding,
            expected_checksum=saved.manifest.plan_checksum,
            confirm_writes=True,
        )
    else:
        worker_result = managed_sync_run.fn(
            run_id,
            operation,
            *binding,
            confirm_writes=operation == "sync",
        )
    record, artifact = _product_parts(projection, run_id)
    return managed_product_envelope(
        worker_result=worker_result,
        record=record,
        artifact=artifact,
        destination=destination,
    )


@pytest.mark.parametrize("operation", ["plan", "apply", "sync"])
def test_executed_three_interface_product_envelopes_are_equal(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    operation: Operation,
    caplog: pytest.LogCaptureFixture,
) -> None:
    run_id = f"run-matrix-{operation}"
    with monkeypatch.context() as scoped:
        cli = _run_cli(scoped, tmp_path / "cli", operation, run_id, caplog)
    with monkeypatch.context() as scoped:
        python = _run_python(scoped, tmp_path / "python", operation, run_id)
    with monkeypatch.context() as scoped:
        managed = _run_managed_worker(scoped, tmp_path / "managed", operation, run_id)
    managed_record = cast("dict[str, object]", managed.product_record)
    assert all(managed_record[key] is not None for key in ("config_id", "registry_version", "package_checksum"))
    managed_links = cast("list[dict[str, object]]", managed_record["prefect_executions"])
    assert managed_links
    assert all(link["terminal_state"] == "completed" for link in managed_links)
    # Standalone interfaces remain legacy by contract. Compare their common product
    # semantics after separately proving managed-only binding and execution liveness.
    managed_record["configuration_reference"] = cli.product_record["configuration_reference"]
    for key in ("config_id", "registry_version", "package_checksum"):
        managed_record[key] = None
    managed_record["prefect_executions"] = []
    observations = [cli, python, managed]
    assert_equivalent(observations)


@pytest.mark.parametrize("mutation", ["returned-count", "returned-outcome", "returned-operation", "destination"])
def test_interface_adapter_mutations_cannot_false_pass(
    tmp_path: Path,
    mutation: str,
) -> None:
    run_id = "run-adapter-mutation"
    instance = _instance(tmp_path)
    saved = _saved(run_id, instance)
    projection = local_product_projection((tmp_path / "products").resolve())
    _seed_plan(instance, saved, tmp_path / "products")
    record, artifact = _product_parts(projection, run_id)
    core_result = _result(run_id, "plan", tmp_path)
    public_result = api.RunResult(
        run_id=run_id,
        operation="plan",
        phase="completed",
        outcome="planned",
        counts=api.ActionCounts(create=1),
        domain_summary={"DcimDevice": 1},
        artifacts=(),
    )
    worker_result: dict[str, object] = {
        "run_id": run_id,
        "stage": "plan",
        "outcome": "planned",
        "summary": saved.summary().model_dump(mode="json"),
    }
    expected = cli_product_envelope(
        core_result=core_result,
        exit_code=0,
        rendering="plan completed",
        record=record,
        artifact=artifact,
        destination=_DestinationProbe(),
    )

    destination = _DestinationProbe()
    if mutation == "returned-count":
        public_result = public_result.model_copy(update={"counts": api.ActionCounts(create=99)})
        observed = python_product_envelope(
            public_result=public_result,
            record=record,
            artifact=artifact,
            destination=destination,
        )
    elif mutation == "returned-outcome":
        worker_result["outcome"] = "failed"
        observed = managed_product_envelope(
            worker_result=worker_result,
            record=record,
            artifact=artifact,
            destination=destination,
        )
    elif mutation == "returned-operation":
        worker_result["operation"] = "apply"
        observed = managed_product_envelope(
            worker_result=worker_result,
            record=record,
            artifact=artifact,
            destination=destination,
        )
    else:
        destination.created_objects.add("unexpected-destination-object")
        observed = managed_product_envelope(
            worker_result=worker_result,
            record=record,
            artifact=artifact,
            destination=destination,
        )

    with pytest.raises(AssertionError, match="canonical interface disagreement"):
        assert_equivalent([expected, observed])


def test_python_and_managed_verify_common_fields_and_cli_review_consume_the_same_plan(  # noqa: PLR0914
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    run_id = "run-matrix-verify"
    common_results: list[dict[str, object]] = []
    verification_results: list[object] = []
    artifacts: list[object] = []
    for surface in ("python", "managed"):
        root = (tmp_path / surface).resolve()
        instance = _instance(root)
        saved = _saved(run_id, instance)
        projection = local_product_projection(root / "products")
        with monkeypatch.context() as scoped:
            if surface == "python":
                _seed_plan(instance, saved, root / "products")
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
                public_result = api.verify(
                    api.VerifyRequest(
                        sync_name="inventory",
                        config_directory=str(root),
                        run_id=run_id,
                        product_cache_location=str(root / "products"),
                    )
                )
            else:
                binding = _register_inventory(projection)
                saved = _saved(run_id, instance, binding)
                projection.create_run(
                    ProductRun(
                        run_id=run_id,
                        operation="plan",
                        configuration_reference=f"{binding[0]}@{binding[1]}",
                        config_id=binding[0],
                        registry_version=binding[1],
                        package_checksum=binding[2],
                        started_at=datetime.now(timezone.utc),
                        phase="accepted",
                        summary={"sync_name": "inventory"},
                    )
                )
                managed_flow._publish_plan(projection, run_id, saved, ())
                scoped.setattr(
                    managed_flow,
                    "_runtime",
                    lambda _root=root, _projection=projection: (str(_root), _projection),
                )
                scoped.setattr(managed_flow, "_run_logger", lambda: (logging.getLogger("db006-matrix"), False))
                scoped.setattr(
                    managed_flow,
                    "resolve_runtime_instance",
                    lambda *_args, _instance=instance, **_kwargs: _instance,
                )
                scoped.setattr(managed_flow, "collect_secret_values", lambda _instance=None: ())
                scoped.setattr(
                    managed_flow,
                    "execute_run",
                    lambda *_args, _saved=saved, **_kwargs: _saved,
                )
                worker_result = managed_sync_run.fn(
                    run_id,
                    "verify",
                    *binding,
                )
        record, artifact = _product_parts(projection, run_id)
        if surface == "python":
            common_results.append(
                {
                    "run_id": public_result.run_id,
                    "operation": public_result.operation,
                    "outcome": public_result.outcome,
                }
            )
        else:
            common_results.append(
                {
                    "run_id": worker_result["run_id"],
                    "operation": worker_result["stage"],
                    "outcome": worker_result["outcome"],
                }
            )
        verification_results.append(record.results["verification"])
        artifacts.append(json.loads(artifact))
    assert common_results[0] == common_results[1]
    assert verification_results[0] == verification_results[1]
    assert artifacts[0] == artifacts[1]

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

    async def pool_status(self, work_pool_name: str, now: datetime) -> PoolStatus:  # noqa: ARG002, PLR6301
        return PoolStatus(detail_available=False, queue_depth=None, observed_at=None)

    async def cancel(self, flow_run_id: str) -> CancellationResult:  # noqa: ARG002, PLR6301
        return CancellationResult(acknowledged=True)


def _execute_submission(parameters: Mapping[str, object]) -> dict[str, object]:
    return managed_sync_run.fn(
        run_id=cast("str", parameters["run_id"]),
        stage=cast('Literal["plan", "verify", "apply", "sync"]', parameters["stage"]),
        config_id=cast("str", parameters["config_id"]),
        registry_version=cast("int", parameters["registry_version"]),
        package_checksum=cast("str", parameters["package_checksum"]),
        branch=cast("str | None", parameters["branch"]),
        expected_checksum=cast("str | None", parameters["expected_checksum"]),
        confirm_writes=cast("bool", parameters["confirm_writes"]),
    )


def test_managed_http_prefect_parameters_and_all_worker_operations_are_executed_and_secret_safe(  # noqa: PLR0914, PLR0915
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv(PRINCIPALS_ENV, json.dumps({"owner": {"token": AUTH_TOKEN}}))
    resolver = EnvironmentPrincipalResolver.from_environment()
    projection = local_product_projection((tmp_path / "products").resolve())
    binding = _register_inventory(projection)
    orchestration = _Orchestration()
    client = TestClient(
        create_app(ManagedRunService(projection, orchestration, secrets=resolver.secret_values), resolver)
    )
    instance = _instance(tmp_path)
    plan_destination = _DestinationProbe()
    sync_destination = _DestinationProbe()
    headers = {"Authorization": f"Bearer {AUTH_TOKEN}"}

    with caplog.at_level(logging.INFO):
        planned = client.post(
            "/runs",
            headers={**headers, "Idempotency-Key": "matrix-plan"},
            json={
                "operation": "plan",
                "config_id": binding[0],
                "registry_version": binding[1],
                "reason": "matrix plan",
            },
        )
        assert planned.status_code == 202
        plan_parameters = orchestration.submissions[-1]
        plan_run_id = str(plan_parameters["run_id"])
        saved = _saved(plan_run_id, instance, binding)
        monkeypatch.setattr(managed_flow, "_runtime", lambda: (str(tmp_path), projection))
        monkeypatch.setattr(managed_flow, "_run_logger", lambda: (logging.getLogger("db006-http-matrix"), False))
        monkeypatch.setattr(managed_flow, "resolve_runtime_instance", lambda *_args, **_kwargs: instance)
        monkeypatch.setattr(managed_flow, "collect_secret_values", lambda _instance=None: (SENTINEL,))
        monkeypatch.setattr(managed_flow, "_plan", lambda *_args, **_kwargs: saved)
        monkeypatch.setattr(managed_flow, "_verify_registered_apply", lambda **_kwargs: None)

        def plan_core(*_args: object, operation: Operation, **_kwargs: object) -> SavedPlan | RunResult:
            if operation == "verify":
                return saved
            result = _result(plan_run_id, "apply", tmp_path)
            plan_destination.apply(saved)
            return result

        monkeypatch.setattr(managed_flow, "execute_run", plan_core)
        plan_worker_result = _execute_submission(plan_parameters)
        plan_destination_after_plan = plan_destination.snapshot()

        verified = client.post(
            f"/runs/{plan_run_id}/verify",
            headers={**headers, "Idempotency-Key": "matrix-verify"},
            json={"reason": "matrix verify"},
        )
        assert verified.status_code == 202
        verify_worker_result = _execute_submission(orchestration.submissions[-1])

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
        apply_worker_result = _execute_submission(orchestration.submissions[-1])

        synced = client.post(
            "/runs",
            headers={**headers, "Idempotency-Key": "matrix-sync"},
            json={
                "operation": "sync",
                "config_id": binding[0],
                "registry_version": binding[1],
                "reason": "matrix sync",
                "confirm_writes": True,
            },
        )
        assert synced.status_code == 202
        sync_parameters = orchestration.submissions[-1]
        sync_run_id = str(sync_parameters["run_id"])
        sync_saved = _saved(sync_run_id, instance, binding)
        monkeypatch.setattr(managed_flow, "_plan", lambda *_args, **_kwargs: sync_saved)

        def sync_core(*_args: object, operation: Operation, **_kwargs: object) -> SavedPlan | RunResult:
            if operation == "verify":
                return sync_saved
            result = _result(sync_run_id, "apply", tmp_path)
            sync_destination.apply(sync_saved)
            return result

        monkeypatch.setattr(managed_flow, "execute_run", sync_core)
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
    assert records[0] is not None
    assert records[1] is not None
    assert artifacts[0] is not None
    assert artifacts[1] is not None
    plan_envelope = managed_product_envelope(
        worker_result=plan_worker_result,
        record=records[0],
        artifact=artifacts[0],
        destination=plan_destination_after_plan,
    )
    apply_envelope = managed_product_envelope(
        worker_result=apply_worker_result,
        record=records[0],
        artifact=artifacts[0],
        destination=plan_destination,
    )
    sync_envelope = managed_product_envelope(
        worker_result=sync_result,
        record=records[1],
        artifact=artifacts[1],
        destination=sync_destination,
    )
    assert plan_envelope.counts == {"create": 1, "update": 0, "delete": 0}
    assert plan_envelope.destination_effects == {"created": 0, "updated": 0, "deleted": 0}
    assert apply_envelope.destination_effects["created"] == 1
    assert sync_envelope.operation == "sync"
    assert sync_envelope.destination_effects["created"] == 1
    boundary = serialized_boundaries(
        planned.json(),
        verified.json(),
        refused.json(),
        applied.json(),
        synced.json(),
        orchestration.submissions,
        plan_worker_result,
        verify_worker_result,
        apply_worker_result,
        sync_result,
        records,
        artifacts,
        caplog.text,
    )
    assert AUTH_TOKEN.encode() not in boundary
    assert SENTINEL.encode() not in boundary

    # HTTP necessarily adds actor/audit/Prefect correlation fields. The lossless oracle
    # retains them, so direct worker and HTTP records are not falsely declared equal.
    assert records[0].actor == "owner"
    assert records[0].audit_links
    assert records[0].prefect_executions
