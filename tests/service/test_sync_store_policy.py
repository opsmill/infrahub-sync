"""Previously registered store blocks refuse before credentials or schema reads."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Literal

import pytest
from fastapi.testclient import TestClient

from infrahub_sync.configuration.storage import UNSUPPORTED_STORE_MESSAGE
from infrahub_sync.product_store import ProductRun, configs, local_product_projection
from infrahub_sync.service import flow
from infrahub_sync.service.app import create_app
from infrahub_sync.service.auth import PRINCIPALS_ENV, EnvironmentPrincipalResolver
from infrahub_sync.service.config_routes import ConfigurationRoutes
from infrahub_sync.service.service import RunService
from tests.configuration.validation_packages import package, package_data
from tests.service.test_config_routes import _Orchestration

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize("stage", ["plan", "verify", "apply", "sync"])
def test_worker_refuses_a_previously_registered_store_before_external_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: Literal["plan", "verify", "apply", "sync"]
) -> None:
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

    with pytest.raises(ValueError) as raised:
        flow._worker_execution_context(
            "legacy-store-run",
            binding,
            config_directory=None,
            projection=projection,
            run_branch=None,
            stage=stage,
        )

    assert str(raised.value) == UNSUPPORTED_STORE_MESSAGE


@pytest.mark.parametrize("operation", ["register", "plan", "sync"])
def test_http_refusal_returns_the_store_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
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
