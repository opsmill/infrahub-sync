# Quickstart: validate run discovery

## Prerequisites

- The local development stack from `docs/docs/development-stack.mdx` (Sync API, worker,
  Prefect, PostgreSQL, S3, Infrahub, NetBox demo data).
- A non-administrator token in `SYNC_API_TOKEN` and an administrator token in
  `SYNC_ADMIN_TOKEN`.

## Offline checks

```bash
uv run invoke tests.tests-unit
uv run invoke lint
uv run infrahub-sync --help
uv run infrahub-sync runs --help
```

The new unit tests cover the contracts in [contracts/http-api.md](contracts/http-api.md) and
[contracts/cli.md](contracts/cli.md) against the SQLite store and a fake orchestrator.

## End-to-end scenario

1. Register a package twice (versions 1 and 2) with the administrator token, then register a
   second configuration with the same `name`.
2. `infrahub-sync whoami` → `administrator: false` with `SYNC_API_TOKEN`.
3. `infrahub-sync plan --config <name>` → refused, listing both `config_id` values.
4. `infrahub-sync plan --config <config_id> --version 1`, review, then
   `infrahub-sync apply RUN_ID --expected-checksum ...`.
5. `infrahub-sync plan --config <config_id> --version 1` and
   `infrahub-sync plan --config <config_id> --version 2`.
6. `infrahub-sync runs list --config <config_id>` → three runs, newest first.
   `--version 1` → two runs.
7. `infrahub-sync configs versions <config_id>` → version 2 has a latest run and no applied
   run; version 1 shows the applied run.
8. In the Prefect UI, filter flow runs on the tag `run:<RUN_ID>` of step 4 → the plan and
   apply flow runs. Filter on `config:<config_id>` → every flow run of the three runs.
9. `infrahub-sync diff --config <config_id> --version 1` → works and prints the deprecation
   warning.

## Paging check

With the unit fixture that creates 120 runs and inserts 10 more between page reads, following
`next_cursor` returns each of the original 120 runs exactly once.
