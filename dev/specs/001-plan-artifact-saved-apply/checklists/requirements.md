# Specification Quality Checklist: Saved plan artifact and apply-exactly-what-was-reviewed

**Purpose**: Validate specification completeness and quality before proceeding to planning

**Created**: 2026-07-26

**Feature**: [spec.md](../spec.md)

## Content Quality

- [ ] No implementation details (languages, frameworks, APIs)
- [x] Focused on user value and business needs
- [x] Written for non-technical stakeholders
- [x] All mandatory sections completed

## Requirement Completeness

- [x] No [NEEDS CLARIFICATION] markers remain
- [x] Requirements are testable and unambiguous
- [x] Success criteria are measurable
- [x] Success criteria are technology-agnostic (no implementation details)
- [x] All acceptance scenarios are defined
- [x] Edge cases are identified
- [x] Scope is clearly bounded
- [x] Dependencies and assumptions identified

## Feature Readiness

- [x] All functional requirements have clear acceptance criteria
- [x] User scenarios cover primary flows
- [x] Feature meets measurable outcomes defined in Success Criteria
- [ ] No implementation details leak into specification

## Notes

- Items marked incomplete require spec updates before `/speckit-clarify` or `/speckit-plan`.
- **Content quality, named systems**: the spec names the Infrahub destination adapter, the
  NetBox → Infrahub qualified path, and `examples/netbox_to_infrahub/config.yml`. These are
  product-scope facts carried verbatim from the delivery brief — which systems the outcome must
  work against — not implementation choices, so they are recorded rather than removed.
- **Requirement completeness, traceability**: every brief requirement (DBR-001..DBR-020) and
  acceptance criterion (DBA-001..DBA-013) is mapped in the spec's Requirements Traceability table.
  No brief item is unmapped, and no functional requirement or success criterion was added that the
  brief does not carry.
- **Deliberate deferrals**: two design commitments were originally recorded under Open Design
  Decisions rather than resolved — the plan artifact's concrete on-disk encoding, and which existing
  commands carry review. The clarification session of 2026-07-26 answered both, plus three further
  commitments it surfaced, and recorded all five as provisional decisions AD001–AD005.
- **Two content-quality items regressed in the clarification session, for one reason.** "No
  implementation details" and "No implementation details leak into specification" are now unchecked
  because the Clarifications section names a concrete encoding, a checksum algorithm, and CLI flag
  spellings. That is the deliberate output of clarification, not drift: five design commitments that
  nine downstream outcomes consume are better stated than left to be chosen silently at
  implementation time. The functional requirements themselves were kept behavior-level and point at
  the decision IDs rather than restating them. Re-evaluate both items once AD001–AD005 are ratified
  and the `[PROVISIONAL ...]` markers are stripped; if the decisions belong in the plan rather than
  the spec, that is the moment to move them.
- **`[PROVISIONAL ADnnn]` is not a `[NEEDS CLARIFICATION]` marker.** The former records an answered
  decision awaiting ratification; the latter records an unanswered question. No marker of the latter
  kind remains.

### Independent verification 2026-07-26

Both content-quality items remain unchecked, on a narrower basis than the previous pass recorded.

- **The plan artifact format is not the problem.** The concrete encoding, the checksum rule, the
  operation-identifier derivation and the CLI flag spelling are all product-level shared contract:
  the brief assigns "Define the plan artifact's format, including per-operation identifiers and
  checksums" to this outcome (DBR-008), its "Shared contracts this brief owns" section states the
  format is owned here and consumed by nine later outcomes, and its Constraints delegate the review
  carrier and flag spelling explicitly. Those passages stay.
- **What still fails both items is internal detail inside normative requirement text.** Six
  functional requirements carry `file:line` code anchors, Python module names, class names and method
  names in their MUST clauses. That is implementation detail belonging to `plan.md`, not to a
  specification "written for non-technical stakeholders":
    1. **FR-005**, spec.md:505 — `` (`generator/__init__.py:28`) `` and the `list[Any]` type name.
    2. **FR-008**, spec.md:524 — `` cache_root_for(<sync name>)/<run_id> `` and `` (`cache/paths.py:56-59`) ``.
    3. **FR-009**, spec.md:538 and :541 — `` (`cache/sidecars.py:71`) ``, `` (`cli.py:336-340` …) `` and `` `cli.py:322` ``. The run-state vocabulary itself (`pending | running | dry-run | applied | failed`) is operator-observable and may stay; the anchors may not.
    4. **FR-013**, spec.md:566-571 — `client.create(...)`, `save(allow_upsert=True)`, `InfrahubModel.update`, `client.get(id=self.local_id, ...)`, `local_id`, and the four anchors `` adapters/infrahub.py:611-612 ``, `:622`, `:510`, `:166-175`, plus `` infrahub_sync/__init__.py:232 ``. The behavioral content — planned creates and updates converge through the destination kind's human-friendly ID, cardinality-many relationships are a replace-set, an update payload is authoritative for the mapped fields it carries — is product-level and stays.
    5. **FR-014**, spec.md:581-583 — `` (`dependency_graph.py:33-34`) ``, `` (`dependency_graph.py:81-98`) ``, `` (`infrahub_sync/__init__.py:132-133`) ``. The three qualification cases (self-reference, cycle-dropped optional edge, explicit `order:`) are product-level and stay.
    6. **FR-023**, spec.md:651 — `` (`potenda/__init__.py:354-360`) ``.
- Every one of those anchors was re-verified against the tree during this pass and is factually
  correct, so relocation is a placement fix, not a correction.
- The Clarifications, Assumptions and Dependencies sections also carry code anchors. Those are
  decision records and recorded before-state facts rather than normative requirement text, and the
  spec's Open Design Decisions section states that several decisions exist precisely to correct
  statements this specification made about existing code, so they are left in place.
- Note for the next pass: **"Written for non-technical stakeholders" is marked `[x]` above but sits
  uneasily with the same six passages.** It was not unmarked here, because this pass's mandate covered
  the two unchecked items; it should be re-evaluated once Phase 3 relocates them.
