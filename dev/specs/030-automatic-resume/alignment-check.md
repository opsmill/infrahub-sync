# Alignment check: spec 030 against the idea brief

## Source

The idea brief "Automatic resume of an interrupted apply", grilled with the user on 2026-11-23, passed inline as the
input of the preparation. Every decision in it was confirmed by the user. It continues
[INFP-650](https://opsmill.atlassian.net/browse/INFP-650).

## Verdict

⚠️ MINOR DRIFT (proceeding)

Every requirement, success criterion, out-of-scope item, dependency and open question of the brief is in
[spec.md](./spec.md). Three differences narrow or add behavior; each one follows from the brief's own rules or from the
critique, and none changes a decision the user made.

## Findings

| Severity | Category | Brief reference | Spec reference | Description |
| --- | --- | --- | --- | --- |
| Minor | changed | FR-001 ("rule 6 (Prefect reports the flow run as ended)") | FR-001, Background | Rule 6 counts only when Prefect reports `crashed`. `failed` is an error the worker raised but could not record, which the brief's FR-002 excludes (critique E1). This narrows FR-001 to serve FR-002. |
| Minor | added | FR-007 (setting off by default) | FR-004 | While the setting is off, Sync records no decision and no event. The brief's journeys describe sites with the setting on; this keeps sites that did not turn it on unchanged (critique P1). |
| Minor | added | not in the brief | FR-019, Edge Cases | A person's `verify` or `runs resume` while Sync waits for a live worker ends the decision as "a person took over". The brief's edge case covered only a pending resume, where the existing rule refuses the second request; the spec keeps that rule while the check or the resume runs (critique P2). |
| Minor | added | FR-006 (approval check, nothing added) | FR-008 | The automatic resume reuses the left-out confirmation that admitted the interrupted write (spec 007). The brief did not mention it; without it, `runs resume` refuses any plan that left out records. |

Requirements mapped one to one (brief → spec): FR-001 → FR-001, FR-002 → FR-002, FR-003 → FR-012, FR-004 → FR-005,
FR-005 → FR-006, FR-006 → FR-008, FR-007 → FR-004, FR-008 → FR-009, FR-009 → FR-010, FR-010 → FR-011, FR-011 →
FR-015, FR-012 → FR-017, FR-013 → FR-003, FR-014 and FR-015 → FR-018, FR-016 → FR-007, FR-017 → FR-014. SC-001 to
SC-005 are unchanged. The constitution amendment and the new ADR are FR-020.

## Action

Proceed. No remediation pass.
