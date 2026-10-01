"""Construct worker-local runtime instances from verified registered packages."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from infrahub_sync import SyncInstance

from .credentials import _REGISTERED_CONTEXT, resolve_reference
from .storage import UnsupportedSyncStoreError

if TYPE_CHECKING:
    from collections.abc import Mapping

    from .models import ConfigurationPackage


def effective_destination_branch(settings: Mapping[str, Any] | None, run_branch: str | None) -> str:
    """Resolve the one destination branch a run works against.

    Declared ``destination.settings.branch`` first, then the run request's branch, then
    ``"main"`` — the SDK's own default. Schema discovery, destination adapter
    construction, and the plan's destination binding all resolve through this, so a run
    cannot read one branch's schema and write another's. Explicit configuration
    validation has no run request and passes ``None``.
    """
    declared = (settings or {}).get("branch")
    if isinstance(declared, str) and declared:
        return declared
    return run_branch or "main"


def resolve_runtime_instance(
    package: ConfigurationPackage, *, directory: str, resolve_source_credentials: bool = True
) -> SyncInstance:
    """Resolve declared credential references without adapter ambient lookup.

    ``resolve_source_credentials=False`` leaves the declared ``source`` block exactly as
    the package declares it, references included. A saved-plan apply constructs the
    destination only, so resolving the source's references would make the apply host hold
    a source credential it never uses. Every source-using path — plan, sync, and
    configuration validation — keeps the default and resolves both sides.
    """

    if package.configuration.store is not None:
        raise UnsupportedSyncStoreError

    def resolve(value: object) -> object:
        if type(value) is dict:  # pylint: disable=unidiomatic-typecheck
            mapping = cast("dict[str, object]", value)
            if set(mapping) == {"$credential"} and type(mapping["$credential"]) is str:  # pylint: disable=unidiomatic-typecheck
                return resolve_reference(package, mapping["$credential"])
            return {key: resolve(child) for key, child in mapping.items()}
        if type(value) is list:  # pylint: disable=unidiomatic-typecheck
            return [resolve(child) for child in cast("list[object]", value)]
        return value

    declared = cast("dict[str, object]", package.configuration.model_dump(mode="json", by_alias=True))
    runtime: dict[str, object] = {
        key: value if key == "source" and not resolve_source_credentials else resolve(value)
        for key, value in declared.items()
    }
    for side in ("source", "destination"):
        adapter = runtime[side]
        if type(adapter) is dict:  # pylint: disable=unidiomatic-typecheck
            adapter_mapping = cast("dict[str, object]", adapter)
            settings = adapter_mapping.get("settings")
            if settings is None:
                # A declared `settings: null` means no settings, not "unregistered": left
                # unmarked, the adapter would fill its address and credentials from the
                # worker environment instead of refusing the missing declaration.
                settings = adapter_mapping["settings"] = {}
            if type(settings) is not dict:  # pylint: disable=unidiomatic-typecheck
                msg = f"registered {side} adapter settings must be a mapping"
                raise TypeError(msg)
            cast("dict[str, object]", settings)[_REGISTERED_CONTEXT] = True
    runtime["directory"] = directory
    return SyncInstance.model_validate(runtime)
