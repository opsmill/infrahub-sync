from __future__ import annotations

from typing import Any

from infrahub_sync.plugin_loader import PluginLoader

# Load model class dynamically at runtime (honor adapters_path, safe fallback)
try:
    _loader = PluginLoader.from_env_and_args(adapter_paths=[])

    _spec = "netbox"

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
    description: str | None = None
    name: str

    local_id: str | None = None
    local_data: Any | None = None


class BuiltinTag(_ModelBaseClass):
    _modelname = "BuiltinTag"
    _identifiers = ("name",)
    _attributes = ("description",)
    name: str
    description: str | None = None

    local_id: str | None = None
    local_data: Any | None = None


class ChoiceCircuitType(_ModelBaseClass):
    _modelname = "ChoiceCircuitType"
    _identifiers = ("name",)
    _attributes = ("tags", "description")
    description: str | None = None
    name: str
    tags: list[str] | None = []

    local_id: str | None = None
    local_data: Any | None = None


class ChoiceDeviceType(_ModelBaseClass):
    _modelname = "ChoiceDeviceType"
    _identifiers = ("name", "manufacturer")
    _attributes = ("tags", "height", "full_depth", "part_number")
    height: int | None = None
    full_depth: bool | None = None
    part_number: str | None = None
    name: str
    manufacturer: str
    tags: list[str] | None = []

    local_id: str | None = None
    local_data: Any | None = None


class InfraCircuit(_ModelBaseClass):
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


class InfraDevice(_ModelBaseClass):
    _modelname = "InfraDevice"
    _identifiers = ("location", "rack", "organization", "name")
    _attributes = ("model", "role", "tags", "serial_number", "description", "asset_tag")
    name: str | None = None
    serial_number: str | None = None
    description: str | None = None
    asset_tag: str | None = None
    model: str
    rack: str | None = None
    role: str | None = None
    tags: list[str] | None = []
    location: str
    organization: str | None = None

    local_id: str | None = None
    local_data: Any | None = None


class InfraIPAddress(_ModelBaseClass):
    _modelname = "InfraIPAddress"
    _identifiers = ("address", "vrf")
    _attributes = ("organization", "description")
    address: str
    description: str | None = None
    organization: str | None = None
    vrf: str | None = None

    local_id: str | None = None
    local_data: Any | None = None


class InfraInterfaceL2L3(_ModelBaseClass):
    _modelname = "InfraInterfaceL2L3"
    _identifiers = ("device", "name")
    _attributes = ("tagged_vlan", "tags", "l2_mode", "mac_address", "description", "interface_type", "mgmt_only")
    l2_mode: str | None = None
    mac_address: str | None = None
    description: str | None = None
    interface_type: str | None = None
    mgmt_only: bool | None = False
    name: str
    tagged_vlan: list[str] | None = []
    untagged_vlan: str | None = None
    tags: list[str] | None = []
    device: str

    local_id: str | None = None
    local_data: Any | None = None


class InfraPrefix(_ModelBaseClass):
    _modelname = "InfraPrefix"
    _identifiers = ("prefix", "vrf")
    _attributes = ("role", "organization", "location", "description")
    prefix: str
    description: str | None = None
    role: str | None = None
    organization: str | None = None
    location: str | None = None
    vrf: str | None = None

    local_id: str | None = None
    local_data: Any | None = None


class InfraProviderNetwork(_ModelBaseClass):
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


class InfraRack(_ModelBaseClass):
    _modelname = "InfraRack"
    _identifiers = ("name", "location")
    _attributes = ("tags", "role", "asset_tag", "facility_id", "height", "serial_number")
    asset_tag: str | None = None
    facility_id: str | None = None
    height: int | None = None
    name: str
    serial_number: str | None = None
    tags: list[str] | None = []
    role: str | None = None
    location: str

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
    _identifiers = ("name", "vlan_id", "location", "vlan_group")
    _attributes = ("organization", "description")
    description: str | None = None
    name: str
    vlan_id: int
    vlan_group: str | None = None
    organization: str | None = None
    location: str | None = None

    local_id: str | None = None
    local_data: Any | None = None


class InfraVRF(_ModelBaseClass):
    _modelname = "InfraVRF"
    _identifiers = ("name",)
    _attributes = ("export_rt", "organization", "import_rt", "description", "vrf_rd")
    description: str | None = None
    vrf_rd: str | None = None
    name: str
    export_rt: list[str] | None = []
    organization: str | None = None
    import_rt: list[str] | None = []

    local_id: str | None = None
    local_data: Any | None = None


class LocationGeneric(_ModelBaseClass):
    _modelname = "LocationGeneric"
    _identifiers = ("name",)
    _attributes = ("organization", "tags", "group", "description", "type")
    description: str | None = None
    type: str
    name: str
    organization: str | None = None
    tags: list[str] | None = []
    group: str | None = None

    local_id: str | None = None
    local_data: Any | None = None


class OrganizationGeneric(_ModelBaseClass):
    _modelname = "OrganizationGeneric"
    _identifiers = ("name",)
    _attributes = ("group", "description")
    description: str | None = None
    name: str
    group: str | None = None

    local_id: str | None = None
    local_data: Any | None = None


class RoleGeneric(_ModelBaseClass):
    _modelname = "RoleGeneric"
    _identifiers = ("name",)
    _attributes = ("description",)
    name: str
    description: str | None = None

    local_id: str | None = None
    local_data: Any | None = None
