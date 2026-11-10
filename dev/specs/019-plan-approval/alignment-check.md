# Alignment check: idea brief against the specification

**Source**: the inline idea brief "Approve a plan without applying it, and a `verify` that checks the destination"
(Status: Ready for next step), grilled with the user on 2026-11-10. No URL source.

**Verdict**: ⚠️ MINOR DRIFT (proceeding)

## Findings

| Severity | Category | Brief reference | Spec reference | Description |
| --- | --- | --- | --- | --- |
| Minor | changed | Edge Cases, "Withdrawing an approval: … the operator cancels the run with `runs cancel`" | Edge Cases "Withdrawing an approval"; Open Questions | The brief's withdrawal path does not exist: `cancel_run` refuses a run with no execution in progress (`no-active-execution`, `execution-terminal`). The spec records "no withdrawal in v1" and raises it as an open question for the user. The decision "no separate withdrawal command" is kept. |
| Minor | added | — | FR-017, `GET /me approval_required` | Operators can read whether approval is required, so a refusal can be predicted. Small, additive, follows from FR-005/FR-006 of the brief. |
| Minor | added | — | FR-004 | Approving refuses a plan whose recorded checksum check failed or that cannot be read. A safety precondition the brief left implicit; it does not add a check against the source or destination. |
| Minor | added | — | contracts (approve response `plan_summary`) | From the critique (P1): `approve` shows the counts of what was approved. Output only. |
| Minor | changed | Open Questions, name of the setting | FR-013 | Resolved as `INFRAHUB_SYNC_REQUIRE_APPROVAL`, service-wide only, following the service's existing `INFRAHUB_SYNC_` variables. Per-configuration setting moved to Out of Scope. |
| Minor | changed | Open Questions, approval in run events | Assumptions | Spec 017's events are not on the base; the approval is visible on the run and in the audit history, and an `approved` event is a follow-up. |

No requirement, journey or success criterion of the brief is missing or softened:

- P1 → User Story 1; P2 → User Story 2; P3 → User Story 3.
- FR-001 to FR-009 of the brief → FR-001, FR-002, FR-009, FR-014, FR-015, FR-016, FR-005, FR-006, FR-007 of the spec.
- SC-001 to SC-005 carried over unchanged.
- Out of scope (four-eyes, drift since plan, withdrawal, expiry, web UI) carried over; `apply` arguments unchanged
  added explicitly.

## Action

Proceed. The withdrawal question is surfaced to the user in the Completion summary. No remediation pass was needed
(0 of 2 used).
