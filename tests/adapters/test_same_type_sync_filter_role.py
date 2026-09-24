"""Regression test for SYNC-103.

Filters and transforms must run only for the adapter instance built with
`target="source"`. The engine passes that role explicitly (see
`get_potenda_from_instance` in `infrahub_sync/utils.py`); comparing the
configured adapter *name* against the adapter *type* breaks as soon as both
sides of a sync use the same adapter type (NetBox-to-NetBox, and so on),
because the predicate is then true for both instances.
"""

from __future__ import annotations

import importlib
import sys
import types
from collections.abc import Callable, Iterator
from typing import Any, cast
from unittest.mock import MagicMock, patch

import pytest

from infrahub_sync import SchemaMappingField, SchemaMappingModel, SyncAdapter, SyncConfig

_ELEMENT_NAME = "InfraDevice"


def _import_with_optional_sdk_stub(
    adapter_module_name: str,
    sdk_name: str,
    **sdk_attrs: Any,  # noqa: ANN401 — stub attribute values vary per SDK (e.g. `IPFClient=object`)
) -> types.ModuleType:
    """Import `infrahub_sync.adapters.<adapter_module_name>`, stubbing an uninstalled SDK.

    Neither `ipfabric` nor `slurpit` is installed in the development or CI unit
    profiles, and both adapter modules import their SDK unconditionally at module
    level. `_create_ipfabric_client`/`_create_slurpit_client` are patched out by the
    callers below, so only the import itself needs to succeed. The stub and the
    freshly imported adapter module are removed again afterward so this does not
    leak into other test modules, matching what
    `tests/adapters/test_reference_conversion_optional_sdk.py` checks for.
    """
    import infrahub_sync.adapters as adapters_package

    full_name = f"infrahub_sync.adapters.{adapter_module_name}"
    try:
        importlib.import_module(sdk_name)
        sdk_installed = True
    except ImportError:
        sdk_installed = False

    if sdk_installed:
        return importlib.import_module(full_name)

    stub = types.ModuleType(sdk_name)
    for name, value in sdk_attrs.items():
        setattr(stub, name, value)
    sys.modules[sdk_name] = stub
    sys.modules.pop(full_name, None)
    try:
        return importlib.import_module(full_name)
    finally:
        sys.modules.pop(sdk_name, None)
        sys.modules.pop(full_name, None)
        adapters_package.__dict__.pop(adapter_module_name, None)


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


def _build_netbox(target: str, config: SyncConfig) -> Any:  # noqa: ANN401 — concrete adapter type varies per builder
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


def _build_nautobot(target: str, config: SyncConfig) -> Any:  # noqa: ANN401 — concrete adapter type varies per builder
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


def _build_genericrestapi(target: str, config: SyncConfig) -> Any:  # noqa: ANN401 — concrete adapter type varies per builder
    from infrahub_sync.adapters.genericrestapi import GenericrestapiAdapter

    with patch.object(GenericrestapiAdapter, "_create_rest_client", return_value=MagicMock()):
        adapter = GenericrestapiAdapter(
            target=target,
            adapter=SyncAdapter(name="genericrestapi", settings={"url": "https://example.invalid"}),
            config=config,
        )
    cast("MagicMock", adapter.client).get.return_value = {}
    return adapter


def _build_ipfabricsync(target: str, config: SyncConfig) -> Any:  # noqa: ANN401 — concrete adapter type varies per builder
    module = _import_with_optional_sdk_stub("ipfabricsync", "ipfabric", IPFClient=object)
    adapter_cls = module.IpfabricsyncAdapter

    with patch.object(adapter_cls, "_create_ipfabric_client", return_value=MagicMock()):
        adapter = adapter_cls(
            target=target,
            adapter=SyncAdapter(name="ipfabricsync", settings={"base_url": "https://example.invalid", "token": "x"}),
            config=config,
        )
    adapter.client.fetch_all.return_value = []
    return adapter


def _build_slurpitsync(target: str, config: SyncConfig) -> Any:  # noqa: ANN401 — concrete adapter type varies per builder
    module = _import_with_optional_sdk_stub("slurpitsync", "slurpit")
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


def _build_aci(target: str, config: SyncConfig) -> Any:  # noqa: ANN401 — concrete adapter type varies per builder
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


def _build_prometheus(target: str, config: SyncConfig) -> Any:  # noqa: ANN401 — concrete adapter type varies per builder
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


_ADAPTER_BUILDERS: dict[str, Callable[[str, SyncConfig], Any]] = {
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


def _run_model_loader(adapter_key: str, *, target: str, source_name: str, dest_name: str) -> MagicMock:
    config = _config(source_name, dest_name, fields=_FIELDS_BY_ADAPTER.get(adapter_key))
    adapter = _ADAPTER_BUILDERS[adapter_key](target, config)
    model = _fake_model()

    if adapter_key == "slurpitsync":
        # Route slurpit's plain (non-dotted) mapping to a zero-record source
        # method instead of the real API surface.
        config.schema_mapping[0].mapping = "fake_resource"
        adapter.fake_resource = list

    adapter.model_loader(_ELEMENT_NAME, model)
    return model


@pytest.mark.parametrize("adapter_key", sorted(_ADAPTER_BUILDERS))
def test_filters_and_transforms_run_only_for_source_role_in_same_type_sync(adapter_key: str) -> None:
    """Same-type sync (source and destination both `adapter_key`): only the source instance filters/transforms."""
    source = _run_model_loader(adapter_key, target="source", source_name=adapter_key, dest_name=adapter_key)
    assert source.filter_records.called
    assert source.transform_records.called

    destination = _run_model_loader(adapter_key, target="destination", source_name=adapter_key, dest_name=adapter_key)
    assert not destination.filter_records.called
    assert not destination.transform_records.called


@pytest.mark.parametrize("adapter_key", sorted(_ADAPTER_BUILDERS))
def test_filters_and_transforms_unchanged_for_heterogeneous_sync(adapter_key: str) -> None:
    """Heterogeneous sync (different source/destination adapter types): behavior is unchanged by role."""
    source = _run_model_loader(adapter_key, target="source", source_name=adapter_key, dest_name="infrahub")
    assert source.filter_records.called
    assert source.transform_records.called

    destination = _run_model_loader(adapter_key, target="destination", source_name="infrahub", dest_name=adapter_key)
    assert not destination.filter_records.called
    assert not destination.transform_records.called
