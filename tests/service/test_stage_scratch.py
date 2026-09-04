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

from infrahub_sync.cache.paths import run_dir
from infrahub_sync.plan.models import PlanManifest
from infrahub_sync.plan.review import SavedPlan, UnknownRunIdentifierError, require_stored_run
from infrahub_sync.plan.writer import write_plan_artifact
from infrahub_sync.product_store import PrefectExecutionLink, ProductRun, local_product_projection
from infrahub_sync.service import flow as service_flow
from infrahub_sync.service.flow import service_sync_run
from infrahub_sync.service.scratch import stage_scratch
from tests.configuration.validation_packages import package
from tests.service.execution_fixtures import append_execution, bind_granting_guard, stage_root

if TYPE_CHECKING:
    from collections.abc import Iterator

WORKER_ID = "3f6b1c2e-52b6-4f1a-9d7c-8a1c0e5b4d21"
SYNC_NAME_FOR_DIAGNOSTIC = "inventory"


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
            schema_mapping=[SimpleNamespace(name="BuiltinTag")],
        ),
    )
    monkeypatch.setattr(service_flow, "build_runtime_model_plan", lambda *_a, **_k: object())
    monkeypatch.setattr(service_flow, "_run_logger", lambda: (service_flow.logger, False))
    # Checkpoint membership has its own tests; these assert only where a stage works.
    monkeypatch.setattr(service_flow, "publish_plan_checkpoint", lambda *_a, **_k: None)
    monkeypatch.setattr(service_flow, "rehydrate_plan_checkpoint", lambda *_a, **_k: None)


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


def test_a_lost_stage_root_is_refused_rather_than_stringified() -> None:
    """A double that loses its stage root must fail, not write into the caller's cwd.

    ``Path(str(None))`` is the relative directory ``None``. Read that way, a stage root a
    caller forgot to pass becomes a real tree under whatever the working directory happens
    to be, which is the shared-filesystem behaviour this unit removed.
    """
    from tests.service.execution_fixtures import stage_root as read_root

    with pytest.raises(AssertionError, match="no explicit run directory"):
        read_root({"operation": "apply", "run_id": "run-1"})

    with pytest.raises(AssertionError, match="must be absolute"):
        read_root({"base_directory": Path("None")})


def test_the_applied_sidecar_refuses_a_relative_destination(tmp_path: Path) -> None:
    """The boundary that writes the sidecar refuses anything but an owned absolute root."""
    from tests.service.execution_fixtures import write_applied_sidecar

    with pytest.raises(ValueError, match="absolute run directory"):
        write_applied_sidecar(Path("None") / "inventory" / "run-1")

    write_applied_sidecar(tmp_path / "inventory" / "run-1")

    assert (tmp_path / "inventory" / "run-1" / "run.json").is_file()
    assert not Path("None").exists()


# ---------------------------------------------------------------------------------------
# The real engine entry point, with only the engine factory doubled
# ---------------------------------------------------------------------------------------
#
# The tests above observe what the stage *hands* the engine. That says nothing about what
# the engine then does with it: `execute_run` takes the core pipeline lock and writes the
# run sidecar itself, and both derive their own paths. Doubling `execute_run` hides exactly
# that, so these drive the real one and double only the engine factory.


class _PlanningEngine:
    """The engine surface `execute_run` touches for a plan, and nothing else."""

    def __init__(self, run_directory: Path) -> None:
        self.run_dir = run_directory
        self.run_id = run_directory.name
        self.top_level = ["BuiltinTag"]
        self.tiers: list[set[str]] | None = None
        self.force_full_extract = False
        self.cache_root = run_directory.parent

    def load_both_sides(self) -> None:
        """Extraction is the boundary this double stands in for."""

    def diff(self) -> Any:  # noqa: ANN401, PLR6301 - the engine's own diff record.
        return SimpleNamespace(rows=[], has_diffs=lambda: False, str=lambda: "fake-diff(0)")

    def _diff_to_rows(self, _diff: Any) -> list[dict[str, str]]:  # noqa: ANN401, PLR6301
        return []

    def write_plan(self, _diff: Any) -> None:  # noqa: ANN401
        """Write the real plan artifact this run's own directory carries."""
        write_plan_artifact(
            run_dir=self.run_dir,
            run_id=self.run_id,
            config_version="configuration-v1",
            source_snapshot=[],
            deletes_computed=True,
            operations=[],
        )


@pytest.fixture
def planning_engine_factory(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    """Replace only the engine factory, so the real `execute_run` runs around it."""
    built: list[Path] = []

    def factory(**kwargs: Any) -> _PlanningEngine:  # noqa: ANN401 - the pinned factory shape.
        instance = kwargs["sync_instance"]
        run_directory = run_dir(instance.name, str(kwargs["run_id"]), base_directory=stage_root(kwargs))
        run_directory.mkdir(parents=True, exist_ok=True)
        built.append(run_directory)
        return _PlanningEngine(run_directory)

    monkeypatch.setattr("infrahub_sync.execution.get_potenda_from_instance", factory)
    return built


@pytest.fixture
def canary_cache(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Point the retired cache setting at a directory the service must never touch."""
    cache = tmp_path / "legacy-shared-cache"
    cache.mkdir()
    monkeypatch.setenv("INFRAHUB_SYNC_CACHE_DIR", str(cache))
    return cache


def _tree(root: Path) -> list[str]:
    """Every path under `root`, relative and sorted, so an assertion names what appeared."""
    return sorted(str(path.relative_to(root)) for path in root.rglob("*"))


@pytest.mark.usefixtures("claimed", "stub_runtime")
def test_the_managed_plan_stage_touches_neither_the_legacy_cache_nor_the_working_directory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    canary_cache: Path,
    canary_cwd: Path,
    planning_engine_factory: list[Path],
) -> None:
    """A managed plan runs entirely inside its own stage root.

    Both canaries are seeded the way an upgraded operator's environment carries them: the
    cache setting inherited from before the cutover, and a working directory that is not
    the stage's. Everything the run needs on disk -- its plan, its sidecar, and whatever
    exclusion the core engine takes -- belongs under the explicit stage root.
    """
    run_id = "run-real-plan"
    projection = _product_run(tmp_path / "product", run_id)
    monkeypatch.setattr(service_flow, "_runtime", lambda: (str(tmp_path), projection))
    monkeypatch.setattr(service_flow, "publish_plan_checkpoint", lambda *_a, **_k: None)
    monkeypatch.setattr(service_flow, "_publish_plan", lambda *_a, **_k: None)

    service_sync_run.fn(run_id, "plan", *_binding(projection, run_id))

    assert _tree(canary_cache) == [], f"the managed plan wrote into the legacy cache: {_tree(canary_cache)}"
    assert _tree(canary_cwd) == [], f"the managed plan wrote into the working directory: {_tree(canary_cwd)}"
    assert len(planning_engine_factory) == 1
    stage_directory = planning_engine_factory[0]
    assert canary_cache not in stage_directory.parents
    assert canary_cwd not in stage_directory.parents


@pytest.mark.usefixtures("claimed", "stub_runtime")
def test_the_managed_sync_planning_leg_touches_neither_canary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    canary_cache: Path,
    canary_cwd: Path,
    planning_engine_factory: list[Path],
) -> None:
    """The planning leg of a sync holds to the same boundary as a standalone plan."""
    run_id = "run-real-sync-plan"
    projection = _product_run(tmp_path / "product", run_id, operation="sync")
    monkeypatch.setattr(service_flow, "_runtime", lambda: (str(tmp_path), projection))
    monkeypatch.setattr(service_flow, "publish_plan_checkpoint", lambda *_a, **_k: None)
    monkeypatch.setattr(service_flow, "_publish_plan", lambda *_a, **_k: None)
    bind_granting_guard(monkeypatch, service_flow)

    with pytest.raises(RuntimeError):
        service_sync_run.fn(run_id, "sync", *_binding(projection, run_id), confirm_writes=True)

    assert _tree(canary_cache) == [], f"the sync planning leg wrote into the legacy cache: {_tree(canary_cache)}"
    assert _tree(canary_cwd) == [], f"the sync planning leg wrote into the working directory: {_tree(canary_cwd)}"
    assert len(planning_engine_factory) == 1


def test_an_unknown_run_diagnostic_names_the_stage_root_not_the_legacy_cache(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A missing run is reported against the directory the stage actually used.

    Enumerated from the legacy cache, the refusal would list identifiers from an unrelated
    root and point an operator at a directory this run never used.
    """
    cache = tmp_path / "legacy-shared-cache"
    stale = cache / SYNC_NAME_FOR_DIAGNOSTIC / "run-from-another-host" / "plan"
    stale.mkdir(parents=True)
    monkeypatch.setenv("INFRAHUB_SYNC_CACHE_DIR", str(cache))

    with stage_scratch("verify") as scratch:
        with pytest.raises(UnknownRunIdentifierError) as failure:
            require_stored_run(SYNC_NAME_FOR_DIAGNOSTIC, "run-absent", base_directory=scratch.root)

        message = str(failure.value)
        assert "run-from-another-host" not in message, f"the refusal enumerated the legacy cache: {message}"
        assert str(scratch.root) in message
        assert str(cache) not in message


@pytest.mark.usefixtures("claimed", "stub_runtime")
def test_two_managed_plans_never_share_a_stage_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    canary_cache: Path,
    canary_cwd: Path,
    planning_engine_factory: list[Path],
) -> None:
    """Every execution gets its own root, and no root outlives the stage that made it."""
    for index in range(2):
        run_id = f"run-real-plan-{index}"
        projection = _product_run(tmp_path / f"product-{index}", run_id)
        monkeypatch.setattr(service_flow, "_runtime", lambda p=projection: (str(tmp_path), p))
        monkeypatch.setattr(service_flow, "publish_plan_checkpoint", lambda *_a, **_k: None)
        monkeypatch.setattr(service_flow, "_publish_plan", lambda *_a, **_k: None)

        service_sync_run.fn(run_id, "plan", *_binding(projection, run_id))

    first, second = planning_engine_factory
    assert first != second
    for stage_directory in (first, second):
        assert stage_directory.is_absolute()
        assert not stage_directory.exists(), "a stage's private scratch must not outlive the stage"
    assert _tree(canary_cache) == []
    assert _tree(canary_cwd) == []
