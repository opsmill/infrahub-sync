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
    _attributes = ("name", "description", "irr_as_set", "ipv4_max_prefixes", "ipv6_max_prefixes", "affiliated")
    name: str
    asn: int
    description: str | None = None
    irr_as_set: str | None = None
    ipv4_max_prefixes: int | None = None
    ipv6_max_prefixes: int | None = None
    affiliated: bool | None = None

    local_id: str | None = None
    local_data: Any | None = None


class InfraBGPPeerGroup(InfrahubModel):
    _modelname = "InfraBGPPeerGroup"
    _identifiers = ("name",)
    _attributes = ("import_policies", "export_policies", "bgp_communities", "description", "status")
    name: str
    description: str | None = None
    status: str | None = None
    import_policies: list[str] | None
    export_policies: list[str] | None
    bgp_communities: list[str] | None

    local_id: str | None = None
    local_data: Any | None = None


class InfraBGPCommunity(InfrahubModel):
    _modelname = "InfraBGPCommunity"
    _identifiers = ("name",)
    _attributes = ("label", "description", "value", "community_type")
    name: str
    label: str | None = None
    description: str | None = None
    value: str
    community_type: str | None = None

    local_id: str | None = None
    local_data: Any | None = None


class InfraBGPRoutingPolicy(InfrahubModel):
    _modelname = "InfraBGPRoutingPolicy"
    _identifiers = ("name",)
    _attributes = ("bgp_communities", "label", "description", "policy_type", "weight", "address_family")
    name: str
    label: str | None = None
    description: str | None = None
    policy_type: str
    weight: int | None = 1000
    address_family: int
    bgp_communities: list[str] | None

    local_id: str | None = None
    local_data: Any | None = None


class InfraIXP(InfrahubModel):
    _modelname = "InfraIXP"
    _identifiers = ("name",)
    _attributes = ("import_policies", "export_policies", "bgp_communities", "description", "status")
    name: str
    description: str | None = None
    status: str | None = "enabled"
    import_policies: list[str] | None
    export_policies: list[str] | None
    bgp_communities: list[str] | None

    local_id: str | None = None
    local_data: Any | None = None


class InfraIXPConnection(InfrahubModel):
    _modelname = "InfraIXPConnection"
    _identifiers = ("name",)
    _attributes = ("internet_exchange_point", "description", "peeringdb_netixlan", "status", "vlan")
    name: str
    description: str | None = None
    peeringdb_netixlan: int | None = None
    status: str | None = "enabled"
    vlan: int | None = None
    internet_exchange_point: str

    local_id: str | None = None
    local_data: Any | None = None
