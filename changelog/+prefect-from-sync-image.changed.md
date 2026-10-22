The Compose deployment now runs the Prefect server from the Sync image, so it pulls no Prefect image, publishes no Prefect port and serves no Prefect UI; the Sync API is the only published port. The object store now runs from the Silo image (`pgsty/silo`, AGPLv3, source at https://github.com/pgsty/silo), a maintained build of the MinIO server, instead of the Chainguard MinIO image.

A deployment of `3.0.0a5` cannot be updated in place. Run `docker compose down -v` with the old file before you start the new one. This removes the Prefect work pool, the deployments and the run history, the saved plans and run files, and the `infrahub_sync` database, so keep anything you need outside the deployment first and register your package again afterwards.

`INFRAHUB_SYNC_PREFECT_PORT` has no effect unless an override file publishes the Prefect port, as the documented override snippet does.
