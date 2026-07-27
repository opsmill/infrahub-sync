# Fidelity critique — round 2

**Lens**: fidelity (scope authority + brief conformance + planner feedback)
**Brief**: DB-001 `db-001-plan-artifact-saved-apply.md`, brief_version 5, batch-v3, primary card INFP-653
**Under review**: `spec.md` (2245 lines, FR-001…FR-030, SC-001…SC-018, AD001–AD064), `plan.md` (~750),
`tasks.md` (621, 90 tasks with T060 struck), `research.md`, `data-model.md`, `quickstart.md`,
`contracts/` ×4. No code written.
**Branch/head**: `001-plan-artifact-saved-apply-infp-653` @ `844e98f`, worktree clean
**Round 1**: `critiques/fidelity-r1.md` — F1 and F2 were Must-Address; F3–F13 Recommended/Nit.

**Verdict**: the brief remains one independently testable spec — **no `NEEDS_INTAKE_REVISION`**. F1 and
F2 are **closed**, thoroughly. The **AD055 re-derivation is legitimate on fidelity grounds** and softens
no brief requirement; the brief's Out-of-scope prose is a restatement that legitimately moves with the
derived pair, but the brief itself now needs a v6 revision so it stops asserting the superseded
behavior. **One blocking finding remains**, and it is not AD055: AD054 quietly folded a behavior change
to the **live `sync` write path** into this outcome — the same class of unauthorized existing-path scope
that round 1 raised as F10 and that AD063 had just retired, now with a data-removing blast radius.

---

## Findings

| ID | Severity | Classification | Summary | Anchor |
|---|---|---|---|---|
| R2-F1 | **Must-Address** | defect in the generated artifacts | AD054/T042 turns the live `sync` path's cardinality-many reconciliation from additive into a true replace-set, so destination peers absent from the source start being **removed** by the ordinary `sync` command. Unauthorized by the brief, contradicted by FR-014 and FR-016's own "the existing write path is unchanged", and contradicted by T044 in the same phase and file | `tasks.md` T042 :289-293; `spec.md` AD054 :687-689, FR-014 :1406-1409, FR-016 :1459-1461; `plan.md` V12 :111; `infrahub_sync/adapters/infrahub.py:149-175`, `:625` |
| R2-F2 | Recommended | defect + `brief-gap` (**systemic**) | AD055's stated authority is D020. D020 ratifies the **brief's** derived rows on a basis-disclosure proviso; it does not say a derived row may be re-derived downstream. As written the reasoning would license re-deriving 6 of 20 DBRs and 10 of 13 DBAs. The real authority is the brief's own approver overriding at the gate — which `tasks.md` says and `spec.md` does not | `spec.md` :782-786 vs `tasks.md` :88, :94; brief §Approved decisions, D020 |
| R2-F3 | Recommended | `brief-gap` (**instance**) | Brief v5 still says a delete-bearing run fails, in §Out of scope and in §User scenarios Scenario 4. Both are restatements that move with DBR-016/DBA-007, so nothing normative is contradicted — but the brief now contradicts the delivered spec on the record, and DB-012/DB-014 read this brief | Brief :98-99, :143-151; `spec.md` :772-819, :2130-2145 |
| R2-F4 | Recommended | defect in the generated artifacts | `spec.md` §Out of Scope is headed "Carried verbatim from the brief" while its first bullet is materially rewritten to the AD055 behavior. A reader diffing the two sections is told they agree | `spec.md` :1894 vs :1896-1902 |
| R2-F5 | Recommended | defect in the generated artifacts | The skipped-delete warning's log level is never pinned. `--quiet` sets the package logger to `WARNING`, so a sub-warning emission is suppressed — and that warning is the only operator-facing signal that the applied set differs from the reviewed set, which is the whole of what the re-derived DBR-016 now rests on | `spec.md` FR-017 :1470; `contracts/destination-write-surface.md` :59, :316; `infrahub_sync/cli.py:59-60` |
| R2-N1 | Nit | defect in the generated artifacts | Five round-1 non-blocking findings are unremediated: F4 (five checks recorded as coverage, not expansion), F7 (DBR-008's plural "checksums" resolved to one with the reading recorded nowhere), F11 (the shared-execution-core constraint still has no traceability row), F12 (FR-024 says "what is missing" where the brief says "identifier"), F13 (63 `[PROVISIONAL]` markers still live). Root's packet routed only the blocking themes, so this is expected — recorded so they are not lost | `spec.md` :1775-1776 equivalent rows :2168-2169; no `checksums` hit anywhere; :2150-2170; :1538; 63 markers |
| R2-N2 | Nit | defect in the generated artifacts | `tasks.md`'s Input header still reads "AD001–AD053" after eleven more decisions landed | `tasks.md` :9 |

Severity counts: **Must-Address 1**, **RETHINK 0**, Recommended 4, Nit 2.
`brief-gap` findings this round: **2** — R2-F2 (`systemic`), R2-F3 (`instance`).
Carried forward unrepaired from round 1: **4** `brief-gap`s, all `instance` (r1 F2, F3, F6, F9).

**A blocking finding remains: R2-F1.**

---

## The AD055 re-derivation — fidelity verdict

**Legitimate. It softens no brief requirement, and the substance of what ships is defensible against the
brief's own text. Two bookkeeping repairs are needed (R2-F2, R2-F3), neither blocking.**

I verified the human's reasoning against the brief's requirements table rather than taking it on trust.

**The origin labels are exactly as claimed.** Brief §Requirements: DBR-009 `QUOTED` (:165), DBR-010
`QUOTED` (:166), DBR-016 `DERIVED` (:172). Brief §Acceptance criteria: DBA-007 `DERIVED` (:189). So the
pair being re-derived is the derived pair, and the pair left untouched is the quoted pair. That much is
literally true.

**Nothing quoted changes.** I walked the quoted items the re-derivation could plausibly have reached:

- DBR-009 (record deletes, changing today's suppression default) — FR-015 is unchanged by AD055. Deletes
  are still derived and still written into the plan.
- DBR-010 (do not apply deletes) — FR-016 is unchanged in substance. AD004's structural guarantee holds:
  deletes never enter the comparison result the write path consumes, so a delete cannot reach a
  destination even by misconfiguration. AD055 only adds the framing that declining is designed rather
  than faulty.
- DBR-001 (a plan containing every proposed create/update/delete/relationship) — untouched.
- DBA-003 (`QUOTED, narrowed` by the brief itself) — SC-003 still excludes delete on the brief's own
  narrowing, unchanged.
- DBA-004's "the run does not reach an applied state" governs **refusals**, not a delete-bearing apply,
  so the new `applied` outcome does not collide with it.

**The basis carries, and carries at more than one place.** D020's proviso is that each derived item carry
its basis. The new basis for DBR-016 and for DBA-007 is stated inline at FR-017 (:1477-1484), again in a
dedicated §"Derived brief items re-derived here" table (:2130-2145) that prints the brief's original basis
beside the new one, again at AD055 itself (:800-808), and the traceability rows for DBR-016 and DBA-007
are both marked **re-derived** with the new basis summarized (:2111, :2122). The proviso is met more
thoroughly than the brief's own DERIVED rows meet it.

**The property DBR-016 protects is genuinely preserved, and I checked it rather than accepting the
argument.** The claim is that "provably knowable" — not `failed` — is what DBR-016 defends. Three things
make that hold in the delivered artifacts rather than only in the prose:

1. FR-020 gives the applied-operation set one named home, `summary["applied_operations"]` (AD062), and
   FR-017 puts the skipped-delete count and identifiers under their own keys in the same summary, so the
   reviewed set, the applied set and the difference are readable from one place.
2. SC-007 asserts the closure directly: "the identifiers recorded as applied plus the identifiers
   recorded as skipped account for **every** operation the plan contained". T054 encodes it as
   `set(applied) | set(skipped) == plan identifier set` with the lengths summing to
   `manifest.operations_count`, and T078 repeats it on the live half.
3. SC-007 makes `failed` a **failing** state for the criterion, and T054 asserts that explicitly, so the
   re-derivation cannot silently revert to the old reading in implementation.

That is a stronger reading of "never silently skipped" than the failed state was: `failed` forced the
difference into view without recording what it consisted of.

**A second, stronger basis is available and under-used.** DBR-016's own term is "an **unsupported
operation**". A `delete` is a member of the closed action vocabulary and a fully recognized operation;
what the brief excludes is *executing* it (§Out of scope, first bullet). So the split FR-017 draws —
between an operation this release declines to execute by design and an operation whose action it does not
recognize — is arguably a reading of DBR-016's existing term rather than a re-derivation of it. The
artifacts do draw the split (FR-017 clauses 1 and 2, User Story 4 scenario 3, two distinct Edge Cases) but
never make this argument, resting instead on the weaker D020 one. Worth adding: it needs no override at
all.

**The Out-of-scope prose question — my ruling: restatement, and it moves with them.** Brief :98-99 reads
"Until DB-014 ships, a plan containing a delete behaves **as DBR-016 and DBA-007 specify**: the delete is
reported and the run fails, never silently skipped." The construction names DBR-016 and DBA-007 as the
authority and then restates them after a colon. It carries no requirement ID, no origin label, and no
acceptance criterion, and it sits inside a bullet whose normative content is the **exclusion** ("Applying
a delete to the destination"), which is honored and honored structurally. When the two items it defers to
are re-derived, the restatement follows. The same ruling applies to brief §User scenarios Scenario 4
(:143-151), which is near-verbatim DBA-007 plus its rationale — and the section as a whole is narrative
restatement, since Scenarios 1, 2 and 3 mirror DBA-001, DBA-002 and DBA-004 the same way.

So the re-derivation does not contradict normative brief content. It does leave brief v5 asserting, in two
places, a behavior the delivery deliberately does not implement — which is R2-F3, a planner repair rather
than a delivery defect.

**One interaction I checked and cleared.** AD055 makes a delete-bearing apply record `applied`, and
`applied` is in the incremental gate's success set (`cache/incremental.py:24`), which under AD024 means a
later warm run computes no deletes at all. That looks at first like AD055 eroding DBR-009 in the steady
state. It does not: a successful plan run already records `dry-run` (`cli.py:154`), which is also in that
success set, so the warm path was reachable before AD055 and independent of it. The spec discloses the
warm-start consequence at :812-815 and in Assumptions :2050-2054, and the omission itself is disclosed at
both review depths under AD056. No finding.

**No new run status, as claimed.** `RunFile` keeps `pending | running | dry-run | applied | failed`; the
count lives in the free-form `summary` mapping already inside the closed key set, and the successor note
promoting it to a first-class field is recorded at AD055, FR-017 and the re-derivation table.

---

## R2-F1 — Must-Address — the live `sync` write path's relationship semantics are changed

**Classification**: defect in the generated artifacts.

AD054 (`spec.md`:687-689) carries a third clause beyond the two the collation packet described: "the
pre-existing additive ordering in the **live update path** is corrected while we are there". T042
(`tasks.md`:289-293) implements it, and says so without hedging:

> **The extraction is deliberately NOT ordering-preserving, and that is the point of the task (AD054).**
> … The helper must **fetch first, then read `peer_ids`, then `compare_lists`, then remove `existing_only`
> and add `new_only`**. Correcting the ordering here rather than only on the new caller is required
> because the helper is shared.

Its Done-when is explicit that live behavior changes: "a manager whose destination peer set differs from
the desired one has its `existing_only` peers **removed**, which the pre-extraction code did not do. That
test must fail against the pre-extraction ordering."

I verified the blast radius in the tree. `update_node` (`adapters/infrahub.py:97`) reads
`attr_manager.peer_ids` at `:151` and only calls `fetch()` at `:168-169`, so today `existing_only` comes
back empty and it adds without removing. Its single caller is `InfrahubModel.update`
(`adapters/infrahub.py:625`) — the **live DiffSync `sync` write path**, not the new saved-plan path. So
after T042, `infrahub-sync sync` starts **removing destination relationship peers** that the source does
not carry, on configurations where it has never removed one.

Three things make this a fidelity finding rather than an engineering call:

1. **The brief does not authorize it.** In scope names "A destination write surface on the Infrahub
   adapter capable of executing a planned create or update convergently" and "Apply-time relationship
   peer resolution". Neither reaches the live `sync` write path. §Completion conditions requires that "no
   out-of-scope behavior is introduced". Nothing in §Constraints or §Out of scope contemplates changing
   what `sync` does to existing destination relationships — which is the most consequential class of
   change this repository can make, because it removes data.
2. **The specification says the opposite in two requirements.** FR-014 (:1406-1409): "The existing live
   write path's warn-and-continue on an unresolvable peer is unchanged by this feature: it is existing
   behavior on an existing path that this outcome **does not authorize touching**." FR-016 (:1459-1461):
   "The existing write path's behavior under a project's configured comparison flags is unchanged by this
   feature." T044, in the same phase and the same file as T042, repeats it: "Do **not** change the live
   `sync` write path's warn-and-continue … that is existing behavior on an existing path this brief does
   not authorize touching." T053 goes further and *asserts* the live path unchanged as SC-016 evidence.
   One task in the phase changes the live path's data semantics while its neighbour tests that the live
   path is untouched.
3. **It is exactly the class round 1 raised and AD063 retired.** F10 objected to T060 changing a
   pre-existing refusal path's recorded run state; AD063 dropped it, and the collation packet recorded
   that "it retires the one piece of unauthorized scope fidelity flagged". Confirmed retired — see the
   F1/F2 section below. But AD054 re-introduces the pattern in the same round, with a far larger
   consequence: T060 would have written a different string into `run.json`; T042 removes relationship
   peers from a live destination.

I am not disputing the engineering. E1b is right that the existing ordering is a real defect, and T042's
argument that a shared helper cannot be a replace-set on one caller and additive on the other is sound.
What is missing is that a data-removing change to an existing command is a **decision for the brief's
owner**, not a derivation to be folded into a task whose stated subject is a refactor extraction — and
that no artifact discloses it. There is no requirement stating it, no success criterion measuring it, no
Edge Case, no Out-of-Scope amendment, and no entry in T069/T071's documentation sweep, which does disclose
the delete-recording change to operators. An operator upgrading gets a `sync` that starts pruning
relationships, with the only trace in a unit test's Done-when.

### Minimum fix — either of two, not both

- **Confine it.** `_replace_relationship_set` takes the new caller only; `update_node` keeps its present
  ordering, with a comment naming the defect and the outcome that should own the correction. The helper's
  "cannot behave two ways" objection is answerable by making the re-read a parameter with the new caller
  passing it, or by leaving the pre-existing read-order in `update_node` and calling the helper from the
  planned-write path alone. This is the smaller change and it is what AD023, AD048 and AD063 all do.
- **Or authorize it, on the record.** Promote it out of AD054 into its own decision put to the brief's
  owner as a user-visible, data-affecting change to the existing `sync` command; add it to the
  specification as an explicit approved change to an existing path (the shape the brief's own
  delete-recording constraint takes); give it a criterion; and add it to T069/T071's documentation sweep
  so an operator meets it in release notes rather than in production. Also then correct FR-014's and
  FR-016's "the existing write path is unchanged", which will no longer be true.

Either way, remove the contradiction between T042 and T044/T053.

---

## R2-F2 — Recommended — the authority AD055 claims is not the authority it has

**Classification**: defect in the generated artifacts, with a `brief-gap` dimension at repair level
**systemic**.

`spec.md`:782-786 states the authority: "**The derived side, because it is the derived side.** … DBR-016
and DBA-007 are both **derived**, and approved decision D020 ratified derived requirements as batch policy
on the proviso that each carries its basis — so they are **re-derived** here."

D020's text (brief :260) is: "Derived requirements and acceptance criteria are ratified as a batch-wide
policy, on the proviso that each carries its basis. Every DERIVED row in this brief satisfies that
proviso. *Rationale: seventeen of twenty cards state no acceptance criteria; per-card ratification would
reach the same answer seventeen times.*"

Read against its own rationale, D020 does one thing: it lets the **planner** derive requirements into a
brief without seeking per-card human ratification, provided each derivation shows its work. It says
nothing about whether a derived row, once inside an approved `READY` brief, may be re-derived downstream.
Taken as AD055 takes it, D020 would make **6 of 20 requirements** (DBR-013…DBR-018) and **10 of 13
criteria** (DBA-004…DBA-010, DBA-013 and the two others marked DERIVED) re-derivable by any implementation
that can articulate a preferred basis. That is more than half the brief becoming advisory, which is not
what a `READY` brief with `approved_by` set means.

The decision is nonetheless **legitimate**, and by a cleaner route: `tasks.md`:88 states it correctly —
"**AD055 is a human override at the decision gate**" — and the human at that gate is Blake Ellis, the same
person recorded as the brief's `approved_by` and as the approver of D020, D005, D025 and D026. An approver
amending their own approved artifact is scope authority acting, not scope drift. `spec.md` should say that,
and cite D020 only for the narrower thing it does supply: the proviso that the new basis be carried, which
the artifacts satisfy.

**Brief-gap statement.** Brief §Approved decisions, D020. It should have said whether a ratified derived
requirement is thereafter treated as brief content on the same footing as a quoted one, or whether an
implementation may re-derive it — and if it may, who ratifies the re-derivation and what it must carry.
Repair level **systemic**: D020 is stated as batch-wide policy and will be inherited by every brief in
batch-v3, so the same ambiguity is live in all of them, and the question will recur wherever a derived
criterion collides with the tree.

### Minimum fix in the artifacts

Restate AD055's authority as the brief approver's override at the decision gate, keeping the D020 citation
for the basis proviso only. Add the "unsupported operation" reading noted in the verdict above as a second
and independent ground, since it requires no override at all.

---

## R2-F3 — Recommended — brief v5 still asserts the superseded behavior in two places

**Classification**: `brief-gap` — repair level **instance**.

Neither passage is normative content the re-derivation contradicts — see the ruling in the verdict — but
both now read false, and this brief is read by other people: DB-012 consumes FR-020's identifiers, DB-014
inherits delete execution, and the brief is the artifact a reviewer diffs the delivery against.

**Brief-gap statement.** Two passages need revision in brief v6:

- **§Out of scope**, first bullet (:98-99): "Until DB-014 ships, a plan containing a delete behaves as
  DBR-016 and DBA-007 specify: the delete is reported and the run fails, never silently skipped." Should
  become: *"…the delete is reported, recorded, and not executed; the run completes, recording how many
  deletes it skipped and which. Never silently skipped."*
- **§User scenarios, Scenario 4** (:143-151): "completes in a **failed** state naming the unsupported
  operation". Should become the applied-state outcome with the recorded count and identifiers, keeping the
  "never silently skipped" rationale, which survives intact.

Repair level `instance`: both slots exist and simply state the pre-override reading. A second, smaller
repair belongs with them — the collation packet already routed AD055's originating gap (that recording
deletes by default plus failing on any recorded delete makes the qualified path's default posture a failed
apply), and brief v6 should close that too, or the same collision reappears at the next brief that touches
deletes.

### Minimum fix in the artifacts

One sentence in `spec.md`'s §"Derived brief items re-derived here" naming the two brief passages the
re-derivation supersedes, so the planner has the exact repair target and a later reader is not left to
discover the divergence by diffing.

---

## R2-F4 — Recommended — "Carried verbatim from the brief" is no longer true

**Classification**: defect in the generated artifacts.

`spec.md`:1894 heads §Out of Scope with "Carried verbatim from the brief. None of the following is
delivered here." The first bullet (:1896-1902) then rewrites the brief's delete bullet to the AD055
behavior — "the run ends in the applied state … it is never reported as a run failure either". The
exclusion is carried faithfully; the behavioral clause is inverted. Anyone checking the spec's
out-of-scope list against the brief's is told the two agree.

### Minimum fix

Amend the header to "Carried from the brief; the delete bullet's behavioral clause is restated per AD055"
and leave the bullet as it is. The bullet's content is right — only the header's claim is wrong.

---

## R2-F5 — Recommended — the skipped-delete warning's level is never pinned

**Classification**: defect in the generated artifacts.

Under the re-derivation, the operator-facing signal that the applied set differs from the reviewed set is
one warning naming the count. FR-017 requires "an operator-visible warning naming that count";
`contracts/destination-write-surface.md`:59 says "on the run's log stream … not a debug line and not a
per-operation trace: one message an operator reading the run's output cannot miss"; T047 says
"operator-visible warning on the run's log stream"; T054 asserts "a captured warning at operator-visible
level". No artifact names a level.

That matters because the CLI's `--quiet` sets the package logger to `logging.WARNING`
(`infrahub_sync/cli.py:59-60`, `_setup_logging` at `:40-47`). A message emitted at `INFO` satisfies every
sentence above and is invisible under `--quiet` — which is the flag an automated pipeline uses. A skip
notice suppressible by a routine flag is close to the silent skip DBR-016 forbids, and it is the one
place where the re-derivation's whole argument is delivered by a single observable.

### Minimum fix

Pin the level: FR-017, the write-surface contract, T047 and T054 state `WARNING` — or, better phrased,
"at a level `--quiet` does not suppress" — and T054 asserts the level, not only the text.

---

## F1 and F2 — verdicts

### F1 (a missing delete class was invisible at review) — **CLOSED**

The round-1 minimum fix asked for three things and the artifacts deliver four, all as MUSTs rather than
illustrations:

- **FR-006** (:1212-1218) now carries it as a requirement: "**Both depths MUST also surface the
  delete-computation record FR-015 puts in the manifest**, stating plainly when delete operations were not
  computed", plus a second obligation round 1 did not ask for — a **non-zero** delete count MUST be
  annotated inline at both depths saying no delete will be executed by this release.
- **SC-009** (:1817-1821) moves the pass condition: both depths must state the record and annotate the
  count, and **two of the four cases must run against a plan whose destination side was loaded
  incrementally**, so the not-computed wording is asserted reachable rather than assumed. That closes the
  hole exactly where round 1 found it — SC-009's pass condition was previously the counts alone.
- **`contracts/plan-reader-api.md`** puts both fields on `PlanSummary` as **required**
  (`delete_operations_computed: bool`, `deletes_not_executed: int`, :46-47) with the obligation table
  entries at :56-57, so the flag is no longer reachable only through `SavedPlan.manifest`.
- **`contracts/cli-review-mode.md`** :103-113 makes it "mandatory output", with a three-row table fixing
  the rendering for each of the three states and worked samples for all three.
- **Tasks**: T009 makes both fields required on the model; T061 asserts the disclosure per case with two
  of four cases on an incremental plan; T027 and T087 carry the in-process and disclosure halves; T037
  asserts both review depths of an incremental plan state that deletes were not computed; T069 and T071
  put it in the operator documentation.

FR-015 (:1440-1442) also now says the quiet part out loud: "'Reviewable' MUST be delivered rather than
asserted". AD024's justification is finally carried by an obligation. Closed.

### F2 (DBA-006 reported as plainly carried when it is conditional) — **CLOSED**

Both carriers now state the condition:

- **Traceability** (:2121): "DBA-006 | SC-006; User Story 2 scenario 5 — **carried conditionally**: it
  holds only when both plan runs extracted the same way on each side, because the manifest's
  delete-computation record is inside the checksum and outside the brief's two-field mask, so two runs at
  different extraction modes are expected to differ."
- **User Story 2 scenario 5** (:889-895) no longer restates the brief's unconditional wording: "**with
  both runs having extracted the same way on each side**", with the reason stated inline so the condition
  is not mistaken for test hygiene.

SC-006 keeps the engineering rescue that round 1 endorsed and did not want changed. Closed. The underlying
brief-gap (r1 F2) remains open with the planner and is carried forward below.

### AD063 and round-1 F10 — **retired, as claimed**

T060 is struck through with an explicit "**DROPPED [AD063]**" (`tasks.md`:359), its T065 test case is
dropped with it (:374), FR-009 states the decline in the requirement text — "It is deliberately **not**
extended to the pre-existing schema-subhash abort … Making that check live is unrelated scope and is not
done here" (:1293-1297) — and the traceability and coverage tables both annotate "T060 dropped — AD063"
(:459, :514, :570, :616). The pre-existing bug is left to its own change. F10 is retired, not left. That
makes R2-F1 the more uncomfortable finding: the same round retired one unauthorized existing-path change
and introduced a larger one.

---

## The other ten decisions — unauthorized scope check

| Decision | Scope verdict |
|---|---|
| **AD054** | **Two of three clauses clear; the third does not.** Rebuilding the conformance harness against a committed schema fixture and re-reading the destination peer set inside the *new* write path are both inside FR-013's mandate. Correcting `update_node`'s ordering on the **live** path is not — R2-F1 |
| **AD056** | Clear. Review-output content under DBR-002, and it is what makes DBR-009's disclosure real. Adds no capability, no input, no command |
| **AD057** | Clear, and explicitly authorized: brief §Constraints makes "their exact flag spelling … an implementation choice" within the no-new-group bar, and FR-008/SC-012 still assert the bar. It removes an error case rather than adding one |
| **AD058** | Clear. Three contracts corrected to their own declared signatures; the empty-versus-raise split moves a presentation rule out of the data interface FR-029 already required to be consumable without parsing output |
| **AD059** | Clear. Scoped to "every failure **this feature introduces**", which is message content on new paths. Does not reach the live path's existing errors — FR-014 and T044 keep the live warn-and-continue explicitly |
| **AD060** | Clear, and strengthens evidence: SC-012's baseline becomes a committed fixture, closing a comparison that passed against itself |
| **AD061** | Clear. Help text and the documentation obligation were already FR-008's; AD061 only moves the decision earlier than generation |
| **AD062** | Clear, and deliberately minimal: the record goes in the free-form `summary` inside the existing closed key set, so no persisted schema is extended and the brief's "durable run/artifact storage is elsewhere" boundary holds |
| **AD063** | Clear, and scope-reducing — see above |
| **AD064** | Clear. Reporting only; no requirement moves |

All nine of the brief's out-of-scope boundaries still hold, including the two I re-checked most closely:
no delete reaches a destination (structural via AD004, FR-016), and no new command group or command
(FR-008, SC-012's committed baseline, T064).

---

## Evidence deferral — still disclosed correctly, and it has not grown

**Verdict: unchanged in scope, unchanged in honesty.** Still exactly **five** brief criteria — DBA-001,
DBA-002, DBA-003 and DBA-008 in full, plus the live half of DBA-007 — plus SC-016's live half, which the
artifacts continue to exclude from the brief's tally because this specification derived it. I checked the
count is identical at every disclosure site: `spec.md`:527 and :2018-2027, `plan.md`:159 and :678,
`tasks.md`:22-38, `quickstart.md`:10-13 and :154-157. Each still says in substance that the brief's
completion condition is not met at merge.

Two things changed and both tighten it:

- The compensating harness is now honest about its own earlier weakness. `tasks.md`:33-36 and
  `plan.md`:159, :723 both state that the harness "narrows nothing in its earlier, mocked form" and must
  not be counted unless built in the AD054 shape. `tasks.md`:38's "Do not record Phase H as satisfied on
  the strength of either" survives.
- AD055 does not move DBA-007's live half onto the local side. SC-007's local half (T054) and live half
  (T078) are still split, and T078 still carries the `integration` marker. The brief's evidence column for
  DBA-007 asks for destination object counts before and after, which still needs a destination.

No finding. The underlying brief-gap (r1 F9 — §Assumptions never records that a live Infrahub is required)
remains open with the planner.

---

## DBR / DBA walk

Every brief item, its carrier, and a verdict. "Faithful" means the spec obligation is at least as strong
as the brief item and no weaker.

### Requirements

| Brief item | Spec carrier | Verdict |
|---|---|---|
| DBR-001 — plan contains every proposed create/update/delete/relationship before writing | FR-001; FR-015 for the delete class; User Story 1 | **conditional, now fully disclosed** — AD024's incremental-path omission stands, but FR-006/SC-009/`PlanSummary`/both contracts now make the omission mandatory output at both review depths (F1 closed) |
| DBR-002 — summary and per-object views | FR-006, FR-029; SC-009 | faithful, **strengthened** by the AD056 disclosure obligations |
| DBR-003 — validate a saved plan remains safe | FR-009; SC-004 | faithful, **exceeded** — five mandatory checks against the brief's three; the labelling objection (r1 F4) is unrepaired and non-blocking |
| DBR-004 — apply saved operations without recomputing | FR-012; SC-001 | faithful; evidence deferred, disclosed |
| DBR-005 — stable identifier linking review/application/audit/recovery | FR-003, FR-006, FR-020, FR-021; SC-005 | faithful, **strengthened** — FR-020 now has one named home (AD062), so the contract DB-012 consumes is explicit |
| DBR-006 — safe when checksum, config version, snapshot binding match | FR-004, FR-009, FR-027 | faithful, **exceeded** — safety is five conditions (r1 F4) |
| DBR-007 — resolve peers at apply time with no comparison store | FR-014; SC-008, SC-016 | faithful |
| DBR-008 — define the format, per-operation identifiers and checksums | FR-002, FR-004, FR-027, FR-028 | faithful on identifiers; **plural "checksums" still resolved to one plan-level checksum with the reading recorded nowhere** (r1 F7, unrepaired) |
| DBR-009 — record deletes, changing today's suppression default | FR-015; SC-017; User Story 4 | **conditional, now fully disclosed** — same AD024 condition; SC-017 additionally asserts the incremental plan's apply records a zero skipped count, so no phantom delete inflates what an operator is shown |
| DBR-010 — do not apply deletes | FR-016 | faithful, and structural — deletes never enter the comparison result the write path consumes. AD055 changes the framing, not the behavior |
| DBR-011 — format with identifiers and full payloads, write surface, peer resolution | FR-002, FR-013, FR-014, FR-028 | faithful |
| DBR-012 — readable from the stored artifact at any time | FR-007, FR-029; SC-009 | faithful |
| DBR-013 — Infrahub adapter executes a planned create or update convergently | FR-013; SC-002, SC-008 | faithful; the AD054 re-read strengthens the replace-set clause, but its **live-path spillover is R2-F1** |
| DBR-014 — deterministic serialization, stable checksum | FR-005, FR-028; SC-006 | faithful as a requirement; its criterion is the conditional one, now reported as such |
| DBR-015 — bind plan and source snapshot so the pair cannot tear | FR-004, FR-010; SC-004 | faithful |
| DBR-016 — unsupported operation reported, fails the run, never silently skipped | FR-017; FR-016, FR-020; User Story 4 scenarios 1–3 | **re-derived** (AD055, human override). Not weakened on the property it protects: the applied ∪ skipped closure is asserted at SC-007, T054 and T078, and `failed` is made a *failing* state so the reading cannot silently revert. Basis carried at four places. Authority mis-attributed (R2-F2); the brief's own restatements now stale (R2-F3) |
| DBR-017 — no secret in the artifact or any review output | FR-018; SC-010 | **narrowed (disclosed)** — "secret" still scoped to `settings` credentials by AD018; unchanged from round 1, brief-gap r1 F3 open |
| DBR-018 — configuration-version field, opaque, compared for equality | FR-011; SC-013 | faithful; the AD041 credential-rotation trade is unchanged and still weighed on one ground only (r1 F5, unrepaired) |
| DBR-019 — v1 detected and rejected, not migrated, no second apply path | FR-019; SC-011 | faithful |
| DBR-020 — review by extending existing commands + in-process API, no new group | FR-008, FR-029; SC-012 | faithful, **exceeded benignly** — no new command either, and AD057's spelling sits inside the brief's own stated latitude |

### Acceptance criteria

| Brief item | Spec carrier | Verdict |
|---|---|---|
| DBA-001 — applied without re-extraction, no fork-wide comparison rewrite | SC-001 (T075, `integration`) | faithful; evidence deferred, disclosed |
| DBA-002 — re-apply converges, no duplicate | SC-002 (T076, `integration`) | faithful; evidence deferred, disclosed |
| DBA-003 — create/update/relationship classes at clean-single-run counts, both crash windows | SC-003 (T077, `integration`) | faithful to the brief's own narrowing; evidence deferred |
| DBA-004 — refusal before any write, five negative cases | SC-004 (T025, T065) | faithful, **exceeded benignly** — six cases, all six now asserted individually on the CLI apply path |
| DBA-005 — review identifiers are the apply-result identifiers | SC-005 (T056) | faithful; apply-side set now read from one named home |
| DBA-006 — re-plan byte-identical, mask exactly two fields | SC-006 (T041) | **carried conditionally, and now reported as such at both carriers** (F2 closed) |
| DBA-007 — delete recorded, non-deletes applied, run fails naming it | SC-007 (T054 local, T065 CLI, T078 live) | **re-derived** (AD055): run state `applied`, non-zero recorded skipped count with identifiers, operator-visible warning, and the applied ∪ skipped closure asserted. Live half still deferred. Basis carried; brief restatements stale (R2-F3); warning level unpinned (R2-F5) |
| DBA-008 — relationship kind applies with no comparison store, peers match | SC-008 (T079, `integration`) | faithful, **exceeded benignly** — pre-existing-peer requirement retained; evidence deferred |
| DBA-009 — summary and detail after process exit, in-process and CLI | SC-009 (T027, T061, T087) | faithful, **strengthened** — the pass condition now includes the delete-computation record and two of four cases run on an incremental plan |
| DBA-010 — canary scan over artifact and both review outputs | SC-010 (T072) | faithful to the criterion as written; narrowed by DBR-017's reading (r1 F3) |
| DBA-011 — v1 plan rejected with a re-plan message, no write | SC-011 (T024, T065) | faithful |
| DBA-012 — no new command group, review through existing commands | SC-012 (T064, T002 baseline) | faithful, **strengthened** — the baseline is now a committed fixture, closing a comparison that passed against itself (AD060) |
| DBA-013 — config-version mismatch refused without parsing; opaque round-trip | SC-013 (T014, T057) | faithful |

**Summary of the walk.** Nothing **unowned**. Nothing **weakened** in the sense of a brief obligation
delivered less strongly than stated: the two items that moved (DBR-016, DBA-007) moved by human override
of derived items, with the protected property preserved and measured, and the two that remain conditional
(DBR-001, DBR-009) now carry the disclosure that was the whole justification for the condition. One item
stays **narrowed** and disclosed (DBR-017). Six **exceeded**, all toward a stronger bar: DBR-003/DBR-006's
five checks, DBA-004's sixth case, DBA-008's pre-existing peer, DBR-020's no-new-command, DBA-009's
disclosure and incremental cases, DBA-012's committed baseline. The only **exceeded-without-authorization**
item is not in these tables at all, because it is not a brief item: the live `sync` relationship semantics
change (R2-F1).

---

## Planner feedback — `brief-gap`s

New this round:

| ID | Brief section | What it should have said | Repair level |
|---|---|---|---|
| R2-F2 | §Approved decisions, D020 | Whether a ratified DERIVED requirement is thereafter brief content on the same footing as a QUOTED one, or whether an implementation may re-derive it — and if it may, who ratifies the re-derivation and what it must carry beyond a basis. As written, D020 reads as licensing downstream re-derivation of 6 of 20 requirements and 10 of 13 criteria | **systemic** — D020 is batch-wide and inherited by every brief in batch-v3 |
| R2-F3 | §Out of scope (delete bullet, :98-99); §User scenarios, Scenario 4 (:143-151) | Both restate the pre-override behavior ("the run fails"). Brief v6 should restate them to the ratified outcome: the delete is reported, recorded and not executed; the run completes recording how many deletes were skipped and which; never silently skipped. Fold in AD055's originating gap too — that recording deletes by default plus failing on any recorded delete makes the qualified path's default posture a failed apply | **instance** |

Carried forward from round 1, unrepaired and still open with the planner:

| ID | Brief section | Missing content | Repair level |
|---|---|---|---|
| r1 F2 | §Edge cases; DBA-006 | Whether deletes are omitted on an incremental destination load and how the omission is disclosed; whether byte-determinism holds across extraction modes | `instance` |
| r1 F3 | DBR-017; DBA-010 | A definition of "secret", imported from another card without one, and the canary's injection point | `instance` |
| r1 F6 | §Edge cases | A reviewed `update` whose target vanished: the mandated convergent write creates it, colliding with the Outcome's "applied equals reviewed" and the freshness/conflict exclusion | `instance` |
| r1 F9 | §Assumptions | "A live Infrahub is reachable in the implementation environment", with impact if wrong. Systemically, the completion-condition template admits no notion of environment-gated evidence | `instance` |

---

## Report metadata

Path: `dev/specs/001-plan-artifact-saved-apply/critiques/fidelity-r2.md`
Round 2 of a maximum 3.
