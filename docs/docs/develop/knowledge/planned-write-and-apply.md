---
title: "Planned writes and apply"
---

## Planned writes and apply

<!-- Extracted from dev/specs/archive/001-plan-artifact-saved-apply on 2026-07-28 -->

> Part of: Develop > Knowledge | Related: [The saved plan artifact](plan-artifact.md), [Adapter anatomy](adapter-anatomy.md), [ADR 0002](https://github.com/opsmill/infrahub-sync/blob/feature/v3-develop/dev/adr/0002-planned-write-destination-protocol.md)

Applying a [saved plan artifact](plan-artifact.md) is a different write path from `sync`. A `sync` walks
a live comparison result and calls each destination model's `create` / `update` / `delete`; an apply
walks stored operations and never computes a diff, never extracts either side, and never reads the
destination the way `sync` does. This page describes what a destination adapter must offer for that to
work, how peers are resolved without a comparison store, and what the write does about relationships and
deletes.

`sync` is untouched by all of this. Everything below is new code on the planned-write path.

### The write surface

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

**In v1 this surface is Infrahub-only.** `PeerResolver` is the Infrahub adapter's concrete class, so
both members name an Infrahub type and a non-Infrahub destination cannot conform statically without
importing the Infrahub adapter. The protocol is the boundary the *engine* holds — it is what keeps the
apply loop free of a concrete adapter — and not yet the boundary a second destination can implement
against. An adapter-neutral resolver type is a tracked follow-up, to be settled with the first real
second implementer.

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

### Applying one operation

`apply_planned_operation` executes one operation convergently and returns the destination node id.

```text
1. node_schema = client.schema.get(kind=operation.kind)
2. data        = dict(operation.payload)      # mapped fields INCLUDING the identity components
3. for ref in operation.relationships:
       ids = [peers.resolve(peer_kind=ref.peer_kind, identity=p,
                            referring_operation_id=operation.operation_id)
              for p in ref.peers]
       data[ref.field] = ids[0] if ref.cardinality == "one" else ids
4a. CREATE ONLY — COVERAGE: the operation's identity names every human-friendly-ID
       component of the kind, or, for a kind declaring none, covers a uniqueness
       constraint            → UnkeyedCreateRefusedError
4b. CREATE ONLY — DIAGNOSTIC (AD051): every one of those components arrives with a
       USABLE value  → UnaccountedIdentityComponentError, naming the component
5.  node = client.create(kind=..., data=generate_payload_create(...))
5b. UPDATE ONLY: node.id = operation.destination_id   # the recorded key, never in `data`
6.  node.save(allow_upsert=True)               # the convergence point, and the ONLY write
6b. UPDATE ONLY: a NODE_NOT_FOUND + http_status 404 answer → StaleDestinationIdError
8.  peers.remember(operation.kind, operation.identity, node.id)
```

Both refusals at 4 happen **before** `client.create`, so a refused operation is proven to have
attempted no destination mutation. They are different questions and stay different mechanisms:
coverage is about the plan, and AD051 is the only check that can say *which* component is missing.

Both are **creates only**. A create is matched by the destination on the human-friendly-ID
components it carries, so their completeness is a condition of the write. An update is keyed by
its recorded id — the server is told exactly which object to write — so it may omit, blank or
fail to resolve a component and still land on the object it means.

Creates and updates both route through the same convergent upsert. Neither routes through
`InfrahubModel.update`, whose `local_id` keying needs a destination load an apply must not perform.

An update is keyed by the destination `id` recorded for it at plan time; a create carries none and is
matched by the server on the human-friendly-ID components in its payload, which is why step 4 proves
they are all there. There is no step reading the SDK's private pre-save render: that gate is retired
(ADR 0013) because on newer SDK versions it reported an `hfid` the issued mutation did not carry.

One consequence of using an upsert is accepted rather than detected: a create whose identity already
exists converges onto the existing object without examining whether its payload differs. There is no
conflict detection or freshness check, and the case is reported under the operation's **original**
identifier and **original** action, so the review-to-apply link is unaffected.

An update whose target was deleted out-of-band **no longer materializes as a create**. It carries that
object's recorded id, the destination answers `NODE_NOT_FOUND` with `extensions.http_status` 404 and
creates nothing, and the apply raises `StaleDestinationIdError` asking for a fresh `diff`. Both halves
of that signature are required, in one error: the code alone does not prove the id path ran, and the
refusal claims the write never happened.

#### Convergence rides on the destination kind's human-friendly ID

This is the single most misread part of the path, so it is worth stating plainly: what a **create**
converges on is the **destination schema's** `human_friendly_id`, not the source configuration's
`identifiers`. An update converges on the destination `id` recorded for it and is not subject to this
section. No `hfid` key is rendered on the wire for either: the SDK sets `data["id"]` when the node
carries one, and the server matches a create on the components in `data` (ADR 0013).

The two are not the same question and do not have the same answer. On the example NetBox configuration,
**ten** mapping entries carry a reference inside their `identifiers` — a configuration-side figure — while
the number of destination kinds whose *convergence key* crosses a relationship is **five**
(`InterfacePhysical`, `InterfaceVirtual`, `InterfaceLag`, `IpamPrefix`, `IpamIPAddress`). A magnitude for
a keying risk read off the configuration instead of the destination schema was wrong by a factor of two,
was cited by four decisions, and survived three rounds of critique before live data exposed it. When you
need to know how a kind converges, read the destination schema.

Two checks protect a create's key, and they check different things:

- **Step 4a is coverage**, read from the operation's `identity` alone: every component's mapping field
  must be named there, or — for a kind declaring no human-friendly ID — the identity must cover a
  declared uniqueness constraint. It is a question about the plan, and it is answered before any value
  is examined.
- **Step 4b is the diagnostic (AD051).** A direct component (`<attr>` or `<attr>__value`) must be
  present in `data` with a **usable** value: absent, empty and whitespace-only all key nothing, while
  `0` and `False` are values the destination matches on perfectly well. A relationship-crossing
  component (`<rel>__<attr>__value`) must have `<rel>` present in `data` **and** `<attr>` supplied,
  usably, by the operation's nested `{peer_kind, identity}` for `<rel>`. An unaccounted-for component
  raises, naming the kind and the component. "Resolves against the create data" is not implementable:
  by this step a relationship-crossing component's slot in `data` holds a resolved node-id string, and
  no attribute can be read out of a node id — which is why the value comes from the peer identity.
- **There used to be a third check, on the SDK's private pre-save render.** It is gone
  ([ADR 0013](https://github.com/opsmill/infrahub-sync/blob/feature/v3-develop/dev/adr/0013-writes-are-keyed-by-recorded-id-and-complete-hfid.md)). On infrahub-sdk
  1.23.2 that render reports an `hfid` the issued mutation does not carry, so it passed writes that
  were unkeyed on the wire, and it refused relationship-crossing kinds the server converges. No
  product code reads a private SDK render.

#### How each action is keyed

The two actions are keyed differently, and the asymmetry is the point: an update can be keyed by
something the plan recorded, and a create cannot.

**An update carries the destination `id`** recorded for it at plan time (plan format 3). Apply sets it
on the node before `save(allow_upsert=True)`; the SDK writes `data["id"] = self.id` when the node
carries one and considers `hfid` only otherwise, so that is what makes the upsert a keyed update of
that exact object. The id is never put into the `data` mapping handed to `client.create`, where it
would render as the attribute-shaped `id: {}` and key nothing. An id the destination cannot find comes
back as `NODE_NOT_FOUND` / `extensions.http_status` 404 with nothing written, raised as
`StaleDestinationIdError` and recorded as not-written.

Because an id keys anything, kinds with no human-friendly ID are fully supported for updates, and a
rename that changes the destination's human-friendly ID still updates the object it means.

**A create has no id**, so the server matches it on the human-friendly-ID components its payload
carries. A payload missing one component does **not** match: the server answers `ok: true` and creates
a second object, which nothing downstream can detect. Planning therefore proves the key rather than
warning about it, and refuses with `UnkeyedCreateRefusedError` when it cannot:

- every component's mapping field must be named by the operation's `identity` — a component resolvable
  from the payload but absent from identity still refuses, because identity is what the plan proves the
  write on and what a reviewer reads;
- each covered component must carry a usable value, proven through the peer's own identity where the
  component crosses a relationship;
- a kind declaring **no** human-friendly ID is allowed only where a declared uniqueness constraint is
  fully covered by the identity — the destination refuses the duplicate there (transport 200,
  `extensions.http_status` 422) — and refused otherwise, because it would duplicate on every write.

The selected key must also be writable. A read-only, server-allocated attribute such as
`rule_id` cannot recreate the source value during a create, even when it appears in the
human-friendly ID. Validation names the unusable components before extraction, and the
destination checks again before mutation. Map and select every component of an independent
writable uniqueness constraint to use a computed or read-only display ID safely. Registered
apply compares the bound schema fingerprint, including attribute writability, before writing.

Several creates projecting onto **one** destination human-friendly ID are refused as
`DestinationIdentityCollisionError`: the sync tells them apart and the destination does not, so
applying them would converge them onto one object each and lose the rest at exit 0. Updates are
excluded from that count, since an id-keyed write cannot converge onto another operation's object.

The write surface applies the same rule, from the same function, before `client.create` — so a create
refused at plan time and the same create arriving in a hand-built artifact are refused for the same
reason, and neither attempts a mutation.

Explicit `hfid` rendering is deliberately **not** reintroduced: `save(allow_upsert=True)` strips it on
1.23.2 and the server matches on complete components anyway, so rendering it would assert a key the
wire does not carry.

Apply is sequential, so the guarantee is stated per operation: a create refused by either check makes
zero mutation calls, and no later operation executes. Operations applied before it stay written, the
apply record reports them, and the refused operation is recorded as having written nothing rather than
as a possible partial write.

### Peer resolution

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
dropped operation does, and unlike a skipped delete it is not a designed limitation. For a
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

### Cardinality-one nulls in plan format v1

Plan format v1 does not encode whether a null optional cardinality-one relationship means
"the source supplied no relationship" or "clear the destination relationship." Planned apply
therefore omits that field. On a create this preserves absence; on an update it is a no-op and emits a
warning naming the operation, kind, and field so the run does not silently claim that a requested clear
converged. Clear the relationship directly at the destination, or use a future plan format that
represents clears explicitly, when a clear is intended.

A null mandatory cardinality-one relationship is never meaningful. Planned apply refuses it with
`NullRelationshipValueError` before SDK rendering or any destination mutation, rather than allowing the
SDK to turn Python `None` into a relationship id.

### Cardinality-many is an enforced replace-set

The destination ends holding exactly the peers the plan names. The convergent upsert of step 6 is what
does it: it carries each cardinality-many relationship as the plan's resolved peer list, and it is the
operation's only destination write. No destination read is involved.

**Surplus-peer removal is the server's replace semantics, not something the client can express one
peer at a time.** The SDK renders the **complete** reduced peer list the plan names — `[{id: …}, …]` —
and that list *is* the replacement signal: the destination removes every peer absent from it. What
never reaches the wire is an explicit per-peer removal directive, so an omitted peer means "remove
this one", never "leave this one alone" — an adapter that treats peer omission as irrelevant keeps
surplus peers silently. A fetch-and-reconcile round trip before the write would therefore decide
nothing: if the destination's Upsert mutation replaces the list, the written list is the new set with
or without it; if it merged, no in-process reconciliation could remove a peer either. The semantics
are pinned by a live shrink test rather than hedged in code.

See [ADR 0012](https://github.com/opsmill/infrahub-sync/blob/feature/v3-develop/dev/adr/0012-the-convergent-upsert-is-the-replace-set-write.md) for why the second,
targeted relationship write this path used to make was deleted; it carries the link to the record
of the forms that came before it.

`peers: []` under `cardinality: "many"` means "empty the set", and the upsert carries `[]` for it. The
observable throughout is the **issued destination write carrying the plan's peer list** — not the
manager's in-memory state and not a mocked adapter call.

### The apply loop

The order of the first four steps is load-bearing: **require `plan/`, read once, verify those bytes,
then parse them.**

```text
1. require_plan_directory(run_dir) — settled FIRST, so a run in the pre-existing row format
   keeps FR-019's own verdict instead of arriving as an unparseable manifest
2. read the artifact's bytes ONCE. Verification, the parse and the loop all consume that one
   RawPlanArtifact; a second read is what let files replaced mid-apply execute unverified
   (DBR-006, DBA-004)
3. verify THOSE bytes + isinstance(destination, PlannedWriteDestination) → refuse before any write
4. parse them; classify v1 / torn / unrecognized version.
   An action outside ACTIONS is refused HERE, before any write → run state failed
4b. refuse a format the current version cannot APPLY → PlanFormatApplyUnsupportedError.
   AFTER the parse, so a torn format-2 artifact still reports as torn rather than merely
   old; BEFORE the resolver and the loop, so nothing is dispatched. A format-2 update
   records no destination id and so cannot be keyed; the plan stays readable and
   reviewable, and the message asks for a fresh `diff`
5. peers = destination.new_peer_resolver()
6. applied: list[str] = []   ;   skipped_deletes: list[str] = []      # both ORDERED
7. for operation in stored order:
       delete            → record the identifier and continue, never dispatched
       peer/destination failure → attach the PARTIAL record — including this operation's id
                                  under failed_operation — to the error, name the next
                                  action, STOP
       otherwise         → applied.append(operation.operation_id)
8. AFTER the loop, on a COMPLETED apply, check the knowability invariant
9. if skipped_deletes: one warning naming the count
10. RETURN the record; apply_plan writes no run file
```

**Verification precedes the parse**, and not the other way round, because FR-009 requires the
format-version gate's message to state that the remaining four checks were not evaluated, and requires a
tear co-occurring with a `config_version` or `source_snapshot` mismatch to report every failure rather
than only the tear. Parsing first raises the parser's single-condition refusal, and neither obligation
can then be met. The parse still runs **before the loop**, so an unrecognized `action` is refused before
any destination write (FR-017, AD055).

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
`skipped_delete_count`, `failed_operation`, `failed_operation_wrote` and
`may_have_partially_written`. All six are always written: "nothing was applied" and "nothing failed"
have to be readable from the run rather than inferred from an absent key.

A destination rejection or transport failure stops at that operation. What was written stays written;
there is no rollback.

**And the failing operation may itself have written part of its change — unless it is proven not to
have.** An operation is one destination mutation, and one mutation can still commit remotely before
its response, or the transport carrying it, fails; that leaves the destination changed by an
operation in neither `applied_operations` nor `skipped_delete_operations`. The record names it under
`failed_operation` and reports `may_have_partially_written`, and the engine's error message says the
same in words. The marker is deliberately "may": the engine learns that the call raised, never how
far it got, and a marker that understated the writes would be the one an operator could not recover
from by reading the run. Convergent re-apply is what recovers it (AD033).

`failed_operation_wrote` is the exception, and it carries exactly one claim: `None` means the reach
is unknown — what every failure meant before it existed — and `False` means the operation is **proven**
to have written nothing. Only two things may claim it. A refusal raised before the SDK write
(`UnkeyedCreateRefusedError`, `UnaccountedIdentityComponentError`, `NullRelationshipValueError`,
`PeerNotFoundError`, `PeerAmbiguousError`) attempted no mutation at all. And `StaleDestinationIdError`
carries the destination's own not-found for an id-keyed upsert, a path that creates nothing. `True` is
never set: an operation that failed *after* dispatching is precisely the case that cannot be known.

`may_have_partially_written` is then `failed_operation is not None and failed_operation_wrote is not
False`, the engine's message drops the partial-write sentence for a proven-not-written refusal, and the
service boundary settles such a run as `failed` rather than `interrupted`/`ambiguous`, so it does not
set `reconciliation_required`. The claim is scoped to the **failing operation** and says nothing
about the plan: operations applied before it stay written and are listed in `applied_operations`,
and the apply stops there, so the destination is not as the plan describes it. What goes away is
the *uncertainty* — reporting an operation that provably did nothing as possibly partial was
sending operators to reconcile it (S6).

`may_have_partially_written` is derived from the two stored fields rather than stored beside them, as
`skipped_delete_count` is derived from `skipped_delete_operations`: on the record that is the only
account of what an apply did, a second source of truth is a state that can contradict itself. The
service boundary resolves the record once, from the failure it is describing, and reads its verdict and
its evidence from that same record for the same reason.

#### The operational exception boundary

Only an **operational** failure is reported as a destination refusal. `OPERATIONAL_APPLY_FAILURES`
in `infrahub_sync/potenda/__init__.py` is the list, and it has three members: the `PlanArtifactError`
taxonomy the write surface raises deliberately (a peer matching nothing or many, an unaccounted
identity component, a create that cannot be proven keyed, a stale recorded id),
`SkippedDeleteOperation`, and `infrahub_sdk.exceptions.Error`
— the destination library's own base, and therefore its transport, authentication, GraphQL and
object-validation rejections.

Anything else is a **defect**: a `TypeError` from the adapter's own schema-type guard, an
`AttributeError` or `KeyError` after an SDK shape change, a stray `AssertionError`. Wrapping one in
`OperationApplyFailedError` tells the operator to repair a destination that is working and re-plan a
plan that is fine, and hides the traceback that is the only diagnosis of the real fault — so it
propagates **unchanged**, with the partial record attached as an `apply_record` attribute. The CLI's
generic arm persists that record, logs one line saying this is a defect rather than a refusal, and
re-raises so the traceback still reaches the operator. Its interrupt arm records the same way and
logs nothing: an interrupt is neither a defect nor a refusal.

The boundary is deliberately narrow rather than generous. An `httpx` error the SDK failed to
translate escapes as a defect instead of being wrapped on suspicion, because a defect labelled as a
destination refusal is the more expensive mistake — the run records what was written either way.

### Deletes are recorded, never executed

Deletes are derived by set difference and recorded as first-class operations, then never executed —
executing them is not supported. An apply over a delete-bearing plan completes
**`applied`**, with `summary["skipped_delete_count"]`, `summary["skipped_delete_operations"]` in stored
order, and one warning naming the count. `applied_operations ∪ skipped_delete_operations` equals the
plan's full identifier set on any completed apply, which is what keeps the applied set knowable against
the reviewed set as a recorded value rather than an inference.

Because the engine's fallback flag set hides destination-only objects from the comparison, a
delete-bearing plan is the **ordinary** case, not an exception. See
[ADR 0004](https://github.com/opsmill/infrahub-sync/blob/feature/v3-develop/dev/adr/0004-deletes-are-recorded-but-never-executed.md).

### A note on logging

The code on this path emits through the standard library's `logging`, as every other module in
`infrahub_sync/` does, and the warning levels described above are `logging` levels. The project
constitution and `AGENTS.md` both mandate `structlog`, which no module currently uses. That divergence is
an open governance question tracked outside this feature — it is recorded here only so the levels above
read unambiguously, and it is **not** guidance either way.

### See also

- [The saved plan artifact](plan-artifact.md) — the format this path consumes.
- [ADR 0002](https://github.com/opsmill/infrahub-sync/blob/feature/v3-develop/dev/adr/0002-planned-write-destination-protocol.md) — the write-surface boundary.
- [ADR 0012](https://github.com/opsmill/infrahub-sync/blob/feature/v3-develop/dev/adr/0012-the-convergent-upsert-is-the-replace-set-write.md) — one upsert is the
  whole write, and what pins its replace semantics.
- [ADR 0004](https://github.com/opsmill/infrahub-sync/blob/feature/v3-develop/dev/adr/0004-deletes-are-recorded-but-never-executed.md) — the delete contract.
- [Adapter anatomy](adapter-anatomy.md) — the `sync`-path contract this sits beside.
