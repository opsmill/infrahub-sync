# Feature Specification: Qualified and Consumable MVP Release

**Feature Branch:** `014-mvp-release-qualification`
**Created:** 2026-10-08
**Status:** Draft
**Input:** Publish and independently qualify one immutable single-host MVP candidate.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Obtain and verify an immutable candidate (Priority: P1)

As an evaluator, I can pull the supported Sync image by digest and verify that its published SBOM
and release metadata refer to that exact artifact.

**Independent Test:** Starting only from the release record, pull by digest, verify metadata and
SBOM association, and confirm the running image has the recorded identity.

**Acceptance Scenarios:**

1. **Given** a published candidate, **When** it is pulled by digest, **Then** the obtained image
   matches the digest and its release record identifies the same source revision and SBOM.
2. **Given** a tag later points elsewhere, **When** the recorded digest is pulled, **Then** the
   evaluator still obtains the qualified candidate.

### User Story 2 - Start the supported single-host deployment (Priority: P1)

As an operator, I can start the documented Compose deployment with PostgreSQL and a maintained
S3-compatible store, using custom trust and adapter credentials without rebuilding the image.

**Independent Test:** Start from a clean supported host, configure custom CA trust and the
qualified adapter credentials, and reach the documented ready state.

### User Story 3 - Independently complete the qualified journey (Priority: P1)

As a release reviewer who did not author the candidate, I can run a user-owned NetBox-to-Infrahub
configuration through register, validate, plan, review, apply, and inspect against an existing
Infrahub deployment.

**Independent Test:** An independent evaluator follows only published documentation and records a
passing evidence bundle tied to the candidate digest.

### Edge Cases

- Registry tag and digest disagree.
- The SBOM is missing, inaccessible, or names another artifact.
- The host requires a private CA for registry, source, or destination access.
- Qualification is interrupted and rerun on a clean host.
- Canary scanning or one lifecycle stage fails after image publication but before promotion.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001:** The candidate image MUST be available from the supported registry by immutable digest.
- **FR-002:** Release metadata MUST bind image digest, source revision, version, build provenance,
  Compose bundle revision, and SBOM.
- **FR-003:** The SBOM MUST be downloadable with the release and MUST identify the qualified image.
- **FR-004:** The supported Compose deployment MUST use PostgreSQL and a maintained S3-compatible
  storage image rather than building a temporary storage service.
- **FR-005:** An operator MUST be able to provide custom CA trust and qualified adapter credentials
  to the service and worker without rebuilding the Sync image or committing secrets.
- **FR-006:** Qualification MUST start from a clean supported host and use the same immutable image
  digest throughout.
- **FR-007:** Qualification MUST exercise obtain, start, register, validate, plan, review, apply,
  and inspect for a user-owned NetBox-to-Infrahub configuration against an existing Infrahub.
- **FR-008:** Qualification MUST verify secret-canary containment according to spec 010.
- **FR-009:** Qualification MUST retain the evidence required by spec 009 and MUST name the exact
  revisions of all six MVP specifications.
- **FR-010:** At least one acceptance run MUST be performed and signed off by an evaluator who did
  not author the candidate changes.
- **FR-011:** A failed required gate MUST prevent candidate promotion and MUST identify the failed
  criterion without publishing secrets.
- **FR-012:** The lifecycle qualification gate MUST pass three consecutive clean executions for the
  same candidate inputs before it is considered stable enough for MVP promotion.
- **FR-013:** Published startup documentation MUST distinguish supported MVP deployment from the
  developer stack and from post-MVP production/Kubernetes guidance.
- **FR-014:** Qualification MUST complete in under one hour and record each lifecycle stage
  duration so the result can be audited.

### Key Entities

- **Candidate Digest:** The immutable identity of the image under qualification.
- **Release Record:** The binding among digest, source, provenance, SBOM, bundle, and spec revisions.
- **Qualification Evidence Bundle:** Retained criterion results for one candidate digest.
- **Independent Acceptance:** A named reviewer verdict by someone other than the candidate author.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001:** An evaluator can obtain the exact candidate from its digest using only release metadata.
- **SC-002:** The SBOM and provenance unambiguously identify the qualified digest and source revision.
- **SC-003:** Three consecutive clean-host lifecycle qualifications pass for identical candidate inputs.
- **SC-004:** An independent evaluator completes the full supported journey without undocumented help.
- **SC-005:** Custom CA trust and adapter credentials work without image rebuild or secret disclosure.
- **SC-006:** The final evidence bundle passes every non-waived criterion in spec 009 and contains
  zero planted canary values.

## Assumptions

- The MVP supports one-host Compose deployment, not Kubernetes or high availability.
- A supported registry and release publication location are selected before implementation planning.
- The MVP release-duration floor is under one hour.
