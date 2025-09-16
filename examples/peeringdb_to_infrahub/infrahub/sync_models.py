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
    _attributes = ("ipv6_max_prefixes", "name", "ipv4_max_prefixes", "irr_as_set")
    ipv6_max_prefixes: int | None = None
    name: str
    ipv4_max_prefixes: int | None = None
    asn: int
    irr_as_set: str | None = None

    local_id: str | None = None
    local_data: Any | None = None
