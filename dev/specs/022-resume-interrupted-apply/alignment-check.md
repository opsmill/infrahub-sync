# Alignment check: idea brief against spec.md

**Source**: the idea brief "Resume an interrupted apply", grilled with the user on 2026-11-12 (inline input to the
prep run, kept outside the repository). Every decision in it was confirmed by the user.

**Verdict**: ⚠️ MINOR DRIFT (proceeding)

## Requirement mapping

| Brief | Spec |
| --- | --- |
| FR-001 save the marker (500 operations, 30 seconds, kind end, failure, end) | FR-001 |
| FR-002 per-kind committed, uncertain, not reached | FR-004 |
| FR-003 resume on the same run with apply's conditions | FR-009, FR-010, FR-011 |
| FR-004 actor and optional reason | FR-014 |
| FR-005 refuse a run that is not interrupted, or withdrawn | FR-013 |
| FR-006 stronger `verify` with per-operation report | FR-005, FR-006, FR-008 |
| FR-007 `verify` required; the report does not block | FR-007, FR-012 |
| FR-008 `sync` runs | FR-017 |
| FR-009 marker lags, never leads; 5-minute stop | FR-002, FR-003 |
| SC-001 to SC-005 | SC-001 to SC-005, unchanged |
| Out of scope (7 items) | Out of Scope, unchanged |

## Findings

| Severity | Category | Brief reference | Spec reference | Description |
| --- | --- | --- | --- | --- |
| Minor | changed | FR-006 "operations after the last saved position (at most N+1)" | FR-005, "Uncertain operations" | The window is the operations through the failing operation when it is known. When a killed worker leaves it unknown, the window is the next 500 operations, or every remaining operation if the last save failed. In the normal case this matches the brief's "at most N+1"; it is larger only after save failures. Added by critique E3 so `verify` never understates what may have been written. |
| Minor | added | — | FR-006 | `verify` output says that a resume overwrites operations reported as "differs from the plan" (critique P1). It makes the brief's edge case "values changed → overwritten" visible to the operator. |
| Minor | added | — | FR-005 | `verify` is accepted on an interrupted run. The store refuses it today (critique E1), and the brief's FR-007 cannot be met without this. |
| Minor | added | — | FR-016 | A finished resume clears `reconciliation_required` and the run ends applied. This is a consequence of resuming on the same run. |
| Minor | added | — | FR-018 | Only the run's actor or an administrator may resume, as for every other stage. |

No requirement, success criterion or out-of-scope item from the brief is missing, dropped or contradicted.

## Action

Proceed. The additions are clarifications the brief's own decisions require. The one semantic change (the
`verify` window after a killed worker) is worth naming to the user in the completion summary.
