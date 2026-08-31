"""AR1/AR5/AR9: a registered NetBox to Infrahub run executes on runtime models.

The engine, both adapters, extraction, the diff and the saved plan are the product's
own; only the two provider clients are faked, so this exercises the seam a real run uses
rather than a mock standing in for it. No generated Python exists anywhere on the path,
and no network call is possible.
"""

from __future__ import annotations

import importlib
import sys
import types
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import pytest
from infrahub_sdk.exceptions import NodeNotFoundError

from infrahub_sync.configuration import ConfigurationPackage, parse_configuration_package
from infrahub_sync.configuration.runtime import resolve_runtime_instance
from infrahub_sync.execution import execute_run
from infrahub_sync.plan.review import SavedPlan
from infrahub_sync.plugin_loader import PluginLoadError
from infrahub_sync.runtime_schema import build_runtime_model_plan
from infrahub_sync.runtime_schema import worker as worker_module
from infrahub_sync.utils import get_potenda_from_instance
from tests.configuration.validation_packages import package_data

if TYPE_CHECKING:
    from collections.abc import Iterator

    from infrahub_sync import SyncInstance

TAG_ATTRIBUTES = {
    "name": {"kind": "Text", "optional": False, "default_value": None, "unique": True},
    "description": {"kind": "Text", "optional": True, "default_value": None, "unique": False},
}
SNAPSHOT: dict[str, Any] = {
    "BuiltinTag": {
        "human_friendly_id": ["name__value"],
        "uniqueness_constraints": [["name__value"]],
        "attributes": TAG_ATTRIBUTES,
        "relationships": {},
    },
}
MAPPING = [
    {
        "name": "BuiltinTag",
        "mapping": "extras.tags",
        "fields": [{"name": "name", "mapping": "name"}, {"name": "description", "mapping": "description"}],
    }
]
# One NetBox tag the destination does not have, and one whose description differs.
NETBOX_TAGS = [
    {"id": 1, "name": "blue", "description": "cool"},
    {"id": 2, "name": "green", "description": "fresh"},
]
INFRAHUB_TAGS = [{"id": "node-green", "name": "green", "description": "stale"}]


# --- narrow provider fakes --------------------------------------------------------------


@dataclass
class _Attribute:
    value: Any


@dataclass
class _NodeSchema:
    kind: str
    attribute_names: list[str] = field(default_factory=list)
    relationships: list[Any] = field(default_factory=list)
    relationship_names: list[str] = field(default_factory=list)
    human_friendly_id: list[str] = field(default_factory=lambda: ["name__value"])
    uniqueness_constraints: list[list[str]] = field(default_factory=lambda: [["name__value"]])


class _Node:
    """One destination node, in the shape the adapter reads."""

    def __init__(self, node_id: str, kind: str, attributes: dict[str, Any]) -> None:
        self.id = node_id
        self._schema = _NodeSchema(kind=kind, attribute_names=list(attributes))
        for name, value in attributes.items():
            setattr(self, name, _Attribute(value=value))


class _Store:
    def __init__(self) -> None:
        self.nodes: dict[str, _Node] = {}

    def set(self, key: str, node: _Node) -> None:
        self.nodes[key] = node

    def get(self, key: str, **_kwargs: object) -> _Node | None:
        return self.nodes.get(key)


class _SchemaEndpoint:
    def __init__(self, schema: dict[str, Any]) -> None:
        self._schema = schema
        self.branches: list[str | None] = []

    def all(self, branch: str | None = None) -> dict[str, Any]:
        self.branches.append(branch)
        return self._schema


class _InfrahubClient:
    """The Infrahub SDK surface the destination adapter uses, and nothing else."""

    def __init__(self, address: str, config: object) -> None:
        self.address = address
        self.config = config
        self.schema = _SchemaEndpoint({kind: _NodeSchema(kind=kind) for kind in SNAPSHOT})
        self.store = _Store()
        self.created: list[tuple[str, dict[str, Any]]] = []

    @staticmethod
    def get(*_args: object, **kwargs: object) -> _Node:
        raise NodeNotFoundError(identifier={"key": [str(kwargs)]}, node_type=str(kwargs.get("kind")))

    @staticmethod
    def all(kind: str, **_kwargs: object) -> list[_Node]:
        if kind != "BuiltinTag":
            return []
        return [
            _Node(tag["id"], kind, {"name": tag["name"], "description": tag["description"]}) for tag in INFRAHUB_TAGS
        ]


class _NetboxEndpoint:
    def __init__(self, records: list[dict[str, Any]]) -> None:
        self._records = records

    def all(self) -> list[dict[str, Any]]:
        return [dict(record) for record in self._records]


def _forget_netbox_adapter() -> None:
    """Drop the NetBox adapter so the next import binds the current pynetbox stub.

    Only NetBox needs this: it imports its driver at module load, so a module another
    suite imported against a different stub keeps that stub. The Infrahub adapter's
    client is patched by attribute on the live module, which needs no reimport.
    """
    sys.modules.pop("infrahub_sync.adapters.netbox", None)


@pytest.fixture(name="providers", autouse=True)
def _providers(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[dict[str, Any]]:
    """Install both provider clients and the run cache; nothing may reach a network."""
    clients: dict[str, Any] = {}

    def _netbox_api(url: str, token: str) -> types.SimpleNamespace:
        del url, token
        api = types.SimpleNamespace(extras=types.SimpleNamespace(tags=_NetboxEndpoint(NETBOX_TAGS)))
        clients["netbox"] = api
        return api

    def _infrahub_client(address: str, config: object) -> _InfrahubClient:
        client = _InfrahubClient(address, config)
        clients["infrahub"] = client
        return client

    driver = cast("Any", types.ModuleType("pynetbox"))
    driver.api = _netbox_api
    monkeypatch.setitem(sys.modules, "pynetbox", driver)
    _forget_netbox_adapter()
    monkeypatch.setattr("infrahub_sync.adapters.infrahub.InfrahubClientSync", _infrahub_client)
    monkeypatch.setattr(worker_module, "read_destination_schema_snapshot", lambda _package, _branch: SNAPSHOT)
    monkeypatch.setenv("INFRAHUB_SYNC_CACHE_DIR", str(tmp_path / "runs"))
    monkeypatch.setenv("NETBOX_TOKEN", "netbox-execution-canary")
    monkeypatch.setenv("INFRAHUB_API_TOKEN", "infrahub-execution-canary")
    yield clients
    _forget_netbox_adapter()


def _registered_instance(
    tmp_path: Path,
    *,
    branch: str | None = None,
    run_branch: str | None = None,
    source_adapter: str | None = None,
) -> SyncInstance:
    """One registered package resolved and prepared exactly as the worker prepares it."""
    content = package_data()
    content["configuration"]["schema_mapping"] = MAPPING
    if branch is not None:
        content["configuration"]["destination"]["settings"]["branch"] = branch
    if source_adapter is not None:
        content["configuration"]["source"]["adapter"] = source_adapter
    package: ConfigurationPackage = parse_configuration_package(content)
    instance = resolve_runtime_instance(package, directory=str(tmp_path / "config"))
    (tmp_path / "config").mkdir(exist_ok=True)
    instance._runtime_models = build_runtime_model_plan(
        package=package, instance=instance, run_branch=run_branch, scope="both"
    )
    return instance


# --- AR1: a registered run plans creates and updates on runtime models -------------------


def test_a_registered_run_plans_creates_and_updates_through_engine_assembly(tmp_path: Path) -> None:
    instance = _registered_instance(tmp_path)

    saved = execute_run(
        instance,
        operation="plan",
        run_id="registered-execution",
        show_progress=False,
        print_diff=False,
        _return_saved_plan=True,
    )

    assert isinstance(saved, SavedPlan)
    summary = saved.summary()
    assert summary.by_action == {"create": 1, "update": 1}
    assert summary.by_kind == {"BuiltinTag": 2}
    actions = {(operation.action, operation.kind) for operation in saved.operations()}
    assert actions == {("create", "BuiltinTag"), ("update", "BuiltinTag")}


def test_the_run_binds_runtime_models_onto_both_installed_adapters(tmp_path: Path) -> None:
    instance = _registered_instance(tmp_path)
    plan = instance._runtime_models
    assert plan is not None
    assert plan.source is not None

    engine = get_potenda_from_instance(sync_instance=instance, run_id="registered-binding")

    live = importlib.import_module("infrahub_sync.adapters.infrahub")
    source = cast("Any", engine.source)
    destination = cast("Any", engine.destination)
    # Installed resolution reached execution: these are the classes the plan resolved.
    assert type(source) is plan.source.adapter_class
    assert type(destination) is plan.destination.adapter_class
    assert isinstance(destination, live.InfrahubAdapter)
    # And the classes each adapter loads with are this run's, over that side's base.
    assert source.BuiltinTag is plan.source.models["BuiltinTag"]
    assert destination.BuiltinTag is plan.destination.models["BuiltinTag"]
    assert issubclass(destination.BuiltinTag, live.InfrahubModel)


def test_no_generated_python_is_written_or_read_by_a_registered_run(tmp_path: Path) -> None:
    instance = _registered_instance(tmp_path)
    # Scoped to this run: the legacy path legitimately imports generated wrappers, and
    # other suites leave those modules behind.
    before = {name for name in sys.modules if name.endswith(".adapter")}

    execute_run(
        instance,
        operation="plan",
        run_id="registered-no-generated-files",
        show_progress=False,
        print_diff=False,
    )

    assert list((tmp_path / "config").rglob("*.py")) == []
    assert {name for name in sys.modules if name.endswith(".adapter")} == before


# --- AR5: the constructed destination works against the effective branch -----------------


@pytest.mark.parametrize(
    ("declared", "run_branch", "expected"),
    [("staging", "review", "staging"), (None, "review", "review"), (None, None, "main")],
)
def test_the_constructed_destination_receives_the_effective_branch(
    tmp_path: Path, declared: str | None, run_branch: str | None, expected: str
) -> None:
    instance = _registered_instance(tmp_path, branch=declared, run_branch=run_branch)
    plan = instance._runtime_models
    assert plan is not None

    engine = get_potenda_from_instance(sync_instance=instance, branch=run_branch, run_id="registered-branch")

    destination = cast("Any", engine.destination)
    assert plan.branch == expected
    assert destination.destination_binding.branch == expected
    assert destination.client.config.default_branch == expected
    assert destination.client.schema.branches == [expected]


# --- R2: the declared class is the class the engine runs ---------------------------------


class _EntryPoint:
    def __init__(self, name: str, target: object) -> None:
        self.name = name
        self._target = target

    def load(self) -> object:
        return self._target


class _EntryPoints:
    """The packaging metadata one installed plugin distribution publishes."""

    def __init__(self, entry_point: _EntryPoint) -> None:
        self._entry_point = entry_point

    def select(self, *, group: str, name: str) -> tuple[_EntryPoint, ...]:
        if group == "infrahub_sync.adapters" and name == self._entry_point.name:
            return (self._entry_point,)
        return ()


def _publish_entry_point(monkeypatch: pytest.MonkeyPatch, target: object) -> str:
    monkeypatch.setattr(
        "infrahub_sync.plugin_loader.entry_points", lambda: _EntryPoints(_EntryPoint("plugin_source", target))
    )
    return "plugin_source"


def _plugin_source_module() -> types.ModuleType:
    """An installed plugin's adapter module, with a second adapter to name wrongly."""
    module = types.ModuleType("declared_identity_plugin")
    source = """
from diffsync import Adapter, DiffSyncModel

from infrahub_sync import DiffSyncMixin, DiffSyncModelMixin


class PluginModel(DiffSyncModelMixin, DiffSyncModel):
    pass


class _Base(DiffSyncMixin, Adapter):
    def __init__(self, target, adapter, config, **kwargs):
        super().__init__(**kwargs)
        self.target = target
        self.config = config

    def model_loader(self, model_name, model):
        return None


class PluginAdapter(_Base):
    type = "Plugin"


class OtherAdapter(_Base):
    type = "Other"
"""
    exec(compile(source, module.__name__, "exec"), module.__dict__)  # noqa: S102
    for name in ("PluginModel", "_Base", "PluginAdapter", "OtherAdapter"):
        getattr(module, name).__module__ = module.__name__
    return module


def test_the_declared_source_class_is_the_class_the_engine_constructs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The declared `:ClassName` is inside the package checksum, so the engine must run
    # that class and not whichever one the entry point happens to load.
    module = _plugin_source_module()
    monkeypatch.setitem(sys.modules, module.__name__, module)
    entry_point = _publish_entry_point(monkeypatch, module.OtherAdapter)
    instance = _registered_instance(tmp_path, source_adapter=f"{entry_point}:PluginAdapter")

    engine = get_potenda_from_instance(sync_instance=instance, run_id="declared-identity")

    assert type(engine.source) is module.PluginAdapter
    assert type(engine.source) is not module.OtherAdapter
    plan = instance._runtime_models
    assert plan is not None
    assert plan.source is not None
    assert issubclass(plan.source.models["BuiltinTag"], module.PluginModel)
    assert cast("Any", engine.source).BuiltinTag is plan.source.models["BuiltinTag"]


def test_a_declared_source_class_the_plugin_does_not_provide_refuses_before_assembly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _plugin_source_module()
    monkeypatch.setitem(sys.modules, module.__name__, module)
    entry_point = _publish_entry_point(monkeypatch, module.OtherAdapter)

    with pytest.raises(PluginLoadError, match="MissingAdapter"):
        _registered_instance(tmp_path, source_adapter=f"{entry_point}:MissingAdapter")


def test_a_namespace_installed_source_reaches_engine_assembly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # A PEP 420 namespace distribution is an ordinary way to ship an adapter, so one has
    # to reach real engine assembly, not merely pass admission.
    from tests.runtime_schema.installed_distribution import (
        ADAPTER_SOURCE,
        install_namespace_distribution,
    )

    installed = tmp_path / "site-packages"
    dotted = install_namespace_distribution(
        monkeypatch,
        portions={installed: {"adapter": ADAPTER_SOURCE}},
        dotted="vendor_ns.sync_plugins.adapter",
        installed_root=installed,
    )
    assert not (installed / "vendor_ns" / "__init__.py").exists()
    instance = _registered_instance(tmp_path, source_adapter=dotted)

    engine = get_potenda_from_instance(sync_instance=instance, run_id="namespace-source")

    module = importlib.import_module(dotted)
    plan = instance._runtime_models
    assert plan is not None
    assert plan.source is not None
    assert type(engine.source) is module.WheelAdapter
    assert cast("Any", engine.source).BuiltinTag is plan.source.models["BuiltinTag"]
    assert issubclass(plan.source.models["BuiltinTag"], module.WheelModel)
