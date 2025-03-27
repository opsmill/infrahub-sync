from infrahub_sync.adapters.infrahub import InfrahubAdapter

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


# -------------------------------------------------------
# AUTO-GENERATED FILE, DO NOT MODIFY
#  This file has been generated with the command `infrahub-sync generate`
#  All modifications will be lost the next time you reexecute this command
# -------------------------------------------------------
class InfrahubSync(InfrahubAdapter):
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
