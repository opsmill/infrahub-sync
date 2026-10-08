# Feature Specification: Complete Configuration Preflight

**Feature Branch:** `011-configuration-preflight`
**Created:** 2026-10-08
**Status:** Draft
**Input:** Make `configs validate` predict whether the qualified journey can safely plan and write.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Refuse an invalid configuration before a run (Priority: P1)

As an operator, I can validate a registered configuration and learn every actionable schema,
write-mode, identity, and credential-readiness problem before starting a run.

**Independent Test:** Validate configurations containing one instance of every required failure.
Each report is complete, safe, and no run or destination write is created.

**Acceptance Scenarios:**

1. **Given** a mapped destination field was removed or renamed, **When** validation runs, **Then**
   validation fails and names the mapping location and missing field.
2. **Given** a required credential reference is unavailable in the worker runtime, **When**
   validation runs, **Then** validation fails without exposing the credential value.
3. **Given** a source value is outside a destination Dropdown's choices, **When** validation can
   evaluate that value during planning, **Then** planning refuses before apply.

### User Story 2 - Receive the same verdict through every client (Priority: P1)

As an API, Python, or CLI user, I receive equivalent validation status, issue codes, and locations
for the same configuration version.

**Independent Test:** Submit one version through all supported validation interfaces and compare
their normalized reports.

### User Story 3 - Trust validation and planning to agree (Priority: P1)

As an operator, a configuration that passes preflight does not later fail planning for a condition
that preflight was required to evaluate.

**Independent Test:** Exercise the required validation matrix, then plan every passing case and
verify that none is rejected for a covered preflight rule.

### Edge Cases

- A mapped kind has no writable HFID for create operations but is valid for non-create behavior.
- Destination capabilities change after validation and before planning.
- Several independent issues exist in one configuration version.
- A credential reference exists in the API environment but not in the worker environment.
- Redacted configuration content is displayed beside an original-package checksum.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001:** Validation MUST evaluate one immutable registered configuration version.
- **FR-002:** Validation MUST perform structural, adapter-capability, destination-schema,
  destination-write-mode, HFID, mapped-field, and credential-readiness checks.
- **FR-003:** Validation MUST refuse removed or renamed mapped destination fields rather than
  silently omit them from runtime models.
- **FR-004:** Validation MUST distinguish operations requiring a writable HFID from operations for
  which no create identity is needed.
- **FR-005:** Validation MUST report unsupported destination write modes before a run starts.
- **FR-006:** Validation MUST test credential availability in the runtime context that will execute
  the run, without returning or persisting credential values.
- **FR-007:** Planning MUST validate source values against destination enumerations such as
  Dropdown choices before producing an applicable plan.
- **FR-008:** REST, Python, and CLI validation MUST expose equivalent issue codes, severities,
  configuration locations, and safe messages.
- **FR-009:** Validation MUST report all independently detectable issues in one invocation rather
  than stop after the first issue.
- **FR-010:** Validation MUST be read-only: it MUST NOT create a run, publish a plan, or mutate a
  source or destination.
- **FR-011:** Planning MUST refuse when a required preflight rule would fail against the current
  schema or capabilities, even if an earlier validation passed.
- **FR-012:** Registration responses MUST distinguish the checksum of immutable accepted content
  from any redacted representation shown to the operator.
- **FR-013:** Reports MUST identify the configuration version, schema/capability observation, and
  validation time needed to explain later drift.

### Key Entities

- **Validation Report:** The immutable-version verdict and its complete set of safe issues.
- **Validation Issue:** A coded, located, severity-ranked problem with an operator action.
- **Schema Observation:** The destination schema/capability state used for the verdict.
- **Credential Readiness Result:** Availability status for a reference, never its value.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001:** Every required invalid case is refused before a run or destination write exists.
- **SC-002:** REST, Python, and CLI produce equivalent normalized reports for the same version.
- **SC-003:** No passing matrix case later fails planning for a rule preflight was required to test.
- **SC-004:** A report with multiple independent faults returns all of them in one invocation.
- **SC-005:** Validation emits no credential value and performs zero source/destination mutations.

## Assumptions

- Live destination access is available when schema-dependent validation is requested.
- Drift between validation and planning is possible; planning therefore rechecks safety conditions.
- Full conformance across every bundled adapter remains post-MVP; the qualified journey is required.
