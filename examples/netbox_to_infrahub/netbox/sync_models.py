from __future__ import annotations

from typing import Any, List

from infrahub_sync.adapters.netbox import NetboxModel

# -------------------------------------------------------
# AUTO-GENERATED FILE, DO NOT MODIFY
#  This file has been generated with the command `infrahub-sync generate`
#  All modifications will be lost the next time you reexecute this command
# -------------------------------------------------------
class CoreStandardGroup(NetboxModel):
    _modelname = "CoreStandardGroup"
    _identifiers = ("name",)
    _attributes = ("description",)
    name: str
    description: str | None = None

    local_id: str | None = None
    local_data: Any | None = None

class BuiltinTag(NetboxModel):
    _modelname = "BuiltinTag"
    _identifiers = ("name",)
    _attributes = ("description",)
    name: str
    description: str | None = None

    local_id: str | None = None
    local_data: Any | None = None

class InfraCircuit(NetboxModel):
    _modelname = "InfraCircuit"
    _identifiers = ("circuit_id",)
    _attributes = ("provider", "type", "tags", "description", "vendor_id")
    description: str | None = None
    vendor_id: str | None = None
    circuit_id: str
    provider: str
    type: str
    tags: list[str] | None = []

    local_id: str | None = None
    local_data: Any | None = None

class ChoiceCircuitType(NetboxModel):
    _modelname = "ChoiceCircuitType"
    _identifiers = ("name",)
    _attributes = ("tags", "description")
    description: str | None = None
    name: str
    tags: list[str] | None = []

    local_id: str | None = None
    local_data: Any | None = None

class InfraDevice(NetboxModel):
    _modelname = "InfraDevice"
    _identifiers = ("location", "rack", "organization", "name")
    _attributes = ("role", "tags", "model", "asset_tag", "serial_number", "description")
    asset_tag: str | None = None
    serial_number: str | None = None
    name: str | None = None
    description: str | None = None
    role: str | None = None
    tags: list[str] | None = []
    rack: str | None = None
    organization: str | None = None
    model: str
    location: str

    local_id: str | None = None
    local_data: Any | None = None

class ChoiceDeviceType(NetboxModel):
    _modelname = "ChoiceDeviceType"
    _identifiers = ("name", "manufacturer")
    _attributes = ("tags", "full_depth", "height", "part_number")
    full_depth: bool | None = None
    name: str
    height: int | None = None
    part_number: str | None = None
    manufacturer: str
    tags: list[str] | None = []

    local_id: str | None = None
    local_data: Any | None = None

class InfraInterfaceL2L3(NetboxModel):
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

class InfraIPAddress(NetboxModel):
    _modelname = "InfraIPAddress"
    _identifiers = ("address", "vrf")
    _attributes = ("organization", "description")
    description: str | None = None
    address: str
    organization: str | None = None
    vrf: str | None = None

    local_id: str | None = None
    local_data: Any | None = None

class InfraProviderNetwork(NetboxModel):
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

class InfraPrefix(NetboxModel):
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

class InfraRack(NetboxModel):
    _modelname = "InfraRack"
    _identifiers = ("name", "location")
    _attributes = ("role", "tags", "asset_tag", "height", "serial_number", "facility_id")
    asset_tag: str | None = None
    height: int | None = None
    serial_number: str | None = None
    name: str
    facility_id: str | None = None
    role: str | None = None
    tags: list[str] | None = []
    location: str

    local_id: str | None = None
    local_data: Any | None = None

class InfraRouteTarget(NetboxModel):
    _modelname = "InfraRouteTarget"
    _identifiers = ("name", "organization")
    _attributes = ("description",)
    name: str
    description: str | None = None
    organization: str | None = None

    local_id: str | None = None
    local_data: Any | None = None

class InfraVLAN(NetboxModel):
    _modelname = "InfraVLAN"
    _identifiers = ("name", "vlan_id", "location", "vlan_group")
    _attributes = ("organization", "description")
    name: str
    vlan_id: int
    description: str | None = None
    location: str | None = None
    vlan_group: str | None = None
    organization: str | None = None

    local_id: str | None = None
    local_data: Any | None = None

class InfraVRF(NetboxModel):
    _modelname = "InfraVRF"
    _identifiers = ("name",)
    _attributes = ("organization", "export_rt", "import_rt", "description", "vrf_rd")
    description: str | None = None
    name: str
    vrf_rd: str | None = None
    organization: str | None = None
    export_rt: list[str] | None = []
    import_rt: list[str] | None = []

    local_id: str | None = None
    local_data: Any | None = None

class OrganizationGeneric(NetboxModel):
    _modelname = "OrganizationGeneric"
    _identifiers = ("name",)
    _attributes = ("group", "description")
    name: str
    description: str | None = None
    group: str | None = None

    local_id: str | None = None
    local_data: Any | None = None

class RoleGeneric(NetboxModel):
    _modelname = "RoleGeneric"
    _identifiers = ("name",)
    _attributes = ("description",)
    name: str
    description: str | None = None

    local_id: str | None = None
    local_data: Any | None = None

class LocationGeneric(NetboxModel):
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
