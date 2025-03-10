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
    _attributes = ("description", "name", "irr_as_set", "ipv4_max_prefixes", "ipv6_max_prefixes", "affiliated")
    asn: int
    description: str | None = None
    name: str
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
    import_policies: list[str] | None = []
    export_policies: list[str] | None = []
    bgp_communities: list[str] | None = []

    local_id: str | None = None
    local_data: Any | None = None


class InfraBGPRoutingPolicy(InfrahubModel):
    _modelname = "InfraBGPRoutingPolicy"
    _identifiers = ("name",)
    _attributes = ("bgp_communities", "policy_type", "address_family", "description", "weight", "label")
    policy_type: str
    address_family: int
    description: str | None = None
    name: str
    weight: int | None = 1000
    label: str | None = None
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
    label: str | None = None
    name: str

    local_id: str | None = None
    local_data: Any | None = None


class InfraIXP(InfrahubModel):
    _modelname = "InfraIXP"
    _identifiers = ("name",)
    _attributes = ("import_policies", "bgp_communities", "export_policies", "status", "description")
    name: str
    status: str | None = "enabled"
    description: str | None = None
    import_policies: list[str] | None = []
    bgp_communities: list[str] | None = []
    export_policies: list[str] | None = []

    local_id: str | None = None
    local_data: Any | None = None


class InfraIXPConnection(InfrahubModel):
    _modelname = "InfraIXPConnection"
    _identifiers = ("name",)
    _attributes = ("internet_exchange_point", "status", "vlan", "peeringdb_netixlan", "description")
    status: str | None = "enabled"
    name: str
    vlan: int | None = None
    peeringdb_netixlan: int | None = None
    description: str | None = None
    internet_exchange_point: str

    local_id: str | None = None
    local_data: Any | None = None
