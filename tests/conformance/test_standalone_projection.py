"""Matrix cases proving standalone entry paths consume the DB-003 projection."""

# ruff: noqa: PLR6301 - behavioral fakes intentionally mirror instance protocols.

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path
from typing import cast

import pytest
from typer.testing import CliRunner

from infrahub_sync import SyncAdapter, SyncInstance
from infrahub_sync.api import v1 as api
from infrahub_sync.api.v1 import _operations as api_operations  # noqa: PLC2701 - integration seam under test.
from infrahub_sync.cli import app
from infrahub_sync.execution import RunResult
from infrahub_sync.plan.errors import OperationApplyFailedError
from infrahub_sync.plan.models import ApplyRecord, PlanManifest
from infrahub_sync.plan.review import SavedPlan
from infrahub_sync.product_store import local_product_projection
from infrahub_sync.product_store.standalone import StandaloneProductRecordError, execute_standalone
from infrahub_sync.product_store.store import FileArtifactStore, SQLiteRunStore


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


def test_configured_apply_failure_retains_partial_write_evidence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    run_id = "run-standalone-partial-apply"
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
    partial = ApplyRecord(applied_operations=("op-applied",), failed_operation="op-failed")

    def fail(*_args: object, **_kwargs: object) -> None:
        message = "destination rejected operation"
        raise OperationApplyFailedError(message, apply_record=partial)

    monkeypatch.setattr("infrahub_sync.product_store.standalone.execute_run", fail)
    with pytest.raises(OperationApplyFailedError):
        execute_standalone(
            instance,
            operation="apply",
            run_id=run_id,
            confirm_writes=True,
            product_cache_location=cache,
        )

    record = local_product_projection(cache).lookup_run(run_id).value
    assert record is not None
    assert record.phase == "apply-failed"
    assert record.outcome == "failed"
    assert record.summary["may_have_partially_written"] is True
    assert record.results["apply_failure"] == {
        "stage": "apply",
        "outcome": "failed",
        "error_type": "OperationApplyFailedError",
        **partial.as_summary_keys(),
    }


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


def test_cli_plan_refuses_an_unresolvable_product_cache_user_without_a_traceback(
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

    with caplog.at_level(logging.ERROR, logger="infrahub_sync.cli"):
        invocation = CliRunner().invoke(
            app,
            [
                "diff",
                "--name",
                "inventory",
                "--directory",
                str(config_root),
                "--product-cache-location",
                "~db006-user-that-cannot-exist/product-cache",
            ],
        )

    assert invocation.exit_code == 1
    assert "unresolvable user home" in caplog.text
    assert "Traceback" not in invocation.output


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


def test_duplicate_configured_cli_plan_is_a_one_line_typed_refusal(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    run_id = "run-cli-duplicate-product"
    cache = (tmp_path / "products").resolve()
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
    core_result = RunResult(
        sync_name="inventory",
        operation="plan",
        run_id=run_id,
        status="no-change",
        changed=False,
        summary={"create": 0, "update": 0, "delete": 0},
        artifact_path=str(tmp_path / run_id),
    )
    monkeypatch.setattr("infrahub_sync.cli.execute_run", lambda *_args, **_kwargs: core_result)
    monkeypatch.setattr("infrahub_sync.product_store.standalone.read_saved_plan", lambda **_kwargs: _saved(run_id))
    arguments = [
        "diff",
        "--name",
        "inventory",
        "--directory",
        str(config_root),
        "--run-id",
        run_id,
        "--product-cache-location",
        str(cache),
    ]

    first = CliRunner().invoke(app, arguments)
    with caplog.at_level(logging.ERROR, logger="infrahub_sync.cli"):
        duplicate = CliRunner().invoke(app, arguments)

    assert first.exit_code == 0
    assert duplicate.exit_code == 1
    assert "already exists; use a fresh run ID" in caplog.text
    assert "Traceback" not in duplicate.output


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


@pytest.mark.parametrize("parallel", [False, True], ids=["serial", "parallel"])
def test_configured_cli_sync_publishes_review_before_any_destination_write(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    parallel: bool,  # noqa: FBT001
) -> None:
    monkeypatch.setenv("INFRAHUB_SYNC_CACHE_DIR", str((tmp_path / "run-cache").resolve()))
    run_id = f"run-prewrite-{'parallel' if parallel else 'serial'}"
    product_cache = (tmp_path / "products").resolve()
    projection = local_product_projection(product_cache)
    events: list[str] = []

    class Diff:
        def has_diffs(self) -> bool:
            return True

        def str(self) -> str:  # ty: ignore[invalid-type-form]
            return "one create"

    class Engine:
        def __init__(self) -> None:
            self.run_id = run_id
            self.run_dir = tmp_path / "runs" / run_id
            self.run_dir.mkdir(parents=True)
            self.top_level = ["Device"]
            self.tiers = [{"Device"}] if parallel else []
            self.force_full_extract = False

        def load_both_sides(self) -> None:
            return

        def check_rowcount_guardrail(self, *, allow_drop: bool) -> None:
            assert allow_drop is False

        def diff(self) -> Diff:
            return Diff()

        def write_plan(self, _diff: Diff) -> dict[str, int]:
            events.append("plan-committed")
            return {"create": 1, "update": 0, "delete": 0}

        def _diff_to_rows(self, _diff: Diff) -> list[dict[str, str]]:
            return [{"action": "create"}]

        def sync(self, *, diff: Diff) -> None:
            assert diff.has_diffs()
            assert projection.lookup_artifact(run_id, "plan-review").available
            events.append("destination-write")

        def sync_in_tiers(
            self,
            *,
            parallel: bool,
            allow_rowcount_drop: bool,
            plan_committed: Callable[[], None],
        ) -> dict[str, int]:
            assert parallel is True
            assert allow_rowcount_drop is False
            events.append("plan-committed")
            plan_committed()
            assert projection.lookup_artifact(run_id, "plan-review").available
            events.append("destination-write")
            return {"create": 1, "update": 0, "delete": 0}

        def persist_baseline_counts(self) -> None:
            return

    monkeypatch.setattr("infrahub_sync.product_store.standalone.read_saved_plan", lambda **_kwargs: _saved(run_id))

    result = execute_standalone(
        _instance(tmp_path),
        operation="sync",
        run_id=run_id,
        confirm_writes=True,
        product_cache_location=product_cache,
        parallel=parallel,
        potenda_factory=lambda **_kwargs: Engine(),
        print_diff=False,
    )

    assert isinstance(result, RunResult)
    assert events == ["plan-committed", "destination-write"]


def test_configured_sync_publication_failure_refuses_before_destination_write(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    run_id = "run-prewrite-publication-failure"
    writes: list[str] = []

    def core(_instance: object, *, operation: str, **kwargs: object) -> RunResult:
        assert operation == "sync"
        callback = cast("Callable[[], None]", kwargs["_plan_committed"])
        callback()
        writes.append("destination-write")
        return RunResult(
            sync_name="inventory",
            operation="sync",
            run_id=run_id,
            status="no-change",
            changed=False,
            summary={"create": 0, "update": 0, "delete": 0},
            artifact_path=str(tmp_path / run_id),
        )

    def fail_publication(*_args: object, **_kwargs: object) -> None:
        message = "object publication unavailable"
        raise RuntimeError(message)

    monkeypatch.setattr("infrahub_sync.product_store.standalone.read_saved_plan", lambda **_kwargs: _saved(run_id))
    monkeypatch.setattr("infrahub_sync.product_store.standalone._publish_plan", fail_publication)

    with pytest.raises(RuntimeError, match="object publication unavailable"):
        execute_standalone(
            _instance(tmp_path),
            operation="sync",
            run_id=run_id,
            confirm_writes=True,
            product_cache_location=(tmp_path / "products").resolve(),
            _core_executor=core,
        )

    assert writes == []


def test_configured_sync_mid_publication_failure_is_terminal_and_artifact_stays_incomplete(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    run_id = "run-mid-publication-failure"
    cache = (tmp_path / "products").resolve()
    saved = _saved(run_id)
    writes: list[str] = []

    def core(_instance: object, *, operation: str, **kwargs: object) -> RunResult:
        assert operation == "sync"
        callback = cast("Callable[[], None]", kwargs["_plan_committed"])
        callback()
        writes.append("destination-write")
        return RunResult(
            sync_name="inventory",
            operation="sync",
            run_id=run_id,
            status="no-change",
            changed=False,
            summary={"create": 0, "update": 0, "delete": 0},
            artifact_path=str(tmp_path / run_id),
        )

    def fail_after_reservation(_self: FileArtifactStore, _reference: object, _data: bytes) -> None:
        stored = SQLiteRunStore(cache / "product-records.sqlite3").lookup_artifact_reference(
            run_id,
            "plan-review",
        )
        assert stored.value is not None
        _reserved, published = stored.value
        assert published is False
        message = "injected failure after artifact reservation"
        raise OSError(message)

    monkeypatch.setattr("infrahub_sync.product_store.standalone.read_saved_plan", lambda **_kwargs: saved)
    monkeypatch.setattr(FileArtifactStore, "publish", fail_after_reservation)

    with pytest.raises(OSError, match="failure after artifact reservation"):
        execute_standalone(
            _instance(tmp_path),
            operation="sync",
            run_id=run_id,
            confirm_writes=True,
            product_cache_location=cache,
            _core_executor=core,
        )

    assert writes == []
    restarted = local_product_projection(cache)
    record = restarted.lookup_run(run_id).value
    assert record is not None
    assert record.phase == "sync-failed"
    assert record.outcome == "failed"
    assert record.finished_at is not None
    assert record.results["sync_failure"]["error_type"] == "OSError"
    assert restarted.lookup_artifact(run_id, "plan-review").reason == "artifact-publication-incomplete"
