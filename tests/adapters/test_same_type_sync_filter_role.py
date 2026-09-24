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
from typing import Any, cast
from unittest.mock import MagicMock, patch

import pytest

from infrahub_sync import SchemaMappingField, SchemaMappingModel, SyncAdapter, SyncConfig

_ELEMENT_NAME = "InfraDevice"

# Import name (the `sys.modules` / `import` key) to PyPI distribution name, for SDKs
# whose distribution name differs from their import name.
_SDK_DISTRIBUTIONS = {"ipfabric": "ipfabric", "slurpit": "slurpit-sdk"}


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


def _import_with_optional_sdk_stub(
    adapter_module_name: str,
    sdk_name: str,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
    **sdk_attrs: Any,  # noqa: ANN401 — stub attribute values vary per SDK (e.g. `IPFClient=object`)
) -> types.ModuleType:
    """Import `infrahub_sync.adapters.<adapter_module_name>`, stubbing an uninstalled SDK.

    Neither `ipfabric` nor `slurpit` is installed in the development or CI unit
    profiles, and both adapter modules import their SDK unconditionally at module
    level. `_create_ipfabric_client`/`_create_slurpit_client` are patched out by the
    callers below, so only the import itself needs to succeed. All `sys.modules` and
    `infrahub_sync.adapters` package attribute changes go through `monkeypatch`, which
    restores the exact prior state (including an absent key or a `None` entry) at
    teardown regardless of what this function does in between. A module-level event
    loop created by the stubbed import (Slurp'it) is closed via `request.addfinalizer`
    rather than inline, so it stays open for the rest of the test.
    """
    import infrahub_sync.adapters as adapters_package

    full_name = f"infrahub_sync.adapters.{adapter_module_name}"
    if _sdk_installed(sdk_name):
        return importlib.import_module(full_name)

    # Record (via monkeypatch) and clear any prior cache entry/attribute before
    # importing, so the import below observes a clean slate and monkeypatch's
    # teardown restores exactly what was there beforehand.
    monkeypatch.delitem(sys.modules, full_name, raising=False)
    monkeypatch.delattr(adapters_package, adapter_module_name, raising=False)

    stub = types.ModuleType(sdk_name)
    for name, value in sdk_attrs.items():
        setattr(stub, name, value)
    monkeypatch.setitem(sys.modules, sdk_name, stub)

    module = importlib.import_module(full_name)
    loop = getattr(module, "loop", None)
    if isinstance(loop, asyncio.AbstractEventLoop):
        request.addfinalizer(loop.close)

    # Drop the stub-based entries the import just created so nothing leaks into the
    # rest of the test; monkeypatch still restores the pre-test state at teardown.
    sys.modules.pop(full_name, None)
    sys.modules.pop(sdk_name, None)
    return module


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


def _build_netbox(
    target: str, config: SyncConfig, _monkeypatch: pytest.MonkeyPatch, _request: pytest.FixtureRequest
) -> Any:  # noqa: ANN401 — concrete adapter type varies per builder
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


def _build_nautobot(
    target: str, config: SyncConfig, _monkeypatch: pytest.MonkeyPatch, _request: pytest.FixtureRequest
) -> Any:  # noqa: ANN401 — concrete adapter type varies per builder
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


def _build_genericrestapi(
    target: str, config: SyncConfig, _monkeypatch: pytest.MonkeyPatch, _request: pytest.FixtureRequest
) -> Any:  # noqa: ANN401 — concrete adapter type varies per builder
    from infrahub_sync.adapters.genericrestapi import GenericrestapiAdapter

    with patch.object(GenericrestapiAdapter, "_create_rest_client", return_value=MagicMock()):
        adapter = GenericrestapiAdapter(
            target=target,
            adapter=SyncAdapter(name="genericrestapi", settings={"url": "https://example.invalid"}),
            config=config,
        )
    cast("MagicMock", adapter.client).get.return_value = {}
    return adapter


def _build_ipfabricsync(
    target: str, config: SyncConfig, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> Any:  # noqa: ANN401 — concrete adapter type varies per builder
    module = _import_with_optional_sdk_stub("ipfabricsync", "ipfabric", monkeypatch, request, IPFClient=object)
    adapter_cls = module.IpfabricsyncAdapter

    with patch.object(adapter_cls, "_create_ipfabric_client", return_value=MagicMock()):
        adapter = adapter_cls(
            target=target,
            adapter=SyncAdapter(name="ipfabricsync", settings={"base_url": "https://example.invalid", "token": "x"}),
            config=config,
        )
    adapter.client.fetch_all.return_value = []
    return adapter


def _build_slurpitsync(
    target: str, config: SyncConfig, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> Any:  # noqa: ANN401 — concrete adapter type varies per builder
    module = _import_with_optional_sdk_stub("slurpitsync", "slurpit", monkeypatch, request)
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


def _build_aci(
    target: str, config: SyncConfig, _monkeypatch: pytest.MonkeyPatch, _request: pytest.FixtureRequest
) -> Any:  # noqa: ANN401 — concrete adapter type varies per builder
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


def _build_prometheus(
    target: str, config: SyncConfig, _monkeypatch: pytest.MonkeyPatch, _request: pytest.FixtureRequest
) -> Any:  # noqa: ANN401 — concrete adapter type varies per builder
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


_ADAPTER_BUILDERS: dict[str, Callable[[str, SyncConfig, pytest.MonkeyPatch, pytest.FixtureRequest], Any]] = {
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


def _run_model_loader(  # noqa: PLR0913 — one parameter per builder input this dispatches, plus the pytest fixtures it threads through
    adapter_key: str,
    *,
    target: str,
    source_name: str,
    dest_name: str,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> MagicMock:
    config = _config(source_name, dest_name, fields=_FIELDS_BY_ADAPTER.get(adapter_key))
    adapter = _ADAPTER_BUILDERS[adapter_key](target, config, monkeypatch, request)
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
    adapter_key: str, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> None:
    """Same-type sync (source and destination both `adapter_key`): only the source instance filters/transforms."""
    source = _run_model_loader(
        adapter_key,
        target="source",
        source_name=adapter_key,
        dest_name=adapter_key,
        monkeypatch=monkeypatch,
        request=request,
    )
    assert source.filter_records.called
    assert source.transform_records.called

    destination = _run_model_loader(
        adapter_key,
        target="destination",
        source_name=adapter_key,
        dest_name=adapter_key,
        monkeypatch=monkeypatch,
        request=request,
    )
    assert not destination.filter_records.called
    assert not destination.transform_records.called


@pytest.mark.parametrize("adapter_key", sorted(_ADAPTER_BUILDERS))
def test_filters_and_transforms_unchanged_for_heterogeneous_sync(
    adapter_key: str, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> None:
    """Heterogeneous sync (different source/destination adapter types): behavior is unchanged by role."""
    source = _run_model_loader(
        adapter_key,
        target="source",
        source_name=adapter_key,
        dest_name="infrahub",
        monkeypatch=monkeypatch,
        request=request,
    )
    assert source.filter_records.called
    assert source.transform_records.called

    destination = _run_model_loader(
        adapter_key,
        target="destination",
        source_name="infrahub",
        dest_name=adapter_key,
        monkeypatch=monkeypatch,
        request=request,
    )
    assert not destination.filter_records.called
    assert not destination.transform_records.called


def test_import_with_optional_sdk_stub_restores_prior_sys_modules_state(request: pytest.FixtureRequest) -> None:
    """A pre-existing `None` entry and a leaked stub must come back exactly as they were.

    A `None` entry in `sys.modules` for the adapter module name is a valid sentinel
    (Python's own import machinery uses it to mean "this name is known to fail to
    import"); the old hand-written restore skipped it because it only reinstated a
    prior value that was not `None`, silently dropping that sentinel. A stub module
    leaked into `sys.modules` under the SDK's own name by an earlier test must also
    come back exactly, not be merged with or overwritten by the fresh stub import.
    """
    sdk_name = "ipfabric"
    full_name = "infrahub_sync.adapters.ipfabricsync"

    prior_sdk_stub = types.ModuleType(sdk_name)
    sys.modules[full_name] = None  # ty: ignore[invalid-assignment] — a real `None` sentinel is the scenario under test
    sys.modules[sdk_name] = prior_sdk_stub
    try:
        with pytest.MonkeyPatch.context() as mp:
            module = _import_with_optional_sdk_stub("ipfabricsync", sdk_name, mp, request, IPFClient=object)
            assert module.IpfabricsyncAdapter is not None

        assert sys.modules[full_name] is None
        assert sys.modules[sdk_name] is prior_sdk_stub
    finally:
        sys.modules.pop(full_name, None)
        sys.modules.pop(sdk_name, None)
