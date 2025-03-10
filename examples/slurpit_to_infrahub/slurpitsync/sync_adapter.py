from infrahub_sync.adapters.slurpitsync import SlurpitsyncAdapter

from .sync_models import (
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
    TemplateDeviceType,
)


# -------------------------------------------------------
# AUTO-GENERATED FILE, DO NOT MODIFY
#  This file has been generated with the command `infrahub-sync generate`
#  All modifications will be lost the next time you reexecute this command
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
    TemplateDeviceType = TemplateDeviceType
