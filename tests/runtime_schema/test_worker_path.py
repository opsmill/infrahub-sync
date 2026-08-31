"""AR1/AR3/AR4/AR5/AR9: registered composition builds and binds runtime models."""

from __future__ import annotations

import copy
import importlib
import sys
import types
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

import pytest

from infrahub_sync.configuration import ConfigurationPackage, parse_configuration_package
from infrahub_sync.configuration.capabilities import DestinationSchemaReadError
from infrahub_sync.execution import RunResult
from infrahub_sync.plan.config_version import resolve_config_version
from infrahub_sync.plan.models import PlanManifest
from infrahub_sync.plan.review import SavedPlan
from infrahub_sync.product_store.models import ConfigurationVersion, LookupResult, ProductRun
from infrahub_sync.runtime_schema import (
    DestinationSchemaUnavailableError,
    MissingMappedKindError,
    RuntimeModelPlan,
    RuntimeModelScope,
    RuntimeModelScopeError,
    UnsupportedDestinationProfileError,
    build_runtime_model_plan,
)
from infrahub_sync.runtime_schema import worker as worker_module
from infrahub_sync.utils import get_potenda_from_instance
from tests.configuration.validation_packages import package_data
from tests.runtime_schema.installed_distribution import install_distribution

if TYPE_CHECKING:
    from collections.abc import Iterator

    from infrahub_sync import SyncAdapter, SyncInstance
    from infrahub_sync.product_store import ProductProjection

pytest.importorskip("prefect")
pytest.importorskip("opsmill_prefect_extras")

from infrahub_sync.managed import flow as managed_flow

_SNAPSHOT: dict[str, Any] = {
    "BuiltinTag": {
        "human_friendly_id": ["name__value"],
        "uniqueness_constraints": [["name__value"]],
        "attributes": {
            "name": {"kind": "Text", "optional": False, "default_value": None, "unique": True},
            "description": {"kind": "Text", "optional": True, "default_value": None, "unique": False},
        },
        "relationships": {},
    },
    "LocationSite": {
        "human_friendly_id": ["name__value"],
        "uniqueness_constraints": [["name__value"]],
        "attributes": {"name": {"kind": "Text", "optional": False, "default_value": None, "unique": True}},
        "relationships": {"tags": {"peer": "BuiltinTag", "cardinality": "many", "optional": True, "kind": "Generic"}},
    },
}

_MAPPING = [
    {
        "name": "BuiltinTag",
        "mapping": "extras.tags",
        "fields": [{"name": "name", "mapping": "name"}, {"name": "description", "mapping": "description"}],
    },
    {
        "name": "LocationSite",
        "mapping": "dcim.sites",
        "fields": [{"name": "name", "mapping": "name"}, {"name": "tags", "mapping": "tags", "reference": "BuiltinTag"}],
    },
]


def _package_content(**overrides: object) -> dict[str, Any]:
    content = package_data()
    content["configuration"]["schema_mapping"] = copy.deepcopy(_MAPPING)
    content["configuration"].update(overrides)
    return content


def _package(**overrides: object) -> ConfigurationPackage:
    return parse_configuration_package(_package_content(**overrides))


class _SnapshotSpy:
    """Records every destination schema read and returns a fixed snapshot."""

    def __init__(self, snapshot: dict[str, Any] | None = None) -> None:
        self.snapshot = _SNAPSHOT if snapshot is None else snapshot
        self.branches: list[str] = []

    def __call__(self, package: ConfigurationPackage, branch: str) -> dict[str, Any]:
        del package
        self.branches.append(branch)
        return self.snapshot


@pytest.fixture(name="spy")
def _spy(monkeypatch: pytest.MonkeyPatch) -> _SnapshotSpy:
    spy = _SnapshotSpy()
    monkeypatch.setattr(worker_module, "read_destination_schema_snapshot", spy)
    return spy


@pytest.fixture(name="credentials", autouse=True)
def _credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NETBOX_TOKEN", "netbox-worker-canary")
    monkeypatch.setenv("INFRAHUB_API_TOKEN", "infrahub-worker-canary")


@pytest.fixture(name="netbox_driver", autouse=True)
def _netbox_driver(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Make the NetBox adapter importable without its optional driver installed."""
    driver = cast("Any", types.ModuleType("pynetbox"))
    driver.api = lambda *_args, **_kwargs: types.SimpleNamespace()
    monkeypatch.setitem(sys.modules, "pynetbox", driver)
    yield
    sys.modules.pop("infrahub_sync.adapters.netbox", None)


def _instance(package: ConfigurationPackage, tmp_path: Path) -> SyncInstance:
    from infrahub_sync.configuration.runtime import resolve_runtime_instance

    return resolve_runtime_instance(package, directory=str(tmp_path))


def _plan(
    package: ConfigurationPackage,
    tmp_path: Path,
    *,
    run_branch: str | None = None,
    scope: RuntimeModelScope = "both",
) -> RuntimeModelPlan:
    return build_runtime_model_plan(
        package=package, instance=_instance(package, tmp_path), run_branch=run_branch, scope=scope
    )


# --- AR1: the registered worker has a real runtime-model consumer -----------------------


def test_the_plan_carries_fresh_model_classes_for_both_sides(spy: _SnapshotSpy, tmp_path: Path) -> None:
    plan = _plan(_package(), tmp_path)

    assert plan.source is not None
    assert set(plan.source.models) == {"BuiltinTag", "LocationSite"}
    assert set(plan.destination.models) == {"BuiltinTag", "LocationSite"}
    assert plan.source.models["BuiltinTag"] is not plan.destination.models["BuiltinTag"]
    assert plan.destination.models["LocationSite"]._attributes == ("tags",)
    assert spy.branches == ["main"]


def test_registered_composition_attaches_the_plan_to_the_runtime_instance(spy: _SnapshotSpy, tmp_path: Path) -> None:
    package = _package()
    binding = ("cfg-runtime-models", 1, package.checksum())
    projection = _StubProjection(package, binding)

    _, instance, name = managed_flow._worker_execution_context(
        "run-runtime-models",
        binding,
        config_directory=str(tmp_path),
        projection=cast("ProductProjection", projection),
        run_branch=None,
        stage="plan",
    )

    assert name == package.configuration.name
    assert instance._runtime_models is not None
    assert set(instance._runtime_models.destination.models) == {"BuiltinTag", "LocationSite"}
    assert spy.branches == ["main"]


def test_a_legacy_unregistered_run_builds_no_runtime_models(spy: _SnapshotSpy, tmp_path: Path) -> None:
    package = _package()
    (tmp_path / "from-netbox").mkdir()
    (tmp_path / "from-netbox" / "config.yml").write_text(
        "name: from-netbox\nsource:\n  name: netbox\ndestination:\n  name: infrahub\n", encoding="utf-8"
    )
    from infrahub_sync.execution import resolve_sync_instance

    reference = resolve_config_version(resolve_sync_instance("from-netbox", directory=str(tmp_path)))
    projection = _StubProjection(package, None, sync_name="from-netbox", configuration_reference=reference)

    _, instance, _ = managed_flow._worker_execution_context(
        "legacy-run",
        None,
        config_directory=str(tmp_path),
        projection=cast("ProductProjection", projection),
        run_branch=None,
        stage="plan",
    )

    assert instance._runtime_models is None
    assert spy.branches == []


def test_a_registered_stage_reaches_execution_with_its_models_bound(
    spy: _SnapshotSpy, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = _package()
    binding = ("cfg-runtime-models", 1, package.checksum())
    projection = _StubProjection(package, binding)
    seen: list[SyncInstance] = []

    def _record(instance: SyncInstance, **kwargs: object) -> SavedPlan | RunResult:
        seen.append(instance)
        if kwargs.get("operation") == "apply":
            return _run_result(instance.name, tmp_path / "run-composed-sync")
        return _saved_plan()

    def _skip(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(managed_flow, "execute_run", _record)
    monkeypatch.setattr(managed_flow, "_publish_plan", _skip)
    monkeypatch.setattr(managed_flow, "_verify_registered_apply", _skip)
    monkeypatch.setattr(managed_flow, "_require_planned_schema", _skip)

    result, _ = managed_flow._execute_stage(
        "run-composed-sync",
        "sync",
        *binding,
        None,
        None,
        confirm_writes=True,
        run_logger=managed_flow.logger,
        secrets=[],
        config_directory=str(tmp_path),
        projection=cast("ProductProjection", projection),
    )

    assert result["operation"] == "sync"
    # Plan, verify and apply legs share the one instance the single schema read built.
    assert len({id(instance) for instance in seen}) == 1
    assert seen[0]._runtime_models is not None
    assert spy.branches == ["main"]


# --- F1: runtime preparation is scoped to what the stage consumes -----------------------


@pytest.mark.parametrize(
    ("stage", "expected_reads"),
    [("plan", 1), ("sync", 1), ("apply", 1), ("verify", 0)],
)
def test_only_stages_that_construct_adapters_read_the_schema(
    spy: _SnapshotSpy, tmp_path: Path, stage: str, expected_reads: int
) -> None:
    package = _package()
    binding = ("cfg-runtime-models", 1, package.checksum())
    projection = _StubProjection(package, binding)

    _, instance, _ = managed_flow._worker_execution_context(
        f"run-{stage}",
        binding,
        config_directory=str(tmp_path),
        projection=cast("ProductProjection", projection),
        run_branch=None,
        stage=stage,
    )

    assert len(spy.branches) == expected_reads
    assert (instance._runtime_models is None) == (expected_reads == 0)


def test_a_saved_plan_apply_builds_no_source_requirements(spy: _SnapshotSpy, tmp_path: Path) -> None:
    # Apply constructs the destination only, so requiring the source plugin would
    # reintroduce the source dependency a no-source apply exists to avoid.
    package = _package()
    binding = ("cfg-runtime-models", 1, package.checksum())
    projection = _StubProjection(package, binding)

    _, instance, _ = managed_flow._worker_execution_context(
        "run-apply",
        binding,
        config_directory=str(tmp_path),
        projection=cast("ProductProjection", projection),
        run_branch=None,
        stage="apply",
    )

    plan = instance._runtime_models
    assert plan is not None
    assert plan.source is None
    assert set(plan.destination.models) == {"BuiltinTag", "LocationSite"}
    assert spy.branches == ["main"]


def _recording_resolution(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """Record every installed-resolution call in order, passing each one through.

    Recording rather than refusing: the destination is resolved first, so a resolver that
    always raises reports the destination call as though it were the source.
    """
    calls: list[tuple[str, str]] = []
    resolve_adapter = worker_module.resolve_installed_adapter_class
    resolve_base = worker_module.resolve_installed_model_base

    def _adapter(adapter: SyncAdapter) -> type:
        calls.append(("adapter_class", adapter.name))
        return resolve_adapter(adapter)

    def _base(adapter: SyncAdapter) -> type:
        calls.append(("model_base", adapter.name))
        return resolve_base(adapter)

    monkeypatch.setattr(worker_module, "resolve_installed_adapter_class", _adapter)
    monkeypatch.setattr(worker_module, "resolve_installed_model_base", _base)
    return calls


def test_an_apply_scoped_plan_resolves_only_destination_requirements(
    spy: _SnapshotSpy, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _recording_resolution(monkeypatch)

    plan = _plan(_package(), tmp_path, scope="destination")

    assert plan.source is None
    assert calls == [("adapter_class", "infrahub"), ("model_base", "infrahub")]
    assert spy.branches == ["main"]


def test_a_two_sided_plan_resolves_both_sides_so_the_oracle_can_tell_them_apart(
    spy: _SnapshotSpy, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Without this the apply case above would pass against a plan that resolves nothing.
    calls = _recording_resolution(monkeypatch)

    _plan(_package(), tmp_path, scope="both")

    assert calls == [
        ("adapter_class", "infrahub"),
        ("model_base", "infrahub"),
        ("adapter_class", "netbox"),
        ("model_base", "netbox"),
    ]
    assert spy.branches == ["main"]


def test_engine_assembly_refuses_a_destination_only_plan(spy: _SnapshotSpy, tmp_path: Path) -> None:
    # Assembling a two-sided engine from an apply-scoped plan is a wrong-stage request,
    # refused where the classes are selected rather than by loading a source adapter.
    package = _package()
    instance = _instance(package, tmp_path)
    instance._runtime_models = _plan(package, tmp_path, scope="destination")

    with pytest.raises(RuntimeModelScopeError):
        get_potenda_from_instance(sync_instance=instance)

    assert spy.branches == ["main"]


# --- AR5: one snapshot decides one plan -------------------------------------------------


def test_one_read_feeds_both_sides_and_the_fingerprint(spy: _SnapshotSpy, tmp_path: Path) -> None:
    plan = _plan(_package(), tmp_path)

    assert len(spy.branches) == 1
    assert len(plan.schema_fingerprint) == 64


@pytest.mark.parametrize(
    ("declared", "run_branch", "expected"),
    [("staging", "review", "staging"), (None, "review", "review"), (None, None, "main")],
)
def test_discovery_and_the_destination_binding_use_the_same_branch(
    spy: _SnapshotSpy, tmp_path: Path, declared: str | None, run_branch: str | None, expected: str
) -> None:
    content = _package_content()
    if declared is not None:
        content["configuration"]["destination"]["settings"]["branch"] = declared
    plan = _plan(parse_configuration_package(content), tmp_path, run_branch=run_branch)

    assert spy.branches == [expected]
    assert plan.branch == expected


# --- AR4: schema acquisition is declared, bounded, and secret-safe -----------------------


def test_a_non_infrahub_destination_refuses_before_any_schema_read(spy: _SnapshotSpy, tmp_path: Path) -> None:
    content = _package_content()
    content["configuration"]["destination"] = {
        "name": "peeringmanager",
        "settings": {"url": "https://peering.example.net", "token": {"$credential": "infrahub-token"}},
    }

    with pytest.raises(UnsupportedDestinationProfileError):
        _plan(parse_configuration_package(content), tmp_path)

    assert spy.branches == []


@pytest.mark.parametrize("declare_class", [False, True], ids=["module", "module-and-class"])
def test_an_installed_dotted_source_with_an_infrahub_destination_may_execute(
    spy: _SnapshotSpy, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, declare_class: bool
) -> None:
    # Admitted, not qualified. The source is laid out and claimed the way an install lays
    # it out, because registered admission binds the dotted origin to a shipped file.
    dotted = install_distribution(
        monkeypatch, site_packages=tmp_path / "site-packages", package="workerpkg", module="adapter"
    )
    content = _package_content()
    content["configuration"]["source"]["adapter"] = f"{dotted}:WheelAdapter" if declare_class else dotted

    plan = _plan(parse_configuration_package(content), tmp_path)

    module = importlib.import_module(dotted)
    assert plan.source is not None
    assert plan.source.adapter_class is module.WheelAdapter
    assert set(plan.source.models) == {"BuiltinTag", "LocationSite"}
    assert issubclass(plan.source.models["BuiltinTag"], module.WheelModel)
    assert spy.branches == ["main"]


def test_an_entry_point_source_with_an_infrahub_destination_may_execute(
    spy: _SnapshotSpy, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.runtime_schema.installed_source_adapter import InstalledSourceAdapter, InstalledSourceModel

    module = importlib.import_module("tests.runtime_schema.installed_source_adapter")
    monkeypatch.setattr(
        "infrahub_sync.plugin_loader.is_installed_distribution_module", lambda name: name == module.__name__
    )
    monkeypatch.setattr(
        "infrahub_sync.plugin_loader.module_origin_is_admitted",
        lambda loaded, name: loaded is module and name == module.__name__,
    )
    monkeypatch.setattr(
        "infrahub_sync.plugin_loader.entry_points",
        lambda: _EntryPoints("installed_source_entry_point", module),
    )
    content = _package_content()
    content["configuration"]["source"]["adapter"] = "installed_source_entry_point"

    plan = _plan(parse_configuration_package(content), tmp_path)

    assert plan.source is not None
    assert plan.source.adapter_class is InstalledSourceAdapter
    assert issubclass(plan.source.models["BuiltinTag"], InstalledSourceModel)
    assert spy.branches == ["main"]


class _EntryPoints:
    """The packaging metadata one installed plugin distribution would publish.

    A real distribution points its ``infrahub_sync.adapters`` entry at the module, which
    is what lets one entry serve both the adapter class and the model base.
    """

    def __init__(self, name: str, target: ModuleType) -> None:
        self._name = name
        self._target = target

    def select(self, *, group: str, name: str) -> tuple[object, ...]:
        if group != "infrahub_sync.adapters" or name != self._name:
            return ()
        return (SimpleNamespace(name=self._name, module=self._target.__name__, load=lambda: self._target),)


def test_a_failed_schema_read_becomes_a_typed_failure_carrying_only_its_reason(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def _refuse(package: ConfigurationPackage, branch: str) -> dict[str, Any]:
        del package, branch
        msg = "third-party text with infrahub-worker-canary inside"
        raise DestinationSchemaReadError(msg, reason="timeout")

    monkeypatch.setattr(worker_module, "read_destination_schema_snapshot", _refuse)

    with pytest.raises(DestinationSchemaUnavailableError) as caught:
        _plan(_package(), tmp_path)

    assert caught.value.reason == "timeout"
    assert "third-party text" not in str(caught.value)
    assert "canary" not in str(caught.value)


def test_a_mapped_kind_the_schema_does_not_declare_refuses(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        worker_module,
        "read_destination_schema_snapshot",
        _SnapshotSpy({"BuiltinTag": _SNAPSHOT["BuiltinTag"]}),
    )

    with pytest.raises(MissingMappedKindError) as caught:
        _plan(_package(), tmp_path)

    assert "LocationSite" in str(caught.value)


# --- AR3: isolation is structural -------------------------------------------------------


def test_two_configurations_sharing_kinds_get_distinct_bound_classes(spy: _SnapshotSpy, tmp_path: Path) -> None:
    first = _plan(_package(), tmp_path)
    assert spy.branches == ["main"]
    second = _plan(_package(name="second-configuration"), tmp_path)

    assert spy.branches == ["main", "main"]
    assert first.destination.models["BuiltinTag"] is not second.destination.models["BuiltinTag"]
    assert first.schema_fingerprint == second.schema_fingerprint


def test_a_rebuild_after_a_schema_change_leaves_the_earlier_classes_untouched(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    spy = _SnapshotSpy()
    monkeypatch.setattr(worker_module, "read_destination_schema_snapshot", spy)
    before = _plan(_package(), tmp_path)

    grown = copy.deepcopy(_SNAPSHOT)
    grown["BuiltinTag"]["attributes"]["colour"] = {
        "kind": "Text",
        "optional": True,
        "default_value": None,
        "unique": False,
    }
    spy.snapshot = grown
    after = _plan(_package(), tmp_path)

    assert "colour" not in before.destination.models["BuiltinTag"].model_fields
    assert after.destination.models["BuiltinTag"] is not before.destination.models["BuiltinTag"]
    assert after.schema_fingerprint == before.schema_fingerprint


# --- AR9: generated Python is absent from the registered path ---------------------------


def test_the_plan_binds_onto_adapters_without_reading_generated_python(
    spy: _SnapshotSpy, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert spy.branches == []
    from infrahub_sync import utils as utils_module

    generated = tmp_path / "infrahub"
    generated.mkdir()
    (generated / "__init__.py").touch()
    (generated / "sync_adapter.py").write_text("raise AssertionError('generated adapter imported')\n")

    def _forbidden(*args: object, **kwargs: object) -> None:
        del args, kwargs
        msg = "registered execution rendered generated code"
        raise AssertionError(msg)

    monkeypatch.setattr(utils_module, "render_adapter", _forbidden)
    monkeypatch.setattr(utils_module, "import_adapter", _forbidden)

    plan = _plan(_package(), tmp_path)
    adapter = _RecordingAdapter()
    worker_module.bind_runtime_models(adapter, plan.destination.models)

    assert adapter.BuiltinTag is plan.destination.models["BuiltinTag"]
    assert adapter.LocationSite is plan.destination.models["LocationSite"]


def _saved_plan() -> SavedPlan:
    """The smallest real saved plan the managed stage's assertions accept."""
    return SavedPlan(
        manifest=PlanManifest(
            format_version=1,
            run_id="run-composed-sync",
            created_at="2026-08-30T00:00:00+00:00",
            config_version="sha256:" + "0" * 64,
            source_snapshot=[],
            operations_count=0,
            delete_operations_computed=True,
            plan_checksum="a" * 64,
        ),
        operations=[],
        checksum_ok=True,
        verification_notes=[],
    )


def _run_result(sync_name: str, artifact_path: Path) -> RunResult:
    return RunResult(
        sync_name=sync_name,
        operation="apply",
        run_id="run-composed-sync",
        status="no-change",
        changed=False,
        summary={"create": 0, "update": 0, "delete": 0},
        artifact_path=str(artifact_path),
    )


class _RecordingAdapter:
    """Stands in for a constructed adapter instance the plan binds onto."""

    BuiltinTag: type
    LocationSite: type


class _StubProjection:
    """The two durable reads registered composition performs, without a store."""

    def __init__(
        self,
        package: ConfigurationPackage,
        binding: tuple[str, int, str] | None,
        *,
        sync_name: str | None = None,
        configuration_reference: str = "legacy@1",
    ) -> None:
        self._package = package
        self._binding = binding
        self._sync_name = sync_name
        self._configuration_reference = configuration_reference

    def lookup_run(self, run_id: str) -> LookupResult[ProductRun]:
        summary = {"sync_name": self._sync_name} if self._sync_name else {}
        return LookupResult(
            value=ProductRun(
                run_id=run_id,
                operation="plan",
                configuration_reference=(
                    self._configuration_reference if self._binding is None else f"{self._binding[0]}@{self._binding[1]}"
                ),
                config_id=None if self._binding is None else self._binding[0],
                registry_version=None if self._binding is None else self._binding[1],
                package_checksum=None if self._binding is None else self._binding[2],
                actor="owner",
                started_at=datetime.now(timezone.utc),
                phase="reserved",
                summary=summary,
            )
        )

    def lookup_configuration_version(self, config_id: str, registry_version: int) -> LookupResult[ConfigurationVersion]:
        assert self._binding is not None
        return LookupResult(
            value=ConfigurationVersion(
                config_id=config_id,
                registry_version=registry_version,
                package_checksum=self._binding[2],
                declared_content=self._package.model_dump(mode="json"),
                created_at=datetime.now(timezone.utc),
            )
        )
