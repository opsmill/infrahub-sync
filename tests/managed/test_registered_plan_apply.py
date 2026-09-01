"""AR6-AR7: registered saved plans are admitted before destination construction."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import pytest

pytest.importorskip("prefect")
pytest.importorskip("opsmill_prefect_extras")

from infrahub_sync.configuration import ConfigurationPackage
from infrahub_sync.configuration.runtime import resolve_runtime_instance
from infrahub_sync.execution import RunResult
from infrahub_sync.managed import flow as managed_flow
from infrahub_sync.managed.flow import managed_sync_run
from infrahub_sync.plan.config_version import resolve_config_version
from infrahub_sync.plan.models import PlannedOperation
from infrahub_sync.plan.writer import write_plan_artifact
from infrahub_sync.product_store import PrefectExecutionLink, ProductRun, local_product_projection
from infrahub_sync.runtime_schema import RuntimeModelPlan, RuntimeSideModels
from tests.configuration.validation_packages import package
from tests.plan.artifact_fixtures import operation_record

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
    projection.add_prefect_execution(
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
    manifest = write_plan_artifact(
        run_dir=tmp_path / "runs" / runtime.name / run_id,
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
    calls: list[str] = []
    monkeypatch.setattr(managed_flow, "_runtime", lambda: (str(tmp_path), projection))
    monkeypatch.setattr(managed_flow, "_run_logger", lambda: (managed_flow.logger, False))
    monkeypatch.setenv("PREFECT__WORKER_ID", WORKER_ID)
    monkeypatch.setattr(managed_flow, "_prefect_flow_run_id", lambda: FLOW_RUN_ID)
    monkeypatch.setattr(managed_flow, "_require_current_worker_identity", lambda *_args: None)
    monkeypatch.setattr(
        managed_flow,
        "build_runtime_model_plan",
        lambda **_kwargs: RuntimeModelPlan(
            branch="main",
            schema_fingerprint=SCHEMA_FINGERPRINT,
            destination=RuntimeSideModels(adapter_class=object, models={}),
            source=None,
        ),
    )

    def destination_forbidden(*_args: object, **_kwargs: object) -> RunResult:
        calls.append("execute-run")
        return RunResult(
            sync_name=runtime.name,
            operation="apply",
            run_id=run_id,
            status="no-change",
            changed=False,
            summary={"create": 0, "update": 0, "delete": 0},
            artifact_path=str(tmp_path / "runs" / runtime.name / run_id),
        )

    monkeypatch.setattr(managed_flow, "execute_run", destination_forbidden)
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
        managed_sync_run.fn(run_id, "apply", *binding, expected_checksum=checksum, confirm_writes=True)

    assert calls == []


def test_bound_apply_accepts_an_exact_manifest_binding(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run_id, binding, checksum, calls = _registered_apply(tmp_path, monkeypatch, manifest_binding="exact")
    managed_sync_run.fn(run_id, "apply", *binding, expected_checksum=checksum, confirm_writes=True)
    assert calls == ["execute-run"]


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
    projection.add_prefect_execution(
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
    manifest = write_plan_artifact(
        run_dir=tmp_path / "runs" / runtime.name / run_id,
        run_id=run_id,
        config_version=resolve_config_version(runtime),
        source_snapshot=[],
        deletes_computed=True,
        operations=[planned],
        configuration_binding=binding,
        schema_fingerprint=SCHEMA_FINGERPRINT,
    )

    writes: list[str] = []
    constructed: list[dict[str, Any]] = []

    class _RecordingDestination:
        """The one adapter a saved-plan apply constructs, recording every planned write."""

        def __init__(self, **kwargs: Any) -> None:
            constructed.append(kwargs)

        def new_peer_resolver(self) -> object:  # noqa: PLR6301
            """The per-apply resolver factory; nothing below this double's surface reads it."""
            return object()

        def apply_planned_operation(self, *, operation: PlannedOperation, peers: Any) -> str:
            del peers
            writes.append(operation.operation_id)
            return f"node-{len(writes)}"

    def _refuse_adapter_import(*, sync_instance: Any, adapter: Any) -> type:
        del sync_instance
        msg = f"a saved-plan apply imported the {adapter.name!r} adapter"
        raise AssertionError(msg)

    monkeypatch.setattr("infrahub_sync.utils.import_adapter", _refuse_adapter_import)
    monkeypatch.setattr(managed_flow, "_runtime", lambda: (str(tmp_path), projection))
    monkeypatch.setattr(managed_flow, "_run_logger", lambda: (managed_flow.logger, False))
    monkeypatch.setattr(managed_flow, "_prefect_flow_run_id", lambda: FLOW_RUN_ID)
    monkeypatch.setattr(managed_flow, "_require_current_worker_identity", lambda *_args: None)
    monkeypatch.setattr(
        managed_flow,
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

    result = managed_sync_run.fn(
        run_id, "apply", *binding, expected_checksum=manifest.plan_checksum, confirm_writes=True
    )

    assert writes == [planned.operation_id]
    assert result["outcome"] == "applied"
    assert result["summary"]["create"] == 1
    assert len(constructed) == 1
    assert constructed[0]["target"] == "destination"
    assert constructed[0]["adapter"].settings["token"] == INFRAHUB_CANARY
