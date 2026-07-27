# Plan Artifact Format Requirements Checklist: Saved plan artifact and apply-exactly-what-was-reviewed

**Purpose**: Validate the quality of the *requirements* that define the plan artifact format —
manifest fields, the per-operation record, operation identifiers, deterministic serialization, the
checksum rule, format versioning and v1 detection, empty vs torn artifacts, and identifier
uniqueness. These items interrogate what the specification says, not what the implementation does.

**Created**: 2026-07-26

**Feature**: [spec.md](../spec.md)

**Requirements in scope**: FR-001, FR-002, FR-003, FR-004, FR-005, FR-010, FR-011, FR-019, FR-021,
FR-022, FR-026; SC-006, SC-011, SC-013; Key Entities (Plan artifact, Plan manifest, Planned
operation, Relationship reference); Clarifications AD001, AD002.

**Dimensions addressed**: completeness, clarity, consistency, measurability, scenarios and edge
cases, dependencies, assumptions.

**How to use**: every item is a yes/no question about the requirements documents, answerable by
reading them. Items are left unchecked deliberately — a separate reviewer marks them `[X]`.

## Requirement Completeness

- [ ] CHK001 Is the complete set of manifest fields enumerated in a normative requirement, rather than only in Key Entities prose and a provisional decision? [Completeness, Spec §FR-004, §Key Entities/Plan manifest, §Clarifications AD001]
- [ ] CHK002 Is a manifest format-version field required by any functional requirement, or is it only implied by Key Entities and by FR-019's detection rule? [Gap, Spec §FR-004, §FR-019, §Key Entities/Plan manifest]
- [X] CHK003 Is reader behavior specified for a manifest whose declared format version is newer or otherwise unrecognized, as distinct from the v1 case? [Gap, Spec §FR-019]
- [ ] CHK004 Are the fields of a per-operation record enumerated with their obligation level — which are always present, and which may legitimately be absent or empty (for example relationship references on a flat create)? [Completeness, Spec §FR-002]
- [X] CHK005 Is the set of permissible action values fixed and enumerated normatively in a requirement, rather than appearing only in Key Entities? [Completeness, Spec §FR-002, §Key Entities/Planned operation]
- [X] CHK006 Is how a dependency tier is derived — or at minimum the property a tier assignment must satisfy — specified anywhere, given FR-014 relies on tier ordering for peer availability? [Gap, Spec §FR-002, §FR-014]
- [X] CHK007 Is the representation of the source-snapshot binding in the manifest specified — what value is stored, and what makes it "match" at apply? [Gap, Spec §FR-004, §FR-009, §SC-004]
- [X] CHK008 Does any requirement give the artifact enough recorded information to detect a *truncated snapshot*, given the checksum described in AD001 covers only the manifest and the operations section? [Gap, Spec §FR-004, §FR-010, §Clarifications AD001]
- [X] CHK009 Is it specified whether a plan run in the new format continues to write the pre-existing v1 plan file alongside the new artifact? [Gap, Spec §FR-019, §Clarifications AD001]
- [X] CHK010 Are requirements defined for how the artifact is written so that a crash during the plan write yields a detectably torn or absent artifact rather than a valid-looking one? [Gap, Coverage, Spec §FR-001, §FR-010]
- [X] CHK011 Is the tension between FR-002's "required source values as a full payload" and FR-018's "no secret value in the plan artifact" resolved for source fields that are themselves credential-bearing? [Conflict, Spec §FR-002, §FR-018]

## Requirement Clarity

- [ ] CHK012 Is "the required source values as a full payload" defined — required by what authority (the configuration's mapping, the destination schema, or the source record)? [Clarity, Spec §FR-002]
- [X] CHK013 Is "relationship change" unambiguously either a distinct action value or a property of a create/update record, given FR-002 carries relationship references inside every operation while Key Entities and SC-003 treat relationship change as its own class? [Ambiguity, Spec §FR-002, §Key Entities/Planned operation, §SC-003]
- [X] CHK014 Is "destination identity" defined precisely enough to be the same value in all three places the spec uses it — the operation record, the identifier derivation, and a relationship reference? [Clarity, Spec §FR-002, §FR-003, §Clarifications AD002, AD003]
- [X] CHK015 Is the checksum's input scoped unambiguously — exactly which bytes, from which files, in which order, with which separator? [Clarity, Spec §FR-004, §FR-005, §Clarifications AD001]
- [X] CHK016 Does FR-005's "a fixed ordering of every ordered collection inside them" distinguish collections whose source order carries meaning from collections that may be canonically re-sorted without changing meaning? [Conflict, Spec §FR-005]
- [X] CHK017 Is "stable identifier" defined by stating what may change without changing the identifier (the payload) and what must change it (action, kind, identity)? [Clarity, Spec §FR-003, §Clarifications AD002]
- [X] CHK018 Is FR-011's "unless the caller supplies a version identifier explicitly" reconciled with the same requirement's "no new user-facing input is introduced" — is the supplying interface named? [Ambiguity, Spec §FR-011]

## Requirement Consistency

- [X] CHK019 Is FR-004's excluded-field list (checksum field, run identifier, creation timestamp) identical to the set SC-006 masks, as both requirements claim of each other? [Consistency, Spec §FR-004, §SC-006]
- [X] CHK020 Do FR-010's operation-count obligation and FR-022's "recorded count of zero" refer to one field with one name and one meaning? [Consistency, Spec §FR-010, §FR-022]
- [X] CHK021 Are FR-003 and FR-021 consistent with the Edge Cases claim that a collision "means two operations target the same object with the same action" — could two legitimate relationship-change operations on the same object collide under that derivation? [Conflict, Spec §FR-003, §FR-021, §Edge Cases/Identifier collision]
- [X] CHK022 Does the spec state consistently across FR-019, Key Entities, and AD001 whether the pre-existing plan file is written, read, or merely left in place by the new path? [Consistency, Spec §FR-019, §Key Entities/Plan artifact, §Clarifications AD001]
- [X] CHK023 Are FR-002's per-operation relationship references and AD003's reference shape stated identically, so the format is defined once rather than twice? [Consistency, Spec §FR-002, §Key Entities/Relationship reference, §Clarifications AD003]

## Acceptance Criteria Quality and Measurability

- [X] CHK024 Is FR-026 ("orders operations without prescribing write granularity, so batched writes remain possible later") objectively verifiable against this deliverable, or does it assert a property of a change that has not been designed? [Measurability, Spec §FR-026]
- [X] CHK025 Do FR-021, FR-022, and FR-026 each have a success criterion or acceptance scenario, or do they appear in neither the SC set nor the Requirements Traceability table? [Traceability, Spec §FR-021, §FR-022, §FR-026, §Requirements Traceability]
- [X] CHK026 Is SC-006's masking procedure specified precisely enough to reproduce — which fields are masked, how, and over which files the byte comparison runs? [Measurability, Spec §SC-006]
- [X] CHK027 Is a "v1-format plan" defined well enough for SC-011's fixture to be built without guessing? [Measurability, Spec §SC-011, §FR-019]
- [X] CHK028 Is SC-013's "arbitrary opaque string" bounded by any stated constraint on the manifest field — encoding, length, or permitted characters — so a round-trip test has a defined domain? [Measurability, Spec §SC-013, §FR-011]
- [X] CHK029 Can FR-005's determinism be verified independently of SC-006's two-run comparison, for example against a re-serialization of identical content in one process? [Measurability, Spec §FR-005, §SC-006]

## Scenario and Edge-Case Coverage

- [X] CHK030 Are requirements defined for a run directory holding neither a new-format manifest nor the pre-existing plan file? [Gap, Coverage, Spec §FR-019]
- [X] CHK031 Is the empty-plan case separated from the torn case by a rule the reader applies, and is the checksum well-defined over a zero-line operations section? [Coverage, Spec §FR-010, §FR-022, §Edge Cases/Empty plan, §Edge Cases/Torn artifact]
- [X] CHK032 Are requirements defined for what the plan-write path leaves on disk when FR-021's uniqueness assertion fails — and whether that residue is then indistinguishable from a torn artifact? [Gap, Spec §FR-021, §FR-010]
- [X] CHK033 Are cyclic or self-referential dependencies between kinds addressed for tier assignment, or explicitly excluded? [Gap, Coverage, Spec §FR-002, §FR-014]
- [ ] CHK034 Is plan size addressed either by a stated requirement or by an explicit exclusion inside the requirements, rather than only as a deferral note? [Coverage, Spec §Open Design Decisions/Plan size and review performance]
- [X] CHK035 Are requirements defined for an operation whose destination identity is absent or empty in the source data, given the identifier is derived from it? [Gap, Coverage, Spec §FR-003, §FR-002]

## Dependencies

- [ ] CHK036 Is the obligation this format carries to the nine consuming outcomes expressed as a requirement on the artifact (an explicit version field plus a change policy), or only as narrative in the Dependencies section? [Dependency, Spec §Dependencies]
- [X] CHK037 Is an extensibility rule stated for fields later outcomes will add (a schema fingerprint, a richer configuration version) — specifically how a reader treats unknown manifest or operation fields? [Dependency, Gap, Spec §Dependencies]
- [X] CHK038 Is the dependency on the existing per-run directory layout — which must be able to host the plan artifact — recorded as a precondition for FR-001 and FR-004? [Dependency, Spec §Assumptions]

## Assumptions

- [X] CHK039 Is it stated which format requirements must be revisited if AD001 or AD002 is not ratified, given FR-002, FR-004, FR-005, and FR-019 all defer detail to them? [Assumption, Spec §Clarifications, §Open Design Decisions]
- [X] CHK040 Is the assumption that today's plan rows are lossy and unsuitable — the basis for the no-compatibility position — recorded with its consequence if a v1 plan is nonetheless encountered in a supported deployment? [Assumption, Spec §Assumptions, §FR-019]

## Notes

- Items are intentionally all unchecked. Marking them is a reviewer action, not an authoring action.
- Spec defects observed and recorded here rather than corrected, per this run's append-only rule:
    - **Identifier derivation may collide on legitimate plans** (CHK021). FR-003 derives the identifier
  from action, kind, and identity only; two relationship-change operations against the same object
  would share all three, yet FR-021 makes a collision fail the plan run.
    - **The action vocabulary is internally inconsistent** (CHK013). Relationship change is listed as
  an action in Key Entities and as a write class in SC-003, while FR-002 models relationship
  references as fields of an operation.
    - **The snapshot binding has no stated representation** (CHK007, CHK008). SC-004 requires a
  snapshot-binding-mismatch case and a truncated-snapshot case, but no requirement says what value
  binds the pair or what detects truncation; the AD001 checksum does not cover snapshot bytes.
    - **FR-011 is self-tensioned** (CHK018): a caller may supply a version identifier explicitly, yet
  no new user-facing input is introduced, and the supplying surface is unnamed.
    - **FR-021, FR-022, and FR-026 carry no acceptance criterion** (CHK025), and FR-026 as worded may
  not be verifiable at all (CHK024).

### Remediation applied 2026-07-26

The spec defects above were repaired in `../spec.md` by the delivery-apply remediation pass. Boxes
remain unchecked; verification is a separate pass.

- CHK007, CHK008 — closed by the `source_snapshot` manifest field (per-file path, SHA-256 digest,
  row count) added to FR-004, FR-010, Key Entities and the Torn-artifact edge case. `[AD008]`
- CHK013, CHK021 — closed by closing the action vocabulary to `create | update | delete` in FR-002
  and Key Entities, restating SC-003's third write class as operations whose payload carries
  relationship references, and correcting the Identifier-collision edge case. `[AD009]`
- CHK019 — FR-004 and the AD001 clarification no longer claim the checksum's excluded set equals
  SC-006's masked set; both now state why the two sets differ.
- CHK016 — FR-005's ordering rule is now scoped to the operations sequence and relationship-reference
  lists; order-bearing payload list attributes must not be re-sorted.
- CHK024, CHK025 — FR-026 is restated as a format constraint; FR-020 through FR-026 each now carry
  either a criterion or an explicit note, and all appear in the traceability tables.
- CHK009, CHK010, CHK022, CHK032 — FR-019 now requires manifest-last writing and makes a `plan/`
  present without a complete manifest torn rather than v1. `[AD014]`
- CHK011 — resolved as scope: mapped source values only; credentials live in `settings`. `[AD018]`
- CHK033 — FR-014's tier guarantee is qualified to the computed graph. `[AD022]`

### Independent verification 2026-07-26

26 of 40 items verified satisfied and marked `[X]`; 14 left unchecked. Every `[X]` was confirmed
against `../spec.md` text, and every code anchor the spec cites was re-checked against the tree.

- CHK001 — no single normative requirement enumerates the manifest field set; it is still assembled from FR-004 (spec.md:487-499), FR-010 (:546-548), FR-015 (:600-602) and Key Entities (:682-689). Spec defect.
- CHK002 — no functional requirement obliges a manifest format-version field; it appears only in Key Entities prose (spec.md:685). Spec defect.
- CHK003 — FR-019 (spec.md:621-632) still covers only v1; a manifest declaring an unrecognized or newer format version has no stated reader behavior. Spec defect.
- CHK004 — FR-002 (spec.md:472-480) lists the per-operation fields under a flat MUST with no obligation level and no absent-versus-empty rule. Spec defect.
- CHK012 — "the required source values as a full payload" (spec.md:476) is still undefined as to authority; FR-018's "mapped source field values only" (:617) is the closest statement and sits in a secrets requirement, not the format definition. Spec defect.
- CHK014 — "destination identity" is given no single representation (ordered list, name-to-value mapping, or joined string), yet AD002 hashes it (spec.md:57-59) and AD003 sorts on it (:68-69). Spec defect.
- CHK015 — AD001 (spec.md:46-48) still does not say whether the three excluded fields are removed or blanked before canonicalization, nor that the two byte sequences are concatenated with no separator. Spec defect.
- CHK026 — SC-006 (spec.md:746-749) names the masked fields but not how masking is applied (placeholder substitution versus key removal). Spec defect (nit).
- CHK028 — no stated encoding, length or character domain for the configuration-version value; FR-011 (spec.md:552-559) and SC-013 (:785-790) leave it implicit in AD001's canonical JSON. Spec defect (nit).
- CHK030 — an *apply* against a run directory holding neither a new-format manifest nor the pre-existing plan file still has no specified behavior; FR-008's error (spec.md:525-528) is scoped to the review path only. Spec defect.
- CHK034 — plan size remains only a deferral note (spec.md:1020-1023); the streamability property is still Key Entities prose (:679-680), with no FR and no Out-of-scope line. Spec defect.
- CHK035 — no requirement covers an operation whose destination identity is absent or empty, though FR-003 (spec.md:481-485) derives the identifier from it and FR-021 (:636-642) fails the run on collision. Spec defect.
- CHK036 — the nine-consumer obligation is still narrative only (spec.md:923-929); no requirement states a version field or a format-change policy. Spec defect.
- CHK037 — no rule states how a reader treats unrecognized manifest or operation fields, though DB-010's schema-fingerprint field is named as a coming addition (spec.md:926). Spec defect.

### Final verification 2026-07-26

8 of the 14 previously-unchecked items verified satisfied and marked `[X]`; 6 left unchecked.
Checklist stands at 34 / 40. No previously-checked item was found to have been invalidated by the
second edit round.

Verified satisfied:

- CHK003 — the unrecognized-format-version case is now specified and explicitly distinguished from
  the v1 case, at Edge Cases (`spec.md:581-584`), FR-009's first check (`:694-696`) and AD028
  (`:296-304`).
- CHK014 — "destination identity" now has one canonical representation stated for every site that
  consumes it: an ordered mapping of identity attribute name to value, key-sorted, which is what
  AD002 hashes and AD003 orders by (AD035, `spec.md:340-342`).
- CHK015 — the checksum input is now fully scoped: which bytes and which files from AD001
  (`spec.md:44-51`), and removal-not-blanking of the three excluded fields plus concatenation with
  **no separator** from AD035 (`:337-338`).
- CHK026 — SC-006's masking is now stated as key removal applied to the same two fields on both
  sides before the byte comparison (AD035, `spec.md:338-340`), over the two files SC-006 names
  (`:939-942`).
- CHK028 — the configuration-version value's character domain is now stated: a non-empty
  printable-ASCII string (AD035, `spec.md:344-345`). Stated in the clarification rather than in
  FR-011 or SC-013, but stated, so SC-013's round trip has a defined domain.
- CHK030 — a run directory holding no plan artifact at all is now covered for apply as well as
  review, at Edge Cases (`spec.md:577-580`, per AD026), which also forbids creating a run directory
  and forbids presenting the case as a zero-operation plan. The three neighbouring verdicts —
  absent, v1, torn, unreadable — are now disjoint by construction across `:577-580`, `:531-532`,
  `:603-606` and FR-019 (`:818-820`).
- CHK035 — an operation whose destination identity is absent or empty now fails the plan run,
  naming the kind and the identity attribute that had no value (Edge Cases, `spec.md:598-602`).
- CHK037 — the extensibility rule is now stated: unknown *additional* manifest fields are tolerated
  on read and preserved for checksum purposes, named against the schema-fingerprint field a later
  outcome adds (AD028, `spec.md:300-303`). Residue: the rule is stated for manifest fields only;
  unknown fields inside a per-operation record are still unaddressed, and the requirement AD028
  intended to carry the rule does not exist (see below).

Left unchecked:

- CHK001 — **remediation claim not delivered.** AD028 states "The complete field set is carried in
  one normative requirement" (`spec.md:303-304`). That requirement was never written. The manifest
  field set is still assembled from FR-004 (`:637-649`), FR-010 (`:723-725`), FR-015 (`:788-790`),
  FR-009's format-version check (`:694-696`) and Key Entities (`:875-882`). Spec defect.
- CHK002 — **remediation claim not delivered.** No functional requirement obliges the manifest to
  carry a format-version field. FR-009 (`spec.md:694-696`) verifies one and cites **FR-027**, which
  does not exist; AD028 (`:296-297`) asserts the field is required. Spec defect.
- CHK004 — **remediation claim not delivered.** FR-002 (`spec.md:626-627`) says each per-operation
  field "carries the obligation level and the absent-versus-empty rule **FR-028** states". FR-028
  does not exist. AD035 (`:342-343`) asserts that each field carries such a rule but never states
  any of them, so no obligation level is enumerated anywhere. Spec defect.
- CHK012 — **remediation claim not delivered.** FR-002 (`spec.md:627-628`) defers the payload's
  "single authority" to FR-028, which does not exist. AD035's clause (`:343-344`) is about the
  payload's authority *over destination fields* — the FR-013 sense — not about which authority
  decides the field set. The only statement of the field set's authority remains FR-018's "mapped
  source field values only" (`:810`), which is exactly the inference the previous pass rejected.
  Spec defect.
- CHK034 — **remediation claim not delivered.** AD030 (`spec.md:310-314`) states that plan size,
  pagination, retention, performance targets, output stability and format-change governance are
  "each recorded as an explicit Out-of-scope line". The Out of Scope section (`:1009-1041`) was not
  touched by the second round and contains no such line. Plan size remains a deferral note at
  `:1213-1216`. Spec defect.
- CHK036 — both halves still missing. No requirement states a manifest version field (see CHK002),
  and no format-change policy exists anywhere: AD030 declined to write one and the Out-of-scope
  line recording the declination was never added. The nine-consumer obligation remains narrative in
  Dependencies (`spec.md:1116-1122`). Spec defect.

Spot-check of previously-checked items in this checklist, chosen where the second round edited
nearby text — CHK005, CHK006, CHK011, CHK013, CHK016, CHK019, CHK022, CHK023, CHK025, CHK027,
CHK031, CHK033, CHK039. All still hold. In particular CHK016's order-bearing-collection rule and
CHK033's three tier-qualification cases survived the anchor relocation intact (FR-005
`spec.md:653-657`, FR-014 `:767-772`), and CHK006's tier-derivation statement is still carried in
Assumptions (`:1071-1078`), which retained its anchors. CHK039's revisit map was correctly widened
from "AD008–AD023" to "AD008–AD036" (`:38-39`).
