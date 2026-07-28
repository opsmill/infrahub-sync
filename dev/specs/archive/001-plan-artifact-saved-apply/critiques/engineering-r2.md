# Engineering lens — round 2

**Feature**: `001-plan-artifact-saved-apply-infp-653` | **Head**: `844e98f` | **Date**: 2026-07-27

**Remit**: correctness, feasibility, testability, error handling, typing, repository standards.
Reviewed through the `wwpd` persona (invoked; `references/profile.md` loaded).

**Method**: every claim about the repository was re-read in the tree or in `.venv` at head `844e98f`.
Nothing is accepted from the remediation's description of itself, and nothing from the artifacts'
description of the codebase.

## Verdicts on round 1

| ID | Verdict | One line |
|---|---|---|
| E1 | **PARTIALLY CLOSED** | Diagnosis and V12 correction are right; the prescribed re-read mechanism (`fetch()` first) provably does not re-read |
| E2 | **PARTIALLY CLOSED** | The harness moved onto the rendered mutation; step 3b, T045 and plan.md did **not**, and the flat guarantee survives verbatim |
| E3 | **NOT CLOSED** | Assertion 3 is materially unchanged and is now contradicted by T081's own diagnosis paragraph |
| E4 | **CLOSED** | `summary["applied_operations"]` is pinned in data-model.md, T047 and every reader — see R5 for the consequence |
| E5 | **CLOSED** | V22 corrected, T060 dropped with its test case, non-goal and traceability updated |

## New findings

| ID | Severity | Summary | Anchor |
|---|---|---|---|
| R1 | **Must-Address** | The re-read is specified as "fetch first", and `fetch()` short-circuits on `initialized` — the exact fact V12a records. The prescribed helper is still a no-op | tasks.md:289; contracts/destination-write-surface.md:190; `.venv/…/infrahub_sdk/node/relationship.py:264,286-288` |
| R2 | **Must-Address** | Step 3b still gates on `data`, not the rendered mutation input, and "an unkeyed write is never issued" survives verbatim in three places | contracts/destination-write-surface.md:83,164; tasks.md:299; plan.md:494 |
| R3 | **Must-Address** | T081 assertion 1 demands every operation be keyed over a fixture it *requires* to contain a relationship-crossing-HFID kind. The artifacts' own verified facts say that operation cannot be keyed | tasks.md:332,331; contracts/destination-write-surface.md:143-149; plan.md:724 |
| R4 | **Must-Address** | T081 assertion 3 asserts two applies issue one create; two applies issue two creates. Its Done-when asserts an outcome that cannot occur | tasks.md:334,337; propagated to tasks.md:490,515, quickstart.md:108, plan.md:563,706 |
| R5 | **Must-Address** | AD062's placement collides with the existing writer: `apply_cmd`'s in-memory `RunFile` (`summary={}`) is saved after `apply_plan()` returns and overwrites all three keys | `infrahub_sync/cli.py:322-323,345-352`; `infrahub_sync/cache/sidecars.py:87-89`; tasks.md:304-306; plan.md:537-541 |
| R6 | Recommended | The knowability invariant's union clause is written unconditioned; only the length clause carries "whenever the apply completes". T047 also says "assert … in the loop" | data-model.md:332-335; tasks.md:310 |
| R7 | Recommended | Two checklist artifacts still record a delete-bearing plan as ending `failed`, one of them marked SATISFIED | checklists/write-convergence.md:74,140; checklists/reviews/write-convergence-review.md:45 |
| R8 | Recommended | A bare `Literal` raises `ValidationError`, not `UnsupportedOperationActionError`; nothing specifies the translation or where the operation identifier comes from | tasks.md:184; data-model.md:142-147 |
| R9 | Recommended | No named error covers an operations line that parses as JSON but fails model validation for any other reason — a raw pydantic traceback with no next action, against AD059 | tasks.md:219; contracts/plan-reader-api.md:87 |
| R10 | Recommended | The reader-level action refusal also refuses the read-only review path. Unstated, untested, and sits oddly beside "review never refuses to show" | contracts/plan-reader-api.md:52 vs :87; tasks.md:228 |
| R11 | Recommended | Correcting the shared helper changes the live `sync` path from additive to destructive replace-set and adds a destination round trip per many-relationship per node. No risk row, no test on that path | tasks.md:289,608-609; `infrahub_sync/adapters/infrahub.py:97,149-175` |
| R12 | Nit | The ordering section still calls T042 "a behavior-preserving extraction" — the opposite of what T042 now says | tasks.md:568 vs tasks.md:289 |
| R13 | Nit | `tasks.md` input line still reads `AD001–AD053`; AD054–AD064 exist | tasks.md:9 |
| R14 | Nit | E11 unfixed: T033 still carries two contradictory `Done when` lines, the stale one saying "three cases" for a four-case T039 | tasks.md:258-259 |

Carried forward from round 1, unaddressed and still Recommended/Nit: **E6** (nested `child_diff`
dropped — `grep child_diff` over the artifacts returns nothing), **E7** (duplicate vs 64-bit
collision), **E8** (`--help` text diff as evidence — no `registered_commands` assertion anywhere),
**E9** (`derive_deletes` still "per kind", tasks.md:255, not "per destination kind in `top_level`"),
**E12** (the AD050 probe still mutates `LocalStore._data`). E10 is superseded: T081 is now built on a
real node, though T050–T053 remain mock-only.

---

## E1 — PARTIALLY CLOSED

**What landed, and it is correct.** V12 is corrected in place (plan.md:111) with the right mechanism:
`update_node` reads `attr_manager.peer_ids` at `:151` and only calls `fetch()` at `:168-169`, so it
adds without removing. V12a (plan.md:112) is new and right. Re-verified in the tree:
`infrahub_sync/adapters/infrahub.py:151` (read), `:166` (`compare_lists`), `:168-169` (guarded
`fetch()`), `:171-175` (remove/add). The extraction is now explicitly declared non-ordering-preserving
(tasks.md:289), the contract carries a "why step 7 must re-read first" section
(contracts/destination-write-surface.md:171-199), and T051/T081 both require the surplus-removal case
to fail if the re-read is removed. That is the right diagnosis, in the right places.

**What did not land — R1.** The mechanism. Every statement of the fix is a statement about *ordering*:

- tasks.md:289 — "The helper must **fetch first, then read `peer_ids`**, then `compare_lists` …"
- contracts/destination-write-surface.md:190 — `7a. fetch the relationship manager from the destination FIRST   # unconditional re-read`

Ordering is necessary and not sufficient. Re-read in `.venv/…/infrahub_sdk/node/relationship.py`:

```python
264:        self.initialized = data is not None
...
286:    def fetch(self) -> None:
287:        if not self.initialized:
288:            exclude = ...            # the client.get lives inside this guard
```

`fetch()` carries its own `if not self.initialized` guard. On the node step 7 holds — built locally
from the write payload, so every many-cardinality manager was constructed with `data` and reports
`initialized is True` — calling `fetch()` first performs **no** destination read. It walks
`self.peers` for the peer prefetch and returns. `peer_ids` is still the desired set, `compare_lists`
still compares it against itself, and the reconciliation is still the guaranteed no-op AD054 exists to
kill. The artifacts state this fact three times (plan.md:112,
contracts/destination-write-surface.md:20, tasks.md:301) and then prescribe the one fix it defeats.

Worse, T051's specified observable is satisfiable by the broken implementation: "Assert the re-read
happened — the relationship manager was fetched before `peer_ids` was read" (tasks.md:321). A no-op
`fetch()` *was* called before `peer_ids` was read. Only the surplus-removal half of that task, and
T081 assertion 2, would fail — and only if the fixture is a real manager rather than a hand-built one,
which T051 does not require (it is a mocked-client task).

**Minimum fix.** Name the forcing mechanism in T042 and in step 7a — one of:

```python
rm = getattr(node, rel_name)
rm.initialized = False          # discard the locally-constructed peer set
rm.fetch()                      # now the client.get actually runs
existing = rm.peer_ids
```

or a scoped `client.get(id=node.id, kind=node._schema.kind, include=[rel_name])` and read the manager
off that node. Then reword T051's assertion from "was fetched before `peer_ids` was read" to "issued a
destination read for the relationship before `peer_ids` was read", which the broken form cannot pass.

## E2 — PARTIALLY CLOSED

**The claim under review**: "step 3b now gates on the **rendered mutation input** containing `id` or
`hfid`, not on the assembled `data`."

**That is not what the artifacts say.** Step 3b is unchanged in substance everywhere it is specified:

- `contracts/destination-write-surface.md:82-83` — `3b. ASSERT every component path of node_schema.human_friendly_id is ACCOUNTED FOR (rules below)` … `never issue an unkeyed write`
- `:164` — "Convergence-key presence | Asserted at step 3b by the per-component rule above — direct components checked in `data`, relationship-crossing components checked in `data` **and** in the operation's nested `{peer_kind, identity}`"
- `tasks.md:299` (T045) — the AD051 per-component rule over `data`, ending "**no unkeyed write is ever issued**"
- `plan.md:492-494` — same, ending "An unkeyed write is never issued."

Round 1's fix (2) — delete or requalify the flat guarantee — was applied in exactly one place
(`contracts/destination-write-surface.md:156`, "no unkeyed write is issued *blind*") and nowhere else.
Fix (1) was not applied at all. What *was* rebuilt is the harness: V12b (plan.md:113) is new and
correct, and T081 now asserts the rendered input. So the offline **detector** exists; the product-code
**gate** and the wording did not move.

Re-verified: `.venv/…/infrahub_sdk/node/node.py:294-297` (`data["id"] = self.id` … `elif self.hfid is
not None and not exclude_hfid`), `:1843-1844` (upsert renders with `exclude_hfid=False`), `:1533-1534`
(`save(allow_upsert=True)` dispatches to `create`). V12b's cited ranges are off by one on the first
(295-298 vs 294-297); the block is right.

**Minimum fix.** Either move the gate as round 1 specified — one line after step 4/5 and before
`save()`, on `node._generate_input_data(exclude_hfid=False)["data"]`, keeping AD051 as the diagnostic
that names the missing component — or strike the flat guarantee from tasks.md:299, plan.md:494 and
contracts/destination-write-surface.md:83 and replace it with what `data`-level checking supports: "no
write is issued whose payload is missing an HFID component." Do not leave both.

## R3 — Must-Address: T081 assertion 1 cannot pass over the fixture T081 mandates

This is the sharp consequence of leaving E2 half-repaired, and it is the answer to "does anything now
assert an outcome that cannot occur?"

T081 requires the fixture to include "**at least one kind whose `human_friendly_id` crosses a
relationship**, so the second arm of AD051's per-component rule is exercised rather than merely
declared" (tasks.md:331). Assertion 1 is then universal: "**For every operation**, the rendered
mutation input carries `id` or `hfid`" (tasks.md:332).

The artifacts' own verified facts say that operation's rendered input carries neither
(contracts/destination-write-surface.md:143-149, re-verified):

1. The plan carries destination *identity values*, never a destination UUID — FR-012 forbids the
   destination load that would supply one. So `self.id is None` and `data["id"]` is never set.
2. `data[<rel>]` is a resolved id string; `generate_payload_create` renders it as `{"id": …}` with no
   `__typename` (`.venv/…/infrahub_sdk/schema/__init__.py:172-181`).
3. `RelatedNodeSync._typename` is therefore `None`, so `get()` never consults the client store and
   raises `ValueError` (`.venv/…/infrahub_sdk/node/related_node.py:64-68,298-304`).
4. `get_path_value` catches it and returns `None`; one `None` component nulls the whole HFID
   (`.venv/…/infrahub_sdk/node/node.py:100-107,135-139`). So `self.hfid is None`.
5. `_generate_input_data` emits neither key (`:294-297`).

Pre-populating the client store does not help — step 2 above means the store is never consulted.
plan.md:724 already records this as a **Material** risk reported to root, honestly. The defect is that
T081 is written as a universal the risk row says must fail, and its Done-when ("all three assertions
pass") therefore cannot be met. An implementer will either drop the relationship-crossing kind from
the fixture — losing the only thing that exercises AD051's second arm — or weaken assertion 1 to the
kinds that already pass, silently.

**Minimum fix.** Split assertion 1 and make the known limitation explicit rather than latent:

1a. For every operation on a kind whose HFID is **all-direct**, the rendered input carries `hfid`. A
    payload built from `source_attrs` alone fails this — the AD042 regression detector, unchanged.
1b. For the relationship-crossing kind, assert today's actual outcome: the rendered input carries
    neither `id` nor `hfid`, marked `xfail(strict=True)` (or an explicitly-named
    `test_relationship_crossing_hfid_is_not_keyed_yet`) with the reason citing plan.md:724. The day
    the write surface closes that hole, the harness flips and tells you.

That is a real, decidable offline statement about the design, and it is the repository's own
annotated-quarantine pattern rather than a claim that cannot hold.

## E3 — NOT CLOSED (R4)

The claim is that "the vacuous 'two applies produce one create' assertion [was] replaced". It was not.

Round 1 text (tasks.md:271): "applying the same operation twice produces exactly **one**
`client.create` invocation with `allow_upsert=True` on save and **no second create**".

Round 2 text (tasks.md:334): "Applying the same operation twice yields exactly **one** `create`
invocation with `allow_upsert=True` — convergence measured at the mutation, which is the only place it
is observable offline."

Same assertion, one clause shorter. And the Done-when now pins the impossible reading explicitly:
"**issuing a second create fails assertion 3**" (tasks.md:337). Two applies of the same operation
issue two `client.create` calls — there is no operation-level dedup in the design and none is wanted;
`PeerResolver`'s memo is keyed on peers and discarded with the apply
(contracts/destination-write-surface.md:157-161). The task now also refutes itself in its own
diagnosis, four lines above the assertion: "A mock holds **no destination state**. 'Two applies
produce one object' cannot fail against one: two applies simply issue two creates" (tasks.md:330).

The false claim is propagated to six other places: tasks.md:490 ("partially anticipated offline by
T081 assertion 3"), tasks.md:515, quickstart.md:108, plan.md:563, plan.md:706,
contracts/destination-write-surface.md:340.

**Minimum fix** (unchanged from round 1, and now cheap because the harness is already on the real
node): assert that the two applies render **byte-identical, keyed** mutation inputs for the same
operation — same `hfid`, same `data` — from `node._generate_input_data(exclude_hfid=False)`. That is a
genuine convergence *precondition*, it is decidable offline, it regresses if R2/R3 regress, and it can
actually fail. Update all six downstream statements with it.

## E4 — CLOSED, and R5 is its consequence

`summary["applied_operations"]` is pinned in data-model.md:319-335, T047 (tasks.md:304-306),
plan.md:537-541 and contracts/destination-write-surface.md:312-314,325, and every consumer — T055,
T056, T059, T065 — is told to read it back by name from `run.json`. Re-verified that this needs no
persisted schema change: `RunFile.summary: dict[str, Any] = field(default_factory=dict)` at
`infrahub_sync/cache/sidecars.py:73`, `KEYS` closed at `:76`. `cache/` genuinely stays unchanged. The
"present and empty on refusal, never absent" rule is stated consistently in data-model.md:328,
contracts/plan-reader-api.md:162-163, contracts/cli-review-mode.md:221, T059 and T065. Good repair.

### R5 — Must-Address: two writers of `run.json`, and the loser holds the record

`Potenda.apply_plan` is now specified to record the three keys (plan.md:537-540 "records the apply
outcome under three named keys of the run file's `summary` mapping"; tasks.md:304-306), and T055
forbids the alternative: "Read every assertion back from `run.json`'s `summary` mapping by name
(AD062) — **not from an in-memory return value**" (tasks.md:325). So the engine must write `run.json`.

The CLI already writes it, from a separate in-memory object:

```python
322:        run_file = RunFile(path=ptd.run_dir / "run.json", status="running", mode="apply")
323:        run_file.save()
...
345:            ptd.apply_plan()
346:            run_file.status = "applied"
...
350:        run_file.finished_at = datetime.now(timezone.utc).isoformat()
351:        run_file.save()
```

`RunFile.save()` writes the whole payload — `{k: getattr(self, k) for k in KEYS}`
(`infrahub_sync/cache/sidecars.py:87-89`) — and this instance's `summary` is `{}` and never reloaded.
Line 351 therefore overwrites all three keys the engine just wrote. Nothing in T047, T059 or plan.md
assigns ownership or mentions the collision, and `RunFile` has no merge path (only
`load_or_default`, `:79-85`, which the CLI does not use).

It is caught late but it is caught: T065 reads the keys back from `run.json` on the CLI path, and
quickstart.md:194-197 reads them in step 5, so Checkpoint F/G would fail. That makes this a
specified-design defect rather than an invisible one — but the implementer meets it as a mystery in
Phase F, and the natural blind fix (have the CLI keep writing) silently deletes FR-020's record.

**Minimum fix.** One sentence in T047 and one in T059 pinning ownership. Either (a) `apply_plan`
returns an `ApplyRecord` and T059 makes the CLI do `run_file.summary.update(record.as_summary())`
before `save()` — then relax T055's "not from an in-memory return value" to "read back from
`run.json` after the CLI records it", or (b) `apply_plan` owns `run.json` for `mode="apply"` and T059
deletes the CLI's `RunFile` construction and both `save()` calls. (b) keeps T055 as written and keeps
the record in one place; say which.

## E5 — CLOSED

V22's correction is right and I re-verified all of it at head `844e98f`:

- `infrahub_sync/cli.py:322-323` does write `run.json` at `status: running`.
- `:330` imports `_resolve_infrahub_schema` from `infrahub_sync.utils`; `grep -rn
  "_resolve_infrahub_schema" --include='*.py' .` (excluding `.venv`) returns exactly three lines —
  the comment at `:325`, the import at `:330`, the call at `:332`. The symbol does not exist.
- `except ImportError: pass` at `:341-342` swallows it, so `print_error_and_abort` at `:336-340` is
  unreachable.

The consequence is carried consistently: AD063 (tasks.md:102), plan.md:45 and :123, T060 struck
through (tasks.md:359), its T065 case dropped with a stated reason (tasks.md:374), the FR-009 and US2
traceability rows annotated (tasks.md:459, :514), Phase F ordering updated (tasks.md:570), a non-goal
added (tasks.md:615-616), data-model.md:303-311, spec.md:152-158 and :762,
contracts/cli-review-mode.md:226. Dropping rather than repairing is the right call and it was made
cleanly.

---

## AD055 on engineering grounds

**The product call is settled; I am judging enforceability only.**

**The invariant is enforceable, and T047 states it correctly.** tasks.md:310: "on a **completed
apply**, `set(applied) | set(skipped)` equals the plan's identifier set and `len(applied) + count ==
manifest.operations_count`", with tasks.md:311 spelling out the carve-out: a rejection "stops at that
operation, keeps what was written, records the three keys as they stand, and fails … since the
invariant above does not hold for it". That is the right shape:

- **Partial apply / mid-write failure**: unattempted operations are in neither set, so neither clause
  holds. The run records `failed`, which is how the two cases stay distinguishable. Correct, and it is
  why the invariant must be a post-loop check gated on completion, not an unconditional one.
- **Crash**: FR-025 (spec.md:1564-1567) and the AD011 clarification (spec.md:159-165) already put
  survival of abnormal termination out of scope, and SC-003's crash windows are measured
  destination-side. `run.json` will read `running` with `summary: {}`. Consistent — no claim is made
  that the record survives.
- **Uniqueness**: FR-021 forbids two operations sharing an identifier, so the set-union and the
  length-sum cannot disagree. `applied` and `skipped` are disjoint by construction (only `delete`
  reaches `SkippedDeleteOperation`, tasks.md:299).
- **Ordinariness**: correctly identified. `infrahub_sync/potenda/__init__.py:92-93` — re-verified,
  `SKIP_UNMATCHED_DST` in the fallback flag set — means a delete-bearing plan is the default posture
  on any non-pristine destination, so this path is the common one and deserves the recorded treatment
  it now gets.
- **Warning visibility**: `apply_cmd` takes verbosity from `ctx.obj.get("verbosity", logging.INFO)`
  (`infrahub_sync/cli.py:310`), so a `WARNING` on the run's logger is genuinely operator-visible at
  the default level, and `caplog`-assertable. Fine.
- **`applied` feeding warm starts**: data-model.md:312-318 records the consequence rather than
  discovering it — `_SUCCESS_STATUSES = frozenset({"applied", "dry-run"})`
  (`infrahub_sync/cache/incremental.py:24`). Re-verified. No behavioral problem, correctly disclosed.

**R6 — Recommended.** data-model.md:332-335 states it loosely: "`len(applied_operations) +
skipped_delete_count` equals the plan's `operations_count` **whenever the apply completes without a
rejection**, and `set(applied_operations) | set(skipped_delete_operations)` equals the plan's full
identifier set. A partial apply (AD027) breaks **the first equality** by construction". A partial
apply breaks both; the union clause is not conditioned and the sentence implies it survives. If an
implementer follows data-model.md rather than T047, the union check fires during partial-apply error
handling and replaces a clear destination-rejection message with an assertion error. Also, tasks.md:310
says "Assert the knowability invariant **in the loop**" — it is a post-loop condition; asserted inside
the loop it fails on iteration one. Two words each. And prefer a raised named error over `assert` in
product code, per repository standards (`S101` is globally ignored in `pyproject.toml:272`, so `assert`
would pass lint — that is not the same as being right here).

**R7 — Recommended.** Two artifacts still assert the outcome AD055 abolished:
`checklists/write-convergence.md:74` (CHK030, marked `[X]`: "does the apply perform no writes and
still end in a **failed** state?"), `:140` ("force a spurious failed apply under SC-007"), and
`checklists/reviews/write-convergence-review.md:45` ("FR-017 + FR-016 + FR-022 fully determine a
delete-only plan: no writes, **failed run**", verdict SATISFIED). No implementation task derives from
these, which is why this is not blocking — but a satisfied checklist item recording the abolished
behavior is exactly the residue that gets cited back later. Restate CHK030 and the review row against
AD055. `tasks.md`, `plan.md`, `data-model.md`, `spec.md`, `quickstart.md` and both contracts are
clean — I grepped every `delete` line against `fail`/`failed` and found no surviving product-side
assertion.

### The closed-`Literal` placement

**Sound in placement, under-specified in mechanism.**

Sound: an unrecognized action is refused while reading, therefore before any destination write, which
is where FR-017 clause 2 needs it (spec.md:1472-1478). The reported deviation is honest — with
`PlannedOperation.action` a `Literal` over `ACTIONS`, the apply loop genuinely cannot receive an
unrecognized action, so specifying FR-017's arm in the loop would have specified dead code. Placement
also means the refusal precedes checksum verification, so a hand-edited artifact reports the action
rather than the checksum; that is a defensible diagnostic precedence and T024 asserts the messages are
textually distinct from the delete wording, which is the conflation risk that mattered.

Three residues, all Recommended:

**R8.** tasks.md:184 and data-model.md:142-147 say the `Literal` "raises
`UnsupportedOperationActionError`". A `Literal` mismatch raises `pydantic_core.ValidationError`. The
named error needs a `model_validator(mode="before")` that checks `action` first and reads
`operation_id` out of the **raw input** (the model does not exist yet, so the identifier the message
must name — asserted by T024 and T054 — is not reachable from a `field_validator` unless field order
happens to cooperate). Pydantic v2 propagates non-`ValueError` exceptions, so this works; say so, and
keep the `Literal` for `ty`.

**R9.** T020's classification list (tasks.md:219) is written as exhaustive and has no arm for an
operations line that parses as JSON but fails model validation for any *other* reason — a `create`
missing `payload`, a mismatched stored `operation_id` (T015), a `cardinality: "one"` with two peers. A
raw `ValidationError` escapes to the CLI with no next action, which AD059 forbids across the whole
taxonomy and T089 will not catch because T089 walks the *declared* taxonomy. This is the most likely
corruption class in practice, and it reaches the operator before the checksum check does. One arm:
wrap per-line construction and re-raise as `PlanArtifactTornError` naming the line number and the
field, with the action case taking precedence.

**R10.** `load_plan_artifact` is the review path's reader too — contracts/plan-reader-api.md:61 ("a
thin renderer over this object and re-implements no reading"). So `diff --from-plan` now refuses a
plan carrying an unrecognized action. Nothing states that, no task tests it, and
contracts/plan-reader-api.md:52 says "review never refuses to show" (scoped to verification failures,
so not a strict contradiction — but a reader sitting one row above the refusal will read it as one).
Since such an action can only come from a *newer* writer, this is FR-027's forward-compatibility case
arriving at the operation level: decide whether review renders it with a note or refuses, state it,
and add the case to T026 or T024.

## R11 — Recommended: the shared-helper correction reaches the live `sync` path

Round 1 asked for the ordering fix on both callers, and tasks.md:608-609 declares it a deliberate
exception to the "no change to the live `sync` write path" non-goal. Two costs are now real and
unrecorded:

1. **Semantics.** `update_node` (`infrahub_sync/adapters/infrahub.py:97`, body `:149-175`) goes from
   additive to destructive replace-set on every `sync`. Peers a destination holds that the source does
   not will now be **removed**. That is the intended semantics and arguably a bug fix, but it is a
   data-affecting behavior change on the path the brief scopes out, and nothing in plan.md's risk
   table records it.
2. **Cost.** Forcing the re-read (R1) means one extra destination round trip per cardinality-many
   relationship per node on every live `sync`, where today the `if not attr_manager.initialized`
   guard at `:168-169` usually skips it.

T042's Done-when covers `tests/adapters/` passing plus one new test for the new behavior; there is no
assertion pinning the live path's *new* removal semantics, and no note that operators will see peers
disappear. Add a risk row and one `update_node`-level test, or scope the correction to the new caller
and accept the duplicated logic — but decide it rather than inherit it.

---

## Code facts re-verified at head `844e98f`

| Fact | Verdict |
|---|---|
| V12 as corrected — `update_node` reads `peer_ids` at `:151`, compares at `:166`, fetches at `:168-169`, so it adds without removing | **Correct** (`infrahub_sync/adapters/infrahub.py:151,166,168-169,171-175`) |
| V12a — `self.initialized = data is not None`; `fetch()` returns early when initialized | **Correct** (`.venv/…/infrahub_sdk/node/relationship.py:264`, `:286-288`). And it is why the prescribed "fetch first" does not re-read — R1 |
| V12b — keyedness is in the rendered mutation input; upsert renders with `exclude_hfid=False` | **Correct**; cited lines off by one (`.venv/…/infrahub_sdk/node/node.py:294-297`, upsert `:1843-1844`, dispatch `:1533-1534`) |
| V22 as corrected — the schema-subhash abort is unreachable | **Correct** (`infrahub_sync/cli.py:322-323,325,330,332,336-340,341-342`; the grep returns only those three lines) |
| AD062's premise — `summary` is `dict[str, Any]` inside the closed `KEYS`, `cache/` needs no change | **Correct** (`infrahub_sync/cache/sidecars.py:73,76`) |
| `RunFile.save()` writes the whole payload from the in-memory instance; no merge on save | **Correct** (`:87-89`; `load_or_default` at `:79-85`) — the basis of R5 |
| AD055's "ordinary case" premise — the fallback flag set yields deletes on any non-pristine destination | **Correct** (`infrahub_sync/potenda/__init__.py:92-93`) |
| `applied` counts as a successful prior run for warm starts | **Correct** (`infrahub_sync/cache/incremental.py:24`) |
| V39 / the write-surface fact table — a bare-id relationship renders without `__typename`, the store is never consulted, one `None` nulls the HFID, the mutation goes out unkeyed | **Correct** (`.venv/…/infrahub_sdk/schema/__init__.py:172-181`; `.venv/…/infrahub_sdk/node/related_node.py:64-68,298-304`; `.venv/…/infrahub_sdk/node/node.py:100-107,135-139,294-297`) — the basis of R3 |
| `SLF001` and `S101` are globally ignored, so T081's `_generate_input_data` access and an in-product `assert` are lint-clean | **Correct** (`pyproject.toml:161,272,278`) |
| `pyproject.toml` still has no `[[tool.ty.overrides]]` | **Correct** |
| Task count | **90** (`grep -c "^- \[ \] T\|^- ~~T"`), matching the stated 90 |

No code fact recorded in the artifacts is now wrong. Both round-1 corrections (V12, V22) landed
accurately, and V12a/V12b are new and accurate. The defects in this round are all in what the
artifacts *do* with those facts, not in the facts.

## Green-tree and phase-ordering claims

Still hold. S/A/B/C pure additions; D explicitly not fully green with the sweep in G/T067
(tasks.md:277-280 and plan.md's statement agree); E green because T066 lands with T048 in the same
change (tasks.md:342-343, :552-553, :583); F and G green. The phase chain S→A→B→C→D→E→F→G is
dependency-ordered and T060's removal did not break the Phase F sequence (tasks.md:570). One
inconsistency, R12: tasks.md:568 still introduces T042 as "a behavior-preserving extraction", which is
what AD054 exists to deny.
