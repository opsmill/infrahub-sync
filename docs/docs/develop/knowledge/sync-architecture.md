---
title: "Sync architecture"
---

## Sync architecture

Infrahub Sync compares data from a source system with data from a destination and records the
proposed changes in a saved plan. For example, you can review a plan from NetBox to
Infrahub before approving destination writes.

In the registered service workflow, the CLI submits work to the Sync API and a worker
executes it on Infrahub's task manager, which is Infrahub's Prefect server.

### Service components

The Compose deployment runs three long-running services on one host, next to an Infrahub
deployment whose task manager and PostgreSQL server it uses:

| Component | Responsibility |
| --- | --- |
| Sync API | Accepts run requests and reads configurations from Infrahub; returns product records, plans and results. |
| Sync worker | Polls Sync's own work pool on Infrahub's task manager and starts a process to execute each service flow run. Its adapters read the source and read or write the destination. |
| S3-compatible object store | Stores immutable artifacts and internal plan checkpoints. The Compose bundle uses MinIO. |
| Infrahub | Holds Sync's configurations, which operators create, and the versions, run copies and approvals that the Sync service account writes, as `SyncConfiguration`, `SyncConfigurationVersion`, `SyncRun` and `SyncApproval` nodes. |
| Infrahub task manager (Infrahub's) | Infrahub's Prefect server. Schedules Sync's flow runs and records their execution state and logs, next to Infrahub's own tasks. |
| Task manager PostgreSQL (Infrahub's) | Holds Sync's own `infrahub_sync` database, with its product records and advisory locks, separate from the task manager's `prefect` database. |

```text
CLI / Python client ──requests──> Sync API ──submits──> Infrahub task manager
                                     │                    │
                              product records       execution records
                                     │                    │
                                     v                    v
                 task manager PostgreSQL: `infrahub_sync` next to `prefect`

Sync worker ──polls──> Infrahub task manager
     │
     ├──executes service flow──> source / destination adapters
     ├──reads and writes───────> Sync product records in `infrahub_sync`
     └──reads and writes───────> S3-compatible object store
                                ^
                                │ reads artifacts
                             Sync API
```

The CLI accesses the Sync API; it does not run adapters or read the worker's filesystem.
Bootstrap jobs create Sync's database on the task manager's PostgreSQL server, the artifact
bucket, the work pool and the service deployment before the API and worker start. Configurations are created in Infrahub.
See [Compose deployment](../../compose-deployment.mdx) for the operating procedure.

### One registered run

A **product run** is the durable Sync record identified by `run_id`. A **Prefect flow run**
is one execution associated with it; planning, verification and apply can have separate
Prefect execution IDs for the same product run.

1. **Submit.** The API binds the product run to a configuration ID, version and package
   checksum. When the request names a version, the API reads that recorded version. When it
   names none, the API reads the configuration's current document on Infrahub's default
   branch, validates it, and reuses the `SyncConfigurationVersion` that holds that content or
   records it as the next one. A document that is not a YAML or JSON mapping is refused with
   `configuration-document-invalid`, and content with errors with `configuration-invalid`;
   neither refusal records a version. The API reserves the run before submitting the
   requested stage to Prefect, then stores the Prefect execution link.
2. **Execute.** The worker claims that execution, reads the registered package and resolves
   its credential references. For planning, it reads the destination schema and builds
   runtime models before loading the source and destination data.
3. **Save the plan.** The engine compares the two datasets and records the proposed
   operations. The worker publishes an internal plan checkpoint and then the review
   artifact available through the API, so a reviewable plan also has the retained data needed for a later apply.
4. **Review and apply.** You review the plan through the Sync API and approve its checksum.
   When the API accepts the apply, it records a `SyncApproval` node that names the approver
   and the approved checksum. The apply stage retrieves the checkpoint, verifies its binding
   and checksum, and checks the destination schema before writing. It uses the saved
   operations rather than extracting the source again.
5. **Record the result.** The worker saves final evidence and updates the product run, then
   copies the run to its `SyncRun` node in Infrahub. The API also writes that node when it
   accepts a run, an apply or a cancellation, and when it records a change in the run's
   state. These copies are best effort: a failed write never fails the run, because Sync's
   database holds it. The API returns the retained result alongside available Prefect
   execution information.

Verification can also run as a separate stage; it checks the retained plan without
constructing adapters. A confirmed `sync` combines planning, verification and apply in one
service execution, publishing the plan before the first destination write.
Both `sync` and saved-plan `apply` hold a PostgreSQL advisory lock for the configuration
while they work. For `sync`, the lock covers planning, verification and apply. This serializes
writes for that configuration; it is not a lock on every destination object that other
configurations or external tools might change.

For command examples, follow the [reviewed-run procedure](../../compose-deployment.mdx#the-operator-sequence).
For the stage entrypoints, see [Prefect orchestration](orchestration-prefect.md#registered-service-integration).

### Persistence and deployment limits

Configurations and their versions live in Infrahub, while product runs, execution links and
advisory locks stay in Sync's `infrahub_sync` database on the task manager's PostgreSQL server
and artifacts stay in object storage. Sync retains internal checkpoints in that object storage
too. Each worker stage creates its own temporary directory and
removes it when the stage ends; a later stage retrieves its plan from object storage, not from
a shared cache directory.

Replacing an API or worker process preserves Sync's database, which lives with Infrahub's task
manager, and the object-store volume.
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
and writes saved plans. The service applies those plans through the destination's
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
- [Incremental extraction and cache](incremental-and-cache.md) — understand retained extraction data and cache behavior.
- [The saved plan artifact](plan-artifact.md) — inspect the plan's manifest and operations.
- [Adding an adapter](../guides/adding-an-adapter.md) — implement and test a connector.
