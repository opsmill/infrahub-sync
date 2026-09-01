from __future__ import annotations

from typing import Any

from infrahub_sync.plugin_loader import PluginLoader

# Load model class dynamically at runtime (honor adapters_path, safe fallback)
try:
    _loader = PluginLoader.from_env_and_args(adapter_paths=[])

    _spec = "infrahub"

    _ModelBaseClass = _loader.resolve(_spec, default_class_candidates=("Model",))
except Exception:
    # Fallback: use DiffSyncModel to avoid import-time failure
    from diffsync import DiffSyncModel as _FallbackModel

    _ModelBaseClass = _FallbackModel


# -------------------------------------------------------
# Generated file - do not edit.
# -------------------------------------------------------
class CoreStandardGroup(_ModelBaseClass):
    _modelname = "CoreStandardGroup"
    _identifiers = ("name",)
    _attributes = ("description",)
    name: str
    description: str | None = None

    local_id: str | None = None
    local_data: Any | None = None


class InfraDevice(_ModelBaseClass):
    _modelname = "InfraDevice"
    _identifiers = ("name",)
    _attributes = ("site", "type")
    type: str
    name: str
    site: str | None = None

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


class LocationSite(_ModelBaseClass):
    _modelname = "LocationSite"
    _identifiers = ("name",)
    _attributes = ("description",)
    name: str
    description: str | None = None

    local_id: str | None = None
    local_data: Any | None = None
