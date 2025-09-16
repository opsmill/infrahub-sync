from __future__ import annotations

from typing import Any

from infrahub_sync.adapters.device42 import Device42Model


# -------------------------------------------------------
# AUTO-GENERATED FILE, DO NOT MODIFY
#  This file has been generated with the command `infrahub-sync generate`
#  All modifications will be lost the next time you reexecute this command
# -------------------------------------------------------
class BuiltinTag(Device42Model):
    _modelname = "BuiltinTag"
    _identifiers = ("name",)
    _attributes = ("description",)
    description: str | None = None
    name: str

    local_id: str | None = None
    local_data: Any | None = None


class ChoiceDeviceType(Device42Model):
    _modelname = "ChoiceDeviceType"
    _identifiers = ("name", "manufacturer")
    _attributes = ("part_number", "height")
    name: str
    manufacturer: str
    part_number: str | None = None
    height: int | None = None

    local_id: str | None = None
    local_data: Any | None = None


class InfraDevice(Device42Model):
    _modelname = "InfraDevice"
    _identifiers = ("name",)
    _attributes = ("serial_number", "asset_tag", "description", "model", "organization", "location", "rack")
    name: str
    serial_number: str | None = None
    asset_tag: str | None = None
    description: str | None = None
    model: str | None = None
    organization: str | None = None
    location: str | None = None
    rack: str | None = None

    local_id: str | None = None
    local_data: Any | None = None


class InfraInterfaceL2L3(Device42Model):
    _modelname = "InfraInterfaceL2L3"
    _identifiers = ("device", "name")
    _attributes = ("description",)
    device: str
    name: str
    description: str | None = None

    local_id: str | None = None
    local_data: Any | None = None


class InfraRack(Device42Model):
    _modelname = "InfraRack"
    _identifiers = ("name", "location")
    _attributes = ("height", "serial_number", "asset_tag")
    name: str
    location: str
    height: int | None = None
    serial_number: str | None = None
    asset_tag: str | None = None

    local_id: str | None = None
    local_data: Any | None = None


class LocationGeneric(Device42Model):
    _modelname = "LocationGeneric"
    _identifiers = ("name",)
    _attributes = ("description", "type")
    name: str
    description: str | None = None
    type: str | None = None

    local_id: str | None = None
    local_data: Any | None = None


class OrganizationGeneric(Device42Model):
    _modelname = "OrganizationGeneric"
    _identifiers = ("name",)
    _attributes = ("description",)
    name: str
    description: str | None = None

    local_id: str | None = None
    local_data: Any | None = None


class RoleGeneric(Device42Model):
    _modelname = "RoleGeneric"
    _identifiers = ("name",)
    _attributes = ("description",)
    name: str
    description: str | None = None

    local_id: str | None = None
    local_data: Any | None = None