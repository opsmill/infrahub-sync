"""Construct worker-local runtime instances from verified registered packages."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from infrahub_sync import SyncInstance

from .credentials import resolve_reference

if TYPE_CHECKING:
    from .models import ConfigurationPackage


def resolve_runtime_instance(package: ConfigurationPackage, *, directory: str) -> SyncInstance:
    """Resolve declared credential references without adapter ambient lookup."""

    def resolve(value: object) -> object:
        if type(value) is dict:
            mapping = cast("dict[str, object]", value)
            if set(mapping) == {"$credential"} and type(mapping["$credential"]) is str:
                return resolve_reference(package, cast("str", mapping["$credential"]))
            return {key: resolve(child) for key, child in mapping.items()}
        if type(value) is list:
            return [resolve(child) for child in cast("list[object]", value)]
        return value

    resolved = resolve(package.configuration.model_dump(mode="json", by_alias=True))
    return SyncInstance(**cast("dict[str, Any]", resolved), directory=directory)
