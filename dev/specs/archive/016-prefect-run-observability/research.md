# Research: Prefect run observability and the missing Sync API address

All findings were read from `feature/v3-develop` at `3c617102`. The reference commits are
[`43986fa9..188bbb1d`](https://github.com/opsmill/infrahub-sync/compare/43986fa9~1...188bbb1d).

## R1. Where a stage ends, and where the last log line and the artifact go

**Finding**: `service_sync_run` (`infrahub_sync/service/flow.py:864-950`) claims the execution, runs
`_execute_stage`, and then takes one of two exits:

- On success, `_commit_success` stores the result.
- On failure, `_record_failure` (`flow.py:776`) stores the failure, and the sanitized error is raised again.

Today the only final log line is written inside `_execute_stage`, at `flow.py:714`, before the success is
committed. A stage whose commit fails therefore logs `outcome=<success>` and then fails.

**Decision**: Move the final log line out of `_execute_stage` into `service_sync_run`, after the exit is
known:

- After a successful commit, log the outcome that the result holds.
- After `_record_failure`, log the terminal outcome that it stored.

Publish the artifact at the same two points, after the line is logged.

**Rationale**: One place for both side effects, after the run record is final. That gives the spec's order:
the record is stored, then the log line, then the artifact.

**Alternatives considered**: Keep the line at `flow.py:714` and add a second line on failure. Rejected,
because the success line would still be written before the commit, and the reference commit `dcbf391a` also
moved it.

## R2. The next action of a failure

**Finding**: `operator_cause` (`infrahub_sync/service/failure_cause.py:29`) records a redacted `next_action`
only when this repository wrote it, either from a lineage refusal or from a `PlanArtifactError`.
`_failure_evidence` stores it in `results["<stage>_failure"]`. `_record_failure` returns `None` today.

**Decision**: `_record_failure` returns the stored terminal outcome and the evidence's `next_action`, as a
small frozen dataclass `RecordedFailure(outcome, next_action)`, or `None` when nothing was stored. When a stage
succeeds but its result carries a failure, as a `sync` that stops on left-out records does, read
`result["failure"]["next_action"]`. The CLI already prints that field at `cli.py:809`.

**Rationale**: The next action stays the redacted, repository-written text. No exception text reaches the log.

## R3. Prefect run context

**Finding**: `_run_logger()` (`flow.py:133`) returns `(logger, prefect_context)`. `prefect_context` is true only
inside a real Prefect run. `_remote_log_bridge` already uses this flag.

**Decision**: Publish the artifact only when `prefect_context` is true. Inject the publishing function
(`create_markdown_artifact` by default), so that unit tests pass a recorder.

## R4. Artifact content and key

**Finding**: `ProductRun` (`infrahub_sync/product_store/models.py:175-197`) holds these fields:

- `run_id`
- `operation`
- `config_id`
- `registry_version`
- `destination_branch`
- `outcome`
- `summary`
- `results`
- `artifact_refs`

A plan's `summary` holds `by_action` and `by_kind`, and an apply's summary holds one count per action key
(`ACTION_KEYS`). The reference module listed `runs problems` and `runs left-out` commands that do not exist on
this branch. The `runs` group has `list`, `show`, `plan` (with `--detail`), `results`, `artifacts` and `cancel`.

Prefect artifact keys accept only lowercase letters, digits and dashes. A run ID such as
`20261105T1514-f62dfb83` holds an uppercase `T`.

**Decision**: Build a new `infrahub_sync/service/run_artifact.py` from the reference, with these changes:

- Show `config_id@registry_version` and the destination branch.
- Show `by_action` and `by_kind` counts.
- List only commands that exist: `runs show`, `runs plan <id> --detail` when a saved plan review exists, then
  the failure's `next_action`.
- Lowercase the key and replace every other character with a dash.
- Pass the finished markdown through `redact(..., secrets)`.

**Alternatives considered**: Cherry-pick `43986fa9`. Rejected, because the commit names commands this branch does
not have and reads summary keys that 009 added.

## R5. Link to the Prefect flow run

**Finding**: `build_app` (`infrahub_sync/service/serve.py:45-75`) constructs `RunService` from the environment.
`RunService.__init__` (`infrahub_sync/service/service.py:142`) takes keyword options. Both orchestration entries
are built at `service.py:1011` and `service.py:1033`, as `client.models.OrchestrationSummary`, which is the model
that the API and the client share.

**Decision**:

- Add `infrahub_sync/service/prefect_ui.py` with `prefect_ui_url_from_environment()`. It reads
  `INFRAHUB_SYNC_PREFECT_UI_URL`. When the setting is empty or unset, it returns `None`. When the setting is
  invalid, it raises `ValueError("INFRAHUB_SYNC_PREFECT_UI_URL must be an absolute http or https URL without
  credentials, query or fragment")`. The message never includes the value.
- Add `flow_run_url(base, flow_run_id)`, which returns `f"{base.rstrip('/')}/runs/flow-run/{flow_run_id}"`.
- `build_app` reads the setting once and passes `prefect_ui_url=` to `RunService`.
- `OrchestrationSummary` gains `prefect_flow_run_url: str | None = None`.

**Rationale**: Validating once at start-up, through the same `ValueError` path as other settings, refuses a bad
value before any request is served. The path `/runs/flow-run/<id>` is the route that Prefect 3.8.6 uses for its own
UI links (`prefect/utilities/urls.py:37`, `"flow-run": "runs/flow-run/{obj_id}"`). In the same release,
`prefect/runtime/flow_run.py:330` builds `/flow-runs/flow-run/<id>`. The live check in `quickstart.md` confirms
which path the UI opens.

## R6. Refused claim

**Finding**: `_claim_current_execution` (`flow.py:408-423`) raises `RuntimeError(_WORKER_EXECUTION_REFUSED)` when
`projection.claim_execution` returns false. Tests match on `_WORKER_EXECUTION_REFUSED`.

**Decision**: Add `ExecutionClaimRefusedError(RuntimeError)`. Its message is
`_WORKER_EXECUTION_REFUSED + ": " + fixed guidance`, built from the run ID only. `service_sync_run` catches it
around the claim, logs it at ERROR level through `run_logger`, and raises it again. Existing matches on the
prefix still pass.

## R7. Terminal state

**Finding**: `service.py:1037` sets `state=live if observed.available else retained`.

**Decision**: When `link.terminal_state` is set, `state` is `link.terminal_state` in both branches. Otherwise
keep the current rule.

## R8. CLI hint

**Finding**: `_error_fields` (`cli.py:202-205`) renders `ClientInputError` as `("client-input",
(("argument", error.argument),))`. The URL arrives through `--api-url` or `INFRAHUB_SYNC_API_URL`
(`cli.py:127-129`). The token arrives only through `INFRAHUB_SYNC_API_TOKEN` (`cli.py:158`). The client raises
`ClientInputError("service_url")` and `ClientInputError("token")` (`client/client.py:103,133-141`). The CLI
already prints a `hint:` line for a run failure's next action (`cli.py:811`).

**Decision**: In `_error_fields`, add `("hint", ...)` after `argument` for exactly the two arguments, from a
fixed table:

| Argument | Hint |
| --- | --- |
| `service_url` | `set INFRAHUB_SYNC_API_URL or pass --api-url to an absolute http or https URL of the Sync API` |
| `token` | `set INFRAHUB_SYNC_API_TOKEN to a Sync API bearer token` |

Exit code and field order are unchanged.
