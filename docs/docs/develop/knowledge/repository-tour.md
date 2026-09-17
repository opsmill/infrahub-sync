---
title: "Repository tour"
---

## Repository tour

> Part of: Develop > Knowledge | Related: [Sync architecture](sync-architecture.md), [The shared execution surface](execution-surface.md), [Testing tiers](../guidelines/testing-tiers.md)

**Verified 2026-09-16 against source revision `61b6a1b`.** Every path and ownership claim
below comes from reading the tree at that revision, not from a live run. Where a module's
behavior is explained in depth elsewhere, this page links that page rather than restating
it.

Where each concern lives, so you can find the right module before changing anything. This is a
map, not a specification: it says what owns what and where the deeper document is.

Read this alongside [Sync architecture](sync-architecture.md), which explains how one run
moves through the engine, and [Testing tiers](../guidelines/testing-tiers.md), which explains
which suite covers which part of the tree.

### The one boundary to hold on to

Three things in this repository look interchangeable and are not:

| Route | What it is | Where it lives |
|---|---|---|
| Registered V3 execution | The supported route. A configuration package is registered with the Sync API, validated, planned, reviewed and applied by a service worker. | `infrahub_sync/service/`, `infrahub_sync/product_store/`, `infrahub_sync/configuration/` |
| Internal generation and local plugins | Development material. Rendered DiffSync modules and filesystem adapter loading, used while building an adapter. | `infrahub_sync/generator/`, `infrahub_sync/plugin_loader.py` |
| Direct Prefect execution | An optional integration that runs a flow without the Sync API. Not the operator route. | `infrahub_sync/orchestration/` |

A fourth category is historical evidence — archived specifications and decision records under
`dev/`. It documents why the code is shaped this way; it is not a description of current
behavior.

### The command-line client and its HTTP client

- `infrahub_sync/cli.py` — the Typer entry point. It is a client of the Sync HTTP API and
  nothing else: it constructs no HTTP request itself and reads neither the source system nor a
  local plan file.
- `infrahub_sync/client/` — the only place that builds Sync API requests.
  `client.py` holds the synchronous client, `models.py` its typed request and response shapes,
  and `errors.py` its error taxonomy.

The command surface is `configs`, `runs`, `diff`, `sync` and `apply`. See the
[CLI reference](../../reference/cli.mdx) for every option.

### Configuration admission

`infrahub_sync/configuration/` decides what a registered package is allowed to declare. It
performs no I/O against a source or destination unless a caller explicitly opts in to the
destination-schema checks.

| Module | Owns |
|---|---|
| `models.py` | The package envelope, its parser, and the finding type |
| `capabilities.py` | `AdapterConfigurationCapabilities` and the closed `BUILTIN_ADAPTER_CAPABILITIES` registry |
| `validation.py` | The finding-producing checks and their fixed execution order |
| `credentials.py` | What a credential reference is, and how a provider resolves one |
| `schema_validation.py` | The opt-in destination-schema checks |
| `warnings.py` | Intentional omissions and unqualified optional features |
| `runtime.py` | Runtime resolution, including the effective destination branch |

[Configuration foundation](configuration-foundation.md) explains the declared identity,
credential references and the connection-free capability declaration in full.

### The Sync service and its worker

`infrahub_sync/service/` is the optional service extra. It holds the FastAPI application
(`app.py`, `serve.py`, `config_routes.py`), authentication (`auth.py`), the Prefect worker and
deployment (`worker.py`, `deploy.py`, `orchestration.py`, `flow.py`), liveness and checkpoint
policy (`liveness.py`, `checkpoints.py`), per-stage scratch directories (`scratch.py`), the
write guard (`apply_guard.py`) and artifact storage (`storage.py`).

Two facts about it are often missed:

- Each stage creates its own private scratch directory. The service reads no shared cache
  location.
- It resolves its flow as an installed module, so it declares no working directory and needs
  no source tree. That is why the development stack starts the worker from an empty directory
  — see [the local development stack](../../development-stack.mdx).

[The configuration write guard](apply-guard.md) covers the advisory lock that serializes one
configuration's writes.

### Runtime schema

`infrahub_sync/runtime_schema/` discovers the destination schema at run time and builds
DiffSync models in memory from it, rather than from committed generated code. `domain.py`
holds the domain types, `projection.py` the projection onto DiffSync models, `worker.py` the
out-of-process discovery path, and `errors.py` its failures.

### One run: the shared execution surface

`infrahub_sync/execution.py` is the typed Python entry point to a single run, used by the CLI
path, the service worker stages and the packaged Prefect flow. It imports no Prefect symbol,
so it stays importable in a base install. [The shared execution surface](execution-surface.md)
is the full document.

### Plans

`infrahub_sync/plan/` owns the saved plan artifact and everything that reads or writes it:

| Module | Owns |
|---|---|
| `models.py`, `canonical.py`, `checksum.py` | The artifact shape, its canonical encoding, and the checksum |
| `writer.py`, `reader.py` | Writing and reading the artifact |
| `derive.py`, `identity.py`, `keying.py`, `ownership.py` | Deriving operations, their identifiers, keyedness and ownership |
| `review.py` | The review projection the CLI renders |
| `verify.py`, `errors.py` | Pre-write verification and the error taxonomy |
| `write_surface.py` | `PlannedWriteDestination`, the destination write surface an apply goes through |
| `config_version.py`, `destination_only_peer.py` | Version binding and destination-only peers |

[The saved plan artifact](plan-artifact.md) and [Planned writes and apply](planned-write-and-apply.md)
are the deep documents.

### Product storage

`infrahub_sync/product_store/` is the durable record of configurations, runs and artifacts.
`configs.py` is the configuration service boundary — register, version, list, show, validate —
`store.py` the durable projection, `models.py` the record types and `bundle.py` the artifact
bundle. [Durable product records](../../reference/durable-product-records.mdx) documents what
is kept.

### Adapters

`infrahub_sync/adapters/` holds the nine bundled connectors: `aci`, `genericrestapi`,
`infrahub`, `ipfabricsync`, `nautobot`, `netbox`, `peeringmanager`, `prometheus` and
`slurpitsync`, plus the shared `rest_api_client.py` and `utils.py`.

Every bundled adapter has a matching entry in `BUILTIN_ADAPTER_CAPABILITIES`; the pair is what
makes a package using that adapter admissible. [Adapter anatomy](adapter-anatomy.md) explains
the contract, and [Adding an adapter](../guides/adding-an-adapter.md) is the procedure.

### Cache and incremental extraction

`infrahub_sync/cache/` persists a run's snapshots and drives incremental extraction:
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
- `infrahub_sync/dependency_graph.py` computes write-order tiers from a configuration's
  `schema_mapping`, which is why `order` can be omitted.

### The engine

`infrahub_sync/potenda/` is the Potenda engine: it drives load, diff and write for both the
live compare-and-write path and the apply path, and owns the destination SDK exception
boundary. `infrahub_sync/utils.py` assembles the pieces — configuration, plugin loading,
runtime models, cache paths and the engine — into a runnable instance.
[Sync architecture](sync-architecture.md) walks one run end to end.

### Optional orchestration

`infrahub_sync/orchestration/` holds the packaged flow (`flow.py`) and its serve entry point
(`serve.py`). It is the direct-Prefect integration, separate from the service's own Prefect
usage, and it is not the supported operator route.
[Prefect orchestration](orchestration-prefect.md) covers the import boundary and the traps.

### Vendored extras

`opsmill_prefect_extras/` is a frozen, byte-identical copy of a private upstream package,
kept at its original import name so nothing rewrites imports. Its upstream unit tests are
copied under `tests/vendored_prefect_extras/`. Do not edit it; `opsmill_prefect_extras/VENDORED.md`
records the upstream commit and the local additions.

### Tasks

`tasks/` holds the Invoke definitions the workflow is built from:

| Module | Owns |
|---|---|
| `__init__.py` | The `format`, `lint`, `tests-*` aggregates and `check-310` |
| `linter.py`, `docs.py` | The individual lint and documentation legs |
| `tests.py` | `tests.tests-unit` and `tests.tests-integration` |
| `preview.py` | The local development stack |
| `image.py`, `compose.py`, `release.py` | Image build and smoke, Compose lifecycle, release gates |

[Quality gates](quality-gates.md) explains what the aggregates really run, and
[Testing tiers](../guidelines/testing-tiers.md) which test task to reach for.

### The development stack and the deployment bundle

- `development/` holds the local stack: the compose files, the shipped `preview.env` defaults
  and `preview.local.env`, which Git ignores. Runtime state lives under `.preview/`.
  [Local development stack](../../development-stack.mdx) is the procedure.
- `deploy/compose/` is the shipped deployment bundle — `compose.yaml`, the
  `infrahub-sync-compose` entry point, `configuration/`, `bootstrap/` and `OPERATING.md`.
  [Compose deployment](../../compose-deployment.mdx) is the operator page.
- `examples/` holds the example configuration packages. Each directory pairs a `config.yml`
  with the `package.yml` envelope that registers it.

### Tests

`tests/` mirrors the source tree: `adapters/`, `api/`, `cache/`, `cli/`, `client/`,
`configuration/`, `conformance/`, `plan/`, `product_store/`, `runtime_schema/`, `service/`,
`orchestration/` and `release/`, plus the opt-in `integration/`, `preview/`, `image/` and
`compose/` suites and the frozen `vendored_prefect_extras/` copy.

Which of these the default gate runs, and which need something live, is
[Testing tiers](../guidelines/testing-tiers.md).

### Historical evidence

`dev/adr/` holds the decision records, and `dev/specs/archive/` the completed specifications
whose durable output became the pages under `docs/docs/develop/`. Both explain why a boundary
exists. Neither is a current inventory: when an archived document and the code disagree, the
code is right and the page you are reading should be corrected.

### Related

- [Sync architecture](sync-architecture.md) — how one run moves through the engine.
- [Testing tiers](../guidelines/testing-tiers.md) — which suite covers which part of this tree.
- [Quality gates](quality-gates.md) — what `invoke lint` and `invoke format` run.
- [Decision records](../adr-index.mdx) — why the architecture is shaped this way.
