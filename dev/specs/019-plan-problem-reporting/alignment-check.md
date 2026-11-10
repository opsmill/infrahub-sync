# Alignment check: Plan problem reporting

**Date**: 2026-11-11

## Source

The source PRD is old specification 009 (`tmp-sync-specs/009-plan-problem-reporting/spec.md`,
35 FRs and 10 SCs, with `ad-12-amendment.md` and its contracts) plus the caller's decisions 1 to
11 of 2026-11-11, which [spec.md](spec.md) lists under "Decisions that change old specification
009". Requirements that the gap report of 2026-11-10 marks DELIVERED (old FR-004, FR-013) are
context, not drift. Changes made by decisions 1 to 11 are not drift.

## Verdict

MINOR DRIFT (proceeding). One dropped constraint was found and restored in `spec.md` during this
check. The remaining differences are small and recorded below.

## Findings

| Severity | Category | PRD reference | Spec reference | Description |
| --- | --- | --- | --- | --- |
| Significant, fixed | dropped | Old FR-030: "FR-014 to FR-019 MUST NOT ship before the product owner signs off this change" | Dependencies | The ship gate on product sign-off was missing. Restored for FR-014 to FR-019 (record 0021) and FR-005 to FR-012 (record 0020 and AD-12) |
| Minor | added | Old FR-025 failure type list | FR-014 | Three failure types added: `permission_missing`, `lineage_refused`, `destination_schema_unavailable`. They name refusals that specs 014 and 017 added after old 009; without them those failures would be `internal_error`. Extension of decision 1 |
| Minor | changed | Old FR-019: Infrahub's message only for a rejected write | FR-019 | The allowlist also keeps the permission or message Infrahub returns with a 403, which spec 014 records today. Keeping current behavior, not a new exposure |
| Minor | changed | Old contract: 409 `plan-format-not-applicable` for a format 2 or 3 plan | contracts/sync-api.md, research R9 | Uses the existing `PlanFormatApplyUnsupportedError` refusal instead of a new code, in line with decision 4's preference for existing codes. Format 3 apply is open question 2 |
| Minor | added | not in old 009 | Out of Scope | States that records removed by a configured filter are not counted as left out (Hivemind note of 2026-10-20). Scope statement only |
| Minor | added | not in old 009 | FR-023 | A `sync` run that stopped after its plan can be applied once under its existing write admission (critique E1). Needed for old FR-012 to work on the current store |
| None | changed by decisions | Old FR-025, FR-014, FR-015, FR-022, FR-029, FR-030, FR-031, CLI contract | FR-012, FR-014, FR-020, FR-023, FR-027, FR-028, FR-031, FR-032, FR-036, Dependencies | Decisions 1 to 10 |

Every other old FR and all ten old SCs are in the spec with the same meaning; see the
traceability table in [spec.md](spec.md). SC-011 is new, for decision 9.

## Action

Proceed. No remediation pass was needed after the sign-off gate was restored. Remediation passes
used: 0.
