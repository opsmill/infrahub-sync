# Alignment check: Manage schedules from the web interface

## Source

- The inline brief given to the preparation pipeline on 2026-11-19, after the maintainer's request
  "let's build the spec to have the scheduler page in the UI as well". No URL was given.
- The maintainer's review of the first version, on 2026-11-19: "I think we need to preview start
  time and to have some kind of helper for the cron notation, from memory gitlab or GitHub did
  that. We need the schedule event history."

## Verdict

⚠️ MINOR DRIFT (proceeding)

## Findings

| Severity | Category | Source reference | Spec reference | Description |
| --- | --- | --- | --- | --- |
| Minor | changed | Brief: "Out of scope: changes to the scheduling engine or the API unless a gap blocks the page" | Decision 6; FR-031, FR-032 | Two Sync API additions. Both are needed by the review's requests: the preview must use the scheduler's code (a browser computation can disagree at a daylight-saving change), and the history must be read newest first. This matches the brief's exception "unless a gap blocks the page". |
| Minor | added | (none) | FR-011, User Story 4 scenario 1 | A schedule line and "Create schedule" on the configuration page; recorded as Decision 1. |
| Minor | added | (none) | plan.md, research R9; tasks T001, T002 | Vitest and a `pnpm test` CI step for the frontend; a test tool, not a requirement. |
| Minor | added | (none) | FR-024; tasks T042, T043 | User guide and API reference updates, required by `AGENTS.md` for user-visible changes. |
| None | covered | Review: "preview start time" | User Story 4 scenarios 9 to 13; FR-028 to FR-031; FR-030 on the schedule page | Five next starts, from the Sync API, before saving and on the schedule page. |
| None | covered | Review: "some kind of helper for the cron notation, from memory gitlab or GitHub did that" | Decision 3; FR-025 to FR-027; research R7 | Common cadence forms that build the expression, labelled fields for a custom expression. GitLab's pipeline schedules were the reference; GitHub was not checked. |
| None | covered | Review: "We need the schedule event history" | User Story 7; FR-033, FR-034 | Newest first, with a switch for run events. |
| None | covered | Brief: list, view (state, cron cadence, time zone, next start, owner, last run, consecutive failures, stop reason and the action it needs) | User Story 1; FR-002 to FR-007 | |
| None | covered | Brief: create, change, pause, resume, delete, take over | User Stories 3 to 6; FR-012 to FR-023 | |
| None | covered | Brief: same permissions as the Sync API | FR-012 to FR-014 | |
| None | covered | Brief: follow from a schedule to its runs | User Story 2; FR-008 to FR-010 | |
| None | covered | Brief: reuse the HTTP contract and frontend patterns; account naming rule | research R1 to R5; FR-007 | |

## Action

Proceed. The API additions are the gap the brief allowed for, and the review asked for the
features that need them. No remediation pass was run.
