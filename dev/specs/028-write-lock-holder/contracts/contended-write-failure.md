# Contract: failure of a write that could not take the configuration's write lock

Applies to the `sync_failure`, `apply_failure` and resume failure records returned by `GET /api/runs/{run_id}`
and printed by the CLI after `sync`, `apply` and `runs resume`.

## API fields (unchanged shape)

```json
{
  "failure_type": "write_guard_contended",
  "error_type": "ApplyGuardContentionError",
  "active_run_id": "20261123T1012-3fa1b2c4",
  "cause": "...",
  "next_action": "..."
}
```

`active_run_id` is a run ID or `null`. No other field is added.

## Cause and next action

| Case | Cause | Next action |
|---|---|---|
| Holder named, its write still open, or ended after the wait began, or with no end time | Sync run `<id>` was writing to this configuration and held its write guard when this write's wait for the guard ended, so this write did not start and changed nothing | Check that run with `infrahub-sync runs show <id>` and wait for it to finish, then plan again. |
| Holder named, its write ended before the wait began | Sync run `<id>` held this configuration's write guard when this write's wait for the guard ended, although its write had ended before that wait began, so this write did not start and changed nothing | The guard is released only when the worker of run `<id>` stops. Ask the administrator of the Sync service to stop or restart that worker, then plan again. |
| Holder not known | Another writer held this configuration's write guard when this write's wait for the guard ended, so this write did not start and changed nothing. Which Sync run holds the guard is not known | List the runs of this configuration with `infrahub-sync runs list` and wait for its write to finish, then plan again. |

For a failed `runs resume`, "then plan again." is replaced by the existing verify-then-resume step.

Note (2026-11-23, review): the causes say which run held the guard when the wait ended, not "for the
whole wait", because another waiter can take the lock between the original holder's release and the
lookup. A named holder gets the "ended" variant only when its run records have no open claimed write
and its last write ended before this write started waiting (the time the guard took immediately
before it sent the lock request); every other named holder gets the "writing" variant.

## CLI lines

When `active_run_id` is set, the CLI prints `active run: <id>` after `cause:`, as on branch
`fix/write-guard-names-active-run`. When it is `null`, no `active run:` line is printed.

## Web interface

The failed run's failure notice shows the stored cause and next action, as it does today. When `active_run_id`
is a non-empty string, the notice also shows an "Open run" link to `/runs/<active_run_id>`. When it is `null`,
absent or not a string, no link is shown. The web interface adds no wording of its own for the "not known" and
"has ended" cases; the stored cause and next action carry them.

## What never appears

The raw session label, a PostgreSQL process ID, a database name, an account name, a host, a port or a password.
