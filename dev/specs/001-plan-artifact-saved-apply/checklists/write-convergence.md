# Write Surface and Convergence Requirements Checklist: Saved plan artifact and apply-exactly-what-was-reviewed

**Purpose**: Validate the quality of the *requirements* governing the destination write surface and
convergence — convergent create and update on the Infrahub adapter, apply-time relationship peer
resolution, deletes recorded but never executed, crash-window behavior, and the non-unique-identifier
warning. These items interrogate what the specification says, not what the implementation does.

**Created**: 2026-07-26

**Feature**: [spec.md](../spec.md)

**Requirements in scope**: FR-013, FR-014, FR-015, FR-016, FR-024; SC-002, SC-003, SC-007, SC-008;
User Stories 3, 4, 5; Edge Cases (Non-unique destination identifier, Partial apply, Recorded deletes
change existing output); Clarifications AD003, AD004.

**Dimensions addressed**: completeness, clarity, consistency, measurability, scenarios and edge
cases, dependencies, assumptions.

**How to use**: every item is a yes/no question about the requirements documents, answerable by
reading them. Items are left unchecked deliberately — a separate reviewer marks them `[X]`.

## Requirement Completeness

- [ ] CHK001 Is it stated whether convergent planned writes are required of any destination adapter other than Infrahub, given FR-013 names only the Infrahub adapter while SC-002 is worded adapter-agnostically? [Completeness, Spec §FR-013, §SC-002, §FR-023]
- [ ] CHK002 Are requirements defined for what a planned update must do with destination attributes that are present in the destination but absent from the plan payload — leave, clear, or unspecified? [Gap, Spec §FR-002, §FR-013]
- [ ] CHK003 Are requirements defined for the semantics of a cardinality-many relationship write — does the plan's peer list replace the destination's peer set or add to it? [Gap, Spec §FR-014, §SC-008, §Clarifications AD003]
- [ ] CHK004 Is the peer-resolution miss path complete — what happens when the destination query for a peer identity returns nothing, and what happens when it returns more than one match? [Gap, Spec §FR-014]
- [ ] CHK005 Is tier assignment specified sufficiently to underwrite FR-014's guarantee that a peer is written before anything referring to it, including self-references and cycles? [Gap, Spec §FR-014, §FR-002]
- [ ] CHK006 Is the consequence of the FR-024 warning stated — does the plan run still succeed, and is the warning recorded in the artifact or only emitted? [Clarity, Spec §FR-024, §Edge Cases/Non-unique destination identifier]
- [ ] CHK007 Is the FR-024 warning's output destination specified, so it is observable and cannot leak into the review output that SC-010 scans? [Gap, Spec §FR-024, §FR-008, §FR-018]
- [ ] CHK008 Are requirements defined for what the apply reports per operation on success — enough for SC-005's identifier comparison and SC-003's per-class matrix to be built? [Completeness, Spec §FR-020, §SC-003, §SC-005]
- [ ] CHK009 Is it stated whether the recorded-delete change affects only the plan artifact, or also every existing renderer of plan content, and is the fixture/documentation update obligation scoped? [Completeness, Spec §FR-015, §Edge Cases/Recorded deletes change existing output]

## Requirement Clarity

- [ ] CHK010 Is "convergently" defined by an observable postcondition (the same object, the same identity, no duplicate) rather than by a mechanism? [Clarity, Spec §FR-013, §SC-002]
- [ ] CHK011 Is "no duplicate" defined against a stated notion of identity — the destination's unique constraint, or the plan's destination identity? [Clarity, Spec §SC-002, §Assumptions]
- [ ] CHK012 Is "with no loaded comparison store" expressed as an externally checkable condition rather than an internal process state? [Measurability, Spec §FR-014, §SC-008]
- [ ] CHK013 Is "memoized within one apply" clear about the cache's lifetime and invalidation — in particular whether a failed write may leave a resolution cached? [Clarity, Spec §FR-014, §Clarifications AD003]
- [ ] CHK014 Is "takes an operation's own result as the resolution for later operations" clear for an update that matched an existing object versus one that created it? [Clarity, Spec §FR-014]
- [ ] CHK015 Is "relationship operations" used consistently to mean one thing — a distinct operation class, or relationship fields carried on create/update operations? [Ambiguity, Spec §FR-014, §FR-002, §SC-003]

## Requirement Consistency

- [ ] CHK016 Are FR-015 (deletes materialized only into plan records) and FR-016 (a delete is never applied) stated so that neither depends on a project's configuration to hold? [Consistency, Spec §FR-015, §FR-016, §Clarifications AD004]
- [ ] CHK017 Is FR-015's derivation of deletes from "the destination-only identities in the loaded destination state" consistent with any case where the destination load is partial, filtered, or paginated short? [Coverage, Gap, Spec §FR-015]
- [ ] CHK018 Is SC-003's enumeration of create, update, and relationship as *write classes* consistent with FR-002's model of relationships as fields of an operation? [Consistency, Spec §SC-003, §FR-002]
- [ ] CHK019 Is FR-016's scope boundary ("the existing write path's behavior under configured comparison flags is unchanged") consistent with FR-015's change to plan content for a mode that both plans and writes in one run? [Consistency, Spec §FR-015, §FR-016]
- [ ] CHK020 Are User Story 3's convergence obligation and FR-013's adapter-scoped obligation consistent about what must converge — the adapter surface, or the whole apply path including relationships? [Consistency, Spec §User Story 3, §FR-013, §FR-014]
- [ ] CHK021 Is the Out of Scope exclusion of batched destination writes consistent with FR-026's promise that batching remains possible without a plan-format change? [Dependency, Consistency, Spec §Out of Scope, §FR-026]

## Acceptance Criteria Quality and Measurability

- [ ] CHK022 Is SC-003's "clean-single-run counts" defined against a stated baseline — the destination state after one uninterrupted apply of the same plan? [Measurability, Spec §SC-003]
- [ ] CHK023 Are the two crash windows specified precisely enough to inject deterministically, given "before it is recorded" refers to a record the Out of Scope section says is not durable? [Measurability, Conflict, Spec §SC-003, §Out of Scope, §FR-020]
- [ ] CHK024 Does SC-008 specify which relationship attributes are compared and how peer ordering is treated in the comparison? [Measurability, Spec §SC-008, §Clarifications AD003]
- [ ] CHK025 Does FR-013 have a success criterion that exercises the adapter surface itself, distinct from SC-002 which measures the end-to-end apply path? [Traceability, Spec §FR-013, §SC-002]
- [ ] CHK026 Does FR-024 have any success criterion or acceptance scenario in the spec? [Traceability, Gap, Spec §FR-024, §Requirements Traceability]
- [ ] CHK027 Is SC-007's "does not delete from the destination" measurable as stated — are the object counts compared before and after defined over which kinds? [Measurability, Spec §SC-007, §FR-016]
- [ ] CHK028 Is SC-002's "same object identities" comparison defined over an enumerable set (which kinds, which attributes) rather than left open? [Measurability, Spec §SC-002]

## Scenario and Edge-Case Coverage

- [ ] CHK029 Are requirements defined for a relationship whose peer exists in the plan only as a recorded delete, which will never be applied? [Gap, Coverage, Spec §FR-014, §FR-016]
- [ ] CHK030 Are requirements defined for a plan containing only delete operations — does the apply perform no writes and still end in a failed state? [Coverage, Spec §FR-017, §SC-007, §FR-022]
- [ ] CHK031 Is the crash-window expectation stated for relationship writes specifically, where a crash may leave an object created but its peers unlinked? [Coverage, Spec §SC-003, §FR-014]
- [ ] CHK032 Are requirements defined for a peer identity that resolves to multiple destination objects because the unique constraint is missing — the FR-024 condition surfacing at apply time rather than plan time? [Gap, Coverage, Spec §FR-024, §FR-014]
- [ ] CHK033 Is behavior specified when a planned update's target no longer exists in the destination at apply time, given destination freshness checks are out of scope? [Gap, Coverage, Spec §FR-012, §Out of Scope]
- [ ] CHK034 Are requirements defined for a planned create whose object already exists in the destination with a different payload — is that convergence, an update, or a conflict? [Gap, Coverage, Spec §FR-013, §SC-002]
- [ ] CHK035 Are requirements defined for a relationship reference whose peer kind is not part of the plan or the configuration at all? [Gap, Coverage, Spec §FR-014, §FR-002]

## Dependencies

- [ ] CHK036 Is the dependency on the Infrahub adapter's existing identifier-keyed converging write path recorded with the evidence it rests on and the impact if it does not converge? [Dependency, Assumption, Spec §Assumptions, §FR-013]
- [ ] CHK037 Is the dependency on dependency tiers existing — whether produced by this outcome or already present — stated rather than presumed by FR-014? [Dependency, Gap, Spec §FR-002, §FR-014, §Assumptions]
- [ ] CHK038 Is the boundary with the later load-path reference-scan replacement stated in requirement terms, so an implementer can tell which resolution path FR-014 must *not* touch? [Dependency, Spec §Out of Scope, §FR-014]

## Assumptions

- [ ] CHK039 Is the unique-constraint assumption scoped — every kind in the qualified configuration, or only the kinds a given plan touches? [Assumption, Spec §Assumptions, §FR-024]
- [ ] CHK040 Is it stated which convergence requirements depend on provisional AD003 and AD004, and which must be revisited if either is not ratified? [Assumption, Spec §Clarifications AD003, AD004, §Open Design Decisions]
- [ ] CHK041 Is the assumption recorded that the qualified NetBox → Infrahub configuration contains at least one relationship-bearing kind adequate to evidence SC-008? [Assumption, Spec §Assumptions, §SC-008]
- [ ] CHK042 Is the deferral of the FR-024 detection mechanism recorded with a statement of what remains fixed (the requirement and the warning's content) versus open (how detection happens)? [Assumption, Spec §Open Design Decisions, §FR-024]

## Notes

- Items are intentionally all unchecked. Marking them is a reviewer action, not an authoring action.
- Spec defects observed and recorded here rather than corrected, per this run's append-only rule:
    - **Update payload semantics are undefined** (CHK002). Nothing says what happens to destination
  attributes absent from the plan payload, which decides whether an apply is additive or
  authoritative.
    - **Cardinality-many relationship semantics are undefined** (CHK003): replace-set versus add-to-set
  changes the destination outcome that SC-008 compares against.
    - **The crash-window criterion references a record the spec excludes** (CHK023). SC-003's "after a
  write commits but before it is recorded" presumes a per-operation record, while a durable
  crash-surviving ledger is out of scope.
    - **FR-024 has no acceptance criterion** (CHK026), and neither the warning's effect on the plan run
  nor its output destination is specified (CHK006, CHK007).
    - **Peer-resolution ambiguity and multiplicity are unhandled** (CHK004, CHK032): a miss that finds
  zero peers and a miss that finds several have no stated behavior.

### Remediation applied 2026-07-26

The spec defects above were repaired in `../spec.md` by the delivery-apply remediation pass. Boxes
remain unchecked; verification is a separate pass.

- CHK036, CHK011, CHK002, CHK003 — FR-013 and the Assumptions row are corrected: the existing
  convergent upsert is create-path-only and HFID-keyed, the existing update path is `local_id`-keyed
  and unusable from a saved plan, planned creates and updates both route through the upsert,
  cardinality-many relationships are replace-set, and an update payload is authoritative for the
  mapped fields it carries. SC-002's "same identity" is defined and SC-008 compares unordered sets of
  (peer kind, peer identity). `[AD015]` `[AD017]`
- CHK004, CHK032 — FR-014 and SC-016 add the zero-match and multi-match refusal arms; neither is
  ever a silent skip. `[AD016]`
- CHK005 — FR-014's tier guarantee is qualified to references in the computed dependency graph, with
  self-references, cycle-dropped optional edges and explicit `order:` named. `[AD022]`
- CHK015, CHK018 — the action vocabulary is closed and SC-003's third write class restated.
  `[AD009]`
- CHK023 — SC-003's crash windows are restated destination-side and FR-025 is scoped in-process.
  `[AD011]`
- CHK026 — FR-024 is restated on the human-friendly ID and given SC-014 plus traceability rows.
  `[AD017]`
- CHK022, CHK027, CHK028, CHK024 — SC-002, SC-003, SC-007 and SC-008 now state their comparison
  scope and baseline.
- CHK037, CHK041 — the tier computation and the qualified configuration's relationship cardinalities
  are recorded in Assumptions.
- CHK017 — subsequently decided as AD024 and applied. FR-015 now derives deletes only when the
  destination side ran a full extract; when it did not, no deletes are derived and the manifest
  discloses that they were not computed. The incremental hydrate path replays the prior run's
  snapshot plus changed-since rows, so an out-of-band destination delete would otherwise surface as a
  phantom delete and force a spurious failed apply under SC-007. Criterion SC-017.
