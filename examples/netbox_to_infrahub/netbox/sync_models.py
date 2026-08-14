from __future__ import annotations

from typing import Any

from infrahub_sync.plugin_loader import PluginLoader

# Load model class dynamically at runtime (honor adapters_path, safe fallback)
try:
    _loader = PluginLoader.from_env_and_args()

    _spec = "netbox"

    _ModelBaseClass = _loader.resolve(_spec, default_class_candidates=("Model",))
except Exception:  # noqa: BLE001 -- generated adapters need a safe import fallback
    # Fallback: use DiffSyncModel to avoid import-time failure
    from diffsync import DiffSyncModel as _FallbackModel

    _ModelBaseClass = _FallbackModel


# -------------------------------------------------------
# AUTO-GENERATED FILE, DO NOT MODIFY
#  This file has been generated with the command `infrahub-sync generate`
#  All modifications will be lost the next time you reexecute this command
# -------------------------------------------------------
class _GeneratedModelBase(_ModelBaseClass):
    if "local_id" not in getattr(_ModelBaseClass, "model_fields", {}):
        local_id: str | None = None
    if "local_data" not in getattr(_ModelBaseClass, "model_fields", {}):
        local_data: Any | None = None


class BuiltinTag(_GeneratedModelBase):
    _modelname = "BuiltinTag"
    _identifiers = ("name",)
    _attributes = ("description",)
    name: str
    description: str | None = None


class DcimDeviceType(_GeneratedModelBase):
    _modelname = "DcimDeviceType"
    _identifiers = ("name", "manufacturer")
    _attributes = ("height", "full_depth", "part_number", "weight")
    height: int | None = 1
    full_depth: bool | None = True
    part_number: str | None = None
    name: str
    weight: int | None = None
    manufacturer: str


class DcimPlatform(_GeneratedModelBase):
    _modelname = "DcimPlatform"
    _identifiers = ("name",)
    _attributes = ("manufacturer",)
    name: str
    manufacturer: str | None = None


class DcimDevice(_GeneratedModelBase):
    _modelname = "DcimDevice"
    _identifiers = ("location", "name")
    _attributes = (
        "tags",
        "platform",
        "primary_address",
        "device_type",
        "status",
        "description",
        "position",
        "rack_face",
        "serial",
    )
    status: str | None = "active"
    description: str | None = None
    name: str
    position: int | None = None
    rack_face: str | None = "front"
    serial: str | None = None
    tags: list[str] | None = []
    platform: str | None = None
    primary_address: str | None = None
    device_type: str | None = None
    location: str


class InterfacePhysical(_GeneratedModelBase):
    _modelname = "InterfacePhysical"
    _identifiers = ("device", "name")
    _attributes = ("bundle", "ip_addresses", "mtu", "description", "mac_address", "l2_mode")
    mtu: int | None = 1500
    name: str
    description: str | None = None
    mac_address: str | None = None
    l2_mode: str | None = None
    bundle: str | None = None
    device: str
    ip_addresses: list[str] | None = []


class InterfaceVirtual(_GeneratedModelBase):
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


class IpamPrefix(_GeneratedModelBase):
    _modelname = "IpamPrefix"
    _identifiers = ("prefix", "vrf")
    _attributes = ("status", "description", "member_type")
    status: str | None = "active"
    description: str | None = None
    prefix: str
    member_type: str | None = "address"
    vrf: str | None = None


class IpamIPAddress(_GeneratedModelBase):
    _modelname = "IpamIPAddress"
    _identifiers = ("address", "vrf")
    _attributes = ("description", "status")
    description: str | None = None
    status: str | None = "active"
    address: str
    vrf: str | None = None


class OrganizationManufacturer(_GeneratedModelBase):
    _modelname = "OrganizationManufacturer"
    _identifiers = ("name",)
    _attributes = ("tags", "description")
    name: str
    description: str | None = None
    tags: list[str] | None = []


class OrganizationProvider(_GeneratedModelBase):
    _modelname = "OrganizationProvider"
    _identifiers = ("name",)
    _attributes = ("tags", "description")
    name: str
    description: str | None = None
    tags: list[str] | None = []


class IpamAggregate(_GeneratedModelBase):
    _modelname = "IpamAggregate"
    _identifiers = ("prefix",)
    _attributes = ("rir", "description", "date_added")
    description: str | None = None
    date_added: str | None = None
    prefix: str
    rir: str


class OrganizationRIR(_GeneratedModelBase):
    _modelname = "OrganizationRIR"
    _identifiers = ("name",)
    _attributes = ("tags", "is_private", "description")
    is_private: bool | None = False
    name: str
    description: str | None = None
    tags: list[str] | None = []


class DcimCircuit(_GeneratedModelBase):
    _modelname = "DcimCircuit"
    _identifiers = ("circuit_id",)
    _attributes = ("provider", "status", "commit_rate", "description")
    status: str | None = "active"
    commit_rate: int | None = None
    circuit_id: str
    description: str | None = None
    provider: str


class InterfaceLag(_GeneratedModelBase):
    _modelname = "InterfaceLag"
    _identifiers = ("device", "name")
    _attributes = (
        "untagged_vlan",
        "tagged_vlan",
        "ip_addresses",
        "description",
        "mac_address",
        "l2_mode",
        "bundle_number",
    )
    name: str
    description: str | None = None
    mac_address: str | None = None
    l2_mode: str | None = None
    bundle_number: int
    device: str
    untagged_vlan: str | None = None
    tagged_vlan: list[str] | None = []
    ip_addresses: list[str] | None = []


class LocationSite(_GeneratedModelBase):
    _modelname = "LocationSite"
    _identifiers = ("name",)
    _attributes = ("tags", "facility", "timezone", "physical_address", "status", "description")
    facility: str | None = None
    timezone: str | None = None
    physical_address: str | None = None
    status: str | None = "active"
    name: str
    description: str | None = None
    tags: list[str] | None = []


class LocationRack(_GeneratedModelBase):
    _modelname = "LocationRack"
    _identifiers = ("name", "site")
    _attributes = ("tags", "asset_tag", "serial_number", "facility_id", "height", "status")
    asset_tag: str | None = None
    serial_number: str | None = None
    facility_id: str | None = None
    name: str
    height: int | None = 42
    status: str | None = "active"
    site: str
    tags: list[str] | None = []


class IpamVLAN(_GeneratedModelBase):
    _modelname = "IpamVLAN"
    _identifiers = ("name", "vlan_id", "vlan_group")
    _attributes = ("description", "status")
    name: str
    description: str | None = None
    vlan_id: int
    status: str | None = "active"
    vlan_group: str


class IpamVLANGroup(_GeneratedModelBase):
    _modelname = "IpamVLANGroup"
    _identifiers = ("name",)
    _attributes = ("description",)
    description: str | None = None
    name: str


class IpamVRF(_GeneratedModelBase):
    _modelname = "IpamVRF"
    _identifiers = ("name",)
    _attributes = ("export_rt", "import_rt", "vrf_rd", "enforce_unique", "description")
    vrf_rd: str | None = None
    enforce_unique: bool | None = True
    description: str | None = None
    name: str
    export_rt: list[str] | None = []
    import_rt: list[str] | None = []


class IpamRouteTarget(_GeneratedModelBase):
    _modelname = "IpamRouteTarget"
    _identifiers = ("name",)
    _attributes = ("description",)
    description: str | None = None
    name: str
