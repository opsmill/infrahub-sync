from infrahub_sync.adapters.observium import ObserviumAdapter

from .sync_models import (
    CoreStandardGroup,
    InfraDevice,
    IpamIPAddress,
)


# -------------------------------------------------------
# Generated file - do not edit.
# -------------------------------------------------------
class ObserviumSync(ObserviumAdapter):
    CoreStandardGroup = CoreStandardGroup
    InfraDevice = InfraDevice
    IpamIPAddress = IpamIPAddress
