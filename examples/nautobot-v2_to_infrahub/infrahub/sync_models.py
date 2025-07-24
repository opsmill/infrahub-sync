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


class InfraAutonomousSystem(InfrahubModel):
    _modelname = "InfraAutonomousSystem"
    _identifiers = ("name",)
    _attributes = ("organization", "description", "asn")
    description: str | None = None
    asn: int
    name: str
    organization: str

    local_id: str | None = None
    local_data: Any | None = None


class InfraCircuit(InfrahubModel):
    _modelname = "InfraCircuit"
    _identifiers = ("circuit_id",)
    _attributes = ("provider", "tags", "type", "status", "vendor_id", "description")
    vendor_id: str | None = None
    circuit_id: str
    description: str | None = None
    provider: str
    tags: list[str] | None = []
    type: str
    status: str | None = None

    local_id: str | None = None
    local_data: Any | None = None


class ChoiceCircuitType(InfrahubModel):
    _modelname = "ChoiceCircuitType"
    _identifiers = ("name",)
    _attributes = ("description",)
    description: str | None = None
    name: str

    local_id: str | None = None
    local_data: Any | None = None


class InfraDevice(InfrahubModel):
    _modelname = "InfraDevice"
    _identifiers = ("location", "organization", "name")
    _attributes = ("platform", "tags", "role", "rack", "status", "model", "asset_tag", "serial_number")
    name: str | None = None
    asset_tag: str | None = None
    serial_number: str | None = None
    platform: str | None = None
    organization: str | None = None
    tags: list[str] | None = []
    role: str | None = None
    rack: str | None = None
    status: str | None = None
    location: str
    model: str

    local_id: str | None = None
    local_data: Any | None = None


class ChoiceDeviceType(InfrahubModel):
    _modelname = "ChoiceDeviceType"
    _identifiers = ("name", "manufacturer")
    _attributes = ("tags", "full_depth", "height", "part_number")
    name: str
    full_depth: bool | None = None
    height: int | None = None
    part_number: str | None = None
    tags: list[str] | None = []
    manufacturer: str

    local_id: str | None = None
    local_data: Any | None = None


class InfraFrontPort(InfrahubModel):
    _modelname = "InfraFrontPort"
    _identifiers = ("name", "device")
    _attributes = ("rear_port", "description", "port_type")
    name: str
    description: str | None = None
    port_type: str | None = None
    rear_port: list[str] | None = []
    device: str

    local_id: str | None = None
    local_data: Any | None = None


class InfraInterfaceL2L3(InfrahubModel):
    _modelname = "InfraInterfaceL2L3"
    _identifiers = ("name", "device")
    _attributes = (
        "tagged_vlan",
        "status",
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
    tagged_vlan: list[str] | None = []
    status: str | None = None
    device: str
    tags: list[str] | None = []

    local_id: str | None = None
    local_data: Any | None = None


class InfraIPAddress(InfrahubModel):
    _modelname = "InfraIPAddress"
    _identifiers = ("address", "ip_prefix")
    _attributes = ("organization", "interfaces", "role", "description")
    description: str | None = None
    address: str
    organization: str | None = None
    interfaces: list[str] | None = []
    role: str | None = None
    ip_prefix: str | None = None

    local_id: str | None = None
    local_data: Any | None = None


class ChoiceLocationType(InfrahubModel):
    _modelname = "ChoiceLocationType"
    _identifiers = ("name",)
    _attributes = ("description",)
    description: str | None = None
    name: str

    local_id: str | None = None
    local_data: Any | None = None


class NautobotNamespace(InfrahubModel):
    _modelname = "NautobotNamespace"
    _identifiers = ("name",)
    _attributes = ("description",)
    description: str | None = None
    name: str

    local_id: str | None = None
    local_data: Any | None = None


class InfraPlatform(InfrahubModel):
    _modelname = "InfraPlatform"
    _identifiers = ("name", "manufacturer")
    _attributes = ("napalm_driver", "description")
    name: str
    napalm_driver: str | None = None
    description: str | None = None
    manufacturer: str | None = None

    local_id: str | None = None
    local_data: Any | None = None


class InfraProviderNetwork(InfrahubModel):
    _modelname = "InfraProviderNetwork"
    _identifiers = ("name",)
    _attributes = ("provider", "status", "tags", "vendor_id", "description")
    name: str
    vendor_id: str | None = None
    description: str | None = None
    provider: str
    status: str | None = None
    tags: list[str] | None = []

    local_id: str | None = None
    local_data: Any | None = None


class InfraPrefix(InfrahubModel):
    _modelname = "InfraPrefix"
    _identifiers = ("prefix", "ip_namespace")
    _attributes = ("organization", "locations", "status", "role", "vlan", "description")
    description: str | None = None
    prefix: str
    organization: str | None = None
    locations: list[str] | None = []
    status: str | None = None
    role: str | None = None
    vlan: str | None = None
    ip_namespace: str | None = None

    local_id: str | None = None
    local_data: Any | None = None


class InfraRack(InfrahubModel):
    _modelname = "InfraRack"
    _identifiers = ("name",)
    _attributes = ("location", "role", "tags", "serial_number", "asset_tag", "height", "facility_id")
    serial_number: str | None = None
    name: str
    asset_tag: str | None = None
    height: int | None = None
    facility_id: str | None = None
    location: str
    role: str | None = None
    tags: list[str] | None = []

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
    description: str | None = None
    name: str
    organization: str | None = None

    local_id: str | None = None
    local_data: Any | None = None


class InfraVLAN(InfrahubModel):
    _modelname = "InfraVLAN"
    _identifiers = ("name",)
    _attributes = ("locations", "vlan_group", "organization", "role", "status", "vlan_id", "description")
    vlan_id: int
    description: str | None = None
    name: str
    locations: list[str] | None = []
    vlan_group: str | None = None
    organization: str | None = None
    role: str | None = None
    status: str | None = None

    local_id: str | None = None
    local_data: Any | None = None


class InfraVRF(InfrahubModel):
    _modelname = "InfraVRF"
    _identifiers = ("name", "ip_namespace")
    _attributes = ("import_rt", "export_rt", "organization", "vrf_rd", "description")
    vrf_rd: str | None = None
    name: str
    description: str | None = None
    import_rt: list[str] | None = []
    export_rt: list[str] | None = []
    organization: str | None = None
    ip_namespace: str

    local_id: str | None = None
    local_data: Any | None = None


class OrganizationGeneric(InfrahubModel):
    _modelname = "OrganizationGeneric"
    _identifiers = ("name",)
    _attributes = ("group", "description", "type")
    name: str
    description: str | None = None
    type: str | None = None
    group: str | None = None

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


class RoleGeneric(InfrahubModel):
    _modelname = "RoleGeneric"
    _identifiers = ("name",)
    _attributes = ("label", "description")
    name: str
    label: str | None = None
    description: str | None = None

    local_id: str | None = None
    local_data: Any | None = None


class LocationGeneric(InfrahubModel):
    _modelname = "LocationGeneric"
    _identifiers = ("name",)
    _attributes = ("tags", "location_type", "status", "description")
    name: str
    description: str | None = None
    tags: list[str] | None = []
    location_type: str | None = None
    status: str | None = None

    local_id: str | None = None
    local_data: Any | None = None
