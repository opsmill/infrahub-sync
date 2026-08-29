"""Construct worker-local runtime instances from verified registered packages."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from infrahub_sync import SyncInstance

from .credentials import _REGISTERED_CONTEXT, resolve_reference

if TYPE_CHECKING:
    from .models import ConfigurationPackage


def resolve_runtime_instance(package: ConfigurationPackage, *, directory: str) -> SyncInstance:
    """Resolve declared credential references without adapter ambient lookup."""

    def resolve(value: object) -> object:
        if type(value) is dict:  # pylint: disable=unidiomatic-typecheck
            mapping = cast("dict[str, object]", value)
            if set(mapping) == {"$credential"} and type(mapping["$credential"]) is str:  # pylint: disable=unidiomatic-typecheck
                return resolve_reference(package, mapping["$credential"])
            return {key: resolve(child) for key, child in mapping.items()}
        if type(value) is list:  # pylint: disable=unidiomatic-typecheck
            return [resolve(child) for child in cast("list[object]", value)]
        return value

    resolved = resolve(package.configuration.model_dump(mode="json", by_alias=True))
    runtime = cast("dict[str, object]", resolved)
    for side in ("source", "destination"):
        adapter = runtime[side]
        if type(adapter) is dict:  # pylint: disable=unidiomatic-typecheck
            adapter_mapping = cast("dict[str, object]", adapter)
            settings = adapter_mapping.get("settings")
            if type(settings) is dict:  # pylint: disable=unidiomatic-typecheck
                cast("dict[str, object]", settings)[_REGISTERED_CONTEXT] = True
    runtime["directory"] = directory
    return SyncInstance.model_validate(runtime)
