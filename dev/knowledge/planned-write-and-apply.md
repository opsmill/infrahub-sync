# Planned writes and apply

<!-- Extracted from dev/specs/archive/001-plan-artifact-saved-apply on 2026-07-28 -->

> Part of: `dev/knowledge/` | Related: [The saved plan artifact](plan-artifact.md), [Adapter anatomy](adapter-anatomy.md), [ADR 0002](../adr/0002-planned-write-destination-protocol.md)

Applying a [saved plan artifact](plan-artifact.md) is a different write path from `sync`. A `sync` walks
a live comparison result and calls each destination model's `create` / `update` / `delete`; an apply
walks stored operations and never computes a diff, never extracts either side, and never reads the
destination the way `sync` does. This page describes what a destination adapter must offer for that to
work, how peers are resolved without a comparison store, and what the write does about relationships and
deletes.

`sync` is untouched by all of this. Everything below is new code on the planned-write path.

## The write surface

A destination that a saved plan can be applied through is a `PlannedWriteDestination` — a
`runtime_checkable` `Protocol` in `infrahub_sync/plan/write_surface.py` with exactly two members:

```python
@runtime_checkable
class PlannedWriteDestination(Protocol):
    def new_peer_resolver(self) -> PeerResolver: ...
    def apply_planned_operation(
        self, *, operation: PlannedOperation, peers: PeerResolver
    ) -> str: ...
```

The resolver factory is a member because the engine has to build a resolver without naming a concrete
adapter — that narrowing is what previously forced a `cast("InfrahubAdapter", ...)` in the apply loop,
and no cast remains.

The pre-write gate is `isinstance(destination, PlannedWriteDestination)`, run inside the same gate as
the artifact verification checks rather than as a per-operation surprise. A destination that is not one
is refused **before any write**, named, and directed at `sync`.

**What that check does and does not verify.** An `isinstance` check against a `runtime_checkable`
Protocol verifies **member presence only, never signatures**, so against a duck-typed destination it is
no stronger than the `hasattr` check it replaced. What the Protocol genuinely fixes is the **static**
boundary: `ty` verifies both call sites and the resolver factory's type, and the untyped `getattr`
dispatch and the unjustified cast are gone. Making the runtime refusal real would need an explicit
opt-in from the destination — ABC inheritance or a class-level marker — and that is an open decision, not
something this surface provides. A test asserts the limit by passing the gate with a destination whose
members carry the right names and the wrong shapes.

Only the Infrahub adapter implements the surface today.

## Applying one operation

`apply_planned_operation` executes one operation convergently and returns the destination node id.

```text
1. node_schema = client.schema.get(kind=operation.kind)
2. data        = dict(operation.payload)      # mapped fields INCLUDING the identity components
3. for ref in operation.relationships:
       ids = [peers.resolve(peer_kind=ref.peer_kind, identity=p,
                            referring_operation_id=operation.operation_id)
              for p in ref.peers]
       data[ref.field] = ids[0] if ref.cardinality == "one" else ids
3b. DIAGNOSTIC: every human-friendly-ID component of the kind is accounted for
5.  node = client.create(kind=..., data=generate_payload_create(...))
5b. GATE: read the rendered mutation input and check it carries `id` or `hfid`
6.  node.save(allow_upsert=True)               # the convergence point
7.  for each cardinality-many relationship: reconcile the peer set (see below)
7e. ONE targeted relationship write flushing the reconciled sets
8.  peers.remember(operation.kind, operation.identity, node.id)
```

Creates and updates both route through the same convergent upsert. Neither routes through
`InfrahubModel.update`, whose `local_id` keying needs a destination load an apply must not perform.

Two consequences of using an upsert are accepted rather than detected: a create whose identity already
exists converges onto the existing object without examining whether its payload differs, and an update
whose target was deleted out-of-band materializes as a create. There is no conflict detection, freshness
check or refusal path. Either case is reported under the operation's **original** identifier and
**original** action, so the review-to-apply link is unaffected.

### Convergence rides on the destination kind's human-friendly ID

This is the single most misread part of the path, so it is worth stating plainly: the convergence key is
the **destination schema's** `human_friendly_id`, not the source configuration's `identifiers`. The SDK's
upsert mutation is keyed on `data["id"]` if set, else `data["hfid"]`, and `get_human_friendly_id()`
returns `None` if any component path resolves to `None`.

The two are not the same question and do not have the same answer. On the example NetBox configuration,
**ten** mapping entries carry a reference inside their `identifiers` — a configuration-side figure — while
the number of destination kinds whose *convergence key* crosses a relationship is **five**
(`InterfacePhysical`, `InterfaceVirtual`, `InterfaceLag`, `IpamPrefix`, `IpamIPAddress`). A magnitude for
a keying risk read off the configuration instead of the destination schema was wrong by a factor of two,
was cited by four decisions, and survived three rounds of critique before live data exposed it. When you
need to know how a kind converges, read the destination schema.

Two checks protect the key, and they check different things:

- **Step 3b is the diagnostic.** A direct component (`<attr>` or `<attr>__value`) must be present and
  non-`None` in `data`. A relationship-crossing component (`<rel>__<attr>__value`) must have `<rel>`
  present in `data` **and** `<attr>` supplied by the operation's nested `{peer_kind, identity}` for
  `<rel>`. An unaccounted-for component raises, naming the kind and the component. "Resolves against the
  create data" is not implementable: by step 3b, a relationship-crossing component's slot in `data` holds
  a resolved node-id string, and no attribute can be read out of a node id.
- **Step 5b is the gate**, and it reads the property where it actually lives — the rendered mutation
  input, which the SDK builds client-side, so it is checkable with no server. Read it one level deeper
  than the render call's own `"data"` key: `_generate_input_data(...)["data"]` is `{"data": {...}}`, a
  one-key mapping, so a check written against it would fire on every operation ever rendered.

The gate branches on the kind's HFID shape, and the branch is the point:

| HFID shape | Render carries neither `id` nor `hfid` |
|---|---|
| **All direct** components | **Raise**, naming the kind — it can only mean the payload lost its identity components, which is always a defect |
| **Crosses a relationship** | **Warn once per kind** and proceed |
| **No HFID declared** | **Warn once per kind** and proceed — never raise; for such a kind, unkeyed is a schema fact, not a defect |

The relationship-crossing row is not a shrug. The SDK cannot form an `hfid` client-side from a peer
supplied as a resolved id: rendering a relationship value handed in as a bare id produces `{"id": ...}`
with no `__typename`, so the store read that would resolve the peer is never attempted. Refusing would
withdraw the relationship-bearing kinds from what this path supports. The write is issued, and the server
may still key it: against one live destination, thirteen `InterfacePhysical` upserts rendered unkeyed, were
issued with the warning, and a second apply of the identical plan produced no duplicate — because that
kind declares a `device-name` uniqueness constraint the destination resolved the upsert on. That is one
destination's answer. A relationship-crossing key with no covering uniqueness constraint would still
duplicate.

**So state the guarantee narrowly.** No write is issued whose payload is missing an HFID component, and
no render is issued unkeyed where being unkeyed can only be a defect. "An unkeyed write is never issued"
is false, and was struck from three places where it had been written.

The warning is at **warning level** and fires **once per destination kind**, with the dedup set living on
the adapter instance for the lifetime of the apply. Both properties are pinned rather than described:
`--quiet` floors the package logger at warning level, so an info-level emission satisfies every prose
description of the obligation and vanishes for exactly the scripted and CI runs where it is the only
signal; and once-per-operation would put a line on every row of a large apply. The content names the
kind, that the write was issued anyway, which of the two conditions applies, and what to watch for.

## Peer resolution

An apply cannot use the comparison store — that is exactly the dependency a saved-plan apply cannot
satisfy — so peers are resolved through a `PeerResolver` the destination builds for one apply.

```python
class PeerResolver:
    def resolve(self, *, peer_kind: str, identity: dict[str, Any],
                referring_operation_id: str) -> str: ...
    def remember(self, kind: str, identity: dict[str, Any], node_id: str) -> None: ...
```

| Property | Rule |
|---|---|
| Lifetime | One apply. Created at its start, discarded with it, never persisted |
| Key | `(kind, canonical_identity(identity))` — the same canonical form the operation identifier hashes |
| Population | From each completed create/update, so an operation's own result resolves later operations that refer to it |
| Miss | Queries the destination and memoizes the **successful** result |
| Negative caching | **Never.** A failed lookup is not cached, so a later reference re-attempts resolution |
| Comparison store | Never read, neither `client.store` nor the DiffSync store |

`resolve` is the one declared entry point; there is no `resolve_one` / `resolve_many`. The
cardinality distinction is the **caller's** to make from `ref.cardinality`, because the resolver maps one
peer identity to one destination id and knows nothing about the shape of the field it is resolving for.
Passing `referring_operation_id` on every call is what lets a miss name the referring operation.

The destination query is built from the destination schema's own `human_friendly_id` component paths,
which the adapter already caches wholesale:

| HFID component path | Value taken from | Filter kwarg |
|---|---|---|
| `<attr>__value` | the peer identity's scalar under `<attr>` | `<attr>__value=<v>` |
| `<rel>__<attr>__value` | the nested `{peer_kind, identity}` the peer identity records under `<rel>`, read at `identity[<attr>]` | `<rel>__<attr>__value=<v>` |

The result count drives three arms: exactly one returns the node id and memoizes; **zero** raises
`PeerNotFoundError`; **more than one** raises `PeerAmbiguousError`. Both name the peer kind, the peer
identity, the referring operation and the operator's next action, and both fail the run. Neither is ever
a silent skip — a dropped relationship makes the applied set differ from the reviewed set exactly as a
dropped operation does, and unlike a skipped delete it is not a designed limitation of the release. For a
kind whose HFID does not cover its plan identity, the documented fallback is to resolve the reference
component's own peer first and filter on `<rel>__ids`.

These two refusals belong to **this resolver only**. The live `sync` path's existing warn-and-continue on
an unresolvable peer, and the SDK's bare `IndexError` on a multi-match, are unchanged, and a test asserts
they still hold.

Dependency-tier ordering guarantees a peer is written before anything referring to it, but only for
references the dependency graph carries. Three cases it cannot express — a self-reference, a reference
reachable only through an optional edge dropped to break a cycle, and any reference under an explicit
`order:`, which yields no tiers at all — may leave a peer unresolved at apply, where the zero-match arm
governs. The qualification is safe precisely because the miss is loud.

## Cardinality-many is an enforced replace-set

The convergent path has no verified replace-set semantics, and whether the server's upsert replaces or
merges a relationship list cannot be settled offline. So the apply path reconciles explicitly after the
upsert, per cardinality-many relationship:

```text
7a. rm = getattr(node, ref.field)     # the manager ON THE NODE THE CALLER WILL WRITE
    rm.initialized = False            # discard the locally built peer set …
    rm.fetch()                        # … so the guarded client.get actually runs
7b. existing = rm.peer_ids            # now the destination's actual set
7c. _, existing_only, new_only = compare_lists(existing, new_peer_ids)
7d. remove existing_only, add new_only            # IN MEMORY ONLY
7e. (caller, once after the loop) one targeted relationship write
```

Three properties of this are easy to get wrong, and each was got wrong once:

- **The manager must be forced cold.** A relationship manager sets `initialized = data is not None`, so a
  node built from the write payload reports the **desired** set as its **existing** set, and the
  comparison compares a set against itself and removes nothing. Calling `fetch()` earlier does not fix
  it: `fetch()` opens with `if not self.initialized:` and the `client.get` that would read the
  destination lives inside that guard. The property at stake is whether a destination read is **issued**,
  not what order two calls happen in.
- **The reconciliation must be flushed.** `RelationshipManagerSync` has no `save`; `add` and `remove`
  only mutate `self.peers` and set a flag. Without a subsequent write, the surplus is computed and
  discarded. This mirrors `update_node`, which also returns its node unwritten and leaves the flush to
  its caller.
- **The flush is a targeted relationship write, never a whole-node re-render.** A whole-node render of a
  node the SDK considers existing emits `<rel>: None` for every **optional cardinality-one** relationship
  left uninitialized, silently clearing destination fields the plan never mapped. No render flag avoids
  it. The flush is built by hand — `f"{kind}Update"` carrying `id` plus only the replaced
  cardinality-many fields, issued through `client.execute_graphql` — and issued **once** after the loop,
  against the same node object whose managers were reconciled. See
  [ADR 0003](../adr/0003-replace-set-flush-is-a-targeted-relationship-write.md) for the full reasoning
  and the two withdrawn forms.

`peers: []` under `cardinality: "many"` means "empty the set", and the replace-set acts on it. The
observable throughout is the **issued destination write carrying the reconciled peer list** — not the
manager's in-memory state and not a mocked adapter call.

## The apply loop

```text
1. load the artifact; classify v1 / torn / unrecognized version.
   An action outside ACTIONS is refused HERE, before any write → run state failed
2. verification checks + isinstance(destination, PlannedWriteDestination) → refuse before any write
3. peers = destination.new_peer_resolver()
4. applied: list[str] = []   ;   skipped_deletes: list[str] = []      # both ORDERED
5. for operation in stored order:
       delete            → record the identifier and continue, never dispatched
       peer/destination failure → attach the PARTIAL record — including this operation's id
                                  under failed_operation — to the error, name the next
                                  action, STOP
       otherwise         → applied.append(operation.operation_id)
6. AFTER the loop, on a COMPLETED apply, check the knowability invariant
7. if skipped_deletes: one warning naming the count
8. RETURN the record; apply_plan writes no run file
```

Stored order is executed exactly. `applied` is an ordered sequence, so "the last operation reported as
applied" is its final element rather than a separate field. An empty plan applies as a successful no-op —
but verification still runs first.

**One writer owns the run file.** `apply_plan` returns its record and writes nothing; the CLI merges it
into `run_file.summary` and saves. `RunFile.save()` writes the whole payload with no merge, and the CLI
builds its instance with an empty summary and saves after the apply returns, so two writers means the
engine's keys are deleted. A mid-apply rejection carries its **partial** record on the raised error so
the CLI can merge it before recording `failed`. That partial record is best-effort and explicitly not
required to survive abnormal process termination.

The merged summary keys are `applied_operations`, `skipped_delete_operations`,
`skipped_delete_count`, `failed_operation` and `may_have_partially_written`. All five are always
written: "nothing was applied" and "nothing failed" have to be readable from the run rather than
inferred from an absent key.

A destination rejection or transport failure stops at that operation. What was written stays written;
there is no rollback.

**And the failing operation may itself have written part of its change.** Applying one operation is
not one write — step 5 upserts the object and step 7e writes its replaced cardinality-many
relationship sets — so a failure between them leaves the destination changed by an operation that is
in neither `applied_operations` nor `skipped_delete_operations`. The record therefore names it under
`failed_operation` and reports `may_have_partially_written`, and the engine's error message says the
same in words. The marker is deliberately "may": the engine learns that the call raised, never how
far it got, and a marker that understated the writes would be the one an operator could not recover
from by reading the run. Convergent re-apply is what recovers it — re-applying an operation that
already succeeded in whole or in part converges on the same object (AD033).

`may_have_partially_written` is derived from `failed_operation` rather than stored beside it, as
`skipped_delete_count` is derived from `skipped_delete_operations`: on the record that is the only
account of what an apply did, a second source of truth is a state that can contradict itself.

## Deletes are recorded, never executed

Deletes are derived by set difference and recorded as first-class operations, then never executed —
executing them is out of scope for this release. An apply over a delete-bearing plan completes
**`applied`**, with `summary["skipped_delete_count"]`, `summary["skipped_delete_operations"]` in stored
order, and one warning naming the count. `applied_operations ∪ skipped_delete_operations` equals the
plan's full identifier set on any completed apply, which is what keeps the applied set knowable against
the reviewed set as a recorded value rather than an inference.

Because the engine's fallback flag set hides destination-only objects from the comparison, a
delete-bearing plan is the **ordinary** case, not an exception. See
[ADR 0004](../adr/0004-deletes-are-recorded-but-never-executed.md).

## A note on logging

The code on this path emits through the standard library's `logging`, as every other module in
`infrahub_sync/` does, and the warning levels described above are `logging` levels. The project
constitution and `AGENTS.md` both mandate `structlog`, which no module currently uses. That divergence is
an open governance question tracked outside this feature — it is recorded here only so the levels above
read unambiguously, and it is **not** guidance either way.

## See also

- [The saved plan artifact](plan-artifact.md) — the format this path consumes.
- [ADR 0002](../adr/0002-planned-write-destination-protocol.md) — the write-surface boundary.
- [ADR 0003](../adr/0003-replace-set-flush-is-a-targeted-relationship-write.md) — the flush.
- [ADR 0004](../adr/0004-deletes-are-recorded-but-never-executed.md) — the delete contract.
- [Adapter anatomy](adapter-anatomy.md) — the `sync`-path contract this sits beside.
