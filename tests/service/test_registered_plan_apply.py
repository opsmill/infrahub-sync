"""AR6-AR7: registered saved plans are admitted before destination construction."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal

import pytest

pytest.importorskip("prefect")
pytest.importorskip("opsmill_prefect_extras")

from infrahub_sync.configuration import ConfigurationPackage
from infrahub_sync.configuration.runtime import resolve_runtime_instance
from infrahub_sync.execution import RunResult
from infrahub_sync.plan.config_version import resolve_config_version
from infrahub_sync.plan.models import PlannedOperation
from infrahub_sync.plan.writer import write_plan_artifact
from infrahub_sync.product_store import PrefectExecutionLink, ProductProjection, ProductRun, local_product_projection
from infrahub_sync.runtime_schema import RuntimeModelPlan, RuntimeSideModels
from infrahub_sync.service import flow as service_flow
from infrahub_sync.service.flow import service_sync_run
from tests.configuration.validation_packages import package
from tests.plan.artifact_fixtures import manifest_path, operation_record, write_artifact
from tests.service.execution_fixtures import (
    append_execution,
    bind_granting_guard,
    publish_authored_plan,
    stage_root,
    write_applied_sidecar,
)

FLOW_RUN_ID = "ed4778cb-f2cf-4b1f-a87b-68be37659e93"
WORKER_ID = "8c1da53d-0e6b-4d3d-a0f1-97b6a9ccebf0"
SCHEMA_FINGERPRINT = "c" * 64
INFRAHUB_CANARY = "registered-infrahub-canary"


def _registered_apply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, manifest_binding: tuple[str, int, str] | Literal["exact"] | None
) -> tuple[str, tuple[str, int, str], str, list[str]]:
    """Prepare one bound run and a manifest, with a destination-call sentinel."""
    monkeypatch.setenv("NETBOX_TOKEN", "registered-netbox-canary")
    monkeypatch.setenv("INFRAHUB_API_TOKEN", "registered-infrahub-canary")
    monkeypatch.setenv("INFRAHUB_SYNC_CACHE_DIR", str(tmp_path / "runs"))
    projection = local_product_projection(tmp_path / "product")
    registered = projection.create_configuration(package())
    binding = (registered.config_id, registered.registry_version, registered.package_checksum)
    if manifest_binding == "exact":
        manifest_binding = binding
    run_id = "registered-plan-apply"
    projection.create_run(
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
        projection,
        run_id,
        PrefectExecutionLink(
            flow_run_id=FLOW_RUN_ID, purpose="apply", attempt=1, submitted_at=datetime.now(timezone.utc)
        ),
    )
    stored = projection.lookup_configuration_version(binding[0], binding[1]).value
    assert stored is not None
    runtime = resolve_runtime_instance(
        ConfigurationPackage.model_validate(stored.declared_content), directory=str(tmp_path)
    )
    runtime._configuration_binding = binding
    authored = tmp_path / "runs" / runtime.name / run_id
    manifest = write_plan_artifact(
        run_dir=authored,
        run_id=run_id,
        config_version=resolve_config_version(runtime),
        source_snapshot=[],
        deletes_computed=True,
        operations=[],
        configuration_binding=manifest_binding,
        # Required alongside a configuration binding; this file is about the binding
        # comparison, which the schema guard's own suite covers separately.
        schema_fingerprint=None if manifest_binding is None else SCHEMA_FINGERPRINT,
    )
    publish_authored_plan(projection, run_id, run_directory=authored, manifest=manifest)
    calls: list[str] = []
    monkeypatch.setattr(service_flow, "_runtime", lambda: (str(tmp_path), projection))
    bind_granting_guard(monkeypatch, service_flow)
    monkeypatch.setattr(service_flow, "_run_logger", lambda: (service_flow.logger, False))
    monkeypatch.setenv("PREFECT__WORKER_ID", WORKER_ID)
    monkeypatch.setattr(service_flow, "_prefect_flow_run_id", lambda: FLOW_RUN_ID)
    monkeypatch.setattr(service_flow, "_require_current_worker_identity", lambda *_args: None)
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

    def destination_forbidden(*_args: object, **kwargs: object) -> RunResult:
        calls.append("execute-run")
        # The engine leaves the applied sidecar the final checkpoint carries.
        write_applied_sidecar(stage_root(kwargs) / runtime.name / run_id)
        return RunResult(
            sync_name=runtime.name,
            operation="apply",
            run_id=run_id,
            status="no-change",
            changed=False,
            summary={"create": 0, "update": 0, "delete": 0},
            artifact_path=str(tmp_path / "runs" / runtime.name / run_id),
        )

    monkeypatch.setattr(service_flow, "execute_run", destination_forbidden)
    return run_id, binding, manifest.plan_checksum, calls


@pytest.mark.parametrize(
    "manifest_binding",
    [
        pytest.param(None, id="legacy-manifest"),
        pytest.param(("other-config", 1, "a" * 64), id="cross-product"),
        pytest.param(("config-001", 2, "a" * 64), id="version-mutation"),
        pytest.param(("config-001", 1, "b" * 64), id="checksum-mutation"),
    ],
)
def test_bound_apply_refuses_nonmatching_manifest_before_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, manifest_binding: tuple[str, int, str] | None
) -> None:
    run_id, binding, checksum, calls = _registered_apply(tmp_path, monkeypatch, manifest_binding=manifest_binding)

    with pytest.raises(RuntimeError, match="registered saved plan binding"):
        service_sync_run.fn(run_id, "apply", *binding, expected_checksum=checksum, confirm_writes=True)

    assert calls == []


def test_bound_apply_accepts_an_exact_manifest_binding(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run_id, binding, checksum, calls = _registered_apply(tmp_path, monkeypatch, manifest_binding="exact")
    service_sync_run.fn(run_id, "apply", *binding, expected_checksum=checksum, confirm_writes=True)
    assert calls == ["execute-run"]


@pytest.mark.parametrize(
    ("manifest_state", "expected_action", "wrong_action"),
    [
        pytest.param(
            "unsupported",
            "apply it with the version that wrote it",
            "artifact is incomplete",
            id="unsupported",
        ),
        pytest.param("malformed", "rebuild the plan artifact", "apply it with the version", id="malformed"),
        pytest.param("missing-version", "rebuild the plan artifact", "apply it with the version", id="missing-version"),
        pytest.param("absent", "rebuild the plan artifact", "apply it with the version", id="absent-manifest"),
    ],
)
def test_registered_apply_reports_verifier_recovery_before_destination(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    manifest_state: str,
    expected_action: str,
    wrong_action: str,
) -> None:
    """The managed precheck carries the format gate's specific, safe recovery action."""
    run_id = "managed-plan-recovery"
    run_directory = tmp_path / "managed-plan" / run_id
    write_artifact(run_directory, run_id=run_id)
    path = manifest_path(run_directory)
    if manifest_state == "absent":
        path.unlink()
    else:
        mapping = json.loads(path.read_bytes())
        if manifest_state == "unsupported":
            mapping["format_version"] = 99
        elif manifest_state == "malformed":
            mapping["format_version"] = "private-token-canary"
        else:
            del mapping["format_version"]
        path.write_text(json.dumps(mapping), encoding="utf-8")
    before = path.read_bytes() if path.exists() else None
    monkeypatch.setattr(service_flow, "resolve_config_version", lambda _instance: "fixture-version")

    with pytest.raises(ValueError, match="registered saved plan verification failed") as error:
        service_flow._verify_registered_apply(
            instance=SimpleNamespace(name="managed-plan"),
            run_id=run_id,
            binding=None,
            expected_checksum=None,
            base_directory=tmp_path,
        )

    message = str(error.value)
    assert "format_version:" in message
    assert expected_action in message
    assert wrong_action not in message
    assert "private-token-canary" not in message
    assert isinstance(error.value, service_flow.RegisteredPlanVerificationError)
    assert error.value.recovery_action == ("compatible_version" if manifest_state == "unsupported" else "rebuild")
    assert (path.read_bytes() if path.exists() else None) == before


@pytest.mark.parametrize(
    ("declared_version", "expected_action"),
    [
        pytest.param(99, "compatible_version", id="unsupported"),
        pytest.param("private-token-canary", "rebuild", id="malformed"),
    ],
)
def test_registered_apply_stores_only_the_fixed_recovery_signal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    declared_version: int | str,
    expected_action: str,
) -> None:
    """A refused managed apply preserves a safe CLI hint without storing manifest text."""
    run_id, binding, checksum, calls = _registered_apply(tmp_path, monkeypatch, manifest_binding="exact")
    projection = service_flow._runtime()[1]
    rehydrate = service_flow.rehydrate_plan_checkpoint

    def damaged_checkpoint(product: ProductProjection, selected_run_id: str, *, destination: Path) -> Path:
        directory = rehydrate(product, selected_run_id, destination=destination)
        path = manifest_path(directory)
        mapping = json.loads(path.read_bytes())
        mapping["format_version"] = declared_version
        path.write_text(json.dumps(mapping), encoding="utf-8")
        return directory

    monkeypatch.setattr(service_flow, "rehydrate_plan_checkpoint", damaged_checkpoint)

    with pytest.raises(RuntimeError, match="registered saved plan verification failed"):
        service_sync_run.fn(run_id, "apply", *binding, expected_checksum=checksum, confirm_writes=True)

    stored = projection.lookup_run(run_id).value
    assert stored is not None
    assert stored.phase == "apply-failed"
    assert stored.results["apply_failure"]["error_type"] == "RegisteredPlanVerificationError"
    assert stored.results["apply_failure"]["recovery_action"] == expected_action
    assert "private-token-canary" not in json.dumps(stored.results["apply_failure"])
    assert calls == []


def test_a_registered_saved_apply_runs_without_the_source_credential(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AR10: applying a reviewed plan needs destination credentials only.

    The plan was computed where the source was reachable; this apply runs on a host that
    holds `INFRAHUB_API_TOKEN` and no `NETBOX_TOKEN` at all. `execute_run` is the real
    one, so the reviewed operation reaches the destination the apply seam constructed —
    and `import_adapter` refuses every side, which is what proves the source adapter is
    neither imported nor constructed on the way there.
    """
    monkeypatch.setenv("NETBOX_TOKEN", "plan-host-netbox-canary")
    monkeypatch.setenv("INFRAHUB_API_TOKEN", INFRAHUB_CANARY)
    monkeypatch.setenv("INFRAHUB_SYNC_CACHE_DIR", str(tmp_path / "runs"))
    monkeypatch.setenv("PREFECT__WORKER_ID", WORKER_ID)

    projection = local_product_projection(tmp_path / "product")
    registered = projection.create_configuration(package())
    binding = (registered.config_id, registered.registry_version, registered.package_checksum)
    run_id = "registered-no-source-apply"
    projection.create_run(
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
        projection,
        run_id,
        PrefectExecutionLink(
            flow_run_id=FLOW_RUN_ID, purpose="apply", attempt=1, submitted_at=datetime.now(timezone.utc)
        ),
    )
    stored = projection.lookup_configuration_version(binding[0], binding[1]).value
    assert stored is not None
    # The plan-time runtime: the host that computed the plan could resolve both sides.
    runtime = resolve_runtime_instance(
        ConfigurationPackage.model_validate(stored.declared_content), directory=str(tmp_path)
    )
    runtime._configuration_binding = binding
    planned = PlannedOperation.model_validate(operation_record(identity={"name": "prod"}))
    authored = tmp_path / "runs" / runtime.name / run_id
    manifest = write_plan_artifact(
        run_dir=authored,
        run_id=run_id,
        config_version=resolve_config_version(runtime),
        source_snapshot=[],
        deletes_computed=True,
        operations=[planned],
        configuration_binding=binding,
        schema_fingerprint=SCHEMA_FINGERPRINT,
    )
    publish_authored_plan(projection, run_id, run_directory=authored, manifest=manifest)

    writes: list[str] = []
    constructed: list[dict[str, Any]] = []

    class _RecordingDestination:
        """The one adapter a saved-plan apply constructs, recording every planned write."""

        def __init__(self, **kwargs: Any) -> None:  # noqa: ANN401
            constructed.append(kwargs)

        def new_peer_resolver(self) -> object:  # noqa: PLR6301
            """The per-apply resolver factory; nothing below this double's surface reads it."""
            return object()

        def apply_planned_operation(self, *, operation: PlannedOperation, peers: Any) -> str:  # noqa: ANN401, PLR6301
            del peers
            writes.append(operation.operation_id)
            return f"node-{len(writes)}"

    def _refuse_adapter_import(*, sync_instance: Any, adapter: Any) -> type:  # noqa: ANN401
        del sync_instance
        msg = f"a saved-plan apply imported the {adapter.name!r} adapter"
        raise AssertionError(msg)

    monkeypatch.setattr("infrahub_sync.utils.import_adapter", _refuse_adapter_import)
    monkeypatch.setattr(service_flow, "_runtime", lambda: (str(tmp_path), projection))
    bind_granting_guard(monkeypatch, service_flow)
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
    # The apply host itself: the destination credential resolves, the source one does not exist.
    monkeypatch.delenv("NETBOX_TOKEN")

    result = service_sync_run.fn(
        run_id, "apply", *binding, expected_checksum=manifest.plan_checksum, confirm_writes=True
    )

    assert writes == [planned.operation_id]
    assert result["outcome"] == "applied"
    assert result["summary"]["create"] == 1
    assert len(constructed) == 1
    assert constructed[0]["target"] == "destination"
    assert constructed[0]["adapter"].settings["token"] == INFRAHUB_CANARY
