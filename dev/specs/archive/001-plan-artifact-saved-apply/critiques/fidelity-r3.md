# Fidelity critique — round 3 (final)

**Lens**: fidelity (scope authority + brief conformance + planner feedback)
**Brief**: DB-001 `db-001-plan-artifact-saved-apply.md`, brief_version **5** (unedited), batch-v3,
primary card INFP-653
**Under review**: `spec.md` (2499 lines, FR-001…FR-030, SC-001…SC-018, AD001–AD074), `plan.md` (818),
`tasks.md` (674, 90 tasks with T060 struck), `research.md` (530), `data-model.md` (395),
`quickstart.md` (285), `contracts/` ×4, `checklists/` ×5. No code written.
**Branch/head**: `001-plan-artifact-saved-apply-infp-653` @ `5570270`, worktree clean
**Prior rounds**: `critiques/fidelity-r1.md` (F1, F2 blocking), `critiques/fidelity-r2.md`
(R2-F1 blocking). Packet: `critiques/collation-r2.md`.

**Verdict**: the brief remains one independently testable spec — **no `NEEDS_INTAKE_REVISION`**.
**No blocking finding remains.** R2-F1 is closed by a complete withdrawal, verified in all five
artifacts and in the guardrail list; R2-F2 is closed and the corrected authority is right; R2-F3 is
recorded for the planner in three places and the brief is untouched. The struck keyedness guarantee is
a **legitimate narrowing of a specification-invented overclaim, not a weakening of a brief
requirement** — DBR-013 still ships as a MUST, and the residual is disclosed as Material. AD065–AD074
add no scope and AD070 removes some. Across three rounds nothing in the brief is unowned, and the two
items that moved (DBR-016, DBA-007) moved by the brief owner's override with the protected property
preserved and measured.

---

## Findings

| ID | Severity | Classification | Summary | Anchor |
|---|---|---|---|---|
| R3-F1 | Recommended | defect in the generated artifacts | AD074's "second and stronger ground, which needs no override at all" is **unsound against the brief's own text**. DBA-007 and Scenario 4 both apply the term *the unsupported operation* to the recorded delete, so DBR-016's term demonstrably did reach a recorded delete. The override was therefore **strictly necessary**, and the spec asserts the contrary once without hedging | `spec.md` :809-814, :2366-2368; brief :150-151, :189 |
| R3-F2 | Recommended | `brief-gap` (**instance**) | Brief §Dependencies records "Infrahub destination adapter convergent write path — **Satisfied**" on VAL-9's prototype. This delivery established that the claim holds only where the destination kind's convergence key is all-direct; for a key that crosses a relationship the client cannot form it from a resolved peer id, so the write goes out unkeyed. That is nine kinds and ten mapping entries of the qualified path. The row should have been scoped and §Assumptions should carry it with an impact-if-wrong | brief :241, :270; `plan.md` :813, :812; `spec.md` :1516-1531 |
| R3-F3 | Recommended | defect in the generated artifacts | The `[PROVISIONAL AD0NN]` markers (196 lines carry one) mean "pending ratification, removed once confirmed" (`spec.md`:32-34) while all six session headers state their decisions **are** ratified. Every requirement an implementer reads therefore carries a handle saying it may not hold. Bookkeeping, not scope — but it is on FR/SC text an implementer acts from | `spec.md` :32-34 vs :312, :420, :494, :595, :682, :858 |
| R3-N1 | Nit | defect in the generated artifacts | Stale decision ranges in `plan.md`: :28 "AD001–AD064" and :191 "AD001–AD053" after AD074. `tasks.md`:9 was corrected | `plan.md` :28, :191 |
| R3-N2 | Nit | defect in the generated artifacts | AD066's justification is quantified as "the qualified path's **ten** identity-bearing-reference mapping entries" (V30), a count of *plan-identity* crossings read from `config.yml`. The gate branches on the **destination kind's** human-friendly-ID shape, which is read from the destination schema and is not shown to be the same set. The trade is right either way; the figure is a proxy and should say so | `plan.md` :147, :536; `spec.md` :879-884; `tasks.md` :118 |
| R3-N3 | Nit | defect in the generated artifacts | Four round-1 non-blocking items remain unrepaired, as expected (the packet routed only blocking themes): r1 F4 (five verification checks recorded as coverage rather than expansion of DBR-003/DBR-006), r1 F7 (DBR-008's plural "checksums" resolved to one plan-level checksum with the reading recorded nowhere — `grep checksums` still returns nothing), r1 F11 (the shared-execution-core constraint appears in §Out of Scope but has no row in the constraint traceability table), r1 F12 (FR-024 says "what is missing" where the brief's edge case says "identifier") | `spec.md` :2122 vs the constraint table :2395-2409; :1750; no `checksums` hit |

Severity counts: **Must-Address 0**, **RETHINK 0**, Recommended 3, Nit 3.
`brief-gap` findings this round: **1** — R3-F2 (`instance`).

**No blocking finding remains. Nothing is Must-Address or RETHINK.**

---

## R2-F1 — the live `sync` write path — **CLOSED by complete withdrawal**

**Verified in every artifact, and the contradiction is gone.**

The withdrawal is stated as a withdrawal rather than smuggled in as a rewrite, which is what makes it
checkable:

- **`spec.md`** AD070 (:927-940) answers the question directly — "**No. It is withdrawn.**" — and names
  the reason in the brief's own terms: no requirement, no criterion, no edge case, no documentation
  entry, and "a decision for the brief's owner". FR-013 carries the operative clause as normative text
  (:1550-1554): "**This enforcement is new behavior on the planned-write path only.** The pre-existing
  update path's additive ordering is a pre-existing defect and MUST be left exactly as it is… It is
  recorded as work for a later outcome." The narrowing is repeated at :449-455 and in the non-goals at
  :2222-2229.
- **`tasks.md`** T042 (:323-333) is re-headed "Add … as **new module-level code on the planned-write
  path**, and **do not touch `update_node`**", opens with "This task was specified as an extraction and
  is not one any more (AD070)", and accepts "roughly eight lines of duplication; that duplication is
  what makes 'the live write path is unchanged' literally true". Its Done-when now runs the other way
  from round 2's: `tests/adapters/` passes **unchanged**, plus "a test asserts `update_node`'s body is
  unmodified in effect by exercising the additive case it handles today and asserting nothing is
  removed". The round-2 Done-when required the opposite test to fail against the old ordering; that
  sentence is gone.
- **`tasks.md`** guardrails (:657-662) now read "**No change to the live `sync` write path. No exception
  (AD070).**" The "one deliberate exception" clause round 2 found there is removed and replaced by the
  history of why.
- **`plan.md`** :39, :58, :127 (code fact V12), :567-568, :810 all carry the narrowing.
- **`contracts/destination-write-surface.md`** :25, :102, :207, :254-256 carry it, including the code
  comment placement.

**The contradiction R2-F1 named is resolved in the direction that removes scope.** T044 and T053 still
assert the live path's warn-and-continue is unchanged (`tasks.md`:336, :364); T042 no longer contradicts
them. I re-read the tree at head: `update_node` is still module-level at
`infrahub_sync/adapters/infrahub.py:97`, reads `attr_manager.peer_ids` at `:151`, fetches at `:168-169`,
and its sole caller is `InfrahubModel.update` at `:625`. Nothing in the task set now touches it.

**Nothing else reaches the live `sync` write path.** I enumerated every task that names an existing
module and checked each against the brief:

| Existing surface touched | Task | Authority |
|---|---|---|
| `adapters/infrahub.py` — new module-level helper and new adapter method | T042, T043, T045, T046 | In scope: "A destination write surface on the Infrahub adapter"; "Apply-time relationship peer resolution". `update_node` and the live warn-and-continue untouched (AD048, AD070) |
| `potenda/__init__.py` — `apply_plan` body replaced; v1 `apply_cached_row` dispatch removed | T047, T048, T066 | In scope: "Applying a saved plan by run ID"; DBR-019/D025 forbid a second apply path |
| `cli.py` — `diff` gains `--from-plan/--detail/--kind`; `apply` rewired | T058, T059, T086 | DBR-020 and brief §Constraints, which make the flag spelling an implementation choice inside the no-new-group bar |
| Plan derivation now runs on the non-mutating command, and a derivation failure is fatal there | T028–T036, FR-030 | DBR-001 requires the plan "before writing to the destination", so the derivation is on both paths by construction. AD047 declines a tolerance switch rather than adding surface |
| `run.json` gains three `summary` keys | T047, T059 | Additive inside the already free-form `summary` and the closed `KEYS` tuple (AD062); DB-005's storage boundary holds |
| Existing plan/diff test fixtures | T067 | Brief §Constraints mandates it "in the same change" |

No entry reaches a mutating path the brief does not name. **R2-F1 closed.**

---

## R2-F2 — AD055's authority — **CLOSED, and the correction is right**

`spec.md`:798-808 now reads: "**The authority, corrected (AD074).** This decision was first recorded as
resting on approved decision D020. It does not… The authority is instead the **brief owner's override at
the delivery gate**… D020 is cited here for one narrower thing only — the proviso that a re-derivation
carry its basis." The re-derivation section repeats it at :2362-2368, and the traceability row for
DBR-016 cites "re-derived per AD055 on the authority corrected at AD074" (:1695).

**The recorded authority is now correct.** Blake Ellis is the brief's `approved_by` (brief :14) and the
approver of D020, D025, D026, D005 and D002 (brief :259-264). An approver amending their own approved
artifact at the delivery gate is scope authority acting. The D020 citation is now confined to the
proviso D020 actually supplies, and the artifacts meet that proviso at four places (FR-017, the
re-derivation table, AD055 itself, and both traceability rows). The reading that would have made 6 of 20
requirements and 10 of 13 criteria re-derivable downstream is explicitly repudiated in the spec's own
text, which is the outcome R2-F2 asked for — a miscited authority that gets copied is the harm, and the
spec now says so out loud.

**The narrower ground is not sound — see R3-F1.** It does not affect the closure: the override stands on
its own and is now correctly attributed.

---

## R2-F3 — the brief's two stale passages — **CLOSED as a planner referral; the brief is untouched**

- **The brief was not edited.** `brief_version: 5` (:5), §Out of scope :98-99 still reads "the delete is
  reported and the run fails, never silently skipped", and Scenario 4 :150-151 still reads "completes in
  a **failed** state naming the unsupported operation". Both are verbatim as round 2 found them.
- **The referral is recorded, in three places.** AD055/AD074 (`spec.md`:815-820): "Brief v5's
  Out-of-scope delete bullet and its User-scenario 4 both restate the superseded 'the run fails'
  outcome… a v6 revision owes them the ratified outcome. That repair belongs to the planner; **nothing
  here edits the brief**." The re-derivation section (:2371-2377) names both passages under a heading
  written for the planner: "Two brief passages now read false and are named here so the planner has the
  exact repair target (AD074)". AD074's own entry (:973-976) closes with "the two brief passages that now
  read false are named for a planner revision."
- **The checklist rows round 2's packet listed as stale are repaired.** `checklists/write-convergence.md`
  CHK030 (:74) is restated to the `applied` reading with the supersession named; :140 carries the same.

Round 2's suggested minimum fix — one sentence in the re-derivation section naming the exact repair
target — is delivered as a paragraph. **R2-F3 closed** as far as this delivery can close it; the brief
v6 repair is carried forward in the planner-feedback list below.

---

## R3-F1 — Recommended — the "second ground" is unsound, and the override was necessary

**Classification**: defect in the generated artifacts.

AD074 adds a ground intended to stand without an override (`spec.md`:809-814):

> DBR-016's own term is an **unsupported operation**. A `delete` is a member of the closed action
> vocabulary and a fully recognized operation; what the brief separately excludes is *executing* it… on
> that ground DBR-016 never reached a recorded delete in the first place.

I proposed this ground in round 2 and the packet promoted it. Round 3's mandate is to judge whether it
is sound. **It is not, and I am withdrawing my own round-2 suggestion.** The brief settles the meaning
of its own term in two places, and both settle it against the reading:

- **DBA-007** (brief :189): "A plan containing a delete operation applies its non-delete operations,
  does not delete from the destination, and ends in a failed state naming **the unsupported
  operation**." The criterion is *about* a recorded delete, and it calls that delete "the unsupported
  operation".
- **Scenario 4** (brief :150-151): "completes in a **failed** state naming **the unsupported
  operation** — an unsupported operation is never silently skipped."

So the brief's own usage places a recorded delete inside DBR-016's term. A reading under which
"DBR-016 never reached a recorded delete" would leave DBA-007's phrase without a referent. The
distinction the specification draws — designed-decline versus unrecognized-action — is a good and
necessary distinction, and FR-017 is right to draw it; what it is not is a reading of the brief's
existing text. It is precisely the re-derivation AD055 says it is.

**The consequence is the useful part: the override was strictly necessary.** There was no route to the
`applied` outcome that avoided amending a derived brief item, so the delivery-gate override is not
belt-and-braces — it is the whole authority, and the artifacts are right to lead with it. The spec
hedges once ("arguably a reading of DBR-016's existing term") and then drops the hedge in the same
sentence ("on that ground DBR-016 never reached a recorded delete in the first place"), and the
re-derivation section repeats the unhedged form at :2367-2368.

### Minimum fix

One clause at both sites: note that DBA-007 and Scenario 4 both use "the unsupported operation" of a
recorded delete, so the brief's own usage weighs against this ground, and the brief owner's override is
therefore **necessary** rather than merely primary. Keeping the distinction as the *substance* of the
re-derivation is correct; presenting it as an alternative authority is not.

---

## R3-F2 — Recommended — `brief-gap` — a dependency recorded Satisfied is only partly satisfied

**Classification**: `brief-gap` — repair level **instance**.

Brief §Dependencies (:241):

| Dependency | Type | Status | Required contract | Satisfaction evidence |
|---|---|---|---|---|
| Infrahub destination adapter convergent write path | External, already present | **Satisfied** | The adapter's identifier-keyed upsert converges on repeat | VAL-9 prototype (create, idempotent re-apply, update against live Infrahub); VAL-4 per-class matrix |

This delivery has established, against the destination client library, that the row holds only for a
subset of the qualified path. Where the destination kind's human-friendly ID is composed of its own
direct attributes, the upsert keys and converges. Where a component **crosses a relationship**, the
client resolves the peer through its own store, a peer supplied as a resolved identifier renders with no
type discriminator so the store is never consulted, one unresolved component nulls the whole
human-friendly ID, and the mutation goes out carrying neither identifier — the unkeyed write that
duplicates on re-apply (`plan.md`:813, `spec.md`:1516-1531). On the qualified configuration that is ten
mapping entries across nine kinds (`plan.md` V30 :147, verified below), including every interface kind,
`DcimDevice`, `IpamPrefix`, `IpamIPAddress`, `IpamVLAN`, `LocationRack` and `DcimDeviceType`.

The brief has the slots for this and neither is filled:

- **§Dependencies**, the row above, should have scoped its status — Satisfied *for destination kinds
  whose convergence key is composed of direct attributes*; unverified where the key crosses a
  relationship, since VAL-9's prototype exercised a single kind and its evidence column does not reach
  the class. As written, an implementer is told the write path is present and converges.
- **§Assumptions** carries the adjacent assumption ("Destination identifier attributes are
  unique-constrained on the qualified path", :270) but not this one, which is a different claim about a
  different party: the *client's* ability to form the key, not the *destination's* enforcement of it.
  The impact-if-wrong slot is exactly where it belongs — "DBR-013's convergence and DBA-002/DBA-003 hold
  only for kinds whose convergence key is direct; the relationship-crossing class may duplicate on
  re-apply."

Repair level **instance**: two rows in one brief. It is worth the planner's attention beyond this brief
only in that DB-011, DB-012 and DB-014 all consume this apply path and inherit the same partial
satisfaction; if the batch manifest carries the dependency row forward, the scoping travels with it.

**This is not a delivery defect.** The delivery discloses it as a **Material** risk reported to root
(`plan.md`:813), gives it an offline detector (T045's per-component diagnostic), a self-invalidating
test marker (T081 assertion 2, `xfail(strict=True)`), an operator-facing warning once per destination
kind, and an explicit statement that the class "rests entirely on the deferred live evidence"
(`spec.md`:2251-2252, `plan.md`:812, `quickstart.md`:21-22). That is the honest treatment. What is
missing is on the brief's side of the line.

---

## R3-F3 — Recommended — 196 lines still say "provisional" while six sessions say "ratified"

**Classification**: defect in the generated artifacts.

`spec.md`:32-34 defines the marker: "Each is **provisional** pending ratification — the
`[PROVISIONAL AD0NN]` markers below are the ratification handles and are removed once the decision is
confirmed." Every session header then states the opposite of "pending": ":858 — All ten are ratified";
":682 — All eleven are provisional on the same basis"; the round-two summary at :2474 — "were
**ratified** after the same three lenses re-ran". 196 lines of `spec.md` carry a marker, and they sit on
FR text, SC text and Key Entities — the sentences an implementer executes from.

The skill's gate strips provisional markers at the decision gate, so this resolves itself procedurally.
It is recorded because the count has grown from 63 to 196 across the three rounds while the sessions
have grown more emphatic about ratification, and because `tasks.md` carries the same markers into task
text where a worker will read them as "this instruction may be withdrawn". Bookkeeping — it changes
nothing that ships.

### Minimum fix

Either strip the markers for the decisions the sessions declare ratified, or amend :32-34 so the marker
means "recorded decision, revisit set attached" rather than "pending ratification". The revisit-set
mechanism the markers provide is genuinely valuable and should survive whichever way this goes.

---

## The narrowed keyedness guarantee — fidelity verdict

**Legitimate. It narrows a claim this specification invented, not a requirement the brief made. DBR-013
is still delivered, as a MUST, with its residual disclosed. No brief item is weakened.**

The struck sentence — "an unkeyed write is never issued" — appears nowhere in the brief. It was a
specification-side absolute, and round 2's engineering lens established it rested on a check that could
not deliver it (the gate read the assembled payload; keyedness is a property of the rendered mutation).
Three questions decide the fidelity ruling.

**1. Is the replacement weaker than any brief obligation?** No. The brief's obligations here are
DBR-013 (the adapter can execute a planned create or update convergently, so repeating does not create a
second object), DBR-011 (deliver a destination write surface), DBA-002 (re-apply converges) and DBA-003
(clean-single-run counts per class). Every one is unchanged in the artifacts. FR-013 still MUSTs the
convergent routing keyed on the human-friendly ID and still MUSTs that the assembled data carry every
component (`spec.md`:1500-1509). SC-002 and SC-003 are word-for-word what they were (:1954-1971) — I
checked specifically that the remediation did not soften the criteria to match the narrowed offline
claim, which is the failure mode this finding class exists to catch. It did not. What changed is the
*compensating offline claim* and the *pre-write gate's shape*, both of which are this specification's
own inventions in the space AD045 opened.

**2. Is "warn and proceed" the right side of the trade on the brief's terms?** Yes, and the alternative
would have damaged the brief. Brief §Constraints fixes the qualified path as
`examples/netbox_to_infrahub/config.yml`. I verified the affected set against that file rather than
taking V30 on trust: `LocationRack` identifiers `["name","site"]` with `site` a reference;
`DcimDeviceType` `["name","manufacturer"]`; `DcimDevice` `["location","name"]` in **both** mapping
entries (:213, :255); `InterfacePhysical`, `InterfaceVirtual`, `InterfaceLag` each `["device","name"]`;
`IpamVLAN` `["name","vlan_id","vlan_group"]`; `IpamPrefix` `["prefix","vrf"]`; `IpamIPAddress`
`["address","vrf"]`. Ten entries, nine kinds — the count is real. A universal refusal at the gate would
make the apply decline the greater part of the qualified configuration, which does not deliver DBR-011's
write surface or DBR-013's convergent create/update in any form an operator could use, and leaves DBA-008
with only the thin relationship-bearing kinds. Refusing a write the destination may well key server-side,
on the strength of a client-side render the specification cannot verify offline, would be **over-refusal
that declines a required capability**. Proceeding with a disclosed warning keeps the capability inside
what ships and puts the uncertainty where the brief already put it: on the live evidence.

**3. Is the residual disclosed, or absorbed?** Disclosed, and more sharply than before. The gate still
**raises** wherever an unkeyed render can only mean a defect (all-direct convergence key — the AD042
regression detector, preserved); it warns once per destination kind where the cause is outside this
outcome's control; T081's assertion 2 carries the relationship-crossing case as `xfail(strict=True)`
citing the Material risk row, so the limitation retires itself the day it closes rather than going stale
in prose; and the replacement claim is stated in the same words in all four places the old one appeared
(`spec.md`:1530, `plan.md`:537-539, `tasks.md`:39-41, `contracts/destination-write-surface.md`:148-151).
I grepped for survivors of the flat claim: the only remaining occurrences are the three that *record it
as struck*. Nothing asserts it any more.

**DBR-013: still delivered.** As an obligation it is unchanged and slightly better instrumented (the
diagnostic can now name *which* component is missing; the gate can now see keyedness at all). As
*evidence* it was already deferred to a live run before this round, and it still is — the deferral did
not grow. What is new is a recorded, honest, negative offline signal for one class within it. That is a
disclosure improvement, which is why R3-F2 is aimed at the brief's dependency row rather than at the
spec.

**Two caveats, both non-blocking.** The magnitude figure is a proxy (R3-N2): the ten entries are
plan-identity crossings read from the configuration, while the gate branches on the destination kind's
declared human-friendly ID, read from the destination schema. And the same underlying fact now generates
two operator warnings from different requirements — FR-024's plan-time convergence-key warning (the
brief's own edge case) and AD066's apply-time per-kind warning. They are distinct requirements with
distinct triggers and both are wanted; whether an operator experiences them as one story is an
ergonomics question, not a fidelity one.

---

## AD065–AD074 — unauthorized-scope check

| Decision | Scope verdict |
|---|---|
| **AD065** | Clear. Names the *mechanism* for a re-read FR-013 already mandated; the observable moves from "was fetched" to "a destination read was issued". No new capability, and explicitly confined to the planned-write path |
| **AD066** | Clear, and **scope-preserving** — see the verdict above. Strikes a specification-invented absolute; every brief obligation it touched is unchanged |
| **AD067** | Clear. Splits an offline assertion so a known hole is a live, self-invalidating fact. Records a limitation; creates none |
| **AD068** | Clear. Replaces an offline assertion that could not fail against a test double with one that can. Not a brief criterion — SC-002 and SC-003 are untouched |
| **AD069** | Clear, and it protects a brief item: without the merge, `RunFile.save()`'s whole-payload write destroys FR-020's record, which is DBR-005's link between review and application and the contract DB-012 consumes. Verified in the tree: `cache/sidecars.py:87-89`, `cli.py:322-323` then `:350-351`. Ownership stays where it already is; no layer boundary moves |
| **AD070** | **Removes scope.** The one decision this round that changes what ships, and it changes it by withdrawal — see the R2-F1 closure above |
| **AD071** | Clear. Two named exception classes with next actions on the **new** derivation path, inside FR-030's existing mandate. AD047's refusal to add a tolerance switch to the non-mutating command is reaffirmed, so no user-facing surface appears |
| **AD072** | Clear. Repairs a walkthrough that raised the wrong error. Documentation-side; no requirement moves |
| **AD073** | Clear. Bounds and guards an error-path enumeration that is itself new. Note the guard is load-bearing rather than cosmetic: `cache/paths.py` computes the cache root without creating it, so the unguarded listing raises for a first-time operator |
| **AD074** | Clear on scope; the substance is R2-F2's closure, with one unsound ground — R3-F1 |

**All nine of the brief's out-of-scope boundaries still hold.** I re-checked the two with the largest
blast radius. No delete reaches a destination: structural via AD004 (deletes never enter the comparison
result the write path consumes), FR-016, and `SkippedDeleteOperation` raised before any destination
contact in T045. No new command group or command: FR-008, SC-012's committed baseline fixture, T064's
five-command assertion and its `add_typer`-absence assertion. The guardrail list at `tasks.md`:643-674
now carries eleven prohibitions, one of them absolute where round 2 found an exception.

---

## Evidence deferral — still correctly disclosed, and it has not grown

**Verdict: unchanged in scope, improved in honesty.** Still exactly **five** brief criteria — DBA-001,
DBA-002, DBA-003 and DBA-008 in full, plus the live half of DBA-007 — plus SC-016's live half, which the
artifacts continue to exclude from the brief's tally because this specification derived it. The count
and the enumeration are identical at every disclosure site: `spec.md`:2241-2253, `plan.md`:175, :766,
:812, `tasks.md`:20-38, `quickstart.md`:10-13 and :154-157. Each still states in substance that the
brief's completion condition is **not met at merge**.

Two changes this round, both tightening:

- **The compensating harness's reach is now bounded in writing.** Every site that credits T081 with
  narrowing the deferral now also says how much it does not narrow: "for a destination kind whose
  convergence key crosses a relationship it can only record that the rendered mutation is unkeyed today,
  as a strict expected failure, so that class of convergence stays entirely on the deferred live
  evidence" (`spec.md`:2249-2252, and the same at `plan.md`:812, `tasks.md`:38-41, `quickstart.md`:21-22).
  Round 2 could read the harness as closing more than it does; it cannot now.
- **"Do not record Phase H as satisfied on the strength of either" survives** (`tasks.md`:38), and now
  covers both narrowing mechanisms rather than one.

No finding. The underlying brief-gap — §Assumptions never records that a live Infrahub is required —
remains open with the planner (r1 F9), and R3-F2 adds the adjacent one.

---

## DBR / DBA walk

Every brief item, its carrier, and a verdict at head `5570270`. "Faithful" means the spec obligation is
at least as strong as the brief item and no weaker. Deltas from round 2 are marked **[r3]**.

### Requirements

| Brief item | Spec carrier | Verdict |
|---|---|---|
| DBR-001 — plan contains every proposed create/update/delete/relationship before writing | FR-001; FR-015 for the delete class; User Story 1 | **conditional, fully disclosed** — AD024's incremental-path omission stands; FR-006/SC-009/`PlanSummary`/both contracts make the omission mandatory output at both review depths. Unchanged this round |
| DBR-002 — summary and per-object views | FR-006, FR-029; SC-009 | faithful, **strengthened** — AD056's disclosure obligations, plus **[r3]** AD073 bounding and guarding the run enumeration and AD072 repairing the only hand-run demonstration of the two reader/renderer branches |
| DBR-003 — validate a saved plan remains safe | FR-009; SC-004 | faithful, **exceeded** — five mandatory checks against the brief's three (r1 F4's labelling objection unrepaired, non-blocking) |
| DBR-004 — apply saved operations without recomputing | FR-012; SC-001 | faithful; evidence deferred, disclosed |
| DBR-005 — stable identifier linking review/application/audit/recovery | FR-003, FR-006, FR-020, FR-021; SC-005 | faithful, **strengthened [r3]** — AD062 gave the record one named home and AD069 gave it one *writer*; without AD069 the record was being deleted moments after being written, so this is the round's most consequential protection of a brief item |
| DBR-006 — safe when checksum, config version, snapshot binding match | FR-004, FR-009, FR-027 | faithful, **exceeded** — safety is five conditions |
| DBR-007 — resolve peers at apply time with no comparison store | FR-014; SC-008, SC-016 | faithful, **strengthened [r3]** — AD065 makes the re-read a mechanism with an observable that the no-op cannot satisfy; AD071 gives the two unnamed derivation failures a class and a next action |
| DBR-008 — define the format, per-operation identifiers and checksums | FR-002, FR-004, FR-027, FR-028 | faithful on identifiers; plural "checksums" still resolved to one plan-level checksum with the reading recorded nowhere (r1 F7, unrepaired, non-blocking) |
| DBR-009 — record deletes, changing today's suppression default | FR-015; SC-017; User Story 4 | **conditional, fully disclosed** — same AD024 condition; SC-017 asserts an incremental plan's apply records a zero skipped count so no phantom delete inflates what an operator sees |
| DBR-010 — do not apply deletes | FR-016 | faithful, and structural. AD055 changes the framing, not the behavior. **[r3]** T045 raises before any destination contact; the guardrail is absolute |
| DBR-011 — format with identifiers and full payloads, write surface, peer resolution | FR-002, FR-013, FR-014, FR-028 | faithful. **[r3]** AD066 is what keeps the relationship-bearing part of the write surface inside what ships rather than declining it |
| DBR-013 — Infrahub adapter executes a planned create or update convergently | FR-013; SC-002, SC-008 | **faithful — delivered, with the offline claim honestly narrowed [r3]**. FR-013's MUSTs are unchanged and SC-002/SC-003 are unchanged. The live-path spillover R2-F1 found is **withdrawn** (AD070). The relationship-crossing class carries a Material risk, an operator warning, a strict-xfail marker and an explicit statement that it rests on deferred live evidence. The brief-side gap is R3-F2 |
| DBR-014 — deterministic serialization, stable checksum | FR-005, FR-028; SC-006 | faithful as a requirement; its criterion is the conditional one, reported as such (AD064) |
| DBR-015 — bind plan and source snapshot so the pair cannot tear | FR-004, FR-010; SC-004 | faithful |
| DBR-016 — unsupported operation reported, fails the run, never silently skipped | FR-017; FR-016, FR-020; User Story 4 scenarios 1–3 | **re-derived** (AD055) on the brief owner's delivery-gate override, **authority now correctly recorded [r3]** (AD074). Not weakened on the property it protects: the applied ∪ skipped closure is asserted at SC-007, T054 and T078, `failed` is an explicit *failing* state for the criterion, and **[r3]** the closure is now scoped to a completed apply and the warning is pinned at `logging.WARNING` so `--quiet` cannot suppress the one operator signal the re-derivation rests on. Basis carried at four places. One unsound supporting ground: R3-F1 |
| DBR-017 — no secret in the artifact or any review output | FR-018; SC-010 | **narrowed (disclosed)** — "secret" scoped to `settings` credentials by AD018; unchanged. Brief-gap r1 F3 open |
| DBR-018 — configuration-version field, opaque, compared for equality | FR-011; SC-013 | faithful; AD041's credential-rotation trade unchanged and still weighed on one ground only (r1 F5, non-blocking) |
| DBR-019 — v1 detected and rejected, not migrated, no second apply path | FR-019; SC-011 | faithful. **[r3]** T048's grep-clean done-condition and T066 landing in the same change keep the second path from surviving by accident |
| DBR-020 — review by extending existing commands + in-process API, no new group | FR-008, FR-029; SC-012 | faithful, **exceeded benignly** — no new command either; AD057's spelling sits inside the brief's own stated latitude |

DBR-012 (readable from the stored artifact at any time) — FR-007, FR-029; SC-009 — faithful, unchanged.

### Acceptance criteria

| Brief item | Spec carrier | Verdict |
|---|---|---|
| DBA-001 — applied without re-extraction, no fork-wide comparison rewrite | SC-001 (T075, `integration`) | faithful; evidence deferred, disclosed |
| DBA-002 — re-apply converges, no duplicate | SC-002 (T076, `integration`) | faithful, **criterion text unchanged [r3]** — deliberately checked, since AD066/AD067 narrowed the offline proxy and did not touch the criterion. Evidence deferred; the relationship-crossing class now carries a recorded negative offline signal, disclosed at four sites (R3-F2 is the brief-side repair) |
| DBA-003 — create/update/relationship classes at clean-single-run counts, both crash windows | SC-003 (T077, `integration`) | faithful to the brief's own narrowing; criterion text unchanged; evidence deferred |
| DBA-004 — refusal before any write, five negative cases | SC-004 (T025, T065) | faithful, **exceeded benignly** — six cases, all asserted individually on the CLI apply path, each with zero-write and run-state halves |
| DBA-005 — review identifiers are the apply-result identifiers | SC-005 (T056) | faithful, **strengthened [r3]** — the apply-side set is read from a named home with a single writer; T056's delete-free-fixture precondition keeps the comparison meaningful |
| DBA-006 — re-plan byte-identical, mask exactly two fields | SC-006 (T041) | **carried conditionally, reported as such at both carriers** (AD064) |
| DBA-007 — delete recorded, non-deletes applied, run fails naming it | SC-007 (T054 local, T065 CLI, T078 live) | **re-derived** (AD055): run state `applied`, recorded non-zero skipped count with identifiers, warning pinned at `logging.WARNING`, completion line naming the counts, and the applied ∪ skipped closure asserted **of a completed apply [r3]**. Live half still deferred. Basis carried; brief restatements referred to the planner |
| DBA-008 — relationship kind applies with no comparison store, peers match | SC-008 (T079, `integration`) | faithful, **exceeded benignly** — the pre-existing-peer requirement is retained, which is what makes the query path actually run. Evidence deferred. **[r3]** AD066 is what keeps this criterion achievable rather than declined |
| DBA-009 — summary and detail after process exit, in-process and CLI | SC-009 (T027, T061, T087) | faithful, **strengthened** — pass condition includes the delete-computation record; two of four cases run on an incremental plan |
| DBA-010 — canary scan over artifact and both review outputs | SC-010 (T072) | faithful to the criterion as written; narrowed by DBR-017's reading (r1 F3) |
| DBA-011 — v1 plan rejected with a re-plan message, no write | SC-011 (T024, T065) | faithful |
| DBA-012 — no new command group, review through existing commands | SC-012 (T064, T002 baseline) | faithful, **strengthened** — committed baseline fixture, and T064 fails rather than regenerates when the fixture is absent |
| DBA-013 — config-version mismatch refused without parsing; opaque round-trip | SC-013 (T014, T057) | faithful |

### Summary of the walk

- **Unowned: nothing.** Every DBR and DBA has at least one FR or SC carrier, and every brief edge case
  and constraint has a traceability row except the shared-execution-core constraint, which is honored in
  §Out of Scope but has no row (r1 F11, Nit).
- **Weakened: nothing.** The two items that moved — DBR-016 and DBA-007 — moved by the brief owner's
  override of *derived* items, with the protected property preserved, asserted three times, and made
  un-revertible by an explicit "`failed` fails this criterion". Quoted items are untouched: DBR-009,
  DBR-010, DBR-001 and DBA-003's brief-narrowed form all read as the brief states them. The item most at
  risk of quiet weakening this round was DBR-013, and its criteria are word-for-word unchanged.
- **Exceeded: seven, all toward a stronger bar** — DBR-003/DBR-006's five checks, DBA-004's sixth case,
  DBA-008's pre-existing peer, DBR-020's no-new-command, DBA-009's disclosure and incremental cases,
  DBA-012's committed baseline, DBR-005's single-writer record.
- **Narrowed and disclosed: one** — DBR-017 (r1 F3, open with the planner).
- **Conditional and disclosed: two** — DBR-001 and DBR-009 under AD024, with the disclosure now
  mandatory output at both review depths.
- **Exceeded without authorization: none.** Round 2's single instance — the live `sync` relationship
  semantics — is withdrawn.

---

## Planner feedback — the complete `brief-gap` list across all three rounds

Deduplicated and final. Six gaps, all at repair level `instance` except one `systemic`.

| ID | Brief section | What it should have said | Repair level |
|---|---|---|---|
| r1 F2 | §Edge cases and failure behavior; §Acceptance criteria DBA-006 | Edge cases: deletes are derived by set difference against the loaded destination state, so when the destination side is loaded incrementally the enumeration is incomplete — state whether deletes are then omitted (and how the omission is disclosed to a reviewer) or whether a full destination extract is a precondition for a complete plan. DBA-006: state whether byte-determinism is required across extraction modes or only within one | `instance` |
| r1 F3 | §Requirements DBR-017; §Acceptance criteria DBA-010 | DBR-017: define "secret" — the credential values in the configuration's `settings`, with classification of mapped source data values out of scope (or take the wider reading and accept the classification model as scope). DBA-010: name the canary's injection point | `instance` |
| r1 F6 | §Edge cases and failure behavior | A reviewed `update` whose destination object was removed between plan and apply: the mandated convergent write creates it. State whether that is acceptable and how the apply result reports the action. The Outcome's "applied equals reviewed" and the freshness/conflict exclusion collide precisely here | `instance` |
| r1 F9 | §Assumptions (and, systemically, §Completion conditions) | "A live Infrahub is reachable in the implementation environment. Impact if wrong: DBA-001, DBA-002, DBA-003, DBA-008 and the live half of DBA-007 cannot be evidenced at merge; the completion condition is met only on a later live run." Systemically, the completion-condition template admits no notion of environment-gated evidence | `instance` |
| R2-F2 | §Approved decisions, D020 | Whether a ratified DERIVED requirement is thereafter brief content on the same footing as a QUOTED one, or whether an implementation may re-derive it — and if it may, who ratifies the re-derivation and what it must carry beyond a basis. As written, D020 reads as licensing downstream re-derivation of 6 of 20 requirements and 10 of 13 criteria. **This delivery needed exactly that ruling and had to reach it by an approver override at the gate** (see R3-F1: no reading of the brief's text avoided the override, because DBA-007 and Scenario 4 both apply "the unsupported operation" to a recorded delete) | **systemic** — D020 is batch-wide and inherited by every brief in batch-v3 |
| R2-F3 | §Out of scope (delete bullet, :98-99); §User scenarios Scenario 4 (:143-151) | Both restate the pre-override behavior ("the run fails") and now read false. Brief v6 should restate them to the ratified outcome: the delete is reported, recorded and not executed; the run completes, recording how many deletes it skipped and which; never silently skipped. Fold in the originating gap too — that recording deletes by default plus failing on any recorded delete makes the qualified path's default posture a failed apply, since the engine's fallback flag set yields deletes for any destination holding mapped objects absent from the source | `instance` |
| **R3-F2** | §Dependencies and shared contracts (row: Infrahub destination adapter convergent write path); §Assumptions | The dependency row records **Satisfied** on VAL-9's prototype. Scope it: satisfied for destination kinds whose convergence key is composed of their own direct attributes; **unverified where the key crosses a relationship**, which is ten mapping entries across nine kinds on the qualified path — the destination client cannot form the key from a peer supplied as a resolved identifier, so the write goes out unkeyed and may duplicate on re-apply. §Assumptions should carry it as its own assumption with an impact-if-wrong, distinct from the existing unique-constraint assumption (that one is about the destination's enforcement; this one is about the client's ability to form the key). DB-011, DB-012 and DB-014 inherit the same partial satisfaction through this apply path | `instance` |

---

## Report metadata

Path: `dev/specs/001-plan-artifact-saved-apply/critiques/fidelity-r3.md`
Round 3 of a maximum 3 — final fidelity round.
Head reviewed: `5570270`. Brief: v5, unedited. No code written; no commits made.
