# Contract: the destination planned-write surface and apply-time peer resolution

**Requirements**: FR-013, FR-014, FR-016, FR-017, FR-020, FR-023, FR-025. **Ratified corrections applied
here**: AD054 (the replace-set re-read and the rendered-mutation harness), AD055 (a recorded delete ends
`applied` with a recorded skip count, not `failed`), AD058 (the resolver's declared entry point and the
verifier's adapter-name argument), AD059 (every failure names a next action), AD062 (the apply record's
named home in the run summary). **Scope**: implemented on
the Infrahub destination adapter only, which is the brief's scope and is recorded as an accepted
Principle III tension in [plan.md](../plan.md#complexity-tracking).

## What exists today, verified

| Fact | Location |
|---|---|
| The engine already dispatches a stored plan per row to `destination.apply_cached_row(...)` and guards a missing surface with `NotImplementedError` naming the adapter class | `infrahub_sync/potenda/__init__.py:341-370` |
| `apply_cached_row` has **zero** adapter implementations anywhere in the repository | only `infrahub_sync/potenda/__init__.py`, `tests/cache/test_apply_plan.py:43-44`, `tasks/bench.py:413` |
| The convergent create path is `client.create(...)` then `save(allow_upsert=True)` | `infrahub_sync/adapters/infrahub.py:611-612` |
| `InfrahubModel.update` opens with `client.get(id=self.local_id, ...)`, and `local_id` is populated only by a destination load | `infrahub_sync/adapters/infrahub.py:622`, `:510` |
| The only replace-set-shaped code in the tree is `update_node`'s `compare_lists` remove/add — and it is **not** a replace-set: it reads `attr_manager.peer_ids` at `:151` and only calls `fetch()` at `:168-169`, so it compares the desired set against an unloaded one and adds without removing | `infrahub_sync/adapters/infrahub.py:149-175` (read `:151`, compare `:166`, fetch `:168-169`, remove `:171-172`, add `:174-175`) |
| A relationship manager reports itself initialized from whatever data built it — `self.initialized = data is not None` — and `fetch()` returns immediately once initialized, so a locally built node reports the **desired** peer set as its **existing** one | `.venv/…/infrahub_sdk/node/relationship.py:264`, `:286-299` |
| The rendered mutation input is where keyedness is observable: `data["id"]` if set, else `data["hfid"]`, and the upsert path renders with `exclude_hfid=False` | `.venv/…/infrahub_sdk/node/node.py:295-298`; `create(allow_upsert=True)` `:1843-1846`; `save(allow_upsert=True)` dispatches to it `:1533-1535` |
| Peer resolution today reads the **loaded** SDK node store | `infrahub_sync/adapters/infrahub.py:57-94`, populated at `:454`, `:501`, `:613` |
| A zero-match peer is silently dropped with a warning | `infrahub_sync/adapters/infrahub.py:141-143`, `:212-214`, `:229-231` |
| A multi-match surfaces as a bare `IndexError("More than 1 node returned")` | `.venv/…/infrahub_sdk/client.py:566` |

The v1 dispatch is **replaced**, not kept alongside — leaving it wired would be the second apply path
FR-019 forbids, and it has zero implementations to break (PD-010).

## The adapter method

```python
class InfrahubAdapter(DiffSyncMixin, Adapter):
    def apply_planned_operation(
        self, *, operation: PlannedOperation, peers: PeerResolver
    ) -> str:
        """Execute one planned operation convergently. Returns the destination node id."""
```

An adapter that does not define `apply_planned_operation` causes the apply to fail **before any
write** with a clear, actionable error naming the adapter class and directing the operator to `sync` —
the shape the engine already has (FR-023). The check runs inside the same pre-write gate as the five
verification checks, not as a per-operation surprise.

### `delete` — a designed limitation, not a failure (AD055)

Raises `SkippedDeleteOperation` naming the operation identifier and the kind. It never touches the
destination. The engine **collects** these rather than stopping at the first, because SC-007 requires
every non-delete operation in the same plan to still be applied.

The run then ends **`applied`**, not `failed`. Applying deletes is out of scope for this release and is
assigned to a later outcome, so an apply that declines to execute one is behaving exactly as designed, and
a run that behaves as designed must not be reported as broken. What the engine records instead:

| Obligation | Rule |
|---|---|
| Run state | `applied` — the same state a delete-free apply reaches. No new state is introduced (AD010, AD055) |
| Count | `summary["skipped_delete_count"]`, the number of deletes not executed. Non-zero on any delete-bearing plan |
| Identifiers | `summary["skipped_delete_operations"]`, in stored order |
| Warning | An operator-visible warning on the run's log stream **naming the count** — not a debug line and not a per-operation trace: one message an operator reading the run's output cannot miss |
| Knowability | `summary["applied_operations"]` ∪ `summary["skipped_delete_operations"]` equals the plan's full identifier set. That is what DBR-016 protects: the reviewed set minus the applied set is a **recorded value**, not an inference — which is exactly what a silent skip is not (AD055) |

**The class that does still fail the run** is an operation whose `action` is outside `ACTIONS`. It is
refused while the artifact is being read — before any destination write — as
`UnsupportedOperationActionError` naming the operation identifier, the action found, the recognized
actions and the operator's next action, and the run is recorded `failed`. Nothing about such an operation
is designed, so what it would do to the destination is unknown and the run cannot claim to have applied
what was reviewed (FR-017, AD055, AD059).

### `create` and `update`

Both route through the same convergent upsert. Neither routes through `InfrahubModel.update`, whose
`local_id` keying needs a destination load FR-012 forbids (AD015).

```text
1. node_schema = client.schema.get(kind=operation.kind)            # must be a NodeSchemaAPI
2. data        = dict(operation.payload)   # mapped fields INCLUDING the identity components (AD042)
3. for ref in operation.relationships:                             # AD058 — one declared entry point
       ids = [peers.resolve(peer_kind=ref.peer_kind, identity=p,
                            referring_operation_id=operation.operation_id)
              for p in ref.peers]
       data[ref.field] = ids[0] if ref.cardinality == "one" else ids
3b. ASSERT every component path of node_schema.human_friendly_id is ACCOUNTED FOR (rules below)
       -> otherwise raise, naming the kind and the missing component; never issue an unkeyed write
4. create_data = client.schema.generate_payload_create(
       schema=node_schema, data=data,
       source=source_node.id, owner=owner_node.id, is_protected=True)       # parity with :608-610
5. node = client.create(kind=operation.kind, data=create_data)
6. node.save(allow_upsert=True)                                             # the convergence point
7. for ref in operation.relationships where cardinality == "many":
       _replace_relationship_set(node, ref.field, resolved_peer_ids)        # PD-005 + AD054 re-read
8. peers.remember(operation.kind, operation.identity, node.id)
9. return node.id
```

**Step 3 calls the resolver's one declared entry point (AD058).** An earlier draft of this step called
`peers.resolve_one(ref)` and `peers.resolve_many(ref)`, neither of which the `PeerResolver` contract below
declares — it declares `resolve(*, peer_kind, identity, referring_operation_id)` and nothing else. The
cardinality-one/many distinction is the **caller's** to make from `ref.cardinality`, because the resolver
resolves one peer identity to one destination id and knows nothing about the shape of the field it is being
resolved for. Passing `referring_operation_id` at every call is what lets the zero-match message name the
referring operation, which FR-014 requires.

### Step 2 and step 3b: the payload carries the identity, and the write refuses without it

`operation.payload` is `element.keys ∪ element.source_attrs` (AD042), so step 2 already carries the
identity components. This is the whole reason the write can converge: the SDK's upsert mutation is
keyed on `data["id"]` if set, else `data["hfid"]`, and `get_human_friendly_id()` returns `None` when
any component path resolves to `None` (`.venv/…/infrahub_sdk/node/node.py:295-298`, `:128-138`). A
payload built from `source_attrs` alone carries **no** identity fields — `get_attrs()` "does not
include the fields in `_identifiers`" (`.venv/…/diffsync/__init__.py:340-347`) — so the mutation would
carry neither `id` nor `hfid`, the upsert would be unkeyed, and every re-apply would duplicate.

Step 3b is the guard that makes that failure loud instead of silent: **assert that every
human-friendly-ID component path of the destination kind is accounted for**, before the create call is
made. An unaccounted-for component raises, naming the kind and the component. FR-024 warns about the
same condition at plan time; step 3b is what stops it becoming silent data duplication at apply time if
the plan-time warning was ignored or the schema changed since.

#### What "accounted for" means, per component (AD051)

"Resolves against the create data" was not implementable as first written. For a relationship-crossing
component the data holds a resolved node-id **string** by the time step 3b runs — step 3 put it there —
and an attribute cannot be read out of a node id. The check is therefore defined per component shape,
against two sources: the `data` mapping and the operation the write is executing.

| HFID component path | Check |
|---|---|
| `<attr>` or `<attr>__value` — a **direct** component | `data` contains the key `<attr>` and its value is not `None` |
| `<rel>__<attr>__value` — a **relationship-crossing** component | `data` contains the key `<rel>` and its value is not `None`, **and** the operation's nested `{peer_kind, identity}` for `<rel>` (AD043) supplies `<attr>` in its `identity` |

Both arms are checkable from what the apply holds, and both still fail for every case the assertion
exists to catch: a payload that lost its identity components (AD042's defect class) fails the first
arm, and a relationship reference the plan recorded without the nested pair fails the second. Nesting
deeper than one level recurses through the nested `identity` by the same rule.

#### Recorded risk: nested HFID resolution needs the SDK client store (AD051)

This is a dependency of the design on SDK behavior, recorded because the resolver is specified never to
touch that store, and it is carried as a **risk** with step 3b as its detector rather than as a settled
mechanism. It is also listed in [plan.md](../plan.md#risks).

| Verified fact | Location |
|---|---|
| `get_human_friendly_id()` returns `None` if **any** component resolves to `None` | `.venv/…/infrahub_sdk/node/node.py:135-139` |
| For a relationship-crossing component, `get_path_value()` resolves the peer via `related_node.get()`, catches `NodeNotFoundError`/`ValueError`, and returns `None` — the comment at the catch says "this can happen while batch creating nodes, the lookup won't work as the store is not populated" | `.venv/…/infrahub_sdk/node/node.py:100-107` |
| `RelatedNodeSync.get()` reads the **SDK client store**, and needs both `id` **and** `typename` to do so; with neither that nor an `hfid_str` it raises `ValueError` | `.venv/…/infrahub_sdk/node/related_node.py:298-304` |
| A relationship value handed in as a bare id renders as `{"id": "<id>"}` with no `__typename`, so `_typename` is `None` and the store read above is never attempted | `.venv/…/infrahub_sdk/schema/__init__.py:172-181`; `.venv/…/infrahub_sdk/node/related_node.py:54-55`, `:64-68` |
| With no `id` and no `hfid`, the mutation is unkeyed | `.venv/…/infrahub_sdk/node/node.py:295-298` |
| The client store is populated on `save()` and on `get`/`filters` with `populate_store=True` | `.venv/…/infrahub_sdk/node/node.py:744`, `:1549`; `.venv/…/infrahub_sdk/client.py:911-918`, `:2271-2278` |

**Consequence**: for a destination kind whose HFID crosses a relationship, the SDK cannot compute an
`hfid` client-side from a peer supplied as a resolved id alone. Step 3b's second arm confirms the plan
*carries* the nested attribute; it does not make the SDK able to read it. If the server's upsert cannot
key on the components as sent, the failure surfaces as a duplicate at a live destination — which is
exactly what the deferred SC-002 and SC-003 measure, and why they are deferred rather than replaced.
The mitigation available offline is that no unkeyed write is issued blind: an operation whose HFID
components cannot be accounted for raises at step 3b instead of duplicating silently. The residual —
whether an accounted-for nested component actually keys the server-side upsert — is unverifiable
without a live Infrahub (AD007) and is asserted by the `integration`-marked SC-002 and SC-003 tests.

| Clause | Rule | Requirement |
|---|---|---|
| Convergence key | The destination kind's human-friendly ID — the SDK's upsert mutation is keyed on `data["id"]` if set, else `data["hfid"]` (`.venv/…/infrahub_sdk/node/node.py:295-298`) | FR-013, AD017 |
| Convergence-key presence | Asserted at step 3b by the per-component rule above — direct components checked in `data`, relationship-crossing components checked in `data` **and** in the operation's nested `{peer_kind, identity}`; an unaccounted-for component raises rather than issuing an unkeyed write | FR-013, AD042, AD051 |
| Payload authority | Authoritative for the mapped fields it carries; **must not** touch unmapped destination fields. "Full" means complete with respect to the configuration's field mapping, not the destination schema | FR-028.4 |
| Cardinality-many | **Replace-set**, enforced explicitly at step 7 rather than assumed of the upsert (PD-005), and the enforcement **re-reads the destination peer set before comparing** so it cannot be a silent no-op (AD054). `peers: []` under `cardinality: "many"` means "empty the set", and the replace-set acts on it | FR-013, FR-028.2, AD054 |
| A create whose identity already exists | Converges onto the existing object through the same upsert. Whether that object's payload differs is not examined; conflict policies are out of scope | FR-013, AD025 |
| An update whose target was deleted out-of-band | Materializes as a create, because the upsert creates when no destination object matches the key. No conflict detection, freshness check or refusal path is built | FR-013, AD025 |
| Reporting | Either case is reported under the operation's **original** identifier and **original** action, so SC-005's review-to-apply link is unaffected | AD025 |

#### Why step 7 exists, and why it must re-read first (AD054)

AD015 cites `update_node` as the evidence that cardinality-many is replace-set — but `update_node` is
on the path AD015 forbids the planned write from using, and whether the *upsert* mutation replaces or
merges a relationship list cannot be determined without a live Infrahub (AD007). Step 7 makes the
semantics true by construction rather than assuming them.

**It must re-read the destination's peer set before comparing.** `_replace_relationship_set` as extracted
from `update_node` reads `attr_manager.peer_ids` and only then calls `fetch()`
(`infrahub_sync/adapters/infrahub.py:151` vs `:168-169`). A relationship manager reports itself
initialized from whatever data constructed it — `self.initialized = data is not None`
(`.venv/…/infrahub_sdk/node/relationship.py:264`) — and `fetch()` returns immediately when it is
(`:286-299`). So on a node built locally from the write payload, `peer_ids` **is the desired set**:
`compare_lists(existing, new)` compares that set against itself, both difference sets come back empty,
and the reconciliation is a guaranteed no-op that removes nothing. It can pass only against a mock.

The helper therefore:

```text
7a. fetch the relationship manager from the destination FIRST   # unconditional re-read
7b. existing = attr_manager.peer_ids                            # now the destination's actual set
7c. _, existing_only, new_only = compare_lists(existing, new_peer_ids)
7d. remove every existing_only; add every new_only
```

Two things follow. The extraction from `update_node` is **not** behavior-preserving in the ordering —
it corrects a pre-existing defect, since today's live update path adds without removing, and that
correction is in scope because the helper is shared and cannot be correct on one caller and wrong on the
other. And "if the upsert already replaces, step 7 is a no-op" becomes true for the right reason: the
difference sets are empty because the destination already holds the desired set, not because the
comparison never looked.

## `PeerResolver`

```python
class PeerResolver:
    def resolve(self, *, peer_kind: str, identity: dict[str, Any],
                referring_operation_id: str) -> str: ...
    def remember(self, kind: str, identity: dict[str, Any], node_id: str) -> None: ...
```

| Property | Rule | Requirement |
|---|---|---|
| Lifetime | One apply. Created at the start, discarded with it. Never persisted | FR-014 |
| Key | `(kind, canonical_identity(identity))` — the same canonical form the operation identifier hashes | FR-028.3 |
| Population | From each completed create/update (step 8), so an operation's own result resolves later operations that refer to it | FR-014 |
| Miss | Queries the destination (below) and memoizes the **successful** result | FR-014 |
| Negative caching | **Never.** A failed lookup and a failed write are not cached, so a later operation referring to the same peer re-attempts resolution rather than inheriting a negative result | FR-014, AD036 |
| No comparison store | The resolver never reads `client.store` or the DiffSync store — that is exactly the dependency `resolve_peer_node` has today (`infrahub_sync/adapters/infrahub.py:78,81`) and the one a saved-plan apply cannot satisfy | FR-014, DBR-007 |

### The destination query (PD-004)

The query is built from the **destination schema's own** `human_friendly_id` component paths
(`.venv/…/infrahub_sdk/schema/main.py:272`), which the adapter already caches wholesale
(`infrahub_sync/adapters/infrahub.py:345`):

| HFID component path | Value taken from | Filter kwarg |
|---|---|---|
| `<attr>__value` | the peer identity's scalar value under `<attr>` | `<attr>__value=<v>` |
| `<rel>__<attr>__value` | the **nested** `{peer_kind, identity}` the peer identity records under `<rel>` (AD043), read at `identity[<attr>]` | `<rel>__<attr>__value=<v>` |

A **schema path** is split; a **data value** never is. That distinction is the whole point: recovering
identifiers by splitting a DiffSync unique-id on `__` is the v1 flaw the brief names — and it is
precisely what the resolver would have to do if a peer identity component were a raw unique-id string,
because on a memo miss the resolver holds nothing but that identity mapping. AD043's recursive
`{peer_kind, identity}` shape is what makes the nested arm of this table constructible at all. Nesting
deeper than one level resolves by the same rule, recursively.

This matters on the qualified configuration, where **ten** schema-mapping entries carry a relationship
inside their identity — `LocationRack.site`, `DcimDeviceType.manufacturer`, `DcimDevice.location`
**twice**, `Interface{Physical,Virtual,Lag}.device`, `IpamVLAN.vlan_group`, `IpamPrefix.vrf`,
`IpamIPAddress.vrf` (enumerated in [research.md](../research.md) PD-004).

```python
results = client.filters(kind=peer_kind, **filter_kwargs)
```

| Result count | Behavior | Message names | Requirement |
|---|---|---|---|
| exactly 1 | return `results[0].id`, memoize | — | FR-014 |
| **0** | raise `PeerNotFoundError`; refuse the operation and fail the run | peer kind, peer identity, the referring operation identifier, **and the operator's next action** — create the peer at the destination, or re-plan so this plan creates it | FR-014, SC-016, AD059 |
| **> 1** | raise `PeerAmbiguousError`; refuse the operation and fail the run | peer kind, peer identity, the match count, **and the operator's next action** — de-duplicate at the destination, or narrow the mapping's identifiers so the identity is unique | FR-014, SC-016, AD059 |

Neither is ever a silent skip. "A silent skip would make the applied set differ from the reviewed set"
is the brief's own reasoning for DBR-016, and it governs a dropped relationship exactly as it governs a
dropped operation (AD016). It applies here **unchanged by AD055**: a dropped peer is not a designed
limitation of this release — nothing declares it out of scope, and its effect on the destination is a
half-written object — so it fails the run, where a recorded delete does not.

**Scope of the refusal (AD048).** These two refusals belong to **this resolver only** — the apply-path
resolver, which does not exist today. The live `sync` write path's existing warn-and-continue on an
unresolvable peer (`infrahub_sync/adapters/infrahub.py:141-143`, `:212-214`, `:229-231`) and the SDK's
bare `IndexError` on a multi-match (`.venv/…/infrahub_sdk/client.py:566`) are **unchanged**: they are
existing behavior on an existing path that this brief does not authorize touching. Nothing in this
outcome may alter them, and a test asserts they still hold.

**Fallback for a kind whose HFID does not cover its plan identity**: resolve the reference component's
own peer first (recursively, through the same resolver) to a destination id, then filter
`<rel>__ids=[<id>]`. FR-024 already warns at plan time when the HFID is absent or incomplete, so this
path is the degraded case that warning is about, not a silent alternative.

**Not verifiable offline**: the exact GraphQL filter spelling for a nested `<rel>__<attr>__value`
argument. AD007 records that no live Infrahub is reachable here. It is asserted by the
`integration`-marked SC-008 and SC-016 tests, and the failure mode if the spelling is wrong is a zero
match — a loud refusal, never a silent drop.

### Tier ordering and its limits

Dependency-tier ordering guarantees a peer is written before anything referring to it, **for
references carried in the computed dependency graph**. Three cases the existing tier machinery cannot
express (`infrahub_sync/dependency_graph.py:33-34`, `:81-100`; `infrahub_sync/__init__.py:132-133`):

- a self-reference, excluded from write-order edges;
- a reference reachable only through an optional edge dropped to break a cycle;
- any reference in a configuration that supplies an explicit `order:`, which yields no tiers at all.

In those cases the peer may be unresolved at apply, and the zero-match arm above governs. The
qualification is safe precisely because the miss is loud (AD022).

## The apply loop

In `Potenda.apply_plan`, replacing the v1 body:

```text
1. load the artifact; classify v1 / torn / version (contracts/plan-reader-api.md).
   An action outside ACTIONS is refused HERE, before any write:
   UnsupportedOperationActionError → run state failed          # FR-017, AD055
2. run the five verification checks + the write-surface check   → refuse before any write
   verify_plan(..., write_surface_missing_on=<adapter name> or None)          # AD058
3. peers = PeerResolver(adapter)
4. applied: list[str] = []          # ORDERED — FR-020
   skipped_deletes: list[str] = []  # ORDERED — FR-017, AD055
5. for operation in stored order:          # tier, then operation_id
       if operation.action == "delete":
           skipped_deletes.append(operation.operation_id); continue   # FR-016, FR-017
       try:
           adapter.apply_planned_operation(operation=operation, peers=peers)
       except (PeerNotFoundError, PeerAmbiguousError, DestinationRejected) as exc:
           record the summary keys below as they stand, then run state failed
           naming operation.operation_id, exc and the next action; STOP    # AD027, AD059
       applied.append(operation.operation_id)
6. summary["applied_operations"]         = applied            # FR-020, AD062
   summary["skipped_delete_operations"]  = skipped_deletes    # FR-017, AD055
   summary["skipped_delete_count"]       = len(skipped_deletes)
7. if skipped_deletes:
       warn, at operator-visible level, naming len(skipped_deletes)        # AD055
   run state applied                     # ALWAYS — a skipped delete is a designed
                                         # limitation, never a run failure (AD055)
```

| Rule | Requirement |
|---|---|
| Stored order is executed exactly — no re-sorting, no recomputation, no extraction of either side | FR-012, SC-001 |
| `applied` is an **ordered** sequence; FR-025's "last operation reported as applied" is its final element, not a separate field | FR-020, AD036 |
| The record's home is `summary[…]` on the run file, not a new persisted field: `RunFile.KEYS` is closed and `summary` is already `dict[str, Any]` (`infrahub_sync/cache/sidecars.py:73`, `:76`), so the `cache/` layer stays unchanged | FR-020, AD062 |
| A delete-bearing plan ends **`applied`**, with a non-zero `skipped_delete_count`, the skipped identifiers, and a warning naming the count. It does **not** end `failed` | FR-016, FR-017, SC-007, AD055 |
| `applied` ∪ `skipped_deletes` equals the plan's identifier set on any completed apply — which is what makes the applied set knowable against the reviewed set as a value rather than an inference (DBR-016's actual protection) | FR-017, FR-020, AD055 |
| An operation whose action is outside `ACTIONS` is the **only** class that fails the run for being unsupported, and it is refused at load, before any write | FR-017, AD055 |
| A destination rejection or transport failure stops at that operation; what was written stays written; no rollback; the summary keys are recorded as they stand so a partial apply is still readable | AD027, FR-025 |
| The partial-apply record is best-effort and explicitly **not** required to survive abnormal process termination | FR-025, AD011 |
| An empty plan applies as a successful no-op — but verification still runs first | FR-022, AD033 |
| A delete is never executed, and not because of a configuration setting: deletes never enter the comparison result the write path consumes, so the divergence is structural | FR-016, AD004 |
| An apply that skipped deletes records `applied`, which the incremental path's success set already contains (`infrahub_sync/cache/incremental.py:24`), so it counts as a successful prior run for a later warm start. Correct, and recorded rather than discovered | AD055 |

## Test split

| Evidence | Where | Marker |
|---|---|---|
| Payload construction, upsert invocation, replace-set reconciliation with its re-read, memo population, negative-caching refusal, both peer refusals, a delete collected and skipped, an unrecognized action refused at load, missing surface, ordered applied set and skipped-delete record, fail-fast on rejection | `tests/adapters/test_infrahub_planned_write.py`, mocked `InfrahubClientSync` | local |
| **Rendered-mutation conformance (AD045a, rebuilt by AD054)**: the **rendered mutation input** carries `id` or `hfid` for every operation, built against a **committed `NodeSchemaAPI` fixture** rather than a mock; the replace-set reconciliation re-reads the destination peer set before comparing; a repeated operation producing no second create | `tests/plan/test_apply_conformance.py`, real `InfrahubNodeSync` over a committed schema fixture | local |
| SC-001 (no diff/sync call), SC-002 (converge on re-apply), SC-003 (per-class matrix, both crash windows), SC-007 (live counts before/after, delete targets surviving, run state `applied`, recorded skip count), SC-008 (peer sets read back, at least one peer pre-existing and absent from the plan), SC-016 (real ambiguity) | `tests/integration/test_saved_plan_apply_integration.py` | **`integration`** (`pyproject.toml:133-135`) |

**Why the conformance row was rebuilt (AD054).** As first written it asserted against the *assembled*
`data` and against a wholly mocked SDK, which makes two of its three assertions unfalsifiable. Keyedness is
a property of the **rendered** mutation, not of `data`: by the time `data` is complete, a
relationship-crossing HFID component is a resolved node-id string from which no attribute can be read, so
"every component accounted for in `data`" can hold while the mutation still goes out with neither `id` nor
`hfid`. And a mock holds no destination state, so "a repeated operation produces no second create" and "the
peer set was replaced" cannot fail for the right reason — two applies against a mock simply issue two
creates. The rebuilt harness constructs a real node from a committed schema fixture and asserts the
rendered input, which is checkable offline because the SDK renders the mutation locally
(`.venv/…/infrahub_sdk/node/node.py:295-298`, `:1843-1846`).

**The live row is deferred evidence, not produced evidence (AD045b).** No Infrahub is reachable in the
development environment (AD007), so DBA-001, DBA-002, DBA-003 and DBA-008, and the live halves of
DBA-007 and SC-016, do not have passing evidence at merge time and the brief's completion condition is
not met. The conformance row above narrows what that deferral can hide — in its rebuilt form it would have
caught AD042's defect offline — but it does not close it.
