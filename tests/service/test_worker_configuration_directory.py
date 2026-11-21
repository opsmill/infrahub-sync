"""A worker resolves its configuration from PostgreSQL, not from a directory.

A run reads declared content out of the registry and both adapter classes out of
installed code, so it consults no directory at all. That is what lets a deployed
worker be given no configuration mount, and therefore share no filesystem with
anything.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

pytest.importorskip("prefect")
pytest.importorskip("opsmill_prefect_extras")

from infrahub_sync.execution import redaction_scope
from infrahub_sync.product_store import ProductRun, local_product_projection
from infrahub_sync.service import flow as service_flow
from tests.configuration.validation_packages import DESTINATION_CREDENTIAL, SOURCE_CREDENTIAL, credential_package
from tests.helpers.stored_credentials import create_stored_credential_names
from tests.service.execution_fixtures import serve_released_credentials

if TYPE_CHECKING:
    from infrahub_sync.product_store import ProductProjection

CONFIG_DIR_ENV = "INFRAHUB_SYNC_CONFIG_DIRECTORY"


def _registered_run(cache: Path, run_id: str) -> tuple[ProductProjection, tuple[str, int, str]]:
    """One durable run bound to a registered configuration version."""
    projection = local_product_projection(cache)
    create_stored_credential_names(projection, SOURCE_CREDENTIAL, DESTINATION_CREDENTIAL)
    version = projection.create_configuration(credential_package())
    binding = (version.config_id, version.registry_version, version.package_checksum)
    projection.create_run(
        ProductRun(
            run_id=run_id,
            operation="plan",
            configuration_reference=f"{binding[0]}@{binding[1]}",
            config_id=binding[0],
            registry_version=binding[1],
            package_checksum=binding[2],
            actor="owner",
            started_at=datetime.now(timezone.utc),
            phase="accepted",
        )
    )
    return projection, binding


def test_a_worker_starts_without_a_configuration_directory(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Startup resolves the product store and nothing else.

    A deployed worker is given no configuration mount, so the worker reads no
    configuration directory setting.
    """
    monkeypatch.delenv(CONFIG_DIR_ENV, raising=False)
    projection = local_product_projection(tmp_path / "product")

    resolved = service_flow._runtime(projection_factory=lambda: projection)

    assert resolved is projection
    assert not hasattr(service_flow, "CONFIG_DIR_ENV")


def test_a_registered_run_resolves_with_no_configuration_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Declared content comes from the registry and both adapters from installed code."""
    monkeypatch.delenv(CONFIG_DIR_ENV, raising=False)
    # The declared package names both credentials, so the Sync API has to release them
    # for the run to reach the question this test is about.
    serve_released_credentials(
        monkeypatch,
        service_flow,
        {"source": {"token": "registered-netbox-canary"}, "destination": {"token": "registered-infrahub-canary"}},
    )
    run_id = "registered-without-directory"
    projection, binding = _registered_run(tmp_path / "product", run_id)

    with redaction_scope():
        _, instance, name = service_flow._worker_execution_context(
            run_id,
            binding,
            projection=projection,
            run_branch=None,
            stage="plan",
            build_models=False,
            credentials=service_flow._RunCredentials(run_id, "flow-run-without-directory"),
        )

    assert name == credential_package().configuration.name
    assert instance._configuration_binding == binding
