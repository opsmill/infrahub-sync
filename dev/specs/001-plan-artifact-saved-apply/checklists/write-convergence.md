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

- [X] CHK001 Is it stated whether convergent planned writes are required of any destination adapter other than Infrahub, given FR-013 names only the Infrahub adapter while SC-002 is worded adapter-agnostically? [Completeness, Spec §FR-013, §SC-002, §FR-023]
- [X] CHK002 Are requirements defined for what a planned update must do with destination attributes that are present in the destination but absent from the plan payload — leave, clear, or unspecified? [Gap, Spec §FR-002, §FR-013]
- [X] CHK003 Are requirements defined for the semantics of a cardinality-many relationship write — does the plan's peer list replace the destination's peer set or add to it? [Gap, Spec §FR-014, §SC-008, §Clarifications AD003]
- [X] CHK004 Is the peer-resolution miss path complete — what happens when the destination query for a peer identity returns nothing, and what happens when it returns more than one match? [Gap, Spec §FR-014]
- [X] CHK005 Is tier assignment specified sufficiently to underwrite FR-014's guarantee that a peer is written before anything referring to it, including self-references and cycles? [Gap, Spec §FR-014, §FR-002]
- [X] CHK006 Is the consequence of the FR-024 warning stated — does the plan run still succeed, and is the warning recorded in the artifact or only emitted? [Clarity, Spec §FR-024, §Edge Cases/Non-unique destination identifier]
- [ ] CHK007 Is the FR-024 warning's output destination specified, so it is observable and cannot leak into the review output that SC-010 scans? [Gap, Spec §FR-024, §FR-008, §FR-018]
- [X] CHK008 Are requirements defined for what the apply reports per operation on success — enough for SC-005's identifier comparison and SC-003's per-class matrix to be built? [Completeness, Spec §FR-020, §SC-003, §SC-005]
- [X] CHK009 Is it stated whether the recorded-delete change affects only the plan artifact, or also every existing renderer of plan content, and is the fixture/documentation update obligation scoped? [Completeness, Spec §FR-015, §Edge Cases/Recorded deletes change existing output]

## Requirement Clarity

- [X] CHK010 Is "convergently" defined by an observable postcondition (the same object, the same identity, no duplicate) rather than by a mechanism? [Clarity, Spec §FR-013, §SC-002]
- [X] CHK011 Is "no duplicate" defined against a stated notion of identity — the destination's unique constraint, or the plan's destination identity? [Clarity, Spec §SC-002, §Assumptions]
- [X] CHK012 Is "with no loaded comparison store" expressed as an externally checkable condition rather than an internal process state? [Measurability, Spec §FR-014, §SC-008]
- [X] CHK013 Is "memoized within one apply" clear about the cache's lifetime and invalidation — in particular whether a failed write may leave a resolution cached? [Clarity, Spec §FR-014, §Clarifications AD003]
- [X] CHK014 Is "takes an operation's own result as the resolution for later operations" clear for an update that matched an existing object versus one that created it? [Clarity, Spec §FR-014]
- [X] CHK015 Is "relationship operations" used consistently to mean one thing — a distinct operation class, or relationship fields carried on create/update operations? [Ambiguity, Spec §FR-014, §FR-002, §SC-003]

## Requirement Consistency

- [X] CHK016 Are FR-015 (deletes materialized only into plan records) and FR-016 (a delete is never applied) stated so that neither depends on a project's configuration to hold? [Consistency, Spec §FR-015, §FR-016, §Clarifications AD004]
- [X] CHK017 Is FR-015's derivation of deletes from "the destination-only identities in the loaded destination state" consistent with any case where the destination load is partial, filtered, or paginated short? [Coverage, Gap, Spec §FR-015]
- [X] CHK018 Is SC-003's enumeration of create, update, and relationship as *write classes* consistent with FR-002's model of relationships as fields of an operation? [Consistency, Spec §SC-003, §FR-002]
- [X] CHK019 Is FR-016's scope boundary ("the existing write path's behavior under configured comparison flags is unchanged") consistent with FR-015's change to plan content for a mode that both plans and writes in one run? [Consistency, Spec §FR-015, §FR-016]
- [X] CHK020 Are User Story 3's convergence obligation and FR-013's adapter-scoped obligation consistent about what must converge — the adapter surface, or the whole apply path including relationships? [Consistency, Spec §User Story 3, §FR-013, §FR-014]
- [X] CHK021 Is the Out of Scope exclusion of batched destination writes consistent with FR-026's promise that batching remains possible without a plan-format change? [Dependency, Consistency, Spec §Out of Scope, §FR-026]

## Acceptance Criteria Quality and Measurability

- [X] CHK022 Is SC-003's "clean-single-run counts" defined against a stated baseline — the destination state after one uninterrupted apply of the same plan? [Measurability, Spec §SC-003]
- [X] CHK023 Are the two crash windows specified precisely enough to inject deterministically, given "before it is recorded" refers to a record the Out of Scope section says is not durable? [Measurability, Conflict, Spec §SC-003, §Out of Scope, §FR-020]
- [X] CHK024 Does SC-008 specify which relationship attributes are compared and how peer ordering is treated in the comparison? [Measurability, Spec §SC-008, §Clarifications AD003]
- [X] CHK025 Does FR-013 have a success criterion that exercises the adapter surface itself, distinct from SC-002 which measures the end-to-end apply path? [Traceability, Spec §FR-013, §SC-002]
- [X] CHK026 Does FR-024 have any success criterion or acceptance scenario in the spec? [Traceability, Gap, Spec §FR-024, §Requirements Traceability]
- [X] CHK027 Is SC-007's "does not delete from the destination" measurable as stated — are the object counts compared before and after defined over which kinds? [Measurability, Spec §SC-007, §FR-016]
- [X] CHK028 Is SC-002's "same object identities" comparison defined over an enumerable set (which kinds, which attributes) rather than left open? [Measurability, Spec §SC-002]

## Scenario and Edge-Case Coverage

- [X] CHK029 Are requirements defined for a relationship whose peer exists in the plan only as a recorded delete, which will never be applied? [Gap, Coverage, Spec §FR-014, §FR-016]
- [X] CHK030 Are requirements defined for a plan containing only delete operations — does the apply perform no writes and still end in a failed state? [Coverage, Spec §FR-017, §SC-007, §FR-022]
- [X] CHK031 Is the crash-window expectation stated for relationship writes specifically, where a crash may leave an object created but its peers unlinked? [Coverage, Spec §SC-003, §FR-014]
- [X] CHK032 Are requirements defined for a peer identity that resolves to multiple destination objects because the unique constraint is missing — the FR-024 condition surfacing at apply time rather than plan time? [Gap, Coverage, Spec §FR-024, §FR-014]
- [X] CHK033 Is behavior specified when a planned update's target no longer exists in the destination at apply time, given destination freshness checks are out of scope? [Gap, Coverage, Spec §FR-012, §Out of Scope]
- [X] CHK034 Are requirements defined for a planned create whose object already exists in the destination with a different payload — is that convergence, an update, or a conflict? [Gap, Coverage, Spec §FR-013, §SC-002]
- [X] CHK035 Are requirements defined for a relationship reference whose peer kind is not part of the plan or the configuration at all? [Gap, Coverage, Spec §FR-014, §FR-002]

## Dependencies

- [X] CHK036 Is the dependency on the Infrahub adapter's existing identifier-keyed converging write path recorded with the evidence it rests on and the impact if it does not converge? [Dependency, Assumption, Spec §Assumptions, §FR-013]
- [X] CHK037 Is the dependency on dependency tiers existing — whether produced by this outcome or already present — stated rather than presumed by FR-014? [Dependency, Gap, Spec §FR-002, §FR-014, §Assumptions]
- [X] CHK038 Is the boundary with the later load-path reference-scan replacement stated in requirement terms, so an implementer can tell which resolution path FR-014 must *not* touch? [Dependency, Spec §Out of Scope, §FR-014]

## Assumptions

- [X] CHK039 Is the unique-constraint assumption scoped — every kind in the qualified configuration, or only the kinds a given plan touches? [Assumption, Spec §Assumptions, §FR-024]
- [X] CHK040 Is it stated which convergence requirements depend on provisional AD003 and AD004, and which must be revisited if either is not ratified? [Assumption, Spec §Clarifications AD003, AD004, §Open Design Decisions]
- [X] CHK041 Is the assumption recorded that the qualified NetBox → Infrahub configuration contains at least one relationship-bearing kind adequate to evidence SC-008? [Assumption, Spec §Assumptions, §SC-008]
- [X] CHK042 Is the deferral of the FR-024 detection mechanism recorded with a statement of what remains fixed (the requirement and the warning's content) versus open (how detection happens)? [Assumption, Spec §Open Design Decisions, §FR-024]

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

### Independent verification 2026-07-26

35 of 42 items verified satisfied and marked `[X]`; 7 left unchecked. Every `[X]` was confirmed
against `../spec.md` text, and every code anchor the spec cites was re-checked against the tree —
including the corrected FR-013 / Assumptions claims about the create-path upsert
(`adapters/infrahub.py:611-612`), the `local_id`-keyed update path (`:622`, `:510`), the replace-set
relationship write (`:166-175`) and the HFID key (`infrahub_sdk/node/node.py:295-298`, `:128-138`).
All are accurate.

- CHK006 — FR-024 (spec.md:654-660) and SC-014 (:791-796) now state that the plan run still succeeds, but neither says whether the warning is recorded in the plan artifact or emitted only; FR-004's manifest field set does not include it. Spec defect, partially fixed.
- CHK007 — the FR-024 warning's output stream is still unspecified, so it is undecided whether it lands in the log stream or in the stdout surface SC-010 scans (spec.md:771-776). Spec defect.
- CHK013 — FR-014's memoization (spec.md:575-578) and AD003 (:69-72) still do not say whether a failed write or a failed lookup may leave a resolution cached; negative caching is unaddressed. Spec defect.
- CHK019 — nothing states whether a `sync`-mode run (which plans and writes in one run, `potenda/__init__.py:458-472`) records deletes in its plan and, if so, that the recorded-versus-written divergence is intended; FR-017's "at apply time" (spec.md:613) leaves it implicit. Spec defect.
- CHK025 — FR-013 (spec.md:563-573) still has no criterion exercising the adapter's planned-write surface directly, and no statement that it is verified through SC-002 only. Spec defect.
- CHK033 — a planned update whose target no longer exists at apply is still unspecified; FR-013 routes updates through the upsert, which creates on no match (`infrahub_sdk/node/node.py:295-298`), so a reviewed `update` may silently become a create and nothing says so. Spec defect (the evaluator's sharpest code-level finding).
- CHK034 — a planned create whose destination identity already exists with a different payload is still unspecified: convergence, implicit update, or conflict. Spec defect / brief-level product ambiguity (conflict policies are out of scope, spec.md:838, which makes overwrite an inference rather than a requirement).

### Final verification 2026-07-26

6 of the 7 previously-unchecked items verified satisfied and marked `[X]`; 1 left unchecked.
Checklist stands at 41 / 42. No previously-checked item was found to have been invalidated by the
second edit round.

Verified satisfied:

- CHK006 — both halves are now answered. The plan run still succeeds (FR-024, `spec.md:851-852`;
  SC-014, `:984-989`), and the warning "is emitted only and is not a manifest field, so it stays
  outside `plan_checksum` and SC-006" (AD036, `spec.md:356-357`).
- CHK013 — negative caching is now settled: "The memo MUST hold successful resolutions only: a
  failed destination lookup and a failed destination write MUST NOT be cached, so a later operation
  referring to the same peer re-attempts resolution rather than inheriting a negative result. The
  memo's lifetime is one apply and it is discarded with it" (FR-014, `spec.md:763-767`).
- CHK019 — FR-015 now states `sync`-mode parity explicitly: a `sync`-mode run records deletes in its
  plan exactly as a `plan`-mode run does, "its write path still cannot delete, and not because of a
  configuration setting", and the recorded-versus-written divergence is "structural and intended in
  both modes" (`spec.md:796-800`).
- CHK025 — FR-013's verification route is now stated rather than left absent: "This requirement is
  verified through SC-002 and SC-008 rather than by a criterion of its own: SC-002 measures
  convergence and SC-008 measures the relationship semantics, and between them they exercise every
  clause here" (`spec.md:755-757`).
- CHK033 — the previous pass's sharpest finding is closed. A planned `update` whose destination
  object was deleted out-of-band between plan and apply "materializes as a create, because the
  upsert creates when no destination object matches the key"; no conflict detection, destination
  freshness check, or refusal path is built, and the operation is still reported under its original
  operation identifier and its original action (FR-013, `spec.md:750-755`; Edge Cases `:566-572`;
  AD025 `:274-281`). The behavior is correct against the SDK: the upsert mutation keys on
  `data["id"]` if set, else `data["hfid"]` (`infrahub_sdk/node/node.py:295-298`), so a vanished
  target does create.
- CHK034 — a planned `create` whose destination identity already exists "converges onto the existing
  object rather than producing a duplicate; whether that object's payload differs is not examined,
  because conflict policies are out of scope" (FR-013, `spec.md:748-750`; Edge Cases `:573-576`).

Left unchecked:

- CHK007 — the FR-024 warning's output destination is still unspecified. AD036 (`spec.md:356-357`)
  settles that the warning is emitted rather than recorded, and AD036's FR-018 alignment
  (`:363-364`) keeps warnings outside the surfaces SC-010 scans, but neither FR-024 (`:847-853`) nor
  SC-014 (`:984-989`) says which stream carries it, so it remains undecided whether it lands in the
  log stream or in the standard-output surface. Spec defect.

One consistency finding, recorded here rather than corrected:

- **FR-015's byte-identity precondition is not reflected in SC-006's evidence procedure.** FR-015
  (`spec.md:791-793`) states that the delete-computation manifest field is inside `plan_checksum`
  and unmasked, "so comparing two plans for byte-identity requires both runs to have used the same
  extraction mode". SC-006 (`:939-942`) still describes its evidence as simply "two consecutive plan
  runs", and the engine may legitimately take the incremental path on the second run
  (`should_use_incremental`, `infrahub_sync/cache/incremental.py:51`, consumed at
  `infrahub_sync/potenda/__init__.py:189-200`), which would change both the manifest field and the
  presence of delete operations. The requirement names the precondition; the criterion does not
  carry it. Two smaller notes on the same sentence: it says "the same extraction mode on both
  sides", where the field records only the destination side's completeness.

Spot-check of previously-checked items in this checklist, chosen where the second round edited
nearby text — CHK002, CHK003, CHK004, CHK005, CHK010, CHK011, CHK014, CHK015, CHK016, CHK017,
CHK018, CHK023, CHK036. All still hold. The relocation of FR-013's and FR-014's code anchors cost
nothing: the replace-set rule (CHK003) survives at `spec.md:744-745`, the authoritative-payload rule
(CHK002) at `:745-747`, the zero-match and multi-match refusal arms (CHK004) at `:773-777`, and the
three tier-qualification cases (CHK005) at `:767-772`. The evidence CHK036 rests on is still carried
with its anchors in Assumptions (`:1062-1070`), all of which were re-verified against the tree
(`infrahub_sync/adapters/infrahub.py:611-612`, `:622`, `:510`, `:166-175`;
`infrahub_sync/__init__.py:232`) and are accurate.
