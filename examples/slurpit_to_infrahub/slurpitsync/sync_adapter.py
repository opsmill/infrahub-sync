from infrahub_sync.adapters.slurpitsync import SlurpitsyncAdapter

from .sync_models import (
    ChoiceDeviceType,
    InfraDevice,
    InfraHardwareInfo,
    InfraInterface,
    InfraIPAddress,
    InfraPlatform,
    InfraPrefix,
    InfraVersion,
    InfraVLAN,
    InfraVRF,
    LocationGeneric,
    OrganizationGeneric,
)


# -------------------------------------------------------
# Generated file - do not edit.
# -------------------------------------------------------
class SlurpitsyncSync(SlurpitsyncAdapter):
    InfraDevice = InfraDevice
    InfraHardwareInfo = InfraHardwareInfo
    InfraIPAddress = InfraIPAddress
    InfraInterface = InfraInterface
    InfraPlatform = InfraPlatform
    InfraPrefix = InfraPrefix
    InfraVLAN = InfraVLAN
    InfraVRF = InfraVRF
    InfraVersion = InfraVersion
    LocationGeneric = LocationGeneric
    OrganizationGeneric = OrganizationGeneric
    ChoiceDeviceType = ChoiceDeviceType
