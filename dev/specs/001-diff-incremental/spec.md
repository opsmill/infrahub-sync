# Feature Specification: Preserve incremental extraction after diff

**Feature Branch**: `fix/403-diff-breaks-incremental`

**Created**: 2026-11-09

**Status**: Ready

**Input**: Fix issue #403: a diff run must not prevent a subsequent incremental sync from using the latest successful run with cursors; full-extraction fallbacks must explain why. Scope is shared cache and engine code, tests, and incremental documentation; adapters are excluded.

## User Scenarios & Testing

### User Story 1 - Keep incremental sync after diff (Priority: P1)

An operator previews changes between syncs and retains incremental extraction on the next sync.

**Why this priority**: Previewing changes should not cause unnecessary full destination reads.

**Independent Test**: Use synthetic adapters and a temporary cache to run sync, diff, then incremental sync; verify cached rows and changed rows are loaded without a full read.

**Acceptance Scenarios**:

1. **Given** a successful sync with cursors and a newer diff without cursors, **When** incremental sync runs, **Then** it uses the successful sync baseline.
2. **Given** multiple cursorless diffs and failed runs, **When** the baseline is selected, **Then** they do not displace the newest successful cursor-bearing run.
3. **Given** a successful dry run with cursors, **When** incremental sync runs, **Then** that run remains eligible.

### User Story 2 - Explain full reads (Priority: P2)

An operator can determine why an incremental request reads a side or kind in full.

**Why this priority**: Silent fallback makes performance regressions difficult to diagnose.

**Independent Test**: Capture logs for missing cursors and unsupported cursor tiers and assert the reason, side, and resource are present.

**Acceptance Scenarios**:

1. **Given** an otherwise eligible baseline missing a kind cursor, **When** that kind loads, **Then** a reason is logged before the full read.
2. **Given** an adapter without cursor support, **When** that kind loads, **Then** a reason identifies the unsupported tier.
3. **Given** a global full-read gate, **When** it fails, **Then** the existing reason remains visible.

### Edge Cases

- Empty or absent cache; no successful cursor-bearing run: full extract with a reason.
- Failed or unreadable run metadata: preserve existing skip behavior.
- Partial or empty cursor sidecar: log per-kind missing cursor and use a full read.
- Changed schema, forced full extract, or cadence reached: preserve existing safeguards.

## Requirements

### Functional Requirements

- **FR-001**: Baseline selection MUST choose the most recent successful cursor-bearing run and skip cursorless runs, including diffs.
- **FR-002**: Successful cursor-bearing dry runs MUST remain eligible; failed runs MUST remain excluded.
- **FR-003**: Every existing per-kind full-read fallback MUST log an `Incremental disabled: ...` reason with side and resource context.
- **FR-004**: Existing global fallback reasons and safety gates MUST remain intact.
- **FR-005**: Tests MUST cover baseline selection, the sync → diff → incremental sync sequence, and fallback logging using synthetic local data only.
- **FR-006**: Incremental extraction documentation MUST describe baseline eligibility and fallback reasons.
- **FR-007**: Adapter implementations, cache file formats, and CLI flags MUST remain unchanged.

### Key Entities

- **Run**: Status, chronological identifier, schema fingerprint, snapshots, and optional cursors.
- **Cursor**: Per-side and per-kind extraction position and capability tier.

## Success Criteria

### Measurable Outcomes

- **SC-001**: Interleaving one or more previews causes zero extra full reads for an eligible incremental kind.
- **SC-002**: All tested full-read fallback cases expose an actionable reason in logs.
- **SC-003**: Existing cache and engine tests pass together with the new regressions.

## Assumptions

- Existing chronological run-directory ordering remains authoritative.
- Cursor presence determines baseline eligibility; existing cursor parsing remains unchanged.
- No live infrastructure is needed for validation.
