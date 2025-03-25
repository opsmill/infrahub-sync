from __future__ import annotations

from typing import Any, List

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
    description: str | None = None
    name: str
    tags: list[str] | None = []

    local_id: str | None = None
    local_data: Any | None = None

class ChoiceDeviceType(InfrahubModel):
    _modelname = "ChoiceDeviceType"
    _identifiers = ("name", "manufacturer")
    _attributes = ("tags", "full_depth", "part_number", "height")
    name: str
    full_depth: bool | None = None
    part_number: str | None = None
    height: int | None = None
    tags: list[str] | None = []
    manufacturer: str

    local_id: str | None = None
    local_data: Any | None = None

class InfraCircuit(InfrahubModel):
    _modelname = "InfraCircuit"
    _identifiers = ("circuit_id",)
    _attributes = ("provider", "type", "tags", "vendor_id", "description")
    vendor_id: str | None = None
    description: str | None = None
    circuit_id: str
    provider: str
    type: str
    tags: list[str] | None = []

    local_id: str | None = None
    local_data: Any | None = None

class InfraDevice(InfrahubModel):
    _modelname = "InfraDevice"
    _identifiers = ("location", "rack", "organization", "name")
    _attributes = ("model", "tags", "role", "serial_number", "asset_tag", "description")
    serial_number: str | None = None
    asset_tag: str | None = None
    name: str | None = None
    description: str | None = None
    model: str
    tags: list[str] | None = []
    role: str | None = None
    organization: str | None = None
    location: str
    rack: str | None = None

    local_id: str | None = None
    local_data: Any | None = None

class InfraIPAddress(InfrahubModel):
    _modelname = "InfraIPAddress"
    _identifiers = ("address", "vrf")
    _attributes = ("organization", "description")
    description: str | None = None
    address: str
    vrf: str | None = None
    organization: str | None = None

    local_id: str | None = None
    local_data: Any | None = None

class InfraInterfaceL2L3(InfrahubModel):
    _modelname = "InfraInterfaceL2L3"
    _identifiers = ("device", "name")
    _attributes = ("tagged_vlan", "tags", "mgmt_only", "interface_type", "mac_address", "description", "l2_mode")
    mgmt_only: bool | None = False
    interface_type: str | None = None
    name: str
    mac_address: str | None = None
    description: str | None = None
    l2_mode: str | None = None
    device: str
    tagged_vlan: list[str] | None = []
    tags: list[str] | None = []
    untagged_vlan: str | None = None

    local_id: str | None = None
    local_data: Any | None = None

class InfraPrefix(InfrahubModel):
    _modelname = "InfraPrefix"
    _identifiers = ("prefix", "vrf")
    _attributes = ("location", "role", "organization", "description")
    description: str | None = None
    prefix: str
    location: str | None = None
    vrf: str | None = None
    role: str | None = None
    organization: str | None = None

    local_id: str | None = None
    local_data: Any | None = None

class InfraProviderNetwork(InfrahubModel):
    _modelname = "InfraProviderNetwork"
    _identifiers = ("name",)
    _attributes = ("provider", "tags", "vendor_id", "description")
    vendor_id: str | None = None
    description: str | None = None
    name: str
    provider: str
    tags: list[str] | None = []

    local_id: str | None = None
    local_data: Any | None = None

class InfraRack(InfrahubModel):
    _modelname = "InfraRack"
    _identifiers = ("name", "location")
    _attributes = ("tags", "role", "asset_tag", "facility_id", "serial_number", "height")
    asset_tag: str | None = None
    name: str
    facility_id: str | None = None
    serial_number: str | None = None
    height: int | None = None
    tags: list[str] | None = []
    role: str | None = None
    location: str

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
    name: str
    vlan_id: int
    vlan_group: str | None = None
    location: str | None = None
    organization: str | None = None

    local_id: str | None = None
    local_data: Any | None = None

class InfraVRF(InfrahubModel):
    _modelname = "InfraVRF"
    _identifiers = ("name",)
    _attributes = ("organization", "export_rt", "import_rt", "vrf_rd", "description")
    vrf_rd: str | None = None
    name: str
    description: str | None = None
    organization: str | None = None
    export_rt: list[str] | None = []
    import_rt: list[str] | None = []

    local_id: str | None = None
    local_data: Any | None = None

class LocationGeneric(InfrahubModel):
    _modelname = "LocationGeneric"
    _identifiers = ("name",)
    _attributes = ("group", "tags", "organization", "description", "type")
    description: str | None = None
    name: str
    type: str
    group: str | None = None
    tags: list[str] | None = []
    organization: str | None = None

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
    description: str | None = None
    name: str

    local_id: str | None = None
    local_data: Any | None = None
