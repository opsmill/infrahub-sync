# Feature Specification: MVP Acceptance Contract

**Feature Branch:** `009-mvp-acceptance-contract`
**Created:** 2026-10-08
**Status:** Draft
**Input:** Define the authoritative service-first MVP boundary and its release evidence.

## Clarifications

### Session 2026-10-08

- Q: May any required MVP criterion be waived while still passing qualification? → A: No MVP
  criterion is waivable.
- Q: What form is authoritative for the contract and qualification results? → A: The
  human-readable contract is normative, with a machine-readable result manifest.
- Q: What happens when the approved contract changes after a candidate was qualified? → A: The
  candidate must be requalified against the latest approved contract before release; earlier
  results remain historical records.
- Q: What independent acceptance is required for an MVP candidate? → A: At least one evaluator who
  did not implement the candidate must execute the qualified journey and approve the manifest.
- Q: Which lifecycle capabilities differ between the REST API, Python SDK, and CLI? → A: None; the
  API is authoritative, the SDK exposes it, and the CLI provides capability parity through the SDK.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Determine whether the MVP is complete (Priority: P1)

As a release owner, I can evaluate one finite checklist and reach the same release decision as
another reviewer without interpreting historical cards or capability prose.

**Why this priority:** The team cannot make a reproducible release decision until the MVP boundary
and the evidence required to prove it are explicit.

**Independent Test:** Give the contract and a candidate evidence bundle to two reviewers. They
reach the same pass/fail result and identify the same unmet criteria.

**Acceptance Scenarios:**

1. **Given** all required evidence is present and passing, **When** a reviewer evaluates the
   contract, **Then** the candidate is eligible for independent qualification.
2. **Given** any required criterion lacks passing evidence, **When** a reviewer evaluates the
   contract, **Then** the candidate is not declared MVP-complete and the missing evidence is named.

### User Story 2 - Keep post-MVP work out of the release gate (Priority: P1)

As a product owner, I can distinguish MVP requirements from later v3 outcomes so release work is
not blocked by deferred capabilities or weakened by accidentally deferring MVP safety.

**Why this priority:** A fixed boundary prevents both accidental scope expansion and the release of
an unsafe candidate under the label of MVP.

**Independent Test:** Classify every gap in the approved MVP analysis using only this contract.
Each gap maps to exactly one MVP specification or to the explicit post-MVP list.

### User Story 3 - Trace requirements to evidence (Priority: P2)

As an engineer or reviewer, I can trace every MVP criterion to its specification, validation
method, and retained evidence.

**Why this priority:** Traceability makes failed criteria actionable and keeps the release decision
independent of undocumented project history.

**Independent Test:** Select any acceptance criterion and follow its links to an owning spec and a
named evidence type without consulting Jira.

### Edge Cases

- A capability is implemented but has no retained qualification evidence.
- Historical card text conflicts with the service-first architecture decision.
- A candidate passes an older contract revision but not the current revision.
- An exception is requested for a required MVP criterion.
- Evidence refers to a different source revision or candidate digest than the candidate under review.
- The same evidence item is referenced by two criteria but its declared evidence type is not
  accepted by both criteria.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001:** The contract MUST define the MVP as a service-first product whose supported clients
  use the Sync HTTP API; offline CLI writes and in-process writes are excluded.
- **FR-002:** The contract MUST name NetBox to Infrahub as the qualified end-to-end adapter journey.
- **FR-003:** The required journey MUST cover obtain, start, register, validate, plan, review,
  apply, and inspect against an existing Infrahub deployment.
- **FR-004:** The contract MUST require secret containment, complete preflight validation,
  actionable failure evidence, lifecycle inspection, and immutable release qualification.
- **FR-005:** The contract MUST require run listing, individual inspection, standalone verification,
  cancellation, and public-artifact listing and retrieval across the REST API, Python SDK, and CLI.
  The API MUST be authoritative, and the SDK and CLI MUST preserve the same lifecycle capabilities
  and public semantics.
- **FR-006:** The contract MUST state that uncertain MVP writes require operator reconciliation and
  a new plan; automatic or governed replay is not an MVP capability.
- **FR-007:** The contract MUST enumerate the post-MVP capabilities in the explicit deferral list
  below and MUST NOT make them release gates.
- **FR-008:** Every criterion MUST name an owning specification, an objective validation method,
  and the evidence retained for release review.
- **FR-009:** A criterion without current passing evidence MUST be treated as failed, even if its
  implementation is believed to exist.
- **FR-010:** No required MVP criterion MAY be waived. A candidate with any failed or unevidenced
  criterion MUST fail qualification.
- **FR-011:** The contract MUST be versioned, and qualification evidence MUST identify the exact
  contract revision used.
- **FR-012:** Conflicting historical requirements for standalone execution MUST be marked
  superseded by the service-first contract rather than implemented.
- **FR-013:** The human-readable, version-controlled acceptance contract MUST be the normative
  definition of the MVP criteria and scope boundary.
- **FR-014:** Each qualification decision MUST include a machine-readable result manifest that
  identifies every criterion, its result, and its retained evidence references.
- **FR-015:** A candidate MUST pass the latest approved contract revision before release. A result
  produced under an earlier revision MUST remain immutable as historical evidence but MUST NOT
  establish current release eligibility.
- **FR-016:** At least one evaluator who did not implement the candidate MUST execute the qualified
  end-to-end journey and approve the qualification manifest before the candidate can pass.
- **FR-017:** The CLI MUST expose lifecycle operations through the Python SDK rather than establishing
  a separate lifecycle contract or bypassing the service boundary.
- **FR-018:** Contract validation MUST report whether a manifest is consistent with the selected
  contract bytes and MUST NOT claim that the contract is approved or that the candidate is
  release-eligible. Spec 014 owns approval provenance and the release-eligibility decision.

### MVP Scope Mapping

The acceptance contract owns the release decision; the following specifications own the evidence
needed for that decision:

- **Spec 010 — Secret containment:** credentials do not appear in client-visible or qualification
  surfaces.
- **Spec 011 — Configuration preflight:** invalid or incomplete configurations are refused before
  planning or writing.
- **Spec 012 — Run failure evidence:** refused, failed, and uncertain outcomes tell an operator what
  happened and what to do next.
- **Spec 013 — Run inspection and plan review:** operators can list and inspect runs, verify and
  review plans, cancel eligible work, and inspect artifacts through the supported interfaces.
- **Spec 014 — MVP release qualification:** one immutable candidate is obtained, verified, started,
  exercised end to end, and independently accepted with retained evidence.

### Explicit Post-MVP Deferrals

The following remain part of the larger v3 goal but do not block MVP qualification:

- full adapter conformance and custom adapter onboarding;
- governed crash recovery, checkpoint replay, and automatic replay;
- Infrahub branch-based review and merge workflows;
- schedules, event triggers, and trigger notifications;
- incremental extraction, cursor management, and periodic full resynchronization;
- batched or indexed large-run planning and alternative planners;
- explicit ownership scopes and safe deletion within those scopes;
- parallel partitions and configurable destination-write concurrency;
- Kubernetes, high availability, backup and restore, and broader production operations;
- the operator web interface.

### Key Entities

- **MVP Criterion:** A uniquely identified, required release condition with an owning specification,
  validation method, accepted evidence types, and pass/fail result.
- **Evidence Item:** A retained result with a declared evidence type, tied to one or more criteria,
  the contract revision, source revision, candidate digest, validation time, and evidence producer.
  Reuse is valid only when every referencing criterion accepts that declared type.
- **Qualification Decision:** The pass/fail result for one candidate digest under one exact contract
  revision, including every criterion result.
- **Qualification Manifest:** The machine-readable representation of a qualification decision,
  containing candidate and contract identity, every criterion result, and evidence references; it
  does not replace the normative human-readable contract. It also identifies the independent
  evaluator and records their approval.
- **Scope Deferral:** A named v3 capability intentionally excluded from MVP, with enough description
  to prevent it from being mistaken for an unmet MVP criterion.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001:** Every gap in the approved MVP analysis maps to one MVP spec or one explicit deferral.
- **SC-002:** Two independent reviewers produce identical release decisions from the same evidence.
- **SC-003:** One hundred percent of MVP criteria name an owner, validation method, and evidence.
- **SC-004:** No MVP criterion depends on Jira-only text or an unwritten product decision.
- **SC-005:** No post-MVP capability is required to pass the MVP release gate.
- **SC-006:** A reviewer can detect every evidence item whose contract revision, source revision, or
  candidate digest does not match the candidate under review.
- **SC-007:** Automated validation can account for every normative criterion and reject a manifest
  with a missing, duplicate, or unknown criterion identifier.
- **SC-008:** After a contract revision is approved, no candidate qualified only against an older
  revision is reported as currently release-eligible.
- **SC-009:** Every passing qualification identifies at least one non-implementing evaluator who
  completed the qualified journey and approved the manifest.
- **SC-010:** For a fixed service state, equivalent REST, Python SDK, and CLI lifecycle operations
  expose the same resources, results, and typed refusals.
- **SC-011:** No standalone contract-validation result describes a selected contract as approved or
  a candidate as release-eligible.

## Assumptions

- ADR 16's service-first boundary remains authoritative.
- The full v3 capability set continues after MVP and is not cancelled by this contract.
- The contract is maintained in version control alongside the implementation it qualifies.
- Specs 010 through 014 retain the detailed acceptance criteria for their respective evidence;
  this contract references rather than duplicates those details.
- Spec 014 supplies approved-contract provenance and combines it with a successful consistency
  result before making a release-eligibility decision.
- Release evidence may be produced by automated or human validation, provided the result is
  reproducible and records the required identity fields.

## Dependencies

- The approved MVP delivery-path design supplies the initial scope mapping and post-MVP deferrals.
- Specs 010 through 014 must expose objective results that can be assembled into one qualification
  decision.
- The release process must preserve the contract revision and candidate identity with the evidence
  bundle.
