# Data model: Sync run events and logs

## Event (stored; extends `AuditEvent`, `infrahub_sync/product_store/models.py:494`)

| Field | Type | Rule |
| --- | --- | --- |
| `event_id` | string | Unchanged. |
| `run_id` | string or null | Unchanged. |
| `actor` | string | Unchanged. |
| `operation` | string | Unchanged. |
| `reason` | string or null | Unchanged. |
| `outcome` | string | Unchanged. |
| `created_at` | datetime with time zone | Unchanged. |
| `event_type` | string or null | New. `infrahub_sync.<resource>.<action>`, pattern `^infrahub_sync\.(run\|config)\.[a-z_]+$`. Null only for a refused-authentication record, which `GET /events` never returns (research R6). |
| `primary_resource` | object | New, derived on read: `{"kind": "run", "id": run_id}` or `{"kind": "config", "id": config_id}`. |
| `related` | list of objects | New. Each item `{"kind": <kind>, "id": <id>}`, kinds `account`, `config`, `config_version`, `branch`. |

Storage, for both SQLite and PostgreSQL:

- `audit_events` gains the nullable columns `event_type`, `config_id`, `registry_version` and `branch`.
- A new table `audit_event_resources (event_id, kind, resource_id)` holds the related resources, with an index on
  `(kind, resource_id)` for the related-resource filter. The `account` row is the actor.
- A migration fills `event_type` for every existing row from `(operation, outcome)` with the table below, and
  writes one `account` row per existing event. Existing rows get no configuration or branch, because the old
  record did not hold them (spec, Edge Cases).

### Event types

| `event_type` | Written by | When |
| --- | --- | --- |
| `infrahub_sync.run.accepted` | Sync API | A plan, sync, verify, apply or cancel request is accepted. `operation` names the request. |
| `infrahub_sync.run.replayed` | Sync API | A request is answered from its stored response. |
| `infrahub_sync.run.refused` | Sync API | A request is refused; `outcome` keeps the `refused-*` value. Not `refused-authentication`. |
| `infrahub_sync.run.unavailable` | Sync API | Prefect or the store could not confirm the request. |
| `infrahub_sync.run.planned` | Worker | A plan stage succeeds. |
| `infrahub_sync.run.verified` | Worker | A verify stage succeeds. |
| `infrahub_sync.run.applied` | Worker | An apply stage succeeds. |
| `infrahub_sync.run.synced` | Worker | A sync stage succeeds. |
| `infrahub_sync.run.failed` | Worker | A stage fails or is interrupted; `outcome` is `failed` or `ambiguous`. |
| `infrahub_sync.run.cancelled` | Sync API | A cancellation is recorded as the run's end. |
| `infrahub_sync.config.registered` | Sync API | A configuration is registered (`register-config`). |
| `infrahub_sync.config.version_registered` | Sync API | A configuration version is created. |
| `infrahub_sync.config.refused` | Sync API | A configuration request is refused (`refused-authorization`, `refused-idempotency*`). |
| `infrahub_sync.config.replayed` / `.unavailable` | Sync API | As for runs. |

No source says which actor a worker event names. This plan uses the actor of the accepted request whose mutation
receipt holds the stage's flow run ID (`complete_mutation` stores it, `infrahub_sync/product_store/store.py:2054`),
so an apply that an administrator requested shows the administrator. When no receipt holds the flow run ID, the
event names the run's initiating actor. `operation` is the stage.

## Run log line (view; not stored)

| Field | Source |
| --- | --- |
| `stage` | The orchestration entry's `purpose` for the line's `flow_run_id`. |
| `attempt` | The orchestration entry's `attempt`. |
| `timestamp` | Prefect `Log.timestamp`. |
| `level` | Prefect `Log.level`, as a name (`DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`). |
| `source` | The logger name before ` \| ` (for example `infrahub_sync.service.flow`). |
| `message` | The text after ` \| `. Already redacted by the writer. |

Order: orchestration entries in attempt order, then lines by timestamp, then Prefect's log ID. Lines whose message does not start with the
Sync prefix are left out (research R3).

## Run failure (stored; extends `{stage}_failure` in run results, `infrahub_sync/service/flow.py:743`)

Unchanged fields: `stage`, `outcome`, `error_type`, `cause_type`, and the apply record keys. Change:
`cause` and `next_action` are present for every known error class (FR-001). Known classes for this feature:

- the classes `operator_cause` already handles (`infrahub_sync/service/failure_cause.py`);
- `DestinationSchemaUnavailableError` and its new subclass `DestinationBranchNotFoundError`, one fixed template per
  `reason`.

An unknown class records `error_type` and the next action `` `infrahub-sync runs show <run_id>` ``, as the last log
line already does (spec 016).
