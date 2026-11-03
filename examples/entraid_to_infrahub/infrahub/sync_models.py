from __future__ import annotations

from typing import Any, List

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
# AUTO-GENERATED FILE, DO NOT MODIFY
#  This file has been generated with the command `infrahub-sync generate`
#  All modifications will be lost the next time you reexecute this command
# -------------------------------------------------------
class IdentityEmployee(_ModelBaseClass):
    _modelname = "IdentityEmployee"
    _identifiers = ("entra_id",)
    _attributes = ("name", "display_name", "email", "enabled")
    entra_id: str
    name: str
    display_name: str | None = None
    email: str | None = None
    enabled: bool | None = None

    local_id: str | None = None
    local_data: Any | None = None


class IdentityServiceAccount(_ModelBaseClass):
    _modelname = "IdentityServiceAccount"
    _identifiers = ("entra_id",)
    _attributes = ("name", "display_name", "email", "enabled")
    entra_id: str
    name: str
    display_name: str | None = None
    email: str | None = None
    enabled: bool | None = None

    local_id: str | None = None
    local_data: Any | None = None


class IdentityGroup(_ModelBaseClass):
    _modelname = "IdentityGroup"
    _identifiers = ("entra_id",)
    _attributes = ("members", "name", "description")
    entra_id: str
    name: str
    description: str | None = None
    members: list[str] | None = []

    local_id: str | None = None
    local_data: Any | None = None
