from __future__ import annotations

from typing import Any

from infrahub_sync.adapters.peeringmanager import PeeringmanagerModel


# -------------------------------------------------------
# AUTO-GENERATED FILE, DO NOT MODIFY
#  This file has been generated with the command `infrahub-sync generate`
#  All modifications will be lost the next time you reexecute this command
# -------------------------------------------------------
class InfraAutonomousSystem(PeeringmanagerModel):
    _modelname = "InfraAutonomousSystem"
    _identifiers = ("asn",)
    _attributes = (
        "organization",
        "ipv4_max_prefixes",
        "name",
        "ipv6_max_prefixes",
        "affiliated",
        "irr_as_set",
        "description",
    )
    ipv4_max_prefixes: int | None = None
    name: str
    ipv6_max_prefixes: int | None = None
    affiliated: bool | None = None
    irr_as_set: str | None = None
    asn: int
    description: str | None = None
    organization: list[str] | None = []

    local_id: str | None = None
    local_data: Any | None = None


class InfraBGPPeerGroup(PeeringmanagerModel):
    _modelname = "InfraBGPPeerGroup"
    _identifiers = ("name",)
    _attributes = ("import_policies", "bgp_communities", "export_policies", "status", "description")
    status: str | None = None
    name: str
    description: str | None = None
    import_policies: list[str] | None = []
    bgp_communities: list[str] | None = []
    export_policies: list[str] | None = []

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


class InfraBGPCommunity(PeeringmanagerModel):
    _modelname = "InfraBGPCommunity"
    _identifiers = ("name",)
    _attributes = ("community_type", "description", "label", "value")
    community_type: str | None = None
    name: str
    description: str | None = None
    label: str | None = None
    value: str

    local_id: str | None = None
    local_data: Any | None = None


class InfraBGPRoutingPolicy(PeeringmanagerModel):
    _modelname = "InfraBGPRoutingPolicy"
    _identifiers = ("name",)
    _attributes = ("bgp_communities", "description", "policy_type", "address_family", "label", "weight")
    name: str
    description: str | None = None
    policy_type: str
    address_family: int
    label: str | None = None
    weight: int | None = 1000
    bgp_communities: list[str] | None = []

    local_id: str | None = None
    local_data: Any | None = None


class InfraIXP(PeeringmanagerModel):
    _modelname = "InfraIXP"
    _identifiers = ("name",)
    _attributes = ("import_policies", "export_policies", "bgp_communities", "description", "status")
    name: str
    description: str | None = None
    status: str | None = "enabled"
    import_policies: list[str] | None = []
    export_policies: list[str] | None = []
    bgp_communities: list[str] | None = []

    local_id: str | None = None
    local_data: Any | None = None


class InfraIXPConnection(PeeringmanagerModel):
    _modelname = "InfraIXPConnection"
    _identifiers = ("name",)
    _attributes = (
        "internet_exchange_point",
        "ipv4_address",
        "ipv6_address",
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
    ipv4_address: str | None = None
    ipv6_address: str | None = None

    local_id: str | None = None
    local_data: Any | None = None
