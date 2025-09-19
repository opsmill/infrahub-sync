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
# AUTO-GENERATED FILE, DO NOT MODIFY
#  This file has been generated with the command `infrahub-sync generate`
#  All modifications will be lost the next time you reexecute this command
# -------------------------------------------------------
class GenericrestapiSync(_AdapterBaseClass):
    CoreStandardGroup = CoreStandardGroup
    InfraDevice = InfraDevice
    IpamIPAddress = IpamIPAddress
    LocationSite = LocationSite
