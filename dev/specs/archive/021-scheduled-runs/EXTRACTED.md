# Extraction Record

**Extracted on**: 2026-11-18
**Extracted by**: speckit.opsmill.extract

## ADRs Created

The next free number on this branch was 0048. Other branches already use 0048
(`020-product-store-failure-cause`, `024-demo-ready-netbox`) and 0049 and 0050
(`022-local-accounts`), so the records of this spec start at 0051.

- `dev/adr/0051-a-schedule-is-a-prefect-schedule-and-the-worker-admits-each-start.md`: a
  schedule's cadence is a Prefect schedule on the deployment `infrahub-sync-service/run`, matched
  to the stored schedule by slug, and the worker admits each scheduled start and writes its run
  (from R1, R2, R10, R13, and the sections "Checked since" for R1, R2, R10 and R13, which correct
  the bootstrap bullet of R13).
- `dev/adr/0052-a-scheduled-write-to-a-branch-that-sync-creates-needs-no-approval.md`: a run with
  branch review needs no Sync approval; a schedule that writes to the default branch is refused
  while approval is required; the Sync API, the API process at start and the worker all check the
  setting for the default branch (from R6, R11, R15, FR-002, FR-003, FR-020, FR-021, FR-031).
  It amends ADR 0037.
- `dev/adr/0053-a-waiting-scheduled-start-ends-its-flow-run-with-the-pause-signal.md`: a start
  that waits, under the `queue` policy or for its destination, sets `AwaitingRetry` through the
  Prefect client and raises `Pause` (from R7, FR-035, and the sections "Checked since" for R7 and
  for R7 and FR-035).

## ADRs Amended

- `dev/adr/0037-an-approval-is-a-record-on-the-run.md`: the status line names ADR 0052 and what
  it changes. The body is unchanged, as for ADR 0010 and ADR 0027.
- `dev/adr/README.md`: entries for 0051 to 0053, and "amended by 0052" on the entry of 0037.

## Knowledge Updated

- `docs/docs/development/knowledge/sync-architecture.md`, section "A scheduled run":
    - links to ADR 0051 and ADR 0052 in place of "Research R1 of spec 021";
    - new subsection "When a start gets no flow run": when Prefect's scheduler creates flow runs,
      the call to `POST /deployments/{id}/schedule` after a change of a Prefect schedule, the
      starts that still get no flow run, the 5-minute on-time window, and the bootstrap job that
      keeps the Prefect schedules (from R10, R13 and the sections "Checked since");
    - new subsection "A start that waits": the `queue` wait and the destination wait of FR-035,
      `_destination_not_ready` and `_run_again_later` (from R7 and FR-035).
- `docs/docs/development/knowledge/orchestration-prefect.md`, section "Prefect-specific traps",
  four new items: SQLAlchemy held below 2.1 and the condition to remove the limit; a flow that
  returns a scheduled state crashes under a worker; applying a deployment again keeps the
  schedules that have a slug; Prefect's scheduler creates flow runs only from the moment its loop
  runs.
- `docs/docs/development/knowledge/index.md`: the descriptions of these two pages.

The feature commits had already added "A scheduled run" to `sync-architecture.md`, the schedule
modules and indexes to `repository-tour.md`, and the amendment of principle I (version 1.1.0) to
`docs/docs/development/constitution.md`. They were not repeated.

No new page was created, so `docs/sidebars.ts` is unchanged.

## Guidelines Updated

None. The one prescriptive rule found, that a test of a flow that must run again later calls the
flow as the worker's process does, is stated in the trap of `orchestration-prefect.md`.

## Other files

- `dev/specs/archive/README.md`: a section that says `021-scheduled-runs` is not spec 015, whose
  number before the renumbering of 2026-11-15 was 021. Branch `022-local-accounts` adds a section
  with the same heading for spec 022; the two must be merged into one.
- The relative links in `spec.md` were changed for the new depth of the directory.
- Text in `plan.md`, `tasks.md` and `opsmill-implement-report.md` that names
  `dev/specs/021-scheduled-runs` was left as written: these files record the work as it ran.
- No file outside the spec directory pointed at `dev/specs/021-scheduled-runs`. The untracked file
  `.specify/feature.json` still names it.

## Not extracted

- Routes, request and answer fields, error codes, CLI commands and event types
  (`contracts/http-api.md`, `contracts/cli.md`, `contracts/events.md`): already in
  `docs/docs/reference/sync-http-api.mdx`, `docs/docs/reference/cli.mdx` and
  `docs/docs/orchestration.mdx`.
- The schedule records and the branch review operations (`data-model.md`,
  `contracts/branch-review.md`): already in `docs/docs/reference/durable-product-records.mdx`,
  `repository-tour.md` and `sync-architecture.md`. The permissions of the worker's token in
  Infrahub are in `docs/docs/compose-deployment.mdx`.
- R3 (the cadence validated by Prefect's `CronSchedule`): in `service/schedule_cadence.py` and
  `repository-tour.md`.
- R4, R5 and R14: implementation details of the run that the admission writes, the left-out
  setting and the injected time; in the code.
- R8 (the owner is an actor name): in `sync-architecture.md`. Branch `022-local-accounts` replaces
  actor names with account identifiers.
- R9 (schedule events): in `docs/docs/reference/sync-http-api.mdx` and
  [ADR 0033](../../../adr/0033-an-event-is-saved-in-the-transaction-of-its-decision.md).
- R12 (new tables and columns reach only a new database): the rule that an earlier product
  database must be replaced is already in
  `docs/docs/operations/supported-platforms-and-limits.mdx`.
- `quickstart.md`, `plan.md`, `tasks.md`, `checklists/`, `critiques/`, `alignment-check.md`,
  `opsmill-implement-report.md`, `morning-report.md` and `overnight-log.md`: execution records.

## Open at archive time

All 63 tasks of `tasks.md` are ticked. Nothing is pushed and no pull request is open
(`morning-report.md`, section 2, item 5).

- The Helm chart sets `INFRAHUB_SYNC_REQUIRE_APPROVAL` on the Sync API only, so on Kubernetes the
  worker does not check approval for a scheduled start. The maintainer answered that the approval
  design will change and asked for no action (`morning-report.md`, section 2, item 2). The
  Kubernetes deployment page states the gap.
- The SQLAlchemy limit (`<2.1`) stays until the `prefect` pin moves to 3.9.1 or later, which waits
  for `infrahub-testcontainers` to pin `prefect-client` 3.9.1 or later.
- The four low findings of the second review, not tracked (`morning-report.md`, section 4):
    - the worker's approval stop does not check the write target again under the row lock;
    - the worker does not validate the approval setting when it starts;
    - after a failed store write, the immediate creation of flow runs is skipped until Prefect's
      next loop;
    - a deletion of one Prefect schedule also deletes the late flow runs of the others.
- The medium and low findings of the first review that `opsmill-implement-report.md` lists as
  deferred, not tracked. The main ones: a person's `sync` can start between the check for a run in
  progress and the admission; the test fakes accept requests that Infrahub refuses.
- Not run: the Compose test tier (`pytest -m compose tests/compose`).
- Not observed on the development stack:
    - a worker account with only the permissions of the group "Infrahub Users";
    - an approval or a merge by a second Infrahub account;
    - the `queue` policy run again by a real worker;
    - a worker stopped during a write;
    - a Prefect server stopped for longer than the flow runs it created ahead.
- A run up to 5 minutes late counts as on time and records no missed start. FR-018 does not state
  that window; the user guide does.
- Nobody reviewed the code except automated reviewers (`morning-report.md`, section 5).

## Archive

The spec directory moved to `dev/specs/archive/021-scheduled-runs/` as a historical record.
