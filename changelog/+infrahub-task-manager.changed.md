The Compose deployment now runs on Infrahub's task manager instead of its own Prefect server and
PostgreSQL: it joins Infrahub's Compose network, keeps its `infrahub_sync` database on the task
manager's PostgreSQL server, and tags each run so it appears in Infrahub's task views, titled after
its stage and configuration (for example `Plan sync of netbox-to-infrahub`) instead of a generated
name. Prefect is pinned to 3.8.6, the version Infrahub 1.11.3 and 1.11.4 ship, and the Sync API and
worker refuse to start against a task manager running another Prefect version.
`INFRAHUB_TASKMANAGER_DB_PASSWORD`, the administrator password of Infrahub's task-manager PostgreSQL
server, replaces `INFRAHUB_SYNC_POSTGRES_ADMIN_PASSWORD`. `INFRAHUB_SYNC_PREFECT_PASSWORD` is
removed with the Prefect database Sync no longer owns, and `INFRAHUB_SYNC_PREFECT_AUTH_STRING` is
now optional. The worker starts every admitted run with Prefect's direct engine starter, pinned to
the installed service flow, instead of the workspace supervisor Prefect 3.8.6 uses for a run with no
configured command, so the child runs exactly what the worker admitted.
