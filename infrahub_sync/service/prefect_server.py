"""Application factory that runs the Prefect server from the Sync image.

Every stack starts the Prefect server with::

    uvicorn --host 0.0.0.0 --port 4200 --factory infrahub_sync.service.prefect_server:create_prefect_app

so the server runs the Prefect version the Sync image installs, and no separate
Prefect image is needed. The factory adds nothing to Prefect's own application:
every Prefect server setting (database, UI, authentication, telemetry) is read
from the environment exactly as the `prefect server start` command reads it.
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
