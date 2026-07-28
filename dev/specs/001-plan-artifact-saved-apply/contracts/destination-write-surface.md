# Contract: the destination planned-write surface and apply-time peer resolution

**Requirements**: FR-013, FR-014, FR-016, FR-017, FR-020, FR-023, FR-025. **Ratified corrections applied
here**: AD054 (the replace-set re-read and the rendered-mutation harness), AD055 (a recorded delete ends
`applied` with a recorded skip count, not `failed`), AD058 (the resolver's declared entry point and the
verifier's adapter-name argument), AD059 (every failure names a next action), AD062 (the apply record's
named home in the run summary), AD065 (the re-read is a named mechanism, not a call order), AD066 (the
keyedness gate reads the rendered mutation and the flat guarantee is struck), AD067 (the conformance
keyedness assertion is split), AD068 (byte-identical rendered inputs replace "one create"), AD069 (one
writer for the run record), AD070 (the enforcement is new code; the live write path is untouched).
**Scope**: implemented on
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
| A relationship manager reports itself initialized from whatever data built it — `self.initialized = data is not None` — and `fetch()` returns immediately once initialized, so a locally built node reports the **desired** peer set as its **existing** one. The `client.get` that would read the destination lives **inside** that guard, which is why calling `fetch()` earlier reads nothing (AD065) | `.venv/…/infrahub_sdk/node/relationship.py:264`, guard `:286-288`, the read it guards `:290-296` |
| `update_node`'s only caller is `InfrahubModel.update` — the live `sync` write path — so any change to its ordering is a change to what the existing mutating command does to destination relationships (AD070) | `infrahub_sync/adapters/infrahub.py:625` |
| `RunFile.save()` writes the whole payload from the in-memory instance with no merge, and the `apply` command constructs its `RunFile` with an empty `summary` and saves it again after the apply returns (AD069) | `infrahub_sync/cache/sidecars.py:87-89`; `infrahub_sync/cli.py:322-323`, `:350-351` |
| The rendered mutation input is where keyedness is observable: `data["id"]` if set, else `data["hfid"]`, and the upsert path renders with `exclude_hfid=False` | `.venv/…/infrahub_sdk/node/node.py:295-298`; `create(allow_upsert=True)` `:1843-1846`; `save(allow_upsert=True)` dispatches to it `:1533-1535` |
| `RelationshipManagerSync` has **no `save`**: `add` and `remove` only mutate `self.peers` and set `_has_update`, issuing no client call, so a reconciled peer set reaches the destination only on a later save of the **node** (AD075) | `.venv/…/infrahub_sdk/node/relationship.py`: no `def save` in the class (`:238`), `add` `:322-332`, `remove` `:339-357`, flag at `:57` |
| `node.update(do_full_update=True)` after step 6 flushes it as an update carrying the reconciled peer list, because `do_full_update=True` renders with `exclude_unmodified=False` and the unmodified-field stripping never runs; the manager renders the full peer list, which is what makes the write a replace, and `id` is still rendered, so the update targets that node (AD085, amending AD075) | `.venv/…/infrahub_sdk/node/node.py:1870` (`exclude_unmodified=not do_full_update`), `:290-291` (the stripping runs only under `if exclude_unmodified:`), `:295-296` (`id`), `:1872` (`f"{kind}Update"`); full render at `relationship.py:68-69` |
| A **plain** `node.save()` would drop an **emptied** peer set. It renders with the stripping on, and `_strip_unmodified`'s second loop pops any key whose rendered value equals the create payload's: `generate_payload_create` writes `[]` for a cardinality-many relationship, so `data[item] == original_data[item]` is `[] == []` and the key is popped, because a relationship manager is not an `Attribute` and the guard passes. Non-empty replaces are unaffected (AD085) | `.venv/…/infrahub_sdk/node/node.py:365-370` (equal-payload pop, `Attribute` guard at `:368-370`); `.venv/…/infrahub_sdk/schema/__init__.py:179` (the create payload's `[]`) |
| `_generate_input_data(...)["data"]` is `{"data": {…}}`, **not** the rendered `data` — the gate must read one level deeper (AD076) | `.venv/…/infrahub_sdk/node/node.py:300`, `:304-308` |
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
| Warning | One warning on the run's log stream **naming the count** — not a debug line and not a per-operation trace: one message an operator reading the run's output cannot miss. **The level is `logging.WARNING`**, pinned rather than described as "operator-visible": `--quiet` floors the package logger at `logging.WARNING` (`infrahub_sync/cli.py:29`, the `--quiet` shorthand at `:59-60`, `_setup_logging` at `:41-48`), so an `INFO`-level emission would satisfy every prose description of this row and vanish for exactly the scripted and CI invocations where this warning and the run record are the only signals. The test asserts the level, not only the text |
| Completion line | When the count is non-zero, the command's own completion line names it — today's terminal line is a bare `logger.info("Applied run %s", …)` (`infrahub_sync/cli.py:352`) emitted after the warning, so on a long apply the last thing an operator reads says only "Applied". It must read as, for example, `Applied run <id>: 33 operations applied, 4 deletes skipped` |
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
3b. DIAGNOSTIC: every component path of node_schema.human_friendly_id is ACCOUNTED FOR (rules below)
       -> otherwise raise, naming the kind and the missing component        # AD051 — names WHICH component
4. create_data = client.schema.generate_payload_create(
       schema=node_schema, data=data,
       source=source_node.id, owner=owner_node.id, is_protected=True)       # parity with :608-610
5. node = client.create(kind=operation.kind, data=create_data)
5b. GATE: rendered = node._generate_input_data(exclude_hfid=False)["data"]["data"]  # AD066 / AD076
       #   NOT ["data"] alone: that is {"data": {…}}, a one-key mapping, so the test below would be
       #   true for EVERY operation and the raising arm would fire on all of them (node.py:300, :304-308)
       if "id" not in rendered and "hfid" not in rendered:
           all-direct HFID       -> raise: the payload lost its identity components (the AD042 class)
           relationship-crossing -> WARN ONCE PER KIND (content and level below), and proceed
           NO HFID declared      -> WARN ONCE PER KIND (content and level below), and proceed   # AD076
6. node.save(allow_upsert=True)                                             # the convergence point
7. for ref in operation.relationships where cardinality == "many":
       _replace_relationship_set(node, ref.field, resolved_peer_ids)        # PD-005 + AD054/AD065 re-read
                                                                            # NEW code; update_node untouched (AD070)
7e. node.update(do_full_update=True)
                  # AD075 as amended by AD085 — THE FLUSH. remove()/add() are purely local, so
                  #   without this step the whole reconciliation is computed and discarded. An
                  #   UPDATE, not a second save(allow_upsert=True) — and do_full_update=True, NOT
                  #   a plain node.save(), which strips an EMPTIED peer set out of the render.
                  #   One write after the loop, not one per relationship.
                  #   `node` is THIS node — the one step 7 reconciled the managers OF. Writing any
                  #   other object reproduces the AD075 defect silently.
                  #   See "Step 7e" below for why this form is the correct one.
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

### Step 5b: where keyedness is actually observable, and what the gate can promise (AD066)

Step 3b is a **diagnostic**, not the keyedness gate, and the difference was papered over by a guarantee the
check could not deliver. Keyedness is a property of the **rendered mutation input**: the SDK sets
`data["id"]` if the node has one, else `data["hfid"]` when `exclude_hfid` is false
(`.venv/…/infrahub_sdk/node/node.py:294-297`), and the upsert path renders with `exclude_hfid=False`
(`:1843-1844`), dispatching through `save(allow_upsert=True)` (`:1533-1534`). All of that is client-side, so
the render is readable with no server. Step 3b can hold while that render carries neither key — which is
exactly the case the fact table below documents — so the gate is read where the property lives.

**Read the render one level deeper than it first appears (AD076).** `_generate_input_data` returns
`{"data": mutation_payload, "variables": …, "mutation_variables": …}` where `mutation_payload` is itself
`{"data": data}` (`.venv/…/infrahub_sdk/node/node.py:300`, `:304-308`). So `…["data"]` is a **one-key
mapping** and `"id" not in rendered` is true for every operation ever rendered — the all-direct arm would
raise on all of them. The gate reads `…["data"]["data"]`. This is written as executable pseudocode in four
places and is corrected in all four.

The gate branches on the destination kind's HFID shape, and the branch is the whole point:

| HFID shape | Rendered input carries neither `id` nor `hfid` | Why |
|---|---|---|
| **All direct** components | **Raise**, naming the kind | It can only mean the payload lost its identity components — the AD042 defect class, and always a defect |
| **Crosses a relationship** | **Warn once per destination kind** (content and level below) and proceed | It is the expected render today for a reason this outcome does not control (the fact table below): the SDK cannot form the `hfid` from a peer supplied as a resolved id. Refusing would withdraw the ten identity-bearing-reference mapping entries of the qualified configuration from what this outcome delivers — the relationship-bearing capability DBR-013 and DBA-008 require — and the convergent write may still key server-side, which only live evidence can settle |
| **No HFID declared** (absent or empty) | **Warn once per destination kind** (content and level below) and proceed — **never raise** (AD076) | This kind matches neither row above, and under the natural implementation ("every component is direct" over an empty list) it would fall into the raising arm and be refused with the message that arm fixes — "the payload lost its identity components" — which is **false**, because nothing went missing. FR-024 explicitly contemplates a destination kind that declares no human-friendly ID and requires the plan run to survive it, so refusing here would decline a configuration class the specification tolerates. The narrowed guarantee below excludes it in its own words: for such a kind, unkeyed is a **schema fact**, not a defect |

**What the report says, and at what level (AD078).** Both warning rows are one obligation, and it is
pinned rather than described, because "operator-visible" is the exact phrase this run already ruled
insufficient for the sibling skipped-delete warning:

| Property | Rule |
|---|---|
| Level | **`logging.WARNING`.** Pinned for the same reason as the delete warning above: `--quiet` floors the package logger at `logging.WARNING` (`infrahub_sync/cli.py:29`), so an `INFO` emission satisfies every prose description of this row and vanishes for exactly the scripted and CI invocations where this report is the only signal. The test asserts `record.levelno >= logging.WARNING`, not only the text |
| Content | The **destination kind**; that the write **was issued anyway**; and **what to watch for** — a duplicate object of that kind at the destination if the destination does not key on the components as sent. Plus which of the two conditions applies (the convergence key crosses a relationship, or the kind declares none). "Naming the recorded risk" is **not** sufficient content: the recorded risk is a row in [plan.md](../plan.md#risks), an artifact the operator does not have |
| Cardinality | **Once per destination kind**, not once per operation — per operation would put one line per row on a four-thousand-operation apply, the same drowning failure the run-id enumeration bound exists to prevent |
| Where the dedup state lives | A set of already-reported destination kinds on the **adapter instance**, created at the start of an apply and discarded with it — the same lifetime as `PeerResolver`'s memo. `apply_planned_operation` is per-operation, so "once per kind" cannot be local to it |
| Documentation | The docs sweep states plainly that convergence is **not verified in this release** for destination kinds whose convergence key crosses a relationship, on the same footing as the delete limitation (T069) |

**The flat claim is struck.** "An unkeyed write is never issued" appeared in three places and was false for
the second row above. What these two checks deliver, stated once and repeated nowhere in a stronger form:
**no write is issued whose payload is missing an HFID component, and no render is issued unkeyed where being
unkeyed can only be a defect.** The warning is per kind rather than per operation so it discloses without
drowning a large apply, and the same condition is carried as a strict expected failure in the offline
conformance harness (AD067), so the limitation retires itself when the hole closes.

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
The mitigation available offline is narrower than "no unkeyed write is ever issued", and is stated as what it
is (AD066): an operation whose HFID components cannot be accounted for raises at step 3b instead of
duplicating silently; an operation whose render comes back unkeyed for a kind with an **all-direct** HFID
raises at step 5b; and an operation whose render comes back unkeyed for a **relationship-crossing** HFID —
this case — is **warned about once per kind and issued**, because refusing it would withdraw
relationship-bearing kinds from what this outcome delivers. The residual —
whether an accounted-for nested component actually keys the server-side upsert — is unverifiable
without a live Infrahub (AD007) and is asserted by the `integration`-marked SC-002 and SC-003 tests, with
the offline harness carrying it as a strict expected failure in the meantime (AD067).

| Clause | Rule | Requirement |
|---|---|---|
| Convergence key | The destination kind's human-friendly ID — the SDK's upsert mutation is keyed on `data["id"]` if set, else `data["hfid"]` (`.venv/…/infrahub_sdk/node/node.py:295-298`) | FR-013, AD017 |
| Convergence-key presence | Two checks, not one (AD066). Step 3b is the **diagnostic**: direct components checked in `data`, relationship-crossing components checked in `data` **and** in the operation's nested `{peer_kind, identity}`; an unaccounted-for component raises, naming which one. Step 5b is the **gate**, read on the rendered mutation input **one level deeper than the render call's own `"data"` key** (AD076): unkeyed raises for an all-direct HFID, and warns once per kind — at a pinned level, with pinned content (AD078) — for a relationship-crossing one **and for a kind that declares no HFID at all** (AD076) | FR-013, AD042, AD051, AD066, AD076, AD078 |
| Payload authority | Authoritative for the mapped fields it carries; **must not** touch unmapped destination fields. "Full" means complete with respect to the configuration's field mapping, not the destination schema | FR-028.4 |
| Cardinality-many | **Replace-set**, enforced explicitly at step 7 rather than assumed of the upsert (PD-005). The enforcement **issues its own destination read of the peer set before comparing**, so it cannot be a silent no-op (AD054, AD065), **and it issues a destination write carrying the reconciled peer list at step 7e**, because the peer-set editors are purely local and a reconciliation that is never saved is discarded (AD075). `peers: []` under `cardinality: "many"` means "empty the set", and the replace-set acts on it. The enforcement is **new code on this path**; the pre-existing `update_node` is untouched (AD070) | FR-013, FR-028.2, AD054, AD065, AD070, AD075 |
| A create whose identity already exists | Converges onto the existing object through the same upsert. Whether that object's payload differs is not examined; conflict policies are out of scope | FR-013, AD025 |
| An update whose target was deleted out-of-band | Materializes as a create, because the upsert creates when no destination object matches the key. No conflict detection, freshness check or refusal path is built | FR-013, AD025 |
| Reporting | Either case is reported under the operation's **original** identifier and **original** action, so SC-005's review-to-apply link is unaffected | AD025 |

#### Why step 7 exists, and why it must re-read first (AD054)

AD015 cites `update_node` as the evidence that cardinality-many is replace-set — but `update_node` is
on the path AD015 forbids the planned write from using, and whether the *upsert* mutation replaces or
merges a relationship list cannot be determined without a live Infrahub (AD007). Step 7 makes the
semantics true by construction rather than assuming them.

**It must re-read the destination's peer set before comparing.** The pre-existing shape in `update_node`
reads `attr_manager.peer_ids` and only then calls `fetch()`
(`infrahub_sync/adapters/infrahub.py:151` vs `:168-169`). A relationship manager reports itself
initialized from whatever data constructed it — `self.initialized = data is not None`
(`.venv/…/infrahub_sdk/node/relationship.py:264`) — and `fetch()` returns immediately when it is
(`:286-299`). So on a node built locally from the write payload, `peer_ids` **is the desired set**:
`compare_lists(existing, new)` compares that set against itself, both difference sets come back empty,
and the reconciliation is a guaranteed no-op that removes nothing. It can pass only against a mock.

**And "fetch first" does not fix that (AD065).** This contract previously prescribed
`7a. fetch the relationship manager from the destination FIRST`, which performs no destination read at all:
`fetch()` opens with `if not self.initialized:` (`.venv/…/infrahub_sdk/node/relationship.py:286-288`) and the
`client.get` that would do the reading lives **inside** that guard (`:290-296`). On the node step 7 holds,
the guard is false, `fetch()` walks the locally built peers and returns, and the comparison still compares
the desired set against itself. The defect in that prescription is worth naming: it described the fix as an
**ordering** when the property at stake is **whether a read happens**, so both the change and a test written
against it would have passed while nothing changed.

The helper therefore, with the mechanism named rather than implied:

```text
# 7a–7d are the helper's body, run once PER cardinality-many relationship:
7a. rm = getattr(node, ref.field)           # the manager ON THE NODE THE CALLER WILL SAVE
    rm.initialized = False                  # discard the locally constructed peer set …
    rm.fetch()                              # … so the guarded client.get actually runs, and the peers it
                                            #   returns land back on THIS manager (relationship.py:290-299)
7b. existing = rm.peer_ids                                      # now the destination's actual set
7c. _, existing_only, new_only = compare_lists(existing, new_peer_ids)
7d. remove every existing_only; add every new_only               # in memory ONLY — see 7e
    # the helper RETURNS HERE, leaving the node unsaved (like update_node does)

# 7e is the CALLER's, run ONCE after the loop over every cardinality-many relationship:
7e. node.update(do_full_update=True)
                     # AD075/AD085 — the flush. An update, not save(allow_upsert=True); and
                     #   do_full_update=True, not a plain node.save().
                     #   `node` is the SAME OBJECT 7a reconciled — see the invariant below
```

**The invariant: the node that is updated MUST be the node whose manager was reconciled.** Forcing the
manager cold and calling `fetch()` is therefore the **only** prescribed re-read mechanism, and this
invariant is why. An earlier form of this contract offered a second mechanism as equivalent — fetch a
separate `node2 = client.get(id=node.id, kind=node._schema.kind, include=[ref.field])` and reconcile
`getattr(node2, ref.field)`. **The two stopped being equivalent the moment the flush became the caller's
responsibility (AD075)**, and under the second one this section silently reproduces the very defect AD075
closes: `add`/`remove` land on *`node2`*'s manager, while the `node` the caller flushes still holds the
manager built from the create payload, so the flush renders that payload's peer list back — the plan's
**desired** set, never compared against anything — and the reconciliation is computed and discarded. What
reaches the destination is then whatever the mutation does with a peer list on its own, which is exactly
the question AD007 says cannot be settled offline and PD-005 exists so as not to depend on. Two smaller facts finish the
case: the helper returns nothing, so the caller cannot reach `node2` to save it instead; and one `node2` per
relationship is incompatible with the single post-loop flush 7e pins. Nothing is lost by dropping it —
the SDK's own `fetch()` on a cold manager issues exactly that scoped `client.get` and then assigns the
fetched peers back onto the manager it was called on
(`.venv/…/infrahub_sdk/node/relationship.py:290-299`), so the surviving mechanism **is** the dropped one
plus the write-back this invariant requires.

"Call `fetch()` earlier" remains unacceptable for the separate reason AD065 gives. **The observable the
tests assert is that a destination read was *issued* for that relationship before the peer set was read** —
not that the manager was fetched, which the no-op satisfies.

#### Step 7e: the reconciliation is inert until it is flushed, and the flush is a full update (AD075, AD085)

`RelationshipManagerSync` has `fetch`, `add`, `remove` and `extend` — and **no `save`**. Both editors are
purely local: they mutate `self.peers` and set `_has_update = True`, and neither issues a client call
(`.venv/…/infrahub_sdk/node/relationship.py`: `add` `:322-332`, `remove` `:339-357`, the flag exposed at
`:57`). The reconciled set reaches the destination **only on a subsequent write of the node**. Without step
7e the helper computes the surplus correctly and throws it away.

That is exactly how the pre-existing shape behaves, which is why the omission was easy to specify: the
module-level `update_node` ends `return node` **unwritten** (`infrahub_sync/adapters/infrahub.py:177`) and its
caller flushes it — `InfrahubModel.update` calls it at `:625` then `node.save(allow_upsert=True)` at `:626`.
Duplicating that function into a call site with no write after it is what leaves the reconciliation inert.

**Why an update and not `save(allow_upsert=True)`**, verified end to end against the SDK:

| Step in the chain | Fact | Location |
|---|---|---|
| Step 6 marks the node existing | `_process_mutation_result` sets `self.id` then `self._existing = True` | `.venv/…/infrahub_sdk/node/node.py:1810-1811` |
| The flush is an update mutation, not a second upsert | `update()` names the mutation `f"{kind}Update"` | `:1872` |
| A second `save(allow_upsert=True)` would be wrong | it re-enters `create(allow_upsert=True)` and re-renders the **upsert create** instead of an update | `:1533-1534` → `:1838-1846` |
| The manager renders the **full** peer list, which is what makes the write a replace | the manager renders every peer it holds; an emptied set renders `[]` | `.venv/…/infrahub_sdk/node/relationship.py:68-69` |

**Why `do_full_update=True` and not a plain `node.save()` (AD085).** A plain save dispatches `update()` with
`do_full_update` defaulting to `False`, which renders with `exclude_unmodified=True` — and that stripping
drops an **emptied** peer set, so `peers: []` never reaches the destination. AD075 pinned the plain save on
the strength of a mechanism that turned out to be only half the picture; the amendment is below, and it
changes only which flush is issued.

| Step in the chain | Fact | Location |
|---|---|---|
| `do_full_update=True` turns the stripping off | `update()` calls `_generate_input_data(exclude_unmodified=not do_full_update)` | `.venv/…/infrahub_sdk/node/node.py:1870` |
| …and `_strip_unmodified` then never runs at all, so the emptied set survives into the mutation | it is called only under `if exclude_unmodified:` | `:290-291` |
| …while `id` is still rendered, so the update targets the right node | `if self.id is not None: data["id"] = self.id` | `:295-296` |
| Under a plain save, the **first** loop does **not** pop the emptied manager — AD075 was right about this arm | a manager defines neither `__bool__` nor `__len__`, so it is always truthy and the `not relationship_property` guard never fires for it; the `has_update` arm keeps it because the reconciliation set the flag | `:354-364`, guard at `:356`, `has_update` arm at `:362` |
| …but the **second** loop pops it, and that is the actual collision | with the create payload's `[]` for the same field, `data[item] == original_data[item]` is `[] == []`, and the pop fires because a relationship manager is not an `Attribute` so the guard passes | `:365-370`, `Attribute` guard at `:368-370`; the create payload's `[]` at `.venv/…/infrahub_sdk/schema/__init__.py:179` |
| The differing-payload path is **not** where it is lost | `_strip_unmodified_dict` is dispatched only under `isinstance(original_data[item], dict)`, and a cardinality-many relationship is written as a **list**, so that branch is never reached and the key survives | `:372`; `.venv/…/infrahub_sdk/schema/__init__.py:179` |

Non-empty replaces are unaffected either way — for them AD075's mechanism holds — so the amendment is
scoped to the empty-set case and to which call issues the flush.

**And it is an update of the reconciled node.** The invariant stated with the helper above governs this step
too: the flush renders the managers that hang off *`node`*, so a re-read mechanism that reconciles a manager
on any other object leaves this write carrying the create payload's peer list — the desired set, never
compared against the destination's. That is why the mechanism is pinned rather than left to the
implementer's choice.

**One write after the loop, not one per relationship.** The flush is issued once, after every
cardinality-many relationship on the operation has been reconciled — cheaper, and equally correct, since
one update carries every changed relationship. Pinning it matters so the tests know what to count.

**The observable for step 7 as a whole is the issued destination write carrying the reconciled peer
list** — not the manager's in-memory `peer_ids`, and not a mocked adapter call. This is the same correction
AD065 made for the read side, and it is what makes the fix verifiable: every earlier observable (a mocked
assertion that "existing-only peers are removed", a conformance assertion that "the surplus is removed", a
done-condition on manager state) is satisfied by a helper that reconciles and never writes. The empty-set
case is the one that decides between the two flush forms, so its assertion is on the **rendered mutation**.
Because the stripping is undocumented SDK internals and `pyproject.toml:18` pins `infrahub-sdk[all]>=1.17,<2`
— a range — an **SDK-boundary tripwire** accompanies it: a test that fails loudly, naming AD085, if
`_generate_input_data(exclude_unmodified=False)` stops retaining an emptied cardinality-many relationship or
`exclude_unmodified=True` stops stripping it on the equal-payload path.

**Why this failure had to be caught before implementation.** It is co-extensive with the risk step 7
exists for. Where the convergent write already **replaces** the peer set, the re-read finds no difference,
there is nothing to flush, and the omission is invisible — the feature works by accident. Where it
**merges** — the case AD007 says cannot be settled offline and PD-005 exists for — the surplus is computed
correctly and discarded, so the mitigation is void precisely when it is needed. And the only criterion that
would catch it, SC-008, is behind the `integration` marker and is not produced at merge.

**This is new code on this path, and `update_node` is left exactly as it is (AD070).** An earlier form of
this contract had the helper *extracted* from `update_node` and shared, with its ordering corrected for both
callers. That reaches the live `sync` write path: `update_node`'s only caller is `InfrahubModel.update`
(`infrahub_sync/adapters/infrahub.py:625`), so correcting it there would make `infrahub-sync sync` start
**removing** destination relationship peers absent from the source, on configurations that have never
removed one. That is a data-removing change to an existing command, unauthorized by this outcome and
described by no requirement, criterion or documentation entry — and it directly contradicted the AD048 scope
rule two sections below, which this same contract states. So the shape is written a second time here, the
pre-existing additive ordering is recorded as a **pre-existing defect for a later outcome to own**, and the
duplication is the deliberate price of leaving the live path untouched.

One thing still follows as before: "if the upsert already replaces, step 7 is a no-op" becomes true for the
right reason — the difference sets are empty because the destination already holds the desired set, not
because the comparison never looked.

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
           attach the PARTIAL record (applied, skipped_deletes) to the raised error,   # AD069
           then raise naming operation.operation_id, exc and the next action; STOP
           # the CLI merges the partial record, then records run state failed  # AD027, AD059
       applied.append(operation.operation_id)
6. AFTER the loop, on a COMPLETED apply, check the knowability invariant   # AD069, post-loop
       set(applied) | set(skipped_deletes) == plan identifier set
       len(applied) + len(skipped_deletes) == manifest.operations_count
   — raise a named error if either fails; neither holds for a partial apply, which is
     why this is not evaluated inside the loop nor on the rejection path
7. if skipped_deletes:
       logger.warning(... naming len(skipped_deletes) ...)                 # AD055, level pinned
8. RETURN the record; apply_plan writes no run file                        # AD069
   applied_operations        = applied            # FR-020, AD062
   skipped_delete_operations = skipped_deletes    # FR-017, AD055
   skipped_delete_count      = len(skipped_deletes)
   # The CLI merges these into run_file.summary under those key names and saves,
   # then records run state applied — ALWAYS on a completed apply, because a skipped
   # delete is a designed limitation and never a run failure (AD055) — and names the
   # skipped count on its completion line when it is non-zero.
```

| Rule | Requirement |
|---|---|
| Stored order is executed exactly — no re-sorting, no recomputation, no extraction of either side | FR-012, SC-001 |
| `applied` is an **ordered** sequence; FR-025's "last operation reported as applied" is its final element, not a separate field | FR-020, AD036 |
| The record's home is `summary[…]` on the run file, not a new persisted field: `RunFile.KEYS` is closed and `summary` is already `dict[str, Any]` (`infrahub_sync/cache/sidecars.py:73`, `:76`), so the `cache/` layer stays unchanged | FR-020, AD062 |
| **One writer.** `apply_plan` returns the record and writes no run file; the CLI merges it into `run_file.summary` before saving. `RunFile.save()` writes the whole payload with no merge (`:87-89`) and the CLI's instance is built with an empty `summary` (`infrahub_sync/cli.py:322-323`) and saved after the apply returns (`:350-351`), so two writers means the engine's keys are deleted. A mid-apply rejection carries its partial record on the raised error so the CLI can merge it before recording `failed` | FR-020, FR-025, AD069 |
| A delete-bearing plan ends **`applied`**, with a non-zero `skipped_delete_count`, the skipped identifiers, and a warning naming the count. It does **not** end `failed` | FR-016, FR-017, SC-007, AD055 |
| `applied` ∪ `skipped_deletes` equals the plan's identifier set on any **completed** apply, and the two lengths sum to `operations_count` on the same condition — which is what makes the applied set knowable against the reviewed set as a value rather than an inference (DBR-016's actual protection). Both clauses are checked **after** the loop and **not** on the rejection path: a partial apply breaks both by construction, so an unconditioned check would replace a clear destination-rejection message with an invariant error | FR-017, FR-020, AD055, AD069 |
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
| **Rendered-mutation conformance (AD045a, rebuilt by AD054, sharpened by AD065/AD067/AD068/AD075)**: the **rendered mutation input** carries `id` or `hfid` — required for every operation on a kind whose HFID is **all-direct**, and carried as a `xfail(strict=True)` for a kind whose HFID **crosses a relationship**, which cannot render keyed today (AD067) — built against a **committed `NodeSchemaAPI` fixture** rather than a mock; the replace-set reconciliation **issued a destination read** for the relationship before reading the peer set it compares against (AD065); the reconciled peer set was **issued to the destination** by the step 7e flush — `node.update(do_full_update=True)` on the reconciled node, rendering as an **update** carrying that peer list, not a second upsert, and carrying it for an **emptied** set too (AD075, AD085); two applies of one operation render **byte-identical** inputs (AD068) | `tests/plan/test_apply_conformance.py`, real `InfrahubNodeSync` over a committed schema fixture | local |
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

**Four further corrections to what it asserts.** **(AD065)** The replace-set observable is that a
destination **read was issued** for the relationship, not that the manager was fetched — a `fetch()` on an
already-initialized manager satisfies the latter and reads nothing. **(AD075, AD085)** The other half of the same
observable: the reconciled peer set must be shown to have been **issued to the destination**, by asserting
the step 7e flush — `node.update(do_full_update=True)` on the node whose manager was reconciled, rendering
as an **update** whose relationship value is the reconciled peer list, and failing against a helper that
never flushes, against a flush spelled `save(allow_upsert=True)`, and — for an **emptied** peer set — against
a flush spelled as a plain `node.save()`, which strips the empty list back out. Without it the harness
passes on an enforcement that computes the surplus and discards it, which is the AD075 defect. **(AD067)** The keyedness assertion is
split, because the harness is *required* to include a kind whose HFID crosses a relationship and that kind
cannot render keyed today: all-direct kinds must render keyed, and the relationship-crossing kind carries the
same assertion marked `xfail(strict=True)` with a reason citing the Material risk row in
[plan.md](../plan.md#risks) — so it reports the limitation today and turns into a suite failure the day the
limitation is gone, instead of the risk table quietly going stale. **(AD068)** "Two applies produce exactly
one create" is replaced by "two applies render **byte-identical** mutation inputs", keyed wherever keyedness
is assertable: a mock holds no destination state, there is no operation-level deduplication in this design
and none is wanted, so the old form could never fail. Byte-identity is the strongest claim that is checkable
offline and is the property convergence actually rests on.

**The live row is deferred evidence, not produced evidence (AD045b).** No Infrahub is reachable in the
development environment (AD007), so DBA-001, DBA-002, DBA-003 and DBA-008, and the live halves of
DBA-007 and SC-016, do not have passing evidence at merge time and the brief's completion condition is
not met. The conformance row above narrows what that deferral can hide — in its rebuilt form it would have
caught AD042's defect offline — but it does not close it.
