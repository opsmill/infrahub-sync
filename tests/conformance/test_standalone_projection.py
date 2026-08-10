"""Matrix cases proving standalone entry paths consume the DB-003 projection."""

from __future__ import annotations

import json
import logging
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path

import pytest
from typer.testing import CliRunner

from infrahub_sync import SyncAdapter, SyncInstance
from infrahub_sync.api import v1 as api
from infrahub_sync.api.v1 import _operations as api_operations  # noqa: PLC2701 - integration seam under test.
from infrahub_sync.cli import app
from infrahub_sync.execution import RunResult
from infrahub_sync.plan.models import PlanManifest
from infrahub_sync.plan.review import SavedPlan
from infrahub_sync.product_store import local_product_projection
from infrahub_sync.product_store.standalone import StandaloneProductRecordError, execute_standalone


def _saved(run_id: str) -> SavedPlan:
    return SavedPlan(
        manifest=PlanManifest(
            format_version=2,
            run_id=run_id,
            created_at="2026-08-10T12:00:00+00:00",
            config_version="configuration-v1",
            source_snapshot=[],
            operations_count=0,
            delete_operations_computed=True,
            plan_checksum="a" * 64,
        ),
        operations=[],
        checksum_ok=True,
        verification_notes=[],
    )


def _instance(tmp_path: Path) -> SyncInstance:
    return SyncInstance(
        name="inventory",
        directory=str(tmp_path),
        source=SyncAdapter(name="source", settings={}),
        destination=SyncAdapter(name="destination", settings={}),
        store=None,
    )


def test_configured_standalone_plan_publishes_the_managed_product_contract(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    run_id = "run-conformance-plan"
    saved = _saved(run_id)

    def core(_instance: object, *, operation: str, **kwargs: object) -> SavedPlan:
        assert operation == "plan"
        assert kwargs["run_id"] == run_id
        return saved

    monkeypatch.setattr("infrahub_sync.product_store.standalone.execute_run", core)

    returned = execute_standalone(
        _instance(tmp_path),
        operation="plan",
        run_id=run_id,
        product_cache_location=tmp_path / "products",
        _return_saved_plan=True,
    )

    assert returned is saved
    projection = local_product_projection((tmp_path / "products").resolve())
    record = projection.lookup_run(run_id).value
    assert record is not None
    assert record.operation == "plan"
    assert record.phase == "planned"
    assert record.outcome == "no-change"
    assert record.prefect_executions == ()
    assert [artifact.artifact_id for artifact in record.artifact_refs] == ["plan-review"]
    artifact = projection.lookup_artifact(run_id, "plan-review").value
    assert artifact is not None
    document = json.loads(artifact)
    assert document == {
        "run_id": run_id,
        "checksum": "a" * 64,
        "checksum_ok": True,
        "verification_notes": [],
        "summary": {
            "by_action": {},
            "by_kind": {},
            "total": 0,
            "delete_operations_computed": True,
            "deletes_not_executed": 0,
        },
        "operations": [],
    }


def test_configured_standalone_apply_extends_the_planning_record(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    run_id = "run-conformance-apply"
    cache = (tmp_path / "products").resolve()
    instance = _instance(tmp_path)
    saved = _saved(run_id)

    monkeypatch.setattr("infrahub_sync.product_store.standalone.execute_run", lambda *_args, **_kwargs: saved)
    execute_standalone(
        instance,
        operation="plan",
        run_id=run_id,
        product_cache_location=cache,
        _return_saved_plan=True,
    )

    applied = RunResult(
        sync_name="inventory",
        operation="apply",
        run_id=run_id,
        status="no-change",
        changed=False,
        summary={"create": 0, "update": 0, "delete": 0},
        artifact_path=str((tmp_path / run_id).resolve()),
    )
    monkeypatch.setattr("infrahub_sync.product_store.standalone.execute_run", lambda *_args, **_kwargs: applied)
    monkeypatch.setattr("infrahub_sync.product_store.standalone.read_saved_plan", lambda **_kwargs: saved)

    returned = execute_standalone(
        instance,
        operation="apply",
        run_id=run_id,
        confirm_writes=True,
        product_cache_location=cache,
    )

    assert returned is applied
    record = local_product_projection(cache).lookup_run(run_id).value
    assert record is not None
    assert record.operation == "plan"
    assert record.phase == "applied"
    assert record.outcome == "no-change"
    assert record.results["operation"] == "apply"
    assert len(record.artifact_refs) == 1
    assert record.finished_at is not None
    assert record.started_at <= datetime.now(timezone.utc)


def test_configured_plan_verify_apply_survives_projection_restart(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    run_id = "run-conformance-restart"
    cache = (tmp_path / "products").resolve()
    instance = _instance(tmp_path)
    saved = _saved(run_id)

    monkeypatch.setattr("infrahub_sync.product_store.standalone.execute_run", lambda *_args, **_kwargs: saved)
    execute_standalone(
        instance,
        operation="plan",
        run_id=run_id,
        product_cache_location=cache,
        _return_saved_plan=True,
    )
    before_verify = local_product_projection(cache).lookup_run(run_id).value
    assert before_verify is not None

    verified = execute_standalone(
        instance,
        operation="verify",
        run_id=run_id,
        product_cache_location=cache,
        _require_verified=True,
    )
    assert verified is saved
    after_verify = local_product_projection(cache).lookup_run(run_id).value
    assert after_verify is not None
    assert after_verify.phase == before_verify.phase
    assert after_verify.finished_at == before_verify.finished_at
    assert after_verify.results["verification"]["outcome"] == "verified"

    applied = RunResult(
        sync_name="inventory",
        operation="apply",
        run_id=run_id,
        status="no-change",
        changed=False,
        summary={"create": 0, "update": 0, "delete": 0},
        artifact_path=str((tmp_path / run_id).resolve()),
    )
    monkeypatch.setattr("infrahub_sync.product_store.standalone.execute_run", lambda *_args, **_kwargs: applied)
    returned = execute_standalone(
        instance,
        operation="apply",
        run_id=run_id,
        confirm_writes=True,
        product_cache_location=cache,
    )
    assert returned is applied
    restarted = local_product_projection(cache).lookup_run(run_id).value
    assert restarted is not None
    assert restarted.phase == "applied"
    assert restarted.results["operation"] == "apply"


def test_configured_standalone_failure_is_typed_durable_and_secret_safe(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    run_id = "run-conformance-failure"
    cache = (tmp_path / "products").resolve()
    secret = "db006-secret-sentinel-credential"  # noqa: S105 - deliberate non-secret canary.
    instance = _instance(tmp_path)
    assert instance.source.settings is not None
    instance.source.settings["token"] = secret

    def fail(*_args: object, **_kwargs: object) -> None:
        msg = f"adapter rejected {secret}"
        raise RuntimeError(msg)

    monkeypatch.setattr("infrahub_sync.product_store.standalone.execute_run", fail)
    with pytest.raises(RuntimeError, match=secret):
        execute_standalone(
            instance,
            operation="plan",
            run_id=run_id,
            product_cache_location=cache,
            _return_saved_plan=True,
        )

    record = local_product_projection(cache).lookup_run(run_id).value
    assert record is not None
    assert record.phase == "plan-failed"
    assert record.outcome == "failed"
    assert record.results["plan_failure"]["error_type"] == "RuntimeError"
    boundary = repr(record)
    database = (cache / "product-records.sqlite3").read_bytes()
    assert secret not in boundary
    assert secret.encode() not in database


def test_python_plan_request_projects_the_same_record_and_review_artifact(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    run_id = "run-python-plan"
    cache = (tmp_path / "python-products").resolve()
    config_root = tmp_path / "configs"
    sync_root = config_root / "inventory"
    sync_root.mkdir(parents=True)
    (sync_root / "config.yml").write_text(
        """name: inventory
source:
  name: source
destination:
  name: destination
""",
        encoding="utf-8",
    )
    instance = SyncInstance(
        name="inventory",
        directory=str(sync_root),
        source=SyncAdapter(name="source"),
        destination=SyncAdapter(name="destination"),
    )
    saved = _saved(run_id)
    monkeypatch.setattr(api_operations, "generate_run_id", lambda: run_id)
    monkeypatch.setattr(api_operations, "resolve_sync_instance", lambda *_args, **_kwargs: instance)
    monkeypatch.setattr(api_operations, "execute_run", lambda *_args, **_kwargs: saved)
    monkeypatch.setattr(api_operations, "resolve_run_directory", lambda *_args, **_kwargs: tmp_path / run_id)

    result = api.plan(
        api.PlanRequest(
            sync_name="inventory",
            config_directory=str(config_root),
            product_cache_location=str(cache),
        )
    )

    assert result.run_id == run_id
    record = local_product_projection(cache).lookup_run(run_id).value
    assert record is not None
    assert record.results["stage"] == "plan"
    assert [reference.artifact_id for reference in record.artifact_refs] == ["plan-review"]


def test_cli_plan_accepts_explicit_product_cache_configuration(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    run_id = "run-cli-plan"
    cache = (tmp_path / "cli-products").resolve()
    config_root = tmp_path / "configs"
    sync_root = config_root / "inventory"
    sync_root.mkdir(parents=True)
    (sync_root / "config.yml").write_text(
        """name: inventory
source:
  name: source
destination:
  name: destination
""",
        encoding="utf-8",
    )
    saved = _saved(run_id)
    core_result = RunResult(
        sync_name="inventory",
        operation="plan",
        run_id=run_id,
        status="no-change",
        changed=False,
        summary={"create": 0, "update": 0, "delete": 0},
        artifact_path=str((tmp_path / run_id).resolve()),
    )
    monkeypatch.setattr("infrahub_sync.cli.execute_run", lambda *_args, **_kwargs: core_result)
    monkeypatch.setattr("infrahub_sync.product_store.standalone.read_saved_plan", lambda **_kwargs: saved)

    invocation = CliRunner().invoke(
        app,
        [
            "diff",
            "--name",
            "inventory",
            "--directory",
            str(config_root),
            "--run-id",
            run_id,
            "--product-cache-location",
            str(cache),
        ],
    )

    assert invocation.exit_code == 0, invocation.output
    record = local_product_projection(cache).lookup_run(run_id).value
    assert record is not None
    assert record.operation == "plan"
    assert record.phase == "planned"


def test_cli_review_renders_a_configured_product_record_refusal_without_a_traceback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    config_root = tmp_path / "configs"
    sync_root = config_root / "inventory"
    sync_root.mkdir(parents=True)
    (sync_root / "config.yml").write_text(
        """name: inventory
source:
  name: source
destination:
  name: destination
""",
        encoding="utf-8",
    )

    def refuse(*_args: object, **_kwargs: object) -> None:
        message = "configured product record is unavailable"
        raise StandaloneProductRecordError(message)

    monkeypatch.setattr("infrahub_sync.cli.execute_standalone", refuse)
    with caplog.at_level(logging.ERROR, logger="infrahub_sync.cli"):
        invocation = CliRunner().invoke(
            app,
            [
                "diff",
                "--name",
                "inventory",
                "--directory",
                str(config_root),
                "--from-plan",
                "run-cli-review",
                "--product-cache-location",
                str((tmp_path / "products").resolve()),
            ],
        )

    assert invocation.exit_code == 1
    assert "configured product record is unavailable" in caplog.text
    assert "Traceback" not in invocation.output


def test_python_confirmed_sync_keeps_one_product_identity_across_all_stages(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    run_id = "run-python-sync"
    cache = (tmp_path / "python-sync-products").resolve()
    instance = _instance(tmp_path)
    saved = _saved(run_id)
    calls: list[str] = []

    def core(_instance: object, *, operation: str, **_kwargs: object) -> SavedPlan | RunResult:
        calls.append(operation)
        if operation in {"plan", "verify"}:
            return saved
        return RunResult(
            sync_name="inventory",
            operation="apply",
            run_id=run_id,
            status="no-change",
            changed=False,
            summary={"create": 0, "update": 0, "delete": 0},
            artifact_path=str((tmp_path / run_id).resolve()),
        )

    monkeypatch.setattr(api_operations, "generate_run_id", lambda: run_id)
    monkeypatch.setattr(api_operations, "resolve_sync_instance", lambda *_args, **_kwargs: instance)
    monkeypatch.setattr(api_operations, "resolve_run_directory", lambda *_args, **_kwargs: tmp_path / run_id)
    monkeypatch.setattr(api_operations, "execute_run", core)
    monkeypatch.setattr(api_operations, "bounded_run_lock", lambda *_args, **_kwargs: nullcontext())

    result = api.sync(
        api.SyncRequest(
            sync_name="inventory",
            config_directory=str(tmp_path),
            confirm_writes=True,
            product_cache_location=str(cache),
        )
    )

    assert result.operation == "sync"
    assert calls == ["plan", "verify", "apply"]
    record = local_product_projection(cache).lookup_run(run_id).value
    assert record is not None
    assert record.run_id == run_id
    assert record.operation == "sync"
    assert record.phase == "applied"
    assert record.results["operation"] == "sync"
    assert record.results["outcome"] == "no-change"
    assert [reference.artifact_id for reference in record.artifact_refs] == ["plan-review"]
