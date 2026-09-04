"""Opt-in proof that independently composed service processes share durable storage."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

pytest.importorskip("boto3")
pytest.importorskip("psycopg")

from infrahub_sync.product_store.bundle import PLAN_CHECKPOINT_ARTIFACT_ID, write_bundle

from infrahub_sync.product_store import ProductProjection, ProductRun, local_product_projection
from infrahub_sync.service.storage import service_product_projection
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
        pytest.skip(f"service storage integration requires explicit settings; missing: {', '.join(missing)}")
    return {
        "INFRAHUB_SYNC_DATABASE_URL": values["INFRAHUB_SYNC_STORAGE_INTEGRATION_DATABASE_URL"],
        "INFRAHUB_SYNC_S3_BUCKET": values["INFRAHUB_SYNC_STORAGE_INTEGRATION_S3_BUCKET"],
        "INFRAHUB_SYNC_S3_ENDPOINT_URL": values["INFRAHUB_SYNC_STORAGE_INTEGRATION_S3_ENDPOINT_URL"],
        "INFRAHUB_SYNC_S3_PREFIX": os.environ.get("INFRAHUB_SYNC_STORAGE_INTEGRATION_S3_PREFIX", "integration"),
        "INFRAHUB_SYNC_S3_REGION": os.environ.get("INFRAHUB_SYNC_STORAGE_INTEGRATION_S3_REGION", "us-east-1"),
    }


def test_independent_service_projections_share_configurations_runs_and_artifacts() -> None:
    """API-like and worker-like composition roots observe one PostgreSQL/S3 record set."""
    settings = _settings_or_skip()
    api_projection = service_product_projection(environ=settings)
    worker_projection = service_product_projection(environ=settings)
    version = api_projection.create_configuration(package())
    run_id = f"service-storage-integration-{version.config_id}"
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


def _publish_bundle_run(projection: ProductProjection, run_id: str, data: bytes) -> None:
    """Create one run carrying a public review artifact and one internal plan bundle."""
    version = projection.create_configuration(package())
    projection.create_run(
        ProductRun(
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
    )
    projection.publish_artifact(
        run_id,
        artifact_id="plan-review",
        kind="saved-plan-review",
        media_type="application/json",
        data=b'{"review": true}',
    )
    projection.publish_artifact(
        run_id,
        artifact_id=PLAN_CHECKPOINT_ARTIFACT_ID,
        kind="run-bundle",
        media_type="application/zip",
        data=data,
        visibility="internal",
    )


def test_an_internal_bundle_is_byte_identical_across_the_deployed_providers(tmp_path: Path) -> None:
    """The digest a filesystem stage computed has to name the same object in S3.

    Determinism only pays off if it survives the provider change between the local
    profile and a deployed one, so this compares real S3-backed storage against the
    filesystem provider rather than comparing S3 with itself.
    """
    settings = _settings_or_skip()
    data = write_bundle(
        {
            "plan/operations.jsonl": b'{"op": "create", "id": "1"}\n',
            "plan/manifest.json": b'{"checksum": "' + b"d" * 64 + b'"}',
            "A/devices.parquet": b"PAR1integrationPAR1",
        }
    )
    run_id = f"bundle-parity-{datetime.now(timezone.utc):%Y%m%d%H%M%S%f}"
    deployed = service_product_projection(environ=settings)
    local = local_product_projection(tmp_path.resolve())
    _publish_bundle_run(deployed, run_id, data)
    _publish_bundle_run(local, run_id, data)

    remote_reference = deployed.lookup_internal_reference(run_id, PLAN_CHECKPOINT_ARTIFACT_ID).value
    local_reference = local.lookup_internal_reference(run_id, PLAN_CHECKPOINT_ARTIFACT_ID).value

    assert remote_reference is not None
    assert local_reference is not None
    assert remote_reference.digest == local_reference.digest
    assert remote_reference.size == local_reference.size == len(data)
    assert remote_reference.object_key == local_reference.object_key
    assert deployed.lookup_internal_artifact(run_id, PLAN_CHECKPOINT_ARTIFACT_ID).value == data
    assert local.lookup_internal_artifact(run_id, PLAN_CHECKPOINT_ARTIFACT_ID).value == data


def test_a_deployed_internal_bundle_is_invisible_to_the_public_paths() -> None:
    """Confidentiality is a storage-backed property, so it is proved on the real backend."""
    settings = _settings_or_skip()
    data = write_bundle({"plan/manifest.json": b'{"checksum": "' + b"e" * 64 + b'"}'})
    projection = service_product_projection(environ=settings)
    run_id = f"bundle-private-{datetime.now(timezone.utc):%Y%m%d%H%M%S%f}"
    _publish_bundle_run(projection, run_id, data)

    run = projection.lookup_run(run_id)

    assert run.value is not None
    assert [reference.artifact_id for reference in run.value.artifact_refs] == ["plan-review"]
    assert projection.lookup_artifact(run_id, PLAN_CHECKPOINT_ARTIFACT_ID).value is None
    assert projection.lookup_internal_artifact(run_id, PLAN_CHECKPOINT_ARTIFACT_ID).value == data


def test_a_deployed_bundle_over_the_bound_is_refused_before_download() -> None:
    """The metadata refusal has to hold against a real object store, not only a fake."""
    settings = _settings_or_skip()
    data = write_bundle({"plan/manifest.json": b'{"checksum": "' + b"f" * 64 + b'"}'})
    projection = service_product_projection(environ=settings)
    run_id = f"bundle-bound-{datetime.now(timezone.utc):%Y%m%d%H%M%S%f}"
    _publish_bundle_run(projection, run_id, data)

    refused = projection.lookup_internal_artifact(run_id, PLAN_CHECKPOINT_ARTIFACT_ID, limit=len(data) - 1)

    assert refused.value is None
    assert refused.reason == "artifact-too-large"
    assert projection.lookup_internal_artifact(run_id, PLAN_CHECKPOINT_ARTIFACT_ID, limit=len(data)).value == data
