# Data model: Prefect run observability and the missing Sync API address

No stored record changes. Two response fields and two in-process values are added.

## OrchestrationSummary (response model, `infrahub_sync/client/models.py`)

| Field | Type | Change | Rule |
| --- | --- | --- | --- |
| `state` | `str \| None` | rule changed | The recorded `terminal_state` when the entry has one. Otherwise the live observation when available, else the retained observation (FR-019). |
| `prefect_flow_run_url` | `str \| None` | added, default `None` | `<INFRAHUB_SYNC_PREFECT_UI_URL without trailing slash>/runs/flow-run/<flow_run_id>` when the setting is set, else `None` (FR-011). |

Clients from before this change ignore the new field. This client reads responses without the field as `None`.

## RecordedFailure (in process, `infrahub_sync/service/flow.py`)

| Field | Type | Source |
| --- | --- | --- |
| `outcome` | `str` | The terminal outcome that `_record_failure` stored: `failed` or `ambiguous`. |
| `next_action` | `str \| None` | `evidence["next_action"]` from `operator_cause`, already redacted. |

`_record_failure` returns `None` when it stored nothing: no stored run, or a persistence error.

## Run summary artifact (Prefect, one per stage)

| Element | Value |
| --- | --- |
| key | `sync-run-<run_id>-<stage>`, lowercased. Every character other than `a-z`, `0-9` and `-` becomes `-`, and repeated dashes collapse. |
| description | `Sync run <run_id>, stage <stage>` |
| markdown | The heading `# Sync run <run_id>`, then the lines `Stage`, `Outcome`, `Configuration` (`<config_id>@<registry_version>`), `Destination branch` when set, `Operations by action` and `Operations by kind` when present, then `## Next steps`. |

The markdown is built from `ProductRun` fields and fixed templates only, then passed through `redact`.

## Client-input error (CLI output)

| Field | Order | Present when |
| --- | --- | --- |
| `error: client-input` | 1 | Always, as today. |
| `argument: <name>` | 2 | Always, as today. |
| `hint: <text>` | 3 | `argument` is `service_url` or `token`. |
