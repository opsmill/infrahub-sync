# Data Model: A second plan shows only real changes, and the plan step is measured by part

## Plan operation (changed)

`PlannedOperation` in `infrahub_sync/plan/models.py`. Only the rule for a one-peer reference
changes.

| Field | Change |
| --- | --- |
| `relationships[*]` (`RelationshipReference`) | For `cardinality: "one"`, `peers` holds 0 or 1 peer from plan format 5 on. An empty list means "the relationship becomes empty". |

Validation rules:

- An empty one-peer reference is allowed only on an `update` (FR-007).
- An empty one-peer reference is refused on a field that is part of the operation identity.
- Under a reader context with a format below 5, a one-peer reference holds exactly one peer, as
  today.
- An emptied field counts as a changed field of the update (`infrahub_sync/plan/last_changes.py:76-97`).

## Saved plan format (changed)

| Constant | Before | After |
| --- | --- | --- |
| `PLAN_FORMAT_VERSION` | 4 | 5 |
| `SUPPORTED_FORMAT_VERSIONS` | {2, 3, 4} | {2, 3, 4, 5} |
| Lowest format that apply accepts | 4 | 4 |

The writer writes format 5 for every new plan. A reader refuses any other version with a
message that names the version found and the supported versions.

## Nodes that Infrahub creates itself (new, in memory only)

Kept by the Infrahub destination adapter for the duration of one plan step. Not saved.

| Field | Type | Meaning |
| --- | --- | --- |
| `builtin_node_ids` | `frozenset[str]` | Destination node ids that Infrahub creates itself, among the nodes the read loaded. |

Exposed through an optional protocol in `infrahub_sync/plan/write_surface.py`. Filled from the
mark that the probe of R2 selects.

## Peers removed by a filter (new, in memory only)

Kept by a source adapter that uses the shared reference helper, for one plan step.

| Field | Type | Meaning |
| --- | --- | --- |
| `filtered_peer_fields` | `frozenset[tuple[str, str, str]]` | `(owner kind, owner source id, field)` for each optional peer that a configured filter removed. |

Derivation records no emptied relationship for these entries.

## Plan step document (new, stored)

Column `plan_step` (TEXT, nullable) on `prefect_executions`, one JSON document per execution.

```json
{
  "running": "comparing",
  "parts": {
    "comparing": {"started_at": "2026-11-16T10:00:12.104+00:00"},
    "preparing": {"started_at": "...", "ended_at": "..."}
  }
}
```

| Key | Type | Rule |
| --- | --- | --- |
| `running` | string or absent | One of the part names. Absent when no part runs. |
| `parts` | object | Keys among `preparing`, `comparing`, `checking`, `saving`. |
| `parts.<name>.started_at` | ISO 8601 with time zone | Required for a part that has an entry. |
| `parts.<name>.ended_at` | ISO 8601 with time zone, or absent | Absent while the part runs. |

- The store refuses any other key or value before the write (FR-021).
- The write is refused once the execution has a verdict (`terminal_at` set).
- `preparing.started_at` is `claimed_at`; `preparing.ended_at` is the earlier read
  `started_at`. Both are derived when the document is read; the worker writes no `preparing`
  entry. The `parts` key `preparing` is accepted so that a later release can store it.

State transitions of one part: absent → running (`started_at`, `running` set) → ended
(`ended_at` set, `running` cleared). On failure, cancellation or worker loss, a running part
gets a derived `stopped_at` when the document is read (research R13).

## Plan step timings (new, in the run response)

`OrchestrationSummary.plan_step: PlanStepTimings | None` in `infrahub_sync/client/models.py`.

`PlanStepTimings`:

| Field | Type | Meaning |
| --- | --- | --- |
| `running` | `str \| None` | The part that runs, or `None`. |
| `parts` | `dict[str, PlanStepPart]` | One entry per part that started. A part that did not start has no entry. |

`PlanStepPart`:

| Field | Type | Meaning |
| --- | --- | --- |
| `started_at` | `datetime` | Start of the part. |
| `ended_at` | `datetime \| None` | End of the part, when it ended. |
| `stopped_at` | `datetime \| None` | For the part that was running when the step failed, was cancelled or was interrupted: the time of the verdict. |

Part names in the response: `waiting`, `preparing`, `comparing`, `checking`, `saving`. The two
reads stay in `OrchestrationSummary.reads` (FR-015). Names are plain strings, so a later part
does not fail an older client.

`plan_step` is `None` for an execution with no stored document, including every execution
created before this release (FR-019). `waiting` is returned only when `claimed_at` is not
before `submitted_at`.

## Benchmark record (changed)

`ResultRecord` in `development/bench/records.py`.

| Field | Change |
| --- | --- |
| `plan_parts` | New. `dict[str, float]`: seconds per part, for the parts that have both times; the two reads are included as `source_read` and `destination_read`. |
| `source_read_mode`, `destination_read_mode` | New. `str \| None`. |
| `plan_bytes` | New. `int \| None`: size of the public plan review artifact. |
| `operations_digest` | New. `str \| None`: R16. |
| `left_out`, `kept`, `problems` | New. `int \| None`: counts of the plan report. |
| `builtin_deletes` | Removed. |

New fields are cleared for a result that is not `ok`, as the existing times are.

## Operations digest (new)

`infrahub_sync/plan/digest.py::operations_digest(operations) -> str`: SHA-256, in hexadecimal,
of the sorted canonical JSON lines of `{action, kind, identity, payload, relationships}` of each
operation. `destination_id` is left out. Equal for two plans that hold the same operations.
