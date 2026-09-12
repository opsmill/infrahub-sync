from __future__ import annotations

from infrahub_sync.plugin_loader import PluginLoader

from .sync_models import (
    BuiltinTag,
    LocationSite,
    OrganizationTenant,
)

# Load adapter class dynamically at runtime

_AdapterBaseClass = PluginLoader().resolve("infrahub")


# -------------------------------------------------------
# Generated file - do not edit.
# -------------------------------------------------------
class InfrahubSync(_AdapterBaseClass):
    BuiltinTag = BuiltinTag
    LocationSite = LocationSite
    OrganizationTenant = OrganizationTenant
