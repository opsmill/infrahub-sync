from __future__ import annotations

from infrahub_sync.plugin_loader import PluginLoader

from .sync_models import (
    CoreStandardGroup,
    InfraDevice,
    IpamIPAddress,
)

# Load adapter class dynamically at runtime

_AdapterBaseClass = PluginLoader().resolve("infrahub")


# -------------------------------------------------------
# Generated file - do not edit.
# -------------------------------------------------------
class InfrahubSync(_AdapterBaseClass):
    CoreStandardGroup = CoreStandardGroup
    InfraDevice = InfraDevice
    IpamIPAddress = IpamIPAddress
