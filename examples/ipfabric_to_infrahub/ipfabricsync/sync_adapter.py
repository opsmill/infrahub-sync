from infrahub_sync.adapters.ipfabricsync import IpfabricsyncAdapter

from .sync_models import (
    ChoiceDeviceType,
    InfraDevice,
    InfraInterfaceL3,
    InfraIPAddress,
    InfraNOSVersion,
    InfraPartNumber,
    InfraPlatform,
    InfraPrefix,
    InfraVLAN,
    InfraVRF,
    LocationGeneric,
    OrganizationGeneric,
)


# -------------------------------------------------------
# Generated file - do not edit.
# -------------------------------------------------------
class IpfabricsyncSync(IpfabricsyncAdapter):
    InfraDevice = InfraDevice
    InfraIPAddress = InfraIPAddress
    InfraInterfaceL3 = InfraInterfaceL3
    InfraNOSVersion = InfraNOSVersion
    InfraPartNumber = InfraPartNumber
    InfraPlatform = InfraPlatform
    InfraPrefix = InfraPrefix
    InfraVLAN = InfraVLAN
    InfraVRF = InfraVRF
    LocationGeneric = LocationGeneric
    OrganizationGeneric = OrganizationGeneric
    ChoiceDeviceType = ChoiceDeviceType
