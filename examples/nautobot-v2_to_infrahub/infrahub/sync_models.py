from __future__ import annotations

from typing import Any

from infrahub_sync.plugin_loader import PluginLoader

# Load model class dynamically at runtime (honor adapters_path, safe fallback)
try:
    _loader = PluginLoader.from_env_and_args(adapter_paths=[])

    _spec = "infrahub"

    _ModelBaseClass = _loader.resolve(_spec, default_class_candidates=("Model",))
except Exception:
    # Fallback: use DiffSyncModel to avoid import-time failure
    from diffsync import DiffSyncModel as _FallbackModel

    _ModelBaseClass = _FallbackModel


# -------------------------------------------------------
# AUTO-GENERATED FILE, DO NOT MODIFY
#  This file has been generated with the command `infrahub-sync generate`
#  All modifications will be lost the next time you reexecute this command
# -------------------------------------------------------
class CoreStandardGroup(_ModelBaseClass):
    _modelname = "CoreStandardGroup"
    _identifiers = ("name",)
    _attributes = ("description",)
    name: str
    description: str | None = None

    local_id: str | None = None
    local_data: Any | None = None


class BuiltinTag(_ModelBaseClass):
    _modelname = "BuiltinTag"
    _identifiers = ("name",)
    _attributes = ("description",)
    description: str | None = None
    name: str

    local_id: str | None = None
    local_data: Any | None = None


class InfraAutonomousSystem(_ModelBaseClass):
    _modelname = "InfraAutonomousSystem"
    _identifiers = ("name",)
    _attributes = ("organization", "description", "asn")
    name: str
    description: str | None = None
    asn: int
    organization: str

    local_id: str | None = None
    local_data: Any | None = None


class InfraCircuit(_ModelBaseClass):
    _modelname = "InfraCircuit"
    _identifiers = ("circuit_id",)
    _attributes = ("status", "type", "tags", "provider", "description", "vendor_id")
    circuit_id: str
    description: str | None = None
    vendor_id: str | None = None
    status: str | None = None
    type: str
    tags: list[str] | None = []
    provider: str

    local_id: str | None = None
    local_data: Any | None = None


class ChoiceCircuitType(_ModelBaseClass):
    _modelname = "ChoiceCircuitType"
    _identifiers = ("name",)
    _attributes = ("description",)
    name: str
    description: str | None = None

    local_id: str | None = None
    local_data: Any | None = None


class InfraDevice(_ModelBaseClass):
    _modelname = "InfraDevice"
    _identifiers = ("location", "organization", "name")
    _attributes = ("role", "model", "status", "platform", "rack", "tags", "asset_tag", "serial_number")
    name: str | None = None
    asset_tag: str | None = None
    serial_number: str | None = None
    role: str | None = None
    model: str
    status: str | None = None
    location: str
    platform: str | None = None
    organization: str | None = None
    rack: str | None = None
    tags: list[str] | None = []

    local_id: str | None = None
    local_data: Any | None = None


class ChoiceDeviceType(_ModelBaseClass):
    _modelname = "ChoiceDeviceType"
    _identifiers = ("name", "manufacturer")
    _attributes = ("tags", "part_number", "full_depth", "height")
    part_number: str | None = None
    full_depth: bool | None = None
    height: int | None = None
    name: str
    manufacturer: str
    tags: list[str] | None = []

    local_id: str | None = None
    local_data: Any | None = None


class InfraFrontPort(_ModelBaseClass):
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


class InfraInterfaceL2L3(_ModelBaseClass):
    _modelname = "InfraInterfaceL2L3"
    _identifiers = ("name", "device")
    _attributes = (
        "tagged_vlan",
        "tags",
        "status",
        "l2_mode",
        "description",
        "mac_address",
        "mgmt_only",
        "interface_type",
    )
    l2_mode: str | None = None
    description: str | None = None
    name: str
    mac_address: str | None = None
    mgmt_only: bool | None = False
    interface_type: str | None = None
    tagged_vlan: list[str] | None = []
    untagged_vlan: str | None = None
    device: str
    tags: list[str] | None = []
    status: str | None = None

    local_id: str | None = None
    local_data: Any | None = None


class InfraIPAddress(_ModelBaseClass):
    _modelname = "InfraIPAddress"
    _identifiers = ("address", "ip_prefix")
    _attributes = ("organization", "interfaces", "role", "status", "description")
    description: str | None = None
    address: str
    organization: str | None = None
    interfaces: list[str] | None = []
    role: str | None = None
    status: str | None = None
    ip_prefix: str | None = None

    local_id: str | None = None
    local_data: Any | None = None


class ChoiceLocationType(_ModelBaseClass):
    _modelname = "ChoiceLocationType"
    _identifiers = ("name",)
    _attributes = ("description",)
    name: str
    description: str | None = None

    local_id: str | None = None
    local_data: Any | None = None


class NautobotNamespace(_ModelBaseClass):
    _modelname = "NautobotNamespace"
    _identifiers = ("name",)
    _attributes = ("description",)
    description: str | None = None
    name: str

    local_id: str | None = None
    local_data: Any | None = None


class InfraPlatform(_ModelBaseClass):
    _modelname = "InfraPlatform"
    _identifiers = ("name", "manufacturer")
    _attributes = ("napalm_driver", "description")
    napalm_driver: str | None = None
    name: str
    description: str | None = None
    manufacturer: str | None = None

    local_id: str | None = None
    local_data: Any | None = None


class InfraProviderNetwork(_ModelBaseClass):
    _modelname = "InfraProviderNetwork"
    _identifiers = ("name",)
    _attributes = ("provider", "tags", "status", "description", "vendor_id")
    description: str | None = None
    name: str
    vendor_id: str | None = None
    provider: str
    tags: list[str] | None = []
    status: str | None = None

    local_id: str | None = None
    local_data: Any | None = None


class InfraPrefix(_ModelBaseClass):
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


class InfraRack(_ModelBaseClass):
    _modelname = "InfraRack"
    _identifiers = ("name",)
    _attributes = ("role", "tags", "location", "serial_number", "asset_tag", "height", "facility_id")
    name: str
    serial_number: str | None = None
    asset_tag: str | None = None
    height: int | None = None
    facility_id: str | None = None
    role: str | None = None
    tags: list[str] | None = []
    location: str

    local_id: str | None = None
    local_data: Any | None = None


class InfraRearPort(_ModelBaseClass):
    _modelname = "InfraRearPort"
    _identifiers = ("name", "device")
    _attributes = ("description", "port_type")
    name: str
    description: str | None = None
    port_type: str | None = None
    device: str

    local_id: str | None = None
    local_data: Any | None = None


class InfraRouteTarget(_ModelBaseClass):
    _modelname = "InfraRouteTarget"
    _identifiers = ("name", "organization")
    _attributes = ("description",)
    name: str
    description: str | None = None
    organization: str | None = None

    local_id: str | None = None
    local_data: Any | None = None


class InfraVLAN(_ModelBaseClass):
    _modelname = "InfraVLAN"
    _identifiers = ("name",)
    _attributes = ("role", "vlan_group", "locations", "organization", "status", "description", "vlan_id")
    name: str
    description: str | None = None
    vlan_id: int
    role: str | None = None
    vlan_group: str | None = None
    locations: list[str] | None = []
    organization: str | None = None
    status: str | None = None

    local_id: str | None = None
    local_data: Any | None = None


class InfraVRF(_ModelBaseClass):
    _modelname = "InfraVRF"
    _identifiers = ("name", "ip_namespace")
    _attributes = ("import_rt", "export_rt", "organization", "vrf_rd", "description")
    name: str
    vrf_rd: str | None = None
    description: str | None = None
    ip_namespace: str
    import_rt: list[str] | None = []
    export_rt: list[str] | None = []
    organization: str | None = None

    local_id: str | None = None
    local_data: Any | None = None


class OrganizationGeneric(_ModelBaseClass):
    _modelname = "OrganizationGeneric"
    _identifiers = ("name",)
    _attributes = ("group", "description", "type")
    name: str
    description: str | None = None
    type: str | None = None
    group: str | None = None

    local_id: str | None = None
    local_data: Any | None = None


class StatusGeneric(_ModelBaseClass):
    _modelname = "StatusGeneric"
    _identifiers = ("name",)
    _attributes = ("label", "description")
    name: str
    label: str | None = None
    description: str | None = None

    local_id: str | None = None
    local_data: Any | None = None


class RoleGeneric(_ModelBaseClass):
    _modelname = "RoleGeneric"
    _identifiers = ("name",)
    _attributes = ("label", "description")
    name: str
    label: str | None = None
    description: str | None = None

    local_id: str | None = None
    local_data: Any | None = None


class LocationGeneric(_ModelBaseClass):
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
