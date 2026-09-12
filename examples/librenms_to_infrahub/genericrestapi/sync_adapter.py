from __future__ import annotations

from infrahub_sync.plugin_loader import PluginLoader

from .sync_models import (
    CoreStandardGroup,
    InfraDevice,
    IpamIPAddress,
    LocationSite,
)

# Load adapter class dynamically at runtime

_AdapterBaseClass = PluginLoader().resolve("genericrestapi")


# -------------------------------------------------------
# Generated file - do not edit.
# -------------------------------------------------------
class GenericrestapiSync(_AdapterBaseClass):
    CoreStandardGroup = CoreStandardGroup
    InfraDevice = InfraDevice
    IpamIPAddress = IpamIPAddress
    LocationSite = LocationSite
