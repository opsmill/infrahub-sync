"""The destination planned-write surface, expressed as a type — see `dev/adr/0002`.

`PlannedWriteDestination` is what a destination has to offer for a saved plan to be applied
through it: the per-operation write surface, and the factory that builds the per-apply peer
resolver it is handed. The factory is a member because the engine builds the resolver
without naming a concrete adapter.

**The check is presence-only.** `isinstance` against a `runtime_checkable` Protocol verifies
member presence and never signatures, so against a duck-typed destination it is exactly as
strong as a `hasattr` gate. FR-023's refusal is presence-checking and this type does not
harden it; what it fixes is the static boundary, where `ty` verifies every call site.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from infrahub_sync.plan.last_changes import LastChangeCollector
from infrahub_sync.plan.models import PlanLineageRecord

if TYPE_CHECKING:
    from infrahub_sync.adapters.infrahub import PeerResolver
    from infrahub_sync.plan.last_changes import LastChange
    from infrahub_sync.plan.models import PlanManifest, PlannedOperation


@runtime_checkable
class PlannedWriteDestination(Protocol):
    """A destination a saved plan can be applied through (FR-013, FR-014, FR-023, AD086).

    A destination that is not one of these is refused in the pre-write gate and named.
    The operator can review a diff or choose a destination with planned-write support.
    See the module docstring for what that refusal does and does not verify.
    """

    def new_peer_resolver(self) -> PeerResolver:
        """Build the peer resolver for one apply (FR-014).

        One resolver per apply, created at its start and discarded with it. The factory
        lives on the destination because only the destination knows what the resolver needs
        to query it with.
        """

    def apply_planned_operation(self, *, operation: PlannedOperation, peers: PeerResolver) -> str:
        """Execute one planned operation convergently, returning the destination node id (FR-013)."""


@runtime_checkable
class LineageResolvingDestination(Protocol):
    """A destination that resolves the lineage it writes, before a plan reads and before an apply writes.

    Only the Infrahub destination is one; every other destination records no lineage. Like
    `PlannedWriteDestination`, the `isinstance` check is presence-only, so each value read
    through it is still checked before use. The protocol declares `resolve_lineage` alone:
    that call is what enforces the import-account comparison and the named-node checks
    (FR-012), so it must not depend on any other member being present. Keeping the lineage
    record for the manifest (`LineageRecordingDestination`) and reading last-change metadata
    (`MetadataReadingDestination`) are separate capabilities.
    """

    def resolve_lineage(self, *, saved_plan: PlanManifest | None = None) -> object:
        """Resolve and keep the lineage this run writes; raise to stop the run.

        An apply passes the saved plan's manifest, so the destination can compare the
        lineage the plan recorded with the lineage it resolves now. A plan passes none.
        """


@runtime_checkable
class LineageRecordingDestination(Protocol):
    """A destination that keeps the lineage a plan records in its manifest.

    Only the Infrahub destination is one. The `isinstance` check is presence-only, like
    `LineageResolvingDestination`'s, so the value is still checked before use.
    """

    @property
    def lineage_record(self) -> PlanLineageRecord | None:
        """The lineage a plan records in its manifest, or `None` before `resolve_lineage` runs."""


@runtime_checkable
class MetadataReadingDestination(Protocol):
    """A destination that reads who last changed each value, for the plan's last-change file (FR-007).

    Only the Infrahub destination is one; every other destination records no last changes.
    The `isinstance` check is presence-only, like `LineageResolvingDestination`'s.
    """

    @property
    def last_changes(self) -> Mapping[tuple[str, str], LastChange]:
        """Who last changed each value this plan read, keyed by (destination node id, field name)."""

    @property
    def metadata_read(self) -> bool:
        """Whether this plan's reads asked for `updated_by` and `updated_at`."""

    def resolve_metadata_read(self) -> None:
        """Decide, before either side of a plan is loaded, whether the destination's reads ask for metadata."""


def resolve_destination_lineage(destination: object, *, saved_plan: PlanManifest | None = None) -> None:
    """Resolve the destination's lineage when the destination writes lineage; otherwise do nothing.

    The engine calls this before either side of a plan is loaded and before the first write
    of an apply, so a lineage refusal stops the run before the source system is read and
    before anything is written. An apply passes `saved_plan`; a plan does not.
    """
    if not isinstance(destination, LineageResolvingDestination):
        return
    destination.resolve_lineage(saved_plan=saved_plan)


def resolve_destination_metadata_read(destination: object) -> None:
    """Let the destination decide whether its reads ask for metadata; otherwise do nothing.

    The order of this call is stated at `Potenda.load_both_sides`.
    """
    if not isinstance(destination, MetadataReadingDestination):
        return
    destination.resolve_metadata_read()


def destination_lineage_record(destination: object) -> PlanLineageRecord | None:
    """The lineage the destination resolved for this plan, for the manifest's `lineage` field.

    `None` when the destination records no lineage, so the plan is written without the field.
    """
    if not isinstance(destination, LineageRecordingDestination):
        return None
    record = destination.lineage_record
    return record if isinstance(record, PlanLineageRecord) else None


def destination_last_changes(destination: object) -> LastChangeCollector | None:
    """A collector over the last changes the destination read for this plan (FR-007).

    `None` when the destination keeps no last changes (it is not Infrahub), so the plan is
    written without `plan/last_changes.jsonl` and without the manifest's `last_changes` field.
    """
    if not isinstance(destination, MetadataReadingDestination):
        return None
    changes = destination.last_changes
    if not isinstance(changes, Mapping):
        return None
    return LastChangeCollector(changes, metadata_read=destination.metadata_read is True)
