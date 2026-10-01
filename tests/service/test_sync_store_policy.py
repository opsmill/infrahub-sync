"""Previously registered store blocks refuse before credentials or schema reads."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Literal

import pytest
from fastapi.testclient import TestClient

from infrahub_sync.configuration.storage import UNSUPPORTED_STORE_MESSAGE, UNSUPPORTED_STORE_REASON
from infrahub_sync.product_store import PrefectExecutionLink, ProductRun, configs, local_product_projection
from infrahub_sync.service import flow
from infrahub_sync.service.app import create_app
from infrahub_sync.service.auth import PRINCIPALS_ENV, EnvironmentPrincipalResolver
from infrahub_sync.service.config_routes import ConfigurationRoutes
from infrahub_sync.service.models import ApplyRunRequest, CreateRunRequest, VerifyRunRequest
from infrahub_sync.service.service import RunService
from tests.configuration.validation_packages import package, package_data
from tests.service.execution_fixtures import append_execution
from tests.service.test_config_routes import _Orchestration

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize("stage", ["plan", "verify", "apply", "sync"])
def test_worker_refuses_a_previously_registered_store_before_external_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: Literal["plan", "verify", "apply", "sync"]
) -> None:
    """Refuse stores before external reads and expose the fixed diagnostic."""
    projection = local_product_projection(tmp_path)
    content = package_data()
    content["configuration"]["store"] = {"type": "redis"}
    # Model a version admitted before the V3 storage policy changed.
    with monkeypatch.context() as before_policy:
        before_policy.setattr("infrahub_sync.product_store.store.validate_package_credentials", lambda _package: None)
        version = projection.create_configuration(package(content))
    binding = (version.config_id, version.registry_version, version.package_checksum)
    projection.create_run(
        ProductRun(
            run_id="legacy-store-run",
            operation=stage,
            configuration_reference=f"{version.config_id}@{version.registry_version}",
            config_id=version.config_id,
            registry_version=version.registry_version,
            package_checksum=version.package_checksum,
            actor="test",
            started_at=datetime.now(timezone.utc),
            phase="accepted",
        )
    )
    monkeypatch.setattr(
        "infrahub_sync.configuration.runtime.resolve_reference", lambda *_args: pytest.fail("credential read")
    )
    monkeypatch.setattr(flow, "build_runtime_model_plan", lambda **_kwargs: pytest.fail("schema read"))

    flow_run_id = "11111111-1111-1111-1111-111111111111"
    worker_id = "22222222-2222-2222-2222-222222222222"
    append_execution(
        projection, "legacy-store-run", PrefectExecutionLink(flow_run_id=flow_run_id, purpose=stage, attempt=1)
    )
    assert projection.claim_execution("legacy-store-run", flow_run_id, worker_id=worker_id)
    monkeypatch.setattr(flow, "_runtime", lambda: (None, projection))
    monkeypatch.setattr(flow, "_run_logger", lambda: (logging.getLogger("store-policy"), False))
    monkeypatch.setattr(flow, "_claim_current_execution", lambda *_args: (flow_run_id, worker_id))

    with pytest.raises(RuntimeError, match="Configured sync stores"):
        flow.service_sync_run.fn("legacy-store-run", stage, *binding, expected_checksum="a" * 64, confirm_writes=True)

    stored = projection.lookup_run("legacy-store-run").value
    assert stored is not None
    evidence = stored.results[f"{stage}_failure"]
    assert evidence["reason"] == UNSUPPORTED_STORE_REASON
    assert evidence["message"] == UNSUPPORTED_STORE_MESSAGE
    assert evidence["error_type"] == "UnsupportedSyncStoreError"


@pytest.mark.parametrize("operation", ["register", "plan", "sync", "verify", "apply"])
def test_http_refusal_returns_the_store_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    """The HTTP route answers a previously registered store with the fixed refusal message."""
    projection = local_product_projection(tmp_path)
    content = package_data()
    content["configuration"]["store"] = {"type": "redis"}
    monkeypatch.setenv(
        PRINCIPALS_ENV, json.dumps({"test": {"token": "store-policy-test-canary", "administrator": True}})
    )
    resolver = EnvironmentPrincipalResolver.from_environment()
    client = TestClient(
        create_app(
            RunService(projection, _Orchestration()),
            resolver,
            ConfigurationRoutes(product_projection=projection),
        )
    )
    body: dict[str, Any]
    if operation == "register":
        path = "/configs"
        body = {"package": content, "reason": "register inventory"}
    else:
        with monkeypatch.context() as before_policy:
            before_policy.setattr(
                "infrahub_sync.product_store.store.validate_package_credentials", lambda _package: None
            )
            version = projection.create_configuration(package(content))
        path = "/runs"
        body = {
            "operation": operation,
            "config_id": version.config_id,
            "registry_version": version.registry_version,
            "reason": "plan inventory",
            "confirm_writes": True,
        }
    if operation in {"verify", "apply"}:
        projection.create_run(
            ProductRun(
                run_id="legacy-store-run",
                operation="plan",
                actor="test",
                configuration_reference=f"{version.config_id}@{version.registry_version}",
                config_id=version.config_id,
                registry_version=version.registry_version,
                package_checksum=version.package_checksum,
                started_at=datetime.now(timezone.utc),
                phase="planned",
            )
        )
        path = f"/runs/legacy-store-run/{operation}"
        body = {"reason": "review inventory"}
        if operation == "apply":
            body.update(expected_checksum="a" * 64, confirm_writes=True)
    response = client.post(
        path,
        headers={"Authorization": "Bearer store-policy-test-canary", "Idempotency-Key": "store-refusal"},
        json=body,
    )

    assert response.status_code == 422
    assert response.json()["error"]["message"] == UNSUPPORTED_STORE_MESSAGE


def test_validation_reports_the_store_finding_for_a_previously_registered_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Validating a previously registered version reports the store finding."""
    projection = local_product_projection(tmp_path)
    content = package_data()
    content["configuration"]["store"] = {"type": "redis"}
    with monkeypatch.context() as before_policy:
        before_policy.setattr("infrahub_sync.product_store.store.validate_package_credentials", lambda _package: None)
        version = projection.create_configuration(package(content))

    report = configs.validate(
        config_id=version.config_id, registry_version=version.registry_version, projection=projection
    )

    assert [(finding.code, finding.location, finding.message) for finding in report.findings] == [
        ("unsupported-sync-store", "/configuration/store", UNSUPPORTED_STORE_MESSAGE)
    ]


@pytest.mark.parametrize("operation", ["plan", "sync", "verify", "apply"])
@pytest.mark.parametrize("accepted", [False, True])
def test_legacy_store_receipts_replay_only_completed_responses(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: Literal["plan", "sync", "verify", "apply"],
    *,
    accepted: bool,
) -> None:
    """Replay accepted receipts and refuse unsubmitted receipts after upgrade."""
    projection = local_product_projection(tmp_path)
    content = package_data()
    content["configuration"]["store"] = {"type": "redis"}
    with monkeypatch.context() as before_policy:
        before_policy.setattr("infrahub_sync.product_store.store.validate_package_credentials", lambda _package: None)
        version = projection.create_configuration(package(content))
    monkeypatch.setenv(
        PRINCIPALS_ENV, json.dumps({"test": {"token": "store-policy-test-canary", "administrator": True}})
    )
    resolver = EnvironmentPrincipalResolver.from_environment()
    service = RunService(projection, _Orchestration())
    client = TestClient(create_app(service, resolver))
    run = ProductRun(
        run_id="legacy-store-run",
        operation="plan",
        actor="test",
        configuration_reference=f"{version.config_id}@{version.registry_version}",
        config_id=version.config_id,
        registry_version=version.registry_version,
        package_checksum=version.package_checksum,
        started_at=datetime.now(timezone.utc),
        phase="planned",
    )
    if operation in {"plan", "sync"}:
        request = CreateRunRequest(
            operation="sync" if operation == "sync" else "plan",
            config_id=version.config_id,
            registry_version=version.registry_version,
            reason="review inventory",
            confirm_writes=True,
        )
        path = "/runs"
        target = None
    else:
        projection.create_run(run)
        request = (
            VerifyRunRequest(reason="review inventory")
            if operation == "verify"
            else ApplyRunRequest(reason="review inventory", expected_checksum="a" * 64, confirm_writes=True)
        )
        path = f"/runs/{run.run_id}/{operation}"
        target = run.run_id
    body = request.model_dump(mode="json")
    receipt = service._new_receipt(
        actor="test",
        idempotency_key="legacy-key",
        operation=operation,
        target_run_id=target,
        run_id=run.run_id,
        body=body,
        reason=request.reason,
        now=datetime.now(timezone.utc),
    )
    projection.reserve_mutation(
        receipt, run=run if target is None else None, admit_write=operation in {"apply", "sync"}
    )
    stored_response = {"run_id": run.run_id, "phase": "accepted"}
    if accepted:
        projection.complete_mutation(
            receipt.receipt_id,
            response_status=202,
            response_body=stored_response,
            flow_run_id="11111111-1111-1111-1111-111111111111",
        )
    monkeypatch.setattr(_Orchestration, "submit", lambda *_args, **_kwargs: pytest.fail("new execution"))
    response = client.post(
        path, headers={"Authorization": "Bearer store-policy-test-canary", "Idempotency-Key": "legacy-key"}, json=body
    )
    if accepted:
        assert response.status_code == 202
        assert response.json() == stored_response
        body["reason"] = "different request"
        conflict = client.post(
            path,
            headers={"Authorization": "Bearer store-policy-test-canary", "Idempotency-Key": "legacy-key"},
            json=body,
        )
        assert conflict.status_code == 409
        assert conflict.json()["error"]["code"] == "idempotency-conflict"
    else:
        assert response.status_code == 422
        assert response.json()["error"]["message"] == UNSUPPORTED_STORE_MESSAGE


def test_validation_reason_is_independent_of_message() -> None:
    """Carry the refusal reason explicitly even when the display text changes."""
    error = configs.ConfigsValidationError("different display text", reason=UNSUPPORTED_STORE_REASON)
    assert error.reason == UNSUPPORTED_STORE_REASON
    assert configs.ConfigsValidationError(UNSUPPORTED_STORE_MESSAGE).reason is None
