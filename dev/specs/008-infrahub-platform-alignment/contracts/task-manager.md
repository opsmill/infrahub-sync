# Contract: Sync on Infrahub's task manager

## Version

- `prefect==3.8.6`, equal to the version locked by Infrahub 1.11.3 and 1.11.4, the supported
  releases today; a later Prefect in Infrahub needs a Sync release (research R1, R11).
- Sync refuses to start when the task manager reports another server version, naming both
  (FR-002). The server version is read from the Prefect API `/version` route.

## Objects Sync creates on the task manager

| Object | Name | Created by |
|---|---|---|
| Work pool | `infrahub-sync` (type `process`, `ServiceProcessWorker`) | `sync-bootstrap` |
| Deployment | `infrahub-sync-service/run` | `sync-bootstrap` |

Infrahub's startup does not modify them (research R2). Sync never modifies `infrahub-worker`.

## Flow-run tags (FR-003)

Every flow run Sync creates carries these tags, added to the tags the run
already has (the `infrahub-sync` and `service` deployment tags included). The two
node tags arrive with US2, when the `SyncRun` and `SyncConfiguration` nodes exist;
US1 adds the first three:

```text
infrahub.app
infrahub.app/branch/<target branch>
infrahub.app/workflow-type/sync-<plan|verify|apply|sync>
infrahub.app/node/<SyncRun node id>
infrahub.app/node/<SyncConfiguration node id>
```

## Access (FR-018)

| `INFRAHUB_SYNC_PREFECT_AUTH_STRING` | Behavior | Startup log |
|---|---|---|
| set | Sent as `PREFECT_API_AUTH_STRING` | `prefect_auth=credential` |
| unset | No credential sent | `prefect_auth=network-isolation` |

## Lock database (FR-014)

`INFRAHUB_SYNC_DATABASE_URL` points to database `infrahub_sync` on Infrahub's task-manager-db
server. A bootstrap step creates the database if missing and converges the product-store schema
(`product_store/store.py`); it never touches the `prefect` database.
