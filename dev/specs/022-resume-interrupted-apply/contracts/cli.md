# Contract: CLI changes

## `infrahub-sync runs resume RUN_ID` (new)

```text
infrahub-sync runs resume RUN_ID --expected-checksum CHECKSUM [--confirm-left-out] [--reason TEXT]
    [--idempotency-key KEY] [--wait | --no-wait] [--wait-timeout SECONDS] [--poll-interval SECONDS]
```

- Takes the same options as `apply`, which sends `confirm_writes=true` itself.
- Prints the run identifier, the position the resume starts after, and the new execution.
- Exit codes and error lines follow `apply`: a refusal prints `error: CODE` and the message, and
  `hint: run infrahub-sync verify RUN_ID first` for `verify-required`, and
  `hint: run infrahub-sync approve RUN_ID --expected-checksum CHECKSUM first` for `approval-required`.

## `infrahub-sync approve RUN_ID` (extended)

Also accepts an interrupted run that can be resumed, so a site that requires approval can resume a run that was
never approved (spec FR-019). Options and output are unchanged.

## `infrahub-sync runs show RUN_ID` (extended)

For a run with a write, prints:

```text
progress: 6000/10000 operations committed (saved 2026-11-12T10:15:00Z)
resumable: yes (verify required)
kind DcimDevice: committed 1200, uncertain 0, not reached 0
kind DcimInterface: committed 4800, uncertain 1, not reached 3999
```

After a resume that succeeded, the run is not resumable, every operation is committed, and one line per resume
follows the kind lines:

```text
resume 1: ops 2026-11-12T11:02:00Z after 5999 succeeded, wrote 4000 operations
```

In this example, the marker names position 5999, the 6,000th operation, the worker recorded the failed operation
at position 6000, and the plan holds no recorded delete. The counts are over plan positions, recorded deletes
included. The uncertain operations follow the marker: through the failed operation when the failure record of the
latest write execution names it; the next 500 creates and updates, with the recorded deletes between them, when the
last save is known to have succeeded; every remaining operation otherwise, for example after a killed worker
(research R11).

For a `sync` whose worker was killed while it was still planning, the run saved no plan, so `runs show` prints
`progress: last committed position <none> (no position saved)` and `resumable: no`, and `runs resume` is refused
with `run-not-resumable` and a message that says to start a new `plan` or `sync` run (research R14).

`, wrote N operations` is left out when the saved plan cannot be read. For a stopped apply or resume, the
`written: N of M operations` line counts the creates and updates of the whole run, every execution included
(research R6).

## `infrahub-sync verify RUN_ID` (extended)

For a resumable run, after the `check: NAME passed` lines:

```text
operation OPERATION_ID KIND create: matches
operation OPERATION_ID KIND update: differs
operation OPERATION_ID KIND create: missing
operations not reported: 1200
note: runs resume writes the plan's values over every operation reported as differs
```

The last line appears only when the report was truncated.
