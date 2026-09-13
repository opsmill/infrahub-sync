# 12. The convergent upsert is the replace-set write

**Status**: Accepted
**Date**: 2026-09-13
**Source**: ADP-4 (v3-mvp), superseding [ADR 0003](0003-replace-set-flush-is-a-targeted-relationship-write.md)

## Context

[ADR 0003](0003-replace-set-flush-is-a-targeted-relationship-write.md) made a planned operation two
destination writes: the convergent upsert, then a targeted `<kind>Update` — the flush — carrying the
node's `id` plus only the cardinality-many fields being replaced. It existed because no live Infrahub
was reachable when the planned-write path was built, so whether the server's Upsert mutation replaces
or merges a relationship list could not be settled and was not assumed.

Three facts have since settled it.

The flush duplicated the upsert. Its own record says so: it rendered "the manager the create payload
built", carrying "the same per-peer `source`/`owner`/`is_protected` metadata the upsert carried". It
sent the destination a peer list the upsert had already sent.

The live proof arrived. With the flush stubbed to a no-op — and a raise-probe proving the stub ran —
the live shrink test passed 3 → 1 → 0 against Infrahub 1.10.6. The upsert alone shrinks a
cardinality-many peer set and empties it.

The flush cost an SDK-boundary coupling. Rendering a targeted mutation by hand needed three private
infrahub-sdk calls — `RelationshipManagerBase._generate_input_data`,
`InfrahubNodeBase._generate_mutation_query` and `_process_mutation_result` — inside a dependency
pinned to a range (`infrahub-sdk[all]>=1.17,<2`). The flush's stated reason for being *targeted*
rather than a whole-node re-render was that the SDK emits `<rel>: null` for every unmapped optional
cardinality-one relationship once a node is marked existing, and the upsert marks it existing. That
hazard belongs to a render **after** the upsert. With no second write there is no such render: the
keyed-render gate's probe and the render inside `save(allow_upsert=True)` both run while the node is
still marked new.

## Decision

A planned operation is written by exactly one destination mutation: the convergent upsert. It carries
the plan's cardinality-many peer list, including `[]` for a set the plan records as empty. There is no
second write, and the write itself makes no private SDK mutation-render, mutation-query or
mutation-response call.

## Consequences

Surplus-peer removal now rests entirely on the destination's Upsert mutation replacing a relationship
list rather than merging it. That is a property of the server, and
`tests/integration/test_infrahub_replace_set_shrink_integration.py` is what pins it: it shrinks a peer
set 3 → 1 → 0 through the planned-write surface and reads the result back from the destination.

**The escalation is unchanged from ADR 0003.** If that test ever fails on a peer-set assertion,
Infrahub has been proven to merge rather than replace, and the answer is to implement explicit
per-peer removal mutations — not to weaken the assertions.

Two properties the flush was documented to provide are now proven of the upsert instead, offline and
live: per-peer write metadata (`is_protected`, `source`, `owner`) reaches the destination on the
upsert, and an optional cardinality-one relationship the plan does not map is left untouched.

A failed operation is simpler to describe: one mutation may have committed before the failure was
observed, rather than a two-write window. The conservative `may_have_partially_written` marker is
unchanged.

## Alternatives considered

**Keep the flush, behind a public SDK API when one exists.** Rejected. The objection to the flush is
not only that it reaches past the SDK's public surface; it is that it is a second write that adds
nothing. A public API for it would still send the destination a peer list the upsert already sent.

**Keep the flush until the SDK bump.** Rejected. The coupling it carries is exactly what makes an SDK
bump expensive, and the live evidence that it is unnecessary is already in hand.
