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
class InfraDevice(_ModelBaseClass):
    _modelname = "InfraDevice"
    _identifiers = ("name",)
    _attributes = ("type",)
    name: str
    type: str

    local_id: str | None = None
    local_data: Any | None = None
