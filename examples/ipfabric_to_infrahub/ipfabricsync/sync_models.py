from typing import Any, Optional

from infrahub_sync.adapters.ipfabricsync import IpfabricsyncModel


# -------------------------------------------------------
# AUTO-GENERATED FILE, DO NOT MODIFY
#  This file has been generated with the command `infrahub-sync generate`
#  All modifications will be lost the next time you reexecute this command
# -------------------------------------------------------
class InfraDevice(IpfabricsyncModel):
    _modelname = "InfraDevice"
    _identifiers = ("hostname",)
    _attributes = ("model", "location", "platform", "version", "fqdn", "serial_number", "hardware_serial_number")
    fqdn: Optional[str] = None
    hostname: str
    serial_number: str
    hardware_serial_number: str
    model: Optional[str] = None
    location: str
    platform: Optional[str] = None
    version: Optional[str] = None

    local_id: Optional[str] = None
    local_data: Optional[Any] = None


class InfraIPAddress(IpfabricsyncModel):
    _modelname = "InfraIPAddress"
    _identifiers = ("interface", "address")
    _attributes = ("prefix", "description")
    address: str
    description: Optional[str] = None
    interface: Optional[str] = None
    prefix: Optional[str] = None

    local_id: Optional[str] = None
    local_data: Optional[Any] = None


class InfraInterfaceL3(IpfabricsyncModel):
    _modelname = "InfraInterfaceL3"
    _identifiers = ("device", "name")
    _attributes = ("description", "speed", "mtu", "mac_address")
    name: str
    description: Optional[str] = None
    speed: Optional[int] = None
    mtu: Optional[int] = 1500
    mac_address: Optional[str] = None
    device: str

    local_id: Optional[str] = None
    local_data: Optional[Any] = None


class InfraNOSVersion(IpfabricsyncModel):
    _modelname = "InfraNOSVersion"
    _identifiers = ("manufacturer", "model", "version")
    _attributes = ("platform",)
    version: str
    manufacturer: str
    platform: Optional[str] = None
    model: Optional[str] = None

    local_id: Optional[str] = None
    local_data: Optional[Any] = None


class InfraPartNumber(IpfabricsyncModel):
    _modelname = "InfraPartNumber"
    _identifiers = ("device", "name")
    _attributes = ("model", "manufacturer", "part_vid", "part_id", "description", "part_sn")
    name: str
    part_vid: Optional[str] = None
    part_id: Optional[str] = None
    description: Optional[str] = None
    part_sn: Optional[str] = None
    device: str
    model: Optional[str] = None
    manufacturer: Optional[str] = None

    local_id: Optional[str] = None
    local_data: Optional[Any] = None


class InfraPlatform(IpfabricsyncModel):
    _modelname = "InfraPlatform"
    _identifiers = ("name",)
    _attributes = ("description",)
    description: Optional[str] = None
    name: str

    local_id: Optional[str] = None
    local_data: Optional[Any] = None


class InfraPrefix(IpfabricsyncModel):
    _modelname = "InfraPrefix"
    _identifiers = ("vrf", "prefix")
    _attributes = ("vlan", "location")
    prefix: str
    vlan: Optional[str] = None
    vrf: Optional[str] = None
    location: Optional[str] = None

    local_id: Optional[str] = None
    local_data: Optional[Any] = None


class InfraVLAN(IpfabricsyncModel):
    _modelname = "InfraVLAN"
    _identifiers = ("location", "vlan_id")
    _attributes = ("description", "name")
    vlan_id: int
    description: Optional[str] = None
    name: Optional[str] = None
    location: str

    local_id: Optional[str] = None
    local_data: Optional[Any] = None


class InfraVRF(IpfabricsyncModel):
    _modelname = "InfraVRF"
    _identifiers = ("name",)
    _attributes = ("vrf_rd",)
    name: str
    vrf_rd: Optional[str] = None

    local_id: Optional[str] = None
    local_data: Optional[Any] = None


class LocationGeneric(IpfabricsyncModel):
    _modelname = "LocationGeneric"
    _identifiers = ("name",)
    _attributes = ("type", "description")
    type: str
    description: Optional[str] = None
    name: str

    local_id: Optional[str] = None
    local_data: Optional[Any] = None


class OrganizationGeneric(IpfabricsyncModel):
    _modelname = "OrganizationGeneric"
    _identifiers = ("name",)
    _attributes = ("type",)
    name: str
    type: Optional[str] = None

    local_id: Optional[str] = None
    local_data: Optional[Any] = None


class TemplateDeviceType(IpfabricsyncModel):
    _modelname = "TemplateDeviceType"
    _identifiers = ("name", "manufacturer")
    _attributes = ("description",)
    description: Optional[str] = None
    name: str
    manufacturer: Optional[str] = None

    local_id: Optional[str] = None
    local_data: Optional[Any] = None
