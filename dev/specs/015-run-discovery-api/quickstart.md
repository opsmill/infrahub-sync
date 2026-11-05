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

## Validation notes (2026-11-04, follow-ups)

The end-to-end scenario ran again at HEAD d3cea1c3, after four follow-up changes: the most
recently applied run, `configs version` retries after a rename, the configuration and
applied-run indexes, and Prefect tags at flow-run creation. All nine steps passed. Step 7
added a check that an older plan applied last is reported as `latest_applied_run`.

### Environment

- Sync stack: `uv run invoke build` and `uv run invoke start`, Compose project
  `infrahub-sync-dev`, with the image built from d3cea1c3.
- Non-administrator token: `sync-api` recreated with a scratch override file that adds the
  token of actor `sp015fu-operator` to `INFRAHUB_SYNC_SERVICE_BEARER_TOKENS`.
- Infrahub 1.11.10 from Infrahub's own Compose file, Compose project `sp015fu-infrahub`, with
  `tests/data/nightly_schema` loaded and the branch `netbox-import` created. The worker joined
  it through `INFRAHUB_SYNC_DEV_INFRAHUB_NETWORK=sp015fu-infrahub_default`.
- NetBox demo data from `uv run invoke netbox.seed --dataset demo`, with a temporary
  `development/netbox/netbox.local.env` that set the Compose project to `sp015fu-netbox` and
  the port to 8093.
- Before any run, the tag `team:network` was added by hand to the `infrahub-sync-service/run`
  deployment through the Prefect API, next to `infrahub-sync` and `service`.
- Packages as in the previous notes. `configs version` named the configuration by its name,
  `from-netbox`, before the second configuration existed.
- A first attempt was discarded: an `apply` without `--branch netbox-import` failed with
  `PlanVerificationError` (the plan was bound to `netbox-import`, the apply to `main`) and
  wrote nothing. The Sync stack was reset with `invoke destroy` and started again, so the run
  counts below come from a clean store.
- All three Compose projects and their volumes were removed afterwards, with `invoke destroy`,
  `invoke netbox.down` and `docker compose -p sp015fu-infrahub down --volumes`.

### Results by step

1. Pass, 21:29:15Z to 21:29:25Z. `configs register` printed
   `config_id: 20261104T2129-cb872277`. `configs version from-netbox` printed
   `config_id: 20261104T2129-cb872277` and the idempotency key before the request, then
   `registry_version: 2`. A second `configs register` printed
   `config_id: 20261104T2129-b02b02c4`, `name: from-netbox`.
2. Pass, 21:29:30Z. `whoami` printed `actor: sp015fu-operator`, `administrator: false` with
   the non-administrator token, and `administrator: true` with the administrator token.
3. Pass, 21:29:30Z. `plan --config from-netbox --version 1` exited 2 with
   `error: name 'from-netbox' matches several configurations:`, followed by both `config_id`
   values.
4. Pass, 21:29:35Z to 21:31:00Z. `plan --config 20261104T2129-cb872277 --version 1
   --branch netbox-import` created run `20261104T2129-a128e7db` with 1688 operations.
   `apply --branch netbox-import` with the reviewed checksum ended with `phase: applied`,
   `outcome: applied`.
5. Pass, 21:31:05Z and 21:31:31Z. The plans of versions 1 and 2 created runs
   `20261104T2131-05e87d79` and `20261104T2131-5068b4cf`, each with 1 operation.
6. Pass, 21:31:50Z. `runs list --config 20261104T2129-cb872277` printed three rows, newest
   first: `20261104T2131-5068b4cf` (`@2`), `20261104T2131-05e87d79` (`@1`) and
   `20261104T2129-a128e7db` (`@1`, `applied`). With `--version 1` it printed the two
   version 1 runs.
7. Pass, 21:31:51Z and 21:32:38Z. `configs versions` printed, for version 2,
   `latest_applied_run: <none>`, and for version 1 `latest_applied_run:
   20261104T2129-a128e7db`. Then plan P2 `20261104T2131-791e0d7c` was created at 21:31:56Z
   for version 1, after P1 `20261104T2131-05e87d79` (21:31:06Z). P2 was applied first
   (finished 21:32:19Z), then P1 (finished 21:32:32Z). `configs versions` then printed, for
   version 1, `latest_run: 20261104T2131-791e0d7c` and `latest_applied_run:
   20261104T2131-05e87d79`: the run applied most recently, although it was planned earlier.
8. Pass, 21:32:50Z. The Prefect API (`POST /api/flow_runs/filter`) returned 2 flow runs for
   the tag `run:20261104T2129-a128e7db`: `Plan sync of from-netbox` with `stage:plan` and
   `Apply sync plan of from-netbox` with `stage:apply`. Both carry
   `config:20261104T2129-cb872277`, `config-version:20261104T2129-cb872277@1`,
   `infrahub-sync`, `service` and the hand-added `team:network`. The filter
   `config:20261104T2129-cb872277` returned 7 flow runs, which cover every plan and apply of
   steps 4, 5 and 7, and all 7 carry `team:network`.
9. Pass, 21:32:56Z. `diff --config 20261104T2129-cb872277 --version 1 --branch netbox-import`
   created run `20261104T2132-db7bd0e7` and printed `warning: 'diff' is deprecated, use
   'plan'`. `diff` does not appear in `infrahub-sync --help`.

### Retry of `configs version` after a rename

At 21:33:25Z, `configs version 20261104T2129-b02b02c4` with a package named
`from-netbox-renamed` and `--idempotency-key sp015fu-rename-key-0001` printed `config_id` and
the key, and created version 2, which renamed the configuration. The same command again with
the same key and the `config_id` returned the original response: `registry_version: 2`,
`created: true`, and no version 3. At 21:33:35Z, the same retry by the old name resolved
`from-netbox` to the other configuration, printed `config_id: 20261104T2129-cb872277`, and
was refused with `409 idempotency-conflict`; nothing was created. The hint printed when a
retried name matches no configuration was checked by the unit test only.

### Other checks

- PostgreSQL integration tier (`invoke tests.tests-product-store-postgresql`) against a
  temporary `postgres:16-alpine` container: 74 passed at 21:21:56Z. The container and its
  volume were removed afterwards.
- `EXPLAIN` on PostgreSQL 16 and `EXPLAIN QUERY PLAN` on SQLite 3.45.1 used the new indexes;
  research R3 records the plans.
