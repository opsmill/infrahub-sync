from __future__ import annotations

from typing import Any, List

from infrahub_sync.plugin_loader import PluginLoader
# Load model class dynamically at runtime (honor adapters_path, safe fallback)
try:

    _loader = PluginLoader.from_env_and_args()


    _spec = "ciscosdwan"

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
class SdwanConfigurationGroup(_ModelBaseClass):
    _modelname = "SdwanConfigurationGroup"
    _identifiers = ("name",)
    _attributes = ("instance", "transport_profile", "description", "cli_profile", "policy_object_profile", "system_profile", "service_profile")
    transport_profile: str
    description: str | None = None
    cli_profile: str | None = None
    policy_object_profile: str | None = None
    system_profile: str
    name: str
    service_profile: str | None = None
    instance: str

    local_id: str | None = None
    local_data: Any | None = None

class SdwanInstance(_ModelBaseClass):
    _modelname = "SdwanInstance"
    _identifiers = ("name",)
    _attributes = ("manager_version",)
    manager_version: str | None = None
    name: str

    local_id: str | None = None
    local_data: Any | None = None

class SdwanRouter(_ModelBaseClass):
    _modelname = "SdwanRouter"
    _identifiers = ("chassis_id",)
    _attributes = ("site", "configuration_group", "model")
    configuration_group: str | None = None
    model: str | None = None
    chassis_id: str
    site: str

    local_id: str | None = None
    local_data: Any | None = None

class SdwanSite(_ModelBaseClass):
    _modelname = "SdwanSite"
    _identifiers = ("id_value",)
    _attributes = ("instance",)
    id_value: int
    instance: str

    local_id: str | None = None
    local_data: Any | None = None
