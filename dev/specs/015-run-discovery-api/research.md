# Research: Discoverable runs, configurations and versions

All paths are relative to the repository root on `015-run-discovery-api` (based on
`feature/v3-develop` at `8d1518918`).

## R1. Where a run's configuration binding lives

- **Finding**: `product_runs` has real `config_id`, `registry_version` and `package_checksum`
  columns, added at start-up by `_migrate_product_runs_columns`
  (`infrahub_sync/product_store/store.py:150-153`, `:616-621`). A CHECK constraint and
  triggers keep the three all NULL or all set (`store.py:169-170`, `:211-223`). The comment
  at `store.py:142-149` that says no `ProductRun` field reads them is stale.
- **Finding**: the branch is not stored on the run. It exists in the Prefect parameters, the
  receipt fingerprint, and the saved plan's `destination_branch`.
- **Decision**: filter on the existing columns. Do not add `branch` to the run list in this
  feature; the spec's acceptance scenario that lists `branch` is narrowed in the contract to
  the fields the run resource already publishes. Branch stays readable from the run's plan.
- **Rationale**: adding a stored branch is a write-path change on run creation and a backfill
  question for existing runs; it is not needed to find runs by configuration.
- **Alternatives considered**: read `destination_branch` from each saved plan while listing
  (one object-store read per row; rejected for cost); add a `branch` column now (deferred).

## R2. Ordering and cursor

- **Finding**: `generate_run_id()` (`infrahub_sync/cache/paths.py:63-70`) is
  `YYYYMMDDTHHMM-<8 hex>`, so it orders by time only to the minute. `started_at` is stored as
  ISO 8601 text (`store.py:840`). No index exists on `started_at`, `config_id` or
  `registry_version`.
- **Decision**: order by `(started_at DESC, run_id DESC)` and page with a keyset cursor on that
  pair. The cursor is an opaque, URL-safe base64 JSON value holding the last
  `(started_at, run_id)` and a digest of the filters it was issued for. A cursor whose digest
  does not match the request's filters, or that does not decode, is refused with `422`.
  The store converts `started_at` to UTC when it inserts a run, so text order equals time
  order even when an injected service clock is not UTC (critique E1).
- **Rationale**: keyset paging meets FR-004. Runs created after the first page are newer than
  every cursor, so they never appear in later pages, and no existing run is skipped. Offset
  paging would shift rows when new runs arrive.
- **Alternatives considered**: offset/limit (fails FR-004); `run_id`-only cursor (minute
  resolution with random tie-break breaks newest-first order).

## R3. Indexes and migration

- **Finding**: the store has no numbered migrations. `_initialize` runs `CREATE ... IF NOT
  EXISTS` and idempotent `_migrate_*` methods that inspect the catalog
  (`store.py:564-592`, `:594-614`), on both SQLite and PostgreSQL (`_RelationalRunStore`,
  `store.py:543`).
- **Decision**: add, in the same idempotent style:
  - `CREATE INDEX IF NOT EXISTS product_runs_started ON product_runs (started_at, run_id)`;
  - `CREATE INDEX IF NOT EXISTS product_runs_binding_started ON product_runs (config_id,
    registry_version, started_at, run_id)`;
  - nullable columns `registered_by` and `registration_reason` on `configuration_versions`
    (R5);
  - a nullable `name` column on `configurations` (R6).
- **Rationale**: matches the existing migration mechanism; every step is safe to re-run and
  safe under the concurrent first start-up already documented in the API reference.
- **Governance gate**: database schema change. Called out in `plan.md`.

## R4. Store, projection and service shape

- **Finding**: `_RunStore` (`store.py:402-525`) has `list_configurations` and
  `list_configuration_versions`; `ProductProjection` (`store.py:2600`) passes them through.
  There is no run list; `lookup_run` loads artifacts and executions per run.
- **Decision**: add `list_runs(filters, after, limit) -> RunPage` to the protocol, implemented
  once in `_RelationalRunStore`. List rows map to `ProductRun` without artifact references or
  execution links. The run list returns the same public resource with `artifact_refs` and
  `prefect_executions` empty; `GET /runs/{id}` remains the full read. This is documented in
  the contract.
- **Rationale**: loading executions and artifacts for 200 rows is N+1 queries; list consumers
  (CLI table, UI list) do not need them.
- **Alternatives considered**: a separate slim `RunSummaryResource` (rejected: a second model
  for the same fields, and FR-002 says the same public fields).

## R5. Who registered a version, and why

- **Finding**: `configuration_versions` has no actor or reason
  (`store.py:102-107`). Actor and reason exist on the `mutation_receipts` row of the
  registration, with `registry_version` only inside `response_body` JSON
  (`infrahub_sync/service/config_routes.py:177-257`).
- **Decision**: add nullable `registered_by` and `registration_reason` columns and write them
  in the same transaction that inserts the version. Versions registered before this change
  report `null` for both; no backfill from receipts.
- **Rationale**: parsing stored response bodies to backfill is fragile, and the alpha already
  requires a reset on upgrade (`product/path-to-3.0.md`).
- **Alternatives considered**: join receipts at read time (fragile JSON parsing per row);
  backfill on start-up (same fragility, once).

## R6. Configuration name

- **Finding**: the name is `declared_content["configuration"]["name"]`
  (`infrahub_sync/configuration/models.py:589-596`; `infrahub_sync/service/service.py:246`).
  Nothing makes names unique. `ConfigurationSummaryResource` has only `config_id` and
  `created_at` (`infrahub_sync/client/models.py:378`).
- **Decision**: store the latest version's name in a nullable `configurations.name` column,
  updated in the transaction that registers each version. At start-up, fill rows whose `name`
  is NULL from their latest version's package (idempotent). Filter `GET /configs?name=` by
  exact match.
- **Rationale**: avoids dialect-specific JSON extraction in list queries and keeps the filter
  indexable.

## R7. Latest run and latest applied run per version

- **Finding**: successful `apply` and `sync` both set `phase="applied"`; plans set
  `planned` (`infrahub_sync/service/`).
- **Decision**: for each version on the returned page, select the newest run by
  `(started_at, run_id)` and the newest run with `phase = 'applied'`, using the new binding
  index. One query per page, not per row (window function or two grouped subqueries, both
  available on SQLite 3.25+ and PostgreSQL).
- **Rationale**: answers "was this version ever applied?" without a client-side scan.

## R8. Pagination for configuration and version lists

- **Finding**: `GET /configs` and `GET /configs/{id}/versions` validate `offset` and `limit`
  and ignore them (`config_routes.py:331-356`); `_MAX_PAGE_LIMIT = 256`. The client expects a
  bare JSON array (`infrahub_sync/client/client.py:197`, `:215`, `:465`).
- **Decision**: replace `offset` with `cursor`, set default `limit` 50 and maximum 200, and
  return `{"items": [...], "next_cursor": ...}` on all three lists. Configurations order by
  `(created_at DESC, config_id DESC)`; versions by `registry_version DESC`. The client gains
  page iteration; the CLI prints one page and a hint, with `--all` to follow cursors.
- **Rationale**: one paging model across the API (FR-008). The response shape change is
  acceptable under `v3-unstable`.

## R9. Prefect tags

- **Finding**: the deployment carries `("infrahub-sync", "service")`
  (`infrahub_sync/service/orchestration.py:47-54`). The vendored executor's `submit` takes no
  tags (`opsmill_prefect_extras/executors.py:589-617`) and the vendored copy must stay
  byte-identical to upstream (`opsmill_prefect_extras/VENDORED.md`). Prefect's
  `update_flow_run(..., tags=...)` replaces the tag list instead of merging.
- **Decision**: extend `_title` (`orchestration.py:184-205`) to read the flow run's current
  tags (`read_flow_run`) and call
  `update_flow_run(flow_run_id, name=..., tags=sorted(current_tags | run_tags))`, and widen
  the `_NamingClient` protocol. Taking the union keeps the deployment's tags and any tag an
  operator added in Prefect (critique E2). Run tags: `run:<run_id>`, `stage:<stage>`, and for registered
  runs `config:<config_id>` and `config-version:<config_id>@<registry_version>`. A failure is
  logged and swallowed exactly like the title today (FR-012).
- **Rationale**: no change to vendored code; one API call already exists at that point.
- **Alternatives considered**: tags at creation through the executor (requires changing
  upstream `opsmill-prefect-extras` first).

## R10. Token check

- **Finding**: `Principal` has `actor` and `administrator` (`infrahub_sync/service/auth.py:15-21`);
  `authenticate` records refusals through `record_authentication_refusal`
  (`infrahub_sync/service/app.py:81-96`, `service.py:1003-1011`).
- **Decision**: `GET /me` depends on `authenticate` and returns `{"actor", "administrator"}`.
  No audit event on success (it is a read); refusals are recorded by the existing dependency.

## R11. CLI

- **Finding**: `diff`, `sync` and `apply` are top-level; `runs show|results|plan` exist
  (`infrahub_sync/cli.py:620-784`). `SyncClient` already has `verify`, `cancel_run` and
  `list_artifacts`, with no CLI command. There is no `list_runs` or `me`. Configuration
  identifiers are `--config-id` options on `diff`/`sync` and positional arguments on
  `configs` subcommands.
- **Decision**:
  - `plan` replaces `diff`; `diff` stays as a hidden Typer command that prints a deprecation
    warning to stderr and delegates.
  - New: `verify RUN_ID`, `runs list [--config] [--version] [--limit] [--all]`,
    `runs artifacts RUN_ID`, `runs cancel RUN_ID`, `whoami`.
  - Name resolution: every configuration argument or `--config-id` option first tries the
    value as a `config_id`; on not-found, it lists configurations filtered by that name. One
    match is used; several matches are refused with the list of IDs; none is refused as not
    found.
- **Rationale**: identifier-first avoids surprising a user whose `config_id` equals another
  configuration's name.

## R12. Constitution and docs

- **Finding**: the constitution names `diff` as a non-applying command
  (`docs/docs/development/constitution.md:20`). `infrahub-sync diff` appears 18 times in 14
  docs files; `docs/docs/reference/cli.mdx` is generated by `invoke docs.generate`
  (`tasks/docs.py:14-24`).
- **Decision**: update the constitution line to name `plan` (a wording amendment, not a
  principle change), update every current page except past release notes, and regenerate the
  CLI reference.
