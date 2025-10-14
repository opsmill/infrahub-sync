from __future__ import annotations

from infrahub_sync.plugin_loader import PluginLoader

from .sync_models import (
    DcimPhysicalDevice,
    DcimPhysicalInterface,
    LocationBuilding,
    LocationMetro,
    OrganizationCustomer,
)

# Load adapter class dynamically at runtime

_AdapterBaseClass = PluginLoader().resolve("aci")


# -------------------------------------------------------
# AUTO-GENERATED FILE, DO NOT MODIFY
#  This file has been generated with the command `infrahub-sync generate`
#  All modifications will be lost the next time you reexecute this command
# -------------------------------------------------------
class AciSync(_AdapterBaseClass):
    DcimPhysicalDevice = DcimPhysicalDevice
    DcimPhysicalInterface = DcimPhysicalInterface
    LocationBuilding = LocationBuilding
    LocationMetro = LocationMetro
    OrganizationCustomer = OrganizationCustomer
