from typing import Any, List, Optional

from infrahub_sync.adapters.nautobot import NautobotModel


# -------------------------------------------------------
# AUTO-GENERATED FILE, DO NOT MODIFY
#  This file has been generated with the command `infrahub-sync generate`
#  All modifications will be lost the next time you reexecute this command
# -------------------------------------------------------
class CoreStandardGroup(NautobotModel):
    _modelname = "CoreStandardGroup"
    _identifiers = ("name",)
    _attributes = ("description",)
    name: str
    description: Optional[str] = None

    local_id: Optional[str] = None
    local_data: Optional[Any] = None


class BuiltinTag(NautobotModel):
    _modelname = "BuiltinTag"
    _identifiers = ("name",)
    _attributes = ("description",)
    name: str
    description: Optional[str] = None

    local_id: Optional[str] = None
    local_data: Optional[Any] = None


class InfraAutonomousSystem(NautobotModel):
    _modelname = "InfraAutonomousSystem"
    _identifiers = ("name",)
    _attributes = ("organization", "description", "asn")
    name: str
    description: Optional[str] = None
    asn: int
    organization: str

    local_id: Optional[str] = None
    local_data: Optional[Any] = None


class InfraCircuit(NautobotModel):
    _modelname = "InfraCircuit"
    _identifiers = ("circuit_id",)
    _attributes = ("status", "tags", "provider", "type", "description", "vendor_id")
    circuit_id: str
    description: Optional[str] = None
    vendor_id: Optional[str] = None
    status: Optional[str] = None
    tags: Optional[List[str]] = []
    provider: str
    type: str

    local_id: Optional[str] = None
    local_data: Optional[Any] = None


class TemplateCircuitType(NautobotModel):
    _modelname = "TemplateCircuitType"
    _identifiers = ("name",)
    _attributes = ("description",)
    description: Optional[str] = None
    name: str

    local_id: Optional[str] = None
    local_data: Optional[Any] = None


class InfraDevice(NautobotModel):
    _modelname = "InfraDevice"
    _identifiers = ("location", "organization", "name")
    _attributes = ("status", "rack", "tags", "platform", "role", "model", "serial_number", "asset_tag")
    name: Optional[str] = None
    serial_number: Optional[str] = None
    asset_tag: Optional[str] = None
    status: Optional[str] = None
    location: str
    rack: Optional[str] = None
    tags: Optional[List[str]] = []
    platform: Optional[str] = None
    role: Optional[str] = None
    organization: Optional[str] = None
    model: str

    local_id: Optional[str] = None
    local_data: Optional[Any] = None


class TemplateDeviceType(NautobotModel):
    _modelname = "TemplateDeviceType"
    _identifiers = ("name", "manufacturer")
    _attributes = ("tags", "part_number", "height", "full_depth")
    part_number: Optional[str] = None
    height: Optional[int] = None
    name: str
    full_depth: Optional[bool] = None
    manufacturer: str
    tags: Optional[List[str]] = []

    local_id: Optional[str] = None
    local_data: Optional[Any] = None


class InfraFrontPort(NautobotModel):
    _modelname = "InfraFrontPort"
    _identifiers = ("name", "device")
    _attributes = ("rear_port", "description", "port_type")
    name: str
    description: Optional[str] = None
    port_type: Optional[str] = None
    rear_port: Optional[List[str]] = []
    device: str

    local_id: Optional[str] = None
    local_data: Optional[Any] = None


class InfraInterfaceL2L3(NautobotModel):
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
    l2_mode: Optional[str] = None
    name: str
    description: Optional[str] = None
    mgmt_only: Optional[bool] = False
    mac_address: Optional[str] = None
    interface_type: Optional[str] = None
    untagged_vlan: Optional[str] = None
    tagged_vlan: Optional[List[str]] = []
    status: Optional[str] = None
    device: str
    tags: Optional[List[str]] = []

    local_id: Optional[str] = None
    local_data: Optional[Any] = None


class InfraIPAddress(NautobotModel):
    _modelname = "InfraIPAddress"
    _identifiers = ("address", "ip_prefix")
    _attributes = ("organization", "interfaces", "role", "description")
    address: str
    description: Optional[str] = None
    organization: Optional[str] = None
    interfaces: Optional[List[str]] = []
    role: Optional[str] = None
    ip_prefix: Optional[str] = None

    local_id: Optional[str] = None
    local_data: Optional[Any] = None


class TemplateLocationType(NautobotModel):
    _modelname = "TemplateLocationType"
    _identifiers = ("name",)
    _attributes = ("description",)
    description: Optional[str] = None
    name: str

    local_id: Optional[str] = None
    local_data: Optional[Any] = None


class NautobotNamespace(NautobotModel):
    _modelname = "NautobotNamespace"
    _identifiers = ("name",)
    _attributes = ("description",)
    name: str
    description: Optional[str] = None

    local_id: Optional[str] = None
    local_data: Optional[Any] = None


class InfraPlatform(NautobotModel):
    _modelname = "InfraPlatform"
    _identifiers = ("name", "manufacturer")
    _attributes = ("description", "napalm_driver")
    description: Optional[str] = None
    name: str
    napalm_driver: Optional[str] = None
    manufacturer: str

    local_id: Optional[str] = None
    local_data: Optional[Any] = None


class InfraProviderNetwork(NautobotModel):
    _modelname = "InfraProviderNetwork"
    _identifiers = ("name",)
    _attributes = ("provider", "tags", "status", "description", "vendor_id")
    description: Optional[str] = None
    vendor_id: Optional[str] = None
    name: str
    provider: str
    tags: Optional[List[str]] = []
    status: Optional[str] = None

    local_id: Optional[str] = None
    local_data: Optional[Any] = None


class InfraPrefix(NautobotModel):
    _modelname = "InfraPrefix"
    _identifiers = ("prefix", "ip_namespace")
    _attributes = ("organization", "locations", "status", "role", "vlan", "description")
    prefix: str
    description: Optional[str] = None
    organization: Optional[str] = None
    locations: Optional[List[str]] = []
    status: Optional[str] = None
    role: Optional[str] = None
    vlan: Optional[str] = None
    ip_namespace: Optional[str] = None

    local_id: Optional[str] = None
    local_data: Optional[Any] = None


class InfraRack(NautobotModel):
    _modelname = "InfraRack"
    _identifiers = ("name",)
    _attributes = ("tags", "role", "location", "asset_tag", "height", "serial_number", "facility_id")
    name: str
    asset_tag: Optional[str] = None
    height: Optional[int] = None
    serial_number: Optional[str] = None
    facility_id: Optional[str] = None
    tags: Optional[List[str]] = []
    role: Optional[str] = None
    location: str

    local_id: Optional[str] = None
    local_data: Optional[Any] = None


class InfraRearPort(NautobotModel):
    _modelname = "InfraRearPort"
    _identifiers = ("name", "device")
    _attributes = ("description", "port_type")
    name: str
    description: Optional[str] = None
    port_type: Optional[str] = None
    device: str

    local_id: Optional[str] = None
    local_data: Optional[Any] = None


class InfraRouteTarget(NautobotModel):
    _modelname = "InfraRouteTarget"
    _identifiers = ("name", "organization")
    _attributes = ("description",)
    name: str
    description: Optional[str] = None
    organization: Optional[str] = None

    local_id: Optional[str] = None
    local_data: Optional[Any] = None


class InfraVLAN(NautobotModel):
    _modelname = "InfraVLAN"
    _identifiers = ("name",)
    _attributes = ("role", "status", "organization", "locations", "vlan_group", "vlan_id", "description")
    name: str
    vlan_id: int
    description: Optional[str] = None
    role: Optional[str] = None
    status: Optional[str] = None
    organization: Optional[str] = None
    locations: Optional[List[str]] = []
    vlan_group: Optional[str] = None

    local_id: Optional[str] = None
    local_data: Optional[Any] = None


class InfraVRF(NautobotModel):
    _modelname = "InfraVRF"
    _identifiers = ("name", "ip_namespace")
    _attributes = ("organization", "export_rt", "import_rt", "description", "vrf_rd")
    description: Optional[str] = None
    name: str
    vrf_rd: Optional[str] = None
    organization: Optional[str] = None
    export_rt: Optional[List[str]] = []
    import_rt: Optional[List[str]] = []
    ip_namespace: str

    local_id: Optional[str] = None
    local_data: Optional[Any] = None


class OrganizationGeneric(NautobotModel):
    _modelname = "OrganizationGeneric"
    _identifiers = ("name",)
    _attributes = ("group", "description", "type")
    name: str
    description: Optional[str] = None
    type: Optional[str] = None
    group: Optional[str] = None

    local_id: Optional[str] = None
    local_data: Optional[Any] = None


class StatusGeneric(NautobotModel):
    _modelname = "StatusGeneric"
    _identifiers = ("name",)
    _attributes = ("label", "description")
    name: str
    label: Optional[str] = None
    description: Optional[str] = None

    local_id: Optional[str] = None
    local_data: Optional[Any] = None


class RoleGeneric(NautobotModel):
    _modelname = "RoleGeneric"
    _identifiers = ("name",)
    _attributes = ("label", "description")
    name: str
    label: Optional[str] = None
    description: Optional[str] = None

    local_id: Optional[str] = None
    local_data: Optional[Any] = None


class LocationGeneric(NautobotModel):
    _modelname = "LocationGeneric"
    _identifiers = ("name",)
    _attributes = ("tags", "location_type", "status", "description")
    name: str
    description: Optional[str] = None
    tags: Optional[List[str]] = []
    location_type: Optional[str] = None
    status: Optional[str] = None

    local_id: Optional[str] = None
    local_data: Optional[Any] = None
