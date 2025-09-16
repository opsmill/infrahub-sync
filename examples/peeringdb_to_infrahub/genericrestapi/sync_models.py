from __future__ import annotations

from typing import Any

from infrahub_sync.adapters.genericrestapi import GenericrestapiModel


# -------------------------------------------------------
# AUTO-GENERATED FILE, DO NOT MODIFY
#  This file has been generated with the command `infrahub-sync generate`
#  All modifications will be lost the next time you reexecute this command
# -------------------------------------------------------
class InfraAutonomousSystem(GenericrestapiModel):
    _modelname = "InfraAutonomousSystem"
    _identifiers = ("asn",)
    _attributes = ("irr_as_set", "name", "ipv4_max_prefixes", "ipv6_max_prefixes")
    asn: int
    irr_as_set: str | None = None
    name: str
    ipv4_max_prefixes: int | None = None
    ipv6_max_prefixes: int | None = None

    local_id: str | None = None
    local_data: Any | None = None
