"""Static values in the NetBox and Nautobot source adapters."""

from __future__ import annotations

import importlib

import pytest

pynetbox = pytest.importorskip("pynetbox")
pynautobot = pytest.importorskip("pynautobot")


def test_netbox_adapter_module_binds_the_real_sdk() -> None:
    """The cached adapter module still holds the real SDK, not an earlier test's stub.

    Resolved through `sys.modules` at run time, which is how `PluginLoader` loads
    adapters in production, so a module left bound to a stub is visible here.
    """
    module = importlib.import_module("infrahub_sync.adapters.netbox")

    assert module.pynetbox is pynetbox
    assert hasattr(module.pynetbox, "__file__")
