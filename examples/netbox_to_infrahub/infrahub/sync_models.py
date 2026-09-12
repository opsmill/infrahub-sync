from __future__ import annotations

from typing import Any

from infrahub_sync.plugin_loader import PluginLoader

# Load model class dynamically at runtime (honor adapters_path, safe fallback)
try:
    _loader = PluginLoader.from_env_and_args(adapter_paths=[])
    _spec = "infrahub"
    _ModelBaseClass = _loader.resolve(_spec, default_class_candidates=("Model",))
except Exception:  # noqa: BLE001 -- generated adapters need a safe import fallback
    # Fallback: use DiffSyncModel to avoid import-time failure
    from diffsync import DiffSyncModel as _FallbackModel

    _ModelBaseClass = _FallbackModel


# -------------------------------------------------------
# Generated file - do not edit.
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
    description: str | None = None
    name: str


class DcimCircuit(_GeneratedModelBase):
    _modelname = "DcimCircuit"
    _identifiers = ("circuit_id",)
    _attributes = ("commit_rate", "description", "provider", "status")
    circuit_id: str
    commit_rate: int | None = None
    description: str | None = None
    status: str | None = "active"
    provider: str


class DcimDevice(_GeneratedModelBase):
    _modelname = "DcimDevice"
    _identifiers = ("location", "name")
    _attributes = (
        "description",
        "device_type",
        "platform",
        "position",
        "primary_address",
        "rack_face",
        "serial",
        "status",
        "tags",
    )
    description: str | None = None
    name: str
    position: int | None = None
    rack_face: str | None = "front"
    serial: str | None = None
    status: str | None = "active"
    device_type: str | None = None
    location: str
    platform: str | None = None
    primary_address: str | None = None
    tags: list[str] | None = []


class DcimDeviceType(_GeneratedModelBase):
    _modelname = "DcimDeviceType"
    _identifiers = ("name", "manufacturer")
    _attributes = ("full_depth", "height", "part_number", "weight")
    full_depth: bool | None = True
    height: int | None = 1
    name: str
    part_number: str | None = None
    weight: int | None = None
    manufacturer: str


class DcimPlatform(_GeneratedModelBase):
    _modelname = "DcimPlatform"
    _identifiers = ("name",)
    _attributes = ("manufacturer",)
    name: str
    manufacturer: str | None = None


class InterfaceLag(_GeneratedModelBase):
    _modelname = "InterfaceLag"
    _identifiers = ("device", "name")
    _attributes = (
        "bundle_number",
        "description",
        "ip_addresses",
        "l2_mode",
        "mac_address",
        "tagged_vlan",
        "untagged_vlan",
    )
    bundle_number: int
    description: str | None = None
    l2_mode: str | None = None
    mac_address: str | None = None
    name: str
    device: str
    ip_addresses: list[str] | None = []
    tagged_vlan: list[str] | None = []
    untagged_vlan: str | None = None


class InterfacePhysical(_GeneratedModelBase):
    _modelname = "InterfacePhysical"
    _identifiers = ("device", "name")
    _attributes = ("bundle", "description", "ip_addresses", "l2_mode", "mac_address", "mtu")
    description: str | None = None
    l2_mode: str | None = None
    mac_address: str | None = None
    mtu: int | None = 1500
    name: str
    bundle: str | None = None
    device: str
    ip_addresses: list[str] | None = []


class InterfaceVirtual(_GeneratedModelBase):
    _modelname = "InterfaceVirtual"
    _identifiers = ("device", "name")
    _attributes = ("description", "ip_addresses", "l2_mode", "mac_address", "tagged_vlan", "untagged_vlan")
    description: str | None = None
    l2_mode: str | None = None
    mac_address: str | None = None
    name: str
    device: str
    ip_addresses: list[str] | None = []
    tagged_vlan: list[str] | None = []
    untagged_vlan: str | None = None


class IpamAggregate(_GeneratedModelBase):
    _modelname = "IpamAggregate"
    _identifiers = ("prefix",)
    _attributes = ("date_added", "description", "rir")
    date_added: str | None = None
    description: str | None = None
    prefix: str
    rir: str


class IpamIPAddress(_GeneratedModelBase):
    _modelname = "IpamIPAddress"
    _identifiers = ("address", "vrf")
    _attributes = ("description", "status")
    address: str
    description: str | None = None
    status: str | None = "active"
    vrf: str | None = None


class IpamPrefix(_GeneratedModelBase):
    _modelname = "IpamPrefix"
    _identifiers = ("prefix", "vrf")
    _attributes = ("description", "member_type", "status")
    description: str | None = None
    member_type: str | None = "address"
    prefix: str
    status: str | None = "active"
    vrf: str | None = None


class IpamRouteTarget(_GeneratedModelBase):
    _modelname = "IpamRouteTarget"
    _identifiers = ("name",)
    _attributes = ("description",)
    description: str | None = None
    name: str


class IpamVLAN(_GeneratedModelBase):
    _modelname = "IpamVLAN"
    _identifiers = ("name", "vlan_id", "vlan_group")
    _attributes = ("description", "status")
    description: str | None = None
    name: str
    status: str | None = "active"
    vlan_id: int
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
    _attributes = ("description", "enforce_unique", "export_rt", "import_rt", "vrf_rd")
    description: str | None = None
    enforce_unique: bool | None = True
    name: str
    vrf_rd: str | None = None
    export_rt: list[str] | None = []
    import_rt: list[str] | None = []


class LocationRack(_GeneratedModelBase):
    _modelname = "LocationRack"
    _identifiers = ("name", "site")
    _attributes = ("asset_tag", "facility_id", "height", "serial_number", "status", "tags")
    asset_tag: str | None = None
    facility_id: str | None = None
    height: int | None = 42
    name: str
    serial_number: str | None = None
    status: str | None = "active"
    site: str
    tags: list[str] | None = []


class LocationSite(_GeneratedModelBase):
    _modelname = "LocationSite"
    _identifiers = ("name",)
    _attributes = ("description", "facility", "physical_address", "status", "tags", "timezone")
    description: str | None = None
    facility: str | None = None
    name: str
    physical_address: str | None = None
    status: str | None = "active"
    timezone: str | None = None
    tags: list[str] | None = []


class OrganizationManufacturer(_GeneratedModelBase):
    _modelname = "OrganizationManufacturer"
    _identifiers = ("name",)
    _attributes = ("description", "tags")
    description: str | None = None
    name: str
    tags: list[str] | None = []


class OrganizationProvider(_GeneratedModelBase):
    _modelname = "OrganizationProvider"
    _identifiers = ("name",)
    _attributes = ("description", "tags")
    description: str | None = None
    name: str
    tags: list[str] | None = []


class OrganizationRIR(_GeneratedModelBase):
    _modelname = "OrganizationRIR"
    _identifiers = ("name",)
    _attributes = ("description", "is_private", "tags")
    description: str | None = None
    is_private: bool | None = False
    name: str
    tags: list[str] | None = []
