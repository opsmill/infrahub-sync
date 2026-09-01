from __future__ import annotations

from typing import Any

from infrahub_sync.adapters.observium import ObserviumModel


# -------------------------------------------------------
# Generated file - do not edit.
# -------------------------------------------------------
class CoreStandardGroup(ObserviumModel):
    _modelname = "CoreStandardGroup"
    _identifiers = ("name",)
    _attributes = ("description",)
    description: str | None = None
    name: str

    local_id: str | None = None
    local_data: Any | None = None


class InfraDevice(ObserviumModel):
    _modelname = "InfraDevice"
    _identifiers = ("name",)
    _attributes = ("platform", "primary_address", "type", "description")
    type: str
    name: str
    description: str | None = None
    platform: str | None = None
    primary_address: str | None = None

    local_id: str | None = None
    local_data: Any | None = None


class IpamIPAddress(ObserviumModel):
    _modelname = "IpamIPAddress"
    _identifiers = ("address",)
    _attributes = ("description",)
    description: str | None = None
    address: str

    local_id: str | None = None
    local_data: Any | None = None
