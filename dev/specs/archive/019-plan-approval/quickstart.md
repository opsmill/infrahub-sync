# Quickstart: validate approval and the destination-checking `verify`

## Unit tier (offline)

```bash
uv sync --extra dev --extra prefect --extra service
uv run invoke tests.tests-unit
uv run ruff check .
uv run invoke lint
```

The new and changed tests are listed in [tasks.md](tasks.md). They cover each check of the approval contract, each
refusal of the approval-required setting, and, for every pre-write check, that `verify` and `apply` fail on the same
check (SC-004).

## Live check against the development stack

Prerequisites: the development stack (`development/docker-compose.yml`), a registered configuration package, and a
Sync API token in `SYNC_API_TOKEN`.

1. Plan a run and read its checksum:

   ```bash
   infrahub-sync plan --config CONFIG --version VERSION --reason "approval check"
   infrahub-sync runs plan RUN_ID
   ```

2. Approve it without applying, and confirm the destination did not change:

   ```bash
   infrahub-sync approve RUN_ID --expected-checksum CHECKSUM
   infrahub-sync runs show RUN_ID     # approved_by, approved_at, approved_checksum
   ```

   Approving again is refused with `already-approved`.

3. Verify against the destination:

   ```bash
   infrahub-sync verify RUN_ID       # one "check: … passed" line per check
   ```

   Remove a planned attribute from the destination schema (with `state: absent`), run `verify` again, and confirm it
   fails on `destination_schema` or `planned_fields` and writes nothing.

4. Require approval: restart the Sync API with `INFRAHUB_SYNC_REQUIRE_APPROVAL=true`, then:

   ```bash
   infrahub-sync whoami              # approval_required: true
   infrahub-sync sync --config CONFIG --version VERSION   # refused: approval-required-for-sync
   infrahub-sync apply OTHER_RUN_ID --expected-checksum CHECKSUM   # unapproved: approval-required
   infrahub-sync apply RUN_ID --expected-checksum CHECKSUM         # approved: applies
   ```

5. Withdraw a plan: plan and approve a new run, then cancel it:

   ```bash
   infrahub-sync runs cancel NEW_RUN_ID --yes   # phase: cancelled, approval still shown
   infrahub-sync approve NEW_RUN_ID --expected-checksum CHECKSUM               # refused: run-cancelled
   infrahub-sync apply NEW_RUN_ID --expected-checksum CHECKSUM                 # refused: run-cancelled
   infrahub-sync runs cancel NEW_RUN_ID --yes   # refused: run-already-cancelled
   infrahub-sync runs cancel RUN_ID --yes       # the applied run: refused as before (execution-terminal)
   ```

6. Turn the setting off and confirm `apply` and `sync` behave as before.

## Live results

Run by T048 on 2026-11-10, 16:25–16:30 UTC, against the development stack started with
`uv run invoke dev.start` from this branch's worktree (Compose project `infrahub-sync-dev`,
Sync API `http://127.0.0.1:8001`, Infrahub `http://127.0.0.1:8000`) and seeded with
`uv run invoke dev.seed`. The package was Infrahub to Infrahub: `InfraDevice` (`name`, `type`)
from `main` to `preview-smoke`, with `core01`'s `type` changed on `main` before each plan so
every plan held one update. The stack was stopped afterwards with `uv run invoke dev.stop`.

1. Plan: run `20261110T1625-18f56349`, phase `planned`, `by_action: update=1`.
2. Approve: `approved_by: dev`, `approved_checksum` equal to the plan checksum,
   `approved_updates: 1`, phase still `planned`; the destination still held the seeded value.
   A second `approve` was refused with `already-approved`.
3. Verify: eleven `check: … passed` lines, from `checkpoint` to `lineage`. On a second
   configuration whose destination branch `t048-drop` then had `InfraDevice.type` removed
   (`state: absent`), `verify` exited 1, the phase stayed `planned`, and `runs results` showed
   `verify_failure.failed_checks: ["destination_schema"]` (`PlanSchemaChangedError`). The CLI
   printed `error: run-terminal` and no `hint:` line for this failure.
4. With `REQUIRE_APPROVAL=true uv run invoke dev.restart`: `whoami` printed
   `approval_required: true`; `sync` was refused with `approval-required-for-sync`; `apply` of
   an unapproved run with `approval-required`; `apply` of the approved run applied it
   (`phase: applied`, destination value changed). An approved run applied with another
   checksum was refused with `approval-checksum-mismatch`. Note: two plans of unchanged data
   have the same checksum, so the "other run" step needs a different checksum to show the
   mismatch.
5. Withdraw: a new approved run `20261110T1628-f63991d2` cancelled with `runs cancel --yes`
   returned `phase: cancelled`, `outcome: cancelled`, approval still shown; then `approve` and
   `apply` were refused with `run-cancelled`, a second cancel with `run-already-cancelled`, and
   cancelling the applied run with `execution-terminal`. The destination did not change.
6. After `uv run invoke dev.restart` without the setting: `approval_required: false`; an
   unapproved plan was applied (`phase: applied`), and `sync` applied (`phase: applied`).
