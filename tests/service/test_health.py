"""The health checker classifies failures, runs one bounded round per second and reports the workers."""

from __future__ import annotations

import asyncio
import builtins
import socket
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

import httpx
import psycopg
import pytest
from botocore.config import Config
from botocore.exceptions import (
    ClientError,
    ConnectionClosedError,
    ConnectTimeoutError,
    EndpointConnectionError,
    ReadTimeoutError,
)
from structlog.testing import capture_logs

from infrahub_sync.client.models import HealthResource
from infrahub_sync.service.health import (
    DEPENDENCY_NAMES,
    CheckStillRunningError,
    DependencyCheck,
    DependencyUnavailableError,
    ErrorCategory,
    HealthChecker,
    classify_error,
    database_check,
    object_store_check,
    task_manager_check,
    task_manager_db_check,
    workers_health,
)
from infrahub_sync.service.orchestration import PoolStatus, PoolWorker
from infrahub_sync.service.worker import service_worker_name

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from typing_extensions import Self

    CheckFactory = Callable[..., Callable[[], Awaitable[None]]]

NOW = datetime(2026, 11, 23, 10, 15, 9, tzinfo=timezone.utc)
CONNECTION_TEXT = "postgresql://sync:hunter2@db.internal:5432/sync"


def _chained(outer: BaseException, cause: BaseException) -> BaseException:
    outer.__cause__ = cause
    return outer


def _client_error(status: int, code: str) -> ClientError:
    return ClientError({"Error": {"Code": code}, "ResponseMetadata": {"HTTPStatusCode": status}}, "HeadBucket")


# --- classify_error (T006) -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        pytest.param(
            psycopg.OperationalError(
                "failed to resolve host 'no-such-host.invalid': [Errno 8] nodename nor servname provided, or not known"
            ),
            "name_resolution_failed",
            id="psycopg-resolve-host",
        ),
        pytest.param(
            psycopg.OperationalError('could not translate host name "db" to address: Name or service not known'),
            "name_resolution_failed",
            id="libpq-translate-host",
        ),
        pytest.param(
            psycopg.OperationalError(
                'connection failed: connection to server at "127.0.0.1", port 1 failed: '
                "could not receive data from server: Connection refused"
            ),
            "connection_refused",
            id="psycopg-refused",
        ),
        pytest.param(psycopg.errors.ConnectionTimeout("connection timeout expired"), "timeout", id="psycopg-timeout"),
        pytest.param(
            psycopg.OperationalError("server closed the connection unexpectedly"),
            "connection_closed",
            id="psycopg-closed",
        ),
        pytest.param(psycopg.OperationalError("connection to server was lost"), "connection_closed", id="psycopg-lost"),
        pytest.param(psycopg.OperationalError("timeout expired"), "timeout", id="psycopg-timeout-text"),
        pytest.param(
            psycopg.OperationalError('password authentication failed for user "sync"'),
            "unavailable",
            id="psycopg-other",
        ),
        pytest.param(psycopg.errors.QueryCanceled("canceling statement"), "unavailable", id="psycopg-error"),
        pytest.param(socket.gaierror(8, "nodename nor servname provided"), "name_resolution_failed", id="gaierror"),
        pytest.param(ConnectionRefusedError(61, "refused"), "connection_refused", id="refused"),
        pytest.param(ConnectionResetError(54, "reset"), "connection_closed", id="reset"),
        pytest.param(ConnectionAbortedError(53, "aborted"), "connection_closed", id="aborted"),
        pytest.param(BrokenPipeError(32, "pipe"), "connection_closed", id="broken-pipe"),
        pytest.param(TimeoutError(), "timeout", id="timeout-error"),
        pytest.param(httpx.ConnectTimeout("connect"), "timeout", id="httpx-connect-timeout"),
        pytest.param(httpx.ReadTimeout("read"), "timeout", id="httpx-read-timeout"),
        pytest.param(httpx.RemoteProtocolError("closed"), "connection_closed", id="httpx-remote-protocol"),
        pytest.param(ConnectTimeoutError(endpoint_url="http://s3"), "timeout", id="botocore-connect-timeout"),
        pytest.param(ReadTimeoutError(endpoint_url="http://s3"), "timeout", id="botocore-read-timeout"),
        pytest.param(
            EndpointConnectionError(endpoint_url="http://s3", error=ConnectionRefusedError(61, "refused")),
            "connection_refused",
            id="botocore-endpoint-refused",
        ),
        pytest.param(ConnectionClosedError(endpoint_url="http://s3"), "connection_closed", id="botocore-closed"),
        pytest.param(_client_error(404, "404"), "unavailable", id="client-error-404"),
        pytest.param(_client_error(403, "403"), "unavailable", id="client-error-403"),
        pytest.param(DependencyUnavailableError(), "unavailable", id="dependency-unavailable"),
        pytest.param(RuntimeError(CONNECTION_TEXT), "unknown_error", id="runtime-error"),
    ],
)
def test_classify_error_maps_each_client_failure_to_its_category(error: BaseException, expected: str) -> None:
    assert classify_error(error) == ErrorCategory(expected)


def test_classify_error_finds_a_known_error_in_the_cause() -> None:
    error = _chained(httpx.ConnectError("All connection attempts failed"), socket.gaierror(8, "nodename"))

    assert classify_error(error) is ErrorCategory.NAME_RESOLUTION_FAILED


def test_classify_error_finds_a_known_error_in_the_context() -> None:
    outer = RuntimeError("wrapper")
    outer.__context__ = ConnectionRefusedError(61, "refused")

    assert classify_error(outer) is ErrorCategory.CONNECTION_REFUSED


def test_classify_error_finds_a_known_error_inside_an_exception_group() -> None:
    # ty checks against Python 3.10; the Sync service and these tests run on Python 3.11 or later.
    group = builtins.ExceptionGroup(  # ty: ignore[unresolved-attribute]
        "several", [RuntimeError("other"), ConnectionResetError(54, "reset")]
    )

    assert classify_error(_chained(RuntimeError("wrapper"), group)) is ErrorCategory.CONNECTION_CLOSED


def test_classify_error_ends_on_a_cycle_in_the_chain() -> None:
    first = RuntimeError("first")
    second = RuntimeError("second")
    first.__cause__ = second
    second.__cause__ = first

    assert classify_error(first) is ErrorCategory.UNKNOWN_ERROR


def test_the_error_categories_are_the_seven_of_the_contract() -> None:
    assert {member.value for member in ErrorCategory} == {
        "none",
        "timeout",
        "connection_refused",
        "connection_closed",
        "name_resolution_failed",
        "unavailable",
        "unknown_error",
    }


# --- HealthChecker (T007) --------------------------------------------------------------------


class FakeClock:
    """A monotonic clock that only moves when a test moves it."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


async def _up() -> None:  # noqa: RUF029 - the checker awaits every check.
    return None


def _checks(**overrides: Callable[[], Awaitable[None]]) -> list[DependencyCheck]:
    return [DependencyCheck(name, overrides.get(name, _up)) for name in DEPENDENCY_NAMES]


def _live_worker() -> PoolWorker:
    return PoolWorker(
        worker_id="3f0c2a9e-6a1b-4c55-9d3e-2b7c1e0f4a11",
        status="online",
        last_heartbeat=NOW - timedelta(seconds=5),
        heartbeat_interval_seconds=10.0,
        name="infrahub-sync-service-3f0c2a9e-6a1b-4c55-9d3e-2b7c1e0f4a11",
    )


def _pool(*workers: PoolWorker, queue_depth: int = 0) -> PoolStatus:
    return PoolStatus(detail_available=True, queue_depth=queue_depth, observed_at=NOW, workers=workers)


UNAVAILABLE_POOL = PoolStatus(detail_available=False, queue_depth=None, observed_at=None)


def _reader(pool: PoolStatus) -> Callable[[datetime], Awaitable[PoolStatus]]:
    async def read(_now: datetime) -> PoolStatus:  # noqa: RUF029 - the checker awaits every check.
        return pool

    return read


def _checker(  # noqa: PLR0913 - every argument has a default the tests override.
    checks: list[DependencyCheck] | None = None,
    pool: PoolStatus | None = None,
    *,
    reader: Callable[[datetime], Awaitable[PoolStatus]] | None = None,
    minimum: int = 1,
    check_timeout: float = 3,
    clock: FakeClock | None = None,
) -> HealthChecker:
    return HealthChecker(
        checks=checks if checks is not None else _checks(),
        pool_reader=reader or _reader(pool if pool is not None else _pool(_live_worker())),
        minimum=minimum,
        check_timeout=check_timeout,
        clock=clock or FakeClock(),
        wall_clock=lambda: NOW,
    )


async def test_every_component_up_and_a_live_worker_is_healthy() -> None:
    report = await _checker().report()

    assert isinstance(report, HealthResource)
    assert report.status == "healthy"
    assert [(d.name, d.status, d.error) for d in report.dependencies] == [
        (name, "up", "none") for name in DEPENDENCY_NAMES
    ]
    assert report.timestamp == NOW


async def test_a_check_that_raises_is_down_with_its_category_and_unhealthy() -> None:
    async def refused() -> None:  # noqa: RUF029 - the checker awaits every check.
        raise ConnectionRefusedError(61, CONNECTION_TEXT)

    report = await _checker(_checks(object_store=refused)).report()

    assert report.status == "unhealthy"
    by_name = {d.name: d for d in report.dependencies}
    assert (by_name["object_store"].status, by_name["object_store"].error) == ("down", "connection_refused")
    assert all(d.status == "up" for name, d in by_name.items() if name != "object_store")


async def test_a_check_past_the_limit_is_a_timeout_and_the_round_ends_close_to_the_limit() -> None:
    async def hangs() -> None:
        await asyncio.sleep(30)

    started = time.monotonic()
    report = await _checker(_checks(database=hangs), check_timeout=0.2).report()
    elapsed = time.monotonic() - started

    assert elapsed < 0.2 + 0.5
    database = report.dependencies[0]
    assert (database.name, database.status, database.error) == ("database", "down", "timeout")
    assert report.status == "unhealthy"


async def test_ten_concurrent_callers_share_one_round() -> None:
    calls = 0

    async def counted() -> None:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.05)

    checker = _checker(_checks(database=counted))

    reports = await asyncio.gather(*(checker.report() for _ in range(10)))

    assert calls == 1
    assert all(report is reports[0] for report in reports)


@pytest.mark.parametrize(("delay", "rounds"), [(0.9, 1), (1.1, 2)])
async def test_a_report_is_reused_for_one_second_after_its_round_ended(delay: float, rounds: int) -> None:
    clock = FakeClock()
    calls = 0

    async def slow() -> None:  # noqa: RUF029 - the checker awaits every check.
        nonlocal calls
        calls += 1
        # The round takes 5 seconds on the fake clock: the age is measured from its end.
        clock.now += 5

    checker = _checker(_checks(database=slow), clock=clock)
    await checker.report()
    clock.now += delay
    await checker.report()

    assert calls == rounds


async def test_the_dependencies_are_always_the_four_names_in_order() -> None:
    async def fails() -> None:  # noqa: RUF029 - the checker awaits every check.
        raise RuntimeError

    report = await _checker(_checks(database=fails, task_manager=fails)).report()

    assert [d.name for d in report.dependencies] == ["database", "object_store", "task_manager", "task_manager_db"]


def test_the_checker_refuses_checks_that_are_not_the_four_names_in_order() -> None:
    checks = list(reversed(_checks()))

    with pytest.raises(ValueError, match="dependencies"):
        _checker(checks)


async def test_a_failed_check_logs_one_warning_with_the_component_and_category_only() -> None:
    async def fails() -> None:  # noqa: RUF029 - the checker awaits every check.
        message = f"connection to {CONNECTION_TEXT} failed: Connection refused"
        raise psycopg.OperationalError(message)

    with capture_logs() as logs:
        await _checker(_checks(database=fails)).report()

    warnings = [entry for entry in logs if entry["log_level"] == "warning"]
    assert len(warnings) == 1
    assert warnings[0]["component"] == "database"
    assert warnings[0]["category"] == "connection_refused"
    assert "hunter2" not in repr(logs)
    assert "db.internal" not in repr(logs)


# --- worker section (T008) -------------------------------------------------------------------

WORKER_A = "infrahub-sync-service-3f0c2a9e-6a1b-4c55-9d3e-2b7c1e0f4a11"
WORKER_B = "infrahub-sync-service-8b1d7e40-2c9f-4e0a-a6b3-5f2e9c7d1b08"
WORKER_C = "infrahub-sync-service-0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d"
WORKER_D = "infrahub-sync-service-11111111-2222-4333-8444-555555555555"


def _worker(index: int, name: str | None, *, age: timedelta | None, status: str = "online") -> PoolWorker:
    return PoolWorker(
        worker_id=f"00000000-0000-4000-8000-{index:012d}",
        status=status,
        last_heartbeat=None if age is None else NOW - age,
        heartbeat_interval_seconds=10.0,
        name=name,
    )


def test_a_known_pool_counts_live_workers_by_the_shared_rule() -> None:
    pool = _pool(
        _worker(1, WORKER_A, age=timedelta(seconds=5)),
        _worker(2, WORKER_B, age=timedelta(seconds=31)),
        _worker(3, WORKER_C, age=timedelta(seconds=5), status="offline"),
        queue_depth=2,
    )

    workers = workers_health(pool, NOW, minimum=1)

    assert workers.state == "known"
    assert workers.minimum == 1
    assert workers.live_workers == 1
    assert workers.queue_depth == 2
    # A and C share a heartbeat, so the name orders them.
    assert [(item.name, item.state) for item in workers.items] == [
        (WORKER_C, "stale"),
        (WORKER_A, "live"),
        (WORKER_B, "stale"),
    ]


def test_only_workers_with_a_heartbeat_within_24_hours_are_listed() -> None:
    pool = _pool(
        _worker(1, WORKER_A, age=timedelta(hours=24, seconds=1)),
        _worker(2, WORKER_B, age=timedelta(hours=23)),
        _worker(3, WORKER_C, age=None),
    )

    workers = workers_health(pool, NOW, minimum=1)

    assert [(item.name, item.state) for item in workers.items] == [(WORKER_B, "stale")]
    assert workers.items[0].last_heartbeat == NOW - timedelta(hours=23)


def test_workers_are_sorted_newest_heartbeat_first_then_by_name() -> None:
    pool = _pool(
        _worker(1, WORKER_B, age=timedelta(minutes=5)),
        _worker(2, WORKER_C, age=timedelta(minutes=1)),
        _worker(3, WORKER_A, age=timedelta(minutes=5)),
    )

    workers = workers_health(pool, NOW, minimum=1)

    assert [item.name for item in workers.items] == [WORKER_C, WORKER_A, WORKER_B]


@pytest.mark.parametrize(
    "name",
    [
        "acct_x",
        "laptop-01",
        None,
        "infrahub-sync-service-3F0C2A9E-6A1B-4C55-9D3E-2B7C1E0F4A11",
        f"{WORKER_A}\n",
        f"prefix-{WORKER_A}",
    ],
)
def test_a_name_that_is_not_a_sync_worker_name_is_shown_as_other(name: str | None) -> None:
    workers = workers_health(_pool(_worker(1, name, age=timedelta(seconds=5))), NOW, minimum=1)

    assert [item.name for item in workers.items] == ["other"]


def test_a_name_the_sync_worker_entrypoint_registers_is_shown_unchanged() -> None:
    name = service_worker_name()

    workers = workers_health(_pool(_worker(1, name, age=timedelta(seconds=5))), NOW, minimum=1)

    assert [item.name for item in workers.items] == [name]


def test_an_unavailable_pool_gives_an_unknown_worker_state() -> None:
    workers = workers_health(UNAVAILABLE_POOL, NOW, minimum=2)

    assert (workers.state, workers.minimum, workers.live_workers, workers.queue_depth, workers.items) == (
        "unknown",
        2,
        None,
        None,
        [],
    )


async def test_a_pool_read_past_the_limit_gives_an_unknown_worker_state() -> None:
    async def hangs(_now: datetime) -> PoolStatus:
        await asyncio.sleep(30)
        return _pool()

    report = await _checker(reader=hangs, check_timeout=0.2).report()

    assert report.workers.state == "unknown"
    assert report.workers.live_workers is None
    assert report.workers.queue_depth is None
    assert report.workers.items == []


@pytest.mark.parametrize(
    "error", [httpx.ConnectError("All connection attempts failed"), ConnectionRefusedError(61, "refused")]
)
async def test_a_pool_read_that_cannot_reach_the_server_gives_an_unknown_worker_state(error: Exception) -> None:
    async def unreachable(_now: datetime) -> PoolStatus:  # noqa: RUF029 - the checker awaits the reader.
        raise error

    report = await _checker(reader=unreachable).report()

    assert report.workers.state == "unknown"
    assert report.status == "unhealthy"


async def test_an_unexpected_error_of_the_pool_read_gives_an_unknown_worker_state_and_logs_its_type_only() -> None:
    async def fails(_now: datetime) -> PoolStatus:  # noqa: RUF029 - the checker awaits the reader.
        message = f"cannot open the client for {CONNECTION_TEXT}"
        raise RuntimeError(message)

    with capture_logs() as logs:
        report = await _checker(reader=fails).report()

    assert report.workers.state == "unknown"
    assert report.status == "unhealthy"
    assert all(d.status == "up" for d in report.dependencies)
    warnings = [entry for entry in logs if entry["log_level"] == "warning"]
    assert [(w["component"], w["category"], w["error_type"]) for w in warnings] == [
        ("workers", "unknown_error", "RuntimeError")
    ]
    assert "hunter2" not in repr(logs)
    assert "db.internal" not in repr(logs)


async def test_fewer_live_workers_than_the_minimum_is_unhealthy() -> None:
    report = await _checker(pool=_pool(_worker(1, WORKER_A, age=timedelta(seconds=5))), minimum=2).report()

    assert all(d.status == "up" for d in report.dependencies)
    assert report.workers.live_workers == 1
    assert report.status == "unhealthy"


async def test_an_unknown_worker_state_with_every_component_up_is_unhealthy() -> None:
    report = await _checker(pool=UNAVAILABLE_POOL).report()

    assert all(d.status == "up" for d in report.dependencies)
    assert report.status == "unhealthy"


async def test_one_live_and_three_stale_workers_are_healthy_with_a_minimum_of_one() -> None:
    pool = _pool(
        _worker(1, WORKER_A, age=timedelta(seconds=5)),
        _worker(2, WORKER_B, age=timedelta(minutes=10)),
        _worker(3, WORKER_C, age=timedelta(hours=2)),
        _worker(4, WORKER_D, age=timedelta(hours=20)),
    )

    report = await _checker(pool=pool).report()

    assert report.workers.live_workers == 1
    assert [item.state for item in report.workers.items] == ["live", "stale", "stale", "stale"]
    assert report.status == "healthy"


# --- check factories (T016) ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("factory", "path"), [(task_manager_check, "/api/health"), (task_manager_db_check, "/api/ready")]
)
async def test_a_prefect_check_passes_on_a_2xx_answer(factory: CheckFactory, path: str) -> None:
    seen: list[str] = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(200, json=True)

    check = factory("http://prefect:4200/api/", 3, transport=httpx.MockTransport(answer))
    await check()

    assert seen == [path]


@pytest.mark.parametrize("factory", [task_manager_check, task_manager_db_check])
async def test_a_prefect_check_raises_unavailable_on_a_503(factory: CheckFactory) -> None:
    check = factory("http://prefect:4200/api", 3, transport=httpx.MockTransport(lambda _r: httpx.Response(503)))

    with pytest.raises(DependencyUnavailableError) as caught:
        await check()

    assert classify_error(caught.value) is ErrorCategory.UNAVAILABLE
    assert "prefect" not in str(caught.value)


async def test_the_object_store_check_calls_head_bucket_without_retries_and_with_the_limit() -> None:
    built: dict[str, object] = {}
    heads: list[str] = []

    class FakeS3:
        def head_bucket(self, *, Bucket: str) -> None:  # noqa: N803, PLR6301 - the boto3 method and keyword.
            heads.append(Bucket)

    def builder(service: str, **arguments: object) -> FakeS3:
        built["service"] = service
        built.update(arguments)
        return FakeS3()

    check = object_store_check(
        {"INFRAHUB_SYNC_S3_ENDPOINT_URL": "http://minio:9000"}, "sync-bucket", 4, client_builder=builder
    )
    await check()

    config = built["config"]
    assert isinstance(config, Config)
    # botocore sets the options as instance attributes, which type checkers do not see.
    options = vars(config)
    assert built["service"] == "s3"
    assert built["endpoint_url"] == "http://minio:9000"
    assert options["connect_timeout"] == 4
    assert options["read_timeout"] == 4
    assert options["retries"] == {"max_attempts": 0}
    # The addressing style chosen by the S3 settings is kept.
    assert options["s3"] == {"addressing_style": "path"}
    assert heads == ["sync-bucket"]


async def test_the_object_store_client_is_built_once_for_every_round() -> None:
    builds = 0

    class FakeS3:
        def head_bucket(self, *, Bucket: str) -> None:  # noqa: N803, PLR6301 - the boto3 method and keyword.
            del Bucket

    def builder(_service: str, **_arguments: object) -> FakeS3:
        nonlocal builds
        builds += 1
        return FakeS3()

    check = object_store_check({}, "sync-bucket", 3, client_builder=builder)
    await check()
    await check()

    assert builds == 1


async def test_the_object_store_check_starts_no_second_call_while_the_first_has_not_returned() -> None:
    started = threading.Event()
    release = threading.Event()
    calls: list[str] = []

    class BlockedS3:
        def head_bucket(self, *, Bucket: str) -> None:  # noqa: N803, PLR6301 - the boto3 method and keyword.
            calls.append(Bucket)
            started.set()
            release.wait(timeout=10)

    check = object_store_check({}, "sync-bucket", 1, client_builder=lambda _service, **_arguments: BlockedS3())
    try:
        first = asyncio.ensure_future(check())
        assert await asyncio.to_thread(started.wait, 5)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first

        # The next round finds the first call still running: it reports a timeout at once.
        with pytest.raises(CheckStillRunningError) as still_running:
            await asyncio.wait_for(check(), 1)
        assert classify_error(still_running.value) is ErrorCategory.TIMEOUT
        assert calls == ["sync-bucket"]
    finally:
        release.set()

    # Once the first call returned, the next round calls the object store again.
    deadline = time.monotonic() + 5
    while True:
        try:
            await check()
            break
        except CheckStillRunningError:
            if time.monotonic() > deadline:
                raise
            await asyncio.sleep(0.01)
    assert calls == ["sync-bucket", "sync-bucket"]


async def test_the_object_store_check_recovers_after_a_call_cancelled_before_it_started(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    submitted: list[Future[None]] = []

    class FakeS3:
        def head_bucket(self, *, Bucket: str) -> None:  # noqa: N803, PLR6301 - the boto3 method and keyword.
            calls.append(Bucket)

    def pending_submit(_executor: ThreadPoolExecutor, *_args: object, **_kwargs: object) -> Future[None]:
        # A call still queued: no thread has picked it up.
        future: Future[None] = Future()
        submitted.append(future)
        return future

    check = object_store_check({}, "sync-bucket", 1, client_builder=lambda _service, **_arguments: FakeS3())
    with monkeypatch.context() as patch:
        patch.setattr(ThreadPoolExecutor, "submit", pending_submit)
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(check(), 0.01)
    assert submitted[0].cancelled()

    await check()

    assert calls == ["sync-bucket"]


async def test_the_database_check_passes_no_server_options_so_the_url_options_are_kept(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connects: list[tuple[str, dict[str, object]]] = []
    queries: list[str] = []

    class FakeConnection:
        async def __aenter__(self) -> Self:
            return self

        async def __aexit__(self, *_exc: object) -> None:
            pass

        async def execute(self, query: str) -> None:  # noqa: PLR6301 - psycopg's connection method.
            queries.append(query)

    async def connect(conninfo: str, **kwargs: object) -> FakeConnection:  # noqa: RUF029 - psycopg's is async.
        connects.append((conninfo, kwargs))
        return FakeConnection()

    monkeypatch.setattr(psycopg.AsyncConnection, "connect", connect)
    url = "postgresql://sync@db.internal/sync?options=-csearch_path%3Dsync"

    await database_check(url, 1)()

    assert connects == [(url, {"autocommit": True, "connect_timeout": 2})]
    assert queries == ["SELECT 1"]
