# Feature Specification: Sync run events and logs, read from the Sync API

**Feature Branch**: `017-sync-run-events-logs`

**Created**: 2026-11-06

**Status**: Draft

**Input**: The idea brief "Sync run events and logs, aligned with Infrahub", grilled with the user on 2026-11-06.
Every decision in the brief was confirmed by the user. The brief is compared with this specification in
[`alignment-check.md`](./alignment-check.md).

## Background

On 2026-11-06 a plan run on the missing destination branch `demo-test` failed. The run record held only the error
type `DestinationSchemaUnavailableError`, and the frontend told the reader to open the Prefect flow run and the
worker's log to find the cause. In Prefect, the same run held:

- the cause "destination schema for branch 'demo-test' could not be read: rejected"
  (`infrahub_sync/runtime_schema/worker.py:157`);
- a full Python traceback, logged twice, because `infrahub_sync/service/flow.py` re-raises a sanitized error;
- the cause a third time, in "Finished in state Failed(...)";
- Prefect's own lines, which name the worker ID and the internal Prefect address.

Nothing checks whether the destination branch exists, so a missing branch reads as a generic "rejected".

Today the Sync API records an audit event for each decision it takes on a run or a configuration request
(accepted, replayed, refused, cancelled, unavailable), but no HTTP route returns these events. The frontend
rebuilds a run's history from status fields.

ADR [0019](../../adr/0019-prefect-is-an-internal-component-of-sync.md) makes Prefect an internal component of Sync.
Its amendment of 2026-10-23 says that the Prefect API will later be reachable only through the Sync API. Infrahub
1.12 already works this way: it serves its events and its task logs from Prefect through its own API, filtered and
paged (brief, section "How Infrahub does it").

This feature lets the frontend team and operators read a run's timeline, its log and an actionable failure cause
from the Sync API alone.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - The frontend shows a run's timeline from the Sync API (Priority: P1)

The frontend team builds a run page. It requests the events of one run and draws the run's timeline: who started
the run, when each stage was accepted, and how each stage ended.

**Why this priority**: The frontend rebuilds this history from status fields today. The timeline and the log
(User Story 2) ship together, by user decision.

**Independent Test**: Accept a plan, let it finish, accept an apply, let it finish, then request
`GET /events?run_id=<run_id>`. The response lists the four events in time order.

**Acceptance Scenarios**:

1. **Given** a run that was accepted, planned and applied, **When** the frontend requests the events filtered by
   that run, **Then** it receives `infrahub_sync.run.accepted`, `infrahub_sync.run.planned`,
   `infrahub_sync.run.accepted` and `infrahub_sync.run.applied`, in time order. Each event holds its time, its
   actor, its primary resource (the run) and its related resources (the account, the configuration version and
   the destination branch).
2. **Given** a configuration version that an administrator registered, **When** the frontend requests the events
   filtered by that configuration, **Then** it receives `infrahub_sync.config.version_registered` with the
   configuration as its primary resource.
3. **Given** more events than one page holds, **When** the frontend requests the next page with `offset`, **Then**
   it receives the events that follow, and no event twice.
4. **Given** a request rejected for a missing or invalid token, **When** any reader requests events, **Then** no
   event describes that request.

---

### User Story 2 - The frontend shows a run's log from the Sync API (Priority: P1)

The frontend shows the log lines of a run while it runs and after it ends. The page polls the Sync API and appends
the new lines.

**Why this priority**: The log is where an operator looks first when a run fails. Today it is only in Prefect,
mixed with Prefect's own lines.

**Independent Test**: Run a plan, then request `GET /runs/<run_id>/logs`. The response holds the lines that Sync
wrote, each tagged with its stage, and none of Prefect's own lines.

**Acceptance Scenarios**:

1. **Given** a run whose plan stage is running or has ended, **When** the frontend requests the run's log,
   **Then** it receives the redacted lines that Sync wrote, in order, each with its stage, level and time, and no
   line written by the Prefect engine or worker.
2. **Given** that the frontend has read 5 lines and the stage has since written 3 more, **When** it requests the
   log with `offset=5`, **Then** it receives only the 3 new lines.
3. **Given** a run with a plan stage and an apply stage, **When** the frontend requests the log filtered by
   `stage=apply`, **Then** it receives only the apply stage's lines.
4. **Given** a run whose flow runs Prefect no longer holds, **When** the frontend requests the log, **Then** the
   API answers that the log has expired, not with an empty list.
5. **Given** that Prefect cannot be reached, **When** the frontend requests the log, **Then** the API answers that
   the log is unavailable for now, and the run and its events stay readable.

---

### User Story 3 - The reader acts on a failed stage without opening Prefect (Priority: P1)

An operator or the frontend reads a failed run and learns what went wrong and what to do next, from the run record.

**Why this priority**: A failed run that names only an error type sends the reader to Prefect, which holds a vague
message and tracebacks (Background).

**Independent Test**: Start a plan on a branch that does not exist in the destination Infrahub. `GET /runs/<run_id>`
names the branch and the fix. The run's log holds one ERROR line and no traceback.

**Acceptance Scenarios**:

1. **Given** a plan on a destination branch that does not exist, **When** the stage fails, **Then** the run record
   holds a cause that names the branch, and a next action, for example "Branch `demo-test` does not exist in
   Infrahub. Create it, or start the run with `--branch <existing branch>`."
2. **Given** a stage that fails with a known error class, **When** the reader reads the run's log, **Then** the log
   holds one ERROR line with the cause and the next step, and no line contains a Python traceback.
3. **Given** any failed stage, **When** the reader reads the run record, the CLI output or the run's log, **Then**
   no message tells the reader to open Prefect or names the Prefect address.

---

### User Story 4 - An operator follows a run's log and events from the CLI (Priority: P2)

An operator runs `infrahub-sync runs logs <run_id> --follow` while a stage runs, and `infrahub-sync events
--run-id <run_id>` to read the run's timeline.

**Why this priority**: The frontend is the main reader. Operators use the CLI second, and the CLI only calls the
same two routes.

**Independent Test**: Start a plan and run `infrahub-sync runs logs <run_id> --follow`. The command prints new lines
as they arrive and exits when the run ends.

**Acceptance Scenarios**:

1. **Given** a running stage, **When** the operator runs `runs logs <run_id> --follow`, **Then** the command prints
   each new line once and exits when the run reaches a terminal phase.
2. **Given** a run with two stages, **When** the operator runs `runs logs <run_id> --stage apply`, **Then** only the
   apply stage's lines are printed.
3. **Given** a run, **When** the operator runs `events --run-id <run_id>`, **Then** the events are printed in the
   same machine-readable `key: value` style as `runs list`.
4. **Given** `events --follow`, **When** a new event is recorded, **Then** the command prints it once.

### Edge Cases

- **Missing destination branch**: the failure names the branch and the fix (FR-002).
- **Prefect deleted the run's flow runs**: the log answer says that the log has expired (FR-010).
- **Prefect cannot be reached when the log is read**: the log answer says that the log is unavailable for now; run
  records and events are still served (FR-013).
- **A line that contains a secret value**: it is redacted before it reaches Prefect, by the existing log bridge, so
  the API serves the redacted text.
- **A run with no flow run yet** (accepted, not yet submitted): the log answer is an empty list, not an error.
- **A stage that ends with an unexpected error** (an error class Sync does not know): the run record holds the error
  type and the fixed next action `infrahub-sync runs show <run_id>`. The log holds one ERROR line with the error
  type. Whether Prefect's own copy holds a traceback does not matter, because Prefect's own lines are never served.
- **Audit records written before this feature**: they are served as events with a type derived from their
  operation and outcome. Their configuration and branch are not known, so their related resources hold only the
  actor.
- **A request rejected for a missing or invalid token**: no event describes it (FR-009).
- **Retried stage** (a second flow run for the same stage): its lines follow the first attempt's lines and carry the
  same stage.

## Requirements *(mandatory)*

Every requirement is delivered test-first: a failing test is committed before, or in the same commit as, the code
that makes it pass.

### Functional Requirements

#### Failure cause and next action

- **FR-001**: A failed stage MUST store a cause and a next action that the reader can act on, readable from
  `GET /runs/{run_id}` and `infrahub-sync runs show` without opening Prefect. Each cause is built from a fixed
  template and recorded identifiers, and is redacted, like every recorded value.
- **FR-002**: When the destination branch does not exist, the stage MUST fail with that specific reason. The cause
  MUST name the branch, and the next action MUST name the fix: create the branch, or start the run with
  `--branch <existing branch>`.
- **FR-003**: A stage that fails with a known error class MUST NOT write a Python traceback to the lines that Sync
  writes. It MUST write one ERROR line that holds the cause and the next step.
- **FR-006**: No error message that the Sync API returns, no message that the CLI prints, and no line that Sync
  writes to a run's log MUST tell the reader to open, read or retry anything in Prefect, or hold the Prefect API
  address. The Sync API's error messages MUST NOT name Prefect; they name "the orchestrator". These stay as they
  are, because they are not messages or because they answer an action taken in Prefect:
  - field names that already exist, such as `prefect_executions` and `prefect_flow_run_url` (spec 016);
  - the value of `prefect_flow_run_url`, a link that the operator chooses to configure;
  - the refused-claim line, written when someone selects Retry on a flow run in Prefect (spec 016).

#### Run log, read from Prefect through the Sync API

- **FR-004**: `GET /runs/{run_id}/logs` MUST read the log lines of the run's flow runs, one or more per stage, from
  Sync's Prefect API. It MUST return only the lines that Sync's own code wrote, each tagged with its stage, level and
  time, and MUST leave out the lines that the Prefect engine and worker wrote.
- **FR-005**: The log MUST be paged with `limit` and `offset`, the names Infrahub uses as `log_limit` and
  `log_offset`. A request with `offset` equal to the number of lines already read MUST return only the newer lines.
  The log MAY be filtered by `stage`.
- **FR-010**: When Prefect no longer holds a flow run that the run record names, the API MUST answer that the log
  has expired (HTTP 410, error `log-expired`), not return an empty list.
- **FR-011**: The deployment documentation MUST state how long Prefect keeps flow runs and their logs, and which
  setting changes it. Sync MUST NOT delete a flow run while the Sync run record that names it exists.
- **FR-013**: When Prefect cannot be reached, the log request MUST fail with HTTP 503 and the error
  `log-unavailable`. Every other route MUST keep working.

#### Events, stored as Sync audit events in Infrahub's layout

- **FR-007**: `GET /events` MUST filter by run, configuration, event type, related resource, and a time range
  (`since`, `until`). It MUST be paged with `limit` and `offset` and return events in time order. One route serves
  every event; there is no `/runs/{run_id}/events` route.
- **FR-012**: Each event MUST have:
  - a dotted type of the form `infrahub_sync.<resource>.<action>`, for example `infrahub_sync.run.accepted`,
    `infrahub_sync.run.planned`, `infrahub_sync.run.applied` or `infrahub_sync.config.version_registered`;
  - a primary resource, the run or the configuration;
  - related resources: the actor's account, the configuration version and the destination branch, when known.
  The event keeps the fields that audit events hold today: ID, actor, operation, reason, outcome and time.
- **FR-012a**: Each stage that the worker ends MUST record one event: `infrahub_sync.run.planned`,
  `infrahub_sync.run.verified`, `infrahub_sync.run.applied` or `infrahub_sync.run.synced` when it succeeds, and
  `infrahub_sync.run.failed` when it fails or is interrupted. Today only the Sync API's decisions are recorded.
- **FR-014**: An event MUST be saved in the same database transaction as the decision or result it records. When the
  event cannot be saved, the decision or result is not saved either. An event that records a refusal, which saves
  nothing else, is saved on its own.

#### Access

- **FR-008**: Reading events and logs MUST require a valid token, the same rule as `GET /runs`. Any valid token reads
  every event and every run's log.
- **FR-009**: A request rejected for a missing or invalid token MUST NOT appear as an event in `GET /events`.

#### Command-line interface

- **FR-015**: `infrahub-sync runs logs <run_id> [--stage <stage>] [--follow]` MUST print the run's log lines.
  `--follow` MUST poll for new lines and stop when the run reaches a terminal phase.
- **FR-016**: `infrahub-sync events [--run-id] [--config-id] [--type] [--follow]` MUST print events in the same
  machine-readable output style as `runs list`. `--follow` MUST poll for new events until the operator stops it.

#### Documentation and release notes

- **FR-017**: The Sync HTTP API reference, the CLI reference and the deployment page MUST describe the two routes,
  the two commands, the event types, the failure cause, and Prefect's retention (FR-011), in the same change.
  Each user-visible change MUST have a changelog fragment.

### Key Entities

- **Event**: the stored audit event, extended. It keeps its ID, actor, operation, reason, outcome and time, and
  gains a dotted type, a primary resource (a run or a configuration) and related resources (account, configuration
  version, destination branch).
- **Run log line**: a view, not a stored record. It is a Prefect log line that Sync's code wrote, reached through
  the flow run IDs that the run's orchestration entries already store, with its stage, level, time and message.
- **Run failure**: the stored failure evidence of a stage, extended so that every known error class has a cause and
  a next action.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: For a plan on a missing destination branch, the reader finds the cause and the fix on the run page or
  in `runs show`, without opening Prefect.
- **SC-002**: The frontend builds a run page (record, timeline and log) from 3 Sync API requests or fewer, and makes
  no request to Prefect.
- **SC-003**: A new log line can be read through the Sync API within 5 seconds of being written.
- **SC-004**: 0 messages from the Sync API, the CLI or a run's log tell the reader to open Prefect.
- **SC-005**: Stages that fail with a known error class write 0 tracebacks to the run's log.
- **SC-006**: While Prefect cannot be reached, 100% of run record and event requests succeed.

## Assumptions

- The Sync API reaches Sync's own Prefect API with the settings it already uses to submit and observe flow runs
  (ADR 0019).
- Each orchestration entry stores its flow run ID and its stage, as spec 016 uses them for `prefect_flow_run_url`.
- Source values in log lines are already visible to every valid token through `GET /runs/{run_id}/plan`, so any
  valid token may read every log.
- A run's log is small enough to read in full for each request; no volume figure is known.
- Infrahub's behaviour is taken from the brief (Infrahub commit `0dcbe157dd`, release 1.12). Whether Infrahub
  removes Prefect's own lines from task logs is not known.

## Out of Scope

- streaming (WebSocket, server-sent events);
- a log store in Sync;
- sending Sync's events to Prefect;
- outgoing notifications (webhooks, Slack);
- events for requests rejected for a missing or invalid token;
- the frontend pages;
- full-text search over log lines;
- a combined view of Infrahub and Sync events;
- renaming `next_action` to Infrahub's `remediation`.
