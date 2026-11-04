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

## Validation notes (2026-11-04)

The end-to-end scenario ran at HEAD 9bb18ea3, with the CLI as implemented: `plan`,
`runs list --config/--version`, `configs versions`, `whoami`, the `diff` alias and
configuration name resolution. All nine steps passed, and no product defect was found.

### Environment

- Sync stack: `uv run invoke build` and `uv run invoke start`, Compose project
  `infrahub-sync-dev`. Sync API at `http://127.0.0.1:8030`, Prefect at `http://127.0.0.1:4230`.
- Non-administrator token: the dev Compose file defines only the administrator token `dev`.
  The `sync-api` service was recreated with a scratch override file that adds the
  non-administrator token `t043-operator` to `INFRAHUB_SYNC_SERVICE_BEARER_TOKENS`.
- Infrahub 1.11.10 from Infrahub's own Compose file, Compose project `t043-infrahub`, at
  `http://localhost:8000`, with the `tests/data/nightly_schema` schema loaded. The worker joined
  it through `INFRAHUB_SYNC_DEV_INFRAHUB_NETWORK=t043-infrahub_default`.
- NetBox demo data from `uv run invoke netbox.seed --dataset demo`, with a temporary
  `development/netbox/netbox.local.env` that set the Compose project to `t043-netbox` and the
  port to 8092. Another worktree already uses the default project, `infrahub-sync-netbox`.
- Packages: the `netbox.demo-package --dev-stack` output as version 1. Version 2 and the
  second configuration remove the last one and the last two `schema_mapping` entries, so that
  each registration has different content.
- All three Compose projects and their volumes were removed afterwards, with `invoke destroy`,
  `invoke netbox.down` and `docker compose -p t043-infrahub down --volumes`.

### Results by step

1. Pass, 16:21:18Z. `configs register` printed `config_id: 20261104T1621-00d27051`.
   `configs version` printed `registry_version: 2`. A second `configs register` printed
   `config_id: 20261104T1621-55c24a28`, `name: from-netbox`.
2. Pass, 16:21:28Z. `whoami` with the non-administrator token printed `actor: t043-operator`
   and `administrator: false`. With the administrator token it printed `administrator: true`.
3. Pass, 16:21:29Z. `plan --config from-netbox --version 1` exited 2 with
   `error: name 'from-netbox' matches several configurations:`, followed by both `config_id`
   values.
4. Pass, 16:21:40Z to 16:23:15Z. `plan --config 20261104T1621-00d27051 --version 1
   --branch netbox-import` created run `20261104T1621-f0277f2f` with 1688 operations.
   `apply` with the reviewed checksum ended with `phase: applied`, `outcome: applied`.
5. Pass, 16:23:20Z and 16:23:49Z. The plans of versions 1 and 2 created runs
   `20261104T1623-0983c521` and `20261104T1623-9d7c9248`, each with 1 operation.
6. Pass, 16:24:04Z. `runs list --config 20261104T1621-00d27051` printed three rows, newest
   first: `20261104T1623-9d7c9248` (`@2`), `20261104T1623-0983c521` (`@1`) and
   `20261104T1621-f0277f2f` (`@1`, `applied`). With `--version 1` it printed the two
   version 1 runs.
7. Pass, 16:24:05Z. `configs versions 20261104T1621-00d27051` printed, for version 2,
   `latest_run: 20261104T1623-9d7c9248` and `latest_applied_run: <none>`. For version 1 it
   printed `latest_applied_run: 20261104T1621-f0277f2f phase=applied outcome=applied`.
8. Pass, 16:24:11Z. The Prefect API (`POST /api/flow_runs/filter`) returned 2 flow runs for
   the tag `run:20261104T1621-f0277f2f`: the plan flow run (`stage:plan`) and the apply flow
   run (`stage:apply`). It returned 4 flow runs for `config:20261104T1621-00d27051`, which
   cover the three runs of steps 4 and 5. Each flow run also carries a
   `config-version:<config_id>@<version>` tag.
9. Pass, 16:24:19Z. `diff --config 20261104T1621-00d27051 --version 1` exited 0, created run
   `20261104T1624-db9d7074` and printed `warning: 'diff' is deprecated, use 'plan'` to stderr.
   `diff` does not appear in `infrahub-sync --help`.
