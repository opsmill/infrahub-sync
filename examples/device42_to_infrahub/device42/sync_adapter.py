from infrahub_sync.adapters.device42 import Device42Adapter

from .sync_models import (
    BuiltinTag,
    ChoiceDeviceType,
    InfraDevice,
    InfraInterfaceL2L3,
    InfraRack,
    LocationGeneric,
    OrganizationGeneric,
    RoleGeneric,
)


# -------------------------------------------------------
# AUTO-GENERATED FILE, DO NOT MODIFY
#  This file has been generated with the command `infrahub-sync generate`
#  All modifications will be lost the next time you reexecute this command
# -------------------------------------------------------
class Device42Sync(Device42Adapter):
    BuiltinTag = BuiltinTag
    ChoiceDeviceType = ChoiceDeviceType
    InfraDevice = InfraDevice
    InfraInterfaceL2L3 = InfraInterfaceL2L3
    InfraRack = InfraRack
    LocationGeneric = LocationGeneric
    OrganizationGeneric = OrganizationGeneric
    RoleGeneric = RoleGeneric