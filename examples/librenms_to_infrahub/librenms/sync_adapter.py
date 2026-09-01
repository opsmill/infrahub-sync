from infrahub_sync.adapters.librenms import LibrenmsAdapter

from .sync_models import (
    CoreStandardGroup,
    InfraDevice,
    IpamIPAddress,
    LocationSite,
)


# -------------------------------------------------------
# Generated file - do not edit.
# -------------------------------------------------------
class LibrenmsSync(LibrenmsAdapter):
    CoreStandardGroup = CoreStandardGroup
    InfraDevice = InfraDevice
    IpamIPAddress = IpamIPAddress
    LocationSite = LocationSite
