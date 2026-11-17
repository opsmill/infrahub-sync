# Contract: CLI

A new command group `infrahub-sync schedules`. Each command calls one route of
[http-api.md](http-api.md) through one method of `SyncClient`. `<schedule>` is a schedule's
identifier or its name; the identifier is tried first.

Commands that change something take `--reason` and `--idempotency-key`, and print the
idempotency key before the request, as the existing commands do.

| Command | Client method | Route |
| --- | --- | --- |
| `schedules create` | `create_schedule` | `POST /schedules` |
| `schedules list` | `list_schedules`, `iter_schedules` | `GET /schedules` |
| `schedules show <schedule>` | `get_schedule` | `GET /schedules/{schedule_id}` |
| `schedules change <schedule>` | `change_schedule` | `POST /schedules/{schedule_id}/change` |
| `schedules pause <schedule>` | `pause_schedule` | `POST /schedules/{schedule_id}/pause` |
| `schedules resume <schedule>` | `resume_schedule` | `POST /schedules/{schedule_id}/resume` |
| `schedules delete <schedule>` | `delete_schedule` | `POST /schedules/{schedule_id}/delete` |
| `schedules take-over <schedule>` | `take_over_schedule` | `POST /schedules/{schedule_id}/take-over` |

## `infrahub-sync schedules create`

```console
infrahub-sync schedules create --name from-netbox-nightly --config from-netbox --version 3 \
  --cron "0 2 * * *" --timezone Europe/Paris
```

| Option | Meaning |
| --- | --- |
| `--name` | Required. |
| `--config` | Required. A configuration identifier or name. |
| `--version` | Required. |
| `--cron` | Required. |
| `--timezone` | Default `UTC`. |
| `--write-to-default-branch` | Selects the write target `default-branch`. Without it the write target is branch review. |
| `--write-when-left-out` | Turns the left-out setting on. |
| `--overlap-policy` | `reject` (default) or `queue` (user story 3). |
| `--start-late-by-at-most` | A duration such as `30m` or `2h` (user story 3). |

Output: the fields of the schedule, one `name: value` line each, with `next_start_at`.

## `infrahub-sync schedules list`

Options: `--config`, `--state`, `--limit`, `--all`. One line for each schedule: identifier,
name, configuration and version, state, cron, time zone, next start time.

## `infrahub-sync schedules show <schedule>`

Prints every field of the schedule.

## `infrahub-sync schedules change <schedule>`

Options: `--version`, `--cron`, `--timezone`, `--write-to-default-branch` and
`--write-to-branch-review`, `--write-when-left-out` and `--no-write-when-left-out`,
`--overlap-policy`, `--start-late-by-at-most`. A command with none of them exits 2. Output as
for `create`.

## `infrahub-sync schedules pause`, `resume`, `delete`, `take-over`

`delete` asks for confirmation unless `--yes` is given, as `runs cancel` does. Output: the fields
of the schedule.

## Warnings

`schedules list` and `schedules show` write to the standard error stream, for each schedule that
it applies to:

- `warning: a newer version of this configuration exists: <config_id> version <n>` (user story 3)
- `warning: this schedule is stopped: <state_reason>`

A warning does not change the exit code.

## Changed commands

- `runs list` gains `--trigger-type` and `--schedule`, and its lines show the trigger type.
- `runs show` prints `trigger_type` and `schedule_id`.
- `events` accepts `--schedule <schedule>`, which filters on the related schedule.

## Exit codes

As for the existing commands: 2 for an argument the client refuses or a schedule that cannot be
resolved, 1 for a refusal or a failure of the Sync API, 130 on an interrupt.
