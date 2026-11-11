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

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Literal

from infrahub_sync.configuration.capabilities import (
    DestinationSchemaReadError,
    get_adapter_capabilities,
)
from infrahub_sync.configuration.capabilities import (
    UnknownAdapterCapabilitiesError as _UnknownAdapterCapabilitiesError,
)
from infrahub_sync.configuration.runtime import effective_destination_branch
from infrahub_sync.plan.keying import writable_convergence_reason
from infrahub_sync.plugin_loader import resolve_installed_adapter_class, resolve_installed_model_base

from .domain import normalize_destination_schema
from .errors import (
    DestinationBranchNotFoundError,
    DestinationSchemaUnavailableError,
    MissingMappedKindError,
    UnsupportedDestinationProfileError,
    UnwritableConvergenceIdentityError,
)
from .models import build_runtime_models
from .projection import compute_consumed_schema_fingerprint

if TYPE_CHECKING:
    from collections.abc import Mapping

    from diffsync import DiffSyncModel

    from infrahub_sync import SyncAdapter, SyncConfig, SyncInstance
    from infrahub_sync.configuration.models import ConfigurationPackage

    from .domain import DestinationSchemaSnapshot


# What one stage's runtime preparation must produce. A saved-plan apply constructs the
# destination only, so building — and therefore resolving — the source would reintroduce
# the source dependency a no-source apply exists to avoid.
RuntimeModelScope = Literal["destination", "both"]
STAGE_RUNTIME_MODEL_SCOPE: Mapping[str, RuntimeModelScope | None] = MappingProxyType(
    {"plan": "both", "sync": "both", "apply": "destination", "verify": None}
)


@dataclass(frozen=True, slots=True)
class RuntimeSideModels:
    """One side's resolved adapter class and its fresh model classes."""

    adapter_class: type[Any]
    models: Mapping[str, type[DiffSyncModel]]


@dataclass(frozen=True, slots=True)
class RuntimeModelPlan:
    """One run's resolved sides and the schema identity they were built from.

    ``source`` is ``None`` on a destination-only plan. Both sides, when present, come
    from the one snapshot this plan's ``schema_fingerprint`` was computed over.
    """

    branch: str
    schema_fingerprint: str
    destination: RuntimeSideModels
    source: RuntimeSideModels | None
    generic_peers: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    # The choices of each mapped Dropdown attribute, by kind and attribute name, from the same
    # snapshot (spec 019 research R4). The plan checks each mapped value against them.
    dropdown_choices: Mapping[str, Mapping[str, tuple[str, ...]]] = field(default_factory=dict)


def mapped_dropdown_choices(
    snapshot: DestinationSchemaSnapshot, configuration: SyncConfig
) -> dict[str, dict[str, tuple[str, ...]]]:
    """Return the choices of every Dropdown attribute the configuration maps, by kind and attribute."""
    found: dict[str, dict[str, tuple[str, ...]]] = {}
    for mapping in configuration.schema_mapping:
        node = snapshot.kinds.get(mapping.name)
        if node is None:
            continue
        mapped = {field.name for field in mapping.fields or ()}
        for attribute in node.attributes:
            if attribute.choices is not None and attribute.name in mapped:
                found.setdefault(mapping.name, {})[attribute.name] = attribute.choices
    return found


def planned_dropdown_choices(configuration: Any) -> Mapping[str, Mapping[str, tuple[str, ...]]] | None:
    """The Dropdown choices of a run's runtime model plan, or `None` when the run has none.

    A run without a runtime model plan (a direct Prefect flow run) has no destination schema
    snapshot, so its plan cannot check Dropdown values (spec 019 research R4).
    """
    plan = getattr(configuration, "_runtime_models", None)
    if not isinstance(plan, RuntimeModelPlan):
        return None
    return plan.dropdown_choices


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
    scope: RuntimeModelScope,
) -> RuntimeModelPlan:
    """Build one registered run's adapter classes, model classes, and schema fingerprint.

    ``scope`` is what the stage will construct: ``"both"`` for plan and sync, and
    ``"destination"`` for a saved-plan apply, which builds and resolves nothing for the
    source. Whatever the scope covers comes from one schema read.

    Raises:
        UnsupportedDestinationProfileError: the destination is outside the admitted
            profile; raised before any schema I/O.
        DestinationBranchNotFoundError: the destination branch does not exist in Infrahub
            (accessor reason "nobranch").
        DestinationSchemaUnavailableError: the declared accessor could not deliver a
            snapshot. Carries the accessor's short reason and, for a refused token or a
            missing permission, the cause Infrahub states.
        UnsupportedSchemaSemanticsError: the snapshot, or a mapped attribute's kind, is
            outside the closed schema domain.
        MissingMappedKindError: a plan or sync schema declares no kind for a configured
            mapping. Apply preserves that absence in its live fingerprint so the retained
            plan guard can classify it as schema drift.
        PluginLoadError: an installed adapter class or model base does not resolve.
    """
    _require_admitted_destination(instance)
    branch = effective_destination_branch(instance.destination.settings, run_branch)
    try:
        raw = read_destination_schema_snapshot(package, branch)
    except DestinationSchemaReadError as exc:
        if exc.reason == "nobranch":
            raise DestinationBranchNotFoundError(branch=branch) from None
        raise DestinationSchemaUnavailableError(reason=exc.reason, branch=branch, detail=exc.detail) from None
    snapshot = normalize_destination_schema(raw)
    schema_fingerprint = compute_consumed_schema_fingerprint(configuration=instance, snapshot=snapshot)
    if scope == "both":
        _require_mapped_kinds(instance, snapshot.kinds)
        references = {
            mapping.name: {field.name: field.reference for field in mapping.fields or () if field.reference is not None}
            for mapping in instance.schema_mapping
        }
        for mapping in instance.schema_mapping:
            node = snapshot.kinds[mapping.name]
            mapped_fields = {field.name for field in mapping.fields or ()}
            reason = writable_convergence_reason(
                node=node,
                identity_fields=mapped_fields,
                mapped_fields=mapped_fields,
                schemas=snapshot.kinds,
                references=references,
            )
            if reason:
                msg = f"destination kind {mapping.name!r}: {reason}"
                raise UnwritableConvergenceIdentityError(msg)

    def side(adapter: SyncAdapter) -> RuntimeSideModels:
        return RuntimeSideModels(
            adapter_class=resolve_installed_adapter_class(adapter),
            models=build_runtime_models(
                snapshot=snapshot,
                configuration=instance,
                model_base=resolve_installed_model_base(adapter),
            ),
        )

    return RuntimeModelPlan(
        branch=branch,
        schema_fingerprint=schema_fingerprint,
        destination=side(instance.destination),
        source=side(instance.source) if scope == "both" else None,
        generic_peers=snapshot.generic_peers,
        dropdown_choices=mapped_dropdown_choices(snapshot, instance),
    )


def bind_runtime_models(adapter: object, models: Mapping[str, type[DiffSyncModel]]) -> None:
    """Bind one side's model classes onto that run's adapter instance.

    Instance attributes, exactly where the generated ``<Name>Sync`` class declared them,
    so no module, file, process-global registry, or adapter base class is touched and a
    second run cannot reach these classes.
    """
    for kind, model in models.items():
        setattr(adapter, kind, model)
