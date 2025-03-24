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
    _attributes = ("organization", "asn", "description")
    asn: int
    name: str
    description: str | None = None
    organization: str

    local_id: str | None = None
    local_data: Any | None = None


class InfraCircuit(InfrahubModel):
    _modelname = "InfraCircuit"
    _identifiers = ("circuit_id",)
    _attributes = ("status", "tags", "type", "provider", "description", "vendor_id")
    description: str | None = None
    vendor_id: str | None = None
    circuit_id: str
    status: str | None = None
    tags: list[str] | None = []
    type: str
    provider: str

    local_id: str | None = None
    local_data: Any | None = None


class InfraDevice(InfrahubModel):
    _modelname = "InfraDevice"
    _identifiers = ("location", "organization", "name")
    _attributes = (
        "model",
        "tags",
        "rack",
        "role",
        "status",
        "platform",
        "serial_number",
        "asset_tag",
    )
    serial_number: str | None = None
    asset_tag: str | None = None
    name: str | None = None
    model: str
    organization: str | None = None
    tags: list[str] | None = []
    rack: str | None = None
    location: str
    role: str | None = None
    status: str | None = None
    platform: str | None = None

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


class InfraIPAddress(InfrahubModel):
    _modelname = "InfraIPAddress"
    _identifiers = ("address", "ip_prefix")
    _attributes = ("role", "organization", "interfaces", "description")
    description: str | None = None
    address: str
    role: str | None = None
    organization: str | None = None
    interfaces: list[str] | None = []
    ip_prefix: str | None = None

    local_id: str | None = None
    local_data: Any | None = None


class InfraInterfaceL2L3(InfrahubModel):
    _modelname = "InfraInterfaceL2L3"
    _identifiers = ("name", "device")
    _attributes = (
        "status",
        "tags",
        "tagged_vlan",
        "l2_mode",
        "mac_address",
        "description",
        "mgmt_only",
        "interface_type",
    )
    l2_mode: str | None = None
    mac_address: str | None = None
    description: str | None = None
    mgmt_only: bool | None = False
    name: str
    interface_type: str | None = None
    device: str
    status: str | None = None
    tags: list[str] | None = []
    tagged_vlan: list[str] | None = []
    untagged_vlan: str | None = None

    local_id: str | None = None
    local_data: Any | None = None


class InfraPlatform(InfrahubModel):
    _modelname = "InfraPlatform"
    _identifiers = ("name", "manufacturer")
    _attributes = ("napalm_driver", "description")
    name: str
    napalm_driver: str | None = None
    description: str | None = None
    manufacturer: str

    local_id: str | None = None
    local_data: Any | None = None


class InfraPrefix(InfrahubModel):
    _modelname = "InfraPrefix"
    _identifiers = ("prefix", "ip_namespace")
    _attributes = ("status", "organization", "role", "vlan", "locations", "description")
    description: str | None = None
    prefix: str
    status: str | None = None
    ip_namespace: str | None = None
    organization: str | None = None
    role: str | None = None
    vlan: str | None = None
    locations: list[str] | None = []

    local_id: str | None = None
    local_data: Any | None = None


class InfraProviderNetwork(InfrahubModel):
    _modelname = "InfraProviderNetwork"
    _identifiers = ("name",)
    _attributes = ("tags", "provider", "status", "description", "vendor_id")
    name: str
    description: str | None = None
    vendor_id: str | None = None
    tags: list[str] | None = []
    provider: str
    status: str | None = None

    local_id: str | None = None
    local_data: Any | None = None


class InfraRack(InfrahubModel):
    _modelname = "InfraRack"
    _identifiers = ("name",)
    _attributes = (
        "role",
        "location",
        "tags",
        "facility_id",
        "asset_tag",
        "serial_number",
        "height",
    )
    facility_id: str | None = None
    asset_tag: str | None = None
    name: str
    serial_number: str | None = None
    height: int | None = None
    role: str | None = None
    location: str
    tags: list[str] | None = []

    local_id: str | None = None
    local_data: Any | None = None


class InfraRearPort(InfrahubModel):
    _modelname = "InfraRearPort"
    _identifiers = ("name", "device")
    _attributes = ("port_type", "description")
    port_type: str | None = None
    name: str
    description: str | None = None
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
    _attributes = (
        "role",
        "organization",
        "vlan_group",
        "status",
        "locations",
        "description",
        "vlan_id",
    )
    name: str
    description: str | None = None
    vlan_id: int
    role: str | None = None
    organization: str | None = None
    vlan_group: str | None = None
    status: str | None = None
    locations: list[str] | None = []

    local_id: str | None = None
    local_data: Any | None = None


class InfraVRF(InfrahubModel):
    _modelname = "InfraVRF"
    _identifiers = ("name", "ip_namespace")
    _attributes = ("organization", "export_rt", "import_rt", "vrf_rd", "description")
    vrf_rd: str | None = None
    description: str | None = None
    name: str
    organization: str | None = None
    export_rt: list[str] | None = []
    import_rt: list[str] | None = []
    ip_namespace: str

    local_id: str | None = None
    local_data: Any | None = None


class LocationGeneric(InfrahubModel):
    _modelname = "LocationGeneric"
    _identifiers = ("name",)
    _attributes = ("location_type", "tags", "status", "description")
    description: str | None = None
    name: str
    location_type: str | None = None
    tags: list[str] | None = []
    status: str | None = None

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


class OrganizationGeneric(InfrahubModel):
    _modelname = "OrganizationGeneric"
    _identifiers = ("name",)
    _attributes = ("group", "description", "type")
    description: str | None = None
    type: str | None = None
    name: str
    group: str | None = None

    local_id: str | None = None
    local_data: Any | None = None


class RoleGeneric(InfrahubModel):
    _modelname = "RoleGeneric"
    _identifiers = ("name",)
    _attributes = ("description", "label")
    description: str | None = None
    name: str
    label: str | None = None

    local_id: str | None = None
    local_data: Any | None = None


class StatusGeneric(InfrahubModel):
    _modelname = "StatusGeneric"
    _identifiers = ("name",)
    _attributes = ("label", "description")
    label: str | None = None
    description: str | None = None
    name: str

    local_id: str | None = None
    local_data: Any | None = None


class ChoiceCircuitType(InfrahubModel):
    _modelname = "ChoiceCircuitType"
    _identifiers = ("name",)
    _attributes = ("description",)
    name: str
    description: str | None = None

    local_id: str | None = None
    local_data: Any | None = None


class ChoiceDeviceType(InfrahubModel):
    _modelname = "ChoiceDeviceType"
    _identifiers = ("name", "manufacturer")
    _attributes = ("tags", "full_depth", "height", "part_number")
    full_depth: bool | None = None
    height: int | None = None
    part_number: str | None = None
    name: str
    tags: list[str] | None = []
    manufacturer: str

    local_id: str | None = None
    local_data: Any | None = None


class ChoiceLocationType(InfrahubModel):
    _modelname = "ChoiceLocationType"
    _identifiers = ("name",)
    _attributes = ("description",)
    name: str
    description: str | None = None

    local_id: str | None = None
    local_data: Any | None = None
