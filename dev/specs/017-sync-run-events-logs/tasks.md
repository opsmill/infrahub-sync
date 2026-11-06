# Tasks: Sync run events and logs, read from the Sync API

**Input**: Design documents from `dev/specs/017-sync-run-events-logs/`

**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md),
[data-model.md](./data-model.md), [contracts/](./contracts/), [quickstart.md](./quickstart.md)

**Tests**: Required. The feature is delivered test-first: every functional requirement has a test task that must
fail before its implementation task starts. Run one test file with `uv run pytest <file> -q -p no:cacheprovider`.
Run the offline tier with `uv run invoke tests.tests-unit`. Before each commit also run `uv run ruff check .`,
because `invoke lint` does not check `tests/` with Ruff.

**Order**: The phases follow the plan's test-first order (plan.md, "Test-first order"). The event store comes
first because both the timeline (US1) and the worker events need it. US3 (failure cause and the Sync line prefix)
comes before US2 (log reader), because the reader keeps only lines with the prefix that US3 adds to the service
flow's direct lines.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel (different files, no dependency on an unfinished task).
- **[Story]**: the user story in spec.md: US1 timeline, US2 log, US3 failure cause, US4 CLI.

## Phase 1: Setup

- [X] T001 Confirm the baseline: on branch `017-sync-run-events-logs`, run `uv run invoke tests.tests-unit` and `uv run ruff check .`, and record the pass count under a "Baseline" heading in `dev/specs/017-sync-run-events-logs/opsmill-implement-report.md` (create the file).

---

## Phase 2: Foundational - event storage (FR-012, FR-014)

**Purpose**: The stored event gains its type, primary resource and related resources, and the store can write an
event inside the transaction of a decision. Blocks US1 and the worker events.

### Tests (write first, must fail)

- [X] T002 [P] Write failing model tests in `tests/product_store/test_audit_event_migration.py`: `AuditEvent` accepts `event_type` matching `^infrahub_sync\.(run|config)\.[a-z_]+$`, `config_id`, `registry_version`, `branch` and a `related` tuple of `{kind, id}` items with kinds `account`, `config`, `config_version`, `branch`; it refuses any other kind and a malformed type (data-model.md, "Event").
- [X] T003 [P] Write failing migration tests in `tests/product_store/test_audit_event_migration.py` for SQLite, and for PostgreSQL behind the existing PostgreSQL marker (see `tests/product_store/postgresql_isolation.py`): a store created before the change, holding rows with `(operation, outcome)` pairs from data-model.md's table, opens after the change with `event_type` filled from that table, one `account` row per event in `audit_event_resources`, and `event_type` NULL for `refused-authentication` rows (research R6). Opening twice changes nothing.
- [X] T004 [P] Write failing transaction tests in `tests/service/test_event_transactions.py` (FR-014): make the event insert fail (a duplicate `event_id`) inside `complete_mutation(..., event=...)`, `commit_claimed_execution(..., event=...)` and `cancel_execution(..., event=...)`, and assert that the receipt, the run's terminal record and the cancellation are not saved either; with a valid event, both are saved.

### Implementation

- [X] T005 Extend `AuditEvent` with the new fields and validators, and add `EventFilters` (`run_id`, `config_id`, `event_types`, `related`, `since`, `until`) in `infrahub_sync/product_store/models.py`.
- [X] T006 Add the nullable columns to `audit_events`, the `audit_event_resources (event_id, kind, resource_id)` table with an index on `(kind, resource_id)`, and the backfill from data-model.md's table inside the existing migration step (`_migrate_audit_events`) for SQLite and PostgreSQL in `infrahub_sync/product_store/store.py`. Keep `_INSERT_AUDIT_EVENT` naming its columns (plan.md, "Store migration").
- [X] T007 Add a private `_insert_event(cursor, event)` that writes the event row and its resource rows with the given cursor, use it in `record_audit`, and add an optional `event: AuditEvent | None = None` argument to `complete_mutation`, `commit_claimed_execution` and `cancel_execution` (and to their `ProductProjection` wrappers, redacting the event as `record_audit` does) that inserts the event before `connection.commit()`, in `infrahub_sync/product_store/store.py`. T002–T004 pass.
- [X] T008 Add `list_events(filters, limit, offset) -> StorePage[AuditEvent]` to the run store protocol, the relational store and `ProductProjection` in `infrahub_sync/product_store/store.py`: order by `created_at`, then `event_id`; leave out rows whose `event_type` is NULL; filter `related` through `audit_event_resources`; `config_id` matches the column or a `config` related row.
- [X] T009 Write a failing test, then make it pass, for `list_events` filters and paging (each filter alone, `since`/`until` bounds, `offset` past the end, `has_more`) in `tests/product_store/test_audit_event_migration.py` and `infrahub_sync/product_store/store.py`.

**Checkpoint**: The store holds typed events and writes them in the decision's transaction.

---

## Phase 3: User Story 1 - The frontend shows a run's timeline from the Sync API (Priority: P1)

**Goal**: Each decision and each stage result records one typed event, and `GET /events` returns them
(FR-007, FR-008, FR-009, FR-012, FR-012a, FR-014; [contracts/events-route.md](./contracts/events-route.md)).

**Independent test**: Accept a plan, finish it, accept an apply, finish it; `GET /events?run_id=<run_id>` returns
`run.accepted`, `run.planned`, `run.accepted`, `run.applied` in order, each with its related resources.

### Tests for User Story 1 (write first, must fail)

- [X] T010 [P] [US1] Write failing tests in `tests/service/test_http_api.py` (FR-012, FR-014): an accepted plan writes `infrahub_sync.run.accepted` with primary resource the run and related `account`, `config`, `config_version` (`<config_id>@<version>`) and `branch`, in the same transaction as `complete_mutation`; a replayed request writes `run.replayed`; each `refused-*` outcome except `refused-authentication` writes `run.refused`; an orchestration failure writes `run.unavailable`; an accepted cancel writes `run.accepted` with `operation="cancel"` and the recorded cancellation writes `run.cancelled`.
- [X] T011 [P] [US1] Write failing tests in `tests/service/test_config_routes.py` (FR-012): `register-config` writes `infrahub_sync.config.registered`, a new version writes `infrahub_sync.config.version_registered`, a refusal writes `infrahub_sync.config.refused`, each with primary resource the configuration and the version as a related resource where one exists.
- [X] T012 [P] [US1] Write failing tests in `tests/service/test_flow_and_prefect.py` (FR-012a, FR-014): a successful plan, verify, apply and sync stage each write `run.planned`, `run.verified`, `run.applied`, `run.synced` through `commit_claimed_execution(..., event=...)`; a failed or interrupted stage writes `run.failed` with outcome `failed` or `ambiguous`; the event's actor is the actor of the receipt that holds the stage's flow run ID, else the run's actor (data-model.md).
- [X] T013 [P] [US1] Write failing route tests in `tests/service/test_events_route.py` (FR-007, FR-008, FR-009): `GET /events` with each query parameter of the contract, `limit`/`offset` paging with no event twice, time order, 422 for an invalid value, 401 without a valid token, and no event in the response for a request rejected for its token (while the stored `refused-authentication` record still exists, as spec 015 requires).
- [X] T014 [P] [US1] Write failing client tests in `tests/client/test_client.py` and `tests/client/test_models.py`: `SyncClient.list_events(...)` sends the contract's query parameters and parses `EventPage`; `iter_events` follows `offset` until `has_more` is false.

### Implementation for User Story 1

- [X] T015 [US1] In `infrahub_sync/service/service.py`, replace `_audit` with a builder that sets `event_type` from the operation and outcome (data-model.md table) and the related resources from the run, and pass the event to `complete_mutation` and the cancellation writes instead of calling `record_audit` after them; keep `record_audit` for refusals and unavailable answers; give `record_authentication_refusal` no `event_type`. T010 passes.
- [X] T016 [US1] In `infrahub_sync/service/config_routes.py`, build configuration events with type, `config_id`, version and related resources, and pass the accepted event to `complete_mutation`. T011 passes.
- [X] T017 [US1] In `infrahub_sync/service/flow.py`, build the stage-result event (type by stage and outcome, actor from the receipt that holds the flow run ID, else the run's actor) and pass it to `commit_claimed_execution` in `_commit_success` and `_record_failure`. Add the receipt lookup by flow run ID to `infrahub_sync/product_store/store.py` if none exists. T012 passes.
- [X] T018 [US1] Add `EventResource`, `EventPage` and the related-resource model in `infrahub_sync/service/models.py` and `infrahub_sync/client/models.py`, `RunService.list_events` in `infrahub_sync/service/service.py` (using `page_size_or_refuse` for `limit`), and the authenticated `GET /events` route in `infrahub_sync/service/app.py`. T013 passes.
- [X] T019 [US1] Add `list_events` and `iter_events` to `infrahub_sync/client/client.py`. T014 passes.

**Checkpoint**: The frontend reads a run's timeline from `GET /events`.

---

## Phase 4: User Story 3 - The reader acts on a failed stage without opening Prefect (Priority: P1)

**Goal**: Every known failure stores a cause and a next action; a missing branch names the branch and the fix; a
known failure writes one ERROR line and no traceback; every line Sync writes carries the Sync prefix; no API message
names Prefect (FR-001, FR-002, FR-003, FR-006).

**Independent test**: A plan on a missing branch: `GET /runs/<run_id>` holds a cause naming the branch and the fix;
the service flow's lines hold one ERROR line and no `Traceback`.

### Tests for User Story 3 (write first, must fail)

- [ ] T020 [P] [US3] Write failing tests in `tests/configuration/test_schema_validation.py` (FR-002): when `client.schema.all` raises `infrahub_sdk.exceptions.BranchNotFoundError`, the accessor raises `DestinationSchemaReadError` with `reason="nobranch"`; an SDK HTTP 400 on the schema endpoint maps to that error (pins research R5 and critique E9).
- [ ] T021 [P] [US3] Write failing tests in `tests/runtime_schema/` (new file `test_destination_branch_missing.py`) (FR-002): the worker raises `DestinationBranchNotFoundError`, a subclass of `DestinationSchemaUnavailableError`, whose `message` is "Branch `<branch>` does not exist in Infrahub." and whose `next_action` is "Create it, or start the run with `--branch <existing branch>`."
- [ ] T022 [P] [US3] Write failing tests in `tests/service/test_failure_cause.py` (new) (FR-001): `operator_cause` returns a `cause` and a `next_action` for `DestinationBranchNotFoundError` and for each `DestinationSchemaUnavailableError` reason (`timeout`, `unreachable`, `unauthorized`, `forbidden`, `credentials`, `unconfigured`, `rejected`); both are redacted and capped; no text names Prefect.
- [ ] T023 [P] [US3] Write failing tests in `tests/service/test_failure_without_traceback.py` (FR-003): run `service_sync_run` under the Prefect test harness with a stage that raises `DestinationBranchNotFoundError`; capture the run logger's records; exactly one ERROR record is written by Sync, it holds the cause and "Next step:", no Sync record contains `Traceback`, and the flow run ends `Failed` with the same text as its state message. An unknown error class still raises the sanitized error, as today.
- [ ] T024 [P] [US3] Write failing tests in `tests/service/test_run_log_next_step.py` (FR-004 prerequisite, FR-006): every line the service flow writes directly (the last log line, the refused-claim line, the "failure evidence could not be persisted" warning) starts with `infrahub_sync.service.flow | `.
- [ ] T025 [P] [US3] Write failing tests in `tests/service/test_http_api.py` (FR-006): the 503 messages for an unconfirmed submission and an unconfirmed cancellation name "the orchestrator" and not Prefect (plan.md, "Messages that name Prefect today").
- [ ] T026 [US3] Run the unit tier against T023's expectation and list in `dev/specs/017-sync-run-events-logs/opsmill-implement-report.md` every existing `pytest.raises` check on `service_sync_run` that uses a known error class (plan.md, "Failure exit for known error classes"); update each one in its test file to assert the returned `Failed` state.

### Implementation for User Story 3

- [ ] T027 [US3] In `infrahub_sync/configuration/capabilities.py`, catch `BranchNotFoundError` before the SDK base error and raise `DestinationSchemaReadError(..., reason="nobranch")`. T020 passes.
- [ ] T028 [US3] In `infrahub_sync/runtime_schema/errors.py` and `infrahub_sync/runtime_schema/worker.py`, add `DestinationBranchNotFoundError` with fixed `message` and `next_action`, and raise it for `reason == "nobranch"`; give `DestinationSchemaUnavailableError` a fixed cause and next action per reason. T021 passes.
- [ ] T029 [US3] In `infrahub_sync/service/failure_cause.py`, describe `DestinationSchemaUnavailableError` (and its subclass) in `operator_cause`. T022 passes.
- [ ] T030 [US3] In `infrahub_sync/service/flow.py`, add one helper that writes a line through the run logger with the prefix `infrahub_sync.service.flow | ` and redaction, and use it for every direct line (`flow.py:907`, `flow.py:982`, `flow.py:1054`, `flow.py:1071`). T024 passes.
- [ ] T031 [US3] In `infrahub_sync/service/flow.py`, for a failure whose recorded evidence has a `cause`, write one ERROR line `<cause> Next step: <next_action>` with the helper, write the last log line, and `return Failed(message=...)` after `secrets.clear()`; keep `raise sanitized` for other failures and `raise` for a refused claim. T023 and T026's updated tests pass.
- [ ] T032 [US3] In `infrahub_sync/service/service.py`, reword the messages at `service.py:521`, `service.py:529` and `service.py:726` to name "the orchestrator". T025 passes.

**Checkpoint**: A failed run names its cause and fix from the Sync API; Sync's lines carry the prefix.

---

## Phase 5: User Story 2 - The frontend shows a run's log from the Sync API (Priority: P1)

**Goal**: `GET /runs/{run_id}/logs` returns the run's Sync lines, paged and filtered by stage, with clear answers
for an expired log and an unreachable Prefect (FR-004, FR-005, FR-008, FR-010, FR-013;
[contracts/logs-route.md](./contracts/logs-route.md)).

**Independent test**: With a fake orchestration that returns Sync lines mixed with engine lines for two flow runs,
`GET /runs/<run_id>/logs` returns only the Sync lines, in order, each with its stage.

### Tests for User Story 2 (write first, must fail)

- [ ] T033 [P] [US2] Write failing tests in `tests/service/test_run_log.py` (new) for `infrahub_sync/service/run_log.py` (FR-004, FR-005): `sync_lines(entries, logs)` keeps only messages matching `^infrahub_sync(\.[A-Za-z0-9_]+)* \| `, splits `source` and `message`, tags `stage` and `attempt` from the orchestration entry, orders by attempt, timestamp and log ID, strips control characters and caps each message; `page(lines, offset, limit)` returns only lines after `offset` (read 5, add 3, `offset=5` returns 3) and `has_more`.
- [ ] T034 [P] [US2] Write failing tests in `tests/service/test_orchestration_logs.py` (new) for `PrefectOrchestration.read_logs` (FR-004, FR-010): with a fake Prefect client, it calls `read_flow_run` per flow run ID and `read_logs` with a `flow_run_id` filter sorted by timestamp in pages of 200 until a short page; a flow run that `read_flow_run` does not find returns an `expired` result; a client error or timeout raises the orchestration-unavailable error.
- [ ] T035 [P] [US2] Write failing route tests in `tests/service/test_run_logs_route.py` (FR-004, FR-005, FR-008, FR-010, FR-013): `GET /runs/{run_id}/logs` with `stage`, `limit`, `offset`; 404 `run-not-found`; 410 `log-expired`; 503 `log-unavailable` while `GET /runs/{run_id}` and `GET /events` still return 200; 401 without a valid token; a run with no orchestration entry returns `items: []`; `run_phase` is present; no error message names Prefect.
- [ ] T036 [P] [US2] Write failing client tests in `tests/client/test_client.py`: `SyncClient.read_run_logs(run_id, stage, limit, offset)` parses `RunLogPage` and maps 410 and 503 to the existing client error types.

### Implementation for User Story 2

- [ ] T037 [US2] Create `infrahub_sync/service/run_log.py` with the Sync line pattern, `sync_lines` and `page`. T033 passes.
- [ ] T038 [US2] Add `read_logs(flow_run_ids) -> RunLogRead` to the `Orchestration` protocol and `PrefectOrchestration` in `infrahub_sync/service/orchestration.py`, and to `_ClientPerCallOrchestration` in `infrahub_sync/service/serve.py`. T034 passes.
- [ ] T039 [US2] Add `RunLogLine` and `RunLogPage` to `infrahub_sync/service/models.py` and `infrahub_sync/client/models.py`, `RunService.read_run_logs` in `infrahub_sync/service/service.py` (plan.md, "Log reader"), and the authenticated `GET /runs/{run_id}/logs` route in `infrahub_sync/service/app.py`. T035 passes.
- [ ] T040 [US2] Add `read_run_logs` to `infrahub_sync/client/client.py`. T036 passes.

**Checkpoint**: The frontend reads a run's record, timeline and log in 3 requests (SC-002).

---

## Phase 6: User Story 4 - An operator follows a run's log and events from the CLI (Priority: P2)

**Goal**: `infrahub-sync runs logs` and `infrahub-sync events` (FR-015, FR-016;
[contracts/cli-output.md](./contracts/cli-output.md)).

**Independent test**: Against a fake client, `runs logs <run_id> --follow` prints each line once and exits when
`run_phase` is terminal.

### Tests for User Story 4 (write first, must fail)

- [ ] T041 [P] [US4] Write failing tests in `tests/cli/test_client_commands.py` (FR-015): `runs logs <run_id>` prints one tab-separated row per line; `--stage apply` passes the stage; `--follow` polls with `offset` equal to the lines printed, prints each line once, and exits 0 when `run_phase` is terminal and a last read is empty; 410 and 503 print the existing `error:` fields and exit non-zero.
- [ ] T042 [P] [US4] Write failing tests in `tests/cli/test_client_commands.py` (FR-016): `events` with `--run-id`, `--config-id` (a name resolves to an ID as in `runs list`) and `--type` prints one row per event in the contract's column order; `--follow` prints each new event once (stop the loop in the test after two polls).

### Implementation for User Story 4

- [ ] T043 [US4] Add the `runs logs` command to `infrahub_sync/cli.py`, using `_echo_rows` and a 2-second poll interval. T041 passes.
- [ ] T044 [US4] Add the `events` command to `infrahub_sync/cli.py`. T042 passes.
- [ ] T045 [US4] Check `uv run infrahub-sync --help`, `uv run infrahub-sync runs --help` and `uv run infrahub-sync events --help`.

**Checkpoint**: Operators read logs and events from the CLI.

---

## Phase 7: Polish and cross-cutting requirements

- [ ] T046 [P] Write a failing test in `tests/service/test_prefect_wording.py` (new) (FR-006): run the unit tier's HTTP scenarios for a failed run (missing branch), an expired log and an unreachable Prefect; scan every response body's messages, every `runs show`/`runs logs`/`events` output and every served log line for the Prefect API host and port and for "open Prefect", "in Prefect" and "Prefect flow run"; field names and the configured `prefect_flow_run_url` value are allowed (spec FR-006). Fix any message it finds.
- [ ] T047 [P] Write a guard test in `tests/service/test_prefect_wording.py` (FR-011): no module under `infrahub_sync/` calls a Prefect flow run delete (`delete_flow_run`). Sync deletes no flow run today (research R7), so this test passes at once; it fails only if a later change adds a delete. Show that it can fail by running it once against a temporary module that calls `delete_flow_run`, then remove that module.
- [ ] T048 [P] Write a failing test in `tests/test_run_events_docs.py` (new) (FR-017): `docs/docs/reference/sync-http-api.mdx` documents `GET /events` and `GET /runs/{run_id}/logs` with their errors `log-expired` and `log-unavailable`; `docs/docs/compose-deployment.mdx` names `PREFECT_SERVER_SERVICES_DB_VACUUM_ENABLED` and `PREFECT_SERVER_SERVICES_DB_VACUUM_RETENTION_PERIOD` and says that event order needs the containers' clocks to agree.
- [ ] T049 Update `docs/docs/reference/sync-http-api.mdx` (routes, event types, failure `cause`), `docs/docs/compose-deployment.mdx` (Prefect retention, research R7; clock note, critique E2) and `docs/docs/orchestration.mdx` (logs read through the Sync API); run `uv run invoke docs.generate` to refresh `docs/docs/reference/cli.mdx`. T048 passes.
- [ ] T050 Add changelog fragments with `uv run --extra dev towncrier create`: `+run-events.added.md` (`GET /events`, `infrahub-sync events`), `+run-logs.added.md` (`GET /runs/{run_id}/logs`, `infrahub-sync runs logs`), `+missing-branch-cause.fixed.md` (missing branch cause, causes for schema read failures, no traceback for known failures), `+orchestrator-wording.changed.md` (API messages no longer name Prefect). Restore extras with `uv sync --extra dev --extra prefect --extra service` if towncrier removes them.
- [ ] T051 Run `uv run invoke format`, `uv run invoke lint`, `uv run ruff check .` and `uv run invoke tests.tests-unit`; record the results in `dev/specs/017-sync-run-events-logs/opsmill-implement-report.md`.
- [ ] T052 Run [quickstart.md](./quickstart.md) scenarios 1–4 on the development stack and record the results, including the SC-003 delay and the log size measurement (critique E6), in `dev/specs/017-sync-run-events-logs/opsmill-implement-report.md`.

## Requirement coverage

| Requirement | Test task | Implementation task |
| --- | --- | --- |
| FR-001 | T022 | T029 |
| FR-002 | T020, T021 | T027, T028 |
| FR-003 | T023, T026 | T031 |
| FR-004 | T024, T033, T034, T035 | T030, T037, T038, T039 |
| FR-005 | T033, T035 | T037, T039 |
| FR-006 | T025, T046 | T032, T046 |
| FR-007 | T013 | T018 |
| FR-008 | T013, T035 | T018, T039 |
| FR-009 | T013 | T015 |
| FR-010 | T034, T035 | T038, T039 |
| FR-011 | T047, T048 | T049 |
| FR-012 | T002, T010, T011 | T005, T015, T016 |
| FR-012a | T012 | T017 |
| FR-013 | T035 | T039 |
| FR-014 | T004, T010, T012 | T007, T015, T017 |
| FR-015 | T041 | T043 |
| FR-016 | T042 | T044 |
| FR-017 | T048 | T049, T050 |

## Dependencies

- Phase 2 blocks Phase 3 (US1) and T017.
- Phase 4 (US3) T030 blocks Phase 5 (US2): the reader keeps only prefixed lines.
- Phase 5 (US2) and Phase 3 (US1) block Phase 6 (US4).
- Phase 7 runs last.

## Parallel examples

- Phase 2: T002, T003 and T004 are in different files and can be written together.
- US1: T010–T014 in parallel; then T015, T016 and T017 in parallel (different files); T018 after T015.
- US3: T020–T025 in parallel; T027 → T028 → T029 in order; T030 and T032 in parallel.
- US2: T033–T036 in parallel; T037 and T038 in parallel; then T039, then T040.

## Implementation strategy

Deliver in phase order. After Phase 3 the frontend can draw the timeline; after Phase 5 it can build the whole run
page (SC-002). Each phase ends green on the unit tier and with one commit that uses explicit paths.
