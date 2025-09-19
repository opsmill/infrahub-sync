from __future__ import annotations

from infrahub_sync.plugin_loader import PluginLoader

from .sync_models import (
    InfraDevice,
)

# Load adapter class dynamically at runtime

_loader = PluginLoader()
_AdapterBaseClass = _loader.resolve("./examples/custom_adapter/custom_adapter_src/custom_adapter.py:MockdbAdapter")


# -------------------------------------------------------
# AUTO-GENERATED FILE, DO NOT MODIFY
#  This file has been generated with the command `infrahub-sync generate`
#  All modifications will be lost the next time you reexecute this command
# -------------------------------------------------------
class MockdbSync(_AdapterBaseClass):
    InfraDevice = InfraDevice
