from infrahub_sync.adapters.phpipam import PhpipamAdapter

from .sync_models import (
    IpamIPAddress,
    IpamIPPrefix,
)


# -------------------------------------------------------
# AUTO-GENERATED FILE, DO NOT MODIFY
#  This file has been generated with the command `infrahub-sync generate`
#  All modifications will be lost the next time you reexecute this command
# -------------------------------------------------------
class PhpipamSync(PhpipamAdapter):
    IpamIPPrefix = IpamIPPrefix
    IpamIPAddress = IpamIPAddress
