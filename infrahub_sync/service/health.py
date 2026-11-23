"""The health of the Sync API's components and of its workers, as `GET /api/health` reports it.

One round of checks runs the four component checks and the read of the work pool together, each
under the same time limit. A round serves every caller for one second after it ended, so a
monitoring tool that polls often does not multiply the load on the components.

A failure is reported as one of seven categories. The text of an error is read only to choose a
category: it is never logged, returned or re-raised, because a client error can quote a host name,
an address or a connection string (see the secret-redaction guideline).
"""

from __future__ import annotations

import asyncio
import re
import socket
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import TYPE_CHECKING, Any, Final, Literal

import boto3
import httpx
import psycopg
import structlog
from botocore.config import Config
from botocore.exceptions import (
    ClientError,
    ConnectionClosedError,
    ConnectTimeoutError,
    EndpointConnectionError,
    ReadTimeoutError,
)

from infrahub_sync.client.models import (
    DependencyHealthResource,
    HealthResource,
    WorkerHealthResource,
    WorkersHealthResource,
)

from .orchestration import SERVICE_WORKER_NAME_PREFIX, PoolStatus, normalized_pool_status, worker_is_live
from .storage import s3_client_arguments

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping, Sequence

logger = structlog.get_logger(__name__)

DependencyName = Literal["database", "object_store", "task_manager", "task_manager_db"]
DEPENDENCY_NAMES: Final[tuple[DependencyName, ...]] = ("database", "object_store", "task_manager", "task_manager_db")

# A report is reused for this many seconds after its round ended.
MAX_REPORT_AGE_SECONDS: Final = 1.0
# Workers whose last heartbeat is older than this are not listed.
WORKER_LIST_WINDOW: Final = timedelta(hours=24)
# The name the Sync worker entrypoint registers. Any other name is shown as `other`, so the answer
# never holds a name chosen elsewhere, such as an account identifier (spec FR-013).
_WORKER_NAME: Final = re.compile(
    re.escape(SERVICE_WORKER_NAME_PREFIX) + r"-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
)
OTHER_WORKER_NAME: Final = "other"
# libpq raises a `connect_timeout` below 2 seconds to 2 seconds.
_LIBPQ_MINIMUM_CONNECT_TIMEOUT: Final = 2


class ErrorCategory(str, Enum):
    """Why a component check failed; the same seven names as Infrahub's health endpoint."""

    NONE = "none"
    TIMEOUT = "timeout"
    CONNECTION_REFUSED = "connection_refused"
    CONNECTION_CLOSED = "connection_closed"
    NAME_RESOLUTION_FAILED = "name_resolution_failed"
    UNAVAILABLE = "unavailable"
    UNKNOWN_ERROR = "unknown_error"


class DependencyUnavailableError(Exception):
    """A component answered but cannot serve, for example a Prefect answer that is not 2xx."""

    def __init__(self) -> None:
        super().__init__("the component answered that it cannot serve")


# Markers in the text of a psycopg 3.3.6 `OperationalError`, which carries no `OSError` in its
# chain (research R-3). The image runs with the C locale, so libpq messages are in English.
_PSYCOPG_TEXT_MARKERS: Final[tuple[tuple[str, ErrorCategory], ...]] = (
    ("could not translate host name", ErrorCategory.NAME_RESOLUTION_FAILED),
    ("Connection refused", ErrorCategory.CONNECTION_REFUSED),
    ("server closed the connection", ErrorCategory.CONNECTION_CLOSED),
    ("connection to server was lost", ErrorCategory.CONNECTION_CLOSED),
    ("timeout expired", ErrorCategory.TIMEOUT),
)
_PSYCOPG_RESOLVE_PREFIX: Final = "failed to resolve host"

# The error types with a category of their own, in the order they are tested. The specific
# types come first: the botocore timeouts and every connection error are also OSErrors.
_TYPE_CATEGORIES: Final[tuple[tuple[tuple[type[BaseException], ...], ErrorCategory], ...]] = (
    ((TimeoutError, httpx.TimeoutException, ConnectTimeoutError, ReadTimeoutError), ErrorCategory.TIMEOUT),
    ((socket.gaierror,), ErrorCategory.NAME_RESOLUTION_FAILED),
    ((ConnectionRefusedError,), ErrorCategory.CONNECTION_REFUSED),
    (
        (
            ConnectionResetError,
            ConnectionAbortedError,
            BrokenPipeError,
            httpx.RemoteProtocolError,
            ConnectionClosedError,
        ),
        ErrorCategory.CONNECTION_CLOSED,
    ),
    ((ClientError, DependencyUnavailableError), ErrorCategory.UNAVAILABLE),
)


def _psycopg_category(error: psycopg.Error) -> ErrorCategory:
    if isinstance(error, psycopg.errors.ConnectionTimeout):
        return ErrorCategory.TIMEOUT
    if isinstance(error, psycopg.OperationalError):
        text = str(error)
        if text.startswith(_PSYCOPG_RESOLVE_PREFIX):
            return ErrorCategory.NAME_RESOLUTION_FAILED
        for marker, category in _PSYCOPG_TEXT_MARKERS:
            if marker in text:
                return category
    return ErrorCategory.UNAVAILABLE


def _own_category(error: BaseException) -> ErrorCategory | None:
    """Return the category of this one error, or None when only the errors it wraps can tell."""
    if isinstance(error, psycopg.Error):
        return _psycopg_category(error)
    for types, category in _TYPE_CATEGORIES:
        if isinstance(error, types):
            return category
    return None


def _wrapped(error: BaseException) -> Iterator[BaseException]:
    """Yield the errors that this error wraps: group members, the botocore cause, cause, context."""
    # An exception group (Python 3.11 and later) holds its members in `exceptions`.
    members = getattr(error, "exceptions", ())
    if isinstance(members, tuple):
        yield from (member for member in members if isinstance(member, BaseException))
    if isinstance(error, EndpointConnectionError):
        # botocore keeps the error it could not connect with in its keyword arguments.
        inner = error.kwargs.get("error")
        if isinstance(inner, BaseException):
            yield inner
    if error.__cause__ is not None:
        yield error.__cause__
    if error.__context__ is not None:
        yield error.__context__


def classify_error(error: BaseException) -> ErrorCategory:
    """Return the category of a failed check, searching the errors it wraps (research R-3).

    An error that has a category of its own gives it; otherwise the wrapped errors are searched
    depth first. A chain with no known error, or with a cycle, gives `unknown_error`.
    """
    seen: set[int] = set()
    pending: list[BaseException] = [error]
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        category = _own_category(current)
        if category is not None:
            return category
        pending.extend(reversed(list(_wrapped(current))))
    return ErrorCategory.UNKNOWN_ERROR


# The aliases are evaluated when the module loads, so their names are imported at run time.
Check = Callable[[], Awaitable[None]]
PoolReader = Callable[[datetime], Awaitable[PoolStatus]]


@dataclass(frozen=True, slots=True)
class DependencyCheck:
    """One component and the check that proves it can serve; the check raises when it cannot."""

    name: DependencyName
    check: Check


def _public_worker_name(name: str | None) -> str:
    if name is not None and _WORKER_NAME.fullmatch(name):
        return name
    return OTHER_WORKER_NAME


def workers_health(pool: PoolStatus | None, now: datetime, minimum: int) -> WorkersHealthResource:
    """Build the worker section of a report from one work-pool read (None when it timed out).

    The workers listed are those with a heartbeat within 24 hours of `now`, newest first and
    then by name; `live_workers` counts those that `worker_is_live` accepts. A malformed snapshot
    counts as a read that failed, so provider detail that breaks the snapshot's rules never
    reaches the report.
    """
    if pool is not None:
        pool = normalized_pool_status(pool)
    if pool is None or not pool.detail_available:
        return WorkersHealthResource(state="unknown", minimum=minimum, live_workers=None, queue_depth=None, items=[])
    items: list[WorkerHealthResource] = []
    for worker in pool.workers:
        heartbeat = worker.last_heartbeat
        if heartbeat is None or now - heartbeat > WORKER_LIST_WINDOW:
            continue
        items.append(
            WorkerHealthResource(
                name=_public_worker_name(worker.name),
                state="live" if worker_is_live(worker, now) else "stale",
                last_heartbeat=heartbeat,
            )
        )
    items.sort(key=lambda item: item.name)
    items.sort(key=lambda item: item.last_heartbeat, reverse=True)
    return WorkersHealthResource(
        state="known",
        minimum=minimum,
        live_workers=sum(1 for item in items if item.state == "live"),
        queue_depth=pool.queue_depth,
        items=items,
    )


def overall_status(
    dependencies: Sequence[DependencyHealthResource], workers: WorkersHealthResource
) -> Literal["healthy", "unhealthy"]:
    """Healthy only when every component is up and enough workers are known to be live (research R-6)."""
    if any(dependency.status != "up" for dependency in dependencies):
        return "unhealthy"
    if workers.state != "known" or workers.live_workers is None or workers.live_workers < workers.minimum:
        return "unhealthy"
    return "healthy"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class HealthChecker:
    """Run rounds of health checks and serve each report for one second after its round ended."""

    def __init__(
        self,
        *,
        checks: Sequence[DependencyCheck],
        pool_reader: PoolReader,
        minimum: int,
        check_timeout: float,
        max_report_age: float = MAX_REPORT_AGE_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        """Keep the checks, which must be the four components in the order of the report.

        Raises:
            ValueError: the checks are not exactly the four dependencies in order.
        """
        if tuple(check.name for check in checks) != DEPENDENCY_NAMES:
            msg = f"the health checks must be the dependencies {', '.join(DEPENDENCY_NAMES)} in this order"
            raise ValueError(msg)
        self._checks = tuple(checks)
        self._pool_reader = pool_reader
        self._minimum = minimum
        self._check_timeout = check_timeout
        self._max_report_age = max_report_age
        self._clock = clock
        self._wall_clock = wall_clock
        self._lock = asyncio.Lock()
        self._report: HealthResource | None = None
        self._ended_at = 0.0

    def _fresh_report(self) -> HealthResource | None:
        if self._report is not None and self._clock() - self._ended_at < self._max_report_age:
            return self._report
        return None

    async def report(self) -> HealthResource:
        """Return the report of a round that ended under one second ago, or run a new round."""
        cached = self._fresh_report()
        if cached is not None:
            return cached
        async with self._lock:
            # A caller that waited for the lock receives the round that just ended.
            cached = self._fresh_report()
            if cached is not None:
                return cached
            report = await self._round()
            self._report = report
            self._ended_at = self._clock()
            return report

    async def _round(self) -> HealthResource:
        started = self._wall_clock()
        dependencies, pool = await asyncio.gather(
            asyncio.gather(*(self._run_check(check) for check in self._checks)),
            self._read_pool(started),
        )
        workers = workers_health(pool, started, self._minimum)
        return HealthResource(
            status=overall_status(dependencies, workers),
            dependencies=list(dependencies),
            workers=workers,
            timestamp=started,
        )

    async def _run_check(self, dependency: DependencyCheck) -> DependencyHealthResource:
        try:
            await asyncio.wait_for(dependency.check(), self._check_timeout)
        # Any failure of a component client must become `down` for this component, not the
        # error envelope of the whole answer (plan.md, Complexity Tracking). The error is not
        # re-raised, and only its type name is logged, never its text.
        except Exception as error:  # pylint: disable=broad-exception-caught  # noqa: BLE001
            category = classify_error(error)
            logger.warning(
                "health check failed",
                component=dependency.name,
                category=category.value,
                error_type=type(error).__name__,
            )
            return DependencyHealthResource(name=dependency.name, status="down", error=category.value)
        return DependencyHealthResource(name=dependency.name, status="up", error="none")

    async def _read_pool(self, now: datetime) -> PoolStatus | None:
        """Read the work pool within the time limit; None when the limit passed or the read failed.

        The orchestrator's `pool_status` turns a failed read into an unavailable snapshot itself,
        but the Prefect client is opened before that read, so a connection error can come from it.
        """
        try:
            return await asyncio.wait_for(self._pool_reader(now), self._check_timeout)
        # Any failure while the Prefect client opens or reads must make the workers `unknown`, not
        # replace the whole answer with the error envelope (plan.md, Complexity Tracking). The error
        # is not re-raised, and only its type name is logged, never its text.
        except Exception as error:  # pylint: disable=broad-exception-caught  # noqa: BLE001
            logger.warning(
                "health check failed",
                component="workers",
                category=classify_error(error).value,
                error_type=type(error).__name__,
            )
            return None


def database_check(database_url: str, limit: int) -> Check:
    """Open a connection to the product database, run `SELECT 1` and close it (research R-2).

    No server setting is passed: an `options` connection keyword would replace the `options` of
    the database URL, and a connection pooler such as PgBouncer refuses it. The round's time limit
    (`asyncio.wait_for`) cancels a query that does not answer, and psycopg then asks the server to
    cancel it before the connection closes.
    """

    async def check() -> None:
        connection = await psycopg.AsyncConnection.connect(
            database_url,
            autocommit=True,
            connect_timeout=max(limit, _LIBPQ_MINIMUM_CONNECT_TIMEOUT),
        )
        async with connection:
            await connection.execute("SELECT 1")

    return check


def object_store_check(
    values: Mapping[str, str],
    bucket: str,
    limit: int,
    client_builder: Callable[..., Any] = boto3.client,
) -> Check:
    """Check the object store with `head_bucket` on the configured bucket (research R-2, R-4).

    The client is built once, from the same settings as the product store's, with timeouts equal
    to the limit and no retries, so the thread that runs the call ends close to the limit.

    Raises:
        ValueError: an S3 setting is not accepted; the message names the variable.
    """
    arguments = s3_client_arguments(values)
    limits = Config(connect_timeout=limit, read_timeout=limit, retries={"max_attempts": 0})
    arguments["config"] = arguments["config"].merge(limits)
    client = client_builder("s3", **arguments)

    async def check() -> None:
        await asyncio.to_thread(client.head_bucket, Bucket=bucket)

    return check


def _prefect_check(url: str, limit: int, transport: httpx.AsyncBaseTransport | None) -> Check:
    async def check() -> None:
        # A plain client: the Prefect client retries a 503 before it returns.
        async with httpx.AsyncClient(timeout=limit, transport=transport) as client:
            response = await client.get(url)
        if not response.is_success:
            # Not `raise_for_status()`: its message quotes the address.
            raise DependencyUnavailableError

    return check


def task_manager_check(api_url: str, limit: int, transport: httpx.AsyncBaseTransport | None = None) -> Check:
    """Check the Prefect server with `GET {PREFECT_API_URL}/health`."""
    return _prefect_check(f"{api_url.rstrip('/')}/health", limit, transport)


def task_manager_db_check(api_url: str, limit: int, transport: httpx.AsyncBaseTransport | None = None) -> Check:
    """Check the Prefect server's database with `GET {PREFECT_API_URL}/ready`, which answers 503 without it."""
    return _prefect_check(f"{api_url.rstrip('/')}/ready", limit, transport)


__all__ = [
    "DEPENDENCY_NAMES",
    "DependencyCheck",
    "DependencyName",
    "DependencyUnavailableError",
    "ErrorCategory",
    "HealthChecker",
    "PoolReader",
    "classify_error",
    "database_check",
    "object_store_check",
    "overall_status",
    "task_manager_check",
    "task_manager_db_check",
    "workers_health",
]
