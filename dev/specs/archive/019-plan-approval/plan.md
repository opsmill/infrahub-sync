# Implementation Plan: Approve a plan without applying it, and a `verify` that checks the destination

**Branch**: `verify-apply-approval` | **Date**: 2026-11-10 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `dev/specs/019-plan-approval/spec.md`

## Summary

Four changes, each independently shippable:

1. **Approval** (US1): a new API-side request `POST /runs/{run_id}/approve` and CLI command `approve` record who
   approved a planned run, when, and which plan checksum, without contacting any system. The approval is stored on
   the run in four nullable columns and returned on the run resource and in `runs show`.
2. **`verify` checks the destination** (US2): the `verify` worker stage runs every pre-write check that `apply`
   runs, by sharing one pre-write gate split out of `Potenda.apply_plan`, and stops before the write loop.
3. **Approval-required setting** (US3): `INFRAHUB_SYNC_REQUIRE_APPROVAL` makes `apply` refuse an unapproved plan and
   `sync` refuse to start, at admission, before anything is submitted.
4. **Withdraw by cancelling** (US4): `runs cancel` on a finished, unapplied plan sets the run's phase to
   `cancelled` in one store transaction, without Prefect. `approve` and `apply` refuse a cancelled run, at admission
   and under the store's write-first row lock, so a cancel and an apply or approval cannot both succeed.

Decisions and their rationale are in [research.md](research.md).

## Technical Context

**Language/Version**: Python 3.11–3.13 (service profile); the CLI and client also run on 3.10.

**Primary Dependencies**: FastAPI, Pydantic, Typer, HTTPX, Prefect client (no new dependency).

**Storage**: the product store (`_RelationalRunStore`) on SQLite (tests, local) and PostgreSQL (service). Four
nullable columns on `product_runs`, added by the existing idempotent start-up column migration. No backfill.

**Testing**: pytest unit tier (`uv run invoke tests.tests-unit`): `tests/service/`, `tests/product_store/`,
`tests/client/`, `tests/cli/`, `tests/test_potenda*.py`, `tests/test_execution_surface.py`. PostgreSQL isolation
test for the conditional approval update.

**Target Platform**: Linux server (Sync API and worker); CLI on any platform.

**Project Type**: web service with a CLI client.

**Performance Goals**: approving reads one run and one plan resource and writes one row; no flow run. `verify` adds
one destination schema read and an adapter construction, the same work `apply` does before its first write.

**Constraints**:

- The API stays `v3-unstable`; all response changes are additive.
- With the setting off, `apply` and `sync` must behave exactly as before (SC-003): every existing test passes
  unchanged.
- `verify` must never reach the write loop; the gate function it calls has no path to it.
- No secret in approval records, audit events, errors or logs (`secret-redaction.md`).
- The vendored `opsmill_prefect_extras` stays byte-identical.

**Scale/Scope**: about 10 source files and 10 test files; 3 user documentation pages.

## Constitution Check

Constitution: `docs/docs/development/constitution.md` (`.specify/memory/constitution.md`).

| Principle | Assessment |
| --- | --- |
| I. Read-only and dry-run by default | Strengthened. `approve` and `verify` write nothing to the destination. Mandatory approval is an explicit setting, off by default ("New mutating behavior MUST ship behind explicit flags"). `sync` and `apply` keep their confirmations. |
| II. Sync idempotency and safety | `approve` uses the existing mutation receipts and `Idempotency-Key`; a replay returns the stored response. A conditional update makes concurrent approvals safe. `verify` takes no guard and writes nothing. |
| III. Adapter symmetry | No adapter change. `verify` uses the destination adapter exactly as a saved-plan `apply` does. |
| IV. Type safety and explicit contracts | New Pydantic models (`RunApproval`, `ApproveRunRequest`, response field), typed gate result. Contracts in `contracts/`. |
| V. Test discipline | Unit tests for every refusal and for the verify/apply check parity (SC-004); SC-003 by the unchanged existing suite. |
| VI. Security, secrets, input boundaries | `verify` now resolves destination credentials, as `apply` already does; failures cross the worker boundary through the existing redacted failure path. The approval reason is operator text stored like other audit reasons. |
| VII. Simplicity | One gate function shared by `verify` and `apply`; approval is columns on an existing record, not a new subsystem. |

Gate result: PASS. No violation to justify.

## Project Structure

### Documentation (this feature)

```text
dev/specs/019-plan-approval/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── http-api.md
│   └── cli.md
├── checklists/requirements.md
└── tasks.md            (speckit-tasks)
```

### Source Code (repository root)

```text
infrahub_sync/
├── potenda/__init__.py            # split apply_plan into check_plan (gate) + write loop
├── utils.py                       # PlanApplier.check_plan(): destination binding + engine gate
├── service/
│   ├── flow.py                    # verify stage: destination scope, schema check, PlanApplier.check_plan
│   ├── service.py                 # approve_run; approval-required checks in apply_run and create_run(sync);
│   │                              # cancel_run withdraw path; apply/approve refuse phase cancelled;
│   │                              # verify passes run.destination_branch
│   ├── app.py                     # POST /runs/{run_id}/approve; GET /me approval_required
│   ├── settings.py (new)          # INFRAHUB_SYNC_REQUIRE_APPROVAL parsing, fail closed at start-up
│   └── models.py                  # approve request model re-export if needed
├── product_store/
│   ├── models.py                  # RunApproval; ProductRun.approval
│   └── store.py                   # columns, migration, record_approval (conditional update), row mapping,
│                                  # cancel_planned_run; _reservation_refused refuses phase cancelled
├── client/
│   ├── models.py                  # ApproveRunRequest, RunApprovalResource, PrincipalResource.approval_required
│   └── client.py                  # approve_run / approve
└── cli.py                         # approve command; runs show, verify, whoami output

tests/
├── test_potenda_check_plan.py (new)        # gate parity, no dispatch
├── service/test_verify_destination.py (new)
├── service/test_approval.py (new)
├── service/test_approval_required.py (new)
├── service/test_cancel_planned_run.py (new)
├── product_store/test_write_admission.py (extended)
├── product_store/test_run_approval.py (new)
├── client/ and cli/ (extended)
└── service/test_http_api.py (extended)

docs/docs/
├── running-a-sync.mdx             # plan → verify → approve → apply, each with one purpose
├── reference/sync-http-api.mdx    # approve route, run approval field, verify checks, setting
├── reference/cli.mdx              # regenerated
└── compose-deployment.mdx         # INFRAHUB_SYNC_REQUIRE_APPROVAL in the service environment
```

**Structure Decision**: single project. Every change lives in the existing packages that own the stage, the record,
the route, the client and the CLI. One new module, `service/settings.py`, holds the setting so that `service.py`
does not read the environment directly.

## Implementation notes

- **Gate split** (`potenda/__init__.py`): `check_plan(*, run_id, config_version, artifact, expected_checksum=None)`
  returns `PlanCheck(loaded, checks)`. `apply_plan` reads the artifact once, calls `check_plan` with it, and keeps the
  loop. The ordering comments move with the code. `expected_checksum=None` skips only the approval comparison.
- **Verify stage** (`flow.py`): for a registered run, after the existing `execute_run(operation="verify")`, build
  the `apply`-scope runtime models (one schema read), `_require_planned_schema`, then
  `PlanApplier.open_existing(...).check_plan()`. Legacy runs keep file checks only (`destination_checked: false`),
  because a legacy run cannot be applied by the service either.
- **Approval** (`service.py`): order of checks in [contracts/http-api.md](contracts/http-api.md). After the checks,
  the service reserves a mutation receipt (operation `approve`) and calls a new store operation
  `record_approval(run_id, approval, receipt, response)`. In one transaction it runs the conditional update
  (`approved_by IS NULL AND phase = 'planned'`) and completes the receipt with the response body; when the update
  matches no row, it completes the receipt with the `already-approved` error instead. A retry with the same key
  replays the stored response; no flow run is submitted. Before implementing, check whether
  `mutation_receipts.operation` is constrained (column check or `Literal` type) and add `approve` to it.
- **Approve response**: the run resource plus the plan's summary counts (creates, updates, recorded deletes), stored
  in the receipt so a replay shows the same summary. The CLI prints them.
- **Refusal wording**: `run-not-approvable` names the phase, or the active stage when an execution is in progress
  (for example "a verify execution is in progress").
- **Setting** (`settings.py`): parsed once at start-up for validation, read through a function on each admission so
  tests can patch it.
- **Withdraw path** (`service.py`, `store.py`): see research R11. `cancel_run` takes the withdraw path only where it
  answers `execution-terminal` today and the run's phase is `planned`; every other branch of `cancel_run` is
  unchanged. `cancel_planned_run` uses the existing write-first lock (`UPDATE product_runs SET run_id = run_id`) and
  the same checks as `_reservation_refused`, then completes the receipt with `200` and the run resource.
  `_reservation_refused` gains "the locked run's phase is `cancelled`". The existing test
  `test_cancel_treats_expired_and_terminal_links_as_non_cancellable` and any test expecting `execution-terminal` for
  a planned run are updated to the new answer, and named in the change (SC-003).
- **Docs**: the "Running a sync" page gets a short "Steps after a plan" section that answers "is `verify` an
  approval?". It states that `verify` now needs the destination to be reachable with the configured credentials.
- **Changelog**: one `added` entry (approval, the setting, withdrawing a finished plan with `runs cancel`) and one
  `changed` entry (`verify` checks the destination and fails when it cannot reach it; `apply` refuses every run in
  phase `cancelled`).
- **Cancel docs**: `running-a-sync.mdx` ("`runs cancel RUN_ID` cancels a run that has not finished") and
  `sync-http-api.mdx` (the route table line "Ask Prefect to cancel only the latest active linked execution", the 409
  list and the recovery table) describe the withdraw path; the CLI help is regenerated.

## Complexity Tracking

No constitution violation; nothing to track.
