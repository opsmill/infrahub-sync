# Data model: Infrahub platform alignment

Two stores remain. Infrahub holds Sync's records as nodes of the `Sync` schema extension, always
on the default branch. The `infrahub_sync` database on Infrahub's task-manager-db server holds
write safety only. See [research.md](research.md) R5 to R9.

## Infrahub nodes (schema extension, namespace `Sync`)

### SyncConfiguration

Inherits `CoreTaskTarget`. One sync between one source and one destination.

| Field | Kind | Rules |
|---|---|---|
| name | Text | Unique; human-friendly ID; `^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$`. It is the configuration's identity for Sync: the API path, the write lock and the version lock are keyed on it. Renaming a configuration between a plan and its apply is not handled by this spec |
| description | Text | Optional |
| document | TextArea | The whole package (source, destination, mappings, order, credential references), YAML or JSON. Never holds a credential value (FR-017) |
| source_row_counts | JSON | Baseline written after a successful plan; read-only for users |
| runs_since_full_extract | Number | Baseline; read-only for users |
| versions | → SyncConfigurationVersion | Many; peer side `configuration` |
| runs | → SyncRun | Many; peer side `configuration` |

Edited by users through Infrahub branches and proposed changes. Sync never writes `document`.

### SyncConfigurationVersion

An immutable copy of a configuration's document, created at run start (FR-006).

| Field | Kind | Rules |
|---|---|---|
| number | Number | 1, 2, 3 … per configuration |
| checksum | Text | SHA-256 of the canonical declared content |
| document | TextArea | Copy of `SyncConfiguration.document` at creation |
| created_at | DateTime | When the run that first used this content recorded it |
| configuration | → SyncConfiguration | Required |
| created_by_run | → SyncRun | The run that created it |

Uniqueness: (configuration, number) and (configuration, checksum). Never updated after creation.

### SyncRun

Inherits `CoreTaskTarget`. A mirror of one plan, verify, apply or sync execution, whose
authoritative state stays in `product_runs` (research R8). Written best-effort after each state
change; rebuilt from `product_runs` when a write was missed.

| Field | Kind | Rules |
|---|---|---|
| run_id | Text | Unique; the id the Sync API returns |
| operation | Dropdown | `plan`, `verify`, `apply`, `sync` |
| state | Dropdown | See state transitions |
| outcome | Dropdown | `succeeded`, `failed`, `ambiguous`, `refused`; empty while running |
| target_branch | Text | Destination branch of the sync |
| plan_checksum | Text | SHA-256 plan checksum; set after plan |
| summary | JSON | Counts per kind and action |
| requested_by | Text | Infrahub account name of the caller (FR-021) |
| reason | Text | Audit reason given with the request |
| flow_run_id | Text | Prefect flow run id |
| started_at, finished_at | DateTime | |
| configuration | → SyncConfiguration | Required |
| version | → SyncConfigurationVersion | Required once started; never changes (FR-007) |
| plan_run | → SyncRun | For `apply`: the plan run it applies |
| plan_files | → SyncPlanFile | Many |
| bundles | → SyncInternalBundle | Many |
| approvals | → SyncApproval (many) | One per apply of the run's plan; a composed `sync` records none |

State transitions:

```text
queued ──► running ──► finished (outcome: succeeded | failed | ambiguous)
   │           │
   └──► refused (invalid content, permission, checksum mismatch)
running ──► cancelled
```

A run in `finished` or `refused` never changes again, apart from retention deletion.

### SyncApproval

The record that a user approved a write. Creating one is the apply right (research R6).

| Field | Kind | Rules |
|---|---|---|
| approved_checksum | Text | Must equal the plan run's `plan_checksum` |
| approved_by | Text | Infrahub account name |
| reason | Text | |
| run | → SyncRun | The apply or sync run |

### SyncPlanFile

Inherits `CoreFileObject` (`file_name`, `checksum` SHA-1, `file_size`, `file_type`, `storage_id`).
Public review artifact of a run (`saved-plan-review`). Readable by anyone with `view` on the kind.

| Field | Kind | Rules |
|---|---|---|
| role | Dropdown | `review`, `results` |
| sha256 | Text | Sync's own digest, checked on read |
| run | → SyncRun | Required |

### SyncInternalBundle

Inherits `CoreFileObject`. Private plan and final checkpoint bundles (ADR 0011: byte-stable, not
redacted). `view` is granted to the Sync service account only.

| Field | Kind | Rules |
|---|---|---|
| role | Dropdown | `plan-checkpoint`, `final-checkpoint` |
| sha256 | Text | Checked before rehydration; mismatch refuses the apply (FR-011) |
| run | → SyncRun | Required |

## Lock database `infrahub_sync` (task-manager-db server)

Kept from `product_store/store.py:48-110`, and still the authority for run state (research R8).

| Table | Purpose | Change |
|---|---|---|
| product_runs | Run state, phase, outcome, summary, results | Kept; `config_id` is the configuration name, `registry_version` the version number |
| mutation_receipts | Idempotency: one result per (actor, key digest) | `actor` becomes the Infrahub account name |
| write_admissions | Reserved write per configuration and run | Kept |
| prefect_executions | Worker claim, stall and cancellation bookkeeping | Kept |
| configuration_baselines, audit_events | Incremental baseline; audit trail | Kept |
| artifact_refs | Plan files and bundles | Kept until US3 |
| (advisory locks) | Write lock per configuration (ADR 0010); version-creation lock (R9) | Two key spaces |

Removed: `configurations`, `configuration_versions` (moved to Infrahub).

## Validation rules carried from the spec

- A run refuses to start on a document that fails validation, and no version is created (FR-009).
- An apply refuses when the approval checksum differs from the plan run's checksum, or when a
  bundle's `sha256` differs from the stored file (FR-011).
- A version's `document` and `checksum` never change after creation (FR-006).
- No node stores a credential value (FR-017).
