from __future__ import annotations

from typing import Any

from infrahub_sync.plugin_loader import PluginLoader

# Load model class dynamically at runtime

_ModelBaseClass = PluginLoader().resolve("infrahub", default_class_candidates=("Model",))


# -------------------------------------------------------
# AUTO-GENERATED FILE, DO NOT MODIFY
#  This file has been generated with the command `infrahub-sync generate`
#  All modifications will be lost the next time you reexecute this command
# -------------------------------------------------------
class InfraAutonomousSystem(_ModelBaseClass):
    _modelname = "InfraAutonomousSystem"
    _identifiers = ("asn",)
    _attributes = ("ipv6_max_prefixes", "ipv4_max_prefixes", "name")
    ipv6_max_prefixes: int | None = None
    asn: int
    ipv4_max_prefixes: int | None = None
    name: str

    local_id: str | None = None
    local_data: Any | None = None
