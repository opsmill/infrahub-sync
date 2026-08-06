# 2. The destination planned-write surface is a Protocol

**Status**: Accepted
**Date**: 2026-07-28
**Source**: `dev/specs/archive/001-plan-artifact-saved-apply/research.md` (PD-010),
`dev/specs/archive/001-plan-artifact-saved-apply/contracts/destination-write-surface.md` (AD086)

## Context

Applying a saved plan needs something from the destination that a `sync` does not: a per-operation
convergent write that takes a plan operation rather than a comparison model, plus a per-apply peer
resolver. The engine had no typed way to ask for either.

What it had was a `hasattr` probe for the v1 row-apply method, a `getattr` dispatch, and a
`cast("InfrahubAdapter", self.destination)` in the apply loop so the resolver could be built at all.
That cast asserted something the gate never checked, and the cost showed up during the work: with no
typed and no documented write boundary, a prescription to change `update_node` — whose only caller is
the live `sync` write path, so the change would have made `infrahub-sync sync` start removing
destination relationship peers — reached a quality gate before it was caught.

## Decision

The surface is a `runtime_checkable` `Protocol`, `PlannedWriteDestination`, in
`infrahub_sync/plan/write_surface.py`, with exactly two members:

```python
@runtime_checkable
class PlannedWriteDestination(Protocol):
    def new_peer_resolver(self) -> PeerResolver: ...
    def apply_planned_operation(
        self, *, operation: PlannedOperation, peers: PeerResolver
    ) -> str: ...
```

The pre-write gate is `isinstance(destination, PlannedWriteDestination)`. A destination that is not
one is refused before any write, named, and directed at `sync`. The resolver factory is a member
because the engine must build a resolver without naming a concrete adapter — which is precisely what
the cast was doing, and no cast remains.

The v1 `apply_cached_row` dispatch is **removed** rather than kept alongside. A second apply path with
weaker guarantees is the outcome this design exists to prevent, and removal is unusually safe here:
`apply_cached_row` had zero implementations anywhere in the repository, so nothing could have been
calling it successfully.

## Consequences

**What this buys, and what it does not.** `isinstance` against a `runtime_checkable` Protocol verifies
**member presence only, never signatures**. Against a duck-typed destination it is therefore
equivalent to the `hasattr` gate it replaced — no stronger. The runtime refusal is still
presence-checking, and this decision does not harden it.

What it genuinely fixes is the **static** boundary. `ty` now checks both call sites and the resolver
factory's type, the untyped `getattr` dispatch is gone, and the cast that the gate could not justify
is gone with it. A future change to what a destination must offer is a change to a declared type that
a type checker reads, rather than to an attribute name spelled in a string.

The limit is asserted rather than described: a test passes the gate with a destination whose members
carry the right names and the wrong shapes, and shows it failing later. That test is the guard against
a later reader mistaking the Protocol for a conformance check.

**In v1 the surface is Infrahub-only.** Both members name `PeerResolver`, the Infrahub adapter's
concrete resolver class, so a non-Infrahub destination cannot conform to the protocol statically
without importing the Infrahub adapter. The boundary this decision buys is the one the **engine**
holds — it is what keeps the apply loop free of a concrete adapter — and not yet a boundary a second
destination can implement against. An adapter-neutral resolver type is a tracked follow-up, to be
settled with the first real second implementer; the adapter-writing guide and guideline are scoped to
say so.

Making the refusal real at runtime needs an explicit opt-in from the destination — ABC inheritance or
a class-level marker. That is a **separate decision** this one deliberately does not take.

Removing the v1 dispatch retires a public-looking adapter extension point. Nothing implemented it, so
there is no user to deprecate for; `plan.parquet` keeps being written and is simply never read.

## Alternatives Considered

- **Keep the `hasattr` probe.** Rejected: it leaves the cast unjustified and gives the type checker
  nothing to verify, which is the condition that let the `update_node` prescription above through.
- **An abstract base class the destination must inherit.** Would make the runtime check real, at the
  cost of forcing inheritance on every destination adapter. Deferred as its own decision rather than
  taken quietly inside this one.
- **Keep the v1 dispatch beside the new one, selecting on which artifact is present.** Rejected: that
  is the two-paths-with-different-guarantees outcome, and it makes the v1 rejection message
  unreachable.
- **Deprecate `apply_cached_row` with a warning.** Rejected: nothing implements it, so there is no
  caller to warn.
