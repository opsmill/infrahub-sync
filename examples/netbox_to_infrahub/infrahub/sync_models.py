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
    description: str | None = None
    name: str

    local_id: str | None = None
    local_data: Any | None = None


class ChoiceCircuitType(InfrahubModel):
    _modelname = "ChoiceCircuitType"
    _identifiers = ("name",)
    _attributes = ("tags", "description")
    name: str
    description: str | None = None
    tags: list[str] | None = []

    local_id: str | None = None
    local_data: Any | None = None


class ChoiceDeviceType(InfrahubModel):
    _modelname = "ChoiceDeviceType"
    _identifiers = ("name", "manufacturer")
    _attributes = ("tags", "part_number", "full_depth", "height")
    name: str
    part_number: str | None = None
    full_depth: bool | None = None
    height: int | None = None
    manufacturer: str
    tags: list[str] | None = []

    local_id: str | None = None
    local_data: Any | None = None


class InfraCircuit(InfrahubModel):
    _modelname = "InfraCircuit"
    _identifiers = ("circuit_id",)
    _attributes = ("tags", "provider", "type", "description", "vendor_id")
    description: str | None = None
    circuit_id: str
    vendor_id: str | None = None
    tags: list[str] | None = []
    provider: str
    type: str

    local_id: str | None = None
    local_data: Any | None = None


class InfraDevice(InfrahubModel):
    _modelname = "InfraDevice"
    _identifiers = ("location", "rack", "organization", "name")
    _attributes = ("tags", "model", "role", "description", "serial_number", "asset_tag")
    description: str | None = None
    name: str | None = None
    serial_number: str | None = None
    asset_tag: str | None = None
    organization: str | None = None
    tags: list[str] | None = []
    location: str
    model: str
    role: str | None = None
    rack: str | None = None

    local_id: str | None = None
    local_data: Any | None = None


class InfraIPAddress(InfrahubModel):
    _modelname = "InfraIPAddress"
    _identifiers = ("address", "vrf")
    _attributes = ("organization", "description")
    address: str
    description: str | None = None
    vrf: str | None = None
    organization: str | None = None

    local_id: str | None = None
    local_data: Any | None = None


class InfraInterfaceL2L3(InfrahubModel):
    _modelname = "InfraInterfaceL2L3"
    _identifiers = ("device", "name")
    _attributes = ("tags", "tagged_vlan", "mgmt_only", "mac_address", "interface_type", "l2_mode", "description")
    mgmt_only: bool | None = False
    mac_address: str | None = None
    interface_type: str | None = None
    name: str
    l2_mode: str | None = None
    description: str | None = None
    device: str
    tags: list[str] | None = []
    untagged_vlan: str | None = None
    tagged_vlan: list[str] | None = []

    local_id: str | None = None
    local_data: Any | None = None


class InfraPrefix(InfrahubModel):
    _modelname = "InfraPrefix"
    _identifiers = ("prefix", "vrf")
    _attributes = ("organization", "role", "location", "description")
    description: str | None = None
    prefix: str
    vrf: str | None = None
    organization: str | None = None
    role: str | None = None
    location: str | None = None

    local_id: str | None = None
    local_data: Any | None = None


class InfraProviderNetwork(InfrahubModel):
    _modelname = "InfraProviderNetwork"
    _identifiers = ("name",)
    _attributes = ("provider", "tags", "vendor_id", "description")
    name: str
    vendor_id: str | None = None
    description: str | None = None
    provider: str
    tags: list[str] | None = []

    local_id: str | None = None
    local_data: Any | None = None


class InfraRack(InfrahubModel):
    _modelname = "InfraRack"
    _identifiers = ("name", "location")
    _attributes = ("tags", "role", "facility_id", "serial_number", "asset_tag", "height")
    facility_id: str | None = None
    serial_number: str | None = None
    asset_tag: str | None = None
    height: int | None = None
    name: str
    tags: list[str] | None = []
    role: str | None = None
    location: str

    local_id: str | None = None
    local_data: Any | None = None


class InfraRouteTarget(InfrahubModel):
    _modelname = "InfraRouteTarget"
    _identifiers = ("name", "organization")
    _attributes = ("description",)
    description: str | None = None
    name: str
    organization: str | None = None

    local_id: str | None = None
    local_data: Any | None = None


class InfraVLAN(InfrahubModel):
    _modelname = "InfraVLAN"
    _identifiers = ("name", "vlan_id", "location", "vlan_group")
    _attributes = ("organization", "description")
    name: str
    vlan_id: int
    description: str | None = None
    organization: str | None = None
    vlan_group: str | None = None
    location: str | None = None

    local_id: str | None = None
    local_data: Any | None = None


class InfraVRF(InfrahubModel):
    _modelname = "InfraVRF"
    _identifiers = ("name",)
    _attributes = ("organization", "import_rt", "export_rt", "description", "vrf_rd")
    description: str | None = None
    name: str
    vrf_rd: str | None = None
    organization: str | None = None
    import_rt: list[str] | None = []
    export_rt: list[str] | None = []

    local_id: str | None = None
    local_data: Any | None = None


class LocationGeneric(InfrahubModel):
    _modelname = "LocationGeneric"
    _identifiers = ("name",)
    _attributes = ("tags", "group", "organization", "description", "type")
    name: str
    description: str | None = None
    type: str
    tags: list[str] | None = []
    group: str | None = None
    organization: str | None = None

    local_id: str | None = None
    local_data: Any | None = None


class OrganizationGeneric(InfrahubModel):
    _modelname = "OrganizationGeneric"
    _identifiers = ("name",)
    _attributes = ("group", "description")
    description: str | None = None
    name: str
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
