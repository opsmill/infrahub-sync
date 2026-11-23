# Data Model: Name the run that holds a configuration's write lock

No table, column or migration changes. The entities below are values held in memory, in PostgreSQL session
state, or in the existing failure record.

## Write-lock session label

- **Where**: `application_name` of the dedicated guard session (PostgreSQL session state; ends with the session).
- **Value**: `infrahub-sync-guard:<run_id>`.
- **Written when**: after the session opens, before `lock_timeout` is set and before `pg_advisory_lock`.
- **Written only if**: the run ID matches the run ID pattern and the whole label is at most 62 bytes.
- **Read when**: by another write's session, after its lock wait timed out.
- **Accepted only if**: at most 62 bytes, exact prefix, the rest matches `^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$`.

## `ApplyGuardContentionError.holder_run_id`

- **Type**: `str | None`.
- **Meaning**: a candidate run ID parsed from the holder's label. Not yet confirmed.
- **Never holds**: the raw label, a process ID, a database or account name.

## Confirmed holder (in `flow.py`)

- **Accepted when**: candidate is not the failing run's ID, and `write_holder_state(candidate, config_id)`
  returns a state (the run exists and its `config_id` equals the failing run's `config_id`).
- **`ended`**: the returned state says the run has no claimed, open write.
  - Note (2026-11-23, review): `HolderState` is `writing: bool` and `write_ended_at: datetime | None` (the latest
    terminal time of the run's claimed write executions, in UTC). The run counts as ended only when the run records have no claimed, open write for the run and its latest claimed write ended (`HolderState.write_ended_at`) before this write started waiting (the lock timeout minus the deadline the guard used, stamped as `ApplyGuardContentionError.wait_started_at`). A holder that released the lock and recorded its verdict during the wait gets the "writing" variant. The causes say which run held the guard when the wait ended, not "for the whole wait".
- **Otherwise**: not known (`None`).

## Failure record of a contended write (existing, from `cf9f13b6`/`a50d5780`)

| Field | Value |
|---|---|
| `failure_type` | `write_guard_contended` |
| `error_type` | `ApplyGuardContentionError` (unchanged) |
| `active_run_id` | confirmed run ID, or `null` |
| `cause`, `next_action` | from `_guard_contended_template`, one of three variants: named and writing; named and ended; not known |

## Store method change

| Removed | Added |
|---|---|
| `other_write_in_progress(config_id, *, run_id) -> str \| None` | `write_holder_state(run_id, config_id) -> HolderState \| None` |

One query. `None` when the run does not exist or belongs to another configuration. `HolderState.writing` uses the
same rule for "open write": an execution whose purpose is in `WRITE_PURPOSES`, claimed by a worker, with no
terminal verdict.

## Web interface: `FailureDetail.activeRunId`

- **Where**: `frontend/src/domain/run-state.ts`, read by `failureDetail` from the run's failure record.
- **Type**: `string | undefined`; set only when `active_run_id` is a non-empty string.
- **Used by**: the failure notice in `frontend/src/features/runs/run-notices.tsx`, which links to
  `/runs/$runId` with that ID.
