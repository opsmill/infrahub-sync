from __future__ import annotations

from typing import Any

from infrahub_sync.adapters.librenms import LibrenmsModel


# -------------------------------------------------------
# AUTO-GENERATED FILE, DO NOT MODIFY
#  This file has been generated with the command `infrahub-sync generate`
#  All modifications will be lost the next time you reexecute this command
# -------------------------------------------------------
class CoreStandardGroup(LibrenmsModel):
    _modelname = "CoreStandardGroup"
    _identifiers = ("name",)
    _attributes = ("description",)
    description: str | None = None
    name: str

    local_id: str | None = None
    local_data: Any | None = None


class InfraDevice(LibrenmsModel):
    _modelname = "InfraDevice"
    _identifiers = ("name",)
    _attributes = ("site", "type")
    type: str
    name: str
    site: str

    local_id: str | None = None
    local_data: Any | None = None


class IpamIPAddress(LibrenmsModel):
    _modelname = "IpamIPAddress"
    _identifiers = ("address",)
    _attributes = ("description",)
    address: str
    description: str | None = None

    local_id: str | None = None
    local_data: Any | None = None


class LocationSite(LibrenmsModel):
    _modelname = "LocationSite"
    _identifiers = ("name",)
    _attributes = ("description",)
    description: str | None = None
    name: str

    local_id: str | None = None
    local_data: Any | None = None
