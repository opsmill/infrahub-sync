# Feature Specification: Secret Containment and Safe Publication

**Feature Branch:** `010-secret-containment`
**Created:** 2026-10-08
**Status:** Draft
**Input:** Prevent credentials from leaking or corrupting any client-visible or qualification output.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Publish safe run information (Priority: P1)

As an operator, I can inspect errors, plans, results, artifacts, and logs without exposing a
credential used by the run.

**Independent Test:** Execute the supported journey with planted credentials of every required
shape and scan all client-visible outputs. No planted value or reversible representation appears.

**Acceptance Scenarios:**

1. **Given** a short credential appears in an upstream error, **When** the error crosses a public
   boundary, **Then** the credential is replaced and the error remains actionable.
2. **Given** a credential contains quotes, slashes, braces, commas, or control characters, **When**
   a plan or result is serialized, **Then** the document remains valid and contains no credential.

### User Story 2 - Detect containment regressions before release (Priority: P1)

As a release reviewer, I receive automated evidence that planted canary credentials did not appear
in qualification output.

**Independent Test:** Plant a known canary in each supported credential path, run qualification,
and verify that the scan passes; inject one deliberate leak and verify that publication fails.

### User Story 3 - Preserve useful non-secret context (Priority: P2)

As an operator diagnosing a failure, I still receive the stage, error class, affected non-secret
field, and recommended action after redaction.

**Independent Test:** Compare a sanitized failure with its private source and verify that required
diagnostic fields remain while secret values do not.

### Edge Cases

- Credentials shorter than common minimum-redaction thresholds.
- One credential is a substring of another or of a legitimate non-secret value.
- Unicode, multiline, JSON-significant, URL-encoded, or shell-significant credentials.
- A credential occurs multiple times across nested structures and exception chains.
- Publication fails while sanitizing an otherwise valid plan.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001:** All client-visible errors, plans, results, artifacts, logs, and qualification output
  MUST exclude every credential resolved for the associated request or run.
- **FR-002:** Redaction MUST cover credentials regardless of length and MUST cover JSON-significant,
  multiline, Unicode, URL-encoded, and nested occurrences.
- **FR-003:** Sanitization MUST preserve the syntactic validity of every published structured
  document.
- **FR-004:** Sanitization MUST cover exception messages, exception chains, validation details,
  adapter responses, and rendered plan fields before they cross a public boundary.
- **FR-005:** A sanitization or serialization failure MUST prevent publication of the unsafe
  document and return a safe, actionable failure instead.
- **FR-006:** Public output MUST retain non-secret diagnostic context required by spec 012.
- **FR-007:** Internal byte-stable worker bundles MAY remain private and unsanitized, but no public
  API, client command, log, diagnostics output, or release artifact may expose them.
- **FR-008:** Qualification MUST plant distinct canaries in every supported credential source and
  scan all captured output and published artifacts.
- **FR-009:** Detection of any canary or reversible encoding of a canary MUST fail qualification.
- **FR-010:** Canary evidence MUST identify the tested output classes and candidate digest without
  recording the canary values themselves.
- **FR-011:** Redaction behavior MUST be deterministic for identical input and credential sets.
- **FR-012:** The supported journey MUST remain functional when credentials contain every required
  edge-case character class.

### Key Entities

- **Secret Set:** The request- or run-scoped values that public output must not disclose.
- **Public Output:** Any information available to a client, operator, CI reader, or release consumer.
- **Private Bundle:** Worker-only state that is never returned or logged publicly.
- **Canary Evidence:** Proof that each output class was scanned for planted credentials.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001:** Zero planted credentials or reversible encodings appear in qualification output.
- **SC-002:** One hundred percent of published plans and results remain parseable after sanitization.
- **SC-003:** A deliberately planted leak fails qualification before candidate publication.
- **SC-004:** Every sanitized failure still reports its lifecycle stage, error class, and next action.
- **SC-005:** The short and structured credential regressions represented by SYNC-82 and SYNC-262
  are covered by repeatable tests.

## Assumptions

- Credential values are available to the sanitization boundary for the lifetime of a request/run.
- Private worker bundles remain inaccessible through supported client interfaces.
- Role-based access control and external secret-manager support are post-MVP.
