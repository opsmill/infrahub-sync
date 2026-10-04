"""Startup checks against the Prefect server the Sync API and worker share with Infrahub.

Sync runs on Infrahub's task manager, so the Prefect server is not Sync's to pin.
Sync's own Prefect client is pinned to the version the supported Infrahub release
ships, and both services refuse to start against any other server version: a
client and server that were never qualified together are not a supported pair.

Infrahub's task manager offers no API credential setting, so the credential is
optional here. Each service states at startup which of the two access modes it
is in, so an operator can see that a deployment without a credential depends on
network isolation alone.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Final, Literal, Protocol

import httpx
import prefect
from prefect.client.orchestration import get_client
from prefect.settings import get_current_settings

if TYPE_CHECKING:
    from collections.abc import Mapping

logger = logging.getLogger(__name__)

PrefectAuthMode = Literal["credential", "network-isolation"]
ServiceName = Literal["API", "worker"]

AUTH_STRING_ENV: Final = "PREFECT_API_AUTH_STRING"
CREDENTIAL_MODE: Final[PrefectAuthMode] = "credential"
NETWORK_ISOLATION_MODE: Final[PrefectAuthMode] = "network-isolation"
API_SERVICE: Final[ServiceName] = "API"
WORKER_SERVICE: Final[ServiceName] = "worker"

# Each message is the whole report: the client's errors render the API URL, and an
# operator may have put a credential in it.
_UNAVAILABLE = "the Prefect server version could not be read"
_NOT_CONFIGURED = "PREFECT_API_URL is not set; point it at Infrahub's task manager"


class PrefectServerCheckError(RuntimeError):
    """A startup check against the Prefect server failed; the message is secret-safe."""


class PrefectServerUnavailableError(PrefectServerCheckError):
    """The Prefect server is not configured, unreachable, answered a server error, or gave no version."""

    def __init__(self, message: str = _UNAVAILABLE) -> None:
        super().__init__(message)


class PrefectServerNotConfiguredError(PrefectServerUnavailableError):
    """No Prefect API URL is set, so there is no server to check."""

    def __init__(self) -> None:
        super().__init__(_NOT_CONFIGURED)


class PrefectServerRefusedCredentialError(PrefectServerCheckError):
    """The Prefect server refused the configured credential (401 or 403)."""

    def __init__(self) -> None:
        super().__init__("the Prefect server refused the configured credential (INFRAHUB_SYNC_PREFECT_AUTH_STRING)")


class PrefectServerRejectedCheckError(PrefectServerCheckError):
    """The server answered the version route with a client error other than a credential refusal."""

    def __init__(self, status_code: int) -> None:
        super().__init__(
            f"the Prefect server answered its version route with HTTP {status_code}; "
            "check that PREFECT_API_URL points at Infrahub's task manager API"
        )
        self.status_code = status_code


class PrefectVersionMismatchError(PrefectServerCheckError):
    """The Prefect server runs another version than this Sync release's client."""

    def __init__(self, server_version: str, client_version: str) -> None:
        super().__init__(
            f"the Prefect server runs {server_version[:64]!r}, and this Sync release requires {client_version}; "
            "use the Infrahub release that ships the Prefect version Sync requires"
        )
        self.server_version = server_version
        self.client_version = client_version


class PrefectStartupRefusedError(SystemExit):
    """A Sync service refuses to start; the message names the service and the reason."""

    def __init__(self, service: ServiceName, reason: PrefectServerCheckError) -> None:
        super().__init__(f"infrahub-sync {service} refused to start: {reason}")
        self.service = service
        self.reason = reason


class _VersionClient(Protocol):
    """The one Prefect client method the version check uses."""

    async def api_version(self) -> str: ...


async def require_prefect_server_version(client: _VersionClient) -> str:
    """Return the server's Prefect version, or refuse when it differs from the client's."""
    try:
        server_version = await client.api_version()
    except httpx.HTTPStatusError as error:
        # A server that answers and refuses the credential is configured wrongly,
        # not briefly away: that is never tolerated.
        status_code = error.response.status_code
        if status_code in {401, 403}:
            raise PrefectServerRefusedCredentialError from None
        # Any other 4xx is an answer, not an outage: the route does not exist at this
        # URL (404) or the request was rejected. Only 408 and 429 can pass on retry.
        if 400 <= status_code < 500 and status_code not in {408, 429}:
            raise PrefectServerRejectedCheckError(status_code) from None
        raise PrefectServerUnavailableError from None
    # `api_version` decodes the body as JSON: a proxy or login page answering 200
    # raises ValueError, not an HTTP error.
    except (httpx.HTTPError, httpx.InvalidURL, ValueError):
        raise PrefectServerUnavailableError from None
    if not isinstance(server_version, str) or not server_version:
        raise PrefectServerUnavailableError
    if server_version != prefect.__version__:
        raise PrefectVersionMismatchError(server_version, prefect.__version__)
    return server_version


def prefect_auth_mode(environ: Mapping[str, str] | None = None) -> PrefectAuthMode:
    """Name the access mode: a Prefect API credential, or network isolation alone.

    Without a mapping, the credential is read from Prefect's own settings, which is
    what the client presents: the environment, a profile, or a settings file.
    """
    if environ is None:
        secret = get_current_settings().api.auth_string
        credential = secret.get_secret_value() if secret is not None else ""
    else:
        credential = environ.get(AUTH_STRING_ENV, "")
    return CREDENTIAL_MODE if credential.strip() else NETWORK_ISOLATION_MODE


def log_prefect_auth_mode(environ: Mapping[str, str] | None = None) -> PrefectAuthMode:
    """Log the access mode once at startup, never the credential, and return it.

    Logged at WARNING: it states the deployment's security posture, and the services
    log this before any handler would show an INFO record.
    """
    mode = prefect_auth_mode(environ)
    logger.warning("prefect_auth=%s", mode)
    if mode == NETWORK_ISOLATION_MODE:
        logger.warning("no Prefect API credential is set; the task manager is protected by network isolation only")
    return mode


async def check_prefect_server(environ: Mapping[str, str] | None = None) -> str:
    """Run both startup checks against the configured Prefect API; return the server version.

    An unset API URL is refused rather than left to Prefect, which would otherwise
    start a temporary local server whose version always matches.
    """
    log_prefect_auth_mode(environ)
    if not get_current_settings().api.url:
        raise PrefectServerNotConfiguredError
    try:
        async with get_client() as client:
            return await require_prefect_server_version(client)
    except (httpx.InvalidURL, ValueError):
        raise PrefectServerUnavailableError from None


async def api_startup_check() -> None:
    """The API's startup check: refuse a wrong version, no URL, a refused credential or a 4xx answer.

    Only an unreachable task manager is tolerated. The API answers clients and reports
    worker liveness on its own, so a task manager that is briefly down is not a reason
    to stop it: runs are refused at submission and the status reports the missing
    worker. A server of another version, or none configured at all, is never a
    supported state, so both still stop the API.
    """
    try:
        await check_prefect_server()
    except PrefectServerNotConfiguredError:
        raise
    except PrefectServerUnavailableError as error:
        logger.warning("the Prefect server could not be checked at startup (%s); the API serves anyway", error)


def refuse_start_unless_prefect_ready(service: ServiceName) -> None:
    """Run the startup checks, or stop the process with a secret-safe refusal."""
    try:
        asyncio.run(check_prefect_server())
    except PrefectServerCheckError as error:
        raise PrefectStartupRefusedError(service, error) from None
