"""Run the Prefect server from the Sync image.

The server is Prefect's own application, unchanged: the factory adds no route and runs no
Sync bootstrap step, which stays in ``sync-bootstrap``. ``main()`` passes uvicorn the same
values that ``prefect server start`` passes with one worker.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import uvicorn
from prefect.server.api import server as prefect_api_server
from prefect.settings import get_current_settings

if TYPE_CHECKING:
    from fastapi import FastAPI

FACTORY = "infrahub_sync.service.prefect_server:create_prefect_server"


def create_prefect_server() -> FastAPI:
    """Return Prefect's server application, built as the last one of this process."""
    return prefect_api_server.create_app(final=True)


def main() -> None:
    """Serve the Prefect API and UI with the host, port and timeouts of Prefect's settings."""
    # The settings `prefect server start` reads: PREFECT_SERVER_API_HOST, _PORT,
    # _KEEPALIVE_TIMEOUT, _WEBSOCKET_PING_INTERVAL, _WEBSOCKET_PING_TIMEOUT and
    # PREFECT_SERVER_LOGGING_LEVEL.
    server = get_current_settings().server
    uvicorn.run(
        FACTORY,
        factory=True,
        host=server.api.host,
        port=server.api.port,
        timeout_keep_alive=server.api.keepalive_timeout,
        ws_ping_interval=server.api.websocket_ping_interval,
        ws_ping_timeout=server.api.websocket_ping_timeout,
        log_level=server.logging_level.lower(),
    )


if __name__ == "__main__":
    main()
