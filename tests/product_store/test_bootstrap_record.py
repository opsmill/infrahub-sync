"""The `bootstrap_record` table: the Sync version and work pool the bootstrap set up (spec 018 FR-007b).

SQLite covers every case. The same cases run against a real PostgreSQL server when
``PRODUCT_STORE_TEST_POSTGRESQL_DSN`` names one (``-m integration``), confined to a generated
schema; see ``tests/product_store/postgresql_isolation``.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, cast

import pytest

from infrahub_sync.product_store import (
    BootstrapRecord,
    DBAPIConnection,
    ProductProjection,
    ProductStoreProviderError,
    read_bootstrap_record,
)
from infrahub_sync.product_store.store import FileArtifactStore, PostgreSQLRunStore, SQLiteRunStore
from tests.product_store.postgresql_isolation import postgresql_schema_fixture

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

_FIRST = datetime(2026, 11, 16, 9, tzinfo=timezone.utc)
_SECOND = datetime(2026, 11, 16, 10, tzinfo=timezone.utc)

_postgresql_schema = postgresql_schema_fixture("the bootstrap record PostgreSQL parameter")


@dataclass(frozen=True)
class _Profile:
    """One database: a plain connection to it, and the store that initializes its schema."""

    connect: Callable[[], DBAPIConnection]
    projection: Callable[[], ProductProjection]
    tables_query: str


@pytest.fixture(params=("sqlite", pytest.param("postgresql", marks=pytest.mark.integration)))
def profile(request: pytest.FixtureRequest, tmp_path: Path) -> _Profile:
    """An empty database per test, on each provider profile."""
    artifacts = FileArtifactStore(tmp_path / "objects")
    if request.param == "sqlite":
        database = tmp_path / "records.sqlite3"

        def sqlite_connect() -> DBAPIConnection:
            return cast("DBAPIConnection", sqlite3.connect(database))

        return _Profile(
            connect=sqlite_connect,
            projection=lambda: ProductProjection(SQLiteRunStore(database), artifacts),
            tables_query="SELECT COUNT(*) FROM sqlite_master WHERE type = 'table'",
        )
    schema = request.getfixturevalue("_postgresql_schema")
    schema.reset()
    # pylint: disable-next=import-outside-toplevel,import-error
    import psycopg  # ty: ignore[unresolved-import] - TODO: optional service dependency

    from infrahub_sync.service.storage import PsycopgConnectionFactory

    def postgresql_connect() -> Any:  # noqa: ANN401 - the store's own DB-API connection protocol.
        return PsycopgConnectionFactory(psycopg.connect)(schema.dsn)

    return _Profile(
        connect=postgresql_connect,
        projection=lambda: ProductProjection(PostgreSQLRunStore(postgresql_connect), artifacts),
        tables_query="SELECT COUNT(*) FROM pg_tables WHERE schemaname = current_schema()",
    )


def _read(profile: _Profile) -> BootstrapRecord | None:
    connection = profile.connect()
    try:
        return read_bootstrap_record(connection)
    finally:
        connection.close()


def _count(profile: _Profile, query: str) -> int:
    connection = profile.connect()
    try:
        cursor = connection.cursor()
        cursor.execute(query)
        row = cursor.fetchone()
        cursor.close()
        connection.rollback()
    finally:
        connection.close()
    assert row is not None
    return int(row[0])


def test_a_database_without_the_table_holds_no_record(profile: _Profile) -> None:
    assert _read(profile) is None


def test_reading_creates_no_table(profile: _Profile) -> None:
    _read(profile)

    assert _count(profile, profile.tables_query) == 0


def test_a_read_without_the_table_leaves_the_connection_usable(profile: _Profile) -> None:
    connection = profile.connect()
    try:
        # A second read on the same connection works: the first left no aborted transaction.
        assert read_bootstrap_record(connection) is None
        assert read_bootstrap_record(connection) is None
    finally:
        connection.close()


def test_an_initialized_schema_without_a_bootstrap_holds_no_record(profile: _Profile) -> None:
    profile.projection()

    assert _read(profile) is None


def test_the_written_record_is_read_back(profile: _Profile) -> None:
    written = profile.projection().write_bootstrap_record("3.0.0a5", "infrahub-sync", completed_at=_FIRST)

    assert (
        _read(profile)
        == written
        == BootstrapRecord(sync_version="3.0.0a5", work_pool="infrahub-sync", completed_at=_FIRST)
    )


def test_a_second_write_replaces_the_one_row(profile: _Profile) -> None:
    projection = profile.projection()
    projection.write_bootstrap_record("3.0.0a5", "infrahub-sync", completed_at=_FIRST)
    projection.write_bootstrap_record("3.0.0a6", "other-pool", completed_at=_SECOND)

    assert _read(profile) == BootstrapRecord(sync_version="3.0.0a6", work_pool="other-pool", completed_at=_SECOND)
    assert _count(profile, "SELECT COUNT(*) FROM bootstrap_record") == 1


def test_a_record_survives_a_later_schema_initialization(profile: _Profile) -> None:
    profile.projection().write_bootstrap_record("3.0.0a5", "infrahub-sync", completed_at=_FIRST)

    profile.projection()

    assert _read(profile) == BootstrapRecord(sync_version="3.0.0a5", work_pool="infrahub-sync", completed_at=_FIRST)


class _RefusingConnection:
    """A connection whose one statement fails with a PostgreSQL SQLSTATE, as `_PsycopgConnection` reports it."""

    def __init__(self, sqlstate: str) -> None:
        self.sqlstate = sqlstate
        self.rolled_back = 0
        self.cursor_closed = False

    def cursor(self) -> _RefusingConnection:
        return self

    def execute(self, _statement: str, _parameters: Any = ()) -> None:  # noqa: ANN401 - DB-API parameters.
        raise ProductStoreProviderError(sqlstate=self.sqlstate)

    @staticmethod
    def fetchone() -> None:
        return None

    def rollback(self) -> None:
        self.rolled_back += 1

    def close(self) -> None:
        self.cursor_closed = True


def test_an_undefined_table_answer_is_no_record_and_is_rolled_back() -> None:
    connection = _RefusingConnection("42P01")

    assert read_bootstrap_record(cast("DBAPIConnection", connection)) is None
    assert connection.rolled_back == 1
    assert connection.cursor_closed


def test_any_other_database_failure_is_raised_after_a_rollback() -> None:
    connection = _RefusingConnection("08006")

    with pytest.raises(ProductStoreProviderError) as raised:
        read_bootstrap_record(cast("DBAPIConnection", connection))

    assert raised.value.sqlstate == "08006"
    assert connection.rolled_back == 1
