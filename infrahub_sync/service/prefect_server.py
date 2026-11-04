"""Application factory that runs the Prefect server from the Sync image.

Every stack starts the Prefect server with::

    uvicorn --host 0.0.0.0 --port 4200 --factory infrahub_sync.service.prefect_server:create_prefect_app

so the server runs the Prefect version the Sync image installs, and no separate
Prefect image is needed. The factory adds nothing to Prefect's own application:
every setting that application reads (database, UI, authentication, telemetry) comes
from the environment, as it does under `prefect server start`.

The listener is uvicorn's, not Prefect's CLI: the address and port come from the
command line above. `PREFECT_SERVER_API_HOST`, `PREFECT_SERVER_API_PORT`,
`PREFECT_SERVER_API_KEEPALIVE_TIMEOUT` and the websocket ping settings, which
`prefect server start` passes to uvicorn and the application does not read, have no
effect here. `PREFECT_SERVER_LOGGING_LEVEL` still applies: `create_app()` sets up
Prefect's logging, which gives that level to the `prefect.server`, `uvicorn` and
`fastapi` loggers. Only the uvicorn `--log-level` option that `prefect server start`
derives from it is not passed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from prefect.server.api.server import create_app

if TYPE_CHECKING:
    from fastapi import FastAPI


def create_prefect_app() -> FastAPI:
    """Return the Prefect server application, configured from the current Prefect settings.

    The application serves the Prefect REST API under `/api` and, when
    `PREFECT_UI_ENABLED` is true, the Prefect UI under `/`. Its lifespan runs the
    database migration and the server's background services.
    """
    return create_app()
