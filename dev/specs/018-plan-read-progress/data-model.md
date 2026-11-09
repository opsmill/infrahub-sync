# Data model: Read state for each side of a Plan

## Stored: two columns on `prefect_executions`

| Column | Type | Change | Rule |
| --- | --- | --- | --- |
| `source_read` | `TEXT`, nullable | added | The source side's JSON document, or `NULL`. |
| `destination_read` | `TEXT`, nullable | added | The destination side's JSON document, or `NULL`. |

Rows created before this feature have `NULL` in both columns. The columns are added by
`_migrate_prefect_execution_columns`, on SQLite and PostgreSQL. A write is refused when `terminal_at` is set.

### Side document (stored JSON)

| Field | Type | Present when |
| --- | --- | --- |
| `state` | `"waiting"`, `"reading"`, `"succeeded"` or `"failed"` | Always. |
| `started_at` | ISO 8601 UTC time | From `reading` on. |
| `ended_at` | ISO 8601 UTC time | `succeeded` or `failed`. |
| `read_mode` | `"full"` or `"incremental"` | `succeeded`. |
| `nodes_by_kind` | object, kind name to non-negative integer, sorted by name | `succeeded`. |
| `error_class` | string, a Python class name | `failed`. |

No other field is stored. No field holds an exception message, an address, a secret or configuration content.

### State transitions (written by the worker)

```text
waiting ──read_started──▶ reading ──read_ended(ok)────▶ succeeded
                                  └─read_ended(error)─▶ failed
```

A stage stopped before the reads leaves `waiting`. A killed worker leaves `reading`.

## In process: `PrefectExecutionLink` (`infrahub_sync/product_store/models.py`)

| Field | Type | Change |
| --- | --- | --- |
| `source_read` | `dict[str, Any] \| None` | added, default `None` |
| `destination_read` | `dict[str, Any] \| None` | added, default `None` |

## In process: `SideReadReport` (`infrahub_sync/potenda/read_progress.py`)

| Field | Type | Rule |
| --- | --- | --- |
| `succeeded` | `bool` | |
| `read_mode` | `"full" \| "incremental" \| None` | Set when `succeeded`. |
| `nodes_by_kind` | `Mapping[str, int]` | Empty when the read failed. |
| `error_class` | `str \| None` | Set when the read failed. |

`side` is `"source"` or `"destination"`.

## Response: `OrchestrationSummary` (`infrahub_sync/client/models.py`)

| Field | Type | Change |
| --- | --- | --- |
| `reads` | `StageReads \| None` | added, default `None` |

### `StageReads`

| Field | Type |
| --- | --- |
| `source` | `SideRead \| None` |
| `destination` | `SideRead \| None` |

### `SideRead`

| Field | Type | Rule |
| --- | --- | --- |
| `state` | `"waiting"`, `"reading"`, `"succeeded"`, `"failed"`, `"not_started"` or `"interrupted"` | The stored state, except that a terminal execution returns `waiting` as `not_started` and `reading` as `interrupted`. |
| `started_at` | `datetime \| None` | As stored. |
| `ended_at` | `datetime \| None` | As stored. Always `None` for `not_started` and `interrupted`. |
| `read_mode` | `"full" \| "incremental" \| None` | As stored. |
| `nodes_by_kind` | `dict[str, int]` | As stored; empty when absent. |
| `error_class` | `str \| None` | As stored. |

Clients from before this feature ignore `reads`. This client reads a response without it as `None`.
