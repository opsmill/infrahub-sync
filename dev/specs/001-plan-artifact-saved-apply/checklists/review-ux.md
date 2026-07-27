# Plan Review Experience Requirements Checklist: Saved plan artifact and apply-exactly-what-was-reviewed

> **Superseded in part, 2026-07-27 — round-one critique remediation.** This checklist records an
> evaluation performed against the specification as it stood on 2026-07-26. No box below has been
> changed. Ratified decisions **AD054–AD074** have since moved several of the requirements it
> interrogates, so where an item's wording and the current specification disagree, the specification
> is authoritative. The moved requirements are FR-006, FR-008, FR-013, FR-016, FR-017, FR-020, FR-029,
> SC-007, SC-009, SC-012 and SC-017 — most consequentially **AD055**, under which a plan containing a
> delete now applies its non-deletes and ends in the **`applied`** state with a recorded skipped-delete
> count and a warning naming it, rather than ending `failed`.

**Purpose**: Validate the quality of the *requirements* governing the plan review experience —
summary and per-object depth, in-process and CLI reachability, reading a stored artifact in a new
process, the no-new-command-group constraint, secret redaction in the artifact and in both review
outputs, and operator-actionable failure messages. These items interrogate what the specification
says, not what the implementation does.

**Created**: 2026-07-26

**Feature**: [spec.md](../spec.md)

**Requirements in scope**: FR-006, FR-007, FR-008, FR-018; SC-009, SC-010, SC-012; User Story 1
scenarios 2–4; Clarifications AD005.

**Dimensions addressed**: completeness, clarity, consistency, measurability, scenarios and edge
cases, dependencies, assumptions.

**How to use**: every item is a yes/no question about the requirements documents, answerable by
reading them. Items are left unchecked deliberately — a separate reviewer marks them `[X]`.

## Requirement Completeness

- [X] CHK001 Does FR-006 specify which fields a per-object detail record must present — in particular the operation identifier, which SC-005 depends on being visible at review time? [Gap, Spec §FR-006, §SC-005]
- [X] CHK002 Does FR-006's summary specify whether counts are per action, per kind, or the action-by-kind cross product? [Clarity, Spec §FR-006, §SC-009]
- [X] CHK003 Is the kind-narrowing capability recorded in AD005 carried by any functional requirement or success criterion, or does it exist only in the clarification? [Gap, Spec §FR-008, §Clarifications AD005]
- [X] CHK004 Are requirements defined for the shape and stability of review output, given later outcomes and a UI are named as consumers of plan summaries? [Gap, Spec §FR-006, §Dependencies]
- [X] CHK005 Is the in-process reader's contract stated beyond "the single implementation" — what it accepts (a run identifier, a path) and what it returns? [Completeness, Spec §FR-008]
- [X] CHK006 Are requirements defined for reviewing a plan that would fail apply verification — may a torn or checksum-mismatched plan be reviewed, or is review refused on the same path? [Gap, Coverage, Spec §FR-007, §FR-009, §FR-010]
- [X] CHK007 Is a redaction rule specified for FR-018 — what constitutes a secret value in a source payload, and whether it is omitted, masked, or never collected? [Gap, Spec §FR-018, §SC-010, §FR-002]
- [X] CHK008 Are requirements defined for the review path's own failure messages (unknown run, absent artifact, v1 plan) at the actionability bar FR-023 sets for apply? [Gap, Spec §FR-007, §FR-019, §FR-023]
- [X] CHK009 Is the documentation obligation for the new review flags stated as a requirement, given the constitution requires user-visible CLI changes to update docs in the same change? [Dependency, Gap, Spec §FR-008; Constitution §Documentation]

## Requirement Clarity

- [X] CHK010 Is "reachable in-process" defined — a supported public entry point, or any importable function? [Clarity, Spec §FR-008, §SC-009]
- [X] CHK011 Is FR-008's standard-output requirement scoped to the read-from-artifact mode only, or does it also change the existing command's live output? [Ambiguity, Spec §FR-008, §Clarifications AD005]
- [X] CHK012 Is "no new CLI command group" precise enough to verify — does it constrain top-level groups only, or also new subcommands and new flags? [Clarity, Spec §FR-008, §SC-012, §Out of Scope]
- [X] CHK013 Is "MUST NOT construct an adapter or extract either side" checkable from outside the process, or does it require inspecting internals? [Measurability, Spec §FR-008, §SC-009]
- [X] CHK014 Is "operator-actionable" given any content bar in the requirements that assert it? [Clarity, Spec §FR-019, §FR-023]
- [X] CHK015 Is "any review output" in FR-018 defined by enumeration — summary, per-object detail, warnings, refusal messages, logs — or left open? [Clarity, Spec §FR-018, §SC-010]
- [X] CHK016 Is "at any time after the run" bounded by any retention or lifecycle statement, or is indefinite availability implied? [Clarity, Spec §FR-007]

## Requirement Consistency

- [X] CHK017 Are FR-008's "no new CLI command is added" and SC-012's "gains no new command group" the same bar, or does one permit a new command inside an existing group while the other forbids it? [Conflict, Spec §FR-008, §SC-012, §Out of Scope]
- [X] CHK018 Is the constitution's structlog-not-print logging standard reconciled with FR-008's requirement that review output go to standard output, and is the reconciliation recorded? [Consistency, Spec §FR-008; Constitution §Logging]
- [X] CHK019 Does the spec state consistently that the existing non-mutating command's output changes because deletes now appear, and where that user-visible change is documented? [Consistency, Spec §FR-015, §Edge Cases/Recorded deletes change existing output]
- [X] CHK020 Do FR-007 ("at any time after the run, including after the process has exited") and SC-009 ("a stored artifact read in a new process") state the same obligation, without one being materially stronger? [Consistency, Spec §FR-007, §SC-009]
- [X] CHK021 Is FR-008's "the in-process reader MUST be the single implementation" consistent with SC-009's four-case matrix, which treats in-process and CLI as separately evidenced paths? [Consistency, Spec §FR-008, §SC-009]
- [X] CHK022 Are the review depths named identically across FR-006, FR-008, User Story 1 scenario 2, and SC-009 — "summary" and "per-object detail" — without a third vocabulary appearing? [Consistency, Spec §FR-006, §FR-008, §SC-009]

## Acceptance Criteria Quality and Measurability

- [X] CHK023 Does SC-009 state the pass condition for each of its four cases — what must appear in each output for the case to pass? [Measurability, Spec §SC-009]
- [X] CHK024 Is SC-010's canary-credential scan specified — which credential values, injected where, and which outputs are captured for scanning? [Measurability, Spec §SC-010, §FR-018]
- [X] CHK025 Is SC-012's before-and-after command-list comparison specified as a reproducible artifact — which command list, captured how, at which level? [Measurability, Spec §SC-012]
- [X] CHK026 Does FR-006 have an acceptance criterion that measures review *content*, as opposed to SC-009 which measures reachability? [Traceability, Spec §FR-006, §SC-009]
- [X] CHK027 Are review latency and plan-size expectations either quantified or explicitly excluded inside the requirements themselves, rather than only noted as a deferral? [Measurability, Spec §Open Design Decisions/Plan size and review performance]
- [X] CHK028 Is FR-018's obligation verifiable for the negative case — can "no secret value appears" be evidenced beyond the specific canaries SC-010 injects? [Measurability, Spec §FR-018, §SC-010]

## Scenario and Edge-Case Coverage

- [X] CHK029 Are requirements defined for reviewing an empty plan — what the summary presents when the operation count is zero? [Coverage, Spec §FR-022, §FR-006]
- [X] CHK030 Are requirements defined for a kind filter naming a kind with no operations, or a kind absent from the configuration? [Gap, Coverage, Spec §Clarifications AD005]
- [X] CHK031 Are requirements defined for reviewing a v1 plan, as distinct from applying one — is the same rejection and re-plan message required on the review path? [Gap, Coverage, Spec §FR-019, §FR-007]
- [X] CHK032 Are requirements defined for review when the run directory is readable but the manifest is absent? [Coverage, Spec §FR-010, §FR-019]
- [X] CHK033 Is behavior specified when review is requested with no run identifier, or with a run identifier that does not exist? [Gap, Coverage, Spec §FR-008, §FR-007]
- [X] CHK034 Are requirements defined for review of a plan large enough that per-object detail cannot reasonably be rendered in full — pagination, truncation, or an explicit exclusion? [Gap, Coverage, Spec §FR-006, §Open Design Decisions]
- [X] CHK035 Are requirements defined for review output when the operator lacks read access to part of the run directory? [Gap, Coverage, Spec §FR-007]

## Dependencies

- [X] CHK036 Is the dependency on the existing non-mutating command's current contract — its flags, its output, its existing consumers — recorded, given the review flags extend it? [Dependency, Spec §FR-008, §Clarifications AD005]
- [X] CHK037 Is the dependency of SC-005 on review output carrying operation identifiers stated in FR-006 or FR-008, rather than only implied by the success criterion? [Dependency, Traceability, Spec §SC-005, §FR-006, §FR-008]
- [X] CHK038 Is the relationship to the later command-group rework stated in requirement terms, so an implementer can tell what must remain foldable into it without behavior change? [Dependency, Spec §Out of Scope, §FR-008]

## Assumptions

- [X] CHK039 Is the assumption that review can be delivered by extending existing commands recorded together with its escalation path — a scope change requiring a new decision rather than an implementer's call? [Assumption, Spec §Assumptions, §FR-008]
- [X] CHK040 Is it stated which review requirements depend on provisional AD005, and which must be revisited if the command and flag spelling is not ratified? [Assumption, Spec §Clarifications AD005, §Open Design Decisions]
- [X] CHK041 Is the assumption recorded that standard output is capturable in every environment SC-010's scan must run in, including in-process invocation? [Assumption, Spec §SC-010, §FR-008]
- [X] CHK042 Is the assumption recorded that no secret value reaches the plan payload from the *source system's own data*, as opposed to from the sync configuration's credentials? [Assumption, Gap, Spec §FR-018, §FR-002]

## Notes

- Items are intentionally all unchecked. Marking them is a reviewer action, not an authoring action.
- Spec defects observed and recorded here rather than corrected, per this run's append-only rule:
    - **SC-005 depends on an unstated review requirement** (CHK001, CHK037). The criterion compares
  identifiers "shown at review" against those reported at apply, but neither FR-006 nor FR-008
  requires the identifier to appear in review output.
    - **FR-008 and SC-012 may state different bars** (CHK017): "no new CLI command is added" versus
  "the command set gains no new command group".
    - **The redaction rule for FR-018 is undefined** (CHK007, CHK042). The requirement states an
  outcome; nothing states what marks a value as secret, which matters because FR-002 requires full
  source payloads in the artifact.
    - **The stdout requirement's scope is ambiguous** (CHK011): it is unclear whether the existing
  command's live output moves to standard output too, which would be a further user-visible change.
    - **The review path's own failure behavior is unspecified** (CHK008, CHK031, CHK033): unknown run,
  absent artifact, and v1 plan are all specified for apply but not for review.

### Remediation applied 2026-07-26

The spec defects above were repaired in `../spec.md` by the delivery-apply remediation pass. Boxes
remain unchecked; verification is a separate pass.

- CHK001, CHK037 — FR-006 now requires per-object detail to present at least the operation
  identifier, action, destination kind and destination identity; FR-006 is added to the DBR-005
  traceability row and SC-005 names its review-side source. `[AD020]`
- CHK007, CHK042 — resolved as scope: the artifact carries mapped source field values only,
  credentials live in `settings`, and no field-level classification model is built. Recorded in
  FR-018, Assumptions and SC-010's injection point. `[AD018]`
- CHK017 — FR-008 is restated to the brief's bar, no new command *group*, noting that AD005 extends
  the existing command by choice so no command is added either. `[AD019]`
- CHK011 — the stdout requirement is scoped to the read-from-artifact mode; the live path's channel
  is unchanged. `[AD023]`
- CHK002, CHK003, CHK023, CHK029 — FR-006 fixes the summary breakdown as a count per action and a
  count per kind, requires kind narrowing, and FR-022 requires an explicit zero-operations summary;
  SC-009 gains per-case pass conditions.
- CHK008, CHK031, CHK032, CHK033 — FR-008 requires an error naming an unknown or plan-less run
  identifier and forbids presenting it as an empty plan; FR-019 binds the plan reader for review and
  apply alike. `[AD021]`
- CHK013 — SC-009's cases are produced with neither side reachable, evidencing no adapter is built.
- CHK036 — Dependencies now records the extended command's contract: the pre-existing `--run-id`
  meaning, the log-stream output channel, the configuration-bound run-directory location, and the
  pipeline lock. `[AD021]`
- CHK024, CHK041 — SC-010 names the canary injection point and how each output is captured.

### Independent verification 2026-07-26

27 of 42 items verified satisfied and marked `[X]`; 15 left unchecked. Every `[X]` was confirmed
against `../spec.md` text, and every code anchor the spec cites was re-checked against the tree
(`cli.py:31` — no `add_typer` anywhere; `cli.py:98`, `:129`, `:153`; `cache/locks.py:21-33`;
`cache/paths.py:56-59`; `utils.py:244-246`, `:256-263`). All are accurate.

- CHK004 — no requirement states whether rendered review output is human-oriented only or itself a compatibility contract, though SC-005 (spec.md:741-745) and SC-010 (:771-776) both assert properties of it and "plan summaries in the UI" is named as a consumer (:927). Spec defect.
- CHK005 — FR-008 (spec.md:528-529) still states no input or return contract for the in-process reader; "returns data rather than writing to a stream" appears only as an Assumptions note about SC-010's scan mechanics (:900-901), and no surface is named. Spec defect.
- CHK006 — whether review verifies the plan checksum, and what it does on mismatch, is still unspecified; FR-009 and FR-010 are apply-scoped by their own wording. Spec defect.
- CHK009 — no requirement obliges documenting the new review flags, although the constitution requires user-visible CLI changes to update `docs/` in the same change (`dev/constitution.md` §Documentation) and FR-015's docs clause (spec.md:604-605) is scoped to the delete-content change only. Spec defect.
- CHK010 — "reachable in-process" (spec.md:515) is still undefined between a supported public entry point and any importable function; the CLI side is pinned exactly by AD005 while the in-process side is not. Spec defect.
- CHK015 — FR-018 binds "any review output" (spec.md:616) while SC-010 enumerates only the artifact, summary and per-object output (:771-772); warnings, refusal messages and log lines fall inside the requirement and outside its criterion. Spec defect.
- CHK016 — "at any time after the run" (spec.md:513-514) is still unbounded by any retention or lifecycle statement, and no pruning exists in the tree. Spec defect (nit).
- CHK018 — the constitution's structlog-not-`print` rule (`dev/constitution.md` §Logging, enforced by `tests/test_logging.py:56-79`) is still not reconciled with FR-008's stdout requirement (spec.md:521-523); no echo mechanism is named. Spec defect.
- CHK025 — SC-012 (spec.md:780-783) names the top-level command listing and "compared as text" but not how the listing is captured, so the before-state is not a reproducible artifact. Spec defect (nit).
- CHK027 — review latency and plan-size expectations remain only in Open Design Decisions (spec.md:1020-1023); neither is quantified nor excluded inside the requirements or Out of Scope. Spec defect (nit).
- CHK030 — a `--kind` filter naming a kind with no operations, or a kind absent from the configuration, still has no specified behavior; a mistyped kind renders as empty detail, the same silent-misread hazard FR-008 closes for a mistyped run identifier. Spec defect.
- CHK033 — **claimed fixed, only half fixed.** FR-008 (spec.md:525-528) covers an unknown or plan-less run identifier, but review requested with *no* run identifier is still unspecified. Spec defect.
- CHK034 — per-object detail for a plan too large to render in full has no pagination, truncation, or explicit exclusion (spec.md:1020-1023 is a deferral note). Spec defect (nit).
- CHK035 — review output when part of the run directory is unreadable (permission or I/O failure) is still uncovered by FR-007 (spec.md:513-514). Spec defect (nit).
- CHK038 — the "foldable into a later `plan` group without behavior change" statement is still prose in Open Design Decisions (spec.md:1000-1002); no requirement states what must remain true for that fold. Spec defect (nit).

### Final verification 2026-07-26

9 of the 15 previously-unchecked items verified satisfied and marked `[X]`; 6 left unchecked.
Checklist stands at 36 / 42. No previously-checked item was found to have been invalidated by the
second edit round.

Verified satisfied:

- CHK006 — reviewing a plan that would fail apply verification is now specified: review verifies the
  plan checksum and reports the result prominently, but renders the plan regardless, and never
  mutates the run state (Edge Cases `spec.md:594-597`, AD031 `:315-319`).
- CHK009 — FR-008 now carries the documentation obligation explicitly: "Because these review flags
  are a user-visible CLI change, the same change MUST update the user documentation for the command
  they extend" (`spec.md:686-688`), which satisfies the constitution's §Documentation rule.
- CHK010 — "reachable in-process" is now pinned to a supported entry point rather than any
  importable function: "One supported entry point reads a stored plan and produces both review
  depths ... a single reader entry point is named and nothing else" (AD029, `spec.md:305-309`).
- CHK015 — FR-018's "any review output" is now bound to the same surfaces SC-010 enumerates
  (AD036, `spec.md:363-364`), which places warnings, refusal messages and log lines outside the
  requirement rather than inside it and outside its criterion.
- CHK018 — the logging standard and the stdout requirement are now visibly reconciled: output is
  "emitted through the command framework's echo facility rather than the language's built-in print"
  (FR-008, `spec.md:679-681`), with AD032 (`:320-324`) naming `typer.echo`. Verified against the
  code: the enforcement test matches only a call whose function name is the builtin `print`
  (`tests/test_logging.py:56-64`), and the CLI already uses `typer.echo` for help output
  (`infrahub_sync/cli.py:69`). The reconciliation is sound.
- CHK025 — SC-012's before-and-after listing is now a reproducible artifact: captured as `--help`
  output to a file (AD036, `spec.md:365`), over the top-level command listing SC-012 names
  (`:973-976`).
- CHK030 — a kind filter matching no operation, or naming a kind the configuration does not declare,
  is now an error naming that kind and MUST NOT be presented as empty detail (FR-006,
  `spec.md:664-666`).
- CHK033 — now fully closed. FR-008 covers the unknown or plan-less run identifier
  (`spec.md:682-684`) *and* the previously-missing case: "Requesting the read-from-artifact mode
  with **no** run identifier MUST be an error naming the required option" (`:684-685`). This closes
  the "claimed fixed, only half fixed" finding of the previous pass.
- CHK035 — FR-008 now requires an error naming the path that could not be read on a permission or
  I/O failure (`spec.md:685-686`), with the Edge Case at `:603-606` adding that it is never
  presented as an absent plan, a v1 plan, or a zero-operation plan.

Left unchecked:

- CHK004 — **remediation claim not delivered.** AD030 (`spec.md:310-314`) states that an
  output-stability contract is "recorded as an explicit Out-of-scope line". The Out of Scope section
  (`:1009-1041`) was not touched by the second round and carries no such line. Whether rendered
  review output is human-oriented only or itself a compatibility contract is still unstated, while
  SC-005 (`:934-938`) and SC-010 (`:964-969`) both assert properties of it and "plan summaries in
  the UI" is named as a consumer (`:1120`). Spec defect.
- CHK005 — **remediation claim not delivered.** FR-008 (`spec.md:688-689`) defers the in-process
  reader's contract to **FR-029**, which does not exist. AD029 (`:305-309`) says only that one
  supported entry point "reads a stored plan", "produces both review depths" and "returns data
  rather than writing to a stream"; it states no input contract, no return shape, and does not in
  fact name the surface it claims to name. Spec defect.
- CHK016 — **remediation claim not delivered.** AD030 declines retention and lifecycle and claims an
  Out-of-scope line; none was written. "At any time after the run" (`spec.md:668-669`) remains
  unbounded. Spec defect (nit).
- CHK027 — **remediation claim not delivered.** Same missing Out-of-scope line. Review latency and
  plan-size expectations remain only in Open Design Decisions (`spec.md:1213-1216`), neither
  quantified nor excluded inside the requirements. Spec defect (nit).
- CHK034 — **remediation claim not delivered.** Same missing Out-of-scope line. Per-object detail
  for a plan too large to render in full still has no pagination, truncation, or explicit exclusion.
  Spec defect (nit).
- CHK038 — untouched by the second round. The "foldable into a later `plan` group without behavior
  change" statement is still prose in Open Design Decisions (`spec.md:1193-1195`); no requirement
  states what must remain true for that fold. Spec defect (nit).

Spot-check of previously-checked items in this checklist, chosen where the second round edited
nearby text — CHK001, CHK002, CHK003, CHK011, CHK012, CHK013, CHK017, CHK019, CHK021, CHK022,
CHK031, CHK032, CHK036. All still hold. CHK011's scoping survived the FR-008 rewrite — the stdout
clause is still confined to the read-from-artifact mode and "the existing live comparison path's
output channel is unchanged" (`spec.md:681-682`). CHK017's group-only bar is intact (`:671-674`) and
still matches SC-012 (`:973-974`). CHK036's dependency record kept the anchors the relocation moved
out of FR-008 (Dependencies, `:1099-1115`), so nothing was lost. CHK021 still holds; note only that
FR-008's single-implementation clause now ends in a pointer to the non-existent FR-029.

### Final verification round 2 2026-07-26

All 6 remaining items verified satisfied and marked `[X]`. Checklist stands at **42 / 42**.

- CHK004 — Out of Scope now carries "Rendered review output as a stability or compatibility
  contract" (`spec.md:1163-1170`): the summary and per-object renderings are operator-facing text
  whose wording, field order and layout may change without that being a breaking change, and the
  contract this outcome owns is named as the plan artifact format instead. Checked against the
  brief's "Shared contracts this brief owns", which names the artifact format — manifest fields,
  per-operation record, deterministic serialization, checksum rule — and not the rendering; and
  against DB-019, which the line correctly identifies as owning its own presentation. No conflict
  with FR-006's minimum field set (`:661-664`) or SC-009's pass conditions (`:1050-1058`): those
  bind content, the exclusion frees only presentation.
- CHK005 — FR-029 (`spec.md:942-953`) states the contract the item asked for. It **accepts** the
  sync name and the run identifier locating a stored run; it **returns** both review depths FR-006
  defines, as data rather than to any output stream; the command-line mode is a thin renderer over
  it that MUST NOT re-implement reading, filtering or summarizing; and nothing beyond that single
  reader is a supported surface. This resolves FR-008's previously dangling pointer (`:691-692`).
- CHK016 — Out of Scope now carries "Artifact retention, lifecycle, and pruning"
  (`spec.md:1146-1152`). Consistent with the brief, which puts durable run/artifact storage behind
  provider interfaces in DB-005 and plan expiration out of scope alongside destination freshness
  checks and conflict policies. "At any time after the run" is now bounded by the life of the run
  directory, stated explicitly.
- CHK027 — Out of Scope now carries "Plan-volume and review-latency targets" (`spec.md:1158-1162`),
  excluded inside the requirements rather than deferred. The Open Design Decisions deferral note
  (`:1360-1364`) now cross-references it rather than standing alone.
- CHK034 — Out of Scope now carries "Pagination or truncation of per-object review output"
  (`spec.md:1153-1157`): no page size, no record limit, no elision, no continuation handle, with
  FR-006's kind narrowing named as the only supported reduction.
- CHK038 — resolved by declining rather than by asserting, which is the correct resolution. Out of
  Scope now carries "Folding the review flags into a command group" (`spec.md:1178-1185`), and the
  Open Design Decisions entry (`:1335-1342`) has had its previous claim — that the spelling "will
  later be folded into a `plan` group without changing behavior" — **removed** and replaced with an
  explicit non-assertion. That old sentence asserted a property of work this specification does not
  own; deleting it is a correction, not a gap. Consistent with the brief, which assigns the
  command-group rework to DB-004 under D002 and D026.

**Regression check on FR-029 against FR-006 and FR-008.** No contradiction found.

- FR-029's "return as data rather than writing to any output stream" (`spec.md:945-947`) and
  FR-008's "review output MUST be written to standard output" (`:679-682`) bind different things —
  the reader versus the command-line renderer — and both AD005 (`:88-91`) and AD029 (`:305-309`) say
  so. FR-008's own text now closes with "FR-029 fixes that reader's contract" (`:691-692`).
- FR-029's inputs match FR-008's configuration-bound locating rule — sync name plus run identifier
  under the cache root (`:684-686`). Consistent.
- FR-029 produces "both review depths FR-006 defines", and FR-006 (`:661-668`) defines exactly two.
  No third vocabulary introduced.
- One observation, not a contradiction: FR-029 states the reader's inputs as the sync name and the
  run identifier, while its renderer clause forbids the command line from re-implementing
  **filtering** — which implies the reader owns FR-006's kind narrowing, but no filter input is
  named among the reader's stated inputs. The obligation is derivable from the negative clause;
  naming the filter input would make it explicit.

No regression found in any previously-checked item on this checklist. In particular CHK010's
"supported entry point" reading is now carried by a requirement rather than only by AD029, and
CHK015's alignment of FR-018's "any review output" with SC-010's surfaces is unaffected by the new
material.
