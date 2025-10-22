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
    description: str | None = None
    name: str

    local_id: str | None = None
    local_data: Any | None = None
