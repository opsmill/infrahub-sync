# Alignment check: spec.md against the idea brief

**Date**: 2026-11-09

## Source

The idea brief "Read state for each side of a Plan", grilled with the user on 2026-11-09 and marked "Ready for next
step". It was given inline to `speckit-opsmill-prep`. The prep resolved its two open questions: the comparison and
plan-write steps (P1b) are out of v1, and the UI refresh interval stays an assumption under SC-001.

## Verdict

⚠️ MINOR DRIFT (proceeding)

## Findings

| Severity | Category | Brief reference | Spec reference | Description |
| --- | --- | --- | --- | --- |
| Minor | changed | FR-009, SC-003 | FR-009, SC-003, Edge Cases | The brief shows every non-final side of an ended execution as "not started" or "interrupted". The spec keeps that rule for failed and stopped executions, and returns no read state for a non-final side of a **completed** execution, because a completed execution read both sides and only the end write was lost (critique M2). |
| Minor | changed | P2, Key Entities ("the latest execution") | User Story 2, FR-012, Key Entities | The brief shows the latest execution's read state. A registered run records `verify` and `apply` as later executions, so the spec shows the latest `plan` or `sync` execution instead (critique M1). The intent, keeping the Plan's read state visible, is unchanged. |
| Minor | added | Key Entities, P2, the redaction rule of the brief's edge cases | FR-011, FR-012, FR-013 | Three requirements make explicit what the brief decided in other sections: the read state belongs to one execution, `runs show` prints it, and it holds no secret or exception text. |
| Minor | changed | Governance gates ("an optional JSON column") | Assumptions; plan research R3 | The plan stores one column for each side instead of one column, so the two read threads never write the same column. The gate the user accepted (an optional, additive change to the stored execution) is the same. |
| None | dropped | P1b | Out of Scope | Resolved open question: out of v1, as the brief allowed. |

No requirement, success criterion or out-of-scope item of the brief is missing from the spec.

## Action

Proceed. The changes come from the critique and from the code (`verify` and `apply` share the run), and keep the
brief's intent. The spec's assumption about the stored field was reworded to match the plan.
