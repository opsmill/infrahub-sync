# Implementation Plan: Sync run events and logs, read from the Sync API

**Branch**: `017-sync-run-events-logs` | **Date**: 2026-11-06 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `dev/specs/017-sync-run-events-logs/spec.md`

## Summary

The frontend team and operators read a run's timeline, its log and an actionable failure cause from the Sync API.
This plan:

- extends the stored audit event with a dotted type, a primary resource and related resources, writes each event in
  the transaction of the decision it records, adds events for stage results, and serves them on `GET /events`;
- reads the run's Prefect log lines through the Sync API on `GET /runs/{run_id}/logs`, keeping only the lines that
  Sync wrote;
- gives every known failure class a stored cause and next action, adds a specific failure for a missing
  destination branch, and ends a known failure without a traceback in Sync's lines;
- adds `infrahub-sync runs logs` and `infrahub-sync events`.

Each change is delivered test-first. The design decisions are in [research.md](./research.md).

## Technical Context

**Language/Version**: Python 3.10–3.13. The Sync service runs on 3.11–3.13.

**Primary Dependencies**:

- Prefect 3.8.6: the client's `read_logs` and `read_flow_run`, and `prefect.states.Failed`;
- FastAPI and pydantic, through `infrahub_sync/service/models.py` and `infrahub_sync/client/models.py`;
- Typer, for the CLI;
- infrahub-sdk 1.23.2 (installed dist-info): `BranchNotFoundError`.

No new dependency.

**Storage**: The product store (SQLite and PostgreSQL, `infrahub_sync/product_store/store.py`). New columns on
`audit_events`, a new `audit_event_resources` table and a migration (data-model.md). No log store.

**Testing**: pytest unit tier (`uv run invoke tests.tests-unit`). New test files:

- `tests/service/test_events_route.py`
- `tests/service/test_run_logs_route.py`
- `tests/service/test_event_transactions.py`
- `tests/service/test_failure_without_traceback.py`
- `tests/product_store/test_audit_event_migration.py`

Extended files: `tests/service/test_http_api.py`, `tests/service/test_flow_and_prefect.py`,
`tests/service/test_config_routes.py`, `tests/cli/test_client_commands.py`, and the capabilities tests for the
schema accessor (`tests/configuration/test_schema_validation.py`).

**Target Platform**: the Sync API and worker containers (Linux), and the CLI on any host.

**Project Type**: a Python package with a CLI, an HTTP service and a Prefect worker.

**Performance Goals**: A new log line is readable within 5 seconds (SC-003; research R8). A log request reads every
line of the run's flow runs (research R9); no volume figure is known.

**Constraints**:

- No message names Prefect or its address (FR-006).
- Every recorded and served text is redacted, as today.
- Existing response fields and CLI fields do not change; new response fields are additions.
- A request rejected for a token creates no event (FR-009).

**Scale/Scope**: about ten source files, eight test files, a store migration, four documentation pages and
changelog fragments.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Check | Result |
| --- | --- | --- |
| I. Read-only and dry-run by default | The two routes and two commands only read. No new mutating path. | Pass |
| II. Sync idempotency and safety | Events join the decision's transaction (FR-014), so a decision is never recorded without its event, nor the reverse. A replayed request still writes a `replayed` event, as today. | Pass |
| III. Adapter symmetry | No adapter change. The Infrahub schema accessor gains one error arm. | Pass |
| IV. Type safety and explicit contracts | New pydantic resources for events and log lines; a typed `RunLogRead` result for the orchestration call. | Pass |
| V. Test discipline | Each requirement gets a failing test first; the Prefect client is faked in unit tests. | Pass |
| VI. Security, secrets, and input boundaries | Log lines are redacted before they reach Prefect (`RunLoggerBridge`), and the service flow's direct lines use the same redaction. Failure causes come from fixed templates and recorded identifiers. A Prefect log line is untrusted input: it is length-capped and stripped of control characters before it is served. | Pass |
| VII. Simplicity and maintainability | Logs reuse Prefect's storage; events reuse the audit table; polling, not streaming; no unit-of-work abstraction (research R2). | Pass |

Re-check after design: Pass. Governance gates crossed: database change (migration), API change (two routes,
richer failure text). No CI/CD or authentication change.

## Project Structure

### Documentation (this feature)

```text
dev/specs/017-sync-run-events-logs/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── events-route.md
│   ├── logs-route.md
│   └── cli-output.md
└── tasks.md
```

### Source Code (repository root)

```text
infrahub_sync/
├── cli.py                          # runs logs, events
├── client/
│   ├── client.py                   # list_events, read_run_logs
│   └── models.py                   # EventResource, EventPage, RunLogLine, RunLogPage
├── configuration/capabilities.py   # BranchNotFoundError -> reason "nobranch"
├── runtime_schema/
│   ├── errors.py                   # DestinationBranchNotFoundError, fixed causes per reason
│   └── worker.py                   # raise the subclass for "nobranch"
├── product_store/
│   ├── models.py                   # AuditEvent fields, EventFilters
│   └── store.py                    # columns, resources table, migration, event argument on decision writes, list_events
└── service/
    ├── app.py                      # GET /events, GET /runs/{run_id}/logs
    ├── config_routes.py            # config events with type and related resources
    ├── failure_cause.py            # causes for DestinationSchemaUnavailableError reasons
    ├── flow.py                     # Sync prefix on direct lines; ERROR line + Failed state; stage-result events
    ├── orchestration.py            # read_logs in the Orchestration protocol and PrefectOrchestration
    ├── run_log.py                  # new: Sync line filter, stage tagging, paging
    ├── serve.py                    # read_logs in _ClientPerCallOrchestration
    └── service.py                  # events in the decision writes; list_events; read_run_logs

docs/docs/                          # reference/sync-http-api, reference/cli, compose deployment page
changelog/                          # one fragment per user-visible change
```

**Structure Decision**: The single package layout stays. `run_log.py` sits next to `run_artifact.py` and
`failure_cause.py`, which hold the same kind of single-purpose logic.

## Failure exit for known error classes

`service_sync_run` today re-raises a sanitized copy of every failure (`flow.py:1066-1069`). After this change:

| Failure | Sync's lines | Flow function ends with |
| --- | --- | --- |
| Known class (`operator_cause` returns a `cause`) | One ERROR line: `<cause> Next step: <next_action>`, then the last log line | `return Failed(message=<same text>)` |
| Unknown class | One ERROR line: `<error_type>`, then the last log line | `raise sanitized`, as today |
| Refused claim | The existing ERROR line, with the Sync prefix | `return Failed(message=<same text>)`, so Prefect does not log the refusal a second time with a traceback |

The result dictionary that a successful stage returns does not change. `secrets.clear()` still runs after the
line is written.

Callers and tests that see the change (critique E3): a caller that runs the flow and waits for its result receives
an exception that Prefect raises from the `Failed` state, not Sync's sanitized error (research R1). Sixteen files
in `tests/service/` call `service_sync_run`; those with the most `pytest.raises` checks are:

- `tests/service/test_flow_and_prefect.py` (12)
- `tests/service/test_run_log_next_step.py` (9)
- `tests/service/test_service_worker.py` (1)

Which of these checks use a known error class was not checked. The first task of that group lists them by running
the unit tier against the new behaviour, and each one is updated to assert the returned `Failed` state. The
unknown-class path keeps its tests unchanged. The refused-claim tests in `tests/service/test_worker_claim.py` and
`tests/service/test_run_log_next_step.py` assert the returned `Failed` state.

## Messages that name Prefect today (FR-006)

The Sync API returns three error messages that name Prefect. Each is reworded to name "the orchestrator", and a
test pins the new text:

- `infrahub_sync/service/service.py:521` and `service.py:529`: "Prefect could not confirm the cancellation request";
- `infrahub_sync/service/service.py:726`: "Prefect could not confirm service submission (...)".

The refused-claim line (`infrahub_sync/service/flow.py:101-103`) stays, as FR-006 allows. The FR-006 test scans the
responses and CLI output of the unit tier's scenarios for the Prefect API address (`PREFECT_API_URL`'s host and
port) and for the words "open Prefect", "in Prefect" and "Prefect flow run".

## Store migration

The change adds nullable columns to `audit_events` and one table, on SQLite and PostgreSQL, through the store's
existing migration step (`_migrate_audit_events`, `infrahub_sync/product_store/store.py:837`). The existing insert
names its columns (`store.py:417`), so a release from before this change can still write events into a migrated
store; rolling back needs no reverse migration. Event order depends on the clocks of the `sync-api` and worker
containers agreeing (critique E2); the deployment page says so.

## Log reader

`GET /runs/{run_id}/logs` → `RunService.read_run_logs(run_id, stage, limit, offset)`:

1. Read the run (404 when missing). Take its orchestration entries, in attempt order, filtered by `stage`.
2. Call `orchestration.read_logs(flow_run_ids)`. A flow run that Prefect does not hold → 410 `log-expired`. Any
   Prefect client error or timeout → 503 `log-unavailable`.
3. Keep the lines that match the Sync prefix (research R3), split `source` and `message`, cap and clean the text.
4. Apply `offset` and `limit`; return `has_more` and the run's `phase`.

## Test-first order

Each requirement group is one red-green cycle, in this order, because the later groups use the earlier ones:

1. Event model and storage (FR-012, FR-014): columns, resources table, migration, event argument on decision writes.
2. Events from decisions and stage results (FR-012, FR-012a, FR-009): service, config routes, worker.
3. `GET /events` (FR-007, FR-008).
4. Failure cause and missing branch (FR-001, FR-002).
5. Sync prefix and failure exit without traceback (FR-003, FR-006 for log lines).
6. Log reader and `GET /runs/{run_id}/logs` (FR-004, FR-005, FR-010, FR-013).
7. CLI commands (FR-015, FR-016) and the FR-006 message scan.
8. Retention guard (FR-011), documentation and changelog (FR-017).

## Complexity Tracking

No constitution violation to justify.
