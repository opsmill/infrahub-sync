from __future__ import annotations

from typing import Any

from infrahub_sync.plugin_loader import PluginLoader

# Load model class dynamically at runtime (honor adapters_path, safe fallback)
try:
    _loader = PluginLoader.from_env_and_args(adapter_paths=[])
    _spec = "infrahub"
    _ModelBaseClass = _loader.resolve(_spec, default_class_candidates=("Model",))
except Exception:  # noqa: BLE001 -- generated adapters need a safe import fallback
    # Fallback: use DiffSyncModel to avoid import-time failure
    from diffsync import DiffSyncModel as _FallbackModel

    _ModelBaseClass = _FallbackModel


# -------------------------------------------------------
# Generated file - do not edit.
# -------------------------------------------------------
class _GeneratedModelBase(_ModelBaseClass):
    if "local_id" not in getattr(_ModelBaseClass, "model_fields", {}):
        local_id: str | None = None
    if "local_data" not in getattr(_ModelBaseClass, "model_fields", {}):
        local_data: Any | None = None


class InfraDevice(_GeneratedModelBase):
    _modelname = "InfraDevice"
    _identifiers = ("name",)
    _attributes = ("type",)
    name: str
    type: str
