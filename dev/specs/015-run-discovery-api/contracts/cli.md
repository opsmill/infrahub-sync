# Contract: CLI changes

## Command map

| CLI command | API action | Status |
| --- | --- | --- |
| `infrahub-sync plan --config CONFIG --version N [--branch B] [--reason R]` | `POST /runs` (`operation: plan`) | Renamed from `diff` |
| `infrahub-sync diff ...` | same as `plan` | Hidden alias; prints `warning: 'diff' is deprecated, use 'plan'` to stderr; removed at 3.0 |
| `infrahub-sync verify RUN_ID [--reason R]` | `POST /runs/{id}/verify` | New |
| `infrahub-sync apply RUN_ID --expected-checksum C ...` | `POST /runs/{id}/apply` | Unchanged |
| `infrahub-sync sync --config CONFIG --version N ...` | `POST /runs` (`operation: sync`) | Unchanged except name resolution |
| `infrahub-sync runs list [--config CONFIG] [--version N] [--limit L] [--all]` | `GET /runs` | New |
| `infrahub-sync runs show RUN_ID` | `GET /runs/{id}` | Unchanged |
| `infrahub-sync runs plan RUN_ID` | `GET /runs/{id}/plan` | Unchanged |
| `infrahub-sync runs results RUN_ID` | `GET /runs/{id}/results` | Unchanged |
| `infrahub-sync runs artifacts RUN_ID` | `GET /runs/{id}/artifacts` | New |
| `infrahub-sync runs cancel RUN_ID [--reason R] [--yes]` | `POST /runs/{id}/cancel` | New. Without `--yes`, prompts for confirmation when interactive and refuses (exit 2) when not, because cancelling a write can leave an `ambiguous` outcome. |
| `infrahub-sync whoami` | `GET /me` | New |
| `infrahub-sync configs list [--name N] [--limit L] [--all]` | `GET /configs` | Adds name column, filter, paging |
| `infrahub-sync configs versions CONFIG [--limit L] [--all]` | `GET /configs/{id}/versions` | Adds latest run and latest applied run columns, paging |

`--config-id` stays accepted as an alias of `--config` on `plan`, `diff` and `sync`.

## Configuration name resolution

Applies to every `CONFIG` argument or `--config` option:

1. Read `GET /configs/{value}`. If found, use it.
2. On `404`, read `GET /configs?name={value}` (all pages).
3. One match: use its `config_id`. Several: exit 2 with
   `error: name 'X' matches several configurations:` followed by one `config_id` per line.
   None: exit 2 with `error: no configuration has the id or name 'X'`.

The identifier is tried before the name, so a value that is both a `config_id` and another
configuration's name resolves to the identifier. The CLI reference states this order.

## Output

- `runs list` prints one row per run: `run_id`, `config_id@version` (or `-`), branch (or `-`),
  `phase`, `outcome`, `actor`, `started_at`. When `next_cursor` is set and `--all` is not given, it
  prints `more runs: rerun with --all, or --limit up to 200`.
- `whoami` prints `actor: <actor>` and `administrator: <true|false>`.
- `--json` (existing global behavior where supported) prints the API response unchanged.
