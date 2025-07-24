from __future__ import annotations

from typing import Any

from infrahub_sync.adapters.ipfabricsync import IpfabricsyncModel


# -------------------------------------------------------
# AUTO-GENERATED FILE, DO NOT MODIFY
#  This file has been generated with the command `infrahub-sync generate`
#  All modifications will be lost the next time you reexecute this command
# -------------------------------------------------------
class InfraDevice(IpfabricsyncModel):
    _modelname = "InfraDevice"
    _identifiers = ("hostname",)
    _attributes = (
        "model",
        "location",
        "platform",
        "version",
        "fqdn",
        "serial_number",
        "hardware_serial_number",
    )
    fqdn: str | None = None
    hostname: str
    serial_number: str
    hardware_serial_number: str
    model: str | None = None
    location: str
    platform: str | None = None
    version: str | None = None

    local_id: str | None = None
    local_data: Any | None = None


class InfraIPAddress(IpfabricsyncModel):
    _modelname = "InfraIPAddress"
    _identifiers = ("interface", "address")
    _attributes = ("prefix", "description")
    address: str
    description: str | None = None
    interface: str | None = None
    prefix: str | None = None

    local_id: str | None = None
    local_data: Any | None = None


class InfraInterfaceL3(IpfabricsyncModel):
    _modelname = "InfraInterfaceL3"
    _identifiers = ("device", "name")
    _attributes = ("description", "speed", "mtu", "mac_address")
    name: str
    description: str | None = None
    speed: int | None = None
    mtu: int | None = 1500
    mac_address: str | None = None
    device: str

    local_id: str | None = None
    local_data: Any | None = None


class InfraNOSVersion(IpfabricsyncModel):
    _modelname = "InfraNOSVersion"
    _identifiers = ("manufacturer", "model", "version")
    _attributes = ("platform",)
    version: str
    manufacturer: str
    platform: str | None = None
    model: str | None = None

    local_id: str | None = None
    local_data: Any | None = None


class InfraPartNumber(IpfabricsyncModel):
    _modelname = "InfraPartNumber"
    _identifiers = ("device", "name")
    _attributes = (
        "model",
        "manufacturer",
        "part_vid",
        "part_id",
        "description",
        "part_sn",
    )
    name: str
    part_vid: str | None = None
    part_id: str | None = None
    description: str | None = None
    part_sn: str | None = None
    device: str
    model: str | None = None
    manufacturer: str | None = None

    local_id: str | None = None
    local_data: Any | None = None


class InfraPlatform(IpfabricsyncModel):
    _modelname = "InfraPlatform"
    _identifiers = ("name",)
    _attributes = ("description",)
    description: str | None = None
    name: str

    local_id: str | None = None
    local_data: Any | None = None


class InfraPrefix(IpfabricsyncModel):
    _modelname = "InfraPrefix"
    _identifiers = ("vrf", "prefix")
    _attributes = ("vlan", "location")
    prefix: str
    vlan: str | None = None
    vrf: str | None = None
    location: str | None = None

    local_id: str | None = None
    local_data: Any | None = None


class InfraVLAN(IpfabricsyncModel):
    _modelname = "InfraVLAN"
    _identifiers = ("location", "vlan_id")
    _attributes = ("description", "name")
    vlan_id: int
    description: str | None = None
    name: str | None = None
    location: str

    local_id: str | None = None
    local_data: Any | None = None


class InfraVRF(IpfabricsyncModel):
    _modelname = "InfraVRF"
    _identifiers = ("name",)
    _attributes = ("vrf_rd",)
    name: str
    vrf_rd: str | None = None

    local_id: str | None = None
    local_data: Any | None = None


class LocationGeneric(IpfabricsyncModel):
    _modelname = "LocationGeneric"
    _identifiers = ("name",)
    _attributes = ("type", "description")
    type: str
    description: str | None = None
    name: str

    local_id: str | None = None
    local_data: Any | None = None


class OrganizationGeneric(IpfabricsyncModel):
    _modelname = "OrganizationGeneric"
    _identifiers = ("name",)
    _attributes = ("type",)
    name: str
    type: str | None = None

    local_id: str | None = None
    local_data: Any | None = None


class ChoiceDeviceType(IpfabricsyncModel):
    _modelname = "ChoiceDeviceType"
    _identifiers = ("name", "manufacturer")
    _attributes = ("description",)
    description: str | None = None
    name: str
    manufacturer: str | None = None

    local_id: str | None = None
    local_data: Any | None = None
