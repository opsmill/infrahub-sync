# Engineering lens — round 3

**Feature**: `001-plan-artifact-saved-apply-infp-653` | **Head**: `5570270` | **Date**: 2026-07-27

**Remit**: correctness, feasibility, testability, error handling, typing, repository standards.
Reviewed through the `wwpd` persona (invoked; skill loaded).

**Method**: every claim about the repository, the SDK and the artifacts was re-read in the tree at
head `5570270`. Nothing is accepted from the remediation's self-description. Where a mechanism is
prescribed, I executed the prescription against the SDK source to see whether it does what the
artifacts say it does — which is what caught R1 and R2 in the previous two rounds, and what produces
the one blocking finding here.

## Verdicts on round 2

| ID | Verdict | One line |
|---|---|---|
| R1 | **CLOSED** | The forcing mechanism is named, both variants are implementable against the SDK, and the observable moved to "a read was issued" with an explicit anti-regression clause |
| R2 | **CLOSED as narrowed** | The flat guarantee is struck in all three places; step 5b is a real gate on the render. The narrower claim is true for the two cases the gate enumerates — see AD066 below for the third it does not |
| R3 | **CLOSED** | The assertion is split; the relationship-crossing kind is a `xfail(strict=True)` citing plan.md's Material risk row, and the done-condition treats an xpass as a suite failure |
| R4 | **CLOSED** | "Byte-identical mutation inputs" replaces the vacuous form; the propagation is complete except two stale assertion numbers |
| R5 | **CLOSED** | One owner, pinned in the producing task, both reading tasks, both contracts and data-model.md. Sound under all three paths — see AD069 below |

Round 2's Recommended batch: **R6 CLOSED** (both clauses conditioned on a completed apply, checked
after the loop, named error preferred over `assert` — data-model.md:366-375, tasks.md:351).
**R7 CLOSED** (CHK030 restated in both artifacts, checklists/write-convergence.md:74,140-141,
checklists/reviews/write-convergence-review.md:45). **R8 CLOSED** (`model_validator(mode="before")`
reading the identifier out of the raw input — data-model.md:148-155). **R9 CLOSED**
(`PlanArtifactTornError` for any other model-validation failure, naming line number and field, with
the action case taking precedence — data-model.md:156-164, asserted at T024). **R10 CLOSED** (T062
case (c) asserts the review path refuses an unrecognized action with the same message, and says why
the bound must be tested). **R11 moot** — AD070 withdrew the change to `update_node`. **R12, R13, R14
CLOSED.** Carried forward from round 1: **E12 is now CLOSED** (the AD050 probe is
`store.get(model=candidate, identifier=uid)` treating `ObjectNotFound` as "not this kind",
tasks.md:280 — no `LocalStore._data` mutation). **E6** (nested `child_diff`, `grep child_diff` over
the artifacts still returns nothing) and **E9** (`derive_deletes` still "per kind", tasks.md:288) are
unchanged and still Recommended.

## New findings

| ID | Severity | Summary | Anchor |
|---|---|---|---|
| S1 | **Must-Address** | The replace-set reconciliation is never flushed. `remove()`/`add()` are purely local; the specified sequence ends at 7d with no save, so step 7 removes nothing at a real destination — and every specified observable is satisfied without a flush | contracts/destination-write-surface.md:99-104,239-252; tasks.md:324-333,341-343,362,375; plan.md:550-566; `.venv/…/infrahub_sdk/node/relationship.py:322-357` |
| S2 | Recommended | The gate's accessor is one level short: `_generate_input_data(...)["data"]` is `{"data": {…}}`, so `"id" not in rendered` is true for **every** operation and the all-direct arm raises on all of them | contracts/destination-write-surface.md:95-96; tasks.md:118,339; plan.md:529; `.venv/…/infrahub_sdk/node/node.py:300-308` |
| S3 | Recommended | Step 5b's branch table has no row for a kind that declares **no** HFID — which FR-024 explicitly permits and requires the plan run to survive. The all-direct arm swallows it and raises naming the wrong cause, stricter than FR-013's own narrowed guarantee | contracts/destination-write-surface.md:143-146; tasks.md:339; spec.md:1749-1765, :1529-1530 |
| S4 | Recommended | AD066's per-kind warning — the disclosure that justified not refusing — has no pinned log level, no test, and no stated home for its per-kind dedup state. The run's own AD055 precedent pinned `logging.WARNING` and asserted `levelno` for exactly this reason | contracts/destination-write-surface.md:146,205; tasks.md:339; contrast tasks.md:350,365 |
| S5 | Recommended | Two cross-references still say "T081 assertion 3" for byte-identity; after the AD067 split that is assertion **4**, and assertion 3 is now the re-read | tasks.md:537, :562 |
| S6 | Nit | `node.py:295-298` is off by one throughout — the `id`/`hfid` block is `:294-297` | plan.md:129,156; contracts/destination-write-surface.md:27,120,184,204; tasks.md:339 |
| S7 | Nit | plan.md:522 still asserts flatly that "the HFID resolves and the upsert is keyed", three lines above the paragraph that says it does not for a relationship-crossing kind | plan.md:522 |
| S8 | Nit | The apply record has no named type or field list — only three key names. `ty` will infer `dict[str, Any]` and the merge site loses every guarantee | tasks.md:345-349; data-model.md:349-364 |
| S9 | Nit | `checklists/reviews/*.md` carry no supersession banner, and CHK025's minimum fix still proposes the AD068-abolished assertion ("invoked twice … yields one destination object") | checklists/reviews/write-convergence-review.md:1-11,438-440 |

---

## S1 — Must-Address: the reconciliation is never flushed, so step 7 removes nothing

This is the same failure shape as AD065, one step further along the sequence, and the artifacts
prescribe the thing that produces it.

**What the SDK does.** `RelationshipManagerSync` has `fetch`, `add`, `remove`, `extend` — and no
`save`. Both editors are purely local:

```python
339:    def remove(self, data: str | RelatedNodeSync | dict) -> None:
340:        if not self.initialized: raise UninitializedError(...)
...
348:            self.peers.pop(idx)
349:            self._has_update = True
```

(`.venv/…/infrahub_sdk/node/relationship.py:339-357`; `add` at `:322-332` is the same shape.) No
client call, no mutation. The peer set reaches the destination only when the **node** is saved
afterwards: `_strip_unmodified` keeps a `RelationshipManagerBase` in the payload if and only if
`has_update` is true (`.venv/…/infrahub_sdk/node/node.py:352-363`), and the manager renders the
**full** peer list (`relationship.py:68-69`), which is what makes the write a replace.

That is exactly how the pre-existing code works: `update_node` returns the node without saving and
its caller flushes: `update_node` ends `return node` at `infrahub_sync/adapters/infrahub.py:177`, and
`InfrahubModel.update` calls it at `:625` then `node.save(allow_upsert=True)` at `:626`.

**What the artifacts specify.** The sequence ends at 7d and then leaves:

```text
 6. node.save(allow_upsert=True)                       # the convergence point
 7. for ref in operation.relationships where cardinality == "many":
        _replace_relationship_set(node, ref.field, resolved_peer_ids)
7a. rm.initialized = False; rm.fetch()                 # the AD065 mechanism
7b. existing = rm.peer_ids
7c. _, existing_only, new_only = compare_lists(existing, new_peer_ids)
7d. remove every existing_only; add every new_only
 8. peers.remember(...)
 9. return node.id
```

(contracts/destination-write-surface.md:99-104, 239-248.) There is no step 7e. `grep` for `save` over
`tasks.md`, `plan.md` and both write-path contracts returns only step 6's `save(allow_upsert=True)`,
V10, and the SDK store-population fact — never a second save. plan.md:550-566, T042 (tasks.md:324-333),
T045 (`:339`) and T046 (`:341`) all end the reconciliation at remove/add.

**So the reconciliation mutates an in-memory peer list and discards it.** And the failure is
co-extensive with the risk step 7 exists to mitigate: if the server's upsert already **replaces**, the
re-read finds no difference and there is nothing to flush — the feature works by accident. If it
**merges** — the case AD007 says cannot be ruled out offline and PD-005 exists for — step 7 computes
the surplus correctly and then throws it away. The mitigation is void precisely when it is needed.

**And no specified observable can fail.** T051 runs "against a mocked `InfrahubClientSync`"
(tasks.md:361-362) and asserts "existing-only peers are removed and new-only peers added"; T081
assertion 3 says "Seed an existing peer set that differs from the plan's and assert the surplus is
removed" (tasks.md:375). Both are satisfied by `rm.peer_ids == expected` on the in-memory manager.
T042's done-condition asserts the helper "**removed** the surplus peers of a manager whose destination
set differs from the desired one" — again manager state. SC-008 is the only criterion that would
catch it and it is deferred and not produced at merge (plan.md:642-645). So this ships with a green
suite and a documented "replace-set enforced" claim.

Worse, T042 actively steers into it: "The reconciliation shape it duplicates lives in the module-level
`update_node`" (tasks.md:324). Faithfully duplicating a function that does not save, into a call site
that has no save after it, is the specified instruction.

**Minimum fix**, two parts, both small:

1. Add the flush to the sequence, and name it precisely, because the obvious spelling is wrong:

   ```text
   7e. node.save()      # NOT save(allow_upsert=True): _existing is True after step 6
                        # (node.py:1811), so save() dispatches update() (:1533-1534) and
                        # _strip_unmodified keeps the relationship because has_update is True
   ```

   A second `save(allow_upsert=True)` would re-render the upsert create instead. State it in
   contracts/destination-write-surface.md step 7, plan.md:550, T042, T045 and T046 — and say whether
   the flush lives inside `_replace_relationship_set` or after the loop (one save for all
   relationships is cheaper and equally correct; pick one so the tests know what to count).

2. Move the observable, exactly as AD065 did for the read. T051 and T081 assertion 3 must assert that
   **a destination write carrying the reconciled peer list was issued** — a recorded mutation whose
   rendered relationship value is the desired set — not that the manager's in-memory `peer_ids`
   changed. The test must fail against a helper that reconciles and never saves. Without this half,
   the fix is unverifiable for the same reason the read was.

**Why it cannot wait for implementation.** No local test can distinguish a working replace-set from a
no-op one under the specified assertions; the only criterion that could is deferred; and the task
instructs the implementer to duplicate a function whose flush lives in its caller. This is the third
consecutive round in which a mechanism on this exact path was specified in terms that leave it inert,
and it is the one class of defect this feature's headline relationship guarantee rests on.

## S2 — Recommended: the gate reads one level too shallow

`_generate_input_data` returns `{"data": mutation_payload, "variables": …, "mutation_variables": …}`
where `mutation_payload = {"data": data}` (`.venv/…/infrahub_sdk/node/node.py:300-308`). So
`node._generate_input_data(exclude_hfid=False)["data"]` is `{"data": {…}}` — a one-key mapping. The
gate then tests `if "id" not in rendered and "hfid" not in rendered` (contracts:96), which is true for
every operation ever rendered, so the all-direct arm raises on all of them. The correct expression is
`…["data"]["data"]`.

Recommended rather than blocking because T081 assertion 1 fails immediately and loudly on the first
run and the implementer fixes it in a minute. But it is written identically in four places
(contracts:95, tasks.md:118, tasks.md:339, plan.md:529) and it is presented as executable pseudocode,
so correct it in all four.

## S3 — Recommended: no row for a kind that declares no convergence key

Step 5b "branch[es] on the destination kind's HFID shape" with two rows: all-direct → raise,
relationship-crossing → warn and proceed (contracts:143-146). A kind whose `human_friendly_id` is
absent or empty matches neither. Under the natural implementation — "every component is direct" over
an empty component list — it lands in the **raise** arm, with the message the contract fixes for that
arm: "the payload lost its identity components."

FR-024 explicitly contemplates that kind and requires the plan run to survive it: "when a destination
kind declares no human-friendly ID … The plan run MUST still succeed in either case; this is a
warning, not a failure" (spec.md:1749-1765). Step 3b is vacuous for such a kind, so before AD066
nothing refused it. AD066's new gate therefore introduces a refusal for a configuration class the
specification tolerates — and refuses it with a diagnosis that is false, since the payload lost
nothing.

It also contradicts the narrowed guarantee in its own words: "no rendered mutation is issued unkeyed
where being unkeyed **can only be a defect**" (spec.md:1530). For a kind with no HFID, unkeyed is a
schema fact, not a defect, so by the guarantee's own terms the gate must not refuse it.

Recommended rather than blocking because an implementer writing that branch has to decide what an
empty component list means, the artifacts' own AD052 precedent (T033's `getattr(destination, "schema",
None)` guard, skip and return, never an error) points at the right answer, and the guarantee sentence
above states it. But no test covers it — T074's integration fixture asserts every kind under test
declares an HFID as a *fixture precondition* (tasks.md:477), so even the deferred path is scoped away
from this case. **Minimum fix**: a third row — no HFID declared → this is the FR-024 condition, warn
once per kind naming that the kind declares no convergence key and proceed (or refuse with a message
naming *that* cause, if refusing is intended) — plus one T081 or T050 case.

## S4 — Recommended: the AD066 warning has no level, no test, and no home

The per-kind warning is the entire reason refusing the relationship-crossing case was rejected
(spec.md:878-889, plan.md:813). Three things are missing that this same run has already learned to
supply:

- **No level.** It is described as "one operator-visible warning per destination kind"
  (contracts:146, tasks.md:339) and FR-013 says "reported once per destination kind on the run's log
  stream" (spec.md:1526-1527). AD055's warning was described the same way in round 1 and then pinned
  to `logging.WARNING` with the reason spelled out — `--quiet` floors the package logger at
  `WARNING` (`infrahub_sync/cli.py:29`), so an `INFO` emission satisfies every prose description and
  vanishes for exactly the scripted invocations where the warning is the only signal
  (tasks.md:350). The identical argument applies here and is not made.
- **No test.** `grep "once per"` across the task file returns the obligation and no assertion. T054
  asserts the skipped-delete warning's `levelno`; nothing asserts this one at all, so it can be
  absent entirely and the suite stays green. FR-013's disclosure clause then has no evidence.
- **No home for the dedup state.** "Once per destination kind" needs state across operations, and
  `apply_planned_operation` is per-operation. Say where it lives (a set on the adapter instance,
  cleared per apply, like `PeerResolver`'s memo lifetime).

**Minimum fix**: pin `logging.WARNING`, add one T081 or T050 case asserting one record per kind
across two operations of the same kind with `record.levelno >= logging.WARNING`, and name the state's
home in T045.

## S5 — Recommended: two stale assertion numbers

The AD067 split renumbered T081's assertions to four, with the re-read at 3 and byte-identity at 4
(tasks.md:373-376). Two consumers were not renumbered: tasks.md:537 ("partially anticipated offline by
T081 assertion 3 — **byte-identical rendered mutation inputs**") and tasks.md:562 ("T081 assertion 3,
AD068"). Both now point at the re-read. `s/assertion 3/assertion 4/` in those two rows.

---

## AD066 on engineering grounds

**Sound and implementable, and the reasoning for not refusing holds.**

The engineering question is whether a refusing gate would have been unimplementable-in-effect, and
it would. The artifacts' verified facts say a relationship-crossing render carries neither key
today — re-verified end to end: a resolved-id relationship renders as `{"id": …}` with no
`__typename` (`.venv/…/infrahub_sdk/schema/__init__.py:172-181`); `RelatedNodeSync.get()` needs both
`id` and `typename` and raises otherwise (`related_node.py:298-304`); `get_path_value` catches that
and returns `None` (`node.py:100-107`); one `None` nulls the whole HFID (`:135-139`); and
`_generate_input_data` emits neither key (`:294-297`). Ten mapping entries on the qualified path carry
a reference inside their identity (V30). A refusing gate would decline all of them, so DBA-008 would
move from "deferred" to "unachievable". Declining to refuse there is the right call, and the reason is
recorded rather than asserted.

The observation point is right too: keyedness is a client-side render property, the gate reads the
same render `save(allow_upsert=True)` will perform (`node.py:1533-1534` → `:1844`, `exclude_hfid=False`),
and step 3b stays as the only check that can name *which* component is missing. Keeping both, with
different names and different consequences, is better than either alone.

**Is every remaining statement of the guarantee true?** The flat form is genuinely gone. I grepped
every occurrence of "unkeyed" across all fourteen files outside `critiques/`: 44 hits, and not one
carries "an unkeyed write is never issued" or an equivalent absolute. Round 2's three sites — the
T045 task line, the Phase E prose in plan.md and step 3b's clause table — each now carry the narrowed
form and say the flat claim is struck. The narrowed claim — "no write
is issued whose payload is missing an HFID component, and no render is issued unkeyed where being
unkeyed can only be a defect" — is true for the two rows the gate enumerates, and is stated
identically in spec.md:1529-1530, tasks.md:39-41, plan.md:538-540 and contracts:150-151.

Two caveats, neither fatal to the design: the gate as specified is **stricter** than the claim for a
kind with no HFID (S3), and the claim's first clause is loose about "payload" when a
relationship-crossing component legitimately travels outside the payload — FR-013's two-route
paragraph (spec.md:1508-1514) resolves that, but the one-sentence form read alone does not. The
second is a wording nit; the first wants the third table row.

## AD069 on engineering grounds

**Sound under all three paths, and the record-on-the-error is a legitimate mechanism, not a smell.**

Re-verified the premise: `RunFile.save()` writes `{k: getattr(self, k) for k in self.KEYS}` with no
merge (`infrahub_sync/cache/sidecars.py:87-89`); `apply_cmd` builds its `RunFile` with a default empty
`summary` at `infrahub_sync/cli.py:322-323` and saves it again at `:350-351` after `apply_plan()`
returns, never reloading. Option A is correct and is now pinned in six places (tasks.md:121,345-352,
365-367,403; data-model.md:349-358; contracts/destination-write-surface.md:401;
contracts/cli-review-mode.md:230; contracts/plan-reader-api.md:174).

- **Completed apply**: engine returns, CLI merges into `run_file.summary`, then `:351` saves. Works.
- **Mid-apply rejection / partial apply**: the CLI's failure path is `except Exception: run_file.status
  = "failed"; run_file.save(); raise` (`cli.py:346-349`) — the merge slots in before that `save()`,
  which T059 says in as many words. FR-025's last-applied pointer survives because it is the final
  element of the partial `applied_operations`. The invariant is off this path by construction
  (post-loop, completed-apply only), so the rejection message is not replaced by an invariant error —
  and T054 asserts exactly that. Works.
- **Refusal before any write**: the engine has nothing to hand back, and
  contracts/plan-reader-api.md:174 says so explicitly — "A refusal needs no data from the engine — the
  three values are the empty ones above". The CLI writes the present-and-empty triple. T065 asserts it
  read back from `run.json` for all nine refusal cases plus the unrecognized-action one. Works.

**On the partial record riding the exception**: this is the ordinary shape for "the operation failed
and here is what it got done" — structured data on a domain exception, caught by a layer that knows
the type. The alternatives are worse: an out-parameter the caller pre-allocates and the engine
mutates, or returning `(record, error)` and re-raising in the CLI, which loses the traceback and puts
control flow in a tuple. It is testable (T055 asserts the error carries it) and it keeps the engine
free of file I/O. Not a smell.

One residue, S8: the record has no named type — only three key names and "the returned record's
`skipped_delete_count`". Since AD069 introduces a return value crossing a layer boundary, and the
repository is clean under `ty` with no overrides, give it a small frozen dataclass with an
`as_summary()` (or `to_summary_keys()`) so the merge site is typed and a later relocation is a type
error rather than a `KeyError` two phases downstream. Also worth stating that the CLI's failure path
still does not set `finished_at` (`cli.py:350` is only on the success path) — pre-existing, and
unchanged by this design, but a partial-apply record with no `finished_at` is what an operator will
read.

## Regressions from three rounds of editing

I swept the fourteen files for the classes of damage this kind of editing produces. Findings above
are S5 (stale assertion numbers), S6 (a line citation off by one, propagated), S7 (a survived
absolute), S9 (a review artifact still proposing an abolished assertion). Beyond those:

- **Task inventory intact.** `grep -c "^- \[ \] T\|^- ~~T"` returns **90**, matching the stated count;
  T060 is struck through with its reason and its dependent T065 case dropped; no orphan task ids.
- **AD070's withdrawal is complete and consistent.** All 36 `update_node` mentions across the
  artifacts now read the same way. T042 is re-titled as new code with the duplication priced
  (tasks.md:324); the scope guardrail's "one deliberate exception" is gone and replaced with "No
  change to the live `sync` write path. No exception (AD070)" (tasks.md:654-663); V12 carries the
  narrowing (plan.md:127); PD-005's earlier rejection of duplication is explicitly overruled on scope
  grounds with the reasoning stated (plan.md:574-577). T042's done-condition — `tests/adapters/`
  passes **unchanged** — is now the evidence that the live path was not touched, which is the right
  observable. The contradiction with T044 and T053 is gone.
- **Phase ordering and green-tree claims still hold.** S/A/B/C pure additions; D not fully green with
  the sweep in G/T067; E green because T066 lands with T048 in the same change; F and G green.
  Phase E's parallel note now says T042 "**adds** a helper and changes nothing existing", consistent
  with AD070 (tasks.md:615-616). tasks.md and plan.md agree.
- **The knowability invariant is stated identically** in data-model.md:366-375 and tasks.md:351 —
  both clauses, both conditioned, both post-loop, named error preferred over `assert`. No drift.
- **plan.md:640 says "two of the three assertions"** while T081 now has four — but it is describing
  the *earlier* three-assertion form, so it is correct in context. Not a finding.
- **`[PROVISIONAL ADxxx]` on the round-2 decisions** is the file's established convention (196
  occurrences, AD010 through AD074). Not a finding.

## Code facts re-verified at head `5570270`

| Fact | Verdict |
|---|---|
| AD065 option A works: forced cold, `fetch()` issues `client.get(kind=…, id=…, include=[rel], exclude=…)` | **Correct** (`.venv/…/infrahub_sdk/node/relationship.py:286-296`) — and it is the read T051's `include=[<rel>]` observable can record |
| AD065 option B is formable: `node.id` is populated before step 7 | **Correct** — `_process_mutation_result` sets `self.id = object_response["id"]` (`.venv/…/infrahub_sdk/node/node.py:1810`) |
| `add()`/`remove()` issue no destination mutation; the manager has no `save` | **Correct** (`relationship.py:321-357`; no `def save` in the class) — the basis of S1 |
| A second `node.save()` flushes: `_existing` is `True` after step 6, so `save()` dispatches `update()`, and `_strip_unmodified` keeps a manager with `has_update` | **Correct** (`node.py:1811`, `:1533-1534`, `:352-363`; full list render at `relationship.py:68-69`) — the mechanism S1's fix needs |
| The pre-existing shape's flush is its caller's: `update_node` returns unsaved; `InfrahubModel.update` saves | **Correct** (`infrahub_sync/adapters/infrahub.py:172`, `:175`, `return node` at `:177`; caller `:625-626`) |
| `_generate_input_data(...)["data"]` is `{"data": {…}}`, not the rendered `data` | **Correct** (`node.py:300-308`) — the basis of S2 |
| The `id`/`hfid` block is `:294-297`, not `:295-298` | **Correct** — S6 |
| Upsert renders with `exclude_hfid=False`; `save(allow_upsert=True)` dispatches to it | **Correct** (`node.py:1844` inside `:1843-1846`; dispatch `:1533-1534`) |
| V39 / the unkeyed-render chain, re-verified end to end | **Correct** (`schema/__init__.py:172-181`; `related_node.py:64-68`, `:298-304`; `node.py:100-107`, `:135-139`, `:294-297`) |
| AD069's premise: whole-payload save, no merge, CLI saves last from an empty summary | **Correct** (`cache/sidecars.py:87-89`, `:73`, `:76`; `cli.py:322-323`, `:346-349`, `:350-351`) |
| FR-024 permits a destination kind with no HFID and requires the plan run to succeed | **Correct** (spec.md:1749-1765) — the basis of S3 |
| `--quiet` floors the package logger at `WARNING` | **Correct** (`infrahub_sync/cli.py:29`) — the basis of S4 |
| `SLF001` and `S101` globally ignored; no `[[tool.ty.overrides]]` | **Correct** (`pyproject.toml:161,272,278`) |
| Task count | **90**, matching the stated 90 |

No code fact recorded in the artifacts is wrong. The one blocking defect is again in what the
artifacts *do* with correct facts — and again on the same mechanism.

## Are these artifacts sound enough to implement from?

**Almost, and one thing must land first.** Four of the five round-2 blockers are properly closed, and
closed with mechanisms I could execute against the SDK rather than restatements. The remediation
agent's one deliberate overrule (AD066: narrow the claim rather than move the gate wholesale) is
correct on the merits and correctly reasoned — refusing there would have withdrawn ten qualified-path
mapping entries and converted a deferred criterion into an unachievable one. AD069 is sound under all
three paths. The AD067 split is the repository's annotated-quarantine pattern used properly, with a
strict marker that retires the limitation on its own.

**The blocking residue is S1, and it cannot wait.** The replace-set enforcement — SC-008's whole
subject, PD-005's whole purpose, and the clause AD054 and AD065 have each been sharpened once
already — is specified to end at an in-memory `remove()`/`add()` with nothing flushing it. At a real
destination it removes nothing, in exactly the server-merge case it exists to defend against. It
cannot wait for implementation for three reasons: every specified observable (T051, T081 assertion 3,
T042's done-condition) is satisfied without the flush, so no local test can fail; the only criterion
that would catch it is deferred and not produced at merge; and T042 explicitly instructs the
implementer to duplicate `update_node`, a function whose flush lives in its caller. Left as is, this
ships green, with the documentation claiming a replace-set that is a no-op.

The fix is small and mechanical — one step in the sequence in four places, and one observable moved
from manager state onto the issued write, which is the same correction AD065 already made for the
read. S2 through S4 should ride along in the same pass: S2 because it is executable pseudocode that
does not execute, S3 because it is a one-row omission that refuses a configuration class the spec
tolerates, S4 because AD066's compromise is only as good as the disclosure that justified it and that
disclosure currently has no level and no test.
