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
    _attributes = ("affiliated", "description", "ipv4_max_prefixes", "ipv6_max_prefixes", "irr_as_set", "name")
    affiliated: bool | None = None
    description: str | None = None
    ipv4_max_prefixes: int | None = None
    ipv6_max_prefixes: int | None = None
    irr_as_set: str | None = None
    name: str
    asn: int

    local_id: str | None = None
    local_data: Any | None = None


class InfraBGPPeerGroup(InfrahubModel):
    _modelname = "InfraBGPPeerGroup"
    _identifiers = ("name",)
    _attributes = ("import_policies", "export_policies", "bgp_communities", "description", "status")
    description: str | None = None
    name: str
    status: str | None = None
    import_policies: list[str] | None = []
    export_policies: list[str] | None = []
    bgp_communities: list[str] | None = []

    local_id: str | None = None
    local_data: Any | None = None


class InfraBGPRoutingPolicy(InfrahubModel):
    _modelname = "InfraBGPRoutingPolicy"
    _identifiers = ("name",)
    _attributes = ("bgp_communities", "description", "policy_type", "label", "address_family", "weight")
    description: str | None = None
    policy_type: str
    label: str | None = None
    address_family: int
    weight: int | None = 1000
    name: str
    bgp_communities: list[str] | None = []

    local_id: str | None = None
    local_data: Any | None = None


class InfraBGPCommunity(InfrahubModel):
    _modelname = "InfraBGPCommunity"
    _identifiers = ("name",)
    _attributes = ("community_type", "value", "description", "label")
    community_type: str | None = None
    value: str
    description: str | None = None
    name: str
    label: str | None = None

    local_id: str | None = None
    local_data: Any | None = None


class InfraIXP(InfrahubModel):
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


class InfraIXPConnection(InfrahubModel):
    _modelname = "InfraIXPConnection"
    _identifiers = ("name",)
    _attributes = ("internet_exchange_point", "description", "peeringdb_netixlan", "vlan", "status")
    description: str | None = None
    peeringdb_netixlan: int | None = None
    name: str
    vlan: int | None = None
    status: str | None = "enabled"
    internet_exchange_point: str

    local_id: str | None = None
    local_data: Any | None = None
