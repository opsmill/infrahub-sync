# CLI contract: `approve`, `verify`, `runs show`, `whoami`

## `infrahub-sync approve` (new)

```text
infrahub-sync approve RUN_ID --expected-checksum CHECKSUM [--reason TEXT] [--idempotency-key KEY]
```

- Calls `POST /runs/{run_id}/approve`. Does not wait: approval completes in the request.
- Not interactive and needs no `--yes`: it writes nothing to the destination.
- Prints the run lines that `runs show` prints, including the approval lines below, then the plan's summary
  counts that were approved: `approved_creates`, `approved_updates`, `approved_deletes_recorded`.
- Errors are printed through the existing client error path with the service's code and message.
- Help text: "Record approval of the named run's saved plan without applying it."

## `infrahub-sync verify` (output changed)

- On success, after the existing run lines, prints one line per check that passed:
  `check: destination_binding passed` (in the order of `results.verification.checks`).
- On failure, prints the stage failure as today, which names the check.
- Help text: "Check that the named run's saved plan would apply now, without writing."

## `infrahub-sync runs show` (output changed, additive)

When the run has an approval:

```text
approved_by: alice
approved_at: 2026-11-10T14:03:11+00:00
approved_checksum: 0123…cdef
```

No lines when the run has no approval.

## `infrahub-sync whoami` (output changed, additive)

```text
actor: alice
administrator: false
approval_required: true
```

## `infrahub-sync runs cancel` (help and prompt changed)

- Arguments unchanged; still asks for confirmation unless `--yes`.
- Help: "Cancel a run. Cancelling a finished plan withdraws it so it cannot be approved or applied; cancelling a
  write can leave its outcome ambiguous."
- Prompt: "Cancel run RUN_ID? A finished plan is withdrawn; cancelling a write can leave an ambiguous outcome."
- After a withdrawal, prints the run lines with `phase: cancelled`.

## `infrahub-sync apply` and `sync` (unchanged)

Same arguments. When the setting is on, their refusal is printed by the existing error path.
