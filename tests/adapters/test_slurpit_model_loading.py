"""Slurp'it model loading against the real `slurpit-sdk` call signatures (mocked, no live instance).

`slurpit-sdk` 0.9.x's `DeviceAPI.get_devices` and `PlanningAPI.search_plannings`/`get_plannings`
are synchronous, plain-`requests` calls, not coroutines, and `search_plannings` takes no `limit`
keyword. The adapter previously wrapped their results in `run_async` (built for its own async
helpers, not these) and passed `search_plannings` a `limit` it doesn't accept, so every mapping
that reached `unique_vendors`, `unique_device_type`, or `planning_results` failed immediately once
the real SDK was installed. `mock.patch.object(..., autospec=True)` enforces the real method
signature here, so a regression that reintroduces either mistake fails this test rather than only
failing against a live Slurp'it instance.
"""

from __future__ import annotations

import importlib
import sys
import types
from collections.abc import Iterator
from unittest import mock

import pytest

import infrahub_sync.adapters as adapters_package

requires_service_profile = pytest.mark.skipif(
    sys.version_info < (3, 11), reason="the `service` extra is only declared for python_version >= 3.11"
)


@pytest.fixture
def slurpitsync_module() -> Iterator[types.ModuleType]:
    """Import `infrahub_sync.adapters.slurpitsync` against the real, installed `slurpit-sdk`."""
    module_name = "slurpitsync"
    full_name = f"infrahub_sync.adapters.{module_name}"
    had_module = full_name in sys.modules
    prior_module = sys.modules.get(full_name)
    had_attr = hasattr(adapters_package, module_name)
    prior_attr = getattr(adapters_package, module_name, None)
    sys.modules.pop(full_name, None)

    yield importlib.import_module(full_name)

    if had_module and prior_module is not None:
        sys.modules[full_name] = prior_module
    else:
        sys.modules.pop(full_name, None)
    if had_attr:
        setattr(adapters_package, module_name, prior_attr)
    else:
        adapters_package.__dict__.pop(module_name, None)
    for leaked_module in [name for name in sys.modules if name == "slurpit" or name.startswith("slurpit.")]:
        del sys.modules[leaked_module]


def _adapter(module: types.ModuleType):
    """A `SlurpitsyncAdapter` with a real, unconnected `slurpit.api` client."""
    import slurpit

    instance = module.SlurpitsyncAdapter.__new__(module.SlurpitsyncAdapter)
    instance.client = slurpit.api(url="https://slurpit.example", api_key="test-api-key")
    return instance


@requires_service_profile
def test_unique_vendors_calls_get_devices_synchronously(slurpitsync_module) -> None:
    from slurpit.apis.deviceapi import DeviceAPI
    from slurpit.models.device import Device

    device = Device(id=1, hostname="edge1", fqdn="edge1.example", device_os="ios", disabled=0, brand="Cisco")
    instance = _adapter(slurpitsync_module)
    with mock.patch.object(DeviceAPI, "get_devices", autospec=True, return_value=[device]) as get_devices:
        result = instance.unique_vendors()
    get_devices.assert_called_once_with(instance.client.device)
    assert result == [{"brand": "Cisco"}]


@requires_service_profile
def test_unique_device_type_calls_get_devices_synchronously(slurpitsync_module) -> None:
    from slurpit.apis.deviceapi import DeviceAPI
    from slurpit.models.device import Device

    device = Device(
        id=1, hostname="edge1", fqdn="edge1.example", device_os="ios", disabled=0, brand="Cisco", device_type="router"
    )
    instance = _adapter(slurpitsync_module)
    with mock.patch.object(DeviceAPI, "get_devices", autospec=True, return_value=[device]) as get_devices:
        result = instance.unique_device_type()
    get_devices.assert_called_once_with(instance.client.device)
    assert result == [{"brand": "Cisco", "device_type": "router", "device_os": "ios"}]


@requires_service_profile
def test_planning_results_calls_search_plannings_without_limit_kwarg(slurpitsync_module) -> None:
    from slurpit.apis.planningapi import PlanningAPI

    # `Planning` (the real return type of `get_plannings`) has no `slug` attribute; the adapter's
    # own lookup-by-slug is a separate, pre-existing mismatch out of scope here. A plain object
    # standing in for it keeps this test focused on the call-signature bug this fix addresses.
    plan = mock.Mock()
    plan.to_dict.return_value = {"id": 42}
    plan.slug = "hardware-info"

    instance = _adapter(slurpitsync_module)
    with (
        mock.patch.object(PlanningAPI, "get_plannings", autospec=True, return_value=[plan]),
        mock.patch.object(
            PlanningAPI, "search_plannings", autospec=True, return_value=[{"Name": "eth0"}]
        ) as search_plannings,
    ):
        result = instance.planning_results("hardware-info")
    search_plannings.assert_called_once_with(instance.client.planning, {"planning_id": 42, "unique_results": True})
    assert result == [{"Name": "eth0"}]
