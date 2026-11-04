# Alignment Check: spec against the source brief

## Source

The source is the inline idea brief passed to `speckit-specify` on 2026-11-04. It came out
of the grilling interview with the product owner and contains:

- 5 journeys;
- FR-001 to FR-015;
- SC-001 to SC-005;
- governance gates, assumptions and out-of-scope items.

No URL was given.

## Verdict

⚠️ MINOR DRIFT (proceeding)

## Findings

| Severity | Category | Brief reference | Spec reference | Description |
|---|---|---|---|---|
| Minor | Changed | Journey P1 lists `branch` among the fields of each run | US1 scenario 1; Assumptions | The run list does not show the destination branch. Research R1 found that a run does not store its branch: the branch exists only in the saved plan and the Prefect parameters. The deferral and its reason are recorded in the spec's Assumptions and in critique P1. |
| Minor | Added | FR-011 (CLI commands) | FR-015 | `runs cancel` requires `--yes` or an interactive confirmation. The constitution requires this ("New mutating behavior MUST ship behind explicit flags"), and critique E3 raised it. |
| Minor | Added | FR-012 (version summary fields) | Assumptions; data model | Versions registered before this change report an empty registering actor and reason, because that data was never stored. Research R5 records the decision not to backfill it. |
| Info | Added | FR-013 (pagination) | Contract `http-api.md` | A malformed cursor, or a cursor reused with other filters, is refused with `422 invalid-cursor`. This makes FR-004 enforceable, and the brief does not contradict it. |
| Info | Added | FR-014 (configuration name) | Data model | The configuration summary also includes `latest_version`, so the CLI can show it without a second call. This is an expansion of detail. |

Every brief requirement maps to a spec requirement:

| Brief | Spec |
|---|---|
| FR-001 to FR-006 | FR-001 to FR-006 |
| FR-007 and FR-008 | FR-010 to FR-012 |
| FR-009 and FR-010 | FR-013 and FR-014 |
| FR-011 | FR-015 and FR-016 |
| FR-012 | FR-007 |
| FR-013 | FR-008 |
| FR-014 | FR-009 |
| FR-015 | FR-017 |
| Documentation list | FR-018 and FR-019 |

All five success criteria and all out-of-scope items are carried over unchanged. The open
question about which JPD card owns the work is kept.

## Action

Proceed. No remediation pass was needed. The branch deferral is the one change the product
owner should confirm.
