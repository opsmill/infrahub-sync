---
title: "Prefect orchestration"
toc_max_heading_level: 4
---

## Prefect orchestration

Prefect executes Sync work through two integrations. Registered runs use the Sync API and
its service worker; the direct integration runs a read-only plan from a local configuration
without the Sync API.

| Integration | Input | Where to inspect the result |
| --- | --- | --- |
| Registered service | An API-created product run, bound to a registered configuration version | Sync API run, plan and results resources, with links to Prefect executions |
| Direct Prefect | A read-only plan for a configuration name resolved from the serving process's directory | Prefect flow-run logs and the summary line |

For the Compose deployment and the lifecycle of a registered run, start with
[Sync architecture](sync-architecture.md). The direct integration is a separate optional
Python entrypoint, not the Compose operator workflow.

### Registered service integration

`infrahub_sync/service/` provides the Sync API and its Prefect integration, installed by
the `service` extra. The API submits work to `infrahub-sync-service/run`; a process worker
executes the installed `infrahub_sync.service.flow.service_sync_run` function.

The worker, `infrahub_sync/service/worker.py`, runs nothing else. Everything that decides
what a process child executes is data on the Prefect server, so `ServiceProcessJobConfiguration`
admits a flow run only when it belongs to the `SERVICE_DEFINITION` deployment exactly as
`deploy` and `bootstrap` write it: the service flow and deployment names, the installed
module entrypoint, no pull steps, no job variables on the deployment or the flow run, the
process pool's default job template, and the default child command with no working
directory. A refusal raises `ServiceFlowRunRefusedError` while the configuration is
prepared, which Prefect records as a `Crashed` state before any process starts; its
reason is fixed text and echoes no server value. An admitted run is marked as having a
configured command, so Prefect starts it with its direct engine starter, which runs exactly
the admitted command. Without that, Prefect 3.8.6 starts a workspace supervisor for a run
with no configured command: it copies code from the deployment's storage into the run's
workspace and can relaunch through `uv run`, and admission checks none of that. The admitted child then loads the
deployment from the server again, so the check bounds what a run can execute only while
no one else can write to the deployment; that is what the API authentication below
provides. Anything the service applies to its own
deployment later, such as job variables, has to be admitted here in the same change. The
Compose bundle also turns on Prefect's API basic authentication, so reaching the Prefect
API is not enough to create a deployment in the pool.

The service uses the vendored OpsMill Prefect Extras package for deployment catalogue
validation, deployment convergence and submission idempotency. The API retains product
records; Prefect records live execution state and logs. See
[durable product records](../../reference/durable-product-records.mdx) for the distinction.

```python
@flow(name="infrahub-sync-service", flow_run_name=_service_run_name)
def service_sync_run(
    run_id: str,
    stage: Literal["plan", "verify", "apply", "sync"],
    config_id: str | None = None,
    registry_version: int | None = None,
    package_checksum: str | None = None,
    branch: str | None = None,
    expected_checksum: str | None = None,
    confirm_writes: bool = False,
    configuration_name: str | None = None,
    confirm_left_out: bool = False,
) -> dict[str, Any]: ...
```

`confirm_left_out` is the person's confirmation that they reviewed the records a plan leaves
out (spec 007). The API adds it to the flow parameters only when it is true, so an apply that
confirms nothing keeps the parameters of earlier releases. The worker passes it to
`Potenda.apply_plan`, which checks it again against the plan manifest's left-out count and
raises `LeftOutConfirmationRequiredError` before any write when it is missing. Because the
deployment's parameter schema must list the parameter, the deployment is applied again
(`python -m infrahub_sync.service.deploy`, or the Compose `sync-bootstrap` job) before or
together with the Sync API of the release that sends it.

A `sync` stage whose plan leaves out records does not write. After the plan checkpoint,
`service_sync_run` commits the stage as phase `planned`, outcome `left-out-review-required`,
with a `failure` in the stage result (`failure_type: left_out_review_required`, `left_out`,
`next_action`), and the store releases the run's write admission in the same commit, so a
confirmed `apply` can follow once. The stage event is recorded as `succeeded`.

`configuration_name` is the configuration's readable name and serves only the run's
title. The API sets the title when Prefect accepts the run, and `flow_run_name` sets it
again when the run starts, for example `Plan sync of netbox-to-infrahub`, instead of the
name Prefect generates. The title follows the rule Infrahub applies to its own flow runs:
it says what the run does and leaves out the branch and any ID, which the run's page
already shows.

The API also sets the run's tags when it creates the flow run. `PrefectOrchestration.submit`
wraps the Prefect client in `_LabelledCreationClient`, which passes every call through and adds
`name=` and `tags=` to `create_flow_run_from_deployment`. The vendored executor is built from the
wrapper for each submission, so its code is unchanged. `run_tags` builds the tags from
identifiers the run resource already publishes: `run:<run_id>`, `stage:<stage>`, and, for a run
of a registered configuration, `config:<config_id>` and
`config-version:<config_id>@<registry_version>`. It never uses the actor, the branch, the work
pool or a secret. Prefect adds the deployment's tags to these, including a tag an operator added
to the deployment. A retry with the same idempotency key returns the flow run Prefect created
first, with the title and tags it was created with. Sync does not read or update tags after
creation, so no tagging call can fail after Prefect accepted the run.

Registered execution supplies all three configuration fields: `config_id`,
`registry_version` and `package_checksum`. The worker checks them against the product run
and registered package. The worker refuses a partial binding. It also refuses, with one
fixed message, an execution in which all three fields are unset and a stored run that has
no registered configuration, before it resolves a configuration or contacts a source or
destination system. By then the worker has contacted Prefect and claimed the execution.

The worker reads endpoint settings from the package and resolves credential references
in its environment. Each stage uses private
temporary files and exchanges plans through object storage; it reads no shared cache
location. See the [registered-run lifecycle](sync-architecture.md#one-registered-run) for
planning, verification, approved writes and result persistence.

#### What a stage leaves in Prefect

<!-- Extracted from dev/specs/archive/011-prefect-run-observability on 2026-11-09 -->

An operator who opens a service flow run in the Prefect UI reads the stage's outcome and the
next step there. The worker writes what Prefect shows about the outcome only after it has
stored that outcome in the run record
([ADR 0024](https://github.com/opsmill/infrahub-sync/blob/feature/v3-develop/dev/adr/0024-prefect-surfaces-follow-the-stored-run-record.md)).

- **Order.** `service_sync_run` stores the outcome first: `_commit_success` on success, or
  `_record_failure` on failure. `_record_failure` returns a frozen `RecordedFailure`, which
  holds the stored terminal outcome (`failed` or `ambiguous`) and the evidence's redacted
  `next_action`. It returns `None` when it stored nothing. The worker then writes the last log
  line, and then publishes the run summary artifact. Both receive the same outcome and next
  action, and the worker does not reread them from the stored record:
    - On success, they are the stage result's `outcome` and next action. The stored terminal
  outcome is `succeeded`, so the log line can name a more specific outcome, such as
  `verified`.
    - On a stored failure, they are the outcome and `next_action` of the `RecordedFailure`.
    - On a failure that was not stored, `_failure_exit` writes `outcome=failed` with
  `infrahub-sync runs show <run_id>` as the next step, and the run record stays nonterminal
  for reconciliation to decide. This happens when `_record_failure` returns `None`, or when
  a read stage's success commit fails.
- **Last log line.** `final_log_line` writes
  `service run <run_id> stage=<stage> outcome=<outcome>; next step: <next step>`. The next
  step is the failure's `next_action`. A stage that succeeds with a failure in its result,
  such as a `sync` that stops on left-out records, uses `result["failure"]["next_action"]`,
  the field that the CLI prints. Without a next action, the next step is
  `infrahub-sync runs show <run_id>`.
- **Failure exit.** `_failure_exit` ends a failed stage. When the recorded failure has a cause,
  it writes one ERROR line, `<cause> Next step: <next step>`, and returns a `Failed` state with
  the same text, so no traceback passes through Prefect's engine. For any other failure,
  including one that was not recorded, the ERROR line holds only the class name of the error,
  and the flow raises a sanitized copy of the error. The lines and the artifact are written
  before the stage's secrets are cleared.
- **Run summary artifact.** `infrahub_sync/service/run_artifact.py` publishes one markdown
  artifact for each stage, and only when the stage runs inside a real Prefect flow run (the
  `prefect_context` flag of `_run_logger`). The artifact always holds the run ID, the stage
  and the outcome. It also holds `config_id@registry_version`, the destination branch, and
  the operation counts by action and by kind, each only when the run record has that value.
  A `plan` or `sync` stage whose summary records them adds `Records left out: <n> (with a
  problem: <n>, depending on one: <n>)` and `Problems: <n>`.
  It ends with the next steps: `runs show`, `runs plan <id> --detail` when the run
  holds a saved plan review, `runs plan <id> --problems` and `runs plan <id> --left-out` when
  the plan leaves out records, then the next action. The key is `sync-run-<run_id>-<stage>`,
  lowercased, with every character other than `a-z`, `0-9` and `-` replaced by a dash,
  because Prefect artifact keys accept only those characters. A later flow run of the same
  stage adds a version to the same key. The markdown passes through `redact` before it is
  published. When the run record cannot be read, or publishing fails, the worker logs a
  warning, and the run does not fail. `publish_run_artifact` receives the publishing function
  (`create_markdown_artifact` in the worker), so unit tests pass a recorder.
- **Refused claim.** When the worker refuses to claim a flow run that the Sync API is not
  waiting for, such as a Prefect Retry or a run started by hand from the deployment, it raises
  `ExecutionClaimRefusedError`. The message is built from fixed text and the run ID, and says
  that nothing was read or written and how to start a new Sync run. `service_sync_run` logs
  the message at ERROR level in the flow run log, then ends the flow run by returning a `Failed`
  state with the same message. It does not raise the error again, so Prefect's engine does not
  log the refusal a second time with a traceback.
- **Reported state.** In the run response, an orchestration entry that has a recorded
  `terminal_state` reports that verdict as its `state`. Otherwise it reports the live
  observation when available, and the retained observation when not.
- **Link to the flow run.** `infrahub_sync/service/prefect_ui.py` reads
  `INFRAHUB_SYNC_PREFECT_UI_URL` once, when `build_app` starts. An empty or unset value
  turns the link off. A value that is not an absolute `http` or `https` URL, or that carries
  credentials, a query or a fragment, stops start-up with a `ValueError` whose message names
  the setting but not the value. When the setting is set, each orchestration entry carries
  `prefect_flow_run_url`, `<base>/runs/flow-run/<flow_run_id>`, which is the route that
  Prefect 3.8.6 uses for its own UI links. `runs show` prints it.

#### How the Sync API reads a run's log

<!-- Extracted from dev/specs/archive/012-sync-run-events-logs on 2026-11-14 -->

A person reads a run's log with `infrahub-sync runs logs <run_id>` or `GET /runs/{run_id}/logs`,
without opening Prefect. The Sync API keeps no copy of the log. It reads the lines from Prefect
for each request and returns only the lines that Sync wrote (ADR 0034).

- **Reading.** `Orchestration.read_logs` (`infrahub_sync/service/orchestration.py`) takes the
  flow run IDs of the run's executions. It first reads each flow run, to tell a flow run that
  Prefect no longer holds from one that it holds. It then reads the log lines in pages of
  `LOG_PAGE_SIZE` (200), oldest first, until a page is not full.
- **Sync lines.** `sync_lines` (`infrahub_sync/service/run_log.py`) keeps a line when its message
  matches `SYNC_LINE_PATTERN`: an `infrahub_sync` logger name, then ` | `. `RunLoggerBridge`
  writes every forwarded line in this form, and `SyncFlowLines` (`infrahub_sync/service/flow.py`)
  writes the service flow's own lines with the prefix `infrahub_sync.service.flow | `. Lines that
  Prefect's engine writes do not match and are left out. A line of a flow run that the run record
  does not name is left out too.
- **Fields.** Each returned line holds the `stage` and `attempt` of its execution, the time, the
  level name, the logger name as `source` and the text after the prefix as `message`.
- **Order and paging.** Lines follow the order of the run's executions, then the time, then
  Prefect's log ID. `offset` and `limit` apply to the kept lines, so a reader who polls sends the
  number of lines it already holds.
- **Redaction.** Each message is redacted with the Sync API's secrets, loses its control
  characters other than line breaks and tabs, is redacted again, and is then cut at 4,096
  characters.
- **Errors.** The route answers `410 log-expired` when Prefect no longer holds a flow run that
  the run record names, and `503 log-unavailable` when Prefect cannot be reached. The Sync API
  logs the class of the error; the response text does not change.
- **Wording and retention.** `tests/service/test_prefect_wording.py` fails when an API message
  holds the word "Prefect", or when a module under `infrahub_sync/` calls a Prefect flow run
  delete.

### Direct integration {#the-flow}

`infrahub_sync/orchestration/` provides the optional direct integration, installed by the
`prefect` extra. It calls `run_remote_request` in
[the shared execution module](execution-surface.md) in-process.
The flow takes four parameters:

```python
@flow(name="infrahub-sync")
def infrahub_sync_run(
    sync_name: str,
    operation: Literal["plan", "sync"] = "plan",
    confirm_writes: bool = False,
    branch: str | None = None,
) -> dict[str, Any]: ...
```

Only `operation="plan"` executes here. Although `"sync"` remains in the parameter schema,
`run_remote_request` refuses it before resolving a configuration, even when
`confirm_writes=True`. Submit writes through the Sync API.

None of the four parameters accepts a path, a CLI fragment, a credential, or an
environment override. Other runtime settings come from the serving process's
configuration and environment.

The flow name is `infrahub-sync` and the deployment name is `run`, so the deployment lookup
path is `/api/deployments/name/infrahub-sync/run`.

The flow attaches the log bridge, reads `INFRAHUB_SYNC_CONFIG_DIRECTORY`, calls
`run_remote_request`, logs the summary and returns a dictionary. Exceptions propagate to
Prefect with sanitized messages.

#### The summary line is the supported result surface

```python
SUMMARY_LINE_FORMAT = "run %s finished: status=%s changed=%s summary=create:%d,update:%d,delete:%d artifact=%s"
```

This line is how a remote caller reads a run's outcome, and its format is contractual —
never a Python dictionary `repr`. It contains five `RunResult` fields: `run_id` (the leading
substitution), `status`, `changed`, the three summary counts, and `artifact_path`.
`sync_name` and `operation` deliberately do not appear. Changing the format is a breaking
change for consumers.

Result retrieval through Prefect's own result persistence is not part of the contract.

#### The return dictionary {#the-return-value-is-built-by-hand-not-with-asdict}

The flow builds a seven-key dictionary from the `RunResult` fields:

```python
out = {f.name: getattr(result, f.name) for f in dataclasses.fields(result)}
out["summary"] = dict(result.summary)
```

`dataclasses.asdict(result)` cannot be used. It deep-copies field values, and `RunResult`
wraps `summary` in a `MappingProxyType`, which is not deep-copyable —
`TypeError: cannot pickle 'mappingproxy' object`. Using it here would fail a successful run while
constructing the return value.

#### The log bridge

`RunLoggerBridge` forwards records from the `infrahub_sync` logger hierarchy to the Prefect
run logger, preserving each record's level and originating logger name and redacting secrets.
The direct flow sets the source logger to `INFO` and disables propagation while the bridge
is attached. In `finally`, it removes the bridge and restores both the previous level and
propagation setting.

Those settings are process-global. `_REMOTE_LOGGER_OWNERSHIP_LOCK` covers request execution
as well as log forwarding within a process. Concurrent direct-flow calls therefore run one
at a time and cannot attach competing bridges or restore each other's logger settings. It does not serialize runs in separate processes.
The service flow uses the same lock and bridge when a Prefect run context exists.
In offline executor tests, it catches `MissingContextError` and uses its module logger
without attaching a bridge.

#### The serve entrypoint

Run as `python -m infrahub_sync.orchestration.serve`. It:

1. checks for a missing `prefect` import, reports the extra and install command, and exits
   non-zero; unrelated import failures propagate;
2. reads `INFRAHUB_SYNC_CONFIG_DIRECTORY` and, if it is unset, empty, or not an existing
   directory, emits one error line **naming the variable** and exits non-zero before any
   deployment is served;
3. calls `infrahub_sync_run.serve(name=DEPLOYMENT_NAME)` — a locally served deployment, no
   work pool, no separate worker;
4. serves until interrupted.

Startup errors are written to standard error through `sys.stderr.write`.

The directory path is fixed at serve start; its *contents* are re-resolved on every run, so
configurations added, edited or removed take effect on the next run without re-serving.

The serve process must be started from the repository root for the shipped example to work:
its `config.yml` uses repository-root-relative paths resolved against the serving process's
working directory, and the cache root defaults to `Path.cwd()/.infrahub-sync-cache`. Started
elsewhere, the example degrades to a silently empty plan or an adapter import failure.

#### Remote interaction

Direct-integration callers use Prefect's API. The paths below are relative to the server
origin; `$PREFECT_API_URL` normally already includes `/api`.

| Step | Request |
|---|---|
| Find the deployment | `GET /api/deployments/name/infrahub-sync/run` |
| Create a run | `POST /api/deployments/{id}/create_flow_run` with `{"parameters": {…}}` |
| Observe state | `GET /api/flow_runs/{id}` |
| Read logs and the summary line | `POST /api/logs/filter` with `{"logs": {"flow_run_id": {"any_": ["{id}"]}}}` |

Run creation returns the flow-run identifier **synchronously**, in state `SCHEDULED`.

A served deployment defaults to `enforce_parameter_schema=True`, so the `operation`
annotation becomes an enum in the deployment's parameter schema and an invalid value is
refused at run *creation*: `POST … /create_flow_run` with `"operation": "apply"` returns
**HTTP 409** and **no flow run object is created at all**. Input validation for that
parameter therefore never reaches the flow body.

#### Prefect-specific traps

- **Keep concrete parameter annotations.** Deferred annotations caused
  `PydanticUndefinedAnnotation: name 'Literal' is not defined` before the flow body ran
  on Prefect 3.5.0. The module omits `from __future__ import annotations`, and a test
  checks that the operation annotation resolves to `Literal["plan", "sync"]`.
  The tests also check refusal of an invalid operation during parameter validation on
  Prefect 3.8.6.
- **`PREFECT_LOCAL_STORAGE_PATH` does not follow `PREFECT_HOME`.** Redirecting
  `PREFECT_HOME` isolates the database but not persisted run results. Test isolation — and
  any operator who wants one directory — needs both variables set.
- **`dataclasses.asdict()` cannot copy a `MappingProxyType` field** — construct the return
  dictionary explicitly, as shown above.
- **Pinning the version is not optional here.** The extra pins `prefect==3.8.6` exactly.
  The pin dates from the time the base dependency set declared `redis`, which interacted with
  Prefect's transitive `redis` requirement — see
  [ADR 8](https://github.com/opsmill/infrahub-sync/blob/f98a845986d1f03503d321ce5561b65a3946bf74/dev/adr/0008-declare-redis-directly-instead-of-the-diffsync-extra.md).
  [ADR 44](https://github.com/opsmill/infrahub-sync/blob/feature/v3-develop/dev/adr/0044-the-base-install-declares-no-redis-client.md)
  removed the base declaration; the exact pin stays.
- **A Prefect upgrade moves three pins together.** The Prefect server runs from the Sync image
  through `infrahub_sync.service.prefect_server:create_prefect_app`, so the `prefect` pin sets
  both the server and the client version. `fastapi` is pinned at 0.141.1 in the `prefect` and
  `service` extras because FastAPI 0.159 breaks `prefect.server.api.server.create_app` on
  Prefect 3.8.6; check the server factory before you move it. The `testcontainers` extra pulls
  in `prefect-client` through `infrahub-testcontainers`, which pins it exactly. `prefect` and
  `prefect-client` install into the same `prefect/` directory, so `uv.lock` must resolve them
  to one version; `tests/test_release_pin.py` fails when they differ. See
  [ADR 0030](https://github.com/opsmill/infrahub-sync/blob/feature/v3-develop/dev/adr/0030-the-end-to-end-test-class-inherits-infrahubs.md).
- **Importing Prefect configures the root logger.** In the service processes
  (`sync-bootstrap`, `sync-api`, `sync-worker`), Prefect is imported before `main` runs, so a
  later `logging.basicConfig` does nothing and the inherited level drops INFO lines.
  `bootstrap.py` and `trust_store.py` set their own logger to INFO for that reason, and
  `trust_store.py` adds a standard-error handler when no handler would print its lines (the
  Sync API process configures none before uvicorn starts). Both use the standard `logging`
  module, not `structlog`, so the handlers that Prefect configures and pytest's `caplog`
  receive their lines.
- **SQLAlchemy is held below 2.1 while the `prefect` pin is 3.8.6.** With SQLAlchemy 2.1, the
  Prefect 3.8.6 scheduler service fails at each loop in `_insert_scheduled_flow_runs` with
  `InvalidRequestError: Can't evaluate bulk DML statement`, so no deployment schedule creates a
  flow run ([PrefectHQ/prefect#23199](https://github.com/PrefectHQ/prefect/issues/23199)). The
  fix ([PrefectHQ/prefect#23201](https://github.com/PrefectHQ/prefect/pull/23201)) is first
  released in Prefect 3.9.1. The `prefect` and `service` extras declare `sqlalchemy>=2.0,<2.1`.
  Remove both lines when the `prefect` pin moves to 3.9.1 or later; that pin moves with the
  `prefect-client` pin of `infrahub-testcontainers` (previous item).
  `tests/service/test_prefect_scheduler_creates_flow_runs.py` fails with SQLAlchemy 2.1 and
  passes with 2.0.
- **A flow that must run again later cannot return a scheduled state under a worker.** A flow
  that returns `AwaitingRetry` works when a test calls it with `return_state=True`. Under a
  process worker, the child process then asks the engine for the flow's result, the engine raises
  `UnfinishedRun` for a state that is not final, the process ends with status 1, and the runner
  marks the flow run `Crashed`. `_run_again_later` in `service/flow.py` sets the state through
  the Prefect client and raises `prefect.exceptions.Pause` with the stored state instead; the
  process then ends with status 0 and the state stays as stored. A test of such a flow must call
  it as the worker's process does. See
  [ADR 0053](https://github.com/opsmill/infrahub-sync/blob/feature/v3-develop/dev/adr/0053-a-waiting-scheduled-start-ends-its-flow-run-with-the-pause-signal.md).
- **Applying a deployment again keeps the schedules that have a slug.** `apply_deployments` of
  the vendored package sends `schedules: []`, but Prefect's `PATCH /deployments/{id}` keeps the
  existing schedules when each of them has a slug. The update still deletes the future flow runs
  that have not run, and changes the deployment's `updated` time, so Prefect's 5-second scheduler
  loop creates them again.
- **Prefect's scheduler creates flow runs only from the moment its loop runs.** The main loop runs
  every 60 seconds. Creating a deployment schedule does not change the deployment's `updated` time,
  and a change or a deletion of one schedule deletes the future flow runs of every schedule of the
  deployment that have not run. A start within the next minute then gets no flow run.
  `POST /deployments/{id}/schedule` with an empty body creates the flow runs of every active
  schedule from the current time, with the idempotency key of the scheduler; the Sync API calls it
  after it changes a Prefect schedule (`PrefectOrchestration.create_scheduled_runs`). See
  [A scheduled run](sync-architecture.md#a-scheduled-run).

### Optional imports

The `orchestration` and `service` packages contain Sync's Prefect imports; a base install
loads neither integration. The `service` extra also installs the dependencies used by
`opsmill_prefect_extras/`. See
[ADR 9](https://github.com/opsmill/infrahub-sync/blob/f98a845986d1f03503d321ce5561b65a3946bf74/dev/adr/0009-optional-integrations-live-in-their-own-package.md)
for the optional-package boundary.

### See also

- [The shared execution surface](execution-surface.md) — what the flow actually calls.
- [Quality gates](quality-gates.md) — the two CI test legs this integration adds.
- [Direct-flow example](https://github.com/opsmill/infrahub-sync/tree/f98a845986d1f03503d321ce5561b65a3946bf74/examples/prefect_remote_run) — the schema and sample requests.
- [Direct Prefect reference](../../reference/prefect-remote-run.mdx) — configure and call the direct integration.
