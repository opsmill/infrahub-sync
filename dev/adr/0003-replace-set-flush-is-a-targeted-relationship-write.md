# 3. The replace-set flush is a targeted relationship write

**Status**: Accepted
**Date**: 2026-07-28
**Source**: `dev/specs/archive/001-plan-artifact-saved-apply/research.md` (PD-005), `contracts/destination-write-surface.md` (AD054, AD065, AD075, AD085, AD088)

## Context

A planned write must make cardinality-many relationships a replace-set: the destination ends holding
exactly the peers the plan names, with surplus peers removed. The convergent path a planned write is
required to use is `client.create(...)` then `save(allow_upsert=True)`, and whether the server's upsert
mutation replaces or merges a relationship list cannot be determined without a live Infrahub. So the
apply path reconciles the peer set explicitly after the upsert instead of assuming the mutation did.

Getting that reconciliation to actually reach the destination took three shapes across the run. The
reasoning is the durable part of this record, because every intermediate shape passed a plausible test.

1. **Reconcile in memory and rely on the node's own save.** The peer-set editors on
   `RelationshipManagerSync` are purely local: `add()` and `remove()` mutate `self.peers` and set an
   update flag, and neither issues a client call. There is no `save` on the manager at all. A
   reconciliation that is never written is computed and discarded.
2. **Flush with a plain `node.save()`.** That renders with unmodified fields stripped, and the
   stripping pass pops any key whose rendered value equals the create payload's. A relationship
   reconciled to the **empty** set renders `[]`, the create payload also wrote `[]`, so the key is
   popped and `peers: []` — which the format defines as "empty the set" — never reaches the
   destination.
3. **Flush with `node.update(do_full_update=True)`.** That does turn the stripping off, so an emptied
   set survives. It is still a **whole-node render**, and that is the defect that matters.

Two further readings had to be corrected along the way, and both are the same class of error —
describing a fix as an ordering when the property at stake is whether an operation happens.

- Reading `peer_ids` off a locally built node returns the **desired** set as the **existing** one, so
  the comparison compares a set against itself and removes nothing. It can pass only against a mock.
- "Call `fetch()` first" does not fix that. `fetch()` opens with `if not self.initialized:` and the
  manager already reports itself initialized, so the `client.get` that would read the destination
  never runs. The helper must force the manager cold — clear `initialized`, then `fetch()` — so a
  destination read is actually **issued**.

## Decision

**No form of a whole-node re-render is the flush.** The flush is a hand-built targeted relationship
write, carrying `id` plus only the cardinality-many fields being replaced:

```text
Mutation(mutation=f"{kind}Update",
         input_data={"data": {<each replaced rel>: manager._generate_input_data(), "id": node.id}},
         query=node._generate_mutation_query())
  -> client.execute_graphql(...) -> node._process_mutation_result(...)
```

It is issued **once**, after the loop over every cardinality-many relationship on the operation, and it
is issued against the same node object whose managers were reconciled.

The reason no whole-node render qualifies: rendering a node the SDK considers **existing** emits
`data[<rel>] = None` for every **optional cardinality-one** relationship left uninitialized — the SDK's
own comment says it is there "to allow clearing relationships". The upsert at step 6 is what marks the
node existing. So a whole-node flush silently clears every optional cardinality-one destination
relationship the plan never mapped, which the payload-authority requirement forbids in the same
sentence that asked for the full form.

**No flag avoids it.** With stripping off, nothing is stripped. With stripping on, the first pass's
arms match neither an uninitialized optional cardinality-one relationship, and the second pass never
visits a key that is absent from the original data it walks. The null therefore goes out under both
render modes, which means it was latent in the first design rather than introduced by the third.

Two invariants come with it:

- **The node that is written must be the node whose managers were reconciled.** Reconciling a manager
  on a separately fetched node leaves the flush carrying the create payload's peer list — the desired
  set, never compared against anything — and silently reproduces the discarded-reconciliation defect.
  Forcing the manager cold satisfies this by construction, because `fetch()` assigns the peers it
  reads back onto the manager it was called on.
- **The observable is the issued destination write carrying the reconciled peer list** — not the
  manager's in-memory `peer_ids`, and not a mocked adapter call. Every weaker observable is satisfied
  by a helper that reconciles and never writes.

The enforcement is **new code on the planned-write path**. `update_node` keeps its present code and its
present behavior; the eight-line shape is written a second time, and that duplication is the price.

## Consequences

The replace-set clause is true by construction rather than by an assumption about server behavior
nobody could test offline. It costs one extra destination read per cardinality-many relationship and
one extra write per operation that carries any. If the upsert already replaces, the reconciliation is a
no-op — and now a no-op for the right reason: the difference sets are empty because the destination
already holds the desired set, not because the comparison never looked.

The flush's **field set** is asserted, not only its values: it must name `id` plus the replaced
cardinality-many fields and nothing else, against a fixture kind that declares an unmapped optional
cardinality-one relationship. Without such a fixture the assertion has nothing to catch, and every
other assertion in the harness passes against a whole-node flush.

Because this depends on undocumented SDK internals behind a version range, an SDK-boundary tripwire
accompanies it. It goes straight at the SDK with no adapter code involved and fails loudly, naming
this decision, if a whole-node render on an existing node stops emitting `<rel>: None` for an
uninitialized optional cardinality-one relationship under either render mode.

`update_node`'s additive ordering — it compares against an unloaded peer set and so adds without
removing — remains a **pre-existing defect on the live `sync` path, for a later outcome to own**. It is
left alone deliberately: its only caller is the live write path, so correcting it would make
`infrahub-sync sync` start removing destination relationship peers on configurations that have never
removed one. That is a data-removing change to an existing command, and sound engineering about
duplicate shapes is not authority to make it.

## Alternatives Considered

- **Trust the upsert to replace.** Rejected: unverifiable offline, and a silent merge leaves stale
  peers attached — caught only against a live server, which is late.
- **Route through `update_node`.** Rejected: it needs `client.get(id=local_id)`, and a saved-plan apply
  must not read the destination that way.
- **Extract one shared helper and correct its ordering for both callers.** Ratified first, then
  overruled. It reaches the live `sync` write path, which this work does not authorize changing.
- **A relationship-level mutation.** Rejected: the SDK models no general relationship method.
  `RelationshipAdd` appears once, hand-interpolated for one hard-coded relationship name on the async
  client; there is no `RelationshipRemove` at all. A replace-set would need two mutations per
  relationship, and emptying a set could not be expressed through the add half.
- **Pre-initialise or restore the unmapped relationships before a whole-node flush.** Rejected: it
  treats the symptom, and it would require reading every unmapped relationship of the destination
  object first.
