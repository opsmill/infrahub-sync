# Feature Specification: Complete Run Inspection and Plan Review

**Feature Branch:** `013-run-inspection-review`
**Created:** 2026-10-08
**Status:** Draft
**Input:** Complete MVP lifecycle parity and make large saved plans practical to review.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Find and inspect service-owned runs (Priority: P1)

As an operator, I can list runs and inspect a selected run through REST, Python, and CLI without
knowing its identifier in advance.

**Independent Test:** Create runs in several states and verify all three clients return the same
ordered set and the same selected run identity.

**Acceptance Scenarios:**

1. **Given** several runs exist, **When** they are listed, **Then** the newest page is returned in a
   stable order with enough configuration, operation, status, and time information to choose one.
2. **Given** no runs exist, **When** runs are listed, **Then** every client returns an empty result
   successfully.

### User Story 2 - Verify, cancel, and inspect artifacts from the CLI (Priority: P1)

As an operator, I can verify a saved plan without applying it, request cancellation of a cancellable
run, and list/fetch its public artifacts from the CLI.

**Independent Test:** Exercise each operation through REST, Python, and CLI and compare the
normalized resource or refusal.

### User Story 3 - Review a large plan without duplicated noise (Priority: P1)

As a reviewer, I can read a concise summary and request bounded detail without seeing each peer
identity duplicated per operation.

**Independent Test:** Render a representative full NetBox import and verify summary totals equal
detail totals, identities are shown once per operation, and output can be bounded deterministically.

### Edge Cases

- Pagination while new runs are being created.
- Cancellation races with natural completion or a terminal run.
- An artifact exists privately but is not publishable.
- Verification fails multiple checks at once.
- A plan has no operations, very many operations, or identities containing control characters.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001:** The service MUST provide a read-only run-list operation with stable newest-first
  ordering and bounded pagination.
- **FR-002:** Run-list items MUST include run ID, configuration identity/version, operation, status,
  creation time, and completion time when available.
- **FR-003:** Python and CLI run listing MUST preserve the service's ordering, page boundaries, and
  resource meaning.
- **FR-004:** The CLI MUST provide standalone saved-plan verification that performs no destination
  write and reports every failed verification check.
- **FR-005:** The CLI MUST support cancellation with the same cancellability rules and idempotent
  terminal behavior as REST and Python.
- **FR-006:** The CLI MUST list a run's public artifacts and fetch a selected public artifact without
  exposing private worker bundles.
- **FR-007:** Equivalent lifecycle operations MUST return equivalent resources or public errors
  across REST, Python, and CLI.
- **FR-008:** Plan summary totals MUST equal the corresponding detail operation counts.
- **FR-009:** Default plan detail MUST display each operation's peer identity once, not once in both
  source and destination labels when they are equivalent.
- **FR-010:** An operator MUST be able to bound plan detail and select operations by existing stable
  dimensions such as kind and action.
- **FR-011:** Site grouping MUST be available only when a configuration declares a stable,
  non-secret grouping field; the MVP MUST NOT infer site identity heuristically.
- **FR-012:** Empty lists, empty plans, missing public artifacts, terminal cancellation, and failed
  verification MUST be explicit successful-empty or typed-refusal outcomes.
- **FR-013:** Every output and error MUST satisfy secret containment and failure-evidence contracts.

### Key Entities

- **Run List Item:** The bounded summary used to locate a service-owned run.
- **Verification Report:** The complete set of checks and their verdicts, with no write side effect.
- **Public Artifact Reference:** Metadata for an artifact safe for client retrieval.
- **Plan View:** Summary or bounded detail derived from one immutable saved plan.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001:** REST, Python, and CLI list the same run identities in the same order for a fixed state.
- **SC-002:** Standalone verification produces zero destination writes in all passing and failing cases.
- **SC-003:** Repeating cancellation of a terminal run produces no state regression or duplicate work.
- **SC-004:** Summary and detail counts match for empty, small, and representative full-import plans.
- **SC-005:** No rendered operation repeats an equivalent peer identity, and bounded output is stable.
- **SC-006:** No supported artifact operation exposes a private worker bundle.

## Assumptions

- Basic bounded pagination is sufficient for MVP; advanced search and saved filters are post-MVP.
- The service remains the source of truth for lifecycle behavior.
- The operator web UI is post-MVP and will consume these interfaces later.
