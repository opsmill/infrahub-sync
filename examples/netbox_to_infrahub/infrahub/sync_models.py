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
    description: str | None = None
    name: str

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


class InfraCircuit(InfrahubModel):
    _modelname = "InfraCircuit"
    _identifiers = ("circuit_id",)
    _attributes = ("tags", "provider", "type", "vendor_id", "description")
    circuit_id: str
    vendor_id: str | None = None
    description: str | None = None
    tags: list[str] | None = []
    provider: str
    type: str

    local_id: str | None = None
    local_data: Any | None = None


class TemplateCircuitType(InfrahubModel):
    _modelname = "TemplateCircuitType"
    _identifiers = ("name",)
    _attributes = ("tags", "description")
    name: str
    description: str | None = None
    tags: list[str] | None = []

    local_id: str | None = None
    local_data: Any | None = None


class InfraDevice(InfrahubModel):
    _modelname = "InfraDevice"
    _identifiers = ("location", "rack", "organization", "name")
    _attributes = ("model", "tags", "role", "asset_tag", "description", "serial_number")
    asset_tag: str | None = None
    description: str | None = None
    serial_number: str | None = None
    name: str | None = None
    model: str
    tags: list[str] | None = []
    role: str | None = None
    location: str
    organization: str | None = None
    rack: str | None = None

    local_id: str | None = None
    local_data: Any | None = None


class TemplateDeviceType(InfrahubModel):
    _modelname = "TemplateDeviceType"
    _identifiers = ("name", "manufacturer")
    _attributes = ("tags", "part_number", "height", "full_depth")
    part_number: str | None = None
    height: int | None = None
    name: str
    full_depth: bool | None = None
    tags: list[str] | None = []
    manufacturer: str

    local_id: str | None = None
    local_data: Any | None = None


class InfraInterfaceL2L3(InfrahubModel):
    _modelname = "InfraInterfaceL2L3"
    _identifiers = ("device", "name")
    _attributes = ("tagged_vlan", "tags", "l2_mode", "description", "mgmt_only", "mac_address", "interface_type")
    l2_mode: str | None = None
    name: str
    description: str | None = None
    mgmt_only: bool | None = False
    mac_address: str | None = None
    interface_type: str | None = None
    untagged_vlan: str | None = None
    tagged_vlan: list[str] | None = []
    device: str
    tags: list[str] | None = []

    local_id: str | None = None
    local_data: Any | None = None


class InfraIPAddress(InfrahubModel):
    _modelname = "InfraIPAddress"
    _identifiers = ("address", "vrf")
    _attributes = ("organization", "description")
    description: str | None = None
    address: str
    organization: str | None = None
    vrf: str | None = None

    local_id: str | None = None
    local_data: Any | None = None


class InfraProviderNetwork(InfrahubModel):
    _modelname = "InfraProviderNetwork"
    _identifiers = ("name",)
    _attributes = ("tags", "provider", "description", "vendor_id")
    name: str
    description: str | None = None
    vendor_id: str | None = None
    tags: list[str] | None = []
    provider: str

    local_id: str | None = None
    local_data: Any | None = None


class InfraPrefix(InfrahubModel):
    _modelname = "InfraPrefix"
    _identifiers = ("prefix", "vrf")
    _attributes = ("organization", "location", "role", "description")
    prefix: str
    description: str | None = None
    organization: str | None = None
    location: str | None = None
    role: str | None = None
    vrf: str | None = None

    local_id: str | None = None
    local_data: Any | None = None


class InfraRack(InfrahubModel):
    _modelname = "InfraRack"
    _identifiers = ("name", "location")
    _attributes = ("role", "tags", "asset_tag", "height", "serial_number", "facility_id")
    asset_tag: str | None = None
    height: int | None = None
    serial_number: str | None = None
    name: str
    facility_id: str | None = None
    role: str | None = None
    location: str
    tags: list[str] | None = []

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
    _identifiers = ("name", "vlan_id", "location", "vlan_group")
    _attributes = ("organization", "description")
    name: str
    description: str | None = None
    vlan_id: int
    organization: str | None = None
    location: str | None = None
    vlan_group: str | None = None

    local_id: str | None = None
    local_data: Any | None = None


class InfraVRF(InfrahubModel):
    _modelname = "InfraVRF"
    _identifiers = ("name",)
    _attributes = ("export_rt", "organization", "import_rt", "vrf_rd", "description")
    vrf_rd: str | None = None
    description: str | None = None
    name: str
    export_rt: list[str] | None = []
    organization: str | None = None
    import_rt: list[str] | None = []

    local_id: str | None = None
    local_data: Any | None = None


class OrganizationGeneric(InfrahubModel):
    _modelname = "OrganizationGeneric"
    _identifiers = ("name",)
    _attributes = ("group", "description")
    name: str
    description: str | None = None
    group: str | None = None

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


class LocationGeneric(InfrahubModel):
    _modelname = "LocationGeneric"
    _identifiers = ("name",)
    _attributes = ("organization", "tags", "group", "description", "type")
    name: str
    description: str | None = None
    type: str
    organization: str | None = None
    tags: list[str] | None = []
    group: str | None = None

    local_id: str | None = None
    local_data: Any | None = None
