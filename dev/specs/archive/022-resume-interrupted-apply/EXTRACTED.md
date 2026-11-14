# Extraction Record

**Extracted on**: 2026-11-14
**Extracted by**: speckit.opsmill.extract

## ADRs Amended

No ADR was created. `dev/adr/0027-an-interrupted-apply-resumes-from-its-saved-position.md`, which
this spec wrote, was amended:

- Decision, "Progress marker": the apply stops before its next write when the store answers that
  the execution has already ended (from R4).
- Decision, "`verify` on an interrupted run": a run is resumable only once its plan review
  document is published, and a `sync` ended while it was still planning keeps
  `reconciliation_required` (from R1, R14).
- Alternatives considered: leaving `reconciliation_required` unset for a `sync` that saved no
  plan (from R14).
- Source line: the archive path, with R4 and R14 added.

## Knowledge Updated

- `docs/docs/development/knowledge/planned-write-and-apply.md`, section "The progress marker and
  resume":
    - the apply stops when `record_apply_progress` answers `ended` (from R4);
    - which destination read errors `verify` reports as `DestinationReadError`, and which it
      leaves as a defect (from R13);
    - the filters `verify` uses to match a create, and the limit for a kind with no
      human-friendly ID (from R13);
    - why a `sync` ended while it was still planning keeps `reconciliation_required`, and how
      `runs resume`, `verify` and `approve` refuse it (from R14);
    - the plan-layout cache behind the progress view of a run (from R14).

## Guidelines Updated

None. The rule that an error outside the known operational failures is a defect is already in
`planned-write-and-apply.md` ("The operational exception boundary"), and failure records that
name the error class only are already required by
`docs/docs/development/guidelines/secret-redaction.md`.

## Not extracted

- R1, R2, R5, R6, R7, R8, R11, R12 and the `differs` rule of R13: already in ADR 0027 and in
  `planned-write-and-apply.md` ("The progress marker and resume").
- R3 (the observer protocol and the save schedule) and R9 (a `sync` resume reads the saved plan):
  already in `planned-write-and-apply.md`.
- R10 and `contracts/` (HTTP route, CLI command, `apply_progress` and `resumes` fields): already
  in `docs/docs/reference/sync-http-api.mdx`, `docs/docs/reference/cli.mdx` and
  `docs/docs/running-a-sync.mdx`.
- R14 refusal codes for users: already in `docs/docs/running-a-sync.mdx` and
  `docs/docs/reference/sync-http-api.mdx`.
- R14 audit outcome `refused-plan-unavailable`: in the code only. No document lists audit outcome
  values for any command.
- The `resume_admissions` table: already in `docs/docs/reference/durable-product-records.mdx`.
- `data-model.md` "State rules": already in `planned-write-and-apply.md`.
- `plan.md`, `tasks.md`, `quickstart.md`, `checklists/`, `critiques/`, `alignment-check.md` and
  `opsmill-implement-report.md`: execution records.

## Open at archive time

- Task T035, not tracked. The measurements of SC-003 (saving the progress marker adds at most 2%
  to the apply time on tiers M and L) and SC-004 (a `verify` of an interrupted tier L run
  finishes in under 1 minute) never ran, because they need the tier M and tier L datasets. The
  baseline commit is `5f005776`.
- Which phase a run ends in when its worker is killed, not decided, not tracked. The liveness
  monitor sets the phase `interrupted` (`interrupt_execution` in
  `infrahub_sync/product_store/store.py`); a worker that fails on its own sets
  `<stage>-interrupted` (`infrahub_sync/service/flow.py`). The T034 test
  (`tests/dev_stack/smoke/test_resume_interrupted_apply.py`) accepts both `interrupted` and
  `apply-interrupted`. Whether `interrupted` is the intended phase is not known. The user
  documentation gives two different answers, and was left unchanged:
    - `docs/docs/running-a-sync.mdx`, lines 605 to 608: "If the service cannot tell whether it
      wrote, the run ends with `phase: apply-interrupted` and `outcome: ambiguous`, and the
      failed operation may have written part of its change. The same happens when the worker
      stops during the apply, for example when its container is killed. A `sync` run ends with
      `phase: sync-interrupted` in the same cases."
    - `docs/docs/compose-deployment.mdx`, lines 1223 to 1225: "A write execution that ended
      without reporting records phase `interrupted` and outcome `ambiguous`, and sets
      `reconciliation_required`." Line 1234 of the same page then names only `apply-interrupted`
      and `sync-interrupted` as the phases of a run that can be resumed, and
      `docs/docs/tutorials/netbox-demo-to-infrahub.mdx`, lines 353 to 359, names only
      `apply-interrupted`.
- Limit of `verify` for a kind with no human-friendly ID, not fixed, not tracked. A uniqueness
  constraint that covers several fields, including a relationship such as (device, name), is not
  part of the key of a create. When several destination nodes share the key, `verify` reports
  the operation as `differs` (R13, "Not decided").
- The per-operation `verify` results (`matches`, `differs`, `missing`) were not checked on a
  running stack, not tracked. T034 ran `verify` against a running Infrahub but did not keep the
  CLI output, and the relationship filters of R13 were tested against the in-memory client of
  the unit tests only.
- `AGENTS.md` has no entry of its own for `infrahub-sync runs resume` in its list of CLI
  commands, not tracked. The command is named only inside the `approve` entry.
- Deferred by the spec ("Out of Scope"), with no follow-up decided, not tracked:
    - an automatic resume when a worker restarts;
    - the recovery actions `replay-from-tier`, `re-extract`, `resolve-drift` and `escalate`;
    - continuing an apply after a failed operation;
    - adapter declarations of which writes are safe to repeat;
    - checking every plan operation against the destination;
    - executing deletes;
    - the operator web UI.

`alignment-check.md` describes the uncertain operations after a killed worker as "the next 500
operations". R11 replaced that: after a killed worker, every operation after the saved position
is uncertain. R11 and ADR 0027 are the current statement.

## Archive

The spec directory moved to `dev/specs/archive/022-resume-interrupted-apply/` as a historical
record.
