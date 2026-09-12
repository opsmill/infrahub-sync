from __future__ import annotations

from infrahub_sync.plugin_loader import PluginLoader

from .sync_models import (
    InfraAutonomousSystem,
    InfraBGPCommunity,
    InfraBGPPeerGroup,
    InfraBGPRoutingPolicy,
    InfraIXP,
    InfraIXPConnection,
    IpamIPAddress,
    OrganizationProvider,
)

# Load adapter class dynamically at runtime

_AdapterBaseClass = PluginLoader().resolve("infrahub")


# -------------------------------------------------------
# Generated file - do not edit.
# -------------------------------------------------------
class InfrahubSync(_AdapterBaseClass):
    IpamIPAddress = IpamIPAddress
    OrganizationProvider = OrganizationProvider
    InfraAutonomousSystem = InfraAutonomousSystem
    InfraBGPPeerGroup = InfraBGPPeerGroup
    InfraBGPCommunity = InfraBGPCommunity
    InfraBGPRoutingPolicy = InfraBGPRoutingPolicy
    InfraIXP = InfraIXP
    InfraIXPConnection = InfraIXPConnection
