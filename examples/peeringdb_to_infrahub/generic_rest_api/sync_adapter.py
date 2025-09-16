from infrahub_sync.adapters.generic_rest_api import Generic_Rest_ApiAdapter

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


# -------------------------------------------------------
# AUTO-GENERATED FILE, DO NOT MODIFY
#  This file has been generated with the command `infrahub-sync generate`
#  All modifications will be lost the next time you reexecute this command
# -------------------------------------------------------
class Generic_Rest_ApiSync(Generic_Rest_ApiAdapter):
    InfraAutonomousSystem = InfraAutonomousSystem
    InfraBGPPeerGroup = InfraBGPPeerGroup
    IpamIPAddress = IpamIPAddress
    OrganizationProvider = OrganizationProvider
    InfraBGPRoutingPolicy = InfraBGPRoutingPolicy
    InfraBGPCommunity = InfraBGPCommunity
    InfraIXP = InfraIXP
    InfraIXPConnection = InfraIXPConnection
