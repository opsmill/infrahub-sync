from __future__ import annotations

from typing import Any

from infrahub_sync.adapters.genenericrestapi import GenenericrestapiModel


# -------------------------------------------------------
# AUTO-GENERATED FILE, DO NOT MODIFY
#  This file has been generated with the command `infrahub-sync generate`
#  All modifications will be lost the next time you reexecute this command
# -------------------------------------------------------
class InfraAutonomousSystem(GenenericrestapiModel):
    _modelname = "InfraAutonomousSystem"
    _identifiers = ("asn",)
    _attributes = (
        "organization",
        "affiliated",
        "irr_as_set",
        "name",
        "ipv4_max_prefixes",
        "description",
        "ipv6_max_prefixes",
    )
    asn: int
    affiliated: bool | None = None
    irr_as_set: str | None = None
    name: str
    ipv4_max_prefixes: int | None = None
    description: str | None = None
    ipv6_max_prefixes: int | None = None
    organization: str | None = None

    local_id: str | None = None
    local_data: Any | None = None


class InfraBGPPeerGroup(GenenericrestapiModel):
    _modelname = "InfraBGPPeerGroup"
    _identifiers = ("name",)
    _attributes = ("bgp_communities", "import_policies", "export_policies", "description", "status")
    name: str
    description: str | None = None
    status: str | None = None
    bgp_communities: list[str] | None = []
    import_policies: list[str] | None = []
    export_policies: list[str] | None = []

    local_id: str | None = None
    local_data: Any | None = None


class IpamIPAddress(GenenericrestapiModel):
    _modelname = "IpamIPAddress"
    _identifiers = ("address",)
    _attributes = ("description",)
    description: str | None = None
    address: str

    local_id: str | None = None
    local_data: Any | None = None


class OrganizationProvider(GenenericrestapiModel):
    _modelname = "OrganizationProvider"
    _identifiers = ("name",)
    _attributes = ()
    name: str

    local_id: str | None = None
    local_data: Any | None = None


class InfraBGPCommunity(GenenericrestapiModel):
    _modelname = "InfraBGPCommunity"
    _identifiers = ("name",)
    _attributes = ("description", "label", "community_type", "value")
    name: str
    description: str | None = None
    label: str | None = None
    community_type: str | None = None
    value: str

    local_id: str | None = None
    local_data: Any | None = None


class InfraBGPRoutingPolicy(GenenericrestapiModel):
    _modelname = "InfraBGPRoutingPolicy"
    _identifiers = ("name",)
    _attributes = ("bgp_communities", "address_family", "policy_type", "label", "weight", "description")
    address_family: int
    policy_type: str
    label: str | None = None
    weight: int | None = 1000
    name: str
    description: str | None = None
    bgp_communities: list[str] | None = []

    local_id: str | None = None
    local_data: Any | None = None


class InfraIXP(GenenericrestapiModel):
    _modelname = "InfraIXP"
    _identifiers = ("name",)
    _attributes = ("export_policies", "bgp_communities", "import_policies", "status", "description")
    status: str | None = "enabled"
    name: str
    description: str | None = None
    export_policies: list[str] | None = []
    bgp_communities: list[str] | None = []
    import_policies: list[str] | None = []

    local_id: str | None = None
    local_data: Any | None = None


class InfraIXPConnection(GenenericrestapiModel):
    _modelname = "InfraIXPConnection"
    _identifiers = ("name",)
    _attributes = (
        "internet_exchange_point",
        "ipv4_address",
        "ipv6_address",
        "status",
        "peeringdb_netixlan",
        "vlan",
        "description",
    )
    name: str
    status: str | None = "enabled"
    peeringdb_netixlan: int | None = None
    vlan: int | None = None
    description: str | None = None
    internet_exchange_point: str
    ipv4_address: str | None = None
    ipv6_address: str | None = None

    local_id: str | None = None
    local_data: Any | None = None
