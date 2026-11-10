# Research: Sync run events and logs

Every finding below cites a file that was read on 2026-11-06 on branch `017-sync-run-events-logs` (base
`dd1b3280`). A claim that was not checked says so.

## R1. Can a flow run end Failed without raising, so no traceback is written? (brief, open question 1)

**Finding**: Yes, inside the flow function; no, for the whole process.

- A flow function that returns a `State` it built itself (no flow run ID in its state details) ends in that state.
  `handle_success` passes the return value to `return_value_to_state`, which uses a manually created state "as
  given" (`.venv/lib/python3.11/site-packages/prefect/flow_engine.py:799-813`,
  `.venv/lib/python3.11/site-packages/prefect/states.py:308-362`). No exception passes through the engine's
  `except Exception` arm, so the engine does not write "Encountered exception during execution" with its traceback
  (`flow_engine.py:1323`).
- The engine still writes "Finished in state Failed(...)" at INFO (`flow_engine.py:1276-1280`).
- When a worker starts the flow run through the runtime entry point, `run_flow` is called with the default
  `return_type="result"` (`flow_engine.py:277`, `flow_engine.py:2091`). `engine.result()` then raises for a failed
  state (`flow_engine.py:773-797`, `states.py:158-159`), and `run_flow` writes "Engine execution exited with
  unexpected exception" with `exc_info=True` to the flow run logger (`flow_engine.py:2249-2253`). So Prefect's own
  store can still hold one engine traceback. Which entry point Sync's process worker uses was not checked.

**Decision**: For a known error class, `service_sync_run` writes one ERROR line and returns
`Failed(message=<cause and next step>)` instead of re-raising. An unknown error class keeps today's re-raise.
FR-003 is then met in two ways that do not depend on each other:

1. Sync's own lines hold no traceback (Sync writes one formatted ERROR line).
2. Every engine line, including any engine traceback, is left out of `GET /runs/{run_id}/logs` (R3).

**Rationale**: Returning a state removes the flow-level traceback and the duplicate cause. The remaining engine
traceback is outside what Sync serves, so it does not need a Prefect change.

**Alternatives considered**: Raising a custom exception with no traceback text: Prefect formats every exception it
catches with `exc_info`, so this does not remove the traceback. Changing `return_type`: the entry point belongs to
Prefect.

**Effect on the run's observed state**: The flow run's final state is still `Failed`, which the Sync API already
reads through `PrefectOrchestration.observe` (`infrahub_sync/service/orchestration.py:275`). The run record's
terminal state comes from `commit_claimed_execution`, not from Prefect (spec 016, FR-019).

## R2. Are audit events written in the same transaction as the decision? (brief, open question 2)

**Finding**: No.

- `RunService._audit` builds an `AuditEvent` and calls `ProductProjection.record_audit`
  (`infrahub_sync/service/service.py:1063-1081`), which opens its own connection, inserts one row and commits
  (`infrahub_sync/product_store/store.py:2192-2216`).
- The decision is stored by another call with its own connection and commit, before the audit call. For example,
  an accepted run calls `complete_mutation` (`service.py:760-766`, `store.py:2054-2080`) and then `_audit`
  (`service.py:767-773`). A configuration mutation does the same (`infrahub_sync/service/config_routes.py:319-328`).
- The worker records a stage result with `commit_claimed_execution`, one connection and one commit
  (`store.py:1372-1466`), and writes no audit event today (`infrahub_sync/service/flow.py:896-905`,
  `flow.py:1078-1108`).

**Decision**: Add an optional `event` argument to the store methods that record a decision or a result:

- `complete_mutation` (accepted run and configuration requests);
- `commit_claimed_execution` (stage results, FR-012a);
- `cancel_execution` and the cancellation request write (cancel decisions; the exact write is read during
  implementation, `service.py:371-571`).

Each method inserts the event with the same cursor, before its `connection.commit()`. `record_audit` stays for
events that record a refusal or an unavailable answer, which save nothing else. A failed event insert rolls back
the whole transaction (FR-014).

**Rationale**: The store already wraps each of these writes in one transaction with a rollback on error. Passing
the event in keeps one transaction per decision and needs no new transaction API.

**Alternatives considered**: A unit-of-work object shared across store calls: a new abstraction with one use
(constitution VII).

## R3. How to tell Sync's own log lines from Prefect's lines (brief, open question 3)

**Finding**:

- Prefect stores each log line with `name` (the logger name), `level`, `message`, `timestamp`, `flow_run_id`
  and `task_run_id` (`.venv/lib/python3.11/site-packages/prefect/client/schemas/objects.py:1385-1397`).
- `get_run_logger` in a flow returns a `PrefectLogAdapter` on the logger `prefect.flow_runs`
  (`.venv/lib/python3.11/site-packages/prefect/logging/loggers.py:188-230`). The engine writes its own lines
  ("Finished in state ...", "Encountered exception ...") through the same flow run logger
  (`flow_engine.py:1276-1280`, `flow_engine.py:1323`). So `name` does not tell them apart.
- `RunLoggerBridge` forwards every record of the `infrahub_sync` logger tree to the run logger as
  `"%s | %s" % (record.name, redacted message)` (`infrahub_sync/orchestration/flow.py:98-114`;
  `SOURCE_LOGGER_NAME = "infrahub_sync"`, `flow.py:59`). Every bridged message therefore starts with
  `infrahub_sync` and ` | `.
- `service_sync_run` also writes directly to the run logger, without the prefix: the last log line
  (`infrahub_sync/service/flow.py:1054`, `flow.py:1071`), the refused-claim ERROR line (`flow.py:982`) and the
  "failure evidence could not be persisted" warning (`flow.py:907-911`). `publish_run_artifact` receives the run
  logger too (`flow.py:937-946`).

**Decision**: A Sync line is a Prefect log line whose message matches `^infrahub_sync(\.[A-Za-z0-9_]+)* \| `.
The service flow's direct lines are written through one helper that adds the prefix
`infrahub_sync.service.flow | `, so every line Sync writes has the same form. The log reader removes the prefix
and returns the logger name as `source` and the rest as `message`.

**Rationale**: The prefix already exists for every bridged line and is written by Sync's own code. Engine lines
start with fixed English text such as "Finished", "Encountered", "Engine execution", "Beginning" or "Executing",
not with `infrahub_sync`.

**Alternatives considered**: A child logger such as `prefect.flow_runs.infrahub_sync`, so that `name` tells the
lines apart. Whether Prefect's API handler keeps the flow run ID for a child logger was not checked.

## R4. How the Sync API reaches the Prefect API (brief, open question 4)

**Finding**: `infrahub_sync/service/serve.py:26-43` opens one Prefect client per call with
`prefect.client.orchestration.get_client()` for submit, observe, pool status and cancel. The client reads
`PREFECT_API_URL` and `PREFECT_API_AUTH_STRING` from the environment; the root Compose file sets them in a shared
block (`docker-compose.yml:54`, `docker-compose.yml:58`). Whether `sync-api` uses that block was not checked line by
line; the API already submits flow runs, so it reaches Prefect.

The installed client reads logs with `read_logs(log_filter, limit, offset, sort)`
(`.venv/lib/python3.11/site-packages/prefect/client/orchestration/_logs/client.py:75-81`). `LogFilter` filters by
`flow_run_id`, level, timestamp, task run and text (`.venv/lib/python3.11/site-packages/prefect/client/schemas/filters.py:702-719`).
`read_flow_run` already exists on the orchestration client (`orchestration.py:230-232`).

**Decision**: Add `read_logs(flow_run_ids) -> RunLogRead` to the `Orchestration` protocol
(`orchestration.py:149-155`) and to `_ClientPerCallOrchestration`. It calls `read_flow_run` for each flow run ID to
tell an expired flow run (not found) from a held one, then `read_logs` with a `flow_run_id` filter, sorted by
timestamp, in pages of 200. Prefect's maximum page size and Infrahub's batch size were not checked, so 200 is a
starting value that a test pins. This is ADR 0019's direction: the Prefect API is read
through the Sync API.

## R5. Missing destination branch

**Finding**:

- The Infrahub schema accessor calls `client.schema.all(branch=branch)` and turns every SDK error that no earlier
  arm handles into `DestinationSchemaReadError(..., reason="rejected")`
  (`infrahub_sync/configuration/capabilities.py:264-286`). The worker turns that into
  `DestinationSchemaUnavailableError("destination schema for branch ... could not be read: rejected")`
  (`infrahub_sync/runtime_schema/worker.py:153-157`).
- The installed `infrahub_sdk` (dist-info 1.23.2) raises `BranchNotFoundError` when the schema endpoint answers
  HTTP 400 (`.venv/lib/python3.11/site-packages/infrahub_sdk/schema/__init__.py:293-297`,
  `infrahub_sdk/exceptions.py:79-83`). Whether Infrahub answers 400 for any other schema request error was not
  checked.
- `operator_cause` records a cause only for lineage refusals, a `PlanArtifactError` next action, and a refused
  token or missing permission (`infrahub_sync/service/failure_cause.py:29-77`). A
  `DestinationSchemaUnavailableError` therefore records only `error_type` and `cause_type`
  (`flow.py:759-779`), which matches the brief's evidence.

**Decision**:

- Catch `BranchNotFoundError` before the SDK base error and raise
  `DestinationSchemaReadError(..., reason="nobranch")` (the reason pattern is `^[a-z]{1,32}$`,
  `capabilities.py:28`).
- Add a `DestinationBranchNotFoundError(DestinationSchemaUnavailableError)` raised by the worker for that reason,
  with a fixed `message` and `next_action`, and add it to the classes `operator_cause` describes.
- Give every `DestinationSchemaUnavailableError` reason a fixed cause and next action (timeout, unreachable,
  unauthorized, forbidden, credentials, unconfigured, rejected), so FR-001 holds for the whole class.

The branch name comes from the run's resolved destination branch, which the run already stores and returns
(`service.py:192`, `destination_branch`). It is passed through `_recordable` like every recorded text.

## R6. Requests rejected for a missing or invalid token

**Finding**: Today the Sync API records an audit event with outcome `refused-authentication` for such a request
(`infrahub_sync/service/app.py:98-102`, `service.py:1083-1091`). Spec 015 tested that behaviour
(`dev/specs/015-run-discovery-api/tasks.md:100`).

**Decision**: Keep the stored audit record, which spec 015 relies on as security evidence, but give it no event
type and never return it from `GET /events` (FR-009). The brief's out-of-scope item "events for requests rejected
for a bad token" is read as: such requests are not events.

**Alternatives considered**: Stop writing the record. This removes evidence that spec 015 requires, and the brief
does not ask for it.

## R7. Prefect retention

**Finding**: In Prefect 3.8.6 the database vacuum service deletes old flow runs only when `flow_runs` is in
`PREFECT_SERVER_SERVICES_DB_VACUUM_ENABLED`; the default is `{"events"}`. When enabled, a flow run is deleted 90
days after it ends by default (`PREFECT_SERVER_SERVICES_DB_VACUUM_RETENTION_PERIOD`, minimum 1 hour)
(`.venv/lib/python3.11/site-packages/prefect/settings/models/server/services.py:87-148`). The root and development
Compose files set no vacuum or retention value (`grep` of both files). Sync deletes no flow run and no run record
(`grep` for `delete_flow_run` and `retention` in `infrahub_sync/`).

**Decision**: Document the default (flow runs are kept) and the two settings on the deployment page (FR-011). Add a
test that no Sync code path calls a Prefect delete. Infrahub's own value (`days_to_keep=2`) is the brief's
finding and is not Sync's default.

## R8. How fast a new line reaches the API (SC-003)

**Finding**: Prefect's API log handler sends lines in batches every 2 seconds by default
(`.venv/lib/python3.11/site-packages/prefect/settings/models/logging.py:47-50`).

**Decision**: The CLI's `--follow` polls every 2 seconds. A line is then readable within about 4 seconds of being
written, inside SC-003's 5 seconds. A live check in quickstart.md measures it.

## R9. Paging the log after filtering

**Finding**: Prefect pages raw lines; Sync leaves out engine lines after reading them (R3). An `offset` counted in
Prefect lines would change meaning when engine lines are added.

**Decision**: The reader reads every line of the run's flow runs, in attempt order and then time order, keeps Sync
lines, and applies `offset` and `limit` to the kept lines. `offset` therefore counts Sync lines only, which is what
FR-005's polling rule needs. No volume figure for a run's log is known (spec, Assumptions); the plan records the
cost.

## R10. Events paging

**Finding**: `GET /runs` pages with an opaque cursor (`infrahub_sync/service/paging.py`, `service.py:624-657`).
The brief asks for `limit` and `offset` on `GET /events`, the names Infrahub uses.

**Decision**: Use `limit` and `offset`, as the user decided, with the same maximum `limit` as `GET /runs`
(`page_size_or_refuse`, `service.py:128`). Events are ordered by `created_at` and then `event_id`, the order the
store already uses (`store.py:420`), so an offset stays stable while new events are appended.
