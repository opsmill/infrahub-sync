# Fidelity critique — round 1

**Lens**: fidelity (scope authority + brief conformance + planner feedback)
**Brief**: DB-001 `db-001-plan-artifact-saved-apply.md`, brief_version 5, batch-v3, primary card INFP-653
**Under review**: `spec.md` (1834 lines, FR-001…FR-030, SC-001…SC-018, AD001–AD053), `plan.md` (648),
`tasks.md` (523, 85 tasks), `research.md` (469, PD-001…PD-010), `data-model.md` (261),
`quickstart.md` (209), `contracts/` ×4. No code written.
**Branch/head**: `001-plan-artifact-saved-apply-infp-653` @ `016802e`

**Verdict**: the brief remains one independently testable spec — no `NEEDS_INTAKE_REVISION`. No
unauthorized scope belonging to a dependency outcome. Two Must-Address findings, both rooted in one
place: the interaction between the newly mandated delete recording (DBR-009) and the engine's
pre-existing incremental destination-load path, which neither the brief nor the specification
anticipated and which the specification resolves by narrowing two brief items without labelling the
narrowing as such in its traceability.

---

## Findings

| ID | Severity | Classification | Summary | Anchor |
|---|---|---|---|---|
| F1 | **Must-Address** | defect in the generated artifacts | The disclosure that justifies suppressing delete derivation on the incremental path is required in the manifest but at no review surface — the mitigation for a DBR-001/DBR-009 narrowing is non-normative | `spec.md` FR-015 :1152-1160, FR-006 :966-973, SC-009 :1467-1475; `contracts/cli-review-mode.md`:52 |
| F2 | **Must-Address** | `brief-gap` (instance) | DBA-006 is not achievable as the brief states it once deletes are recorded; SC-006 rescues it with a pinned-extraction-mode evidence precondition, and the traceability table still reports DBA-006 as plainly carried | `spec.md` SC-006 :1440-1449, FR-015 :1157-1159, traceability :1748; `tasks.md` trap 1 :87-90 |
| F3 | Recommended | `brief-gap` (instance) | DBR-017 never defines "secret"; AD018 narrows it to `settings` credentials, which makes DBA-010's canary structurally unable to appear in a payload | `spec.md` FR-018 :1176-1180, SC-010 :1476-1481, Out of Scope :1564-1568 |
| F4 | Recommended | defect in the generated artifacts | FR-009 makes five pre-apply checks mandatory where DBR-006 defines safety by three; the two added ones are traced as coverage rather than as an expansion | `spec.md` FR-009 :1000-1022, SC-015 :1506, SC-018 :1526, traceability :1775-1776 |
| F5 | Recommended | defect in the generated artifacts | AD041 puts `settings` credentials inside the configuration-version digest, so a credential rotation invalidates every saved plan; the middle option was never weighed | `spec.md` FR-011 :1048-1057; `research.md` PD-003 :81-121 |
| F6 | Recommended | `brief-gap` (instance) | A reviewed `update` whose target vanished is applied as a create and reported as an `update` — the Outcome's "applied equals reviewed" and the freshness/conflict exclusion collide and the brief never says which wins | `spec.md` Edge Cases :842-848, FR-013 :1091-1095 (AD025) |
| F7 | Recommended | defect in the generated artifacts | DBR-008's "per-operation identifiers **and checksums**" is silently resolved to one plan-level checksum; no decision records the reading | `spec.md` FR-002/FR-004/FR-027; no AD, no PD, no grep hit anywhere in the artifact set |
| F8 | Recommended | defect in the generated artifacts | FR-030 puts new hard failures on the existing non-mutating command — a user-visible behavior change on an existing surface, derived rather than approved | `spec.md` FR-030 :1338-1353; `plan.md` Constitution Check I :137; `contracts/cli-review-mode.md` §"`diff`'s live path also gains hard failures" |
| F9 | Recommended | `brief-gap` (instance) | The brief's completion condition demands passing evidence for every criterion while five of its own need a live Infrahub; its Assumptions table records no assumption that one is reachable and no fallback | Brief §Completion conditions, §Assumptions; `spec.md` :1657-1663 (AD045) |
| F10 | Recommended | defect in the generated artifacts | T060/AD010 changes an existing, unrelated refusal path's persisted run state, while AD023 and AD048 decline to touch existing paths on the grounds the brief does not authorize it | `tasks.md` T060 :289-291; `spec.md` FR-009 :1031-1033 vs AD023 :256-260, AD048 :538-545 |
| F11 | Nit | defect in the generated artifacts | The brief constraint "the shared execution core is not a prerequisite" has no row in the second traceability table, though it is honored elsewhere | `spec.md` :1760-1777 |
| F12 | Nit | defect in the generated artifacts | FR-024's warning names "the affected kind and what is missing"; the brief asks for "the affected kind and identifier". AD052's skip on schema-less destinations is silent | `spec.md` FR-024 :1217-1242; brief §Edge cases |
| F13 | Nit | defect in the generated artifacts | All 53 decisions still carry `[PROVISIONAL ADnnn]`; by the spec's own text several requirements reopen if specific ones are not ratified, so nothing is currently settled | `spec.md` :31-39, :1799-1822 |

Severity counts: **Must-Address 2**, **RETHINK 0**, Recommended 8, Nit 3.
`brief-gap` findings: **4**, all repair level `instance` (F2, F3, F6, F9).

---

## F1 — Must-Address — the incremental-path delete suppression is disclosed only in the manifest

**Classification**: defect in the generated artifacts.

DBR-001 (QUOTED) requires a plan "containing **every** proposed create, update, delete, and
relationship change". DBR-009 (QUOTED) requires recording deletes, "changing today's default of
suppressing them".

AD024 (`spec.md`:266-288) establishes that deletes are derived **only** when the destination side ran
a full extract. When the destination side was loaded incrementally, **no delete operation is derived
at all**, and FR-015 (:1152-1160) makes that a MUST. The reasoning is sound and I do not dispute it:
`should_use_incremental` replays the prior snapshot plus changed-since rows, so a destination-minus-source
difference is over-inclusive and would manufacture phantom deletes that FR-017/SC-007 then turn into
spurious failed applies.

The problem is what carries the narrowing. AD024's own justification is that "the omission is explicit
and reviewable rather than silent", and FR-015 says the manifest "MUST record that deletes were not
computed for this plan ... which is what keeps FR-017's 'never silently skipped' contract true". That
justification is load-bearing — it is the whole reason the departure from DBR-001 is acceptable — and
it is not required at any review surface:

- **FR-006** (:966-973) defines the summary as "a count per action and a count per kind" and per-object
  detail as identifier / action / kind / identity. It says nothing about the delete-computation record.
- **SC-009** (:1467-1475) fixes the pass condition as "the summary presents a count per action and a
  count per kind, and the detail presents one record per operation carrying at least its operation
  identifier, action, destination kind, and destination identity". Again nothing.
- **`contracts/plan-reader-api.md`** `PlanSummary` carries `by_action`, `by_kind`, `total` — and not the
  flag. It is reachable only via `SavedPlan.manifest`, which the reader contract does not oblige a
  renderer to consult.
- The flag appears at a review surface in exactly one place in the whole artifact set: the illustrative
  sample output in `contracts/cli-review-mode.md`:52 (`deletes computed: yes`). An illustration, not an
  obligation. `tasks.md` T061's Done-when repeats SC-009's field set and does not assert it.

So an implementer who satisfies every MUST and every criterion ships a review surface on which a plan
missing its entire delete class is indistinguishable from a plan that genuinely has no deletes. This
matters more than it would elsewhere because the incremental path is the *warm* path — the common
repeat-run case — so the headline user-visible change DBR-009 buys may simply not appear on most real
runs, and the operator has no signal.

**Minimum fix**

1. Add to FR-006 a clause: both review depths MUST surface the manifest's delete-computation record,
   and where deletes were not computed the summary MUST say so in terms an operator can act on.
2. Add the same to `PlanSummary` in `contracts/plan-reader-api.md` and to the SC-009 pass condition and
   T061's Done-when.
3. Recommended alongside (not minimum): emit a plan-time warning on the log stream when the destination
   side was loaded incrementally and deletes were therefore not computed, on the same footing as
   FR-024's warning. FR-015 currently states "No input is added for requesting that deletes be computed,
   and extraction behavior is unchanged" — that is the right restraint, but silence at plan time is not
   required by it.

---

## F2 — Must-Address — DBA-006 is met conditionally, and the traceability does not say so

**Classification**: `brief-gap` — repair level **instance**.

DBA-006 (brief, DERIVED, per D020): "Re-planning an unchanged source and destination produces a
byte-identical operations section and a byte-identical manifest, excluding the fields that necessarily
vary per run (the run identifier and the creation timestamp)."

The brief fixes the mask at exactly two fields. FR-015 (:1157-1159) then puts
`delete_operations_computed` inside the checksummed manifest and explicitly not in the mask, and — more
than that — the *operations section itself* differs between extraction modes, because a full-extract run
carries delete operations and an incremental run carries none. So re-planning an unchanged source and
destination through the engine's normal warm path produces a manifest **and** an operations section that
are not byte-identical, and DBA-006 as the brief states it is false.

SC-006 (:1440-1449) resolves this by adding an evidence precondition — "two consecutive plan runs **that
both used the same extraction mode on each side**" — and `tasks.md` trap 1 (:87-90) makes T041 pin the
mode and assert the pinning held, with a negative control. That is honest engineering and I would not
change the test.

What I object to is the bookkeeping. The traceability table (:1748) reports `DBA-006 | SC-006; User
Story 2 scenario 5` with no marker, and User Story 2 scenario 5 (:699-701) restates the brief's
unconditional wording. Nothing in the spec says "DBA-006 is delivered conditionally". A reader walking
the brief's criteria against the traceability table concludes DBA-006 ships as written. It does not.

**Brief-gap statement.** The brief's §"Edge cases and failure behavior" and §Constraints never mention
the engine's incremental destination-load path, even though it is pre-existing behavior in the target
repository and it collides with both DBR-009 and DBA-006. The brief should have said, in Edge cases:
*"Deletes are derived by set difference against the loaded destination state. When the destination side
is loaded incrementally the enumeration is incomplete; state whether deletes are then omitted (and how
that is disclosed) or whether a full destination extract is required for a plan to be complete."* And
DBA-006 should have said whether determinism is required across extraction modes or only within one.
Repair level `instance`: the Edge-cases and Constraints slots exist in the template and were left empty
on this point.

**Minimum fix in the artifacts**

- Mark DBA-006 in the traceability table as *conditionally carried*, with the condition named, rather
  than as plainly carried.
- Make the same statement in User Story 2 scenario 5 so the acceptance scenario is not stronger than
  what ships.
- Route the brief-gap to the planner (this is that route).

---

## F3 — Recommended — "secret" is undefined in the brief and narrowed in the spec

**Classification**: `brief-gap` — repair level **instance**.

DBR-017 (DERIVED, basis "INFP-651 SRC-651-04 ... secrets never live in run artifacts"): "No secret
value appears in the plan artifact or in any review output." DBA-010: "A canary-credential scan over
the artifact and both review outputs."

AD018 (:217-222) takes the narrow reading deliberately: secrets are the credentials in the
configuration's `settings`; mapped source data values are not classified; no omit/mask/refuse model is
built. FR-018 encodes it and the Out-of-scope section (:1564-1568) records the exclusion. The reading is
defensible — the brief's In-scope mandates "the required source values as a full payload", the source
snapshots already persist the same values today, and a field-level classification model would be a new
configuration surface the In-scope list does not carry.

The consequence worth naming is that the resulting criterion cannot fail by construction. `settings`
values are never read into a plan record on any code path in the design, so injecting the canary there
tests a property that holds trivially. The artifacts partly saw this: T072's Done-when requires "the
test fails if the canary is planted into a payload, proving the scan has teeth" — a mutation check,
which is the right instinct. It still leaves DBA-010 evidencing a narrower claim than DBR-017 states.

**Brief-gap statement.** DBR-017 imports a rule from another card without defining its term, and
DBA-010 names a canary without naming an injection point. The brief should have said, at DBR-017:
*"'Secret' means the credential values in the configuration's `settings`; mapped source data values are
out of scope for classification"* — or, if the wider reading was intended, said so and accepted the
classification model as scope. Repair level `instance`: the Requirements table's Requirement column and
the Acceptance criteria table's "Verification evidence expected" column are both slots that could have
carried this.

**Minimum fix in the artifacts**: none required beyond what is there; keep T072's mutation check as a
hard Done-when, and surface the narrowing at the checkpoint so the planner can confirm the reading.

---

## F4 — Recommended — five mandatory pre-apply checks against the brief's three

**Classification**: defect in the generated artifacts.

DBR-006 (QUOTED): "A plan is safe to apply when its checksum, configuration version, and
source-snapshot binding still match." DBA-004 names the same three plus the torn cases.

FR-009 (:1000-1022) makes **five** checks mandatory and refuses on any of them, adding (a) the
manifest's recorded run identifier must equal the run being applied (AD012) and (b) the declared format
version must be recognized (AD028, gated per AD053). SC-015 and SC-018 are the criteria for the two
additions.

Both additions are well argued and I would keep them. AD012 closes a real hole that AD001's own
checksum exclusions open — a `plan/` directory copied into another run verifies clean otherwise — and
AD028's version check is inside "define the plan artifact's format" (DBR-008). The spec's justification
("DBA-004 names three checks but does not forbid additional ones") is the right line.

The defect is in how it is recorded. DBR-006 reads as a sufficiency statement, and the spec turns it
into a necessary-but-not-sufficient one, so a plan the brief calls safe can now be refused. The
traceability rows (:1775-1776) file both under "Derived from DBR-003/DBR-006", which reads as coverage
of an existing brief item rather than as an expansion of one. Two new refusal conditions on the apply
path are exactly the kind of thing a checkpoint should ratify explicitly.

**Minimum fix**: relabel those two traceability rows as expansions of DBR-006's safety definition,
state at FR-009 that DBR-006's three-check list is being treated as non-exhaustive, and put both
additions in the checkpoint packet as decisions rather than derivations.

---

## F5 — Recommended — credential rotation invalidates every saved plan

**Classification**: defect in the generated artifacts.

DBR-018 requires the manifest's configuration-version field to hold "a deterministic content checksum
computed over the configuration the run used". AD041 / PD-003 close the field set as *everything the
parsed configuration declares except `directory`* — with connection `settings`, including credentials,
inside the hash input.

FR-011 (:1054-1057) and the Assumptions (:1652-1656) record the consequence honestly: **rotating a
credential invalidates every saved plan for that configuration**, refused at apply under FR-009,
requiring a re-plan. In the change-managed environments this outcome exists for, that means a routine
token rotation silently voids every approved-but-unapplied plan.

The reasoning offered is a two-option choice — include `settings` or exclude them entirely, where
excluding means "a changed destination address did not invalidate a plan, which is the worse failure".
PD-003's "Alternatives considered" list confirms only three options were weighed (file bytes,
`schema_mapping` only, include `directory`). The middle option was never on the table: cover the
`settings` keys that address the destination (host, address, branch, port) and exclude the keys whose
values are credentials. The spec has already accepted that the digest need not cover everything parsed
— it excludes `directory` for exactly this class of reason (machine-dependence making plans
non-portable) — so drawing a second such line is consistent, not a new principle. The one-way-digest
argument answers a disclosure objection, which nobody raised; it does not answer the invalidation one.

**Minimum fix**: weigh and record the middle option at PD-003 and AD041. If it is rejected, say why on
the invalidation ground rather than the disclosure ground. Either way, escalate the trade at the
checkpoint — it decides when a saved plan stops being applicable, which is a headline property of this
outcome.

---

## F6 — Recommended — a reviewed `update` can be applied as a create

**Classification**: `brief-gap` — repair level **instance**.

The brief's Outcome promises operations "provably the operations that were reviewed". Its Out-of-scope
list excludes destination freshness checks and conflict policies. Its In-scope mandates "a destination
write surface ... capable of executing a planned create or update **convergently**".

AD025 (:298-305) derives the collision: because FR-013 routes planned creates *and* updates through the
human-friendly-ID-keyed upsert, and the upsert creates when nothing matches the key, a reviewed
`update` whose destination object was deleted out-of-band between plan and apply **materializes as a
create**. The spec records it at FR-013 (:1091-1095) and in an Edge Case (:842-848), builds no conflict
detection, and reports the operation "under its original operation identifier and its original action".

I do not think the artifacts can do better within the brief: detecting the vanished target requires a
pre-write destination read, which is a freshness check. But two things are worth putting in front of a
human rather than resolving here.

First, the reported action. The apply result will say `update op_xyz applied` when a create happened.
That is defensible as "report what was reviewed", but for a feature whose product is an audit trail it
is the one place the record and reality diverge. Whether the upsert created or matched is *post-write*
information the SDK response may carry — surfacing it is not a freshness check and is not obviously out
of scope. Nothing in the artifacts weighs that.

Second, and more the planner's problem: the brief promises "applied equals reviewed" and then excludes
the only mechanism that could enforce it. That is not a contradiction that unmakes the brief — the
convergent-upsert mandate makes the create the correct behavior — but it is a promise the delivered
outcome cannot keep in full, and no one has said so on the record except AD025.

**Brief-gap statement.** The brief's §"Edge cases and failure behavior" should have named this case:
*"A reviewed update whose destination object was removed between plan and apply. The convergent write
creates it. State whether that is acceptable, and how the apply result reports it."* Repair level
`instance` — the Edge cases slot exists and covers five other cases of exactly this shape.

---

## F7 — Recommended — DBR-008's "checksums" resolved to one, recorded nowhere

**Classification**: defect in the generated artifacts.

DBR-008 (QUOTED): "Define the plan artifact's format, including per-operation identifiers **and
checksums**." The brief's Problem section reinforces the phrasing: "there are no operation identifiers
or checksums".

The artifacts deliver exactly one checksum — `plan_checksum`, over the canonical manifest concatenated
with the operations bytes. No per-operation checksum exists anywhere in `spec.md`, `plan.md`,
`data-model.md`, `research.md`, `tasks.md` or the four contracts; the word "checksums" plural does not
appear in the artifact set at all.

I believe the single-checksum reading is right. The brief's In-scope bullet 2 names "a deterministic
checksum over the manifest and the ordered operations" (singular), DBR-006 says "its checksum", DBR-014
says "the checksum", and DBA-006 says "the manifest". Four of five references are singular and one is
the ambiguous DBR-008. Per my remit I flag the tension rather than resolving it by the larger reading —
but the reading the artifacts took is the one the brief's own weight supports.

What is missing is the record. Fifty-three decisions were taken and this one was not, so a reader
walking DBR-008 against the spec finds identifiers covered and "checksums" apparently half-covered,
with nothing saying the plural was deliberate.

**Minimum fix**: record an AD stating that DBR-008's "checksums" is read as the single plan-level
checksum, citing In-scope bullet 2, DBR-006 and DBR-014, and note that per-operation checksums are not
delivered. One paragraph.

---

## F8 — Recommended — new hard failures on an existing non-mutating command, derived not approved

**Classification**: defect in the generated artifacts (with a brief-gap dimension noted below).

FR-030 (:1338-1353) makes a plan-derivation failure fail the command — on the **non-mutating** command
as well as the mutating one — with no error-tolerance option, deliberately. AD047 is the decision;
`plan.md` records it as a material risk (:645) and the Constitution Check restates the reading of "safe
to run at any time" that permits it; `contracts/cli-review-mode.md` has a dedicated section.

The disclosure is genuinely thorough, which is why this is Recommended and not Must-Address. Three
things still make it worth a human decision rather than a derivation:

1. It is a **user-visible behavior change on an existing command**. An operator's `diff` starts exiting
   non-zero on data that renders today — an unformable destination identity, a peer absent from the
   loaded source store, a duplicate identifier, and (per AD050) a peer-kind probe that hits zero or more
   than one candidate. The AD050 arms in particular are new failure modes on configurations that have
   never failed before.
2. The brief does not authorize it and does not exclude it. It never says which command derives the
   artifact, nor what a derivation failure does. The spec's authorization chain runs
   DBR-001 → FR-001 → derivation on the non-mutating path → AD047, and the tie to DBR-016 is by analogy
   ("a silently incomplete plan is the divergence FR-017 exists to prevent"), not by the brief's text.
3. The spec is inconsistent with itself about touching existing paths. AD023 declines to move the
   existing live `diff` output channel because "moving existing output is a user-visible change to an
   existing path that the brief does not authorize". AD048 declines to change the live write path's
   warn-and-continue for the same stated reason. AD047 makes an existing command start failing, on the
   opposite reasoning. Two of the three declines, one proceeds; the distinction is not articulated.

**Brief-gap dimension** (recorded here rather than as a separate finding): the brief's Outcome speaks of
"a sync run in `plan` mode" while the target repository has no `plan` command, and the brief's
dependency table dismisses the run-mode vocabulary as "naming only". It should have named the command
the artifact is produced on and stated whether a derivation failure may fail a non-mutating command.
Repair level would be `instance` — the Constraints slot already carries the sibling constraint about
which commands carry *review*.

**Minimum fix**: promote AD047 from a derived decision to a checkpoint decision, and state at AD047 why
the AD023/AD048 restraint does not apply here.

---

## F9 — Recommended — the evidence deferral is a brief-gap, not an artifact defect

**Classification**: `brief-gap` — repair level **instance**.

**On the artifacts' handling: adequate disclosure, correctly scoped, not a softened requirement.** I
checked this closely because it is the obvious candidate for quiet weakening, and it is not one.

- The scoping is right. Five brief criteria need a live destination — DBA-001, DBA-002, DBA-003 and
  DBA-008 in full, plus the live half of DBA-007 — and I independently walked the other eight (DBA-004,
  005, 006, 009, 010, 011, 012, 013) and agree each is producible locally against fixtures, a
  write-recording fake destination, or a subprocess read. The brief's own "Verification evidence
  expected" column demands a live destination for exactly the five named and for no others.
- The artifacts are careful to separate the brief's tally from their own: SC-016's live half is deferred
  too and is repeatedly excluded from the count "because this specification derived it rather than took
  it from the brief" (`spec.md`:1657-1663, `plan.md`:592-604, `tasks.md`:22-28). That is the correct
  discipline and it is applied consistently — six deferred criteria, five of them the brief's.
- The disclosure is stated, not implied, in six places: AD045, spec Assumptions, plan Constitution
  Check V, the plan's evidence map header, the plan's risk register, and a dedicated up-front section in
  `tasks.md` plus the Phase H header and the implementation strategy. Every one of them says the same
  sentence in substance: **the brief's completion condition is not met at merge time**. That is the
  opposite of softening.
- The compensations are correctly labelled as narrowing rather than closing. T081's mutation-payload
  conformance harness and T045's per-component HFID assertion (AD051) are both stated as catching the
  AD042 defect class offline without substituting for the live run, and `tasks.md`:37 says outright
  "Do not record Phase H as satisfied on the strength of either."

I found nothing to fix here. The finding is against the brief.

**Brief-gap statement.** The brief's §Completion conditions demands "inspectable passing evidence" for
every criterion, and its evidence column demands a live destination for five of them, but the brief
never records that a live Infrahub is required, never assumes one is reachable, and states no fallback.
The §Assumptions table — which does carry two other assumptions with "Impact if wrong" — is the empty
slot: it should have carried *"A live Infrahub is reachable in the implementation environment. Impact if
wrong: DBA-001, DBA-002, DBA-003, DBA-008 and the live half of DBA-007 cannot be evidenced at merge; the
completion condition is met only on a later live run."* Repair level `instance`.

There is a systemic dimension worth the planner's attention even though I am scoring this `instance`:
the completion-condition template admits no notion of environment-gated evidence, so any brief whose
criteria need infrastructure the implementation environment lacks will produce this same collision.

---

## F10 — Recommended — an existing unrelated refusal path is changed

**Classification**: defect in the generated artifacts.

T060 (`tasks.md`:289-291), under FR-009 and AD010, fixes the pre-existing schema-subhash refusal path in
`cli.py:336-340` to record `failed` where today it aborts leaving `status: running` on disk. It is a
real bug and the fix is one line of intent.

It is also a behavior change on a pre-existing path that has nothing to do with plan verification, and
the specification twice declines to make exactly that kind of change on the stated grounds that the
brief does not authorize it (AD023 for the live `diff` output channel, AD048 for the live write path's
peer warn-and-continue). The three cases are not distinguished anywhere.

The defensible reading is that the schema-subhash abort *is* a refusal of the `apply` command being
rewired, so FR-009's "a refused apply MUST record `failed`" covers it and it is inside the blast radius.
If that is the reasoning, say it.

**Minimum fix**: state at AD010 why this existing-path change is authorized where AD023's and AD048's
are not — or drop T060 and leave the pre-existing bug to its own change.

---

## F11 — Nit — one brief constraint unmapped in the second traceability table

The brief has six Constraints. Five have rows in the second traceability table (:1760-1777); "The card
states the shared execution core is not a prerequisite. This outcome must not require the core refactor
to land first" has none. It **is** honored — `plan.md` Technical Context Constraints names it, the
Dependencies section says "No in-batch dependencies", and nothing in the design touches the core — so
this is table completeness only. Add the row.

Otherwise the second table is **real, not decorative**. I checked all sixteen rows against their named
carriers: every brief edge case (6 of 6) and every brief constraint except the one above lands on a
requirement that actually states the obligation, and in ten of sixteen rows on a criterion as well. The
three "Derived from …" rows (extraction precondition, run binding, format-version check) are correctly
labelled as derivations rather than presented as brief items.

---

## F12 — Nit — two small fidelity slips on FR-024

The brief's edge case asks for a warning "naming the affected kind **and identifier**". FR-024 requires
"naming the affected kind and what is missing", and SC-014 asserts "the warning's content" without
fixing it. For the uniqueness-constraint arm in particular, naming the identity attributes is what makes
the warning actionable. Tighten FR-024's wording to the brief's.

Separately, AD052 scopes the whole warning to destinations that expose a schema, and where none is
exposed the check "MUST be skipped and skipping it MUST NOT be an error". The scoping is right — the
brief's write surface is Infrahub-only and FR-023 refuses an apply against any other adapter anyway —
but the skip is entirely silent. A one-line "convergence-key check not performed: destination exposes no
schema" on the log stream costs nothing and keeps the brief's "documenting it as a precondition is not
sufficient" spirit intact for the eight other adapters.

---

## F13 — Nit — every decision is still provisional

All 53 decisions carry `[PROVISIONAL ADnnn]`. The spec's own text (:31-39, :1799-1822) makes the marker
load-bearing: "If AD003 is not ratified, FR-002's reference shape and FR-014's resolution mechanism
reopen"; "If AD042 is not ratified ... SC-002 and SC-003 become unachievable". As the artifacts stand,
nothing in the specification is settled and the revisit set is the whole document.

This is process rather than substance and root's checkpoint packet is the right place to clear it — I
raise it only so the markers are actually removed on acceptance rather than shipped as permanent
furniture.

---

## DBR / DBA walk

Every brief item, its owning spec carrier, and a verdict. "Faithful" means the spec obligation is at
least as strong as the brief item and no weaker.

### Requirements

| Brief item | Spec carrier | Verdict |
|---|---|---|
| DBR-001 — plan contains every proposed create/update/delete/relationship before writing | FR-001; FR-015 for the delete class; User Story 1 | **weakened (conditional, disclosed)** — AD024 omits the whole delete class when the destination side loaded incrementally; the manifest discloses it but no review surface must (F1) |
| DBR-002 — summary and per-object views | FR-006, FR-029; SC-009 | faithful |
| DBR-003 — validate a saved plan remains safe | FR-009; SC-004 | faithful, **exceeded** (two extra mandatory refusal conditions — F4) |
| DBR-004 — apply saved operations without recomputing | FR-012; SC-001 | faithful (evidence deferred, F9) |
| DBR-005 — stable identifier linking review/application/audit/recovery | FR-003, FR-006, FR-020, FR-021; SC-005 | faithful |
| DBR-006 — safe when checksum, config version, snapshot binding match | FR-004, FR-009, FR-027 | faithful, **exceeded** — safety redefined as five conditions (F4) |
| DBR-007 — resolve peers at apply time with no comparison store | FR-014; SC-008, SC-016 | faithful |
| DBR-008 — define the format, per-operation identifiers and checksums | FR-002, FR-004, FR-027, FR-028 | faithful on identifiers; **"checksums" read as one plan-level checksum with the reading recorded nowhere** (F7) |
| DBR-009 — record deletes, changing today's suppression default | FR-015; SC-017; User Story 4 | **weakened (conditional, disclosed)** — same AD024 condition as DBR-001 (F1) |
| DBR-010 — do not apply deletes | FR-016 | faithful — structurally, deletes never enter the comparison result the write path consumes |
| DBR-011 — format with identifiers and full payloads, write surface, peer resolution | FR-002, FR-013, FR-014, FR-028 | faithful |
| DBR-012 — readable from the stored artifact at any time | FR-007, FR-029; SC-009 | faithful |
| DBR-013 — Infrahub adapter executes a planned create or update convergently | FR-013; SC-002, SC-008 | faithful; note the AD025 create-on-vanished-update consequence (F6) |
| DBR-014 — deterministic serialization, stable checksum | FR-005, FR-028; SC-006 | faithful as a requirement; its criterion is the conditional one (F2) |
| DBR-015 — bind plan and source snapshot so the pair cannot tear | FR-004, FR-010; SC-004 | faithful |
| DBR-016 — unsupported operation reported, fails the run, never silently skipped | FR-017; SC-007 | faithful |
| DBR-017 — no secret in the artifact or any review output | FR-018; SC-010 | **narrowed (disclosed)** — "secret" scoped to `settings` credentials by AD018 (F3) |
| DBR-018 — configuration-version field, opaque, compared for equality | FR-011; SC-013 | faithful; the AD041 field-set choice is the concern (F5) |
| DBR-019 — v1 detected and rejected, not migrated, no second apply path | FR-019; SC-011; AD040 removes the v1 dispatch | faithful |
| DBR-020 — review by extending existing commands + in-process API, no new group | FR-008, FR-029; SC-012 | faithful, **exceeded benignly** — AD005 adds no new command either, not just no group |

### Acceptance criteria

| Brief item | Spec carrier | Verdict |
|---|---|---|
| DBA-001 — applied without re-extraction, no fork-wide comparison rewrite | SC-001 (T075, `integration`) | faithful; **evidence deferred, disclosed** |
| DBA-002 — re-apply converges, no duplicate | SC-002 (T076, `integration`) | faithful; **evidence deferred, disclosed** |
| DBA-003 — create/update/relationship classes at clean-single-run counts, both crash windows | SC-003 (T077, `integration`) | faithful to the brief's own narrowing (delete excluded per D005); third class named per AD009; **evidence deferred** |
| DBA-004 — refusal before any write, five negative cases | SC-004 (T025, T065) | faithful, **exceeded benignly** — six cases, adding the absent-snapshot case User Story 2 names |
| DBA-005 — review identifiers are the apply-result identifiers | SC-005 (T056) | faithful |
| DBA-006 — re-plan byte-identical, mask exactly two fields | SC-006 (T041) | **weakened (conditional, disclosed in SC-006, not in traceability)** (F2) |
| DBA-007 — delete recorded, non-deletes applied, run fails naming it | SC-007 (T054 local, T078 live) | faithful; **live half deferred** |
| DBA-008 — relationship kind applies with no comparison store, peers match | SC-008 (T079, `integration`) | faithful, **exceeded benignly** — adds the pre-existing-peer requirement so the destination-query path is actually exercised; **evidence deferred** |
| DBA-009 — summary and detail after process exit, in-process and CLI | SC-009 (T027, T061) | faithful; see F1 on what the summary must show |
| DBA-010 — canary scan over artifact and both review outputs | SC-010 (T072) | faithful to the criterion as written; narrowed by DBR-017's reading (F3) |
| DBA-011 — v1 plan rejected with a re-plan message, no write | SC-011 (T024, T065) | faithful |
| DBA-012 — no new command group, review through existing commands | SC-012 (T002 baseline, T064) | faithful |
| DBA-013 — config-version mismatch refused without parsing; opaque round-trip | SC-013 (T014, T057) | faithful |

**Summary of the walk**: nothing **unowned**. Three items **weakened or narrowed**, all disclosed
somewhere in the artifacts and all traceable to two root causes — the incremental destination path
(DBR-001, DBR-009, DBA-006) and the undefined term "secret" (DBR-017). Four items **exceeded**, all
benignly and all in the direction of stronger evidence or a stronger bar (DBR-003/DBR-006's extra
checks, DBA-004's sixth case, DBA-008's pre-existing peer, DBR-020's no-new-command).

---

## Unauthorized scope

**Verdict: none. Nothing here belongs to a dependency outcome.** I checked each of the brief's nine
Out-of-scope lines against the requirements, the plan's module inventory, and all 85 tasks.

| Out-of-scope line | Status |
|---|---|
| Applying a delete to the destination | Honored, and honored *structurally* — AD004 keeps deletes out of the comparison result the write path consumes, so a delete cannot reach a destination even by misconfiguration. Stronger than required |
| The shared execution core refactor (DB-002) | Honored. AD039 reorders `sync_in_tiers` (compute all tier diffs, write the artifact, then execute) — that is a local reordering forced by FR-001's before-any-write clause, not the core refactor |
| Durable run/artifact storage behind provider interfaces (DB-005) | Honored — the artifact is a `plan/` subdirectory in the existing per-run cache directory; no provider interface anywhere |
| Creating/validating/managing configuration versions (DB-008) | Honored — one digest, stored and compared for equality, never parsed. FR-011 says so and SC-013 tests the opacity |
| A durable per-operation apply ledger (DB-012) | Honored — FR-020 records identifiers on the run result only, and FR-025/AD011 explicitly refuse to make it crash-surviving |
| Load-path reference-scan replacement, batched destination writes (DB-007) | Honored — FR-014 is apply-path only; FR-026 forbids a grouping field in the format |
| **Any new CLI command group** (DB-004) | Honored twice over — no group and no command; T002 captures the baseline before Phase F and T064 diffs `--help` and asserts no `add_typer` exists |
| Destination freshness checks, plan expiration, conflict policies | Honored — AD025 explicitly declines conflict detection, and AD030 records retention/expiry as an exclusion rather than building it |
| Branch review mode (DB-011) | Honored — no mention anywhere |

Two calls that I examined and cleared:

- **FR-027's `format_version` field and its unknown-field tolerance.** AD028's stated motive is that "a
  later outcome adds a schema-fingerprint field to this same manifest" — designing for DB-010. That
  would be dependency-outcome scope, except the brief's §"Shared contracts this brief owns" names
  DB-010's schema-fingerprint field as a consumer of *this* format, which makes forward tolerance this
  brief's obligation. Cleared.
- **AD040's removal of the pre-existing `apply_cached_row` dispatch.** Removing existing code is a
  bigger move than adding beside it, but DBR-019 forbids "a second apply path with weaker guarantees"
  and V3 establishes the surface has zero implementations. Cleared.

The only scope question I am leaving open is F10 (the schema-subhash run-state fix), which is a
pre-existing-bug fix on the same command rather than another outcome's work.

---

## Evidence deferral

**Verdict: adequate disclosure, correctly scoped, not a softened requirement.** Full reasoning is in
F9. In short: the five-criterion count is right and I verified the other eight are producible locally;
the artifacts consistently separate SC-016's derived live half from the brief's tally; the deferral is
stated in six places and each one says the completion condition is not met at merge; and the two offline
compensations are labelled as narrowing rather than closing, with an explicit instruction not to record
Phase H as satisfied on their strength. The gap this exposes is in the brief, recorded as F9.

---

## Planner feedback — every `brief-gap`

| ID | Brief section | What it should have said | Repair level |
|---|---|---|---|
| F2 | §Edge cases and failure behavior; §Acceptance criteria, DBA-006 | Edge cases: "Deletes are derived by set difference against the loaded destination state. When the destination side is loaded incrementally that enumeration is incomplete; state whether deletes are then omitted (and how the omission is disclosed to a reviewer) or whether a full destination extract is a precondition for a complete plan." DBA-006: state whether byte-determinism is required across extraction modes or only within one. | `instance` |
| F3 | §Requirements, DBR-017; §Acceptance criteria, DBA-010 | DBR-017: define the term — "'Secret' means the credential values in the configuration's `settings`; classification of mapped source data values is out of scope" (or take the wider reading and accept the classification model as scope). DBA-010: name the canary's injection point. | `instance` |
| F6 | §Edge cases and failure behavior | "A reviewed update whose destination object was removed between plan and apply. The convergent write creates it. State whether that is acceptable and how the apply result reports the action." The Outcome's "applied equals reviewed" and the freshness/conflict exclusion collide precisely here. | `instance` |
| F9 | §Assumptions (and, systemically, §Completion conditions) | Assumptions: "A live Infrahub is reachable in the implementation environment. Impact if wrong: DBA-001, DBA-002, DBA-003, DBA-008 and the live half of DBA-007 cannot be evidenced at merge; the completion condition is met only on a later live run." Systemically, the completion-condition template admits no notion of environment-gated evidence. | `instance` |

---

## Report metadata

Path: `dev/specs/001-plan-artifact-saved-apply/critiques/fidelity-r1.md`
