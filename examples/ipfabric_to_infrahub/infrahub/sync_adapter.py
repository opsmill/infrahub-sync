from infrahub_sync.adapters.infrahub import InfrahubAdapter

from .sync_models import (
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
    ChoiceDeviceType,
)


# -------------------------------------------------------
# AUTO-GENERATED FILE, DO NOT MODIFY
#  This file has been generated with the command `infrahub-sync generate`
#  All modifications will be lost the next time you reexecute this command
# -------------------------------------------------------
class InfrahubSync(InfrahubAdapter):
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
