"""Opt-in proof that independently composed managed processes share durable storage."""

from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest

pytest.importorskip("boto3")
pytest.importorskip("psycopg")

from infrahub_sync.managed.storage import managed_product_projection
from infrahub_sync.product_store import ProductRun
from tests.configuration.validation_packages import package

pytestmark = pytest.mark.integration

_REQUIRED_ENVIRONMENT = (
    "INFRAHUB_SYNC_STORAGE_INTEGRATION_DATABASE_URL",
    "INFRAHUB_SYNC_STORAGE_INTEGRATION_S3_BUCKET",
    "INFRAHUB_SYNC_STORAGE_INTEGRATION_S3_ENDPOINT_URL",
)


def _settings_or_skip() -> dict[str, str]:
    """Return dedicated disposable-store settings, or skip before any network client exists."""
    values = {name: os.environ.get(name, "") for name in _REQUIRED_ENVIRONMENT}
    if missing := [name for name, value in values.items() if not value]:
        pytest.skip(f"managed storage integration requires explicit settings; missing: {', '.join(missing)}")
    return {
        "INFRAHUB_SYNC_DATABASE_URL": values["INFRAHUB_SYNC_STORAGE_INTEGRATION_DATABASE_URL"],
        "INFRAHUB_SYNC_S3_BUCKET": values["INFRAHUB_SYNC_STORAGE_INTEGRATION_S3_BUCKET"],
        "INFRAHUB_SYNC_S3_ENDPOINT_URL": values["INFRAHUB_SYNC_STORAGE_INTEGRATION_S3_ENDPOINT_URL"],
        "INFRAHUB_SYNC_S3_PREFIX": os.environ.get("INFRAHUB_SYNC_STORAGE_INTEGRATION_S3_PREFIX", "integration"),
        "INFRAHUB_SYNC_S3_REGION": os.environ.get("INFRAHUB_SYNC_STORAGE_INTEGRATION_S3_REGION", "us-east-1"),
    }


def test_independent_managed_projections_share_configurations_runs_and_artifacts() -> None:
    """API-like and worker-like composition roots observe one PostgreSQL/S3 record set."""
    settings = _settings_or_skip()
    api_projection = managed_product_projection(environ=settings)
    worker_projection = managed_product_projection(environ=settings)
    version = api_projection.create_configuration(package())
    run_id = f"managed-storage-integration-{version.config_id}"
    expected_run = ProductRun(
        run_id=run_id,
        operation="plan",
        configuration_reference=f"{version.config_id}@{version.registry_version}",
        config_id=version.config_id,
        registry_version=version.registry_version,
        package_checksum=version.package_checksum,
        actor="integration",
        started_at=datetime.now(timezone.utc),
        phase="accepted",
    )
    api_projection.create_run(expected_run)
    reference = api_projection.publish_artifact(
        run_id,
        artifact_id="shared-artifact",
        kind="integration-proof",
        media_type="text/plain",
        data=b"shared durable state",
    )
    expected_published_run = expected_run.model_copy(update={"artifact_refs": (reference,)})

    assert worker_projection.lookup_configuration_version(version.config_id, version.registry_version).value == version
    assert worker_projection.lookup_run(run_id).value == expected_published_run
    assert worker_projection.lookup_artifact(run_id, "shared-artifact").value == b"shared durable state"
