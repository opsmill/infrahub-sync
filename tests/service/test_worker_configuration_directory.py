"""A registered worker resolves its configuration from PostgreSQL, not from a directory.

`INFRAHUB_SYNC_CONFIG_DIRECTORY` is what an unregistered run resolves its
`SyncConfig` file through. A registered run reads declared content out of the
registry and both adapter classes out of installed code, so it consults no
directory at all — which is what lets a deployed worker be given no
configuration mount, and therefore share no filesystem with anything.

The setting is checked where it is read. Checked at worker start instead, it
would be a requirement every registered deployment has to satisfy with a
directory nothing ever opens.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

pytest.importorskip("prefect")
pytest.importorskip("opsmill_prefect_extras")

from infrahub_sync.product_store import ProductRun, local_product_projection
from infrahub_sync.service import flow as service_flow
from tests.configuration.validation_packages import package

if TYPE_CHECKING:
    from infrahub_sync.product_store import ProductProjection

CONFIG_DIR_ENV = "INFRAHUB_SYNC_CONFIG_DIRECTORY"


def _registered_run(cache: Path, run_id: str) -> tuple[ProductProjection, tuple[str, int, str]]:
    """One durable run bound to a registered configuration version."""
    projection = local_product_projection(cache)
    version = projection.create_configuration(package())
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


def _unregistered_run(cache: Path, run_id: str) -> ProductProjection:
    """One durable run that predates registration and names a configuration by file."""
    projection = local_product_projection(cache)
    projection.create_run(
        ProductRun(
            run_id=run_id,
            operation="plan",
            configuration_reference="legacy-config-version",
            actor="owner",
            started_at=datetime.now(timezone.utc),
            phase="accepted",
            summary={"sync_name": "legacy-inventory"},
        )
    )
    return projection


def test_a_worker_starts_without_a_configuration_directory(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Startup resolves the product store and nothing else.

    A deployed worker is given no configuration mount, so requiring the setting
    here would refuse every registered deployment before it read a single run.
    """
    monkeypatch.delenv(CONFIG_DIR_ENV, raising=False)
    projection = local_product_projection(tmp_path / "product")

    directory, resolved = service_flow._runtime(projection_factory=lambda: projection)

    assert directory is None
    assert resolved is projection


def test_a_registered_run_resolves_with_no_configuration_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Declared content comes from the registry and both adapters from installed code."""
    monkeypatch.delenv(CONFIG_DIR_ENV, raising=False)
    # The declared package references both credentials, so they have to resolve
    # for the run to reach the question this test is about.
    monkeypatch.setenv("NETBOX_TOKEN", "registered-netbox-canary")
    monkeypatch.setenv("INFRAHUB_API_TOKEN", "registered-infrahub-canary")
    run_id = "registered-without-directory"
    projection, binding = _registered_run(tmp_path / "product", run_id)

    _, instance, name = service_flow._worker_execution_context(
        run_id,
        binding,
        config_directory=None,
        projection=projection,
        run_branch=None,
        stage="plan",
        build_models=False,
    )

    assert name == package().configuration.name
    assert instance._configuration_binding == binding


def test_an_unregistered_run_refuses_when_no_configuration_directory_is_set(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The one path that reads the directory is the one that requires it."""
    monkeypatch.delenv(CONFIG_DIR_ENV, raising=False)
    run_id = "unregistered-without-directory"
    projection = _unregistered_run(tmp_path / "product", run_id)

    with pytest.raises(ValueError, match=CONFIG_DIR_ENV) as refusal:
        service_flow._worker_execution_context(
            run_id,
            None,
            config_directory=None,
            projection=projection,
            run_branch=None,
            stage="plan",
        )

    assert "legacy-inventory" not in str(refusal.value)


def test_an_unregistered_run_refuses_a_configuration_directory_that_is_not_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A path that names no directory resolves no configuration file either."""
    monkeypatch.delenv(CONFIG_DIR_ENV, raising=False)
    run_id = "unregistered-with-bad-directory"
    projection = _unregistered_run(tmp_path / "product", run_id)

    with pytest.raises(ValueError, match=CONFIG_DIR_ENV):
        service_flow._worker_execution_context(
            run_id,
            None,
            config_directory=str(tmp_path / "absent"),
            projection=projection,
            run_branch=None,
            stage="plan",
        )


def test_an_unregistered_run_resolves_through_the_directory_it_is_given(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Removing the startup check must not remove the resolution the legacy path does."""
    monkeypatch.delenv(CONFIG_DIR_ENV, raising=False)
    run_id = "unregistered-with-directory"
    projection = _unregistered_run(tmp_path / "product", run_id)
    directory = tmp_path / "configurations"
    directory.mkdir()
    resolved: list[tuple[str, str]] = []
    monkeypatch.setattr(
        service_flow,
        "resolve_sync_instance",
        lambda name, *, directory: (resolved.append((name, directory)), object())[1],
    )
    monkeypatch.setattr(service_flow, "resolve_config_version", lambda _instance: "legacy-config-version")

    service_flow._worker_execution_context(
        run_id,
        None,
        config_directory=str(directory),
        projection=projection,
        run_branch=None,
        stage="plan",
    )

    assert resolved == [("legacy-inventory", str(directory))]
