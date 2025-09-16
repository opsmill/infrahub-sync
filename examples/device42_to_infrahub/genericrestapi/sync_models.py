from __future__ import annotations

from typing import Any, List

from infrahub_sync.adapters.genericrestapi import GenericrestapiModel

# -------------------------------------------------------
# AUTO-GENERATED FILE, DO NOT MODIFY
#  This file has been generated with the command `infrahub-sync generate`
#  All modifications will be lost the next time you reexecute this command
# -------------------------------------------------------
class BuiltinTag(GenericrestapiModel):
    _modelname = "BuiltinTag"
    _identifiers = ("name",)
    _attributes = ()
    name: str

    local_id: str | None = None
    local_data: Any | None = None

class LocationSite(GenericrestapiModel):
    _modelname = "LocationSite"
    _identifiers = ("name",)
    _attributes = ("tags",)
    name: str
    tags: list[str] | None = []

    local_id: str | None = None
    local_data: Any | None = None

class OrganizationTenant(GenericrestapiModel):
    _modelname = "OrganizationTenant"
    _identifiers = ("name",)
    _attributes = ("description",)
    description: str | None = None
    name: str

    local_id: str | None = None
    local_data: Any | None = None
