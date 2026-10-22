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

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from infrahub_sync.plan.models import PlanLineageRecord

if TYPE_CHECKING:
    from infrahub_sync.adapters.infrahub import PeerResolver
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
    """A destination that resolves the lineage it writes, before a plan reads and before an apply writes."""

    def resolve_lineage(self, *, saved_plan: PlanManifest | None = None) -> object:
        """Resolve and keep the lineage this run writes; raise to stop the run.

        An apply passes the saved plan's manifest, so the destination can compare the
        lineage the plan recorded with the lineage it resolves now. A plan passes none.
        """


def resolve_destination_lineage(destination: object, *, saved_plan: PlanManifest | None = None) -> None:
    """Resolve the destination's lineage when the destination writes lineage; otherwise do nothing.

    The engine calls this before either side of a plan is loaded and before the first write
    of an apply, so a lineage refusal stops the run before the source system is read and
    before anything is written. An apply passes `saved_plan`; a plan does not, so the
    plan-time call is unchanged for destinations that take no argument.
    """
    if not isinstance(destination, LineageResolvingDestination):
        return
    if saved_plan is None:
        destination.resolve_lineage()
    else:
        destination.resolve_lineage(saved_plan=saved_plan)


def destination_lineage_record(destination: object) -> PlanLineageRecord | None:
    """The lineage the destination resolved for this plan, for the manifest's `lineage` field.

    `None` when the destination records no lineage, so the plan is written without the field.
    """
    record = getattr(destination, "lineage_record", None)
    return record if isinstance(record, PlanLineageRecord) else None
