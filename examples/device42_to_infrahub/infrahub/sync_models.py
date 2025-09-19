from __future__ import annotations

from typing import Any, List

from infrahub_sync.plugin_loader import PluginLoader

# Load model class dynamically at runtime

_ModelBaseClass = PluginLoader().resolve("infrahub", default_class_candidates=("Model",))


# -------------------------------------------------------
# AUTO-GENERATED FILE, DO NOT MODIFY
#  This file has been generated with the command `infrahub-sync generate`
#  All modifications will be lost the next time you reexecute this command
# -------------------------------------------------------
class BuiltinTag(_ModelBaseClass):
    _modelname = "BuiltinTag"
    _identifiers = ("name",)
    _attributes = ()
    name: str

    local_id: str | None = None
    local_data: Any | None = None

class LocationSite(_ModelBaseClass):
    _modelname = "LocationSite"
    _identifiers = ("name",)
    _attributes = ("tags",)
    name: str
    tags: list[str] | None = []

    local_id: str | None = None
    local_data: Any | None = None

class OrganizationTenant(_ModelBaseClass):
    _modelname = "OrganizationTenant"
    _identifiers = ("name",)
    _attributes = ("description",)
    name: str
    description: str | None = None

    local_id: str | None = None
    local_data: Any | None = None
