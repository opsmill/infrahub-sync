"""Registered composition's runtime model plan: one schema read, one set of classes.

A registered run reads the destination schema once, through the destination adapter's
own declared capability, and derives everything it needs from that one immutable value:
both sides' model classes and the plan's schema fingerprint. There is no second read to
disagree with the first, and no generated Python on the path.

The admitted profile is an Infrahub destination — it owns both V3 seams, schema
discovery and saved-plan writes. A package outside it refuses here, before any schema
I/O. An installed non-bundled source paired with an Infrahub destination may execute; it
is admitted, not qualified.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from infrahub_sync.configuration.capabilities import (
    DestinationSchemaReadError,
    get_adapter_capabilities,
)
from infrahub_sync.configuration.capabilities import (
    UnknownAdapterCapabilitiesError as _UnknownAdapterCapabilitiesError,
)
from infrahub_sync.configuration.runtime import effective_destination_branch
from infrahub_sync.plugin_loader import resolve_installed_adapter_class, resolve_installed_model_base

from .domain import normalize_destination_schema
from .errors import (
    DestinationSchemaUnavailableError,
    MissingMappedKindError,
    UnsupportedDestinationProfileError,
)
from .models import build_runtime_models
from .projection import compute_consumed_schema_fingerprint

if TYPE_CHECKING:
    from collections.abc import Mapping

    from diffsync import DiffSyncModel

    from infrahub_sync import SyncConfig, SyncInstance
    from infrahub_sync.configuration.models import ConfigurationPackage


@dataclass(frozen=True, slots=True)
class RuntimeModelPlan:
    """One run's resolved adapter classes, model classes, and schema identity."""

    branch: str
    schema_fingerprint: str
    source_adapter_class: type[Any]
    source_models: Mapping[str, type[DiffSyncModel]]
    destination_adapter_class: type[Any]
    destination_models: Mapping[str, type[DiffSyncModel]]


def read_destination_schema_snapshot(package: ConfigurationPackage, branch: str) -> Mapping[str, Any]:
    """Read one destination schema snapshot through the declared capability accessor.

    The worker's single schema read, and the only place a registered run performs schema
    I/O. Tests replace this module attribute to inject a snapshot.

    Raises:
        UnsupportedDestinationProfileError: the destination declares no schema accessor.
        DestinationSchemaReadError: the accessor's own typed read failure.
    """
    accessor = _require_admitted_destination(package.configuration).destination_schema_accessor
    if accessor is None:  # pragma: no cover - the admission check already refused this
        msg = "destination declares no schema accessor"
        raise UnsupportedDestinationProfileError(msg)
    return accessor(package, branch)


def _require_admitted_destination(configuration: SyncConfig) -> Any:
    """Return the destination's declaration, refusing a destination outside the profile."""
    name = configuration.destination.name
    try:
        capabilities = get_adapter_capabilities(name)
    except _UnknownAdapterCapabilitiesError:
        msg = (
            f"destination adapter {name!r} is outside the supported runtime-model profile: "
            "it has no configuration capability declaration"
        )
        raise UnsupportedDestinationProfileError(msg) from None
    if not capabilities.destination_schema_validation:
        msg = (
            f"destination adapter {name!r} is outside the supported runtime-model profile: "
            "it does not declare destination schema discovery"
        )
        raise UnsupportedDestinationProfileError(msg)
    return capabilities


def _require_mapped_kinds(configuration: SyncConfig, snapshot_kinds: Mapping[str, Any]) -> None:
    """Refuse a configuration that maps a kind the destination schema does not declare."""
    missing = sorted({mapping.name for mapping in configuration.schema_mapping} - set(snapshot_kinds))
    if missing:
        msg = f"destination schema declares none of the mapped kinds {missing!r}"
        raise MissingMappedKindError(msg)


def build_runtime_model_plan(
    *,
    package: ConfigurationPackage,
    instance: SyncInstance,
    run_branch: str | None,
) -> RuntimeModelPlan:
    """Build one registered run's adapter classes, model classes, and schema fingerprint.

    Raises:
        UnsupportedDestinationProfileError: the destination is outside the admitted
            profile; raised before any schema I/O.
        DestinationSchemaUnavailableError: the declared accessor could not deliver a
            snapshot. Carries the accessor's short reason and nothing else from the read.
        UnsupportedSchemaSemanticsError: the snapshot, or a mapped attribute's kind, is
            outside the closed schema domain.
        MissingMappedKindError: the schema declares no kind for a configured mapping.
        PluginLoadError: an installed adapter class or model base does not resolve.
    """
    _require_admitted_destination(instance)
    branch = effective_destination_branch(instance.destination.settings, run_branch)
    try:
        raw = read_destination_schema_snapshot(package, branch)
    except DestinationSchemaReadError as exc:
        msg = f"destination schema for branch {branch!r} could not be read: {exc.reason}"
        raise DestinationSchemaUnavailableError(msg, reason=exc.reason) from None
    snapshot = normalize_destination_schema(raw)
    _require_mapped_kinds(instance, snapshot.kinds)
    return RuntimeModelPlan(
        branch=branch,
        schema_fingerprint=compute_consumed_schema_fingerprint(configuration=instance, snapshot=snapshot),
        source_adapter_class=resolve_installed_adapter_class(instance, instance.source),
        source_models=build_runtime_models(
            snapshot=snapshot,
            configuration=instance,
            model_base=resolve_installed_model_base(instance, instance.source),
        ),
        destination_adapter_class=resolve_installed_adapter_class(instance, instance.destination),
        destination_models=build_runtime_models(
            snapshot=snapshot,
            configuration=instance,
            model_base=resolve_installed_model_base(instance, instance.destination),
        ),
    )


def bind_runtime_models(adapter: object, models: Mapping[str, type[DiffSyncModel]]) -> None:
    """Bind one side's model classes onto that run's adapter instance.

    Instance attributes, exactly where the generated ``<Name>Sync`` class declared them,
    so no module, file, process-global registry, or adapter base class is touched and a
    second run cannot reach these classes.
    """
    for kind, model in models.items():
        setattr(adapter, kind, model)
