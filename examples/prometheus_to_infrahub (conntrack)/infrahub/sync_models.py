from __future__ import annotations

from typing import Any, List

from infrahub_sync.adapters.infrahub import InfrahubModel

# -------------------------------------------------------
# AUTO-GENERATED FILE, DO NOT MODIFY
#  This file has been generated with the command `infrahub-sync generate`
#  All modifications will be lost the next time you reexecute this command
# -------------------------------------------------------
class MonitoringNetConntrackDialer(InfrahubModel):
    _modelname = "MonitoringNetConntrackDialer"
    _identifiers = ("dialer",)
    _attributes = ("status", "established_rps", "closed_rps")
    dialer: str
    status: str | None = "unknown"
    established_rps: int | None = 0
    closed_rps: int | None = 0

    local_id: str | None = None
    local_data: Any | None = None
