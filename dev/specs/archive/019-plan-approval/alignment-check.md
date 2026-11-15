# Alignment check: idea brief against the specification

**Source**: the inline idea brief "Approve a plan without applying it, and a `verify` that checks the destination"
(Status: Ready for next step), grilled with the user on 2026-11-10. No URL source.

**Verdict**: ⚠️ MINOR DRIFT (proceeding)

## Findings

| Severity | Category | Brief reference | Spec reference | Description |
| --- | --- | --- | --- | --- |
| Resolved | changed | Edge Cases, "Withdrawing an approval: … the operator cancels the run with `runs cancel`" | User Story 4; FR-019 to FR-022; Edge Cases | First recorded as drift: `cancel_run` refuses a run with no execution in progress, so the brief's path did not exist. The user then chose (2026-11-10) to make it exist: `runs cancel` now cancels a finished, unapplied plan, and `approve` and `apply` refuse a cancelled run. The spec now matches the brief's decision. |
| Minor | changed | Success Criteria SC-003 | SC-003, FR-020 | One deliberate exception to "apply behaves as before": `apply` refuses every run in phase `cancelled`, including a run whose `verify` execution was cancelled, which today can still be applied. |
| Minor | added | — | FR-017, `GET /me approval_required` | Operators can read whether approval is required, so a refusal can be predicted. Small, additive, follows from FR-005/FR-006 of the brief. |
| Minor | added | — | FR-004 | Approving refuses a plan whose recorded checksum check failed or that cannot be read. A safety precondition the brief left implicit; it does not add a check against the source or destination. |
| Minor | added | — | contracts (approve response `plan_summary`) | From the critique (P1): `approve` shows the counts of what was approved. Output only. |
| Minor | changed | Open Questions, name of the setting | FR-013 | Resolved as `INFRAHUB_SYNC_REQUIRE_APPROVAL`, service-wide only, following the service's existing `INFRAHUB_SYNC_` variables. Per-configuration setting moved to Out of Scope. |
| Minor | changed | Open Questions, approval in run events | Assumptions | Spec 017's events are not on the base; the approval is visible on the run and in the audit history, and an `approved` event is a follow-up. |

No requirement, journey or success criterion of the brief is missing or softened:

- P1 → User Story 1; P2 → User Story 2; P3 → User Story 3.
- FR-001 to FR-009 of the brief → FR-001, FR-002, FR-009, FR-014, FR-015, FR-016, FR-005, FR-006, FR-007 of the spec.
- SC-001 to SC-005 carried over unchanged.
- Out of scope (four-eyes, drift since plan, a separate withdrawal command, expiry, web UI) carried over; `apply` arguments unchanged
  added explicitly.

## Action

Proceed. The withdrawal question was answered by the user on 2026-11-10 and is resolved in the spec (User Story 4).
No remediation pass was needed (0 of 2 used).
