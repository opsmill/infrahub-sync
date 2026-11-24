# Critique Report: Manage schedules from the web interface (revision)

**Date**: 2026-11-19

**Spec**: [spec.md](../spec.md) | **Plan**: [plan.md](../plan.md)

**Scope**: The maintainer's additions: the cron helper, the start-time preview, the schedule
history, and the two Sync API additions they need. The first critique is
[critique-20261119.md](critique-20261119.md).

**Verdict**: ⚠️ PROCEED WITH UPDATES

## Executive Summary

The additions answer the two questions the first version could not: "when will this run?" before
saving, and "why did it not run?" afterwards. Computing the preview on the server is the right
choice: a check against `schedule_cadence` showed that Prefect starts the nonexistent 02:30 of
28 March 2027 in `Europe/Paris` at 00:30 UTC (01:30 local time), a result a browser library would
likely not reproduce.

One must-address item was found and fixed before this report: the preview was planned as a `GET`
with the cron expression in the query string. The Sync API's uvicorn access log writes the path
and query string, so a credential typed into the cron field by mistake would reach the log before
the route refuses it. Spec 021 FR-025 forbids that. The route is now `POST /schedules/next-starts`
with a JSON body and no retry key, like configuration validation.

## Product Lens Findings

### Problem Validation

| ID | Severity | Finding | Suggestion |
|----|----------|---------|------------|
| P1 | 💡 | The additions come from the maintainer's review, with GitLab and GitHub as references. GitLab's pipeline schedules offer common cadences and a custom cron expression; GitHub was not checked. | Recorded in research R7. No change. |

### User Value Assessment

| ID | Severity | Finding | Suggestion |
|----|----------|---------|------------|
| P2 | 💡 | The history is P2, the same as pause and create. It is the only way to see a skipped or missed start, which leaves no run. | Keep P2; the plan builds it right after the read-only stories (order step 6). |

### Alternative Approaches

| ID | Severity | Finding | Suggestion |
|----|----------|---------|------------|
| P3 | 💡 | A description in words of any cron expression (as some tools show) would need a parser whose reading can differ from Prefect's. | Kept out of scope; the helper describes its own forms and the preview shows the rest. No change. |

### Edge Cases & UX

| ID | Severity | Finding | Suggestion |
|----|----------|---------|------------|
| P4 | 💡 | `0 0 30 2 *` has no start at all, and `schedule_cadence.validate` refuses it. The first draft said the preview would list no start. | Fixed before this report: the preview shows the refusal, as a save does; `0 0 29 2 *` is the example of an expression with fewer than five starts. |
| P5 | 💡 | Day-of-week `7` means Sunday in some cron dialects. Reading it back as "Every week" could change what the person typed. | data-model: `7` stays "Custom". No change. |

### Success Measurement

| ID | Severity | Finding | Suggestion |
|----|----------|---------|------------|
| P6 | 💡 | SC-007 (preview equals the next start) is checkable by a test, not only by hand. | Add a pytest case that compares the preview's first start with `next_start_at` of a schedule created with the same values. Applied to tasks. |

## Engineering Lens Findings

### Architecture Soundness

| ID | Severity | Finding | Suggestion |
|----|----------|---------|------------|
| E1 | 🎯 | The cron expression in a `GET` query string reaches uvicorn's access log (`"GET /api/… HTTP/1.1" 200` lines in the `sync-api` container). | Fixed: `POST /schedules/next-starts` with a JSON body, no retry key (contracts/http-api.md, research R12, plan). |
| E2 | 💡 | `_refuse_secret_values` in `schedules.py` is called on the create and change paths. If it records a refusal event, the preview cannot call it as is. | The plan states "without recording an event"; the task must check whether it calls `_refusal` and, if so, use the same check with `_error`. Applied to tasks. |

### Failure Mode Analysis

| ID | Severity | Finding | Suggestion |
|----|----------|---------|------------|
| E3 | 💡 | A slow preview answer could replace a newer one. | Queries keyed by `cron` and `timezone`; the form shows only the query of the current values (R12). No change. |
| E4 | 💡 | History paging by offset shifts when new events arrive. | `until` fixed at the first read; the first page is read again without `until` and merged by `event_id` (contracts/http-api.md). No change. |

### Security & Privacy

| ID | Severity | Finding | Suggestion |
|----|----------|---------|------------|
| E5 | 💡 | Every authenticated account can read every schedule's events, including the reasons other people gave. The CLI's `events` command already allows the same. | No change; stated in the spec's assumptions. |

### Performance & Scalability

| ID | Severity | Finding | Suggestion |
|----|----------|---------|------------|
| E6 | 💡 | A `desc` sort on `audit_events` filtered by a related resource may need an index on `created_at`. | The task for R13 checks the query plan on SQLite and PostgreSQL with the existing indexes and records the result in the pull request. Applied to tasks. |
| E7 | 💡 | A preview is computed on each pause in typing. `count` is at most 20 and the window 5 years; one `CronSchedule` computation, no input or output. | No change. |

### Testing Strategy

| ID | Severity | Finding | Suggestion |
|----|----------|---------|------------|
| E8 | 💡 | The daylight-saving behavior is the reason for a server preview; it must have a test. | pytest case `30 2 28 3 *` in `Europe/Paris` (R9). No change. |

### Operational Readiness

| ID | Severity | Finding | Suggestion |
|----|----------|---------|------------|
| E9 | 💡 | Two API additions change the OpenAPI document; the web interface's generated types must be refreshed from a Sync API built from this branch. | Plan order step 1. No change. |

### Dependencies & Integration

| ID | Severity | Finding | Suggestion |
|----|----------|---------|------------|
| E10 | 💡 | `order` on `GET /events` touches the store protocol; every implementation (and test double) must accept it with the default `asc`. | Stated in R13. No change. |

## Cross-Lens Insights

| ID | Lens | Severity | Category | Finding | Suggestion |
|----|------|----------|----------|---------|------------|
| X1 | Both | 💡 | Trust × Risk | A preview that disagrees with the real start would cost more trust than having no preview. | Server computation with the scheduler's code; SC-007 test (P6). |

## Applied changes

- contracts/http-api.md, research R12, plan: the preview route is `POST /schedules/next-starts`
  with a JSON body and no retry key (E1).
- spec edge case, contracts/http-api.md, quickstart 5d: an expression with no start is refused
  (P4).
- tasks: SC-007 test (P6), `_refuse_secret_values` check (E2), query plan check (E6).
