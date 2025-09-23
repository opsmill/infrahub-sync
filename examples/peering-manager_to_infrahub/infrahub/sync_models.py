from __future__ import annotations

from typing import Any, List

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
class IpamIPAddress(_ModelBaseClass):
    _modelname = "IpamIPAddress"
    _identifiers = ("address",)
    _attributes = ("description",)
    address: str
    description: str | None = None

    local_id: str | None = None
    local_data: Any | None = None

class OrganizationProvider(_ModelBaseClass):
    _modelname = "OrganizationProvider"
    _identifiers = ("name",)
    _attributes = ()
    name: str

    local_id: str | None = None
    local_data: Any | None = None

class InfraAutonomousSystem(_ModelBaseClass):
    _modelname = "InfraAutonomousSystem"
    _identifiers = ("asn",)
    _attributes = ("organization", "ipv4_max_prefixes", "ipv6_max_prefixes", "affiliated", "name", "irr_as_set", "description")
    ipv4_max_prefixes: int | None = None
    ipv6_max_prefixes: int | None = None
    affiliated: bool | None = None
    name: str
    asn: int
    irr_as_set: str | None = None
    description: str | None = None
    organization: str | None = None

    local_id: str | None = None
    local_data: Any | None = None

class InfraBGPPeerGroup(_ModelBaseClass):
    _modelname = "InfraBGPPeerGroup"
    _identifiers = ("name",)
    _attributes = ("export_policies", "import_policies", "bgp_communities", "description", "status")
    description: str | None = None
    status: str | None = None
    name: str
    export_policies: list[str] | None = []
    import_policies: list[str] | None = []
    bgp_communities: list[str] | None = []

    local_id: str | None = None
    local_data: Any | None = None

class InfraBGPCommunity(_ModelBaseClass):
    _modelname = "InfraBGPCommunity"
    _identifiers = ("name",)
    _attributes = ("value", "label", "community_type", "description")
    value: str
    label: str | None = None
    community_type: str | None = None
    description: str | None = None
    name: str

    local_id: str | None = None
    local_data: Any | None = None

class InfraBGPRoutingPolicy(_ModelBaseClass):
    _modelname = "InfraBGPRoutingPolicy"
    _identifiers = ("name",)
    _attributes = ("bgp_communities", "address_family", "description", "label", "policy_type", "weight")
    name: str
    address_family: int
    description: str | None = None
    label: str | None = None
    policy_type: str
    weight: int | None = 1000
    bgp_communities: list[str] | None = []

    local_id: str | None = None
    local_data: Any | None = None

class InfraIXP(_ModelBaseClass):
    _modelname = "InfraIXP"
    _identifiers = ("name",)
    _attributes = ("import_policies", "export_policies", "bgp_communities", "status", "description")
    name: str
    status: str | None = "enabled"
    description: str | None = None
    import_policies: list[str] | None = []
    export_policies: list[str] | None = []
    bgp_communities: list[str] | None = []

    local_id: str | None = None
    local_data: Any | None = None

class InfraIXPConnection(_ModelBaseClass):
    _modelname = "InfraIXPConnection"
    _identifiers = ("name",)
    _attributes = ("ipv4_address", "ipv6_address", "internet_exchange_point", "peeringdb_netixlan", "vlan", "description", "status")
    name: str
    peeringdb_netixlan: int | None = None
    vlan: int | None = None
    description: str | None = None
    status: str | None = "enabled"
    ipv4_address: str | None = None
    ipv6_address: str | None = None
    internet_exchange_point: str

    local_id: str | None = None
    local_data: Any | None = None
