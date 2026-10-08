# Feature Specification: Actionable Run Failure Evidence

**Feature Branch:** `012-run-failure-evidence`
**Created:** 2026-10-08
**Status:** Draft
**Input:** Make refused, failed, and uncertain-write runs diagnosable through supported interfaces.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Understand why a run stopped (Priority: P1)

As an operator, I can inspect a run and learn the failed stage, safe cause, outcome certainty, and
recommended next action without reading worker internals.

**Independent Test:** Trigger one failure in every lifecycle stage and verify the stored run detail
contains the required evidence after the worker process exits.

**Acceptance Scenarios:**

1. **Given** an apply transport failure with an uncertain outcome, **When** the run is inspected,
   **Then** it is marked reconciliation-required and instructs the operator to inspect the
   destination before creating a new plan.
2. **Given** Infrahub rejects a write for a known safe reason, **When** the run is inspected,
   **Then** the safe rejection class and useful cause are preserved.

### User Story 2 - Resolve configuration contention (Priority: P1)

As an operator whose write is refused, I can identify the active run holding the configuration
guard and inspect it.

**Independent Test:** Hold the guard with one run, submit a conflicting write, and verify the
refusal identifies the holder without changing either run's ownership.

### User Story 3 - Correlate structured operational evidence (Priority: P2)

As an operator or support engineer, I can filter logs by run, operation, stage, and outcome using
separate structured fields.

**Independent Test:** Capture a multi-stage run and select its events using only structured fields,
without parsing message text.

### Edge Cases

- Failure occurs before a durable run record can be created.
- Recording failure evidence also fails.
- An adapter error contains credentials or raw payload data.
- A write succeeds remotely but the acknowledgement or evidence persistence fails.
- The lock holder finishes between refusal detection and inspection.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001:** Every started run that stops unsuccessfully MUST retain a terminal or
  reconciliation-required record whenever product storage remains available.
- **FR-002:** Run detail MUST expose lifecycle stage, safe error class, safe cause, outcome
  certainty, and recommended operator action.
- **FR-003:** A pre-run refusal MUST return a stable error class, safe reason, and correlation data
  even when no run record is created.
- **FR-004:** Configuration-guard refusal MUST identify the holding run when one is known.
- **FR-005:** Adapter and Infrahub rejection details MUST preserve a useful safe cause rather than
  collapsing every non-uniqueness failure into a generic message.
- **FR-006:** Every write failure MUST be classified as not attempted, confirmed failed, confirmed
  applied, or uncertain.
- **FR-007:** An uncertain write MUST set reconciliation-required and MUST NOT recommend automatic
  retry or replay in the MVP.
- **FR-008:** The recommended uncertain-write action MUST instruct the operator to inspect the
  destination and create a new plan after reconciliation.
- **FR-009:** Logs for lifecycle transitions MUST expose run ID, operation, stage, and outcome as
  separate structured fields when those values exist.
- **FR-010:** Evidence returned through REST, Python, CLI, logs, or artifacts MUST satisfy spec 010.
- **FR-011:** Failure to persist secondary diagnostic evidence MUST preserve the original error and
  emit a safe indication that evidence is incomplete.
- **FR-012:** Equivalent failures MUST use the same public classification across REST, Python, CLI,
  and stored run detail.

### Key Entities

- **Failure Evidence:** Safe classification, stage, cause, certainty, and next action.
- **Write Outcome:** One of not-attempted, confirmed-failed, confirmed-applied, or uncertain.
- **Guard Holder:** The active run currently authorized to write one configuration.
- **Reconciliation Requirement:** An explicit instruction and reason attached to an uncertain run.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001:** Every tested lifecycle-stage failure reports stage, class, certainty, and next action.
- **SC-002:** One hundred percent of tested contention refusals identify the active holder when known.
- **SC-003:** Structured log filtering retrieves all events for a run without parsing message text.
- **SC-004:** No uncertain-write test recommends or performs automatic retry.
- **SC-005:** Public failure evidence contains no planted credential or raw private payload.

## Assumptions

- Product storage can be unavailable; the API still returns a safe correlated refusal when possible.
- Governed replay and per-operation recovery ledgers remain post-MVP.
- The existing per-configuration write guard remains the concurrency authority.
