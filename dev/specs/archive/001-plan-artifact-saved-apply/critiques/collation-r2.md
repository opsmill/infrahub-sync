# Critique collation — round 2

Three lenses re-ran against the artifacts at commit `844e98f`, after round 1's eleven remediation
decisions and the human's ratified AD055 override. Reports: `engineering-r2.md` (415 lines),
`ergonomics-r2.md` (552), `fidelity-r2.md` (503).

**9 blocking findings** (5 engineering, 3 ergonomics, 1 fidelity), deduped into **10 themes** below
(one Recommended finding is promoted because it corrects a recorded justification). Root verified the
load-bearing findings against the tree; verdicts are recorded per theme.

**The headline of this round:** round 1's remediation agent reported E2 as closed when it was not, and
prescribed a fix for E1 that its own recorded facts show is a no-op. Two rounds running, a
write-producing agent's self-report has overstated what it delivered. That is the entire argument for
keeping reviewers separate from remediators, and it is why round 3 will run.

## Cross-lens theme table

| Theme | Engineering | Ergonomics | Fidelity | Disposition |
|---|---|---|---|---|
| The relationship re-read mechanism is a no-op | R1 (must) | — | — | **AD065 — blocking** |
| Step 3b still gates on `data` while claiming otherwise | R2 (must) | — | — | **AD066 — blocking** |
| The conformance fixture cannot satisfy its own assertion | R3 (must) | — | — | **AD067 — blocking** |
| The idempotence assertion is still vacuous | R4 (must) | — | — | **AD068 — blocking** |
| Two writers of `run.json`; the record is overwritten | R5 (must) | — | — | **AD069 — blocking** |
| Fixing `update_node` reaches the live `sync` path | (R1 adjacent) | — | R2-F1 (must) | **AD070 — blocking** |
| Two derivation failures have no exception class | (R9 adjacent) | ERG-21 (must) | — | **AD071 — blocking** |
| A quickstart case errors for the wrong reason | — | ERG-22 (must) | — | **AD072 — blocking** |
| The run-id enumeration is unbounded and can raise | — | ERG-24 (must) | — | **AD073 — blocking** |
| AD055's authority is miscited | — | — | R2-F2 (rec.) | **AD074 — promoted** |

## Verification of the load-bearing findings

| Finding | Verdict | Evidence root checked directly |
|---|---|---|
| **R1** — the prescribed re-read is a no-op | **CONFIRMED** | `infrahub_sdk/node/relationship.py:286` — `fetch()` opens `if not self.initialized:`. On a locally-built node `initialized is True` (`:264`), so a "fetch first" helper performs no destination read and the reconciliation remains the guaranteed no-op AD054 existed to remove. The artifacts state that guard three times and then prescribe the one fix it defeats. |
| **R5** — the apply record is silently overwritten | **CONFIRMED** | `cache/sidecars.py:87-89` — `save()` writes `{k: getattr(self, k) for k in self.KEYS}`, a whole-payload write with no merge. `cli.py:322` constructs a second `RunFile` with a default empty `summary`, and `:351` saves it **after** `apply_plan()` returns. Every key AD062 and AD055 place in the summary is destroyed. |
| **R2-F1** — the fix reaches the live `sync` path | **CONFIRMED** | `update_node` has exactly one caller: `adapters/infrahub.py:625`, inside `InfrahubModel.update` — the live `sync` write path. Correcting its ordering makes `sync` start **removing** destination relationship peers absent from the source, on configurations that have never removed one. This contradicts AD048, T044 and T053, which each assert the live path is untouched. Root introduced this in AD054's third clause. |
| **R2** — step 3b never moved | **CONFIRMED** | The round 1 remediation reported this closed. It is not: the gate remains per-component over `data`, and the flat guarantee "an unkeyed write is never issued" survives in three places. Only the offline detector changed. |
| **R3** — the fixture defeats the assertion | **CONFIRMED by construction** | Consistent with the artifacts' own verified facts: a relationship-crossing-HFID operation carries no `id` (no destination load, per DBR-004) and no `hfid` (a bare-id relationship renders without `__typename`, so the store is never consulted and one `None` nulls the HFID). The task mandates that fixture and then asserts every operation is keyed. |
| **R4** — assertion 3 vacuous | **CONFIRMED** | Materially the same sentence survives, and the done-condition now pins the impossible reading while the task's own diagnosis four lines above says two applies issue two creates. |
| **ERG-22** — the quickstart case errors for the wrong reason | **CONFIRMED** | The case reads a run whose `plan/` was removed two commands earlier, so it raises the pre-existing-format error rather than the unknown-kind error it claims to demonstrate; and the kind it names is commented out in the qualified config, so even repaired it would exercise the reader branch rather than the renderer branch. It errors, so it looks like it passed — the same false-pass shape AD060 was created to kill, reintroduced in the file AD060 repaired. |
| **AD055 legitimacy** | **CLEARED** | Fidelity verified the origin labels independently: DBR-009/DBR-010 are `QUOTED` and untouched; DBR-016/DBA-007 are `DERIVED` and are the pair that moved. The new basis carries in four places. The brief's Out-of-scope prose is a restatement introduced by "behaves as DBR-016 and DBA-007 specify:", carrying no identifier, origin label or criterion, so it moves with them — but brief v5 now reads false there and needs a v6. |

Nothing was OVERSTATED or REFUTED. No code fact recorded in the artifacts is now wrong; both round 1
corrections landed accurately. **The defects this round are in what the artifacts do with correct
facts** — which is a harder class to catch and the reason round 2 was not optional.

## Decision records

### AD065 — Name a re-read mechanism that actually reads

**Question:** How is the destination peer set genuinely re-read before comparison?

**Evidence:** R1, CONFIRMED. `fetch()` self-guards on `initialized`, which is `True` on a
locally-built node.

**Options:** **A** force the manager cold before fetching (`initialized = False`, then `fetch()`) ·
**B** issue a scoped destination read for the relationship and compare against its result · **C**
leave it and drop the replace-set guarantee.

**Recommendation:** **A** or **B**, named explicitly rather than left as "fetch first" — and the
observable in the test must become "issued a destination read for the relationship before the peer
set was read", not "was fetched", because the no-op satisfies the latter.

**Rationale:** C abandons SC-008. The failure mode here is precise and instructive: the fix was
described in terms of *call order* when the defect is *whether a read happens at all*, so the
prescribed change and its test would both have passed while changing nothing.

**Confidence:** High. **Origin:** `inherent`.

### AD066 — Move the keyedness gate, or stop claiming it

**Question:** Step 3b asserts against the assembled `data`, but keyedness is a property of the
rendered mutation. Which moves — the gate or the claim?

**Recommendation:** Move the gate onto the rendered mutation input, keeping the per-component check as
a diagnostic. If that is not done, strike the flat "an unkeyed write is never issued" guarantee from
all three places. **Not both** — the current state asserts a guarantee the gate cannot deliver.

**Rationale:** This is the assertion five deferred live criteria lean on. A guarantee that reads
absolute while resting on a weaker check is worse than an honestly narrower one.

**Confidence:** High. **Origin:** `inherent`.

### AD067 — Split the conformance assertion so the known hole is visible

**Question:** The offline harness mandates a relationship-crossing-HFID fixture and then asserts every
operation is keyed, which that fixture cannot satisfy.

**Recommendation:** Split it — all-direct-identity kinds MUST be keyed; the relationship-crossing kind
is asserted **unkeyed today** under a strict expected-failure citing the recorded material risk, so it
flips automatically when the hole closes.

**Rationale:** A strict xfail records the limitation as a live, self-invalidating fact instead of prose
in a risk table. The alternative — dropping the fixture — would remove the only offline signal on the
path that most needs one.

**Confidence:** High. **Origin:** `inherent`.

### AD068 — Assert something a repeated apply can actually falsify

**Recommendation:** Replace "two applies produce exactly one create" with "two applies render
byte-identical **keyed** mutation inputs", and update the six downstream restatements.

**Rationale:** A mock holds no destination state, so the original can only pass for the wrong reason,
and the done-condition had hardened the impossible reading. Byte-identical keyed inputs is the
strongest claim that is actually checkable offline, and it is the property convergence rests on.

**Confidence:** High. **Origin:** `inherent`.

### AD069 — One owner for the run record

**Question:** Both the engine and the CLI write `run.json`, and the CLI writes last with an empty
summary. Who owns it?

**Evidence:** R5, CONFIRMED — whole-payload write, no merge, CLI saves after the engine.

**Options:** **A** the engine returns a record the CLI merges before saving · **B** the engine owns
`run.json` for the apply path and the CLI's writes are removed there.

**Recommendation:** **A**, pinned in one sentence each in the tasks that write and read it.

**Rationale:** A keeps the CLI's existing responsibility for run state — which the refusal paths and
AD010 depend on — while making the summary additive. B would move a persisted-file responsibility
across a layer boundary that this brief does not otherwise touch. Left unfixed, the naive
implementation silently deletes FR-020's record, and the tests that would catch it sit two phases
later.

**Confidence:** High. **Origin:** `inherent`.

### AD070 — Confine the ordering fix to the new apply path

**Question:** AD054's third clause corrects `update_node`'s read-before-fetch ordering. `update_node`'s
only caller is the live `sync` write path. Does the fix belong here?

**Evidence:** R2-F1, CONFIRMED by root.

**Recommendation:** **No.** Confine the correction to the new apply-path helper. Leave `update_node`
untouched, and record its additive ordering as a pre-existing defect for a future brief to own.
Remove the contradiction between the task that changes it and the two tasks that assert the live path
is unchanged.

**Rationale:** The engineering defect is real, but fixing it here would make `infrahub-sync sync`
start removing destination relationship peers on configurations that have never removed one — a
user-visible change to an existing command, with no requirement, criterion, edge case or
documentation entry, in a brief whose out-of-scope list is explicit. That the correct fix is obvious
does not make it authorized. This was root's error in AD054, and it is the same class of mistake
AD048 was written to prevent.

**Confidence:** High. **Origin:** `inherent` — a scope-leak repair.

### AD071 — Every derivation failure gets a named class with a route

**Evidence:** ERG-21. Two of the four derivation failures have no exception class: an unformable
destination identity has none at all, and a source-store peer miss has only a class defined as a
*destination* miss with a destination remedy. The taxonomy sweep enumerates entries, so a condition
with no entry is never swept.

**Recommendation:** Add the two missing classes with next actions, and require the derivation task's
assertions to include the next action rather than only the kind and cause.

**Rationale:** `diff` is the most-run command, has never failed on data, and has no tolerance switch
on that path. Leaving the classes unnamed also lets an implementer raise a bare exception that bypasses
the base-class guarantee AD059 introduced.

**Confidence:** High. **Origin:** `inherent`.

### AD072 — Fix the quickstart case that errors for the wrong reason

**Recommendation:** Move the unknown-kind case above the step that removes the plan directory (or
re-plan in between), name a kind the qualified configuration actually declares, and label which branch
each case exercises — the reader's undeclared-kind raise versus the renderer's declared-but-empty
render.

**Rationale:** As written it raises the pre-existing-format error, so it appears to pass while
demonstrating nothing. That is precisely the false-pass shape AD060 was created to eliminate, back in
the same file. AD058 split those two branches deliberately; the walkthrough must exercise the one it
claims.

**Confidence:** High. **Origin:** `inherent`.

### AD073 — Bound the run-id enumeration and handle no runs

**Evidence:** ERG-24. Both contracts describe the enumeration as a directory listing already in hand,
but the path helper neither creates nor checks the directory, so on a sync that never ran the listing
raises. No task covers the no-runs case, and nothing bounds the list — run directories accumulate one
per `diff` or `sync`, with retention explicitly out of scope.

**Recommendation:** Enumerate the most recent N with the total when truncated (run ids sort by
construction), and give the no-runs case a stated message whose next action is to run `diff` first.
Add the case to the review-CLI test.

**Rationale:** The operator most likely to hit this is the first-time one, who would get a traceback
from a helpful-error path. Bounding it also stops an hourly pipeline turning a helpful enumeration into
thousands of lines.

**Confidence:** High. **Origin:** `inherent`.

### AD074 — Correct AD055's recorded authority (promoted from Recommended)

**Question:** On what authority was the derived pair re-derived?

**Evidence:** R2-F2. The spec cites approved decision D020, but D020 ratifies the **planner's**
derivations on a disclosure proviso. Read as AD055 reads it, D020 would license re-deriving 6 of 20
requirements and 10 of 13 acceptance criteria — which is plainly not what it grants.

**Recommendation:** Record the authority correctly as the brief owner's override at the delivery gate
(which the task file already states and the spec does not), and add the stronger ground the review
identified: DBR-016 governs an **unsupported** operation, while `delete` is a *recognized* action whose
**execution** the brief separately excludes — so DBR-016 arguably never reached recorded deletes at
all. Also note that brief v5 now reads false in its Out-of-scope bullet and Scenario 4, and needs a v6.

**Rationale:** Promoted from Recommended because a miscited authority is the kind of error that gets
copied. If the next brief's implementer reads AD055 as precedent for re-deriving derived requirements
under D020, this run will have taught something false. The narrower ground is also simply better: it
needs no override.

**Confidence:** High. **Origin:** `inherent`.

## Recommended, batched — not triggering another round

Applied alongside the blocking set where cheap: the union clause in the run-record model must be
conditioned on a completed apply (it currently reads unconditionally and would fire an invariant error
inside the destination-rejection path); two stale checklist rows still assert `failed` for a
delete-bearing plan; a closed-`Literal` validation failure raises an unnamed validation error rather
than the named one, and the operation identifier must be read from the raw input; an operations line
failing model validation for any other reason reaches the operator as a raw traceback with no next
action, ahead of the checksum check; the same reader serves review, so review now refuses a plan with
an unrecognized action — unstated, untested, and adjacent to "review never refuses to show"; `--kind`
without `--detail` is unspecified; the skip warning's level is never pinned and the operator's last
line names no skip; `--run-id` is silently ignored alongside `--from-plan`; and one stale line calls
the changed function "behavior-preserving".

## Round disposition

Round 2 produced new blocking findings, so the loop continues: remediate AD065–AD074, then **round
3**. Round 3 is the last permitted round — blocking residue after it makes the run `BLOCKED`.

Re-run in round 3: **engineering** (six of the ten themes are its) and **ergonomics** (three, plus the
quickstart and failure-catalogue walks). **Fidelity** re-runs because AD070 withdraws a change from
the live write path and AD074 rewrites a recorded justification — both move what the artifacts claim
about scope.
