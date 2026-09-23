"""The `service` extra installs every SDK a bundled adapter imports at module top.

Skips per-adapter, rather than failing outright, when its SDK is absent: the base-install
CI job intentionally runs without the `service` extra to keep the Prefect-free guarantee
honest, and these adapters are only ever loaded through the Sync service in that profile.
"""

from __future__ import annotations

import importlib
import sys

import pytest

import infrahub_sync.adapters as adapters_package


@pytest.mark.parametrize(
    ("module_name", "sdk_name"),
    [
        ("ipfabricsync", "ipfabric"),
        ("slurpitsync", "slurpit"),
        ("prometheus", "prometheus_client"),
    ],
)
def test_adapter_imports_without_error_in_service_profile(module_name: str, sdk_name: str) -> None:
    pytest.importorskip(sdk_name, reason="requires the `service` extra")
    full_name = f"infrahub_sync.adapters.{module_name}"
    # A successful import here would otherwise leave `full_name` cached in `sys.modules`,
    # plus a same-name attribute set on `adapters_package`, for the rest of the test
    # session — `tests/adapters/test_reference_conversion_optional_sdk.py` reimports these
    # same modules against stub SDKs and depends on that not having happened yet.
    had_module = full_name in sys.modules
    prior_module = sys.modules.get(full_name)
    had_attr = hasattr(adapters_package, module_name)
    prior_attr = getattr(adapters_package, module_name, None)
    try:
        importlib.import_module(full_name)
    finally:
        if had_module:
            sys.modules[full_name] = prior_module
        else:
            sys.modules.pop(full_name, None)
        if had_attr:
            setattr(adapters_package, module_name, prior_attr)
        else:
            adapters_package.__dict__.pop(module_name, None)
