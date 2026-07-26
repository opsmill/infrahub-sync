# Apply Safety Requirements Checklist: Saved plan artifact and apply-exactly-what-was-reviewed

**Purpose**: Validate the quality of the *requirements* governing pre-apply verification and
refusal — checksum, configuration version, snapshot binding, torn artifacts, v1 rejection,
zero-write guarantees, run state on refusal, unsupported-operation failure, and partial apply.
These items interrogate what the specification says, not what the implementation does.

**Created**: 2026-07-26

**Feature**: [spec.md](../spec.md)

**Requirements in scope**: FR-009, FR-010, FR-012, FR-017, FR-020, FR-023, FR-025; SC-001, SC-004,
SC-005, SC-007, SC-011, SC-013; User Story 2; Edge Cases (Torn artifact, Partial apply, Missing
destination write surface); Clarifications AD001.

**Dimensions addressed**: completeness, clarity, consistency, measurability, scenarios and edge
cases, dependencies, assumptions.

**How to use**: every item is a yes/no question about the requirements documents, answerable by
reading them. Items are left unchecked deliberately — a separate reviewer marks them `[X]`.

## Requirement Completeness

- [X] CHK001 Is the run state that results from a refused apply named anywhere, given SC-004 requires the resulting run state to be asserted? [Gap, Spec §FR-009, §SC-004]
- [ ] CHK002 Is the pre-apply verification set stated exhaustively — does it include the manifest's format version, the operation count, and the presence of the operations section, or only the checksum, configuration version, and snapshot binding? [Completeness, Spec §FR-009, §FR-010, §FR-019]
- [ ] CHK003 Is the ordering of verifications specified, including which failure is reported when several hold at once and whether all failed checks are named or only the first? [Gap, Spec §FR-009, §SC-004]
- [ ] CHK004 Are requirements defined for whether an apply may be requested for a run that already reached an applied state — which User Story 3 presumes but no functional requirement permits? [Gap, Spec §FR-012, §User Story 3, §FR-009]
- [ ] CHK005 Is "before any destination write" stated as an obligation on the ordering of work (verification precedes adapter construction and connection), or only asserted as an evidence condition? [Clarity, Spec §FR-009, §SC-004]
- [ ] CHK006 Are requirements stated for refusal-message content beyond "naming the failed check" — that it identifies the run, is operator-actionable, and carries no secret value? [Completeness, Spec §FR-009, §FR-018, §FR-023]
- [ ] CHK007 Is the classification rule stated that separates conditions which refuse before any write (FR-009, FR-019, FR-023) from conditions which fail only after operations have been applied (FR-017)? [Gap, Consistency, Spec §FR-009, §FR-017, §FR-019, §FR-023]
- [ ] CHK008 Are requirements defined for what the apply path records on the run result when it refuses — whether an empty applied-identifier set is written, or nothing is? [Gap, Spec §FR-009, §FR-020]

## Requirement Clarity

- [ ] CHK009 Is "the configuration version still matches" defined as equality against a value recomputed by the same rule, including which configuration inputs the rule covers (a single file, included templates, environment substitutions)? [Clarity, Spec §FR-009, §FR-011]
- [X] CHK010 Is "never parsed or interpreted" expressed in checkable terms, so SC-013 has a definite pass condition? [Measurability, Spec §FR-011, §SC-013]
- [X] CHK011 Is "truncated" defined separately for the operations section and for the source snapshot, each with a stated detection rule? [Clarity, Spec §FR-010, §SC-004, §Edge Cases/Torn artifact]
- [X] CHK012 Is "applied state" defined as a named run state with stated transitions, so "the run MUST NOT reach an applied state" is objectively checkable? [Clarity, Spec §FR-009, §FR-020]
- [X] CHK013 Is "clear, actionable error naming the adapter" given any content bar beyond naming the adapter? [Clarity, Spec §FR-023, §Edge Cases/Missing destination write surface]
- [X] CHK014 Is "the last operation it reported as applied" unambiguous about ordering — reported in what sequence, and against what record? [Clarity, Spec §FR-025, §FR-020]

## Requirement Consistency

- [ ] CHK015 Do User Story 2 scenario 1 (a snapshot that has been *removed*) and SC-004's five enumerated negative cases (which name snapshot-binding mismatch and truncated snapshot, but not absent snapshot) describe the same coverage set? [Consistency, Spec §User Story 2 scenario 1, §SC-004]
- [X] CHK016 Is FR-019's v1 detection rule — the pre-existing plan file present with no new-format manifest — distinguishable from a new-format plan run that crashed before writing its manifest? [Conflict, Spec §FR-019, §FR-010, §Clarifications AD001]
- [X] CHK017 Are FR-017 (supported operations still applied, run fails) and FR-009 ("refused before any destination write") consistent in making an unsupported operation explicitly *not* a pre-apply refusal? [Consistency, Spec §FR-017, §FR-009, §SC-007]
- [ ] CHK018 Do FR-020 (identifiers of applied operations recorded on the run result) and FR-025 (the run records the last operation reported as applied) describe one record or two, and is their relationship stated? [Consistency, Spec §FR-020, §FR-025]
- [ ] CHK019 Is FR-023's "before any write is attempted" placed relative to FR-009's verifications — is the write-surface check part of pre-apply verification or a separate later failure? [Consistency, Spec §FR-023, §FR-009]
- [X] CHK020 Is the Out of Scope exclusion of a durable crash-surviving apply ledger consistent with FR-025's obligation that the run record the last applied operation after an interrupted apply? [Conflict, Spec §Out of Scope, §FR-025, §Edge Cases/Partial apply]
- [X] CHK021 Is FR-012's "without recomputing the comparison" consistent with FR-009's verification work, so that verification is clearly not a recomputation? [Consistency, Spec §FR-012, §FR-009, §SC-001]

## Acceptance Criteria Quality and Measurability

- [X] CHK022 Does SC-004 specify how "zero destination writes" is evidenced, rather than only asserting it? [Measurability, Spec §SC-004]
- [X] CHK023 Is SC-001's "no comparison-engine diff/sync call on the apply path" stated so it can be verified without depending on internal function names that may change? [Measurability, Spec §SC-001]
- [X] CHK024 Does SC-005 state where the apply-side identifier set is read from — the run result per FR-020 — and at what granularity the comparison is made? [Measurability, Spec §SC-005, §FR-020]
- [X] CHK025 Does SC-011's assertion of "the message" rest on a stated message requirement, or is the message content left to the implementer? [Measurability, Spec §SC-011, §FR-019]
- [X] CHK026 Does FR-025 have a success criterion, and is "the last operation reported as applied" observable after an abnormal termination given no durable ledger exists? [Traceability, Measurability, Spec §FR-025, §Out of Scope]
- [X] CHK027 Does FR-020 have a success criterion of its own, or is it exercised only incidentally through SC-005? [Traceability, Spec §FR-020, §SC-005]
- [X] CHK028 Does SC-007 state the required content of the failed-state message precisely enough to assert against ("naming the unsupported operation" — by identifier, by action, or by object)? [Measurability, Spec §SC-007, §FR-017]

## Scenario and Edge-Case Coverage

- [ ] CHK029 Are requirements defined for an apply request naming a run identifier that does not exist? [Gap, Coverage, Spec §FR-012]
- [X] CHK030 Are requirements defined for two concurrent applies of the same run, or for a plan being read while another process writes it? [Gap, Coverage, Spec §FR-012, §FR-007]
- [ ] CHK031 Is the expected outcome specified when the destination becomes unreachable partway through an apply — is that the FR-025 partial-apply path or a distinct failure? [Coverage, Spec §FR-025, §FR-017]
- [ ] CHK032 Is behavior specified when an individual operation is rejected by the destination (a write error rather than an unsupported action) — does the run stop or continue? [Gap, Coverage, Spec §FR-017, §FR-025]
- [ ] CHK033 Is the zero-operation apply's interaction with pre-apply verification stated — is an empty plan still checksum- and snapshot-verified before succeeding as a no-op? [Coverage, Spec §FR-022, §FR-009]
- [X] CHK034 Are requirements defined for an operations section whose line count disagrees with the manifest count in *both* directions — more lines than recorded and fewer? [Coverage, Spec §FR-010, §Edge Cases/Torn artifact]
- [X] CHK035 Are requirements defined for a plan whose manifest is present and self-consistent but whose recorded run identifier does not match the run being applied? [Gap, Coverage, Spec §FR-004, §FR-012]
- [ ] CHK036 Is the interaction between a refused apply and a subsequent retry specified — may the same run be applied again after a refusal is corrected? [Gap, Coverage, Spec §FR-009, §FR-019]

## Dependencies

- [X] CHK037 Is the dependency on the existing engine writing per-side snapshots and run sidecars recorded as a precondition that FR-009 and FR-010 rest on? [Dependency, Spec §Assumptions]
- [X] CHK038 Is the dependency on a configuration-version value being obtainable at apply time — by recomputation or by carry-through — stated? [Dependency, Spec §FR-011, §Assumptions]
- [X] CHK039 Is the boundary with the later apply-ledger outcome stated in requirement terms, so an implementer can tell what FR-020 and FR-025 must *not* build? [Dependency, Spec §Out of Scope, §FR-020, §FR-025]

## Assumptions

- [X] CHK040 Is the assumption recorded that the configuration-version value recomputes identically at apply time, together with the consequence when the configuration is legitimately reformatted without semantic change? [Assumption, Spec §FR-011, §Assumptions]
- [X] CHK041 Is it stated which apply-safety requirements depend on provisional AD001 — the checksum rule, the torn-artifact rule, and the v1 detection rule — and must be revisited if it is not ratified? [Assumption, Spec §Clarifications AD001, §Open Design Decisions]
- [ ] CHK042 Is the assumption recorded that a refusal can be made before any adapter is constructed, given the constitution treats the mutating path as the deliberate one and the non-mutating path as always safe? [Assumption, Spec §FR-009; Constitution §I]

## Notes

- Items are intentionally all unchecked. Marking them is a reviewer action, not an authoring action.
- Spec defects observed and recorded here rather than corrected, per this run's append-only rule:
    - **The refused-run state is never named** (CHK001, CHK012). FR-009 says the run must not reach an
  applied state and SC-004 requires the resulting run state to be asserted, but no state name or
  transition is specified, so the criterion has no definite pass condition.
    - **v1 detection collides with a crashed new-format plan write** (CHK016). Both present as the
  pre-existing plan file with no new manifest, so the two conditions are not separable by the
  stated rule.
    - **FR-025 and the ledger exclusion pull against each other** (CHK020, CHK026). The run must record
  the last operation reported as applied after an interrupted apply, while a durable
  crash-surviving record is explicitly out of scope.
    - **SC-004's five negative cases and User Story 2 scenario 1 enumerate different sets** (CHK015):
  the story names a removed snapshot; the criterion names a binding mismatch and a truncated
  snapshot.
    - **Destination-side write failures are unaddressed** (CHK032): FR-017 covers unsupported
  operations and FR-025 covers stopping partway, but neither covers an operation the destination
  rejects.

### Remediation applied 2026-07-26

The spec defects above were repaired in `../spec.md` by the delivery-apply remediation pass. Boxes
remain unchecked; verification is a separate pass.

- CHK001, CHK012 — FR-009, SC-004, SC-007 and Key Entities/Run now name the existing run-state
  vocabulary: a refused apply records `failed`, "an applied state" means `status: applied`, and the
  pre-existing schema-subhash refusal path must record `failed` too. `[AD010]`
- CHK011 — closed by the `source_snapshot` manifest field in FR-004 and FR-010. `[AD008]`
- CHK020, CHK026, CHK039 — FR-025 now scopes "stops partway" to in-process termination with a
  reported error, marks the record best-effort and explicitly not crash-surviving, and carries the
  ledger scope boundary; SC-003 states its crash windows are measured destination-side. `[AD011]`
- CHK035 — FR-009 gains a fourth check on the manifest's run identifier, with SC-015 and User Story
  2 scenario 6 as its criterion. `[AD012]`
- CHK038 — FR-011 now names the apply-side supplier: recomputed by the same default rule, or
  compared verbatim when an in-process caller supplies one. `[AD013]`
- CHK016 — v1 versus torn is made disjoint by manifest-last writing in FR-019. `[AD014]`
- CHK029 — FR-008 forbids creating a run directory on the review path and requires an error naming
  an unknown or plan-less run identifier. `[AD021]`
- CHK030 — the pipeline lock is recorded in Dependencies and the review path is exempted. `[AD021]`
- CHK027 — SC-005's evidence now reads the apply-side identifiers from the FR-020 record.

### Independent verification 2026-07-26

25 of 42 items verified satisfied and marked `[X]`; 17 left unchecked. Every `[X]` was confirmed
against `../spec.md` text, and every code anchor the spec cites was re-checked against the tree.

- CHK002 — FR-009's set is closed at four checks (spec.md:531-536) and none verifies the manifest's declared format version. Spec defect.
- CHK003 — no evaluation order for the four checks, and "naming the failed check" (spec.md:536) still does not say whether all failures or only the first are named. Spec defect.
- CHK004 — no requirement permits re-applying a run that already reached `status: applied`, which User Story 3 (spec.md:344-368) and SC-002 both presume. Spec defect.
- CHK005 — "Before any destination write" (spec.md:531) is still not an obligation over adapter construction or destination connection, unlike FR-008's explicit "MUST NOT construct an adapter" (:519). Spec defect.
- CHK006 — no content bar for refusal messages beyond naming the failed check; FR-018 (spec.md:616-620) binds the artifact and review output, not error output. Spec defect.
- CHK007 — no general rule classifies which conditions refuse before any write versus which fail during execution; FR-017 (spec.md:613-615) settles only the unsupported-operation case. Spec defect.
- CHK008 — what the run result records on refusal (an empty applied-identifier set versus nothing) is still unstated in FR-020 (spec.md:633-635). Spec defect.
- CHK009 — FR-011 now names the apply-side recomputation rule (spec.md:555-558) but still does not say which configuration inputs the rule covers (single file, includes, environment substitution). Spec defect, partially fixed.
- CHK015 — SC-004's five enumerated negative cases (spec.md:736-738) still omit the *absent* snapshot that User Story 2 scenario 1 names (:321-324); neither a sixth case nor the "absent, truncated, or mismatched" restatement was applied. Spec defect, brief-originated (DBA-004 enumerates the same five).
- CHK018 — FR-020 (spec.md:633-635) and FR-025 (:661-667) both record on the run result, but nothing states whether the last-applied pointer is the final element of the FR-020 set or a separate field. Spec defect.
- CHK019 — FR-023's "before any write is attempted" (spec.md:649-650) is still not placed relative to FR-009's four checks. Spec defect.
- CHK029 — **claimed fixed, not fixed for apply.** The remediation note above cites FR-008, which is the *review* path (spec.md:525-528, AD021 at :212-215 is explicitly scoped to `--from-plan` mode). No requirement covers an apply naming a run identifier that does not exist, and `utils.py:244-246` still creates the directory unconditionally. Spec defect.
- CHK031 — a mid-apply transport failure is still classified nowhere explicitly; FR-025's "terminates in-process with a reported error" (spec.md:661-662) covers it only by inference. Brief-level gap (the brief's Edge cases section carries no transport-failure case).
- CHK032 — an operation the destination rejects (a write error, not an unsupported action) still has no stop-or-continue policy. Brief-level gap (the brief's Edge cases section carries no apply-time write-error policy).
- CHK033 — nothing states that pre-apply verification is unconditional and independent of the operation count, so an empty plan with a broken checksum remains unresolved between FR-009 (spec.md:531) and FR-022 (:643-646). Spec defect.
- CHK036 — retry after a corrected refusal is still unstated; the run-state fix landed but no clause says a refusal is not terminal for the run identifier. Spec defect (nit).
- CHK042 — no assumption records that a refusal can be made before any adapter is constructed; the CHK005 clause it depends on was not added, and the current call order (`utils.py:183-235` before `cli.py:322-340`) contradicts it. Spec defect.
