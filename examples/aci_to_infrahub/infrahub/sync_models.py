from __future__ import annotations

from typing import Any

from infrahub_sync.plugin_loader import PluginLoader

# Load model class dynamically at runtime (honor adapters_path, safe fallback)
try:
    _loader = PluginLoader.from_env_and_args()

    _spec = "infrahub"

    _ModelBaseClass = _loader.resolve(_spec, default_class_candidates=("Model",))
except Exception:
    # Fallback: use DiffSyncModel to avoid import-time failure
    from diffsync import DiffSyncModel as _FallbackModel

    _ModelBaseClass = _FallbackModel


# -------------------------------------------------------
# Generated file - do not edit.
# -------------------------------------------------------
class DcimPhysicalDevice(_ModelBaseClass):
    _modelname = "DcimPhysicalDevice"
    _identifiers = ("name",)
    _attributes = ("location", "serial", "role", "description", "status")
    serial: str | None = None
    name: str
    role: str | None = None
    description: str | None = None
    status: str
    location: str

    local_id: str | None = None
    local_data: Any | None = None


class DcimPhysicalInterface(_ModelBaseClass):
    _modelname = "DcimPhysicalInterface"
    _identifiers = ("device", "name")
    _attributes = ("description",)
    name: str
    description: str | None = None
    device: str

    local_id: str | None = None
    local_data: Any | None = None


class LocationBuilding(_ModelBaseClass):
    _modelname = "LocationBuilding"
    _identifiers = ("name",)
    _attributes = ("parent", "shortname")
    name: str
    shortname: str
    parent: str

    local_id: str | None = None
    local_data: Any | None = None


class LocationMetro(_ModelBaseClass):
    _modelname = "LocationMetro"
    _identifiers = ("name",)
    _attributes = ("shortname",)
    name: str
    shortname: str

    local_id: str | None = None
    local_data: Any | None = None


class OrganizationCustomer(_ModelBaseClass):
    _modelname = "OrganizationCustomer"
    _identifiers = ("name", "customer_id")
    _attributes = ()
    customer_id: str | None = None
    name: str

    local_id: str | None = None
    local_data: Any | None = None
