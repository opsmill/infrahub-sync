---
title: "Repository tour"
---

## Repository tour

> Part of: Develop > Knowledge | Related: [Sync architecture](sync-architecture.md), [The shared execution surface](execution-surface.md), [Testing tiers](../guidelines/testing-tiers.md)

Find the module responsible for a change, from CLI requests to worker execution and
stored results. For service components and the registered-run lifecycle, start with
[Sync architecture](sync-architecture.md); for the test suite to run, see
[Testing tiers](../guidelines/testing-tiers.md).

The module inventory was verified on 2026-09-16 against
[`61b6a1b`][src-revision]. Inventory links retain that revision; the execution trace
below links to the released [`f98a845`][route-revision] source.

### Execution routes {#the-one-boundary-to-hold-on-to}

| Route | Purpose | Modules |
|---|---|---|
| Registered V3 execution | Register a configuration package with the Sync API, plan, review and approve destination writes. | [`infrahub_sync/service/`][src-infrahub-sync-service], [`infrahub_sync/product_store/`][src-infrahub-sync-product-store], [`infrahub_sync/configuration/`][src-infrahub-sync-configuration] |
| Internal generation and local plugins | Render DiffSync modules and load filesystem adapters during adapter development. | [`infrahub_sync/generator/`][src-infrahub-sync-generator], [`infrahub_sync/plugin_loader.py`][src-infrahub-sync-plugin-loader-py] |
| Direct Prefect planning | Run a read-only plan for a configuration in a local directory, without the Sync API. | [`infrahub_sync/orchestration/flow.py`][route-direct-flow], [`run_remote_request`][route-remote-request] |

A fourth category is historical evidence — archived specifications and decision records under
`dev/`. It documents why the code is shaped this way; it is not a description of current
behavior.

### The command-line client and its HTTP client

- [`infrahub_sync/cli.py`][src-infrahub-sync-cli-py] — the Typer entry point. It is a client of
  the Sync HTTP API: it constructs no HTTP request itself and reads neither the source system
  nor a local plan file. `configs convert` is the exception: it does not call the Sync API. It
  reads a v1 or v2 `config.yml` and writes a package on the machine where it runs. With
  `--live-schema` it reads the destination Infrahub schema without a token, through
  `configuration/conversion_live_schema.py`; without the flag it opens no network connection.
- [`infrahub_sync/client/`][src-infrahub-sync-client] — the only place that builds Sync API requests.
  `client.py` holds the synchronous client, `models.py` its typed request and response shapes,
  and `errors.py` its error taxonomy.

The command surface is `configs` (including the local `configs convert`), `runs`, `plan`,
`verify`, `approve`, `sync`, `apply`, `events` and `whoami`. See the
[CLI reference](../../reference/cli.mdx) for every option.

To trace a registered run from a CLI command to execution, follow these modules:

| Stage | Module and action |
|---|---|
| CLI command | [`cli.py`][route-cli] calls `SyncClient` to submit a plan, sync or apply request. |
| HTTP request | [`client/client.py`][route-client] sends the request to the Sync API and reads run, plan and result resources. |
| Service submission | [`service/app.py`][route-api] handles HTTP requests; [`service/service.py`][route-service] records and submits work through [`service/orchestration.py`][route-orchestration] to Prefect. |
| Worker execution | The process worker executes [`service/flow.py`][route-flow], which resolves the registered configuration and calls the core plan, verify and apply operations. |
| Core operation | [`execution.py`][route-execution] executes each operation. Registered `sync` composes plan, verify and apply in the service flow. |

The CLI reads results through the API. For the execution stages and retained records,
see [the registered-run lifecycle](sync-architecture.md#one-registered-run).

### Configuration admission

`infrahub_sync/configuration/` decides what a registered package is allowed to declare. It
performs no I/O against a source or destination unless a caller explicitly opts in: to the
destination-schema checks, or to the schema read of `configs convert --live-schema`.

| Module | Owns |
|---|---|
| `models.py` | The package envelope, its parser, and the finding type |
| `capabilities.py` | `AdapterConfigurationCapabilities` and the closed `BUILTIN_ADAPTER_CAPABILITIES` registry |
| `infrahub_lineage.py` | The parser and validator for an Infrahub destination's `source`, `owner` and `protect` settings |
| `validation.py` | The finding-producing checks and their fixed execution order |
| `credentials.py` | What a credential reference is, and how a provider resolves one |
| `worker_credentials.py` | The names of the credential variables a worker holds, and the `$credential` variables a package references, for `configs validate --branch` |
| `schema_validation.py` | The opt-in destination-schema checks |
| `warnings.py` | Intentional omissions and unqualified optional features |
| `runtime.py` | Runtime resolution, including the effective destination branch |
| `conversion.py` | `configs convert`: the pure conversion of a v1 or v2 `config.yml` into a package, the tables of what v2 read from the environment, and the worker's credential variables |
| `conversion_authentication.py` | Which credential settings each v2 authentication method used, so a converted package references only those |
| `conversion_live_schema.py` | `configs convert --live-schema`: the destination-schema read without a token, and the fill of an unmapped IP namespace with `default` |
| `conversion_report.py` | The text of the conversion report, the refusals and the next steps |

[Configuration foundation](configuration-foundation.md) explains the declared identity,
credential references and the connection-free capability declaration in full.

### The Sync service and its worker

`infrahub_sync/service/` is the optional service extra. It holds the FastAPI application
(`app.py`, `serve.py`, `config_routes.py`), authentication (`auth.py`), the Prefect worker and
deployment (`worker.py`, `deploy.py`, `orchestration.py`, `flow.py`), liveness and checkpoint
policy (`liveness.py`, `checkpoints.py`), per-stage scratch directories (`scratch.py`), the
write guard (`apply_guard.py`), artifact storage (`storage.py`), the one-shot start-up job of
the Compose deployment (`bootstrap.py`) and the trust of the operator's CA certificates
(`trust_store.py`). `prefect_server.py` holds
`create_prefect_app`, the application factory that the root `docker-compose.yml`, the
development stack's `development/docker-compose.yml` and the copy that
`infrahub_sync.testcontainers` ships (`infrahub_sync/testcontainers/docker-compose.yml`) pass to uvicorn. These stacks run the Prefect server from the Sync image, so they need no
separate Prefect image.

Two facts about it are often missed:

- Each stage creates its own private scratch directory. The service reads no shared cache
  location.
- It resolves its flow as an installed module, so it declares no working directory and needs
  no source tree.

[The configuration write guard](apply-guard.md) covers the advisory lock that serializes one
configuration's writes.

Six service modules carry what a run tells the operator:

- `failure_cause.py` builds the stage failure record: it classifies the error into a
  `failure_type` and copies a fixed set of fields of typed errors (system, Infrahub error code,
  node kind, attribute, record, field). It never copies `str(exc)`; an error class missing from
  its classification table becomes `internal_error`. ADR 0021 records why.
- `run_artifact.py` builds the markdown artifact the worker publishes on each Prefect flow run,
  including the left-out and problem counts and the review commands of a plan that leaves out
  records.
- `read_progress.py` stores the read state of the source and of the destination while a `plan`
  or `sync` stage runs, and derives the `reads` field of the run response from the stored
  documents. The engine side of the report is `infrahub_sync/potenda/read_progress.py`.
- `events.py` builds the event of a request, a configuration change or a stage result, and its
  type (`infrahub_sync.run.accepted`, `infrahub_sync.run.planned` and the others). The store
  saves the event of an accepted request or a stage result in the transaction that saves the
  answer or the result. ADR 0033 records why, and which events are saved by a call of their own.
- `run_log.py` keeps the lines that Sync wrote out of a run's Prefect log, for
  `GET /runs/{run_id}/logs`. [How the Sync API reads a run's log](orchestration-prefect.md#how-the-sync-api-reads-a-runs-log)
  describes it.
- `flow.py` also stops a `sync` whose plan leaves out records, after the plan checkpoint and
  before any write. The stage result carries a `failure` with `failure_type:
  left_out_review_required`, and the store releases the run's write admission in the same
  commit (`release_write_admission` in `product_store/models.py`).

`paging.py` holds the cursor that the three lists share: `GET /runs`, `GET /configs` and
`GET /configs/{config_id}/versions`. A cursor is URL-safe base64 of a JSON object with two keys:
`k`, the sort key of the last row returned, and `f`, the first 16 hexadecimal characters of the
SHA-256 of the list's name and filters. `decode_cursor` raises `InvalidCursorError` for a value
that does not decode, that is longer than 2,048 characters or nested too deeply, or whose digest
differs from the request's; the HTTP layer returns `422 invalid-cursor`. `validate_limit` applies
the limits in `client/models.py` (`DEFAULT_LIMIT` 50, `MAX_LIMIT` 200). ADR 0032 records the
paging rules.

Each worker publishes the names of its set credential variables to the `worker_environments`
table on each poll, through `service.storage.service_product_projection`.

How the Compose deployment reaches its object store and trusts a private CA:

- `storage.s3_client_arguments` builds the arguments of every S3 client. `bootstrap.py` calls
  it directly, and `sync-api` and `sync-worker` call it through
  `storage.service_product_projection`. All three check the endpoint, the region and the
  addressing style (`INFRAHUB_SYNC_S3_ADDRESSING_STYLE`, `path` or `virtual`) the same way,
  before any client exists.
- `bootstrap.py` checks that the endpoint and the bundled store agree: it refuses an endpoint
  that names another store while the host name of the bundled store resolves, and an endpoint
  that names the bundled store while that host name does not resolve. It reads no value of
  `INFRAHUB_SYNC_BUNDLED_OBJECT_STORE`; Compose reads that setting to leave the bundled store
  out. It then checks for the bucket with `HeadBucket` and creates the bucket only when the
  store reports it absent. Last, it writes one new check key create-only twice, to confirm that
  the store accepts the first write and refuses the second, and deletes the key. Each failure
  is one fixed name, printed as `refused: <name>`. The operator page lists the names
  ([Use your own S3-compatible store](../../compose-deployment.mdx#use-your-own-s3-compatible-store)).
- `bootstrap.main()`, `worker.main()` and `serve.build_app()` call
  `trust_store.install_custom_ca_bundle()` before they build any client. When the directory
  that `INFRAHUB_SYNC_CA_DIRECTORY` names holds `*.pem` or `*.crt` files, it writes one bundle,
  the public roots of `certifi` followed by those files, to a new file in the process's
  temporary directory, and points `SSL_CERT_FILE`, `REQUESTS_CA_BUNDLE`, `AWS_CA_BUNDLE` and
  `INFRAHUB_TLS_CA_FILE` at it. A variable that already holds a value is kept, and a warning
  names it. An empty variable counts as unset. `tests/service/test_trust_store_entry_points.py`
  checks the three calls.
- The trust store refuses a file in the directory that holds a private key, whatever the file's
  name, and a `*.pem` or `*.crt` file that does not load as a certificate. The error names the
  file, never its content.

### Runtime schema

[`infrahub_sync/runtime_schema/`][src-infrahub-sync-runtime-schema] discovers the destination schema at run time and builds
DiffSync models in memory from it, rather than from committed generated code. `domain.py`
holds the domain types, `projection.py` the projection onto DiffSync models, `worker.py` the
out-of-process discovery path, and `errors.py` its failures.

### One run: the shared execution surface

[`infrahub_sync/execution.py`][route-execution] provides `execute_run` for service worker
stages and the direct Prefect flow. The CLI uses the HTTP client; the direct Prefect flow
calls [`run_remote_request`][route-remote-request], which resolves a local configuration
and calls `execute_run` for a plan. The execution module imports no Prefect symbols and
remains importable in a base install. See
[the shared execution surface](execution-surface.md) for callers, operation inputs and
return types.

### Plans

[`infrahub_sync/plan/`][src-infrahub-sync-plan] owns the saved plan artifact and everything that reads or writes it:

| Module | Owns |
|---|---|
| `models.py`, `canonical.py`, `checksum.py` | The artifact shape, its canonical encoding, and the checksum |
| `writer.py`, `reader.py` | Writing and reading the artifact |
| `derive.py`, `identity.py`, `keying.py`, `ownership.py` | Deriving operations, their identifiers, keyedness and ownership |
| `review.py` | The review projection the CLI renders |
| `verify.py`, `errors.py` | Pre-write verification and the error taxonomy |
| `write_surface.py` | `PlannedWriteDestination`, the destination write surface an apply goes through |
| `config_version.py`, `destination_only_peer.py` | Version binding and destination-only peers |
| `problems.py` | The problem pass of a plan: `run_problem_pass` finds the problems, walks the required relationships to the records that depend on a left-out record, keeps records with an optional link to one, and selects the operations the plan writes. The writer saves the result as `plan/report.jsonl` (plan format 4) |

[The saved plan artifact](plan-artifact.md) and [Planned writes and apply](planned-write-and-apply.md)
are the deep documents.

### Product storage

`infrahub_sync/product_store/` is the durable record of configurations, runs and artifacts.
`configs.py` is the configuration service boundary — register, version, list, show, validate —
`store.py` the durable projection, `models.py` the record types and `bundle.py` the artifact
bundle. [Durable product records](../../reference/durable-product-records.mdx) documents what
is kept.

The store serves the lists from its own tables only. `list_runs`, `list_configuration_page` and
`list_configuration_version_page` each read one page in a fixed order, newest first, and one row
more to tell whether another page follows:

- runs by `(started_at, run_id)`, without loading artifact references or execution links;
- configurations by `(created_at, config_id)`, with an exact-match filter on `name`;
- versions by `registry_version`. One more query for the page
  (`_SELECT_LATEST_VERSION_RUNS`) finds each version's newest run by `started_at` and its most
  recently applied run by `finished_at`. An apply updates the plan's run, so `started_at` is the
  time the plan was created and `finished_at` is the time the apply finished.

The columns a list filters on or shows are written with the row: `product_runs.destination_branch`
when the run is created, `configuration_versions.registered_by` and `registration_reason` and
`configurations.name` in the transaction that registers a version.

Start-up creates the list indexes, each with `CREATE INDEX IF NOT EXISTS`
(`_RUN_LISTING_INDEXES` in `store.py`):

| Index | Serves |
|---|---|
| `product_runs_started (started_at, run_id)` | The run list without a filter |
| `product_runs_config_started (config_id, started_at, run_id)` | The run list of one configuration |
| `product_runs_binding_started (config_id, registry_version, started_at, run_id)` | The run list of one version, and each version's newest run |
| `product_runs_binding_applied (config_id, registry_version, finished_at, run_id) WHERE phase = 'applied'` | Each version's most recently applied run |
| `configurations_name (name)` | The `name` filter of the configuration list |
| `configurations_created (created_at, config_id)` | The order of the configuration list |

### Adapters

[`infrahub_sync/adapters/`][src-infrahub-sync-adapters] holds the nine bundled connectors: `aci`, `genericrestapi`,
`infrahub`, `ipfabricsync`, `nautobot`, `netbox`, `peeringmanager`, `prometheus` and
`slurpitsync`, plus the shared `rest_api_client.py`, `utils.py`, `references.py` and
`endpoint_records.py`. `endpoint_records.py` holds the endpoint lookup and the record
conversion that the `netbox` and `nautobot` adapters share.
`references.py` resolves a source reference to its peer (`peer_unique_id`, `peer_unique_ids`,
`reference_is_required`). Through an optional relationship, it records a peer that is not
loaded or not in the source in the adapter's load report, and only logs a peer that a filter
removed. Through a required relationship, it raises an error that `load_record` records.

The source adapters read through the loop that `DiffSyncMixin` owns in
`infrahub_sync/__init__.py`: an adapter lists its records (`list_records`) and converts each
one (`record_to_data`), and `load_record` admits it or records why it cannot.
`InfrahubAdapter` keeps its own `model_loader`, because it also fills the SDK store, and admits
each record through `load_record` too. The entries go
to the adapter's `load_report`, whose types are in `infrahub_sync/load_report.py`; the problem
pass of a plan reads them. `infrahub_sync/warm_read.py` holds the methods a warm read of the
source calls on the adapter (`list_changed_records`, `load_changed_records`), so that the
changed records go through `load_record` too.

Every bundled adapter has a matching entry in `BUILTIN_ADAPTER_CAPABILITIES`; the pair is what
makes a package using that adapter admissible. [Adapter anatomy](adapter-anatomy.md) explains
the contract, and [Adding an adapter](../guides/adding-an-adapter.md) is the procedure.

`infrahub_sync/infrahub_errors.py` sits outside the adapters package because the apply, the
destination schema read and the service worker's failure record all use it. It classifies an
Infrahub error as a refused token (HTTP 401) or a missing permission (HTTP 403, or a GraphQL
permission denial). It quotes the permission Infrahub names. When a 403 names no permission, it
quotes the server message instead, with control characters removed and its length capped at
300 characters. `classify_infrahub_error_chain`
walks the cause chain, because a plan wraps every load failure in a `ValueError`.
`rejected_write()` reads the error code, node kind, attribute and message of a write that
Infrahub rejected, for the `destination_rejected_write` failure record.

### Cache and incremental extraction

[`infrahub_sync/cache/`][src-infrahub-sync-cache] persists a run's snapshots and drives incremental extraction:
`cursors.py` (tiers and cursor state), `incremental.py`, `guardrails.py` (row-count
protection), `fingerprint.py`, `paths.py`, `locks.py`, `parquet_io.py` and `sidecars.py`.
[Incremental sync and cache](incremental-and-cache.md) is the full document, and
[Cache layout](../../reference/cache-layout.mdx) the on-disk reference.

### Generation, plugin loading and ordering

These three are development and internal machinery, not the registered route:

- `infrahub_sync/generator/` renders DiffSync adapter and model modules from
  `templates/diffsync_adapter.j2` and `templates/diffsync_models.j2`. Registered execution
  builds its models through `runtime_schema/` instead.
- `infrahub_sync/plugin_loader.py` resolves an adapter class from a built-in name, a dotted
  path, a filesystem path or an entry point. Filesystem targets are a development
  convenience; a registered package cannot declare one. See
  [Local adapters](../../adapters/local-adapters.mdx).
- [`infrahub_sync/dependency_graph.py`][src-infrahub-sync-dependency-graph-py] computes write-order tiers from a configuration's
  `schema_mapping`, which is why `order` can be omitted.

### The engine

[`infrahub_sync/potenda/`][src-infrahub-sync-potenda] is the Potenda engine: it drives load and diff for
planning, then applies verified saved-plan operations. It also owns the destination SDK exception
boundary. [`infrahub_sync/utils.py`][src-infrahub-sync-utils-py] assembles the pieces — configuration, plugin loading,
runtime models, cache paths and the engine — into a runnable instance.
For the service components and registered-run lifecycle, see
[Sync architecture](sync-architecture.md).

### Optional orchestration

`infrahub_sync/orchestration/` contains the direct flow (`flow.py`) and its serve entry point
(`serve.py`). The [direct flow][route-direct-flow] runs read-only plans through
[`run_remote_request`][route-remote-request], which refuses `sync` requests. Registered
writes use the service integration. See [Prefect orchestration](orchestration-prefect.md)
for the two integrations, their inputs and their result locations.

### The end-to-end test class

`infrahub_sync/testcontainers/` is the optional, experimental `testcontainers` extra, for
Python 3.11 or later. `helpers.py` holds `TestInfrahubSyncDocker`, a pytest base class that
starts Infrahub through `infrahub-testcontainers` and the Sync service from
`docker-compose.yml`, a byte-equal copy of the root deployment file shipped with the package.
Importing the module without the extra raises an `ImportError` that names the extra.

### Vendored extras

[`opsmill_prefect_extras/`][src-opsmill-prefect-extras] is a frozen, byte-identical copy of a private upstream package,
kept at its original import name so nothing rewrites imports. Its upstream unit tests are
copied under `tests/vendored_prefect_extras/`. Do not edit it; `opsmill_prefect_extras/VENDORED.md`
records the upstream commit and the local additions.

### Tasks

[`tasks/`][src-tasks] holds the Invoke definitions the workflow is built from:

| Module | Owns |
|---|---|
| `__init__.py` | The `format` and `lint` aggregates, the only top-level tasks |
| `linter.py`, `docs.py` | The individual lint and documentation legs |
| `tests.py` | `tests.tests-unit`, `tests.tests-integration`, `tests.all` and `tests.check-310` |
| `dev.py` | The local development stack: the `dev` namespace (`dev.start`, `dev.deps`, `dev.api`, `dev.worker`, `dev.restart`, `dev.smoke` and the rest) |
| `smoke_dataset.py` | The smoke dataset that `dev.seed` and `dev.smoke` load; no task of its own |
| `netbox.py` | The local NetBox for adapter work: the `netbox` namespace (`netbox.start`, `netbox.seed`, `netbox.destroy`, and dataset dump, restore and change tasks) |
| `bench.py` | The NetBox benchmark runner: the `bench` namespace. It reads the Infrahub port and admin token of its destination through `resolved_compose()` in `dev.py` |
| `release.py` | Keeping the image pin in `docker-compose.yml` in step with the package version |

[Quality gates](quality-gates.md) explains what the aggregates really run, and
[Testing tiers](../guidelines/testing-tiers.md) which test task to reach for.

### The development stack and the deployment file

- [`development/`][src-development] holds the local stacks. `docker-compose.yml` is the
  development stack the `dev` tasks run, formerly `docker-compose.dev.yml`; it includes the
  Infrahub services from `docker-compose.infrahub.yml`, with `docker-compose.infrahub-ports.yml`
  as the override file of that include, which changes only their host ports.
  PostgreSQL, the object store and `prefect-server` have no profile; `sync-bootstrap`,
  `sync-api` and `sync-worker` have the `sync` profile; every included Infrahub service has the
  `infrahub` profile. `dev.start` turns on both profiles and `dev.deps` only `infrahub`.
  With an Infrahub network attached (`--infrahub-network`), `dev.start` turns on only `sync` and
  `dev.deps` turns on no profile. The
  tasks read ports, the project name and the Infrahub admin token from
  `docker compose config --format json` (`dev_settings()` in `tasks/dev.py`), so a setting has
  one default, in the Compose file. ADR 0029 records why. Runtime state the
  tasks and tests write lives under `.dev/`, which Git ignores.
  [Local development stack](../../development-stack.mdx) is the procedure.
- The root `docker-compose.yml` is the operator deployment. It pulls the released image from
  `registry.opsmill.io/opsmill/infrahub-sync` and carries its own database bootstrap script;
  the development stack in `development/docker-compose.yml` builds from the checkout instead.
  `infrahub_sync/testcontainers/docker-compose.yml` is a copy of it that a contract test keeps
  byte-equal.
  With the default settings, every image other than the Sync image is named by its `sha256`
  digest in the root file and in `development/docker-compose.yml`. Two tests of
  `tests/compose/test_compose_contract.py`, one for each file, fail on an image named by tag
  alone; the byte-equal test covers the copy. The Sync image itself is pinned by tag in the
  root file (ADR 0016) and built from the checkout in the development stack.
  [Compose deployment](../../compose-deployment.mdx) is the operator page.
- [`examples/`][src-examples] holds fifteen directories, and they are not uniform. Four —
  `aci_to_infrahub`, `custom_adapter`, `netbox_to_infrahub` and
  `prometheus_to_infrahub (node_exporter)` — pair a `config.yml` with the `package.yml`
  envelope, and a product test holds each pair to its envelope. Ten carry a `config.yml`
  alone, with no registry envelope. One, `prefect_remote_run`, has neither: it is an
  orchestration fixture holding a schema and sample flow-run request bodies. Counts verified
  at this revision.

### Tests

[`tests/`][src-tests] mirrors the source tree: `adapters/`, `api/`, `cache/`, `cli/`, `client/`,
`configuration/`, `conformance/`, `plan/`, `product_store/`, `runtime_schema/`, `service/`,
`orchestration/`, `release/` and `testcontainers/`, plus `dev_stack/` for the development
stack's tasks, Compose file and smoke suite (`dev_stack/smoke/`, whose `smoke`-marked tests are
opt-in), the opt-in
`integration/`, `image/` and `compose/` suites, and the frozen `vendored_prefect_extras/` copy.

Two fixture directories serve the v1 and v2 conversion. `tests/data/v2_examples/` holds
byte-for-byte copies of the example `config.yml` files of the `2.0.2` tag, and of the NetBox
example of the `1.6.1` tag. Its README says not to edit them and how to copy them again from
their tag. `tests/data/v2_gate/` holds the reviewed lists that the nightly `v2-gate` suite
compares the first V3 plan with.

Which of these the default gate runs, and which need something live, is
[Testing tiers](../guidelines/testing-tiers.md).

### Historical evidence

[`dev/adr/`][src-dev-adr] holds the decision records, and [`dev/specs/archive/`][src-dev-specs-archive] the completed specifications
whose durable output became the pages under `docs/docs/development/`. Both explain why a boundary
exists. Neither is a current inventory: when an archived document and the code disagree, the
code is right and the page you are reading should be corrected.

### Related

- [Sync architecture](sync-architecture.md) — service components and the registered-run lifecycle.
- [Testing tiers](../guidelines/testing-tiers.md) — which suite covers which part of this tree.
- [Quality gates](quality-gates.md) — what `invoke lint` and `invoke format` run.
- [Decision records](../adr-index.mdx) — why the architecture is shaped this way.

[src-dev-adr]: https://github.com/opsmill/infrahub-sync/tree/61b6a1b9dccae637b522084f563858dfcd5e31a9/dev/adr
[src-dev-specs-archive]: https://github.com/opsmill/infrahub-sync/tree/61b6a1b9dccae637b522084f563858dfcd5e31a9/dev/specs/archive
[src-development]: https://github.com/opsmill/infrahub-sync/tree/61b6a1b9dccae637b522084f563858dfcd5e31a9/development
[src-examples]: https://github.com/opsmill/infrahub-sync/tree/61b6a1b9dccae637b522084f563858dfcd5e31a9/examples
[src-infrahub-sync-adapters]: https://github.com/opsmill/infrahub-sync/tree/61b6a1b9dccae637b522084f563858dfcd5e31a9/infrahub_sync/adapters
[src-infrahub-sync-cache]: https://github.com/opsmill/infrahub-sync/tree/61b6a1b9dccae637b522084f563858dfcd5e31a9/infrahub_sync/cache
[src-infrahub-sync-cli-py]: https://github.com/opsmill/infrahub-sync/blob/61b6a1b9dccae637b522084f563858dfcd5e31a9/infrahub_sync/cli.py
[src-infrahub-sync-client]: https://github.com/opsmill/infrahub-sync/tree/61b6a1b9dccae637b522084f563858dfcd5e31a9/infrahub_sync/client
[src-infrahub-sync-configuration]: https://github.com/opsmill/infrahub-sync/tree/61b6a1b9dccae637b522084f563858dfcd5e31a9/infrahub_sync/configuration
[src-infrahub-sync-dependency-graph-py]: https://github.com/opsmill/infrahub-sync/blob/61b6a1b9dccae637b522084f563858dfcd5e31a9/infrahub_sync/dependency_graph.py
[src-infrahub-sync-generator]: https://github.com/opsmill/infrahub-sync/tree/61b6a1b9dccae637b522084f563858dfcd5e31a9/infrahub_sync/generator
[src-infrahub-sync-plan]: https://github.com/opsmill/infrahub-sync/tree/61b6a1b9dccae637b522084f563858dfcd5e31a9/infrahub_sync/plan
[src-infrahub-sync-plugin-loader-py]: https://github.com/opsmill/infrahub-sync/blob/61b6a1b9dccae637b522084f563858dfcd5e31a9/infrahub_sync/plugin_loader.py
[src-infrahub-sync-potenda]: https://github.com/opsmill/infrahub-sync/tree/61b6a1b9dccae637b522084f563858dfcd5e31a9/infrahub_sync/potenda
[src-infrahub-sync-product-store]: https://github.com/opsmill/infrahub-sync/tree/61b6a1b9dccae637b522084f563858dfcd5e31a9/infrahub_sync/product_store
[src-infrahub-sync-runtime-schema]: https://github.com/opsmill/infrahub-sync/tree/61b6a1b9dccae637b522084f563858dfcd5e31a9/infrahub_sync/runtime_schema
[src-infrahub-sync-service]: https://github.com/opsmill/infrahub-sync/tree/61b6a1b9dccae637b522084f563858dfcd5e31a9/infrahub_sync/service
[src-infrahub-sync-utils-py]: https://github.com/opsmill/infrahub-sync/blob/61b6a1b9dccae637b522084f563858dfcd5e31a9/infrahub_sync/utils.py
[src-opsmill-prefect-extras]: https://github.com/opsmill/infrahub-sync/tree/61b6a1b9dccae637b522084f563858dfcd5e31a9/opsmill_prefect_extras
[src-revision]: https://github.com/opsmill/infrahub-sync/tree/61b6a1b9dccae637b522084f563858dfcd5e31a9
[src-tasks]: https://github.com/opsmill/infrahub-sync/tree/61b6a1b9dccae637b522084f563858dfcd5e31a9/tasks
[src-tests]: https://github.com/opsmill/infrahub-sync/tree/61b6a1b9dccae637b522084f563858dfcd5e31a9/tests

[route-revision]: https://github.com/opsmill/infrahub-sync/tree/f98a845986d1f03503d321ce5561b65a3946bf74
[route-cli]: https://github.com/opsmill/infrahub-sync/blob/f98a845986d1f03503d321ce5561b65a3946bf74/infrahub_sync/cli.py#L535-L659
[route-client]: https://github.com/opsmill/infrahub-sync/blob/f98a845986d1f03503d321ce5561b65a3946bf74/infrahub_sync/client/client.py#L258-L312
[route-api]: https://github.com/opsmill/infrahub-sync/blob/f98a845986d1f03503d321ce5561b65a3946bf74/infrahub_sync/service/app.py#L167-L211
[route-service]: https://github.com/opsmill/infrahub-sync/blob/f98a845986d1f03503d321ce5561b65a3946bf74/infrahub_sync/service/service.py#L606-L650
[route-orchestration]: https://github.com/opsmill/infrahub-sync/blob/f98a845986d1f03503d321ce5561b65a3946bf74/infrahub_sync/service/orchestration.py#L138-L147
[route-flow]: https://github.com/opsmill/infrahub-sync/blob/f98a845986d1f03503d321ce5561b65a3946bf74/infrahub_sync/service/flow.py#L805-L885
[route-execution]: https://github.com/opsmill/infrahub-sync/blob/f98a845986d1f03503d321ce5561b65a3946bf74/infrahub_sync/execution.py#L1073-L1149
[route-direct-flow]: https://github.com/opsmill/infrahub-sync/blob/f98a845986d1f03503d321ce5561b65a3946bf74/infrahub_sync/orchestration/flow.py#L110-L170
[route-remote-request]: https://github.com/opsmill/infrahub-sync/blob/f98a845986d1f03503d321ce5561b65a3946bf74/infrahub_sync/execution.py#L1304-L1325
