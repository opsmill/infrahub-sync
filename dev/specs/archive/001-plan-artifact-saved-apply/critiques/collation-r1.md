# Critique collation — round 1

Three lenses ran in parallel against the brief, `spec.md`, `plan.md` and `tasks.md` at commit
`016802e`. Reports: `engineering-r1.md` (434 lines), `ergonomics-r1.md` (549), `fidelity-r1.md` (560).

**17 blocking findings** (5 engineering, 10 ergonomics, 2 fidelity), deduped below into **11 themes**.
Root verified the contested and load-bearing findings against the tree before disposition; each
carries a verdict of CONFIRMED, OVERSTATED or REFUTED. A finding surviving only as an unverified
assertion does not become a decision.

## Cross-lens theme table

| Theme | Engineering | Ergonomics | Fidelity | Disposition |
|---|---|---|---|---|
| Offline convergence evidence is weaker than claimed | E1, E2, E3 (must) | — | — | **AD054 — blocking** |
| Deletes recorded by default make an ordinary apply fail | — | ERG-02 (must) | (F6 adjacent) | **AD055 — blocking, needs human** |
| A missing delete class is invisible at review | — | ERG-03 (must) | F1 (must) | **AD056 — blocking** |
| `--run-id` carries two inverse meanings | — | ERG-01 (must) | — | **AD057 — blocking** |
| Reader/write-surface contracts don't match their own API | — | ERG-05, ERG-06, ERG-08 (must) | — | **AD058 — blocking** |
| Failure messages without a next action | — | ERG-04, ERG-10 (must) | — | **AD059 — blocking** |
| Quickstart steps that do not work as written | — | ERG-07 (must) | — | **AD060 — blocking** |
| New flags' help text unspecified; `--run-id` help now false | — | ERG-09 (must) | — | **AD061 — blocking** |
| FR-020's applied-operation set has no home | E4 (must) | — | — | **AD062 — blocking** |
| The corrected refusal path is dead code | E5 (must) | — | — | **AD063 — blocking** |
| DBA-006 is conditionally carried, reported as plainly carried | — | — | F2 (must) | **AD064 — blocking** |
| Brief-gaps for planner feedback | — | — | F2, F3, F6, F9 | Planner feedback; no code change |

## Verification of contested findings

| Finding | Verdict | Evidence root checked directly |
|---|---|---|
| **E1** — replace-set is a no-op against the real SDK | **CONFIRMED** | `infrahub_sdk/node/relationship.py:264` — `self.initialized = data is not None`, peers populated from that data. A node built locally from `create_data` therefore already reports the desired peer set, so `compare_lists` finds nothing to add or remove and `fetch()` never fires. The mitigation can only pass against a mock. |
| **E1b** — today's "replace-set" is additive | **CONFIRMED** | `infrahub_sync/adapters/infrahub.py:151` reads `attr_manager.peer_ids` **before** `if not attr_manager.initialized: attr_manager.fetch()` at `:168-169`. V12 is overstated. |
| **E5** — the schema-subhash refusal path is unreachable | **CONFIRMED** | `_resolve_infrahub_schema` is referenced at `cli.py:330,332` and **defined nowhere** in the package; the comment at `cli.py:325` says "Plan 2 will provide" it. The `except ImportError: pass` at `cli.py:341` swallows the whole block, so the abort at `:336-340` is dead. V22 is wrong, and T060 "fixes" code that cannot run. |
| **E2** — step 3b passes while the mutation is unkeyed | **CONFIRMED by construction** | Consistent with the run's own V39 finding that a relationship-crossing component yields `hfid=None`; the contract's `LocationRack` worked example is exactly that shape. Step 3b asserts against `data`, but keyedness is a property of the rendered mutation. |
| **E3** — "two applies produce one create" is vacuous | **CONFIRMED** | A mock holds no destination state; two applies issue two creates. The assertion cannot fail for the right reason. |
| **E4** — FR-020 has no storage location | **CONFIRMED** | `cache/sidecars.py` `RunFile.KEYS` is a closed tuple (`status`, `mode`, `summary`, `finished_at`); the plan declares `cache/` unchanged; a task nonetheless requires reading the applied set back from `run.json`. Three rules, at most two can hold. |
| **ERG-02** — recorded deletes fail ordinary applies | **CONFIRMED, and mandated by the brief** | DBR-009 requires recording deletes by default; DBA-007 and DBR-016 require a plan containing a delete to end `failed`. Under `SKIP_UNMATCHED_DST` (the engine default, `potenda/__init__.py:92-93`) any destination holding mapped objects absent from the source now yields deletes. Not resolvable by this run — see AD055. |
| **ERG-01** — `--run-id` overload | **CONFIRMED** | `cli.py:98` documents the existing meaning ("Re-use a specific cache run id"); `utils.py:244-246` creates an unknown one. Two inverse meanings discriminated by an omissible flag. |
| **ERG-07** — quickstart steps fail | **CONFIRMED** (reviewer reproduced two empirically) | The heredoc passes no argument; `git stash` on a committed tree is a no-op so the SC-012 "before" baseline is captured from the post-change binary. |
| **F1** — a missing delete class is invisible at review | **CONFIRMED** | FR-006 fixes the summary at counts-by-action and counts-by-kind; `PlanSummary` carries `by_action`/`by_kind`/`total`; the delete-computation flag reaches a review surface only in one illustrative sample. AD024's "explicit and reviewable" justification is not actually delivered by any MUST. |
| **F2** — DBA-006 conditionally carried | **CONFIRMED** | The delete-computation field sits inside the checksummed manifest and outside the brief's two-field mask, and the operations section differs between extraction modes. SC-006 rescues the criterion with a pinned-mode precondition, but the traceability table still reports DBA-006 as plainly carried. |

Nothing was OVERSTATED or REFUTED this round. Two artifact-recorded code facts were falsified by the
lenses (V12, V22) — both are corrected by AD063 and AD054.

## Decision records

### AD054 — Make the offline convergence evidence real, or stop calling it evidence

**Question:** The offline conformance harness is the only compensation for five brief acceptance
criteria that cannot be evidenced without a live destination. Engineering finds two of its three
assertions prove only that a mock was called. What replaces it?

**Evidence:** E1, E2, E3, all CONFIRMED above.

**Options:** **A** build the harness against a real `InfrahubNodeSync` constructed from a committed
`NodeSchemaAPI` fixture, and assert on the **rendered mutation input** (`id` or `hfid` present) rather
than on the assembled `data`; fix the replace-set to re-read the destination peer set before
comparing · **B** keep the mock harness and downgrade the claim · **C** drop the harness and rely
wholly on the deferred live run.

**Recommendation:** **A**.

**Rationale:** A converts the harness from "the mock was called" into a genuine offline *precondition*
for convergence — it would have caught AD042, the run's worst defect, and it is checkable without a
destination because the SDK renders the mutation locally. B leaves a compensating control that reads
stronger than it is, which is worse than no control. C removes the only offline signal on the exact
path five deferred criteria cover. A also fixes the real bug underneath: the replace-set must
re-read the destination's peer set, and the existing additive ordering in `update_node` is a
pre-existing defect worth correcting while we are there.

**Confidence:** High. **Origin:** `inherent`.

### AD055 — Deletes recorded by default make the ordinary apply end `failed`

**Question:** DBR-009 makes recording deletes the default; DBA-007 and DBR-016 make any plan
containing a delete end the apply in a `failed` state. Under the engine's default flags, most real
destinations will produce deletes. Is "every apply fails" the intended outcome?

**Evidence:** ERG-02, CONFIRMED. `SKIP_UNMATCHED_DST` is the fallback flag set
(`potenda/__init__.py:92-93`), so destination-only objects are the normal case rather than an
exception; the migration documentation describes exactly that posture.

**This run cannot resolve it.** Both halves are explicit brief requirements: DBR-009 ("Record delete
operations in the plan, changing today's default of suppressing them"), and DBA-007 plus DBR-016
("the delete is reported and the run fails, never silently skipped"). Softening either would be
reassigning product scope, which is out of bounds in every decision mode. The options below are
therefore presented to the human, not chosen.

**Options for the human:** **A** ship as the brief specifies — a plan containing a delete fails the
apply — and accept that operators on non-pristine destinations see `failed` until DB-014 lands
delete execution · **B** distinguish "recorded, intentionally not executed" from a genuine unsupported
operation, so a delete-bearing plan applies its non-delete operations and completes in a distinct
non-`failed` state that names the skipped deletes — this contradicts DBA-007 as written and needs a
brief revision · **C** make delete recording opt-in, contradicting DBR-009.

**Recommendation to the human:** **A** for this run, with the tension reported to planning as a
brief-gap. It is the only option that does not change what the brief says ships. But B is what the
feature probably wants, and that is a planning decision, not mine.

**Confidence:** High that the tension is real; the resolution is not mine to make.
**Origin:** `brief-gap`, repair level `instance`. Brief section: Out of scope / DBA-007. Missing
content: that recording deletes by default combined with failing on any recorded delete means the
default posture of the qualified path yields a failed apply — and whether that is intended.

### AD056 — Both review depths must surface the delete-computation record

**Question:** AD024 omits the whole delete class when the destination was loaded incrementally,
justified by the omission being "explicit and reviewable". Is it reviewable?

**Evidence:** F1 and ERG-03, both CONFIRMED. The flag lives in the manifest; no requirement puts it
on a review surface.

**Recommendation:** FR-006, `PlanSummary`, SC-009's pass condition and the review-rendering task all
carry a normative obligation to surface the delete-computation record and say plainly when deletes
were not computed. Non-zero delete counts are annotated inline in both summary and detail to say no
delete will be executed.

**Rationale:** Without this, a plan missing its entire delete class is indistinguishable from a plan
with no deletes — and AD024's whole justification for the omission evaporates. This is the disclosure
that makes AD024 defensible; without it AD024 should not stand.

**Confidence:** High. **Origin:** `inherent` — a defect in AD024's own delivery.

### AD057 — Review takes the run id as the review flag's value

**Question:** Keep `--run-id <id> --from-plan`, or fold the id into the review flag?

**Evidence:** ERG-01, CONFIRMED. Without `--from-plan`, `--run-id` is a write target whose unknown
value is silently created and whose existing plan is overwritten; with it, a read source that errors
on an unknown value. The discriminator is omissible, so forgetting one flag turns a read into a
destructive write against the very artifact being read.

**Recommendation:** `--from-plan <run-id>`.

**Rationale:** The brief makes flag spelling an explicit implementation choice within one fixed
constraint (no new command group), so this is fully in scope. It removes the overload, the
missing-flag accident, and the separately-specified "`--from-plan` without `--run-id`" error case at
no cost. Two inverse meanings behind an omissible flag is not a contract an operator can hold.

**Confidence:** High. **Origin:** `inherent`.

### AD058 — The contracts must match their own declared APIs

Three concrete mismatches, all CONFIRMED by reading the contracts against each other: the write
surface calls `peers.resolve_one`/`resolve_many` where `PeerResolver` defines only `resolve(...)`;
`verify_plan` receives a boolean where the promised message names the adapter; and
`SavedPlan.operations(kind=…)` raises on an empty result, pushing a CLI presentation rule into the
data API against FR-029's own "consumes it without parsing output". Fix: rewrite against the real
signatures, pass the adapter name, return `[]` for a declared kind and raise only for an undeclared
one. **Origin:** `inherent`.

### AD059 — Every failure names a next action

Nine failures currently leave the operator without one: torn artifact, unrecognized format version,
unreadable path, unknown run id, `--kind` matching nothing, derivation failure on `diff`, peer
zero-match, peer multi-match, unserializable payload. Two of them echo the operator's own input while
withholding an enumeration already in hand. AD036 attached the next-action obligation to *refusals*
only, so the reader and derivation errors never inherited it. Fix: extend the obligation to the whole
error taxonomy, and have the unknown-kind and unknown-run-id errors list the values that do exist.
**Origin:** `inherent`.

### AD060 — The quickstart must execute as written

Two steps were empirically reproduced as broken: a heredoc that passes no argument and so resolves a
path at the repository root, and a `git stash` baseline that is a no-op on a committed tree — meaning
SC-012's "before" help is captured from the post-change binary and the comparison diffs a file
against itself, passing without a baseline. The third depends on AD055. Fix the first two; the third
follows whatever AD055 resolves to. **Origin:** `inherent`.

### AD061 — Help text is specified, not left to the implementer

No artifact specifies the new flags' help strings, and `--run-id`'s existing string becomes false
under AD057. A task regenerates the CLI reference documentation from whatever strings appear. Fix:
specify the help text for each new flag and the corrected `--run-id` string in the CLI contract, so
the generated documentation is reviewed rather than discovered. **Origin:** `inherent`.

### AD062 — FR-020's applied-operation set gets one named home

`RunFile.KEYS` is closed, the plan declares the cache layer unchanged, and a task requires reading the
applied set back from `run.json`. At most two of those three can hold. Fix: one task pins the
location — extend `RunFile` with an explicit field, or state `summary["applied_operations"]` — and
every task that reads or writes it references that decision. FR-020 is a contract DB-012 consumes, so
it cannot be left implicit. **Recommendation:** `summary["applied_operations"]`, since `summary` is
already a free-form dict inside `RunFile.KEYS` and this avoids changing a persisted schema other code
reads. **Origin:** `inherent`.

### AD063 — Do not "fix" dead code; correct the record instead

The schema-subhash refusal path cannot execute: `_resolve_infrahub_schema` does not exist, so the
import raises and `except ImportError: pass` swallows the block. V22 is wrong, and AD010's incidental
repair of the `status: running` leak repairs something unreachable — the only way its test could pass
is against an injected stub.

**Recommendation:** correct V22; drop the dead-path repair task and its test case from this run. The
run-state vocabulary decision in AD010 stands on its own for the **new** refusal paths, which is what
DBA-004 actually needs. Do not make the dead check live — that is unrelated scope.

**Rationale:** Fixing unreachable code produces a test that proves nothing and a changelog entry that
misleads. Removing the task is also the smaller change, and it retires the one piece of unauthorized
scope fidelity flagged (its F10).

**Confidence:** High. **Origin:** `inherent`.

### AD064 — Report DBA-006 as conditionally carried

The traceability table reports DBA-006 as plainly carried while SC-006 rescues it with a
pinned-extraction-mode precondition, and User Story 2 scenario 5 restates the brief's unconditional
wording. Fix: mark it conditionally carried with the condition named, in both places. The engineering
rescue is sound; only the reporting overstates it. **Origin:** `inherent` (the underlying brief-gap is
filed separately as F2).

## Brief-gaps for planner feedback — no code change

| ID | Brief section | Missing content | Repair |
|---|---|---|---|
| F2 | Edge cases; DBA-006 | Whether deletes are omitted on an incremental destination load and how that is disclosed; and whether byte-determinism holds across extraction modes | `instance` |
| F3 | DBR-017; DBA-010 | A definition of "secret" — imported from another card without one — and the canary's injection point | `instance` |
| F6 | Edge cases | A reviewed `update` whose target vanished: the mandated convergent write creates it, colliding with the Outcome's "applied equals reviewed" and the freshness/conflict exclusion | `instance` |
| F9 | Assumptions | "A live Infrahub is reachable in the implementation environment", with impact if wrong. The slot carries two other assumptions and was left empty on this one | `instance` |
| AD055 | Out of scope; DBA-007 | That recording deletes by default plus failing on any recorded delete means the qualified path's default posture is a failed apply | `instance` |

F9 has a systemic dimension worth noting to the planner even though it is filed `instance`: the
completion-condition template admits no notion of environment-gated evidence, so a brief cannot
express "this criterion needs a live dependency" without it reading as an unmet condition.

## Fidelity walk summary

Nothing unowned, nothing exceeded without justification. Three brief items are narrowed and all three
are disclosed: DBR-001 and DBR-009 (conditional on extraction mode, AD024) and DBR-017 (narrowed to
`settings` credentials, AD018). Four are exceeded toward a stronger bar: five pre-apply checks rather
than three, six negative cases rather than five, the pre-existing-peer requirement added to DBA-008,
and no new command at all rather than no new group. All nine out-of-scope boundaries hold.

## Round disposition

Eleven blocking themes route to remediation as AD054–AD064, except **AD055**, which is presented to
the human at the Phase 4 gate because resolving it would require changing what the brief says ships.

Re-run after remediation: **engineering** and **ergonomics** (their inputs change substantially).
**Fidelity** re-runs because AD055's disposition and AD056's disclosure obligation both move
requirements.
