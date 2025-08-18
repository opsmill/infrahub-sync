from __future__ import annotations

from typing import Any, List

from infrahub_sync.adapters.phpipam import PhpipamModel

# -------------------------------------------------------
# AUTO-GENERATED FILE, DO NOT MODIFY
#  This file has been generated with the command `infrahub-sync generate`
#  All modifications will be lost the next time you reexecute this command
# -------------------------------------------------------
class IpamIPPrefix(PhpipamModel):
    _modelname = "IpamIPPrefix"
    _identifiers = ("prefix",)
    _attributes = ("is_pool",)
    is_pool: bool | None = False
    prefix: str

    local_id: str | None = None
    local_data: Any | None = None

class IpamIPAddress(PhpipamModel):
    _modelname = "IpamIPAddress"
    _identifiers = ("address",)
    _attributes = ("description",)
    description: str | None = None
    address: str

    local_id: str | None = None
    local_data: Any | None = None
