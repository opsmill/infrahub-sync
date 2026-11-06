# Contract: CLI output

## `infrahub-sync runs logs <run_id> [--stage <stage>] [--follow]`

One line per log line, tab-separated, in the row style of `runs list` (`_echo_rows`, `infrahub_sync/cli.py:387`):

```text
<timestamp>	<stage>	<level>	<message>
```

- Without `--follow`: reads every page, prints the lines, exits 0.
- With `--follow`: polls every 2 seconds with `offset` equal to the lines printed so far, and exits 0 when
  `run_phase` is terminal and a last read returns no new line.
- `log-expired` and `log-unavailable` are printed with the existing `error:` field style and a non-zero exit code;
  the exit codes follow the existing mapping for 410 and 503.

## `infrahub-sync events [--run-id] [--config-id] [--type] [--follow]`

One line per event, tab-separated, in the same row style:

```text
<occurred_at>	<event_type>	<primary kind>:<primary id>	<actor>	<outcome>
```

- Without `--follow`: prints every page, exits 0.
- With `--follow`: polls every 2 seconds with `offset` equal to the events printed so far, until the operator stops
  it.

## `infrahub-sync runs show <run_id>`

Unchanged fields. A failed stage's `cause` and `next_action` are printed where the run results already hold them;
for a missing branch they name the branch and the fix (FR-002).
