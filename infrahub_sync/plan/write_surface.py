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

if TYPE_CHECKING:
    from infrahub_sync.adapters.infrahub import PeerResolver
    from infrahub_sync.plan.models import PlannedOperation


@runtime_checkable
class PlannedWriteDestination(Protocol):
    """A destination a saved plan can be applied through (FR-013, FR-014, FR-023, AD086).

    A destination that is not one of these is refused in the pre-write gate, named, and
    directed at `sync` — see the module docstring for what that refusal does and does not
    verify.
    """

    def new_peer_resolver(self) -> PeerResolver:
        """Build the peer resolver for one apply (FR-014).

        One resolver per apply, created at its start and discarded with it. The factory
        lives on the destination because only the destination knows what the resolver needs
        to query it with.
        """

    def apply_planned_operation(self, *, operation: PlannedOperation, peers: PeerResolver) -> str:
        """Execute one planned operation convergently, returning the destination node id (FR-013)."""
