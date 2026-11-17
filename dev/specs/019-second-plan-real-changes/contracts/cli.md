# Contract: CLI changes

## `infrahub-sync runs show <run_id>`

After the `source_read` and `destination_read` lines, one line per part that has an entry:

```text
source_read: state=succeeded mode=full started_at=... ended_at=... nodes=...
destination_read: state=succeeded mode=full started_at=... ended_at=... nodes=...
plan_waiting: 4.0s
plan_preparing: 5.1s
plan_comparing: 31.7s
plan_checking: 2.3s
plan_saving: running
```

- A part that ended prints its duration in seconds with one decimal.
- A part that runs prints `running`.
- A part that stopped prints `stopped after <seconds>s`, or `stopped` when no stop time exists.
- A part with no entry prints no line.
- A run with no `plan_step` prints no plan line and succeeds.

The help text of `runs show` states that the plan step lines are subject to change in a later
release.

## `infrahub-sync runs plan <run_id> --detail`

An update with an emptied one-peer relationship prints:

```text
update InterfacePhysical device=edge-01, name=et-0/0/1
  lag: becomes empty
```

The summary counts the update once, as any update.

## `infrahub-sync runs plan <run_id>`

The help text states: "A plan with 0 operations can still list records left out, kept records
and problems; those records were not imported."

## Refusal of a plan format

`apply`, `verify`, `approve`, `runs resume` and `runs plan` print, for a plan in a format the
release does not read:

```text
saved plan format 6 is not supported; this release reads formats 2, 3, 4 and 5
```

The exact wording is the reader's current message (`infrahub_sync/plan/reader.py:283`).
