# Feature Specification: Prefect run observability and the missing Sync API address

**Feature Branch**: `016-prefect-run-observability`

**Created**: 2026-11-05

**Status**: Extracted

**Input**: User description: "Restore the Prefect run-observability behaviour that never reached
feature/v3-develop, and make the CLI name the missing Sync API address. Use test-driven development."
The full request, with its six requirements, non-goals and acceptance, is recorded in
[`alignment-check.md`](./alignment-check.md) once the alignment check runs.

## Background

On 2026-11-05 an operator ran the full development stack from `feature/v3-develop` at
[`3c617102`](https://github.com/opsmill/infrahub-sync/commit/3c617102). The stack ran Infrahub 1.11.10 from
Infrahub's own Compose file, a local NetBox with the pinned demo data, and the `infrahub-sync:dev` image.
These runs and commands all completed:

- a plan of 1,688 operations;
- an apply of that plan;
- a second plan that held only the recorded delete;
- a `sync` of five changes;
- `verify`;
- the run discovery commands `runs list`, `runs show`, `runs plan`, `runs results` and `runs artifacts`.

The same test showed these gaps:

- The Prefect UI showed no artifact for any of the five flow runs.
- A flow run's log did not say what the operator should do next.
- Nothing linked a Sync run to its Prefect flow run page.
- Without `INFRAHUB_SYNC_API_URL`, every CLI command printed only `error: client-input` and
  `argument: service_url`.

Seven fixes for the Prefect behaviour were approved on 2026-10-20 and committed only on
`009-plan-problem-reporting` and the `feat/010-*` branches, as
[`43986fa9..188bbb1d`](https://github.com/opsmill/infrahub-sync/compare/43986fa9~1...188bbb1d). Two of the seven
reached `feature/v3-develop` in another form:

- the `run:` and `stage:` flow run tags (`infrahub_sync/service/orchestration.py:168-171`);
- `infrahub-sync runs artifacts` (spec [015](../015-run-discovery-api/spec.md)).

This feature delivers the other five, and the CLI hint. The old commits are reference material only: specs 010,
013, 014 and 015 changed the code they touched, so each behaviour is built again against the current code.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Read a run's result in the Prefect UI (Priority: P1)

An operator opens a finished flow run in the Prefect UI and reads one summary of the Sync run:

- what the run was;
- what it changed or planned;
- whether it succeeded;
- which CLI command to run next.

The operator does not need to read the flow run log or run CLI commands to find this.

**Why this priority**: The Prefect UI is where an operator who watches the worker looks first. Today it shows
a completed or failed state and a log, but no summary of the Sync run.

**Independent Test**: Run a plan against the development stack, then open its flow run's Artifacts tab, or
query Prefect's artifact list. One markdown artifact with the key `sync-run-<run_id>-plan` holds the summary.

**Acceptance Scenarios**:

1. **Given** a plan run that finishes, **When** the operator opens its flow run in Prefect, **Then** one
   markdown artifact with the key `sync-run-<run_id>-plan` shows these values:
   - the run ID;
   - the stage;
   - the outcome;
   - the configuration ID and version;
   - the destination branch;
   - the plan's operation counts by action and by kind;
   - the next CLI command.
2. **Given** an apply, sync or verify stage that finishes, **When** the operator opens its flow run, **Then**
   one artifact with the key `sync-run-<run_id>-<stage>` shows that stage's summary.
3. **Given** a stage that fails, **When** the operator opens its flow run, **Then** the artifact still exists.
   It shows the failed outcome and the failure's next action.
4. **Given** that Prefect refuses to store the artifact, **When** the stage ends, **Then** the run's outcome
   is unchanged and the flow run log holds one warning that the summary was not published.

---

### User Story 2 - Read the next step in the flow run log (Priority: P1)

An operator reads the last line of a flow run's log, in the Prefect UI or in the worker's container log. The
line names the run, the stage, the outcome and the next step.

**Why this priority**: The log is the first thing an operator reads when a run fails, and today its last line
gives no next step.

**Independent Test**: Run a stage that succeeds and a stage that fails. The last log line of each has the form
`service run <run_id> stage=<stage> outcome=<outcome>; next step: <action>`.

**Acceptance Scenarios**:

1. **Given** a stage that succeeds, **When** it ends, **Then** its last log line names
   `infrahub-sync runs show <run_id>` as the next step.
2. **Given** a stage whose recorded failure has a next action, **When** it ends, **Then** its last log line
   names that next action.
3. **Given** a stage that stops with a result that carries a failure, such as a `sync` whose plan leaves out
   records, **When** it ends, **Then** its last log line names that failure's next action.

---

### User Story 3 - Open a run's Prefect flow run from the CLI (Priority: P2)

An operator runs `infrahub-sync runs show <run_id>` and opens the printed link to the run's Prefect flow run.

**Why this priority**: It removes the manual search for a flow run ID in the Prefect UI. It depends on an
operator setting, so the run records are complete without it.

**Independent Test**: Set `INFRAHUB_SYNC_PREFECT_UI_URL` on the Sync API, run a plan, then run `runs show`. The
output has a `prefect_flow_run_url` line that opens the flow run page. Without the setting, the field is
`<none>` and the HTTP response holds `null`.

**Acceptance Scenarios**:

1. **Given** that the Sync API has `INFRAHUB_SYNC_PREFECT_UI_URL` set to an `http` or `https` address,
   **When** a client reads a run, **Then** each orchestration entry holds `prefect_flow_run_url`, built from
   that address and the entry's flow run ID.
2. **Given** that the setting is not set, **When** a client reads a run, **Then** `prefect_flow_run_url` is
   `null` for every entry.
3. **Given** that the setting holds an address with another scheme, user information, a query or a
   fragment, **When** the Sync API starts, **Then** it refuses to start and names the setting.
4. **Given** the development stack, **When** it starts, **Then** the Sync API has the setting set to the Prefect
   UI address on the host, `http://127.0.0.1:${PREFECT_PORT:-4230}`.

---

### User Story 4 - Understand a refused Prefect retry (Priority: P2)

An operator selects Retry on a finished flow run in the Prefect UI, or starts a flow run by hand from the
deployment. The worker refuses the flow run. The flow run log says, in a fixed message:

- that nothing was read or written;
- that Sync runs are not replayed from Prefect;
- which commands to run instead.

**Why this priority**: Prefect offers Retry on every failed flow run, so operators reach this state easily.
Today they read only `service worker execution claim was refused`.

**Independent Test**: Start a flow run of the service deployment for a run that is not waiting for an execution.
The flow run fails, and its log holds the fixed message at ERROR level.

**Acceptance Scenarios**:

1. **Given** a flow run that the Sync API did not submit, **When** the worker refuses its claim, **Then** the
   flow run fails with `ExecutionClaimRefusedError`, and its log holds one ERROR line. That line contains
   `service worker execution claim was refused`, says that nothing was read or written, and names
   `infrahub-sync runs show <run_id>`, `infrahub-sync plan`, `infrahub-sync sync` and
   `infrahub-sync apply <run_id> --expected-checksum <checksum>`.
2. **Given** that message, **When** an operator reads it, **Then** it does not name the deprecated `diff`
   command.

---

### User Story 5 - See a finished execution's verdict as its state (Priority: P3)

An operator runs `runs show` on a finished run while Prefect is reachable. The state of each finished
orchestration entry is the verdict that the worker recorded, not a Prefect observation taken before the
verdict.

**Why this priority**: The values are rarely different, but when they are, `runs show` reports a finished run
as `running`.

**Independent Test**: Build a run whose execution has a recorded terminal state, and a live Prefect
observation that reads `Running`. The run summary's state for that entry is the terminal state.

**Acceptance Scenarios**:

1. **Given** an orchestration entry with a recorded terminal state, and a live observation that is available,
   **When** a client reads the run, **Then** the entry's `state` is the terminal state.
2. **Given** an entry with no terminal state, and a live observation that is available, **When** a client reads
   the run, **Then** the entry's `state` is the live observation, as it is today.

---

### User Story 6 - Fix a missing Sync API address from the error (Priority: P1)

An operator runs any CLI command that needs the Sync API without setting its address or token. The error names
the setting to fix.

**Why this priority**: Every new CLI user meets this error first, and today it does not name the variable.

**Independent Test**: Run `infrahub-sync runs list` with `INFRAHUB_SYNC_API_URL` unset. The output keeps
`error: client-input` and `argument: service_url`, and adds a `hint:` line that names
`INFRAHUB_SYNC_API_URL` and `--api-url`. Run it again with the URL set and `INFRAHUB_SYNC_API_TOKEN` unset. The
`hint:` line names `INFRAHUB_SYNC_API_TOKEN`.

**Acceptance Scenarios**:

1. **Given** no Sync API address, **When** the operator runs a command that needs it, **Then** the error keeps
   its `error` and `argument` fields, adds a `hint` field that names `INFRAHUB_SYNC_API_URL` and `--api-url`,
   and exits with the same exit code as today.
2. **Given** an address that is not an absolute `http` or `https` address, or that carries user information,
   a query or a fragment, **When** the operator runs the command, **Then** the same `hint` field appears.
3. **Given** an address and no token, **When** the operator runs the command, **Then** the `hint` field names
   `INFRAHUB_SYNC_API_TOKEN`.
4. **Given** a `client-input` error for any other argument, **When** it is printed, **Then** there is no `hint`
   field, as today.

### Edge Cases

- **Prefect refuses or cannot store the artifact**: the outcome that the run recorded is unchanged, and the
  log holds one warning.
- **The flow function runs outside a Prefect flow run**, for example in a unit test or through
  `run_remote_request`: nothing is published.
- **A stage fails before the plan exists**: the artifact holds no counts. It holds the outcome and the next
  action.
- **A stage fails before the worker records a failure**: the artifact and the last log line use the next step
  `infrahub-sync runs show <run_id>`.
- **Recorded values hold text from a provider**, such as a destination error message: the artifact and the
  log line do not include that text. They hold only fixed template text and recorded identifiers, counts and
  outcomes, and they are passed through the same redaction as the log.
- **A refused claim**: the flow run publishes no artifact, because no Sync stage ran.
- **`INFRAHUB_SYNC_PREFECT_UI_URL` ends with `/`**: the link has no double slash.
- **The Prefect UI is behind a path prefix**, such as `https://example.com/prefect`: the link keeps the
  prefix.

## Requirements *(mandatory)*

Every requirement is delivered test-first: a failing unit test is committed before, or in the same commit as,
the code that makes it pass.

### Functional Requirements

#### Run-summary artifact

- **FR-001**: At the end of each service stage that ran inside a Prefect flow run, including a stage that
  failed, the worker MUST publish one markdown artifact with the key `sync-run-<run_id>-<stage>`.
- **FR-002**: The artifact MUST hold these values:
  - the run ID;
  - the stage;
  - the outcome;
  - the configuration ID and registry version;
  - the destination branch, when the run has one;
  - the plan's operation counts by action, and by kind sorted by kind name, when the run summary holds them;
  - the next CLI commands, ending with the recorded failure's next action when there is one. The failure's
    `cause` text is never shown.
- **FR-003**: The artifact MUST be built only from fixed templates and from values that the product records
  hold: identifiers, counts, outcomes and fixed next actions. It MUST pass through the run's redaction before
  it is published ([secret redaction](../../../docs/docs/development/guidelines/secret-redaction.md)).
- **FR-004**: If publishing the artifact raises an error, the stage's outcome and its recorded result MUST NOT
  change. The worker MUST log one warning that names the run and the stage, without the error text.
- **FR-005**: Outside a Prefect flow run, the worker MUST NOT try to publish an artifact.
- **FR-006**: The artifact key MUST be valid under Prefect's artifact key rules, which allow only lowercase
  letters, digits and dashes. Run IDs MUST be lowercased in the key.

#### Next step in the last log line

- **FR-007**: The last log line of each stage MUST be
  `service run <run_id> stage=<stage> outcome=<outcome>; next step: <action>`.
- **FR-008**: `<action>` MUST be the next action of the failure that the stage recorded, when one exists.
  Otherwise it MUST be the next action of a failure that the stage's result carries. Otherwise it MUST be
  `` `infrahub-sync runs show <run_id>` ``.
- **FR-008a**: When a stage fails and the worker records nothing, `<outcome>` MUST be `failed` and `<action>` MUST
  be `` `infrahub-sync runs show <run_id>` ``. This happens when a read stage's success commit fails, or when the
  failure record cannot be stored. The artifact uses the same values.
- **FR-009**: A stage that ends through the worker's failure-recording path MUST also end with that line.

#### Link to the Prefect flow run

- **FR-010**: The Sync API MUST read an optional setting, `INFRAHUB_SYNC_PREFECT_UI_URL`.
- **FR-011**: When the setting is set, each orchestration entry in a run response MUST hold
  `prefect_flow_run_url`, built as `<setting without trailing slash>/runs/flow-run/<flow_run_id>`. When the
  setting is not set, the field MUST be `null`.
- **FR-012**: The Sync API MUST refuse to start when the setting is not an absolute `http` or `https` address
  with a host, or when it carries user information, a query or a fragment. The refusal MUST name the setting
  and MUST NOT print its value.
- **FR-013**: The field MUST be optional in the client model, so that a client reads responses from a Sync API
  without this change.
- **FR-014**: `infrahub-sync runs show` MUST print `prefect_flow_run_url` for the selected orchestration entry,
  next to `flow_run_id`.
- **FR-015**: `development/docker-compose.yml` MUST set the setting on `sync-api` to
  `http://127.0.0.1:${PREFECT_PORT:-4230}`. The root `docker-compose.yml` MUST pass the setting through to
  `sync-api` with an empty default, which turns the link off, and MUST document it with an example value.

#### Refused claim

- **FR-016**: When the worker refuses to claim an execution, it MUST raise `ExecutionClaimRefusedError`, a
  `RuntimeError` subclass whose message begins with the existing text
  `service worker execution claim was refused`.
- **FR-017**: The message MUST be built from fixed text and the run ID only. It MUST say:
  - that nothing was read or written;
  - that a Sync run is not replayed from Prefect, and that a Prefect Retry, a flow run started by hand from
    the deployment, and an execution whose admission expired are refused;
  - that the operator checks the run with `infrahub-sync runs show <run_id>`;
  - that the operator then starts a new run with `infrahub-sync plan` or `infrahub-sync sync`, or applies a
    reviewed plan with `infrahub-sync apply <run_id> --expected-checksum <checksum>`.
- **FR-018**: The worker MUST log that message at ERROR level in the flow run log before it raises.

#### Terminal state

- **FR-019**: In a run's orchestration summary, when an entry has a recorded terminal state, its `state` MUST be
  that terminal state, whether or not a live observation is available. When it has none, `state` keeps
  today's rule: the live observation when available, else the last retained observation.

#### CLI hint for the Sync API address and token

- **FR-020**: When the CLI prints a `client-input` error for the `service_url` argument, it MUST add a `hint`
  field that names `INFRAHUB_SYNC_API_URL` and `--api-url`.
- **FR-021**: When the CLI prints a `client-input` error for the `token` argument, it MUST add a `hint` field that
  names `INFRAHUB_SYNC_API_TOKEN`.
- **FR-022**: The existing `error` and `argument` fields, their order and the exit code MUST NOT change. Every
  other `client-input` error MUST print no `hint`.

#### Documentation and release notes

- **FR-023**: The documentation MUST describe the artifact, the last log line, the refused claim, the setting
  and the CLI hint, in the orchestration guide, the Compose deployment guide, the Sync HTTP API reference and
  the CLI reference.
- **FR-024**: Each user-visible change MUST have a news fragment under `changelog/`.

### Key Entities

- **Run summary**: one markdown document per stage of a run, published as a Prefect artifact. It holds the run
  ID, stage, outcome, configuration and version, destination branch, operation counts and next command.
- **Orchestration entry**: one Prefect flow run that executes a run's stage. It gains the optional
  `prefect_flow_run_url`.
- **Client-input error**: the CLI's error for an invalid argument. It gains an optional `hint`.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: On the development stack, each of the four stages, plan, apply, sync and verify, produces exactly
  one artifact with the key `sync-run-<run_id>-<stage>`.
- **SC-002**: In 100% of the unit tests that end a stage with success or failure, the last log line names a
  next step.
- **SC-003**: With the development stack's default settings, `runs show` prints a `prefect_flow_run_url` that
  opens the run's flow run page in the Prefect UI.
- **SC-004**: An operator without `INFRAHUB_SYNC_API_URL` set reads the variable's name in the first error the
  CLI prints.
- **SC-005**: Existing assertions on run responses, on `runs show` output and on `client-input` errors keep their
  expected values. The only additions are the `prefect_flow_run_url` and `hint` lines. Tests of the refused
  claim and of the last log line may be tightened to the new error type and the new line.
- **SC-006**: `uv run invoke tests.tests-unit`, `uv run ruff check .` and `uv run invoke lint` pass.

## Assumptions

- The Prefect UI's flow run page path is `/runs/flow-run/<flow_run_id>`, as in Prefect 3.
- An artifact is published inside a real Prefect flow run only. The worker already knows when it runs inside
  one.
- The CLI reads the token only from `INFRAHUB_SYNC_API_TOKEN` and has no token option (`infrahub_sync/cli.py:158`),
  so the token hint names only the variable.
- A refused claim is detected where the worker claims its execution today
  (`infrahub_sync/service/flow.py:408-422`).
- The `run:` and `stage:` tags and `runs artifacts` are already delivered and are out of scope.

## Out of Scope

- New HTTP routes.
- Changes to stored run records, other than optional response fields.
- Scheduling through Prefect.
- Replaying a Sync run from Prefect.
