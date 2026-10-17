"""The worker publishes which credential variables it holds, by name only (spec 009 FR-033, T086).

`configs validate --branch` compares a package's `$credential` references with the names a live
worker published. The record is the only place outside the worker process that says which
`INFRAHUB_SYNC_CREDENTIAL_*` variables the worker holds, so it must never hold a value. SQLite
covers every case; the store cases also run against a real PostgreSQL server when
``PRODUCT_STORE_TEST_POSTGRESQL_DSN`` names one, inside a generated schema that the fixture
creates and drops (see ``tests/product_store/postgresql_isolation``).
"""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID

import pytest

pytest.importorskip("prefect")

from infrahub_sync.product_store import ProductProjection, ProductStoreProviderError, WorkerEnvironment
from infrahub_sync.product_store.store import FileArtifactStore, PostgreSQLRunStore, SQLiteRunStore
from infrahub_sync.service import worker as service_worker
from infrahub_sync.service.storage import ServiceStorageStartupError
from infrahub_sync.service.worker import ServiceProcessWorker
from infrahub_sync.service.worker_environment import (
    LIVE_WORKER_MAX_AGE,
    WorkerEnvironmentPublisher,
    credential_names,
    latest_live_worker_environment,
)
from tests.plan.problem_fixtures import (
    SHORT_CREDENTIAL,
    SHORT_CREDENTIAL_NAME,
    SPECIAL_CREDENTIAL,
    SPECIAL_CREDENTIAL_NAME,
)
from tests.product_store.postgresql_isolation import postgresql_schema_fixture

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from prefect.client.schemas.objects import WorkPool

POOL_NAME = "service-pool"
POOL_ID = UUID("e0679e8a-9460-4ca7-8bf1-70bf967eed2d")
WORKER_A = "infrahub-sync-service-0b6f6c1e-3d2a-4c55-9a51-7e2f0d9a1c11"
WORKER_B = "infrahub-sync-service-5d0e8b7a-61f4-4b3c-8e2d-9c1a7f3e2b44"
NOW = datetime(2026, 10, 17, 9, 30, tzinfo=timezone.utc)
UNSET_NAME = "INFRAHUB_SYNC_CREDENTIAL_PROBLEM_FIXTURE_UNSET"
EMPTY_NAME = "INFRAHUB_SYNC_CREDENTIAL_PROBLEM_FIXTURE_EMPTY"

# Two set credentials, one empty one, and two variables outside the credential namespace.
WORKER_ENVIRONMENT = {
    SHORT_CREDENTIAL_NAME: SHORT_CREDENTIAL,
    SPECIAL_CREDENTIAL_NAME: SPECIAL_CREDENTIAL,
    EMPTY_NAME: "",
    "INFRAHUB_SYNC_DATABASE_URL": "postgresql://sync:database-password@db/sync",
    "INFRAHUB_SYNC_CREDENTIAL_": "a prefix without a name",
}

_postgresql_schema = postgresql_schema_fixture("the worker environment record's PostgreSQL parameter")


@pytest.fixture(params=("sqlite", pytest.param("postgresql", marks=pytest.mark.integration)))
def store(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[tuple[ProductProjection, Any]]:
    """Yield a projection and a function that returns every stored row as text."""
    artifacts = FileArtifactStore(tmp_path / "objects")
    if request.param == "sqlite":
        database = tmp_path / "records.sqlite3"

        def sqlite_rows() -> list[tuple[str, ...]]:
            with sqlite3.connect(database) as connection:
                rows = connection.execute("SELECT * FROM worker_environment").fetchall()
            return [tuple(str(value) for value in row) for row in rows]

        yield ProductProjection(SQLiteRunStore(database), artifacts), sqlite_rows
        return
    schema = request.getfixturevalue("_postgresql_schema")
    # pylint: disable-next=import-outside-toplevel
    import psycopg

    from infrahub_sync.service.storage import PsycopgConnectionFactory

    def connect() -> Any:  # noqa: ANN401 - the store's own DB-API connection protocol.
        return PsycopgConnectionFactory(psycopg.connect)(schema.dsn)

    def postgresql_rows() -> list[tuple[str, ...]]:
        connection = connect()
        try:
            cursor = connection.cursor()
            cursor.execute("SELECT * FROM worker_environment")
            rows = cursor.fetchall()
        finally:
            connection.close()
        return [tuple(str(value) for value in row) for row in rows]

    yield ProductProjection(PostgreSQLRunStore(connect), artifacts), postgresql_rows
    # The generated schema lives for the whole module, so each case removes the records it
    # published; otherwise the next case would read them as a live worker's.
    connection = connect()
    try:
        cursor = connection.cursor()
        cursor.execute("DELETE FROM worker_environment")
        connection.commit()
    except psycopg.errors.UndefinedTable:
        connection.rollback()
    finally:
        connection.close()


def _publisher(projection: ProductProjection, worker_id: str, *, at: datetime) -> WorkerEnvironmentPublisher:
    return WorkerEnvironmentPublisher(
        worker_id,
        projection_factory=lambda: projection,
        environ=WORKER_ENVIRONMENT,
        clock=lambda: at,
    )


def test_only_set_and_non_empty_credential_variables_are_named() -> None:
    assert credential_names(WORKER_ENVIRONMENT) == (SPECIAL_CREDENTIAL_NAME, SHORT_CREDENTIAL_NAME)


def test_the_stored_row_holds_names_and_no_value(store: tuple[ProductProjection, Any]) -> None:
    projection, rows = store

    assert _publisher(projection, WORKER_A, at=NOW).publish() is True

    stored = rows()
    assert len(stored) == 1
    text = " ".join(stored[0])
    assert SHORT_CREDENTIAL_NAME in text
    assert SPECIAL_CREDENTIAL_NAME in text
    assert EMPTY_NAME not in text
    assert UNSET_NAME not in text
    # The values, and the escaped forms that JSON encoding would give the special one.
    for value in (SHORT_CREDENTIAL, SPECIAL_CREDENTIAL, 'p\\"ss', "wo,rd-2026", "database-password"):
        assert value not in text


def test_each_publication_replaces_the_worker_record(store: tuple[ProductProjection, Any]) -> None:
    projection, rows = store
    _publisher(projection, WORKER_A, at=NOW - timedelta(seconds=20)).publish()
    later = WorkerEnvironmentPublisher(
        WORKER_A,
        projection_factory=lambda: projection,
        environ={SHORT_CREDENTIAL_NAME: SHORT_CREDENTIAL},
        clock=lambda: NOW,
    )

    later.publish()

    assert len(rows()) == 1
    assert latest_live_worker_environment(projection, now=NOW) == WorkerEnvironment(
        worker_id=WORKER_A, credential_names=(SHORT_CREDENTIAL_NAME,), observed_at=NOW
    )


def test_the_most_recent_record_of_a_live_worker_is_the_one_compared(store: tuple[ProductProjection, Any]) -> None:
    projection, _rows = store
    WorkerEnvironmentPublisher(
        WORKER_B,
        projection_factory=lambda: projection,
        environ={},
        clock=lambda: NOW - timedelta(seconds=30),
    ).publish()
    _publisher(projection, WORKER_A, at=NOW - timedelta(seconds=5)).publish()

    latest = latest_live_worker_environment(projection, now=NOW)

    assert latest is not None
    assert latest.worker_id == WORKER_A
    assert latest.credential_names == (SPECIAL_CREDENTIAL_NAME, SHORT_CREDENTIAL_NAME)


def test_a_worker_that_stopped_publishing_is_not_live(store: tuple[ProductProjection, Any]) -> None:
    projection, _rows = store
    _publisher(projection, WORKER_A, at=NOW - LIVE_WORKER_MAX_AGE - timedelta(seconds=1)).publish()

    assert latest_live_worker_environment(projection, now=NOW) is None


def test_no_record_means_no_live_worker_environment(store: tuple[ProductProjection, Any]) -> None:
    projection, _rows = store

    assert latest_live_worker_environment(projection, now=NOW) is None


@pytest.mark.parametrize(
    "failure",
    [
        ServiceStorageStartupError(),
        ProductStoreProviderError(sqlstate="08006"),
        ValueError("INFRAHUB_SYNC_DATABASE_URL must be set"),
    ],
    ids=["storage-startup", "provider", "missing-setting"],
)
def test_a_publication_that_cannot_reach_the_store_does_not_stop_the_worker(failure: Exception) -> None:
    def unavailable() -> ProductProjection:
        raise failure

    publisher = WorkerEnvironmentPublisher(WORKER_A, projection_factory=unavailable, environ=WORKER_ENVIRONMENT)

    assert publisher.publish() is False


def test_the_worker_publishes_at_start_after_the_trust_store(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    class RecordingPublisher:
        def __init__(self, worker_id: str) -> None:
            calls.append(f"publisher for {worker_id}")

        def publish(self) -> bool:  # noqa: PLR6301 - stands in for the instance method.
            calls.append("publish")
            return True

    class StoppedWorker:
        def __init__(self, **settings: Any) -> None:  # noqa: ANN401 - the worker's own keywords.
            calls.append(f"worker {settings['name']}")
            assert settings["environment_publisher"].__self__.__class__ is RecordingPublisher

        async def start(self) -> None:  # noqa: PLR6301 - stands in for the instance method.
            calls.append("start")

    monkeypatch.setattr(service_worker, "install_custom_ca_bundle", lambda: calls.append("trust store"))
    monkeypatch.setattr(service_worker, "service_worker_name", lambda: WORKER_A)
    monkeypatch.setattr(service_worker, "WorkerEnvironmentPublisher", RecordingPublisher)
    monkeypatch.setattr(service_worker, "ServiceProcessWorker", StoppedWorker)

    assert service_worker.main(["--pool", POOL_NAME]) == 0

    assert calls == ["trust store", f"publisher for {WORKER_A}", "publish", f"worker {WORKER_A}", "start"]


def test_the_worker_publishes_on_each_poll() -> None:
    published: list[bool] = []

    def publish() -> bool:
        published.append(True)
        return True

    worker = ServiceProcessWorker(work_pool_name=POOL_NAME, name=WORKER_A, environment_publisher=publish)
    worker._work_pool = cast(
        "WorkPool",
        SimpleNamespace(id=POOL_ID, name=POOL_NAME, base_job_template=worker.get_default_base_job_template()),
    )

    async def poll_twice() -> None:
        # No identity is resolved, so each poll submits nothing; it still publishes.
        assert await worker.get_and_submit_flow_runs() == []
        assert await worker.get_and_submit_flow_runs() == []

    asyncio.run(poll_twice())

    assert published == [True, True]


class _LockedStore:
    """A projection whose database driver reports a locked database on every write."""

    def record_worker_environment(self, record: WorkerEnvironment) -> None:  # noqa: PLR6301
        del record
        msg = f"database is locked: {SPECIAL_CREDENTIAL}"
        raise sqlite3.OperationalError(msg)


def test_a_driver_error_at_start_does_not_stop_the_worker(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    started: list[str] = []

    class StoppedWorker:
        def __init__(self, **settings: Any) -> None:  # noqa: ANN401 - the worker's own keywords.
            del settings

        async def start(self) -> None:  # noqa: PLR6301 - stands in for the instance method.
            started.append("start")

    def locked_publisher(worker_id: str) -> WorkerEnvironmentPublisher:
        return WorkerEnvironmentPublisher(
            worker_id, projection_factory=lambda: cast("ProductProjection", _LockedStore()), environ=WORKER_ENVIRONMENT
        )

    monkeypatch.setattr(service_worker, "install_custom_ca_bundle", lambda: None)
    monkeypatch.setattr(service_worker, "service_worker_name", lambda: WORKER_A)
    monkeypatch.setattr(service_worker, "WorkerEnvironmentPublisher", locked_publisher)
    monkeypatch.setattr(service_worker, "ServiceProcessWorker", StoppedWorker)

    with caplog.at_level("WARNING", logger="infrahub_sync.service.worker_environment"):
        assert service_worker.main(["--pool", POOL_NAME]) == 0

    assert started == ["start"]
    assert "OperationalError" in caplog.text
    assert "database is locked" not in caplog.text
    assert SPECIAL_CREDENTIAL not in caplog.text


def test_a_driver_error_on_a_poll_does_not_end_the_poll_loop(caplog: pytest.LogCaptureFixture) -> None:
    publisher = WorkerEnvironmentPublisher(
        WORKER_A, projection_factory=lambda: cast("ProductProjection", _LockedStore()), environ=WORKER_ENVIRONMENT
    )
    worker = ServiceProcessWorker(work_pool_name=POOL_NAME, name=WORKER_A, environment_publisher=publisher.publish)
    worker._work_pool = cast(
        "WorkPool",
        SimpleNamespace(id=POOL_ID, name=POOL_NAME, base_job_template=worker.get_default_base_job_template()),
    )

    async def poll_twice() -> None:
        assert await worker.get_and_submit_flow_runs() == []
        assert await worker.get_and_submit_flow_runs() == []

    with caplog.at_level("WARNING", logger="infrahub_sync.service.worker_environment"):
        asyncio.run(poll_twice())

    warnings = [record.getMessage() for record in caplog.records if record.name.endswith("worker_environment")]
    assert len(warnings) == 2
    assert all("OperationalError" in message for message in warnings)
    assert "database is locked" not in caplog.text
    assert SPECIAL_CREDENTIAL not in caplog.text
