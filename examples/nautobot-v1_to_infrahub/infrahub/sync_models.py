from __future__ import annotations

from typing import Any

from infrahub_sync.adapters.infrahub import InfrahubModel

# -------------------------------------------------------
# AUTO-GENERATED FILE, DO NOT MODIFY
#  This file has been generated with the command `infrahub-sync generate`
#  All modifications will be lost the next time you reexecute this command
# -------------------------------------------------------


class CoreStandardGroup(InfrahubModel):
    _modelname = "CoreStandardGroup"
    _identifiers = ("name",)
    _attributes = ("description",)
    name: str
    description: str | None = None
    local_id: str | None = None
    local_data: Any | None = None


class BuiltinTag(InfrahubModel):
    _modelname = "BuiltinTag"
    _identifiers = ("name",)
    _attributes = ("description",)
    name: str
    description: str | None = None
    local_id: str | None = None
    local_data: Any | None = None


class InfraAutonomousSystem(InfrahubModel):
    _modelname = "InfraAutonomousSystem"
    _identifiers = ("name",)
    _attributes = ("organization", "description")
    name: str
    asn: int
    description: str | None = None
    organization: str
    local_id: str | None = None
    local_data: Any | None = None


class InfraCircuit(InfrahubModel):
    _modelname = "InfraCircuit"
    _identifiers = ("circuit_id",)
    _attributes = ("provider", "type", "tags", "description", "vendor_id")
    circuit_id: str
    description: str | None = None
    vendor_id: str | None = None
    provider: str
    type: str
    tags: list[str] | None
    local_id: str | None = None
    local_data: Any | None = None


class InfraDevice(InfrahubModel):
    _modelname = "InfraDevice"
    _identifiers = ("location", "organization", "name")
    _attributes = (
        "model",
        "rack",
        "role",
        "tags",
        "platform",
        "serial_number",
        "asset_tag",
    )
    name: str | None = None
    serial_number: str | None = None
    asset_tag: str | None = None
    location: str
    model: str
    rack: str | None = None
    role: str | None = None
    tags: list[str] | None
    platform: str | None = None
    organization: str | None = None
    local_id: str | None = None
    local_data: Any | None = None


class InfraFrontPort(InfrahubModel):
    _modelname = "InfraFrontPort"
    _identifiers = ("name", "device")
    _attributes = ("rear_port", "description", "port_type")
    name: str
    description: str | None = None
    port_type: str | None = None
    rear_port: list[str] | None
    device: str
    local_id: str | None = None
    local_data: Any | None = None


class InfraIPAddress(InfrahubModel):
    _modelname = "InfraIPAddress"
    _identifiers = ("address", "vrf")
    _attributes = ("organization", "role", "description")
    address: str
    description: str | None = None
    organization: str | None = None
    vrf: str | None = None
    role: str | None = None
    local_id: str | None = None
    local_data: Any | None = None


class InfraInterfaceL2L3(InfrahubModel):
    _modelname = "InfraInterfaceL2L3"
    _identifiers = ("name", "device")
    _attributes = (
        "tagged_vlan",
        "tags",
        "l2_mode",
        "description",
        "mgmt_only",
        "mac_address",
        "interface_type",
    )
    l2_mode: str | None = None
    name: str
    description: str | None = None
    mgmt_only: bool | None = False
    mac_address: str | None = None
    interface_type: str | None = None
    untagged_vlan: str | None = None
    tagged_vlan: list[str] | None
    device: str
    tags: list[str] | None
    local_id: str | None = None
    local_data: Any | None = None


class InfraPlatform(InfrahubModel):
    _modelname = "InfraPlatform"
    _identifiers = ("name", "manufacturer")
    _attributes = ("description", "napalm_driver")
    name: str
    description: str | None = None
    napalm_driver: str | None = None
    manufacturer: str
    local_id: str | None = None
    local_data: Any | None = None


class InfraPrefix(InfrahubModel):
    _modelname = "InfraPrefix"
    _identifiers = ("prefix", "vrf", "organization")
    _attributes = ("role", "vlan", "description")
    prefix: str
    description: str | None = None
    organization: str | None = None
    role: str | None = None
    vrf: str | None = None
    vlan: str | None = None
    local_id: str | None = None
    local_data: Any | None = None


class InfraProviderNetwork(InfrahubModel):
    _modelname = "InfraProviderNetwork"
    _identifiers = ("name",)
    _attributes = ("provider", "tags", "description", "vendor_id")
    name: str
    description: str | None = None
    vendor_id: str | None = None
    provider: str
    tags: list[str] | None
    local_id: str | None = None
    local_data: Any | None = None


class InfraRack(InfrahubModel):
    _modelname = "InfraRack"
    _identifiers = ("name",)
    _attributes = (
        "location",
        "role",
        "tags",
        "height",
        "facility_id",
        "serial_number",
        "asset_tag",
    )
    name: str
    height: int | None = None
    facility_id: str | None = None
    serial_number: str | None = None
    asset_tag: str | None = None
    location: str
    role: str | None = None
    tags: list[str] | None
    local_id: str | None = None
    local_data: Any | None = None


class InfraRearPort(InfrahubModel):
    _modelname = "InfraRearPort"
    _identifiers = ("name", "device")
    _attributes = ("description", "port_type")
    name: str
    description: str | None = None
    port_type: str | None = None
    device: str
    local_id: str | None = None
    local_data: Any | None = None


class InfraRouteTarget(InfrahubModel):
    _modelname = "InfraRouteTarget"
    _identifiers = ("name", "organization")
    _attributes = ("description",)
    name: str
    description: str | None = None
    organization: str | None = None
    local_id: str | None = None
    local_data: Any | None = None


class InfraVLAN(InfrahubModel):
    _modelname = "InfraVLAN"
    _identifiers = ("name", "vlan_id", "location", "organization")
    _attributes = ("role", "vlan_group", "description")
    name: str
    description: str | None = None
    vlan_id: int
    organization: str | None = None
    role: str | None = None
    vlan_group: str | None = None
    local_id: str | None = None
    local_data: Any | None = None


class InfraVRF(InfrahubModel):
    _modelname = "InfraVRF"
    _identifiers = ("name",)
    _attributes = ("organization", "import_rt", "export_rt", "description", "vrf_rd")
    name: str
    description: str | None = None
    vrf_rd: str | None = None
    organization: str | None = None
    import_rt: list[str] | None
    export_rt: list[str] | None
    local_id: str | None = None
    local_data: Any | None = None


class LocationGeneric(InfrahubModel):
    _modelname = "LocationGeneric"
    _identifiers = ("name",)
    _attributes = ("organization", "location_type", "description", "type")
    name: str
    description: str | None = None
    type: str
    organization: str | None = None
    location_type: str | None = None
    local_id: str | None = None
    local_data: Any | None = None


class RoleGeneric(InfrahubModel):
    _modelname = "RoleGeneric"
    _identifiers = ("name",)
    _attributes = ("description",)
    name: str
    description: str | None = None
    local_id: str | None = None
    local_data: Any | None = None


class StatusGeneric(InfrahubModel):
    _modelname = "StatusGeneric"
    _identifiers = ("name",)
    _attributes = ("label", "description")
    name: str
    label: str | None = None
    description: str | None = None
    local_id: str | None = None
    local_data: Any | None = None


class TemplateCircuitType(InfrahubModel):
    _modelname = "TemplateCircuitType"
    _identifiers = ("name",)
    _attributes = ("description",)
    name: str
    description: str | None = None
    local_id: str | None = None
    local_data: Any | None = None


class TemplateDeviceType(InfrahubModel):
    _modelname = "TemplateDeviceType"
    _identifiers = ("name", "manufacturer")
    _attributes = ("tags", "part_number", "height", "full_depth")
    part_number: str | None = None
    height: int | None = None
    full_depth: bool | None = None
    name: str
    manufacturer: str
    tags: list[str] | None
    local_id: str | None = None
    local_data: Any | None = None


class TemplateLocationType(InfrahubModel):
    _modelname = "TemplateLocationType"
    _identifiers = ("name",)
    _attributes = ("description",)
    name: str
    description: str | None = None
    local_id: str | None = None
    local_data: Any | None = None
