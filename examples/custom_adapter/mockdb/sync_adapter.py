from __future__ import annotations

from infrahub_sync.plugin_loader import PluginLoader

from .sync_models import (
    InfraDevice,
)

# Load adapter class dynamically at runtime

_loader = PluginLoader()
_AdapterBaseClass = _loader.resolve("./examples/custom_adapter/custom_adapter_src/custom_adapter.py:MockdbAdapter")


# -------------------------------------------------------
# Generated file - do not edit.
# -------------------------------------------------------
class MockdbSync(_AdapterBaseClass):
    InfraDevice = InfraDevice
