# Specification Quality Checklist: Plan problem reporting

**Purpose**: Validate specification completeness and quality before proceeding to planning

**Created**: 2026-10-15

**Feature**: [spec.md](../spec.md)

## Content Quality

- [x] No implementation details (languages, frameworks, APIs)
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
- [x] No implementation details leak into specification

## Notes

- Answered by the product owner on 2026-10-15, so no marker remains:
  - FR-005: a record that refers to a left-out record only through an optional relationship is
    kept and written without that relationship.
  - FR-012: a `sync` run stops after a plan that leaves out records. The MVP has no option that
    skips that stop; the policy for runs that nobody watches is 3.0.
- Revision of 2026-10-15 after the cross-spec review: the kept list was added to the API
  contract (FR-023), the stage values follow the code (`plan`, `verify`, `apply`, `sync`), and
  FR-027 to FR-035 assign MVP blockers to this specification (SYNC-82, SYNC-262, #374,
  INFP-651, INFP-652, SYNC-107, SYNC-147) and the AD-12 and #369 decision record changes.
  FR-029 and FR-030 need product sign-off; this is stated in the Summary, not as a marker.
- Revision of 2026-10-15 after the product owner rejected value masking: FR-018, FR-019,
  FR-027, FR-028 and FR-030, User Story 3 scenarios 1, 4 and 5 (and new scenario 6), two edge
  cases, the run failure cause entity, SC-005, SC-007 and three assumptions now state that
  credential values are kept out of causes, problem reasons and plan files by how these are
  built. A cause holds only named fields and never the text of an adapter's or an HTTP
  library's error. The existing replacement of values stays unchanged in the testing release.
  What remains of SYNC-82 (short credentials in the worker and orchestration logs) and of
  SYNC-262 (the replacement pass on published plans) is stated in FR-028 and the assumptions.
- Revision of 2026-10-15 after two more product owner decisions, so no marker remains:
  - A source record that refers to a peer that is not in the source is the seventh problem
    type, `peer_not_found_in_source`, and does not fail the plan. Changed: Summary, FR-002,
    FR-003, FR-005, User Story 1 (independent test, scenario 1, new scenario 8), the plan
    problem and kept record entities, SC-002, new SC-010 and two assumptions.
  - SYNC-262 is follow-up work after the MVP, not an MVP blocker of this specification. The
    existing replacement of values stays unchanged in the MVP. The rest of SYNC-82 (short
    credentials in the worker and orchestration logs) is also follow-up work. Changed:
    Summary, the introduction of the credential requirements, FR-028 and the assumptions.
- The Sync API and the CLI are named because the request asks for the contract the UI of spec 011
  consumes. The requirements state what each surface returns or shows, not how it is built.
- Iteration 1 removed a source file reference and the term "schema fingerprint" from the
  Assumptions section.
