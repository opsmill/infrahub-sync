# Extraction Record

**Extracted on**: 2026-11-14
**Extracted by**: speckit.opsmill.extract

## ADRs Created

- `dev/adr/0036-verify-runs-every-pre-write-check-of-apply-through-one-shared-gate.md` (from R1,
  R2, R3, R4, R5)
- `dev/adr/0037-an-approval-is-a-record-on-the-run.md` (from R6, R7, R8, R9, and one sentence on
  the run event of R10)
- `dev/adr/0038-cancelling-a-finished-plan-withdraws-it.md` (from R11)

## Knowledge Updated

- `docs/docs/development/knowledge/sync-architecture.md`, section "One registered run":
    - step 4 corrected: the person gives the plan's checksum to `apply`, and `approve` is a
      separate request that a site can require (from R6, R9);
    - the sentence on the separate verification stage corrected: for a run of a registered
      configuration the stage checks the destination (from R1, R2);
    - new subsection "Requests the Sync API answers without a flow run" (from R7, R8, R9, R11).
- `docs/docs/development/knowledge/execution-surface.md`, section "Operations and return types":
  a paragraph on what the service's `verify` stage adds to `execute_run(operation="verify")`
  (from R1, R2)
- `docs/docs/development/knowledge/planned-write-and-apply.md`, section "The apply loop": new
  subsection "The pre-write gate" (from R1, R4)
- `docs/docs/reference/durable-product-records.mdx`, section "Record and identity contract": a
  paragraph on the approval of a run (from R6 and `data-model.md`)
- `dev/adr/README.md` and `docs/docs/development/adr-index.mdx` (ADR index)

## Guidelines Updated

- `docs/docs/development/guidelines/testing.md`: new section "Test a shared check on every path
  that runs it" (from R1)

## Not extracted

- R10 (run event for an approval): built with spec 017's events and already in
  `docs/docs/reference/sync-http-api.mdx` ("Approve the reviewed plan" and "List events").
- R12 (CLI surface: `approve`, the approval lines of `runs show`, the check list of `verify`, and
  `approval_required` in `whoami`): already in `docs/docs/reference/cli.mdx`,
  `docs/docs/running-a-sync.mdx` and `AGENTS.md` ("CLI Commands").
- R3 (no write guard for `verify`): already in `docs/docs/development/knowledge/apply-guard.md`.
  ADR 0036 also states it.
- `contracts/http-api.md`: already in `docs/docs/reference/sync-http-api.mdx` ("Verify the
  reviewed plan", "Approve the reviewed plan", "Require an approved plan" and "Withdraw a plan by
  cancelling its run").
- `contracts/cli.md`: already in `docs/docs/reference/cli.mdx`.
- `quickstart.md`: already in `docs/docs/running-a-sync.mdx` and, for
  `INFRAHUB_SYNC_REQUIRE_APPROVAL`, in `docs/docs/compose-deployment.mdx`.
- `data-model.md` (the approval columns): in ADR 0037 and in
  `docs/docs/reference/durable-product-records.mdx`.
- The rule that a setting with a value that is not accepted stops the service at start-up, with
  a message that does not show the value: in ADR 0037. No guideline page covers service
  settings.
- Approval of a resumable run: already ADR 0027 and
  `docs/docs/development/knowledge/planned-write-and-apply.md`.
- `plan.md`, `tasks.md`, `checklists/`, `critiques/`, `alignment-check.md` and
  `opsmill-implement-report.md`: execution records.

## Open at archive time

`tasks.md` has no open task: all 48 tasks are ticked. `spec.md` has no open question.

No GitHub issue was found for any item below. The search was made when the extraction was
planned, with the terms `approval`, `approve` and `withdraw` on `opsmill/infrahub-sync`. An issue
under another title is possible.

- The worker does not check the approval, not tracked. Only the Sync API checks it, when it
  admits an `apply` or a `resume` (`infrahub_sync/service/service.py`,
  `_require_matching_approval`). Research R9 names a check in the worker as "defence in depth for
  a later change". ADR 0037 lists it as deferred.
- Items of `spec.md`, "Out of Scope", not built and not tracked:
    - a reviewer other than the actor who planned; the spec says this waits for single sign-on;
    - a check of whether destination nodes changed since the plan;
    - approval expiry;
    - the approval requirement set for each configuration;
    - approval in the web UI.
- Review findings that `opsmill-implement-report.md` (section 5) marks as deferred, not tracked.
  The report has one row for 12 low findings and does not list them one by one. No other file of
  the spec lists them. The row names four groups:
    - A wrong audit outcome for `run-not-found` (1 finding). Read in the code on 2026-11-14 and
      still present: when the store answers a withdrawal with `404 run-not-found`,
      `_withdraw_planned_run` records the outcome `refused-run-execution-conflict`; when the
      store answers an approval with `404 run-not-found`, `approve_run` records the outcome
      `refused-not-approvable` (`infrahub_sync/service/service.py`).
    - Three comments that are out of date. The report does not say which. Not checked again.
    - Five simplifications. The report does not say which. Not checked again.
    - Three gaps in the CLI tests and the model tests. The report does not say which. Not
      checked again.
- The parity cases of `tests/service/test_verify_destination.py` do not break the `checkpoint`
  check, not tracked. ADR 0036 states it under Consequences.
- The preview test of a withdrawal did not reach the `200` answer, not tracked. In the run that
  the report records, `tests/preview/test_run_completion.py` took the `202` branch both times.
  Not checked again.

Statements of the spec documents that the code no longer matches. The archive keeps the documents
as written:

- `research.md`, R7 and R8: `approve` accepts a run in phase `planned`. Since spec 022 it also
  accepts a resumable run (ADR 0027).
- `research.md`, R8: the follow-up review added a check. The store locks the run's row and stores
  no approval while an execution of the run is unfinished.
- `research.md`, R9: the approval check also applies to `resume`.
- `research.md`, R10: the run event of an approval is built, not a follow-up. An accepted
  approval and a withdrawal each write their events in the transaction of the change.
- `research.md`, R5: the worker resolves the destination's credentials only for `apply`, `resume`
  and `verify`; the research names `apply` and `verify`.

Behaviour that the spec changed on purpose (R11): `apply` refuses every run in phase `cancelled`,
including a run whose `verify` execution was cancelled. Before, such a run could be applied.

## Archive

The spec directory moved to `dev/specs/archive/019-plan-approval/` as a historical record.
