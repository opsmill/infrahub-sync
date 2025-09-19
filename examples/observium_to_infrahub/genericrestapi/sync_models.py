from __future__ import annotations

from typing import Any

from infrahub_sync.plugin_loader import PluginLoader

# Load model class dynamically at runtime

_ModelBaseClass = PluginLoader().resolve("genericrestapi", default_class_candidates=("Model",))


# -------------------------------------------------------
# AUTO-GENERATED FILE, DO NOT MODIFY
#  This file has been generated with the command `infrahub-sync generate`
#  All modifications will be lost the next time you reexecute this command
# -------------------------------------------------------
class CoreStandardGroup(_ModelBaseClass):
    _modelname = "CoreStandardGroup"
    _identifiers = ("name",)
    _attributes = ("description",)
    description: str | None = None
    name: str

    local_id: str | None = None
    local_data: Any | None = None


class InfraDevice(_ModelBaseClass):
    _modelname = "InfraDevice"
    _identifiers = ("name",)
    _attributes = ("primary_address", "platform", "description", "type")
    name: str
    description: str | None = None
    type: str
    primary_address: str | None = None
    platform: str | None = None

    local_id: str | None = None
    local_data: Any | None = None


class IpamIPAddress(_ModelBaseClass):
    _modelname = "IpamIPAddress"
    _identifiers = ("address",)
    _attributes = ("description",)
    address: str
    description: str | None = None

    local_id: str | None = None
    local_data: Any | None = None
