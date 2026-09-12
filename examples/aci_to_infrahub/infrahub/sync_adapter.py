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

_AdapterBaseClass = PluginLoader().resolve("infrahub")


# -------------------------------------------------------
# Generated file - do not edit.
# -------------------------------------------------------
class InfrahubSync(_AdapterBaseClass):
    DcimPhysicalDevice = DcimPhysicalDevice
    DcimPhysicalInterface = DcimPhysicalInterface
    LocationBuilding = LocationBuilding
    LocationMetro = LocationMetro
    OrganizationCustomer = OrganizationCustomer
