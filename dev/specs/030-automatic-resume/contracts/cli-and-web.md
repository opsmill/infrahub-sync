# Contract: CLI and web interface changes for automatic resume

No new command and no new option.

## `infrahub-sync runs show RUN_ID`

After the progress block of spec 016, a block per row of `automatic_resumes`, oldest first:

```text
Automatic resume 1: started
  cause: the worker infrahub-sync-worker-3b2f… stopped sending heartbeats
  check: passed, 0 differs, 0 not checked
  resumed after operation 6000 by Sync (automatic resume)

Automatic resume 2: not started
  cause: Prefect reported the flow run as crashed
  reason: the check found 1 node that differs from the plan
  next: run `infrahub-sync verify RUN_ID`, then a new plan or `infrahub-sync runs resume RUN_ID --expected-checksum CHECKSUM`
```

A row that is not `ended` prints its state: `waiting for a live worker to run the check`, `checking`, `waiting for a
live worker to resume`, `resuming`.

## `infrahub-sync runs list`

A run with `automatic_resume_count` above 0 shows it in a column `auto resumes`.

## Web interface, run page

- The resume entries show `Sync (automatic resume)` where a manual resume shows the account name.
- A notice for each row of `automatic_resumes`, with the same text as `runs show`. A row that is not `ended` shows
  its state.
- The resume action of the run page (spec 016 in the web interface) stays available when the last row ended
  `no-resume`, and is hidden while a row is not `ended`.

## Documentation

- `docs/docs/operations/day-2-operations.mdx`: a section "Automatic resume" that describes the setting, when Sync
  resumes, the reasons it does not, and the limits.
- `docs/docs/running-a-sync.mdx` and `docs/docs/reference/sync-http-api.mdx`: the new fields and events.
- `docs/docs/compose-deployment.mdx` and the Helm chart values page: the setting.
