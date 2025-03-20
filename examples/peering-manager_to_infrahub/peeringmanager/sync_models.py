from __future__ import annotations

from typing import Any, List

from infrahub_sync.adapters.peeringmanager import PeeringmanagerModel

# -------------------------------------------------------
# AUTO-GENERATED FILE, DO NOT MODIFY
#  This file has been generated with the command `infrahub-sync generate`
#  All modifications will be lost the next time you reexecute this command
# -------------------------------------------------------
class InfraAutonomousSystem(PeeringmanagerModel):
    _modelname = "InfraAutonomousSystem"
    _identifiers = ("asn",)
    _attributes = ("organization", "name")
    asn: int
    name: str
    organization: list[str] | None = []

    local_id: str | None = None
    local_data: Any | None = None

class InfraBGPCommunity(PeeringmanagerModel):
    _modelname = "InfraBGPCommunity"
    _identifiers = ("name",)
    _attributes = ("label", "value", "community_type", "description")
    label: str | None = None
    value: str
    community_type: str | None = None
    name: str
    description: str | None = None

    local_id: str | None = None
    local_data: Any | None = None

class InfraBGPPeerGroup(PeeringmanagerModel):
    _modelname = "InfraBGPPeerGroup"
    _identifiers = ("name",)
    _attributes = ("import_policies", "export_policies", "bgp_communities", "description", "status")
    description: str | None = None
    status: str | None = None
    name: str
    import_policies: list[str] | None = []
    export_policies: list[str] | None = []
    bgp_communities: list[str] | None = []

    local_id: str | None = None
    local_data: Any | None = None

class InfraBGPRoutingPolicy(PeeringmanagerModel):
    _modelname = "InfraBGPRoutingPolicy"
    _identifiers = ("name",)
    _attributes = ("bgp_communities", "address_family", "policy_type", "description", "label", "weight")
    address_family: int
    policy_type: str
    description: str | None = None
    name: str
    label: str | None = None
    weight: int | None = 1000
    bgp_communities: list[str] | None = []

    local_id: str | None = None
    local_data: Any | None = None

class InfraIXP(PeeringmanagerModel):
    _modelname = "InfraIXP"
    _identifiers = ("name",)
    _attributes = ("import_policies", "export_policies", "bgp_communities", "description", "status")
    description: str | None = None
    name: str
    status: str | None = "enabled"
    import_policies: list[str] | None = []
    export_policies: list[str] | None = []
    bgp_communities: list[str] | None = []

    local_id: str | None = None
    local_data: Any | None = None

class InfraIXPConnection(PeeringmanagerModel):
    _modelname = "InfraIXPConnection"
    _identifiers = ("name",)
    _attributes = ("ipv6_address", "ipv4_address", "internet_exchange_point", "status", "description", "peeringdb_netixlan", "vlan")
    status: str | None = "enabled"
    description: str | None = None
    peeringdb_netixlan: int | None = None
    name: str
    vlan: int | None = None
    ipv6_address: str | None = None
    ipv4_address: str | None = None
    internet_exchange_point: str

    local_id: str | None = None
    local_data: Any | None = None

class IpamIPAddress(PeeringmanagerModel):
    _modelname = "IpamIPAddress"
    _identifiers = ("address",)
    _attributes = ("description",)
    address: str
    description: str | None = None

    local_id: str | None = None
    local_data: Any | None = None

class OrganizationProvider(PeeringmanagerModel):
    _modelname = "OrganizationProvider"
    _identifiers = ("name",)
    _attributes = ()
    name: str

    local_id: str | None = None
    local_data: Any | None = None
