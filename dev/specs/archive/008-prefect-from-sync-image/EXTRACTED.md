# Extraction Record

**Extracted on**: 2026-11-14
**Extracted by**: speckit.opsmill.extract

## ADRs Created

- `dev/adr/0028-the-bundled-object-store-runs-from-the-silo-image.md` (from R3 and decision D-5)
- `dev/adr/0029-the-development-stack-is-one-compose-file-with-profiles.md` (from R5, R6, R7 and
  decisions D-6, D-8, D-9, D-10, D-12)
- `dev/adr/0030-the-end-to-end-test-class-inherits-infrahubs.md` (from R4, R8 and decisions
  D-15, D-16, D-17)

## Knowledge Updated

- `docs/docs/development/knowledge/orchestration-prefect.md`, section "Prefect-specific traps":
  a Prefect upgrade moves the `prefect` pin, the `fastapi` pin and the `prefect-client` pin of
  `infrahub-testcontainers` together (from R1, R4).
- `docs/docs/development/knowledge/repository-tour.md`:
    - section "Tasks": rows for `tasks/netbox.py` and `tasks/bench.py` (from R6, R9);
    - section "The development stack and the deployment file": the profiles of the development
      Compose file, and where the `dev` tasks read their settings (from R6, R7).
- `dev/adr/README.md` and `docs/docs/development/adr-index.mdx` (ADR index)

## Guidelines Updated

- `docs/docs/development/guidelines/invoke-tasks.md` (new page "Invoke task names"; from R6, R9
  and decision D-7), with its entry in `docs/docs/development/guidelines/index.md` and in
  `docs/sidebars.ts`.

## Not extracted

- R1 and decisions D-1, D-3 (the Prefect server runs from the Sync image): already in ADR 0019
  (amendment of 2026-10-23) and in `repository-tour.md`.
- R2 and decisions D-11, D-13 (the Prefect UI is off by default and opened with an override
  snippet): already in ADR 0016 (note of 2026-10-23) and in
  `docs/docs/compose-deployment.mdx` ("Open the Prefect UI").
- R3, what an operator needs (the Silo image, its licence and its source): already in
  `docs/docs/compose-deployment.mdx` ("The object store image and its licence").
- R5 (the build-inputs label): already in `docs/docs/development-stack.mdx` ("When the image
  is rebuilt"). ADR 0029 records the decision.
- R7, the procedure (two modes, `--infrahub-network`, the task list): already in
  `docs/docs/development-stack.mdx`.
- R8, how to use `TestInfrahubSyncDocker`: already in
  `docs/docs/development/guidelines/testing-tiers.md` ("End-to-end tests with
  `infrahub_sync.testcontainers` (experimental)") and in `repository-tour.md`. The packaged copy
  of the root file is already in ADR 0016 (note of 2026-10-23).
- R9, the current task names: already in `AGENTS.md`, `repository-tour.md` and
  `docs/docs/development-stack.mdx`. The table of removed names in `contracts/invoke-tasks.md`
  stays in this directory only: `tests/test_task_names_in_docs.py` fails when a page under
  `docs/docs/` names a removed task.
- R10 (nightly workflow, compose suite): already in `testing-tiers.md` ("Nightly end-to-end
  report", "Compose (opt-in)").
- R11 (first-start times): the arm64 times are in `docs/docs/compose-deployment.mdx`.
- Decision D-2 (which services hold the Prefect credential): already in ADR 0019 (amendment)
  and in `docs/docs/compose-deployment.mdx` ("Prefect API authentication").
- Decision D-14 (no update in place from `3.0.0a5`): already in
  `docs/docs/compose-deployment.mdx` ("Resetting this alpha to a fresh deployment").
- `contracts/compose-services.md`: checked by the tests under `tests/compose/` and
  `tests/dev_stack/`; the decisions are in ADR 0016, ADR 0019 and ADR 0028.
- `contracts/testcontainers-api.md`: replaced by `testing-tiers.md`.
- `data-model.md`, `plan.md`, `quickstart.md`, `tasks.md`, `opsmill-implement-report.md` and
  `checklists/requirements.md`: execution records.

## Open at archive time

All 69 tasks in `tasks.md` are ticked. The work was merged in two pull requests, #411 (the
stacks and the two modes) and #412 (the renamed and removed tasks). These items were open when
the directory was archived:

- The Silo image has not run on a native linux/amd64 host, not tracked. Task T001 passed on an
  arm64 laptop under amd64 emulation (R3).
- The first-start times on an amd64 GitHub `ubuntu-latest` runner are not measured, not tracked
  (R11, FR-008). `docs/docs/compose-deployment.mdx` holds the arm64 laptop times only.
- The `psutil.cpu_freq` wrapper in `tests/conftest.py` is not marked as temporary, and the
  failure is not reported to the `infrahub-testcontainers` project, not tracked.
- `pyproject.toml` has no comment that explains the `testcontainers>=4.8,<4.9` range, not
  tracked. The comment above the `testcontainers` extra explains the `prefect-client` pin only.
- `dev.smoke` prints a Python traceback before its message when the stack runs in `dev.deps`
  mode, not tracked. `DevStackError` in `tasks/dev.py` is a `RuntimeError`, not an Invoke
  `Exit` (`tasks.md`, "Quickstart rerun (T069)").
- `plan.md` ("Still open after planning") asks for an update of Jira card INFP-692, which named
  the temporary MinIO build as a blocker, not tracked in this repository. Whether the card was
  updated is not known.

Decisions made after `spec.md` was written are recorded in `opsmill-implement-report.md`: the
Sync API publishes on host port 8001 in the root file and in the development stack
(2026-10-23, FR-006a), `invoke lint` runs `ruff check .`, and the packaged copy of the root
Compose file is committed.

## Archive

The spec directory moved to `dev/specs/archive/008-prefect-from-sync-image/` as a historical
record.
