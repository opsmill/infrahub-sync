from __future__ import annotations

from infrahub_sync.plugin_loader import PluginLoader

from .sync_models import (
    InfraAutonomousSystem,
    InfraBGPCommunity,
    InfraBGPPeerGroup,
    InfraBGPRoutingPolicy,
    InfraIXP,
    InfraIXPConnection,
)

# Load adapter class dynamically at runtime

_AdapterBaseClass = PluginLoader().resolve("peeringmanager")


# -------------------------------------------------------
# Generated file - do not edit.
# -------------------------------------------------------
class PeeringmanagerSync(_AdapterBaseClass):
    InfraAutonomousSystem = InfraAutonomousSystem
    InfraBGPPeerGroup = InfraBGPPeerGroup
    InfraBGPCommunity = InfraBGPCommunity
    InfraBGPRoutingPolicy = InfraBGPRoutingPolicy
    InfraIXP = InfraIXP
    InfraIXPConnection = InfraIXPConnection
