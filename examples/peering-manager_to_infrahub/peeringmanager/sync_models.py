from __future__ import annotations

from typing import Any

from infrahub_sync.plugin_loader import PluginLoader

# Load model class dynamically at runtime

_ModelBaseClass = PluginLoader().resolve("peeringmanager", default_class_candidates=("Model",))


# -------------------------------------------------------
# AUTO-GENERATED FILE, DO NOT MODIFY
#  This file has been generated with the command `infrahub-sync generate`
#  All modifications will be lost the next time you reexecute this command
# -------------------------------------------------------
class InfraAutonomousSystem(_ModelBaseClass):
    _modelname = "InfraAutonomousSystem"
    _identifiers = ("asn",)
    _attributes = (
        "organization",
        "ipv6_max_prefixes",
        "irr_as_set",
        "ipv4_max_prefixes",
        "description",
        "affiliated",
        "name",
    )
    ipv6_max_prefixes: int | None = None
    irr_as_set: str | None = None
    asn: int
    ipv4_max_prefixes: int | None = None
    description: str | None = None
    affiliated: bool | None = None
    name: str
    organization: str | None = None

    local_id: str | None = None
    local_data: Any | None = None


class InfraBGPPeerGroup(_ModelBaseClass):
    _modelname = "InfraBGPPeerGroup"
    _identifiers = ("name",)
    _attributes = ("bgp_communities", "import_policies", "export_policies", "status", "description")
    status: str | None = None
    description: str | None = None
    name: str
    bgp_communities: list[str] | None = []
    import_policies: list[str] | None = []
    export_policies: list[str] | None = []

    local_id: str | None = None
    local_data: Any | None = None


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


class InfraBGPCommunity(_ModelBaseClass):
    _modelname = "InfraBGPCommunity"
    _identifiers = ("name",)
    _attributes = ("community_type", "value", "label", "description")
    community_type: str | None = None
    value: str
    label: str | None = None
    name: str
    description: str | None = None

    local_id: str | None = None
    local_data: Any | None = None


class InfraBGPRoutingPolicy(_ModelBaseClass):
    _modelname = "InfraBGPRoutingPolicy"
    _identifiers = ("name",)
    _attributes = ("bgp_communities", "weight", "policy_type", "address_family", "label", "description")
    weight: int | None = 1000
    policy_type: str
    address_family: int
    name: str
    label: str | None = None
    description: str | None = None
    bgp_communities: list[str] | None = []

    local_id: str | None = None
    local_data: Any | None = None


class InfraIXP(_ModelBaseClass):
    _modelname = "InfraIXP"
    _identifiers = ("name",)
    _attributes = ("bgp_communities", "import_policies", "export_policies", "description", "status")
    description: str | None = None
    status: str | None = "enabled"
    name: str
    bgp_communities: list[str] | None = []
    import_policies: list[str] | None = []
    export_policies: list[str] | None = []

    local_id: str | None = None
    local_data: Any | None = None


class InfraIXPConnection(_ModelBaseClass):
    _modelname = "InfraIXPConnection"
    _identifiers = ("name",)
    _attributes = (
        "internet_exchange_point",
        "ipv6_address",
        "ipv4_address",
        "vlan",
        "status",
        "peeringdb_netixlan",
        "description",
    )
    vlan: int | None = None
    name: str
    status: str | None = "enabled"
    peeringdb_netixlan: int | None = None
    description: str | None = None
    internet_exchange_point: str
    ipv6_address: str | None = None
    ipv4_address: str | None = None

    local_id: str | None = None
    local_data: Any | None = None
