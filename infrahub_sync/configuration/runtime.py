"""Construct worker-local runtime instances from verified registered packages."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from infrahub_sync import SyncInstance

from .capabilities import UnknownCredentialMethodError, credential_field_map
from .credentials import _REGISTERED_CONTEXT, CredentialConfigurationError, resolve_reference
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


def _fill_released_values(
    side: str, adapter: dict[str, object], settings: dict[str, object], values: Mapping[str, str] | None
) -> None:
    """Write the released values of the block's stored credential into its settings.

    The field map of the block's adapter and ``auth_method`` says which stored field fills
    which setting. Only those settings are written.

    Raises:
        CredentialConfigurationError: no values were released for the block, a field the block
            needs is not among them, or the block's ``auth_method`` is not one its adapter
            accepts. The message names the block and the fields, never a value.
    """
    if values is None:
        msg = f"the {side} block names a stored credential, and no value was released for it"
        raise CredentialConfigurationError(msg)
    try:
        field_map = credential_field_map(str(adapter.get("name")), settings)
    except UnknownCredentialMethodError:
        msg = f"the {side} block names a stored credential, and its auth_method is not one its adapter accepts"
        raise CredentialConfigurationError(msg) from None
    missing = sorted(field for field in field_map.fields if not values.get(field))
    if missing:
        msg = f"the values released for the {side} block do not hold the fields {', '.join(missing)}"
        raise CredentialConfigurationError(msg)
    for setting, stored_field in field_map.settings.items():
        settings[setting] = values[stored_field]


def resolve_runtime_instance(
    package: ConfigurationPackage,
    *,
    directory: str,
    resolve_source_credentials: bool = True,
    released: Mapping[str, Mapping[str, str]] | None = None,
) -> SyncInstance:
    """Resolve the credentials of a registered package without adapter ambient lookup.

    A block that names a stored credential (``credential: <name>``) takes its values from
    ``released``: for each block, the fields that the Sync API released to the run. The field
    map of the block's adapter writes them into the block settings. A block with
    ``$credential`` references resolves them as before.

    ``resolve_source_credentials=False`` leaves the declared ``source`` settings exactly as
    the package declares them, references included, and fills nothing from ``released``. A
    saved-plan apply constructs the destination only, so resolving the source's credential
    would make the apply host hold a source credential it never uses. Every source-using
    path — plan, sync, and configuration validation — keeps the default and resolves both
    sides.

    Raises:
        CredentialConfigurationError: a resolved block names a stored credential and
            ``released`` does not hold its values; or a reference does not resolve.
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
            # The runtime model has no `credential` key: the name belongs to the package.
            named = adapter_mapping.pop("credential", None)
            settings = adapter_mapping.get("settings")
            if settings is None:
                # A declared `settings: null` means no settings, not "unregistered": left
                # unmarked, the adapter would fill its address and credentials from the
                # worker environment instead of refusing the missing declaration.
                settings = adapter_mapping["settings"] = {}
            if type(settings) is not dict:  # pylint: disable=unidiomatic-typecheck
                msg = f"registered {side} adapter settings must be a mapping"
                raise TypeError(msg)
            if named is not None and (side != "source" or resolve_source_credentials):
                _fill_released_values(
                    side, adapter_mapping, cast("dict[str, object]", settings), (released or {}).get(side)
                )
            cast("dict[str, object]", settings)[_REGISTERED_CONTEXT] = True
    runtime["directory"] = directory
    return SyncInstance.model_validate(runtime)
