# Implementation Plan: Prefect run observability and the missing Sync API address

**Branch**: `016-prefect-run-observability` | **Date**: 2026-11-05 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `dev/specs/016-prefect-run-observability/spec.md`

## Summary

Five Prefect behaviours were approved on 2026-10-20 but never merged into `feature/v3-develop`. This plan
builds them again against the current service code:

- a run-summary markdown artifact for each stage;
- a last log line that names the next step;
- an optional link from each orchestration entry to its Prefect flow run;
- an explanation when the worker refuses a flow run;
- the recorded terminal state reported as a finished entry's state.

The CLI also gains a `hint` field when the Sync API address or token is missing. Each change is delivered
test-first. The design decisions are in [research.md](./research.md).

## Technical Context

**Language/Version**: Python 3.10–3.13. The Sync service runs on 3.11–3.13.

**Primary Dependencies**:

- Prefect 3.8.6: `prefect.artifacts.create_markdown_artifact` and the run logger;
- FastAPI and pydantic, through the existing `client.models`;
- Typer, for the CLI;
- httpx, for URL parsing.

**Storage**: No change. The product store already holds every value the artifact reads.

**Testing**: pytest unit tier (`uv run invoke tests.tests-unit`). The tests extend these files:

- `tests/service/test_flow_and_prefect.py`
- `tests/service/test_worker_claim.py`
- `tests/service/test_status_observation_budget.py`
- `tests/cli/test_client_commands.py`

New test files are `tests/service/test_run_artifact.py`, `tests/service/test_run_log_next_step.py` and
`tests/service/test_prefect_ui.py`.

**Target Platform**: the Sync API and worker containers (Linux), and the CLI on any host.

**Project Type**: a Python package with a CLI, an HTTP service and a Prefect worker.

**Performance Goals**: Each stage adds one artifact call and one store read. The run time does not change in a
measurable way.

**Constraints**:

- ADR 0021 applies: no provider text in operator output.
- The documented redaction rules apply.
- No new HTTP routes.
- Response fields are optional only.
- Existing CLI fields and exit codes do not change.

**Scale/Scope**: about six source files, four test files, two Compose files, four documentation pages and
changelog fragments.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Check | Result |
| --- | --- | --- |
| I. Read-only and dry-run by default | No new mutating path. Artifacts and log lines are written to Prefect, not to the destination. `plan` stays safe. | Pass |
| II. Sync idempotency and safety | A failed artifact publish never changes a run's outcome (FR-004). A refused claim still reads and writes nothing (FR-017). | Pass |
| III. Adapter symmetry | No adapter change. | Not applicable |
| Project policy: typed code, specific exceptions, structlog, no secrets | New `ExecutionClaimRefusedError` (a specific type). The URL setting is validated without printing its value. The artifact is redacted. | Pass |

Re-check after design: Pass. The data model adds two optional fields only.

## Project Structure

### Documentation (this feature)

```text
dev/specs/016-prefect-run-observability/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── run-response.md
│   ├── cli-output.md
│   └── prefect-surfaces.md
└── tasks.md
```

### Source Code (repository root)

```text
infrahub_sync/
├── cli.py                     # hint field in _error_fields; prefect_flow_run_url in runs show
├── client/models.py           # OrchestrationSummary.prefect_flow_run_url
└── service/
    ├── flow.py                # final log line, artifact publish, ExecutionClaimRefusedError
    ├── run_artifact.py        # new: artifact key and markdown from the run record
    ├── prefect_ui.py          # new: setting validation and flow run URL
    ├── serve.py               # read the setting, pass it to RunService
    └── service.py             # terminal-state rule; build the URL per entry

tests/
├── cli/test_client_commands.py
└── service/
    ├── test_run_artifact.py          # new
    ├── test_run_log_next_step.py     # new
    ├── test_prefect_ui.py            # new
    ├── test_worker_claim.py
    └── test_status_observation_budget.py

development/docker-compose.yml   # INFRAHUB_SYNC_PREFECT_UI_URL on sync-api
docker-compose.yml               # setting documented and passed through
docs/docs/                       # orchestration, compose-deployment, reference/sync-http-api, reference/cli
changelog/                       # one fragment per user-visible change
```

**Structure Decision**: The single package layout stays as it is. The two new modules sit in
`infrahub_sync/service/` next to `failure_cause.py`, which plays the same role for failure text.

## Stage exits

`service_sync_run` ends a claimed stage through one of three exits. The last log line and the artifact are
produced at each exit, in this order: store the record, write the log line, publish the artifact. Both are
redacted with the stage's `secrets`, so on the failure exits both happen before `secrets.clear()`
(`flow.py:948`).

| Exit | When | `<outcome>` | `<action>` |
| --- | --- | --- | --- |
| Success | `_commit_success` returned | `result["outcome"]` | `result["failure"]["next_action"]` when present, else `` `infrahub-sync runs show <id>` `` |
| Recorded failure | `_record_failure` returned a `RecordedFailure` | its `outcome` | its `next_action`, else `` `infrahub-sync runs show <id>` `` |
| Unrecorded failure | `record_terminal` is false, or `_record_failure` returned `None` | `failed` | `` `infrahub-sync runs show <id>` `` |

The line is written with `run_logger` directly, outside `_remote_log_bridge`. A refused claim happens before any
exit, so it gets no artifact and no last line, only its ERROR line.

`_ResourceModel` uses `extra="allow"` (`client/models.py:91`), so a CLI from before this change accepts a
response that holds `prefect_flow_run_url`.

## Test-first order

Each requirement group is one red-green cycle, in this order, because the later groups use the earlier ones:

1. CLI hint (FR-020–FR-022): independent and the smallest.
2. Terminal state (FR-019): independent.
3. Refused claim (FR-016–FR-018): independent.
4. Last log line (FR-007–FR-009): it changes `_record_failure`'s return value, which item 5 uses.
5. Run-summary artifact (FR-001–FR-006): it uses the outcome and next action from item 4.
6. Prefect UI link (FR-010–FR-015): the model, service, serve, CLI and Compose files.
7. Documentation and changelog (FR-023–FR-024).

## Complexity Tracking

No constitution violation to justify.
