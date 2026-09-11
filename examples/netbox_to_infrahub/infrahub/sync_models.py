from __future__ import annotations

from typing import Any, List

from infrahub_sync.plugin_loader import PluginLoader
# Load model class dynamically at runtime (honor adapters_path, safe fallback)
try:

    _loader = PluginLoader.from_env_and_args()


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
class BuiltinTag(_ModelBaseClass):
    _modelname = "BuiltinTag"
    _identifiers = ("name",)
    _attributes = ("description",)
    description: str | None = None
    name: str

    local_id: str | None = None
    local_data: Any | None = None

class DcimCircuit(_ModelBaseClass):
    _modelname = "DcimCircuit"
    _identifiers = ("circuit_id",)
    _attributes = ("provider", "description", "commit_rate", "status")
    circuit_id: str
    description: str | None = None
    commit_rate: int | None = None
    status: str | None = "active"
    provider: str

    local_id: str | None = None
    local_data: Any | None = None

class DcimDeviceType(_ModelBaseClass):
    _modelname = "DcimDeviceType"
    _identifiers = ("name", "manufacturer")
    _attributes = ("part_number", "height", "full_depth", "weight")
    name: str
    part_number: str | None = None
    height: int | None = 1
    full_depth: bool | None = True
    weight: int | None = None
    manufacturer: str

    local_id: str | None = None
    local_data: Any | None = None

class DcimPlatform(_ModelBaseClass):
    _modelname = "DcimPlatform"
    _identifiers = ("name",)
    _attributes = ("manufacturer",)
    name: str
    manufacturer: str | None = None

    local_id: str | None = None
    local_data: Any | None = None

class DcimDevice(_ModelBaseClass):
    _modelname = "DcimDevice"
    _identifiers = ("location", "name")
    _attributes = ("tags", "primary_address", "platform", "device_type", "status", "description", "position", "serial_number", "rack_face")
    status: str | None = "active"
    name: str
    description: str | None = None
    position: int | None = None
    serial_number: str | None = None
    rack_face: str | None = "front"
    tags: list[str] | None = []
    primary_address: str | None = None
    platform: str | None = None
    device_type: str | None = None
    location: str

    local_id: str | None = None
    local_data: Any | None = None

class InterfacePhysical(_ModelBaseClass):
    _modelname = "InterfacePhysical"
    _identifiers = ("device", "name")
    _attributes = ("bundle", "ip_addresses", "description", "mtu", "mac_address", "l2_mode")
    name: str
    description: str | None = None
    mtu: int | None = 1500
    mac_address: str | None = None
    l2_mode: str | None = None
    bundle: str | None = None
    device: str
    ip_addresses: list[str] | None = []

    local_id: str | None = None
    local_data: Any | None = None

class InterfaceVirtual(_ModelBaseClass):
    _modelname = "InterfaceVirtual"
    _identifiers = ("device", "name")
    _attributes = ("untagged_vlan", "tagged_vlan", "ip_addresses", "description", "mac_address", "l2_mode")
    name: str
    description: str | None = None
    mac_address: str | None = None
    l2_mode: str | None = None
    device: str
    untagged_vlan: str | None = None
    tagged_vlan: list[str] | None = []
    ip_addresses: list[str] | None = []

    local_id: str | None = None
    local_data: Any | None = None

class IpamPrefix(_ModelBaseClass):
    _modelname = "IpamPrefix"
    _identifiers = ("prefix", "vrf")
    _attributes = ("status", "description", "member_type")
    status: str | None = "active"
    description: str | None = None
    member_type: str | None = "address"
    prefix: str
    vrf: str | None = None

    local_id: str | None = None
    local_data: Any | None = None

class IpamIPAddress(_ModelBaseClass):
    _modelname = "IpamIPAddress"
    _identifiers = ("address", "vrf")
    _attributes = ("description", "status")
    description: str | None = None
    status: str | None = "active"
    address: str
    vrf: str | None = None

    local_id: str | None = None
    local_data: Any | None = None

class IpamAggregate(_ModelBaseClass):
    _modelname = "IpamAggregate"
    _identifiers = ("prefix",)
    _attributes = ("rir", "description", "date_added")
    description: str | None = None
    date_added: str | None = None
    prefix: str
    rir: str

    local_id: str | None = None
    local_data: Any | None = None

class OrganizationRIR(_ModelBaseClass):
    _modelname = "OrganizationRIR"
    _identifiers = ("name",)
    _attributes = ("tags", "private", "description")
    private: bool | None = False
    name: str
    description: str | None = None
    tags: list[str] | None = []

    local_id: str | None = None
    local_data: Any | None = None

class InterfaceLag(_ModelBaseClass):
    _modelname = "InterfaceLag"
    _identifiers = ("device", "name")
    _attributes = ("untagged_vlan", "tagged_vlan", "ip_addresses", "description", "mac_address", "l2_mode", "bundle_number")
    name: str
    description: str | None = None
    mac_address: str | None = None
    l2_mode: str | None = None
    bundle_number: int
    device: str
    untagged_vlan: str | None = None
    tagged_vlan: list[str] | None = []
    ip_addresses: list[str] | None = []

    local_id: str | None = None
    local_data: Any | None = None

class LocationSite(_ModelBaseClass):
    _modelname = "LocationSite"
    _identifiers = ("name",)
    _attributes = ("tags", "status", "facility", "physical_address", "timezone", "description")
    status: str | None = "active"
    facility: str | None = None
    physical_address: str | None = None
    timezone: str | None = None
    name: str
    description: str | None = None
    tags: list[str] | None = []

    local_id: str | None = None
    local_data: Any | None = None

class OrganizationManufacturer(_ModelBaseClass):
    _modelname = "OrganizationManufacturer"
    _identifiers = ("name",)
    _attributes = ("tags", "description")
    name: str
    description: str | None = None
    tags: list[str] | None = []

    local_id: str | None = None
    local_data: Any | None = None

class OrganizationProvider(_ModelBaseClass):
    _modelname = "OrganizationProvider"
    _identifiers = ("name",)
    _attributes = ("tags", "description")
    name: str
    description: str | None = None
    tags: list[str] | None = []

    local_id: str | None = None
    local_data: Any | None = None

class LocationRack(_ModelBaseClass):
    _modelname = "LocationRack"
    _identifiers = ("name", "site")
    _attributes = ("tags", "facility", "status", "height", "serial_number", "asset_tag")
    name: str
    facility: str | None = None
    status: str | None = "active"
    height: int | None = 42
    serial_number: str | None = None
    asset_tag: str | None = None
    site: str
    tags: list[str] | None = []

    local_id: str | None = None
    local_data: Any | None = None

class IpamVLAN(_ModelBaseClass):
    _modelname = "IpamVLAN"
    _identifiers = ("name", "vlan_id", "vlan_group")
    _attributes = ("description", "status")
    name: str
    description: str | None = None
    vlan_id: int
    status: str | None = "active"
    vlan_group: str

    local_id: str | None = None
    local_data: Any | None = None

class IpamVLANGroup(_ModelBaseClass):
    _modelname = "IpamVLANGroup"
    _identifiers = ("name",)
    _attributes = ("description",)
    name: str
    description: str | None = None

    local_id: str | None = None
    local_data: Any | None = None

class IpamVRF(_ModelBaseClass):
    _modelname = "IpamVRF"
    _identifiers = ("name",)
    _attributes = ("import_rt", "export_rt", "vrf_rd", "enforce_unique", "description")
    name: str
    vrf_rd: str | None = None
    enforce_unique: bool | None = True
    description: str | None = None
    import_rt: list[str] | None = []
    export_rt: list[str] | None = []

    local_id: str | None = None
    local_data: Any | None = None

class IpamRouteTarget(_ModelBaseClass):
    _modelname = "IpamRouteTarget"
    _identifiers = ("name",)
    _attributes = ("description",)
    name: str
    description: str | None = None

    local_id: str | None = None
    local_data: Any | None = None
