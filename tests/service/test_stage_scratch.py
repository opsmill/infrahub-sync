"""Every service stage runs in its own private scratch, not a shared cache.

A worker shares no Sync filesystem with the API or with another flow run. What a stage
needs on disk it creates for itself, under a root nothing else can name, and the root
goes away when the stage ends.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from uuid import NAMESPACE_URL, uuid5

import pytest

pytest.importorskip("prefect")

from infrahub_sync.plan.models import PlanManifest
from infrahub_sync.plan.review import SavedPlan
from infrahub_sync.product_store import PrefectExecutionLink, ProductRun, local_product_projection
from infrahub_sync.service import flow as service_flow
from infrahub_sync.service.flow import service_sync_run
from tests.configuration.validation_packages import package
from tests.service.execution_fixtures import append_execution

if TYPE_CHECKING:
    from collections.abc import Iterator

WORKER_ID = "3f6b1c2e-52b6-4f1a-9d7c-8a1c0e5b4d21"


def _saved(run_id: str) -> SavedPlan:
    manifest = PlanManifest(
        format_version=2,
        run_id=run_id,
        created_at="2026-09-04T12:00:00+00:00",
        config_version="configuration-v1",
        source_snapshot=[],
        operations_count=0,
        delete_operations_computed=True,
        plan_checksum="a" * 64,
    )
    return SavedPlan(manifest=manifest, operations=[], checksum_ok=True, verification_notes=[])


@pytest.fixture
def canary_cwd(tmp_path: Path) -> Iterator[Path]:
    """Run the body from a directory a stage must not write into."""
    working = tmp_path / "canary-cwd"
    working.mkdir()
    previous = Path.cwd()
    os.chdir(working)
    try:
        yield working
    finally:
        os.chdir(previous)


def _product_run(cache: Path, run_id: str, *, operation: str = "plan") -> Any:  # noqa: ANN401
    projection = local_product_projection(cache)
    version = projection.create_configuration(package())
    projection.create_run(
        ProductRun(
            run_id=run_id,
            operation=operation,  # ty: ignore[invalid-argument-type]
            configuration_reference=f"{version.config_id}@{version.registry_version}",
            config_id=version.config_id,
            registry_version=version.registry_version,
            package_checksum=version.package_checksum,
            actor="owner",
            started_at=datetime.now(timezone.utc),
            phase="accepted",
            summary={"sync_name": "inventory"},
        )
    )
    return projection


def _binding(projection: Any, run_id: str) -> tuple[str, int, str]:  # noqa: ANN401
    run = projection.lookup_run(run_id).value
    assert run is not None
    assert run.configuration_binding is not None
    return run.configuration_binding


@pytest.fixture
def claimed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give the flow one real durable claim without a Prefect runtime."""

    def claim(projection: Any, run_id: str) -> tuple[str, str]:  # noqa: ANN401
        flow_run_id = str(uuid5(NAMESPACE_URL, run_id))
        run = projection.lookup_run(run_id).value
        if run is not None and not any(link.flow_run_id == flow_run_id for link in run.prefect_executions):
            append_execution(
                projection,
                run_id,
                PrefectExecutionLink(flow_run_id=flow_run_id, purpose="test", attempt=1),
            )
            assert projection.claim_execution(run_id, flow_run_id, worker_id=WORKER_ID)
        return flow_run_id, WORKER_ID

    monkeypatch.setattr(service_flow, "_claim_current_execution", claim)


@pytest.fixture
def recorded_run_directories(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    """Record the explicit run directory the service hands the engine, per stage."""
    recorded: list[Path] = []

    def execute_run(_instance: Any, **kwargs: Any) -> SavedPlan:  # noqa: ANN401
        base = kwargs.get("base_directory")
        if base is None:
            msg = (
                "the service stage gave the engine no explicit run directory, so the engine "
                f"would derive one from the environment or the working directory: {sorted(kwargs)}"
            )
            raise AssertionError(msg)
        directory = Path(base)
        recorded.append(directory)
        return _saved(str(kwargs["run_id"]))

    monkeypatch.setattr(service_flow, "execute_run", execute_run)
    return recorded


@pytest.fixture
def stub_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    """Resolve a minimal instance and publish nothing schema-dependent."""
    monkeypatch.setattr(
        service_flow,
        "resolve_runtime_instance",
        lambda *_args, **_kwargs: SimpleNamespace(
            name="inventory",
            source=SimpleNamespace(settings={}),
            destination=SimpleNamespace(settings={}),
            store=None,
        ),
    )
    monkeypatch.setattr(service_flow, "build_runtime_model_plan", lambda *_a, **_k: object())
    monkeypatch.setattr(service_flow, "_run_logger", lambda: (service_flow.logger, False))


@pytest.mark.usefixtures("claimed", "stub_runtime")
def test_the_service_runtime_needs_no_shared_cache_setting(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A worker resolves its runtime with no shared saved-plan cache configured."""
    monkeypatch.delenv(service_flow.CONFIG_DIR_ENV, raising=False)
    monkeypatch.delenv("INFRAHUB_SYNC_CACHE_DIR", raising=False)
    monkeypatch.setenv(service_flow.CONFIG_DIR_ENV, str(tmp_path))

    config_directory, projection = service_flow._runtime(projection_factory=object)

    assert config_directory == str(tmp_path)
    assert projection is not None
    assert not hasattr(service_flow, "RUN_CACHE_ENV")


@pytest.mark.usefixtures("claimed", "stub_runtime")
def test_each_stage_receives_a_new_empty_private_run_directory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    canary_cwd: Path,
    recorded_run_directories: list[Path],
) -> None:
    """Two executions never share a root, and no root is reused or left behind."""
    shared_cache = tmp_path / "shared-cache"
    shared_cache.mkdir()
    monkeypatch.setenv("INFRAHUB_SYNC_CACHE_DIR", str(shared_cache))
    observed: list[Path] = []

    def observe(*args: Any, **kwargs: Any) -> SavedPlan:  # noqa: ANN401
        del args
        base = Path(kwargs["base_directory"])
        observed.append(base)
        assert base.is_dir()
        assert list(base.iterdir()) == []
        return _saved(str(kwargs["run_id"]))

    for index in range(2):
        run_id = f"run-scratch-{index}"
        projection = _product_run(tmp_path / f"product-{index}", run_id)
        monkeypatch.setattr(service_flow, "_runtime", lambda p=projection: (str(tmp_path), p))
        monkeypatch.setattr(service_flow, "execute_run", observe)
        monkeypatch.setattr(service_flow, "_publish_plan", lambda *_a, **_k: None)

        service_sync_run.fn(run_id, "plan", *_binding(projection, run_id))

    assert len(observed) == 2
    first, second = observed
    assert first != second
    for root in observed:
        assert root.is_absolute()
        assert shared_cache not in root.parents
        assert canary_cwd not in root.parents
        assert not root.exists(), "a stage's private scratch must not outlive the stage"
    assert list(shared_cache.iterdir()) == []
    assert list(canary_cwd.iterdir()) == []
    del recorded_run_directories


@pytest.mark.usefixtures("claimed", "stub_runtime")
def test_a_stage_writes_into_neither_the_shared_cache_nor_the_working_directory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    canary_cwd: Path,
    recorded_run_directories: list[Path],
) -> None:
    """The configured shared cache and the working directory are both left untouched."""
    shared_cache = tmp_path / "shared-cache"
    shared_cache.mkdir()
    monkeypatch.setenv("INFRAHUB_SYNC_CACHE_DIR", str(shared_cache))
    run_id = "run-scratch-isolated"
    projection = _product_run(tmp_path / "product", run_id)
    monkeypatch.setattr(service_flow, "_runtime", lambda: (str(tmp_path), projection))
    monkeypatch.setattr(service_flow, "_publish_plan", lambda *_a, **_k: None)

    service_sync_run.fn(run_id, "plan", *_binding(projection, run_id))

    assert len(recorded_run_directories) == 1
    given = recorded_run_directories[0]
    assert shared_cache not in given.parents
    assert canary_cwd not in given.parents
    assert list(shared_cache.iterdir()) == []
    assert list(canary_cwd.iterdir()) == []
