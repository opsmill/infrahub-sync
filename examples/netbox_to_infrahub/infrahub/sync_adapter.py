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
# Generated file - do not edit.
# -------------------------------------------------------
class InfrahubSync(_AdapterBaseClass):
    BuiltinTag = BuiltinTag
    DcimCircuit = DcimCircuit
    DcimDevice = DcimDevice
    DcimDeviceType = DcimDeviceType
    DcimPlatform = DcimPlatform
    InterfaceLag = InterfaceLag
    InterfacePhysical = InterfacePhysical
    InterfaceVirtual = InterfaceVirtual
    IpamAggregate = IpamAggregate
    IpamIPAddress = IpamIPAddress
    IpamPrefix = IpamPrefix
    IpamRouteTarget = IpamRouteTarget
    IpamVLAN = IpamVLAN
    IpamVLANGroup = IpamVLANGroup
    IpamVRF = IpamVRF
    LocationRack = LocationRack
    LocationSite = LocationSite
    OrganizationManufacturer = OrganizationManufacturer
    OrganizationProvider = OrganizationProvider
    OrganizationRIR = OrganizationRIR
