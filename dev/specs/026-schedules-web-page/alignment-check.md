# Alignment check: Manage schedules from the web interface

## Source

The inline brief given to the preparation pipeline on 2026-11-19, after the maintainer's request
"let's build the spec to have the scheduler page in the UI as well". No URL was given.

## Verdict

⚠️ MINOR DRIFT (proceeding)

## Findings

| Severity | Category | Brief reference | Spec reference | Description |
| --- | --- | --- | --- | --- |
| Minor | added | "follow from a schedule to the runs it started" | FR-011, User Story 4 scenario 1 | The spec adds a schedule line and a "Create schedule" button on the configuration page. The brief does not ask for it. It follows from "a configuration has at most one schedule" and is recorded as Decision 1. |
| Minor | added | (none) | plan.md, research R9; tasks T001, T002 | Vitest and a `pnpm test` CI step are added to the frontend. This is a test tool, not a requirement; it follows constitution principle V. |
| Minor | added | (none) | FR-024; task T032 | The user guide `docs/docs/orchestration.mdx` gets a section on the web interface. Required by `AGENTS.md` for user-visible changes. |
| None | covered | List, view (state, cron cadence, time zone, next start, owner, last run, consecutive failures, stop reason and the action it needs) | User Story 1; FR-002 to FR-007 | Every field the brief names is in FR-004 and the stop panel of FR-005. |
| None | covered | Create, change, pause, resume, delete, take over | User Stories 3 to 6; FR-012 to FR-023 | All six actions. |
| None | covered | Same permissions as the Sync API (owner versus administrator) | FR-012 to FR-014; data-model "Permissions" | Matches FR-012 and FR-026 of spec 021. |
| None | covered | Follow from a schedule to its runs (`trigger_type`, `schedule_id`) | User Story 2; FR-008 to FR-010 | Both directions. |
| None | covered | Reuse the spec 021 HTTP contract and the frontend patterns; account naming rule | research R1 to R5; FR-007; contracts/web-pages.md | No new route; account names only. |
| None | covered | Out of scope: changes to the scheduling engine or the API unless a gap blocks the page | Out of Scope; Decision 6; research R1 | No API change was needed. |

## Action

Proceed. The added items are small and documented as decisions; none changes a requirement of the
brief. No remediation pass was run.
