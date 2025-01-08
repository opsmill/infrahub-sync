from __future__ import annotations

from typing import Any

from infrahub_sync.adapters.infrahub import InfrahubModel


# -------------------------------------------------------
# AUTO-GENERATED FILE, DO NOT MODIFY
#  This file has been generated with the command `infrahub-sync generate`
#  All modifications will be lost the next time you reexecute this command
# -------------------------------------------------------
class InfraAutonomousSystem(InfrahubModel):
    _modelname = "InfraAutonomousSystem"
    _identifiers = ("asn",)
    _attributes = ("affiliated", "name", "irr_as_set", "description", "ipv4_max_prefixes", "ipv6_max_prefixes")
    affiliated: bool | None = None
    name: str
    asn: int
    irr_as_set: str | None = None
    description: str | None = None
    ipv4_max_prefixes: int | None = None
    ipv6_max_prefixes: int | None = None

    local_id: str | None = None
    local_data: Any | None = None


class InfraBGPPeerGroup(InfrahubModel):
    _modelname = "InfraBGPPeerGroup"
    _identifiers = ("name",)
    _attributes = ("bgp_communities", "import_policies", "export_policies", "status", "description")
    status: str | None = None
    name: str
    description: str | None = None
    bgp_communities: list[str] | None = []
    import_policies: list[str] | None = []
    export_policies: list[str] | None = []

    local_id: str | None = None
    local_data: Any | None = None


class IpamIPAddress(InfrahubModel):
    _modelname = "IpamIPAddress"
    _identifiers = ("address",)
    _attributes = ("description",)
    address: str
    description: str | None = None

    local_id: str | None = None
    local_data: Any | None = None


class InfraBGPCommunity(InfrahubModel):
    _modelname = "InfraBGPCommunity"
    _identifiers = ("name",)
    _attributes = ("description", "community_type", "value", "label")
    description: str | None = None
    community_type: str | None = None
    value: str
    name: str
    label: str | None = None

    local_id: str | None = None
    local_data: Any | None = None


class InfraBGPRoutingPolicy(InfrahubModel):
    _modelname = "InfraBGPRoutingPolicy"
    _identifiers = ("name",)
    _attributes = ("bgp_communities", "label", "policy_type", "address_family", "weight", "description")
    name: str
    label: str | None = None
    policy_type: str
    address_family: int
    weight: int | None = 1000
    description: str | None = None
    bgp_communities: list[str] | None = []

    local_id: str | None = None
    local_data: Any | None = None


class InfraIXP(InfrahubModel):
    _modelname = "InfraIXP"
    _identifiers = ("name",)
    _attributes = ("export_policies", "bgp_communities", "import_policies", "status", "description")
    name: str
    status: str | None = "enabled"
    description: str | None = None
    export_policies: list[str] | None = []
    bgp_communities: list[str] | None = []
    import_policies: list[str] | None = []

    local_id: str | None = None
    local_data: Any | None = None


class InfraIXPConnection(InfrahubModel):
    _modelname = "InfraIXPConnection"
    _identifiers = ("name",)
    _attributes = ("ipv4_address", "ipv6_address", "internet_exchange_point", "status", "description", "peeringdb_netixlan", "vlan")
    status: str | None = "enabled"
    description: str | None = None
    peeringdb_netixlan: int | None = None
    vlan: int | None = None
    name: str
    ipv4_address: str | None = None
    ipv6_address: str | None = None
    internet_exchange_point: str

    local_id: str | None = None
    local_data: Any | None = None
