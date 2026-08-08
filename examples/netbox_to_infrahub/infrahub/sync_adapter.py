from __future__ import annotations

from infrahub_sync.plugin_loader import PluginLoader

from .sync_models import (
    BuiltinTag,
    DcimCircuit,
    DcimDevice,
    DcimDeviceType,
    DcimPlatform,
    InterfaceLag,
    InterfacePhysical,
    InterfaceVirtual,
    IpamAggregate,
    IpamIPAddress,
    IpamPrefix,
    IpamRouteTarget,
    IpamVLAN,
    IpamVLANGroup,
    IpamVRF,
    LocationRack,
    LocationSite,
    OrganizationManufacturer,
    OrganizationProvider,
    OrganizationRIR,
)

# Load adapter class dynamically at runtime

_AdapterBaseClass = PluginLoader().resolve("infrahub")


# -------------------------------------------------------
# AUTO-GENERATED FILE, DO NOT MODIFY
#  This file has been generated with the command `infrahub-sync generate`
#  All modifications will be lost the next time you reexecute this command
# -------------------------------------------------------
class InfrahubSync(_AdapterBaseClass):
    BuiltinTag = BuiltinTag
    DcimDeviceType = DcimDeviceType
    DcimPlatform = DcimPlatform
    DcimDevice = DcimDevice
    InterfacePhysical = InterfacePhysical
    InterfaceVirtual = InterfaceVirtual
    IpamPrefix = IpamPrefix
    IpamIPAddress = IpamIPAddress
    OrganizationManufacturer = OrganizationManufacturer
    OrganizationProvider = OrganizationProvider
    IpamAggregate = IpamAggregate
    OrganizationRIR = OrganizationRIR
    DcimCircuit = DcimCircuit
    InterfaceLag = InterfaceLag
    LocationSite = LocationSite
    LocationRack = LocationRack
    IpamVLAN = IpamVLAN
    IpamVLANGroup = IpamVLANGroup
    IpamVRF = IpamVRF
    IpamRouteTarget = IpamRouteTarget
