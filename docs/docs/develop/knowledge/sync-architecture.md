---
title: "Sync architecture"
---

## Sync architecture

Infrahub Sync compares data from a source system with a destination and records the
proposed changes in a saved plan. For example, you can review a plan from NetBox to
Infrahub before approving destination writes.

In the registered service workflow, the CLI submits work to the Sync API and a worker
executes it through Prefect. To investigate a queued run or find its retained plan, first
distinguish the processes that execute work from the stores that retain its records.

### Service components

The Compose deployment runs five long-running services on one host:

| Component | Responsibility |
| --- | --- |
| Sync API | Accepts configuration packages and run requests; returns product records, plans and results. |
| Prefect server | Schedules flow runs and records their execution state and logs. |
| Sync worker | Polls a Prefect work pool and starts a process to execute each service flow run. Its adapters read the source and read or write the destination. |
| PostgreSQL | Stores Sync product records and Prefect execution records in separate databases. |
| S3-compatible object store | Stores immutable artifacts and internal plan checkpoints. The Compose bundle uses MinIO. |

```text
CLI / Python client ──requests──> Sync API ──submits──> Prefect server
                                     │                    │
                              product records       execution records
                                     │                    │
                                     v                    v
                                 PostgreSQL: separate databases

Sync worker ──polls──> Prefect server
     │
     ├──executes service flow──> source / destination adapters
     ├──reads and writes───────> Sync product records in PostgreSQL
     └──reads and writes───────> S3-compatible object store
                                ^
                                │ reads artifacts
                             Sync API
```

The CLI accesses the Sync API; it does not run adapters or read the worker's filesystem.
Bootstrap jobs create the databases, artifact bucket, work pool and service deployment
before the API and worker start. Configuration packages are registered separately.
See [Compose deployment](../../compose-deployment.mdx) for the operating procedure.

### One registered run

A **product run** is the durable Sync record identified by `run_id`. A **Prefect flow run**
is one execution associated with it; planning, verification and apply can have separate
Prefect execution IDs for the same product run.

1. **Submit.** The API reads the selected registered configuration version and binds the
   product run to its configuration ID, version and package checksum. It reserves the run
   before submitting the requested stage to Prefect, then stores the Prefect execution link.
2. **Execute.** The worker claims that execution, reads the registered package and resolves
   its credential references. For planning, it reads the destination schema and builds
   runtime models before loading the source and destination data.
3. **Save the plan.** The engine compares the two datasets and records the proposed
   operations. The worker publishes an internal plan checkpoint and then the review
   artifact available through the API, so a reviewable plan also has the retained data needed for a later apply.
4. **Review and apply.** You review the plan through the Sync API and approve its checksum.
   The apply stage retrieves the checkpoint, verifies its binding and checksum, and checks
   the destination schema before writing. It uses the saved operations rather than
   extracting the source again.
5. **Record the result.** The worker saves final evidence and updates the product run.
   The API returns the retained result alongside available Prefect execution information.

Verification can also run as a separate stage; it checks the retained plan without
constructing adapters. A confirmed `sync` combines planning, verification and apply in one
service execution, publishing the plan before the first destination write.
Both write paths hold a PostgreSQL advisory lock for the configuration while they work.
This serializes writes for that configuration; it is not a lock on every destination
object that other configurations or external tools might change.

For command examples, follow the [reviewed-run procedure](../../compose-deployment.mdx#the-operator-sequence).
For the stage entrypoints, see [Prefect orchestration](orchestration-prefect.md#registered-service-integration).

### Persistence and deployment limits

Sync retains configuration versions, product runs and execution links in PostgreSQL.
It retains artifacts and internal checkpoints in object storage. Each worker stage creates
its own temporary directory and removes it when the stage ends; a later stage retrieves
its plan from object storage, not from a shared cache directory.

Replacing an API or worker process preserves the database and object-store volumes.
Retained records do not guarantee that an interrupted write resumes. If the outcome of a write
is uncertain, inspect its evidence and the destination before creating a fresh plan;
follow the [uncertain-write procedure](../../compose-deployment.mdx#when-the-outcome-of-a-write-is-uncertain).
Follow the [stop, restart and reset procedure](../../compose-deployment.mdx#stop-restart-and-reset)
to choose between replacing processes and deleting durable state.

The qualified deployment is one Linux amd64 host. Process workers and separate storage
services do not establish support for multi-host deployment or high availability.
There is no verified backup, restore or in-place migration procedure.
See [supported platforms and limits](../../operations/supported-platforms-and-limits.mdx)
for the qualification boundary, and [durable product records](../../reference/durable-product-records.mdx)
for storage settings and retained data.

### The foundation classes

[DiffSync](https://github.com/networktocode/diffsync) represents each object as a model
instance with a stable identity:

- **Adapter** — loads objects from one system and implements its supported destination operations.
- **Model** — a typed record with `_identifiers` (the natural key) and `_attributes`
  (the comparable fields). DiffSync matches objects by identifiers and compares their attributes.

`DiffSyncMixin` and `DiffSyncModelMixin` extend these base classes for Sync.
See [adapter anatomy](adapter-anatomy.md) for their contract.

### Source and destination

A configuration names one source and one destination. The direction is fixed for a run:
the source is read-only, and writes target the destination. An adapter can serve either
role only if it implements that role's operations.

The worker resolves registered adapters from installed code and checks their declared
capabilities. See [configuration foundation](configuration-foundation.md) for package
validation and credential references, and [schema mapping](schema-mapping.md) for resource
and field comparisons.

### The sync engine

`Potenda`, in `infrahub_sync/potenda/`, loads adapter data, computes the DiffSync comparison
and writes saved plans. The managed service applies those plans through the destination's
planned-write methods. Deletes are recorded for review but are not executed.
See [planned writes and apply](planned-write-and-apply.md) for the write operations and
relationship handling.

### The code-generation path

Registered runs build DiffSync models in memory from the destination schema through
`infrahub_sync/runtime_schema/`. They do not depend on generated Python files.

For adapter development and the direct configuration-directory path,
`infrahub_sync/generator/` renders models from Jinja2 templates and `plugin_loader.py`
resolves adapter classes. Filesystem adapter paths belong to that development route;
a registered package cannot declare one. See [local adapters](../../adapters/local-adapters.mdx).

### See also

- [Repository tour](repository-tour.md) — locate the module responsible for each stage or record.
- [Prefect orchestration](orchestration-prefect.md) — distinguish service execution from the direct Prefect integration.
- [The saved plan artifact](plan-artifact.md) — inspect the plan's manifest and operations.
- [Adding an adapter](../guides/adding-an-adapter.md) — implement and test a connector.
