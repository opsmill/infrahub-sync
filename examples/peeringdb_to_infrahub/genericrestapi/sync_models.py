from __future__ import annotations

from typing import Any

from infrahub_sync.plugin_loader import PluginLoader

# Load model class dynamically at runtime (honor adapters_path, safe fallback)
try:
    _loader = PluginLoader.from_env_and_args(adapter_paths=[])

    _spec = "genericrestapi"

    _ModelBaseClass = _loader.resolve(_spec, default_class_candidates=("Model",))
except Exception:
    # Fallback: use DiffSyncModel to avoid import-time failure
    from diffsync import DiffSyncModel as _FallbackModel

    _ModelBaseClass = _FallbackModel


# -------------------------------------------------------
# Generated file - do not edit.
# -------------------------------------------------------
class InfraAutonomousSystem(_ModelBaseClass):
    _modelname = "InfraAutonomousSystem"
    _identifiers = ("asn",)
    _attributes = ("ipv4_max_prefixes", "ipv6_max_prefixes", "name")
    ipv4_max_prefixes: int | None = None
    ipv6_max_prefixes: int | None = None
    name: str
    asn: int

    local_id: str | None = None
    local_data: Any | None = None
