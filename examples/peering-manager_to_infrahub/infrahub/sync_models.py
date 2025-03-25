from __future__ import annotations

from typing import Any, List

from infrahub_sync.adapters.infrahub import InfrahubModel

# -------------------------------------------------------
# AUTO-GENERATED FILE, DO NOT MODIFY
#  This file has been generated with the command `infrahub-sync generate`
#  All modifications will be lost the next time you reexecute this command
# -------------------------------------------------------
class InfraAutonomousSystem(InfrahubModel):
    _modelname = "InfraAutonomousSystem"
    _identifiers = ("asn",)
    _attributes = ("organization", "description", "name", "affiliated", "ipv6_max_prefixes", "irr_as_set", "ipv4_max_prefixes")
    description: str | None = None
    name: str
    asn: int
    affiliated: bool | None = None
    ipv6_max_prefixes: int | None = None
    irr_as_set: str | None = None
    ipv4_max_prefixes: int | None = None
    organization: str | None = None

    local_id: str | None = None
    local_data: Any | None = None

class InfraBGPPeerGroup(InfrahubModel):
    _modelname = "InfraBGPPeerGroup"
    _identifiers = ("name",)
    _attributes = ("bgp_communities", "import_policies", "export_policies", "description", "status")
    description: str | None = None
    status: str | None = None
    name: str
    bgp_communities: list[str] | None = []
    import_policies: list[str] | None = []
    export_policies: list[str] | None = []

    local_id: str | None = None
    local_data: Any | None = None

class IpamIPAddress(InfrahubModel):
    _modelname = "IpamIPAddress"
    _identifiers = ("address",)
    _attributes = ("description",)
    description: str | None = None
    address: str

    local_id: str | None = None
    local_data: Any | None = None

class OrganizationProvider(InfrahubModel):
    _modelname = "OrganizationProvider"
    _identifiers = ("name",)
    _attributes = ()
    name: str

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
    _attributes = ("ipv6_address", "ipv4_address", "internet_exchange_point", "description", "peeringdb_netixlan", "vlan", "status")
    description: str | None = None
    peeringdb_netixlan: int | None = None
    name: str
    vlan: int | None = None
    status: str | None = "enabled"
    ipv6_address: str | None = None
    ipv4_address: str | None = None
    internet_exchange_point: str

    local_id: str | None = None
    local_data: Any | None = None
