"""The `service` extra installs every SDK a bundled adapter imports at module top.

`ipfabric`, `slurpit-sdk`, and `prometheus-client` are declared for `python_version >= '3.11'`
only (see `pyproject.toml`), matching the direct-Prefect profile used on Python 3.10, which
has no `service` extra at all. Below 3.11 these tests skip; at 3.11+ the SDK is expected to be
installed, so a missing import or a failing construction is a real failure, not a skip.
"""

from __future__ import annotations

import importlib
import sys
from collections.abc import Callable, Iterator
from typing import Any
from unittest import mock

import pytest

import infrahub_sync.adapters as adapters_package
from infrahub_sync import SyncAdapter

requires_service_profile = pytest.mark.skipif(
    sys.version_info < (3, 11), reason="the `service` extra is only declared for python_version >= 3.11"
)


@pytest.fixture
def restore_adapter_module() -> Iterator[Callable[[str, str], None]]:
    """Undo a successful import's `sys.modules`/package-attribute side effects.

    `tests/adapters/test_reference_conversion_optional_sdk.py` reimports these same modules
    against stub SDKs and depends on that not having happened yet.
    """
    restores: list[tuple[str, str, bool, Any, bool, Any]] = []

    def register(module_name: str, full_name: str) -> None:
        had_module = full_name in sys.modules
        prior_module = sys.modules.get(full_name)
        had_attr = hasattr(adapters_package, module_name)
        prior_attr = getattr(adapters_package, module_name, None)
        restores.append((module_name, full_name, had_module, prior_module, had_attr, prior_attr))

    yield register

    for module_name, full_name, had_module, prior_module, had_attr, prior_attr in restores:
        if had_module:
            sys.modules[full_name] = prior_module
        else:
            sys.modules.pop(full_name, None)
        if had_attr:
            setattr(adapters_package, module_name, prior_attr)
        else:
            adapters_package.__dict__.pop(module_name, None)


@requires_service_profile
@pytest.mark.parametrize(
    ("module_name", "sdk_name"),
    [
        ("ipfabricsync", "ipfabric"),
        ("slurpitsync", "slurpit"),
        ("prometheus", "prometheus_client"),
    ],
)
def test_adapter_imports_without_error_in_service_profile(
    module_name: str, sdk_name: str, restore_adapter_module
) -> None:
    full_name = f"infrahub_sync.adapters.{module_name}"
    restore_adapter_module(module_name, full_name)
    importlib.import_module(full_name)
    assert sdk_name in sys.modules


@requires_service_profile
def test_ipfabric_adapter_constructs_client_without_live_call(restore_adapter_module) -> None:
    full_name = "infrahub_sync.adapters.ipfabricsync"
    restore_adapter_module("ipfabricsync", full_name)
    module = importlib.import_module(full_name)

    from ipfabric.api import IPFabricAPI
    from ipfabric.auth import Setup
    from ipfabric.models.snapshots import Snapshots

    adapter = SyncAdapter(
        name="ipfabricsync",
        settings={"base_url": "https://ipfabric.example", "auth": "test-token", "verify_ssl": False},
    )
    instance = module.IpfabricsyncAdapter.__new__(module.IpfabricsyncAdapter)
    with (
        mock.patch.object(Setup, "check_version", return_value=("v8.0", "8.0.1", "v8.0")),
        mock.patch.object(IPFabricAPI, "get_user", return_value=mock.MagicMock()),
        mock.patch.object(IPFabricAPI, "hostname", new_callable=mock.PropertyMock, return_value="mock-host"),
        mock.patch.object(Snapshots, "get_snapshots", return_value={}),
    ):
        client = instance._create_ipfabric_client(adapter)
    assert client.verify is False


@requires_service_profile
def test_ipfabric_adapter_passes_verify_not_verify_ssl(restore_adapter_module) -> None:
    full_name = "infrahub_sync.adapters.ipfabricsync"
    restore_adapter_module("ipfabricsync", full_name)
    module = importlib.import_module(full_name)

    adapter = SyncAdapter(
        name="ipfabricsync",
        settings={"base_url": "https://ipfabric.example", "auth": "test-token", "verify_ssl": False},
    )
    instance = module.IpfabricsyncAdapter.__new__(module.IpfabricsyncAdapter)
    with mock.patch.object(module, "IPFClient") as ipf_client:
        instance._create_ipfabric_client(adapter)
    _, kwargs = ipf_client.call_args
    assert kwargs["verify"] is False
    assert "verify_ssl" not in kwargs


@requires_service_profile
def test_slurpit_adapter_constructs_client_without_live_call(restore_adapter_module) -> None:
    full_name = "infrahub_sync.adapters.slurpitsync"
    restore_adapter_module("slurpitsync", full_name)
    module = importlib.import_module(full_name)

    adapter = SyncAdapter(name="slurpitsync", settings={"url": "https://slurpit.example", "api_key": "test-api-key"})
    instance = module.SlurpitsyncAdapter.__new__(module.SlurpitsyncAdapter)
    with mock.patch("slurpit.apis.deviceapi.DeviceAPI.get_devices", return_value=[]):
        client = instance._create_slurpit_client(adapter)
    assert client.api_key == "test-api-key"


@requires_service_profile
def test_slurpit_adapter_warns_when_verify_disabled_but_unsupported(caplog, restore_adapter_module) -> None:
    full_name = "infrahub_sync.adapters.slurpitsync"
    restore_adapter_module("slurpitsync", full_name)
    module = importlib.import_module(full_name)

    adapter = SyncAdapter(
        name="slurpitsync",
        settings={"url": "https://slurpit.example", "api_key": "test-api-key", "verify_ssl": False},
    )
    instance = module.SlurpitsyncAdapter.__new__(module.SlurpitsyncAdapter)
    with (
        mock.patch("slurpit.apis.deviceapi.DeviceAPI.get_devices", return_value=[]),
        caplog.at_level("WARNING", logger=module.logger.name),
    ):
        instance._create_slurpit_client(adapter)
    assert "does not support disabling" in caplog.text


@requires_service_profile
def test_slurpit_adapter_does_not_warn_when_verify_enabled(caplog, restore_adapter_module) -> None:
    full_name = "infrahub_sync.adapters.slurpitsync"
    restore_adapter_module("slurpitsync", full_name)
    module = importlib.import_module(full_name)

    adapter = SyncAdapter(
        name="slurpitsync",
        settings={"url": "https://slurpit.example", "api_key": "test-api-key", "verify_ssl": True},
    )
    instance = module.SlurpitsyncAdapter.__new__(module.SlurpitsyncAdapter)
    with (
        mock.patch("slurpit.apis.deviceapi.DeviceAPI.get_devices", return_value=[]),
        caplog.at_level("WARNING", logger=module.logger.name),
    ):
        instance._create_slurpit_client(adapter)
    assert "does not support disabling" not in caplog.text
