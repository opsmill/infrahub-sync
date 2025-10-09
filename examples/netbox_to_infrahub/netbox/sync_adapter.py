from __future__ import annotations

from infrahub_sync.plugin_loader import PluginLoader

from .sync_models import (
    BuiltinTag,
    ChoiceCircuitType,
    ChoiceDeviceType,
    CoreStandardGroup,
    InfraCircuit,
    InfraDevice,
    InfraIPAddress,
    InfraInterfaceL2L3,
    InfraPrefix,
    InfraProviderNetwork,
    InfraRack,
    InfraRouteTarget,
    InfraVLAN,
    InfraVRF,
    LocationGeneric,
    OrganizationGeneric,
    RoleGeneric,
)

# Load adapter class dynamically at runtime

_AdapterBaseClass = PluginLoader().resolve("netbox")


# -------------------------------------------------------
# AUTO-GENERATED FILE, DO NOT MODIFY
#  This file has been generated with the command `infrahub-sync generate`
#  All modifications will be lost the next time you reexecute this command
# -------------------------------------------------------
class NetboxSync(_AdapterBaseClass):
    CoreStandardGroup = CoreStandardGroup
    BuiltinTag = BuiltinTag
    ChoiceCircuitType = ChoiceCircuitType
    ChoiceDeviceType = ChoiceDeviceType
    InfraCircuit = InfraCircuit
    InfraDevice = InfraDevice
    InfraIPAddress = InfraIPAddress
    InfraInterfaceL2L3 = InfraInterfaceL2L3
    InfraPrefix = InfraPrefix
    InfraProviderNetwork = InfraProviderNetwork
    InfraRack = InfraRack
    InfraRouteTarget = InfraRouteTarget
    InfraVLAN = InfraVLAN
    InfraVRF = InfraVRF
    LocationGeneric = LocationGeneric
    OrganizationGeneric = OrganizationGeneric
    RoleGeneric = RoleGeneric
