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


class ChoiceCircuitType(InfrahubModel):
    _modelname = "ChoiceCircuitType"
    _identifiers = ("name",)
    _attributes = ("tags", "description")
    description: str | None = None
    name: str
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
    _attributes = ("tags", "type", "provider", "vendor_id", "description")
    circuit_id: str
    vendor_id: str | None = None
    description: str | None = None
    tags: list[str] | None = []
    type: str
    provider: str

    local_id: str | None = None
    local_data: Any | None = None


class InfraDevice(InfrahubModel):
    _modelname = "InfraDevice"
    _identifiers = ("location", "rack", "organization", "name")
    _attributes = ("tags", "model", "role", "description", "serial_number", "asset_tag")
    description: str | None = None
    serial_number: str | None = None
    name: str | None = None
    asset_tag: str | None = None
    location: str
    organization: str | None = None
    rack: str | None = None
    tags: list[str] | None = []
    model: str
    role: str | None = None

    local_id: str | None = None
    local_data: Any | None = None


class InfraIPAddress(InfrahubModel):
    _modelname = "InfraIPAddress"
    _identifiers = ("address", "vrf")
    _attributes = ("organization", "description")
    address: str
    description: str | None = None
    organization: str | None = None
    vrf: str | None = None

    local_id: str | None = None
    local_data: Any | None = None


class InfraInterfaceL2L3(InfrahubModel):
    _modelname = "InfraInterfaceL2L3"
    _identifiers = ("device", "name")
    _attributes = ("tagged_vlan", "tags", "mac_address", "description", "l2_mode", "interface_type", "mgmt_only")
    name: str
    mac_address: str | None = None
    description: str | None = None
    l2_mode: str | None = None
    interface_type: str | None = None
    mgmt_only: bool | None = False
    untagged_vlan: str | None = None
    tagged_vlan: list[str] | None = []
    device: str
    tags: list[str] | None = []

    local_id: str | None = None
    local_data: Any | None = None


class InfraPrefix(InfrahubModel):
    _modelname = "InfraPrefix"
    _identifiers = ("prefix", "vrf")
    _attributes = ("organization", "role", "description")
    prefix: str
    description: str | None = None
    organization: str | None = None
    role: str | None = None
    vrf: str | None = None

    local_id: str | None = None
    local_data: Any | None = None


class InfraProviderNetwork(InfrahubModel):
    _modelname = "InfraProviderNetwork"
    _identifiers = ("name",)
    _attributes = ("provider", "tags", "vendor_id", "description")
    vendor_id: str | None = None
    name: str
    description: str | None = None
    provider: str
    tags: list[str] | None = []

    local_id: str | None = None
    local_data: Any | None = None


class InfraRack(InfrahubModel):
    _modelname = "InfraRack"
    _identifiers = ("name", "location")
    _attributes = ("tags", "role", "serial_number", "facility_id", "height", "asset_tag")
    name: str
    serial_number: str | None = None
    facility_id: str | None = None
    height: int | None = None
    asset_tag: str | None = None
    tags: list[str] | None = []
    location: str
    role: str | None = None

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
    description: str | None = None
    vlan_id: int
    name: str
    vlan_group: str | None = None
    organization: str | None = None

    local_id: str | None = None
    local_data: Any | None = None


class InfraVRF(InfrahubModel):
    _modelname = "InfraVRF"
    _identifiers = ("name",)
    _attributes = ("organization", "export_rt", "import_rt", "description", "vrf_rd")
    description: str | None = None
    vrf_rd: str | None = None
    name: str
    organization: str | None = None
    export_rt: list[str] | None = []
    import_rt: list[str] | None = []

    local_id: str | None = None
    local_data: Any | None = None


class LocationGeneric(InfrahubModel):
    _modelname = "LocationGeneric"
    _identifiers = ("name",)
    _attributes = ("group", "organization", "tags", "description")
    name: str
    description: str | None = None
    group: str | None = None
    organization: str | None = None
    tags: list[str] | None = []

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
