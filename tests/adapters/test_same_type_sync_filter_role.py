"""Regression test for SYNC-103.

Filters and transforms must run only for the adapter instance built with
`target="source"`. The engine passes that role explicitly (see
`get_potenda_from_instance` in `infrahub_sync/utils.py`); comparing the
configured adapter *name* against the adapter *type* breaks as soon as both
sides of a sync use the same adapter type (NetBox-to-NetBox, and so on),
because the predicate is then true for both instances.
"""

from __future__ import annotations

import asyncio
import importlib
import importlib.metadata
import sys
import types
from collections.abc import Callable, Iterator
from typing import Any, Protocol, cast
from unittest.mock import MagicMock, patch

import pytest

from infrahub_sync import SchemaMappingField, SchemaMappingModel, SyncAdapter, SyncConfig

_ELEMENT_NAME = "InfraDevice"

# Import name (the `sys.modules` / `import` key) to PyPI distribution name, for SDKs
# whose distribution name differs from their import name.
_SDK_DISTRIBUTIONS = {"ipfabric": "ipfabric", "slurpit": "slurpit-sdk"}


class _SDKImporter(Protocol):
    def __call__(
        self,
        adapter_module_name: str,
        sdk_name: str,
        *,
        patcher: pytest.MonkeyPatch | None = None,
        force_stub: bool = False,
    ) -> types.ModuleType: ...


def _sdk_installed(sdk_name: str) -> bool:
    """Check for a real install via package metadata, never via `sys.modules`.

    A stub left behind in `sys.modules` by a previous test (or by this module's own
    stubbing below) must never be mistaken for a real install, so this looks up the
    distribution's installed metadata instead of importing or inspecting the module.
    """
    try:
        importlib.metadata.distribution(_SDK_DISTRIBUTIONS[sdk_name])
    except importlib.metadata.PackageNotFoundError:
        return False
    return True


@pytest.fixture
def optional_sdk_importer(monkeypatch: pytest.MonkeyPatch) -> _SDKImporter:
    """Import optional adapter modules while restoring all import state at teardown."""
    import infrahub_sync.adapters as adapters_package

    def import_adapter(
        adapter_module_name: str,
        sdk_name: str,
        *,
        patcher: pytest.MonkeyPatch | None = None,
        force_stub: bool = False,
    ) -> types.ModuleType:
        """Import an adapter with a temporary SDK stub when needed."""
        patcher = patcher or monkeypatch
        full_name = f"infrahub_sync.adapters.{adapter_module_name}"
        if not force_stub and _sdk_installed(sdk_name):
            return importlib.import_module(full_name)

        # Record the old value (or absence) before importlib adds its own entries.
        patcher.setitem(sys.modules, full_name, None)
        patcher.delitem(sys.modules, full_name, raising=False)
        patcher.setattr(adapters_package, adapter_module_name, None, raising=False)
        patcher.delattr(adapters_package, adapter_module_name, raising=False)
        stub = types.ModuleType(sdk_name)
        if sdk_name == "ipfabric":
            setattr(stub, "IPFClient", object)  # noqa: B010 — ModuleType has no typed SDK attributes
        patcher.setitem(sys.modules, sdk_name, stub)

        module = importlib.import_module(full_name)
        loop = getattr(module, "loop", None)
        if isinstance(loop, asyncio.AbstractEventLoop):
            loop.close()

        # Importlib writes these two entries itself. Their removal is recorded by
        # monkeypatch; the earlier setitem/setattr restore the state before import.
        patcher.delitem(sys.modules, full_name, raising=False)
        patcher.delattr(adapters_package, adapter_module_name, raising=False)
        patcher.delitem(sys.modules, sdk_name, raising=False)
        return module

    return import_adapter


def _config(source_name: str, dest_name: str, *, fields: list[dict] | None = None) -> SyncConfig:
    mapping = SchemaMappingModel(
        name=_ELEMENT_NAME,
        mapping="dcim.devices",
        fields=[SchemaMappingField(**f) for f in (fields or [])],
    )
    return SyncConfig(
        name="t",
        source=SyncAdapter(name=source_name),
        destination=SyncAdapter(name=dest_name),
        schema_mapping=[mapping],
    )


def _fake_model() -> MagicMock:
    model = MagicMock()
    model.filter_records.return_value = []
    model.transform_records.return_value = []
    return model


def _build_netbox(target: str, config: SyncConfig, _importer: _SDKImporter) -> Any:  # noqa: ANN401 — concrete adapter type varies per builder
    pytest.importorskip("pynetbox")
    from infrahub_sync.adapters.netbox import NetboxAdapter

    with patch.object(NetboxAdapter, "_create_netbox_client", return_value=MagicMock()):
        adapter = NetboxAdapter(
            target=target,
            adapter=SyncAdapter(name="netbox", settings={"url": "https://example.invalid", "token": "x"}),
            config=config,
        )
    adapter.client.dcim.devices.all.return_value = []
    return adapter


def _build_nautobot(target: str, config: SyncConfig, _importer: _SDKImporter) -> Any:  # noqa: ANN401 — concrete adapter type varies per builder
    pytest.importorskip("pynautobot")
    from infrahub_sync.adapters.nautobot import NautobotAdapter

    with patch.object(NautobotAdapter, "_create_nautobot_client", return_value=MagicMock()):
        adapter = NautobotAdapter(
            target=target,
            adapter=SyncAdapter(name="nautobot", settings={"url": "https://example.invalid", "token": "x"}),
            config=config,
        )
    adapter.client.dcim.devices.all.return_value = []
    return adapter


def _build_genericrestapi(target: str, config: SyncConfig, _importer: _SDKImporter) -> Any:  # noqa: ANN401 — concrete adapter type varies per builder
    from infrahub_sync.adapters.genericrestapi import GenericrestapiAdapter

    with patch.object(GenericrestapiAdapter, "_create_rest_client", return_value=MagicMock()):
        adapter = GenericrestapiAdapter(
            target=target,
            adapter=SyncAdapter(name="genericrestapi", settings={"url": "https://example.invalid"}),
            config=config,
        )
    cast("MagicMock", adapter.client).get.return_value = {}
    return adapter


def _build_ipfabricsync(target: str, config: SyncConfig, importer: _SDKImporter) -> Any:  # noqa: ANN401 — concrete adapter type varies per builder
    module = importer("ipfabricsync", "ipfabric")
    adapter_cls = module.IpfabricsyncAdapter

    with patch.object(adapter_cls, "_create_ipfabric_client", return_value=MagicMock()):
        adapter = adapter_cls(
            target=target,
            adapter=SyncAdapter(name="ipfabricsync", settings={"base_url": "https://example.invalid", "token": "x"}),
            config=config,
        )
    adapter.client.fetch_all.return_value = []
    return adapter


def _build_slurpitsync(target: str, config: SyncConfig, importer: _SDKImporter) -> Any:  # noqa: ANN401 — concrete adapter type varies per builder
    module = importer("slurpitsync", "slurpit")
    adapter_cls = module.SlurpitsyncAdapter

    with patch.object(adapter_cls, "_create_slurpit_client", return_value=MagicMock()):
        return adapter_cls(
            target=target,
            adapter=SyncAdapter(name="slurpitsync", settings={"url": "https://example.invalid", "token": "x"}),
            config=config,
        )


@pytest.fixture(autouse=True)
def _restore_aci_device_mapping() -> Iterator[None]:
    """AciAdapter construction sets AciModel's class-level device mapping as a side effect."""
    from infrahub_sync.adapters.aci import AciModel

    original = AciModel._device_mapping
    yield
    AciModel._device_mapping = original


def _build_aci(target: str, config: SyncConfig, _importer: _SDKImporter) -> Any:  # noqa: ANN401 — concrete adapter type varies per builder
    from infrahub_sync.adapters.aci import AciAdapter

    with patch.object(AciAdapter, "_create_aci_client", return_value=MagicMock()):
        adapter = AciAdapter(
            target=target,
            adapter=SyncAdapter(
                name="aci", settings={"url": "https://example.invalid", "username": "u", "password": "p"}
            ),
            config=config,
        )
    cast("MagicMock", adapter.client).get.return_value = MagicMock(json=MagicMock(return_value={"imdata": []}))
    return adapter


def _build_prometheus(target: str, config: SyncConfig, _importer: _SDKImporter) -> Any:  # noqa: ANN401 — concrete adapter type varies per builder
    pytest.importorskip("prometheus_client")
    from infrahub_sync.adapters.prometheus import PrometheusAdapter

    adapter = PrometheusAdapter(
        target=target,
        adapter=SyncAdapter(
            name="prometheus", settings={"url": "https://example.invalid", "mode": "api", "promql": {"resources": {}}}
        ),
        config=config,
    )
    adapter._samples_by_metric = {}
    return adapter


_ADAPTER_BUILDERS: dict[str, Callable[[str, SyncConfig, _SDKImporter], Any]] = {
    "netbox": _build_netbox,
    "nautobot": _build_nautobot,
    "genericrestapi": _build_genericrestapi,
    "ipfabricsync": _build_ipfabricsync,
    "slurpitsync": _build_slurpitsync,
    "aci": _build_aci,
    "prometheus": _build_prometheus,
}

# ACI's model_loader skips schema-mapping elements with no `fields` entries.
_FIELDS_BY_ADAPTER: dict[str, list[dict]] = {
    "aci": [{"name": "name", "mapping": "name"}],
}


def _run_model_loader(
    adapter_key: str,
    *,
    target: str,
    source_name: str,
    dest_name: str,
    importer: _SDKImporter,
) -> MagicMock:
    config = _config(source_name, dest_name, fields=_FIELDS_BY_ADAPTER.get(adapter_key))
    adapter = _ADAPTER_BUILDERS[adapter_key](target, config, importer)
    model = _fake_model()

    if adapter_key == "slurpitsync":
        # Route slurpit's plain (non-dotted) mapping to a zero-record source
        # method instead of the real API surface.
        config.schema_mapping[0].mapping = "fake_resource"
        adapter.fake_resource = list

    adapter.model_loader(_ELEMENT_NAME, model)
    return model


@pytest.mark.parametrize("adapter_key", sorted(_ADAPTER_BUILDERS))
def test_filters_and_transforms_run_only_for_source_role_in_same_type_sync(
    adapter_key: str, optional_sdk_importer: _SDKImporter
) -> None:
    """Same-type sync (source and destination both `adapter_key`): only the source instance filters/transforms."""
    source = _run_model_loader(
        adapter_key,
        target="source",
        source_name=adapter_key,
        dest_name=adapter_key,
        importer=optional_sdk_importer,
    )
    assert source.filter_records.called
    assert source.transform_records.called

    destination = _run_model_loader(
        adapter_key,
        target="destination",
        source_name=adapter_key,
        dest_name=adapter_key,
        importer=optional_sdk_importer,
    )
    assert not destination.filter_records.called
    assert not destination.transform_records.called


@pytest.mark.parametrize("adapter_key", sorted(_ADAPTER_BUILDERS))
def test_filters_and_transforms_unchanged_for_heterogeneous_sync(
    adapter_key: str, optional_sdk_importer: _SDKImporter
) -> None:
    """Heterogeneous sync (different source/destination adapter types): behavior is unchanged by role."""
    source = _run_model_loader(
        adapter_key,
        target="source",
        source_name=adapter_key,
        dest_name="infrahub",
        importer=optional_sdk_importer,
    )
    assert source.filter_records.called
    assert source.transform_records.called

    destination = _run_model_loader(
        adapter_key,
        target="destination",
        source_name="infrahub",
        dest_name=adapter_key,
        importer=optional_sdk_importer,
    )
    assert not destination.filter_records.called
    assert not destination.transform_records.called


@pytest.mark.parametrize(("adapter_name", "sdk_name"), [("ipfabricsync", "ipfabric"), ("slurpitsync", "slurpit")])
def test_import_with_optional_sdk_stub_restores_prior_import_state(
    optional_sdk_importer: _SDKImporter, adapter_name: str, sdk_name: str
) -> None:
    """Restore a prior `None` entry and SDK stub without leaking a package attribute.

    A `None` entry in `sys.modules` for the adapter module name is a valid sentinel
    (Python's own import machinery uses it to mean "this name is known to fail to
    import"); the old hand-written restore skipped it because it only reinstated a
    prior value that was not `None`, silently dropping that sentinel. A stub module
    leaked into `sys.modules` under the SDK's own name by an earlier test must also
    come back exactly, not be merged with or overwritten by the fresh stub import.
    """
    import infrahub_sync.adapters as adapters_package

    full_name = f"infrahub_sync.adapters.{adapter_name}"

    prior_sdk_stub = types.ModuleType(sdk_name)
    with pytest.MonkeyPatch.context() as prior_state:
        prior_state.setitem(sys.modules, full_name, None)
        prior_state.setitem(sys.modules, sdk_name, prior_sdk_stub)
        prior_state.delattr(adapters_package, adapter_name, raising=False)
        with pytest.MonkeyPatch.context() as imported_state:
            module = optional_sdk_importer(adapter_name, sdk_name, patcher=imported_state, force_stub=True)
            assert module is not None

        assert sys.modules[full_name] is None
        assert sys.modules[sdk_name] is prior_sdk_stub
        assert not hasattr(adapters_package, adapter_name)
