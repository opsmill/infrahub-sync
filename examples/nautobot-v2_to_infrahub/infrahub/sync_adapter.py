from __future__ import annotations

from infrahub_sync.plugin_loader import PluginLoader

from .sync_models import (
    BuiltinTag,
    ChoiceCircuitType,
    ChoiceDeviceType,
    ChoiceLocationType,
    CoreStandardGroup,
    InfraAutonomousSystem,
    InfraCircuit,
    InfraDevice,
    InfraFrontPort,
    InfraInterfaceL2L3,
    InfraIPAddress,
    InfraPlatform,
    InfraPrefix,
    InfraProviderNetwork,
    InfraRack,
    InfraRearPort,
    InfraRouteTarget,
    InfraVLAN,
    InfraVRF,
    LocationGeneric,
    NautobotNamespace,
    OrganizationGeneric,
    RoleGeneric,
    StatusGeneric,
)

# Load adapter class dynamically at runtime

_AdapterBaseClass = PluginLoader().resolve("infrahub")


# -------------------------------------------------------
# AUTO-GENERATED FILE, DO NOT MODIFY
#  This file has been generated with the command `infrahub-sync generate`
#  All modifications will be lost the next time you reexecute this command
# -------------------------------------------------------
class InfrahubSync(_AdapterBaseClass):
    CoreStandardGroup = CoreStandardGroup
    BuiltinTag = BuiltinTag
    InfraAutonomousSystem = InfraAutonomousSystem
    InfraCircuit = InfraCircuit
    ChoiceCircuitType = ChoiceCircuitType
    InfraDevice = InfraDevice
    ChoiceDeviceType = ChoiceDeviceType
    InfraFrontPort = InfraFrontPort
    InfraInterfaceL2L3 = InfraInterfaceL2L3
    InfraIPAddress = InfraIPAddress
    ChoiceLocationType = ChoiceLocationType
    NautobotNamespace = NautobotNamespace
    InfraPlatform = InfraPlatform
    InfraProviderNetwork = InfraProviderNetwork
    InfraPrefix = InfraPrefix
    InfraRack = InfraRack
    InfraRearPort = InfraRearPort
    InfraRouteTarget = InfraRouteTarget
    InfraVLAN = InfraVLAN
    InfraVRF = InfraVRF
    OrganizationGeneric = OrganizationGeneric
    StatusGeneric = StatusGeneric
    RoleGeneric = RoleGeneric
    LocationGeneric = LocationGeneric
