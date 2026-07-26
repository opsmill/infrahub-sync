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

- [ ] CHK001 Is the run state that results from a refused apply named anywhere, given SC-004 requires the resulting run state to be asserted? [Gap, Spec §FR-009, §SC-004]
- [ ] CHK002 Is the pre-apply verification set stated exhaustively — does it include the manifest's format version, the operation count, and the presence of the operations section, or only the checksum, configuration version, and snapshot binding? [Completeness, Spec §FR-009, §FR-010, §FR-019]
- [ ] CHK003 Is the ordering of verifications specified, including which failure is reported when several hold at once and whether all failed checks are named or only the first? [Gap, Spec §FR-009, §SC-004]
- [ ] CHK004 Are requirements defined for whether an apply may be requested for a run that already reached an applied state — which User Story 3 presumes but no functional requirement permits? [Gap, Spec §FR-012, §User Story 3, §FR-009]
- [ ] CHK005 Is "before any destination write" stated as an obligation on the ordering of work (verification precedes adapter construction and connection), or only asserted as an evidence condition? [Clarity, Spec §FR-009, §SC-004]
- [ ] CHK006 Are requirements stated for refusal-message content beyond "naming the failed check" — that it identifies the run, is operator-actionable, and carries no secret value? [Completeness, Spec §FR-009, §FR-018, §FR-023]
- [ ] CHK007 Is the classification rule stated that separates conditions which refuse before any write (FR-009, FR-019, FR-023) from conditions which fail only after operations have been applied (FR-017)? [Gap, Consistency, Spec §FR-009, §FR-017, §FR-019, §FR-023]
- [ ] CHK008 Are requirements defined for what the apply path records on the run result when it refuses — whether an empty applied-identifier set is written, or nothing is? [Gap, Spec §FR-009, §FR-020]

## Requirement Clarity

- [ ] CHK009 Is "the configuration version still matches" defined as equality against a value recomputed by the same rule, including which configuration inputs the rule covers (a single file, included templates, environment substitutions)? [Clarity, Spec §FR-009, §FR-011]
- [ ] CHK010 Is "never parsed or interpreted" expressed in checkable terms, so SC-013 has a definite pass condition? [Measurability, Spec §FR-011, §SC-013]
- [ ] CHK011 Is "truncated" defined separately for the operations section and for the source snapshot, each with a stated detection rule? [Clarity, Spec §FR-010, §SC-004, §Edge Cases/Torn artifact]
- [ ] CHK012 Is "applied state" defined as a named run state with stated transitions, so "the run MUST NOT reach an applied state" is objectively checkable? [Clarity, Spec §FR-009, §FR-020]
- [ ] CHK013 Is "clear, actionable error naming the adapter" given any content bar beyond naming the adapter? [Clarity, Spec §FR-023, §Edge Cases/Missing destination write surface]
- [ ] CHK014 Is "the last operation it reported as applied" unambiguous about ordering — reported in what sequence, and against what record? [Clarity, Spec §FR-025, §FR-020]

## Requirement Consistency

- [ ] CHK015 Do User Story 2 scenario 1 (a snapshot that has been *removed*) and SC-004's five enumerated negative cases (which name snapshot-binding mismatch and truncated snapshot, but not absent snapshot) describe the same coverage set? [Consistency, Spec §User Story 2 scenario 1, §SC-004]
- [ ] CHK016 Is FR-019's v1 detection rule — the pre-existing plan file present with no new-format manifest — distinguishable from a new-format plan run that crashed before writing its manifest? [Conflict, Spec §FR-019, §FR-010, §Clarifications AD001]
- [ ] CHK017 Are FR-017 (supported operations still applied, run fails) and FR-009 ("refused before any destination write") consistent in making an unsupported operation explicitly *not* a pre-apply refusal? [Consistency, Spec §FR-017, §FR-009, §SC-007]
- [ ] CHK018 Do FR-020 (identifiers of applied operations recorded on the run result) and FR-025 (the run records the last operation reported as applied) describe one record or two, and is their relationship stated? [Consistency, Spec §FR-020, §FR-025]
- [ ] CHK019 Is FR-023's "before any write is attempted" placed relative to FR-009's verifications — is the write-surface check part of pre-apply verification or a separate later failure? [Consistency, Spec §FR-023, §FR-009]
- [ ] CHK020 Is the Out of Scope exclusion of a durable crash-surviving apply ledger consistent with FR-025's obligation that the run record the last applied operation after an interrupted apply? [Conflict, Spec §Out of Scope, §FR-025, §Edge Cases/Partial apply]
- [ ] CHK021 Is FR-012's "without recomputing the comparison" consistent with FR-009's verification work, so that verification is clearly not a recomputation? [Consistency, Spec §FR-012, §FR-009, §SC-001]

## Acceptance Criteria Quality and Measurability

- [ ] CHK022 Does SC-004 specify how "zero destination writes" is evidenced, rather than only asserting it? [Measurability, Spec §SC-004]
- [ ] CHK023 Is SC-001's "no comparison-engine diff/sync call on the apply path" stated so it can be verified without depending on internal function names that may change? [Measurability, Spec §SC-001]
- [ ] CHK024 Does SC-005 state where the apply-side identifier set is read from — the run result per FR-020 — and at what granularity the comparison is made? [Measurability, Spec §SC-005, §FR-020]
- [ ] CHK025 Does SC-011's assertion of "the message" rest on a stated message requirement, or is the message content left to the implementer? [Measurability, Spec §SC-011, §FR-019]
- [ ] CHK026 Does FR-025 have a success criterion, and is "the last operation reported as applied" observable after an abnormal termination given no durable ledger exists? [Traceability, Measurability, Spec §FR-025, §Out of Scope]
- [ ] CHK027 Does FR-020 have a success criterion of its own, or is it exercised only incidentally through SC-005? [Traceability, Spec §FR-020, §SC-005]
- [ ] CHK028 Does SC-007 state the required content of the failed-state message precisely enough to assert against ("naming the unsupported operation" — by identifier, by action, or by object)? [Measurability, Spec §SC-007, §FR-017]

## Scenario and Edge-Case Coverage

- [ ] CHK029 Are requirements defined for an apply request naming a run identifier that does not exist? [Gap, Coverage, Spec §FR-012]
- [ ] CHK030 Are requirements defined for two concurrent applies of the same run, or for a plan being read while another process writes it? [Gap, Coverage, Spec §FR-012, §FR-007]
- [ ] CHK031 Is the expected outcome specified when the destination becomes unreachable partway through an apply — is that the FR-025 partial-apply path or a distinct failure? [Coverage, Spec §FR-025, §FR-017]
- [ ] CHK032 Is behavior specified when an individual operation is rejected by the destination (a write error rather than an unsupported action) — does the run stop or continue? [Gap, Coverage, Spec §FR-017, §FR-025]
- [ ] CHK033 Is the zero-operation apply's interaction with pre-apply verification stated — is an empty plan still checksum- and snapshot-verified before succeeding as a no-op? [Coverage, Spec §FR-022, §FR-009]
- [ ] CHK034 Are requirements defined for an operations section whose line count disagrees with the manifest count in *both* directions — more lines than recorded and fewer? [Coverage, Spec §FR-010, §Edge Cases/Torn artifact]
- [ ] CHK035 Are requirements defined for a plan whose manifest is present and self-consistent but whose recorded run identifier does not match the run being applied? [Gap, Coverage, Spec §FR-004, §FR-012]
- [ ] CHK036 Is the interaction between a refused apply and a subsequent retry specified — may the same run be applied again after a refusal is corrected? [Gap, Coverage, Spec §FR-009, §FR-019]

## Dependencies

- [ ] CHK037 Is the dependency on the existing engine writing per-side snapshots and run sidecars recorded as a precondition that FR-009 and FR-010 rest on? [Dependency, Spec §Assumptions]
- [ ] CHK038 Is the dependency on a configuration-version value being obtainable at apply time — by recomputation or by carry-through — stated? [Dependency, Spec §FR-011, §Assumptions]
- [ ] CHK039 Is the boundary with the later apply-ledger outcome stated in requirement terms, so an implementer can tell what FR-020 and FR-025 must *not* build? [Dependency, Spec §Out of Scope, §FR-020, §FR-025]

## Assumptions

- [ ] CHK040 Is the assumption recorded that the configuration-version value recomputes identically at apply time, together with the consequence when the configuration is legitimately reformatted without semantic change? [Assumption, Spec §FR-011, §Assumptions]
- [ ] CHK041 Is it stated which apply-safety requirements depend on provisional AD001 — the checksum rule, the torn-artifact rule, and the v1 detection rule — and must be revisited if it is not ratified? [Assumption, Spec §Clarifications AD001, §Open Design Decisions]
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
