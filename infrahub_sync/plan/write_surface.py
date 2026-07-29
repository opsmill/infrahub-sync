"""The destination planned-write surface, expressed as a type (AD086).

`PlannedWriteDestination` is what a destination has to offer for a saved plan to be applied
through it, and it has exactly two members: the write surface a saved-plan apply calls per
operation, and the factory that builds the per-apply peer resolver that surface is handed.
The factory is a member of the surface because the engine has to build the resolver without
knowing the concrete adapter — that is what previously forced a cast to `InfrahubAdapter` in
`Potenda.apply_plan`.

**What this type enforces, and what it does not.** `runtime_checkable` makes `isinstance`
legal against it, and an `isinstance` check against a Protocol verifies **member presence
only, never signatures**. Against a duck-typed destination it is therefore **equivalent to
the `hasattr` gate it replaced** — no stronger. FR-023's refusal is still presence-checking,
and this type does not harden it.

What it genuinely fixes is the **static** boundary: `ty` verifies every call site and the
resolver factory's type, the untyped `getattr` dispatch is gone, and `PeerResolver`'s
parameter is no longer narrowed by a cast that the gate could not justify.

Making FR-023's refusal real at **runtime** needs an explicit opt-in from the destination —
ABC inheritance or a class-level marker — which is a **separate design decision** that
AD086 deliberately does not take, and this module does not implement.
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
