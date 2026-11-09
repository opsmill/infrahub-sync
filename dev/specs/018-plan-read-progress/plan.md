# Implementation Plan: Read state for each side of a Plan

**Branch**: `018-plan-read-progress` | **Date**: 2026-11-09 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `dev/specs/018-plan-read-progress/spec.md`

## Summary

An operator who watches a Plan or a `sync` reads, for each side, whether its read is waiting, running, succeeded
or failed, with its times, its read mode and its node counts. This plan:

- makes Potenda report the start and the end of each side's read to an optional observer, and log one line when a
  read ends;
- stores each side's state in one new column of the stage execution's row, written by the worker as the state
  changes;
- returns the state in each orchestration entry of the run response, and shows a side that never ended as
  `not_started` or `interrupted` once the execution is terminal;
- prints the state in `infrahub-sync runs show`.

Each change is delivered test-first. The design decisions are in [research.md](./research.md).

## Technical Context

**Language/Version**: Python 3.10–3.13. The Sync service runs on 3.11–3.13.

**Primary Dependencies**:

- DiffSync's `Adapter.count` and `Adapter.get_all_model_names`, for the node counts;
- FastAPI and pydantic, through `infrahub_sync/client/models.py`;
- Typer, for the CLI.

No new dependency.

**Storage**: The product store (SQLite and PostgreSQL, `infrahub_sync/product_store/store.py`). Two nullable
columns on `prefect_executions`, added by the existing column migration (data-model.md).

**Testing**: pytest unit tier (`uv run invoke tests.tests-unit`). New test files:

- `tests/test_potenda_read_progress.py` (Potenda reports and log lines, with fake adapters);
- `tests/product_store/test_side_read_columns.py` (migration, write, terminal guard, on SQLite; PostgreSQL through
  the existing PostgreSQL isolation tests when they run);
- `tests/service/test_read_progress.py` (the worker's observer, the response derivation, the stage wiring).

Extended files: `tests/test_execution_surface.py` (the private seam), `tests/service/test_http_api.py` (the run
response), `tests/cli/test_client_commands.py` (`runs show`).

**Target Platform**: the Sync API and worker containers (Linux), and the CLI on any host.

**Project Type**: a Python package with a CLI, an HTTP service and a Prefect worker.

**Performance Goals**: A state change is readable within 5 seconds (SC-001). The worker writes up to five times for
each stage execution: two `waiting` documents in one call each, then a start and an end for each side.

**Constraints**:

- The read state holds no exception text, no address, no secret and no configuration content (FR-013).
- A failure to record the state does not change the stage's outcome (FR-007).
- Existing response fields and CLI fields do not change; the new response field is an addition.
- Potenda imports nothing from the service.

**Scale/Scope**: about eight source files, six test files, a store migration, three documentation pages and a
changelog fragment.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Result |
| --- | --- |
| I. Read-only and dry-run by default | Pass. The feature records what reads do. It adds no write to a source or a destination. |
| II. Sync idempotency and safety | Pass. Recording the state never changes the stage's outcome, and a write after the execution ended is dropped. |
| III. Adapter symmetry and pattern consistency | Pass. The state is reported in Potenda, where every adapter's read goes through. No adapter changes. |
| IV. Type safety and explicit contracts | Pass. A `Protocol` for the observer, frozen dataclasses for the report, pydantic models for the response. |
| V. Test discipline | Pass. Each change starts with a failing unit test. |
| VI. Security, secrets, and input boundaries | Pass. Only fixed states, times, counts, kind names and class names are stored. Kind names come from the configuration's schema mapping, as in the plan summary's `by_kind`. |
| VII. Simplicity and maintainability | Exception recorded below: the observer protocol has one implementation. |

Post-design check: unchanged. The data model and the contracts add no field beyond those the spec requires.

## Project Structure

### Documentation (this feature)

```text
dev/specs/018-plan-read-progress/
├── plan.md              # This file
├── research.md          # Phase 0
├── data-model.md        # Phase 1
├── quickstart.md        # Phase 1
├── contracts/
│   ├── run-response.md
│   └── cli-output.md
└── tasks.md             # Phase 2 (/speckit-tasks)
```

### Source Code (repository root)

```text
infrahub_sync/
├── potenda/
│   ├── __init__.py              # _load_side reports to read_observer and logs the end line
│   └── read_progress.py         # new: SideReadObserver, SideReadReport
├── execution.py                 # private _read_observer seam on execute_run
├── product_store/
│   ├── models.py                # PrefectExecutionLink.source_read, destination_read
│   └── store.py                 # two columns, migration, record_side_read
├── service/
│   ├── read_progress.py         # new: StoreSideReadObserver, response derivation
│   ├── flow.py                  # _execute_stage and _plan pass the observer for plan and sync
│   └── service.py               # _resource_with_observations sets OrchestrationSummary.reads
├── client/models.py             # StageReads, SideRead, OrchestrationSummary.reads
└── cli.py                       # _echo_run prints source_read and destination_read

docs/docs/
├── reference/sync-http-api.mdx  # the reads field
├── reference/cli.mdx            # the runs show fields
└── running-a-sync.mdx           # what the operator views during a Plan

changelog/+plan-read-progress.added.md
```

## Shell commands

```bash
uv sync --extra dev --extra prefect --extra service
uv run pytest tests/test_potenda_read_progress.py tests/product_store/test_side_read_columns.py tests/service/test_read_progress.py -q
uv run invoke tests.tests-unit
uv run invoke format
uv run invoke lint
uv run ruff check .
uv run infrahub-sync runs --help
```

## Complexity Tracking

| Exception | Why needed | Simpler alternative rejected because |
| --- | --- | --- |
| `SideReadObserver` protocol with one implementation (principle VII) | The worker must learn of each read's start and end while Potenda runs, without Potenda importing the service. | Potenda writing to the product store would tie the engine to the service; a callable without a protocol would erase the method names that tests and type checks rely on. |

## Dependencies on other work

Implementation starts after pull request #402 (spec 017) and the spec 016 branch are merged into
`feature/v3-develop`. This branch is built on `origin/017-sync-run-events-logs`, so the plan reads their code; it
is rebased onto `feature/v3-develop` once they merge.
