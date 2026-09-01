# pylint: disable=duplicate-code
# EXPECTED_PUBLIC_NAMES below intentionally mirrors infrahub_sync/product_store/__init__.py's
# __all__ as a hand-maintained, independent list; pylint's similarity checker flags that overlap.
from __future__ import annotations

import os
import re
import sqlite3
import subprocess  # noqa: S404 - fixed local interpreter probes restart durability.
import sys
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from threading import Barrier, Event, local
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from infrahub_sync import product_store
from infrahub_sync.configuration import ConfigurationPackage, CredentialConfigurationError
from infrahub_sync.product_store import (
    ArtifactReference,
    ArtifactUnavailableError,
    AuditEvent,
    ConfigurationNotFoundError,
    ConfigurationSummary,
    ConfigurationVersion,
    ConfigurationVersionAllocationError,
    DBAPIConnection,
    DuplicateArtifactError,
    DuplicateConfigurationError,
    DuplicatePrefectExecutionError,
    DuplicateRunError,
    MutationReceipt,
    PrefectExecutionLink,
    ProductProjection,
    ProductRun,
    RunNotFoundError,
    WriteAdmissionConflictError,
    local_product_projection,
)
from infrahub_sync.product_store import store as product_store_store
from infrahub_sync.product_store.store import FileArtifactStore, PostgreSQLRunStore, S3ArtifactStore, SQLiteRunStore

# This intentionally mirrors infrahub_sync/product_store/__init__.py's __all__, hand-maintained
# so a change to the module's exports has to touch this list too.
EXPECTED_PUBLIC_NAMES = {
    "ArtifactReference",
    "ArtifactUnavailableError",
    "AuditEvent",
    "ConfigurationNotFoundError",
    "ConfigurationSummary",
    "ConfigurationVersion",
    "ConfigurationVersionAllocationError",
    "DBAPIConnection",
    "DuplicateArtifactError",
    "DuplicateConfigurationError",
    "DuplicatePrefectExecutionError",
    "DuplicateRunError",
    "ExecutionFinishWriteback",
    "ExecutionMergeWriteback",
    "ExecutionWriteback",
    "LookupResult",
    "MutationReceipt",
    "PrefectExecutionLink",
    "ProductProjection",
    "ProductStoreProviderError",
    "ProductRun",
    "RunNotFoundError",
    "S3Client",
    "WriteAdmissionConflictError",
    "local_product_projection",
    "production_product_projection",
}


_POSTGRESQL_EMULATION_CONSTRAINTS_TABLE = "_fake_postgresql_constraints"


class _CursorAdapter:
    """DB-API cursor over a literal SQLite file for the ``%s``-placeholder "production" profile.

    Genuine CRUD statements pass straight through with ``%s`` translated to ``?``, so every
    contract test below still exercises a real database engine. SQLite has neither
    ``information_schema`` nor ``ALTER TABLE ... ADD CONSTRAINT``, so this adapter emulates
    only those two PostgreSQL-only statements the store's dialect-specific schema bootstrap
    issues; it does not model transactional DDL or abort-on-error (see
    ``_FakePostgreSQLDatabase`` for the fake that does, used to prove the bootstrap fix itself).
    """

    def __init__(self, cursor: sqlite3.Cursor) -> None:
        self._cursor = cursor
        self._fake_rows: tuple[tuple[Any, ...], ...] | None = None

    def execute(self, operation: str, parameters: Sequence[Any] = ()):
        self._fake_rows = None
        stripped = operation.strip()
        if "information_schema.columns" in stripped:
            table_name = parameters[0] if parameters else "product_runs"
            self._cursor.execute(f"PRAGMA table_info({table_name})")
            # ``information_schema.columns`` is always selected as (column_name, is_nullable);
            # PRAGMA's ``notnull`` flag carries the same fact for the literal table. The
            # ``DROP NOT NULL`` below is a no-op here, so a legacy column keeps reporting "NO".
            self._fake_rows = tuple((str(row[1]), "NO" if row[3] else "YES") for row in self._cursor.fetchall())
            return self
        if "information_schema.table_constraints" in stripped:
            self._cursor.execute(
                f"SELECT 1 FROM {_POSTGRESQL_EMULATION_CONSTRAINTS_TABLE} WHERE constraint_name = ?",  # noqa: S608
                (parameters[-1],),
            )
            self._fake_rows = tuple(self._cursor.fetchall())
            return self
        if "ADD CONSTRAINT" in stripped:
            constraint_name = stripped.split("ADD CONSTRAINT", 1)[1].split()[0]
            try:
                self._cursor.execute(
                    f"INSERT INTO {_POSTGRESQL_EMULATION_CONSTRAINTS_TABLE} (constraint_name) VALUES (?)",  # noqa: S608
                    (constraint_name,),
                )
            except sqlite3.IntegrityError as exc:
                raise _FakeDriverError(sqlstate="42710") from exc
            return self
        if "ALTER COLUMN" in stripped and "DROP NOT NULL" in stripped:
            return self
        stripped = stripped.replace(
            "CAST(COALESCE(submitted_at, last_observed_at) AS TIMESTAMPTZ)",
            "infrahub_sync_execution_timestamp_microseconds(COALESCE(submitted_at, last_observed_at))",
        )
        stripped = stripped.replace(
            "CAST(%s AS TIMESTAMPTZ)",
            "infrahub_sync_execution_timestamp_microseconds(?)",
        )
        self._cursor.execute(stripped.replace("%s", "?"), parameters)
        return self

    @property
    def rowcount(self) -> int:
        if self._fake_rows is not None:
            return len(self._fake_rows)
        return self._cursor.rowcount

    def fetchone(self):
        if self._fake_rows is not None:
            return self._fake_rows[0] if self._fake_rows else None
        return self._cursor.fetchone()

    def fetchall(self):
        if self._fake_rows is not None:
            return self._fake_rows
        return self._cursor.fetchall()

    def close(self) -> None:
        self._cursor.close()


class _ConnectionAdapter:
    def __init__(self, path: Path) -> None:
        self._connection = sqlite3.connect(path)
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.create_function(
            "infrahub_sync_execution_timestamp_microseconds",
            1,
            product_store_store._execution_timestamp_microseconds,
            deterministic=True,
        )
        self._connection.execute(
            f"CREATE TABLE IF NOT EXISTS {_POSTGRESQL_EMULATION_CONSTRAINTS_TABLE} (constraint_name TEXT PRIMARY KEY)"
        )
        self._connection.commit()

    def cursor(self) -> _CursorAdapter:
        return _CursorAdapter(self._connection.cursor())

    def commit(self) -> None:
        self._connection.commit()

    def rollback(self) -> None:
        self._connection.rollback()

    def close(self) -> None:
        self._connection.close()


class _FakeDriverError(Exception):
    def __init__(
        self,
        *,
        sqlstate: str | None = None,
        pgcode: str | None = None,
        diagnostic_sqlstate: str | None = None,
    ) -> None:
        super().__init__("fake database error")
        self.sqlstate = sqlstate
        self.pgcode = pgcode
        self.diag = SimpleNamespace(sqlstate=diagnostic_sqlstate)


class _FakeSQLiteIntegrityError(sqlite3.IntegrityError):
    sqlite_errorcode: int


class _FakeS3:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}

    def put(self, *, bucket: str, key: str, data: bytes, if_absent: bool = False) -> None:
        marker = (bucket, key)
        if if_absent and marker in self.objects:
            raise DuplicateArtifactError(key)
        self.objects[marker] = data

    def get(self, *, bucket: str, key: str) -> bytes | None:
        return self.objects.get((bucket, key))

    def copy(self, *, bucket: str, source: str, destination: str) -> None:
        self.objects[bucket, destination] = self.objects[bucket, source]

    def delete(self, *, bucket: str, key: str) -> None:
        self.objects.pop((bucket, key), None)


class _FailingFileStore(FileArtifactStore):
    def _publication_checkpoint(self) -> None:  # noqa: PLR6301 - overrides the provider fault seam.
        msg = "injected interruption"
        raise OSError(msg)


class _FailingS3Store(S3ArtifactStore):
    def _publication_checkpoint(self) -> None:  # noqa: PLR6301 - overrides the provider fault seam.
        msg = "injected interruption"
        raise OSError(msg)


class _FailingMarkSQLiteStore(SQLiteRunStore):
    def mark_artifact_published(self, reference: ArtifactReference) -> None:  # noqa: ARG002, PLR6301
        msg = "injected mark failure"
        raise OSError(msg)


class _FailingMarkPostgreSQLStore(PostgreSQLRunStore):
    def mark_artifact_published(self, reference: ArtifactReference) -> None:  # noqa: ARG002, PLR6301
        msg = "injected mark failure"
        raise OSError(msg)


class _CountingArtifactStore:
    def __init__(self, store: FileArtifactStore | S3ArtifactStore) -> None:
        self._store = store
        self.publish_calls = 0

    def publish(self, reference: ArtifactReference, data: bytes) -> None:
        self.publish_calls += 1
        self._store.publish(reference, data)

    def lookup(self, reference: ArtifactReference) -> product_store.LookupResult[bytes]:
        return self._store.lookup(reference)


class _PositionConflictOnceSQLiteStore(SQLiteRunStore):
    def __init__(self, database: Path) -> None:
        self.insert_calls = 0
        super().__init__(database)

    def _insert_prefect_execution(self, cursor, run_id, link, position):
        self.insert_calls += 1
        if self.insert_calls == 1:
            error = _FakeSQLiteIntegrityError("synthetic position race")
            error.sqlite_errorcode = 2067
            raise error
        return super()._insert_prefect_execution(cursor, run_id, link, position)


class _ChildConflictSQLiteStore(SQLiteRunStore):
    def _insert_prefect_execution(self, cursor, run_id, link, position):  # noqa: ARG002, PLR6301
        error = _FakeSQLiteIntegrityError("synthetic child uniqueness failure")
        error.sqlite_errorcode = 2067
        raise error


class _AlwaysConflictingConfigurationVersionStore(SQLiteRunStore):
    """Forces every configuration-version insert to lose the race, deterministically
    exhausting ``_CONFIGURATION_VERSION_ATTEMPTS`` without needing real concurrency."""

    def _insert_configuration_version_row(self, cursor, version):  # noqa: ARG002, PLR6301
        error = _FakeSQLiteIntegrityError("synthetic version race")
        error.sqlite_errorcode = 2067
        raise error


class _RoundCountingSQLiteStore(SQLiteRunStore):
    """Records, per calling thread, how many insert attempts the current
    ``add_configuration_version`` call has made -- the observable a mutant that retries
    before re-querying by checksum inflates without changing any other observable."""

    def __init__(self, database: Path) -> None:
        self.version_insert_rounds = local()
        super().__init__(database)

    def _insert_configuration_version_row(self, cursor, version) -> None:
        self.version_insert_rounds.count = getattr(self.version_insert_rounds, "count", 0) + 1
        super()._insert_configuration_version_row(cursor, version)


class _RoundCountingPostgreSQLRunStore(PostgreSQLRunStore):
    """PostgreSQL-profile counterpart of ``_RoundCountingSQLiteStore``."""

    def __init__(self, connect) -> None:
        self.version_insert_rounds = local()
        super().__init__(connect)

    def _insert_configuration_version_row(self, cursor, version) -> None:
        self.version_insert_rounds.count = getattr(self.version_insert_rounds, "count", 0) + 1
        super()._insert_configuration_version_row(cursor, version)


class _DeleteBeforeFinishSQLiteStore(SQLiteRunStore):
    def __init__(self, database: Path) -> None:
        self._database = database
        super().__init__(database)

    def finish(  # noqa: PLR0913
        self,
        run_id: str,
        *,
        phase: str,
        outcome: str,
        finished_at: datetime,
        summary: Mapping[str, Any],
        results: Mapping[str, Any],
    ) -> None:
        with sqlite3.connect(self._database) as connection:
            connection.execute("DELETE FROM product_runs WHERE run_id = ?", (run_id,))
        super().finish(
            run_id,
            phase=phase,
            outcome=outcome,
            finished_at=finished_at,
            summary=summary,
            results=results,
        )


class _NoHydratingLookupSQLiteStore(SQLiteRunStore):
    def lookup(self, run_id: str):  # noqa: PLR6301 - override is the assertion seam.
        msg = f"unexpected hydration for {run_id}"
        raise AssertionError(msg)


class _ManifestRaceS3(_FakeS3):
    def put(self, *, bucket: str, key: str, data: bytes, if_absent: bool = False) -> None:
        if if_absent:
            self.objects[bucket, key] = b"winner"
            raise DuplicateArtifactError(key)
        super().put(bucket=bucket, key=key, data=data, if_absent=if_absent)


class _CleanupFailingS3(_FakeS3):
    def delete(self, *, bucket: str, key: str) -> None:  # noqa: ARG002, PLR6301
        msg = "injected cleanup failure"
        raise OSError(msg)


class _CopyAndCleanupFailingS3(_CleanupFailingS3):
    def copy(self, *, bucket: str, source: str, destination: str) -> None:  # noqa: ARG002, PLR6301
        msg = "injected copy failure"
        raise ConnectionError(msg)


def _connect(path: Path):
    return lambda: _ConnectionAdapter(path)


def _bootstrap_connection(failure: BaseException | None = None) -> MagicMock:
    connection = MagicMock()
    if failure is not None:
        connection.cursor.return_value.execute.side_effect = failure
    return connection


def _run(run_id: str = "run-001", *, links: tuple[PrefectExecutionLink, ...] = ()) -> ProductRun:
    return ProductRun(
        run_id=run_id,
        operation="plan",
        configuration_reference="sha256:configuration",
        actor="operator@example.com",
        audit_links=("ticket:change-42",),
        started_at=datetime(2026, 8, 8, 12, tzinfo=timezone.utc),
        phase="planning",
        prefect_executions=links,
    )


def test_registered_run_binding_is_all_or_none_and_survives_restart(tmp_path: Path) -> None:
    """A registered run retains its exact immutable package identity."""
    started_at = datetime(2026, 8, 8, 12, tzinfo=timezone.utc)
    bound = ProductRun(
        run_id="bound-run-001",
        operation="plan",
        configuration_reference="config-001@1",
        config_id="config-001",
        registry_version=1,
        package_checksum="a" * 64,
        started_at=started_at,
        phase="accepted",
    )
    assert bound.configuration_binding == ("config-001", 1, "a" * 64)
    with pytest.raises(ValueError, match="configuration binding"):
        ProductRun(
            run_id="partial-run-001",
            operation="plan",
            configuration_reference="config-001@1",
            config_id="config-001",
            started_at=started_at,
            phase="accepted",
        )

    projection = local_product_projection(tmp_path)
    projection.create_run(bound)
    restarted = local_product_projection(tmp_path).lookup_run("bound-run-001").value
    assert restarted is not None
    assert restarted.configuration_binding == bound.configuration_binding


def _artifact_reference(
    data: bytes = b"{}",
    *,
    object_basename: str = "data",
    manifest_basename: str = "manifest.json",
    separate_manifest_directory: bool = False,
) -> ArtifactReference:
    digest = sha256(data).hexdigest()
    base = f"runs/run-001/artifacts/plan/{digest}"
    return ArtifactReference(
        artifact_id="plan",
        run_id="run-001",
        kind="plan",
        media_type="application/json",
        digest=digest,
        size=len(data),
        object_key=f"{base}/{object_basename}",
        manifest_key=f"{base}{'/manifest' if separate_manifest_directory else ''}/{manifest_basename}",
        created_at=datetime.now(timezone.utc),
    )


def _receipt(  # noqa: PLR0913 - receipt factory exposes contract dimensions.
    receipt_id: str = "mutation-001",
    *,
    run_id: str = "run-001",
    reason: str = "operator requested a plan",
    client_key: str = "client-key",
    operation: str = "plan",
    target_run_id: str | None = None,
) -> MutationReceipt:
    now = datetime(2026, 8, 10, 12, tzinfo=timezone.utc)
    return MutationReceipt(
        receipt_id=receipt_id,
        actor="operator@example.com",
        key_digest=sha256(client_key.encode()).hexdigest(),
        operation=operation,
        target_run_id=target_run_id,
        request_fingerprint=sha256(f"canonical-request:{operation}:{target_run_id}".encode()).hexdigest(),
        reason=reason,
        resource_id=run_id,
        run_id=run_id,
        prefect_key=sha256(receipt_id.encode()).hexdigest(),
        created_at=now,
        updated_at=now,
    )


def _configuration_declaration(**settings_overrides: object) -> dict[str, Any]:
    settings: dict[str, object] = {
        "url": "https://demo.netbox.dev",
        "token": {"$credential": "netbox-token"},
    }
    settings.update(settings_overrides)
    return {
        "format_version": 1,
        "configuration": {
            "name": "from-netbox",
            "source": {"name": "netbox", "settings": settings},
            "destination": {
                "name": "infrahub",
                "settings": {"url": "http://localhost:8000", "token": {"$credential": "infrahub-token"}},
            },
            "order": [],
            "schema_mapping": [],
            "diffsync_flags": [],
            "incremental": None,
        },
        "package_metadata": {"adapter_api_version": 1},
        "credentials": {
            "netbox-token": {"provider": "env", "identifier": "NETBOX_TOKEN"},
            "infrahub-token": {"provider": "env", "identifier": "INFRAHUB_API_TOKEN"},
        },
    }


def _configuration_package(**settings_overrides: object) -> ConfigurationPackage:
    return ConfigurationPackage.model_validate(_configuration_declaration(**settings_overrides))


def test_public_surface_is_exactly_the_supported_contract() -> None:
    assert set(product_store.__all__) == EXPECTED_PUBLIC_NAMES


def test_generate_config_id_shares_the_run_id_generator(monkeypatch: pytest.MonkeyPatch) -> None:
    """The registry must not maintain a second, independently-drifting implementation of the
    run ID scheme: it delegates to ``generate_run_id`` so a change to the shared scheme is
    automatically reflected here too, instead of a docstring claim nothing enforces."""
    monkeypatch.setattr(product_store_store, "generate_run_id", lambda: "sentinel-generated-id")

    assert product_store_store._generate_config_id() == "sentinel-generated-id"


@pytest.mark.parametrize(
    "error",
    [
        _FakeDriverError(sqlstate="23505"),
        _FakeDriverError(pgcode="23505"),
        _FakeDriverError(diagnostic_sqlstate="23505"),
    ],
)
def test_postgresql_unique_violation_exposure_is_recognized(error: BaseException) -> None:
    assert product_store_store._is_unique_violation(error)


@pytest.mark.parametrize("sqlstate", ["23502", "23503"])
def test_postgresql_non_unique_integrity_errors_are_not_duplicates(sqlstate: str) -> None:
    assert not product_store_store._is_unique_violation(
        _FakeDriverError(sqlstate=sqlstate, pgcode=sqlstate, diagnostic_sqlstate=sqlstate)
    )


@pytest.mark.parametrize("sqlstate", ["23505", "42P07", "42710"])
def test_postgresql_schema_bootstrap_retries_only_duplicate_catalog_conflicts(sqlstate: str) -> None:
    conflicted = _bootstrap_connection(_FakeDriverError(sqlstate=sqlstate))
    successful = _bootstrap_connection()
    connections = [conflicted, successful]

    PostgreSQLRunStore(lambda: connections.pop(0))

    assert not connections
    conflicted.rollback.assert_called_once_with()
    conflicted.close.assert_called_once_with()
    successful.commit.assert_called_once_with()
    successful.close.assert_called_once_with()


def test_postgresql_schema_bootstrap_does_not_swallow_other_ddl_failures() -> None:
    connection = _bootstrap_connection(_FakeDriverError(sqlstate="42501"))

    with pytest.raises(_FakeDriverError):
        PostgreSQLRunStore(lambda: connection)

    connection.rollback.assert_called_once_with()
    connection.cursor.return_value.close.assert_called_once_with()
    connection.close.assert_called_once_with()


def test_postgresql_duplicate_column_race_is_recognized() -> None:
    assert product_store_store._is_duplicate_column_error(_FakeDriverError(sqlstate="42701"))


@pytest.mark.parametrize("sqlstate", ["42501", "23505"])
def test_postgresql_non_duplicate_column_errors_are_not_recognized(sqlstate: str) -> None:
    assert not product_store_store._is_duplicate_column_error(_FakeDriverError(sqlstate=sqlstate))


def test_sqlite_duplicate_column_race_is_recognized() -> None:
    connection = sqlite3.connect(":memory:")
    try:
        connection.execute("CREATE TABLE example (existing TEXT)")
        with pytest.raises(sqlite3.OperationalError) as exc_info:
            connection.execute("ALTER TABLE example ADD COLUMN existing TEXT")
        assert product_store_store._is_duplicate_column_error(exc_info.value)
    finally:
        connection.close()


def test_sqlite_other_operational_errors_are_not_duplicate_columns() -> None:
    connection = sqlite3.connect(":memory:")
    try:
        with pytest.raises(sqlite3.OperationalError) as exc_info:
            connection.execute("ALTER TABLE table_that_does_not_exist ADD COLUMN extra TEXT")
        assert not product_store_store._is_duplicate_column_error(exc_info.value)
    finally:
        connection.close()


@pytest.mark.parametrize(
    "duplicate_parameters",
    [
        ("primary", "other-unique", "present"),
        ("other-primary", "unique", "present"),
    ],
)
def test_sqlite_primary_key_and_unique_constraint_failures_are_duplicates(
    duplicate_parameters: tuple[str, str, str],
) -> None:
    connection = sqlite3.connect(":memory:")
    try:
        connection.execute(
            "CREATE TABLE example (primary_value TEXT PRIMARY KEY, unique_value TEXT UNIQUE, required TEXT NOT NULL)"
        )
        connection.execute("INSERT INTO example VALUES (?, ?, ?)", ("primary", "unique", "present"))

        with pytest.raises(sqlite3.IntegrityError) as exc_info:
            connection.execute("INSERT INTO example VALUES (?, ?, ?)", duplicate_parameters)

        assert product_store_store._is_unique_violation(exc_info.value)
    finally:
        connection.close()


@pytest.mark.parametrize("error_code", [1555, 2067])
def test_sqlite_unique_constraint_codes_are_duplicates(error_code: int) -> None:
    error = _FakeSQLiteIntegrityError("synthetic SQLite integrity error")
    error.sqlite_errorcode = error_code

    assert product_store_store._is_unique_violation(error)


@pytest.fixture(params=("local", "production"))
def provider(request, tmp_path: Path) -> ProductProjection:
    if request.param == "local":
        return ProductProjection(
            SQLiteRunStore(tmp_path / "local.sqlite3"),
            FileArtifactStore(tmp_path / "objects"),
        )
    fake_s3 = _FakeS3()
    return ProductProjection(
        PostgreSQLRunStore(_connect(tmp_path / "postgres-emulator.sqlite3")),
        S3ArtifactStore(fake_s3, bucket="product-artifacts", prefix="contract"),
    )


def test_zero_link_standalone_round_trip(provider: ProductProjection) -> None:
    expected = _run()
    provider.create_run(expected)

    result = provider.lookup_run(expected.run_id)

    assert result.available
    assert result.value == expected
    assert result.value is not None
    assert result.value.prefect_executions == ()


def test_mutation_reservation_atomically_creates_one_run_and_replays_on_both_profiles(
    provider: ProductProjection,
) -> None:
    receipt = _receipt()

    first, created = provider.reserve_mutation(receipt, run=_run())
    replay_request = _receipt("mutation-002", run_id="run-never-created")
    replay, replay_created = provider.reserve_mutation(replay_request, run=_run("run-never-created"))

    assert created is True
    assert replay_created is False
    assert replay == first
    assert provider.lookup_run("run-001").value == _run()
    assert provider.lookup_run("run-never-created").reason == "run-not-found"


@pytest.mark.parametrize("profile", [("sqlite",), ("postgresql",)])
def test_receipt_migration_backfills_legacy_runs_and_allows_configuration_receipts(
    tmp_path: Path, profile: tuple[str]
) -> None:
    """Receipt storage upgrades legacy run rows without inventing run identifiers for configs."""
    database = tmp_path / f"{profile[0]}.sqlite3"
    legacy = sqlite3.connect(database)
    try:
        legacy.execute(
            "CREATE TABLE mutation_receipts ("
            "receipt_id TEXT PRIMARY KEY, actor TEXT NOT NULL, key_digest TEXT NOT NULL, "
            "operation TEXT NOT NULL, target_run_id TEXT, request_fingerprint TEXT NOT NULL, "
            "reason TEXT NOT NULL, run_id TEXT NOT NULL, prefect_key TEXT NOT NULL, "
            "state TEXT NOT NULL, response_status INTEGER, response_body TEXT, flow_run_id TEXT, "
            "created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE (actor, key_digest))"
        )
        legacy_receipt = _receipt()
        legacy_values = product_store_store._receipt_values(legacy_receipt)
        legacy.execute(
            "INSERT INTO mutation_receipts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            legacy_values[:7] + legacy_values[9:],
        )
        legacy.commit()
    finally:
        legacy.close()

    records = SQLiteRunStore(database) if profile[0] == "sqlite" else PostgreSQLRunStore(_connect(database))
    projection = ProductProjection(records, FileArtifactStore(tmp_path / f"{profile[0]}-objects"))
    restored = projection.lookup_mutation(legacy_receipt.actor, legacy_receipt.key_digest)
    assert restored.value is not None
    assert restored.value.resource_kind == "run"
    assert restored.value.resource_id == legacy_receipt.run_id
    assert restored.value.run_id == legacy_receipt.run_id
    assert restored.value.prefect_key == legacy_receipt.prefect_key

    restarted = ProductProjection(SQLiteRunStore(database), FileArtifactStore(tmp_path / "restarted-objects"))
    after_restart = restarted.lookup_mutation(legacy_receipt.actor, legacy_receipt.key_digest)
    assert after_restart.value is not None
    assert after_restart.value.run_id == legacy_receipt.run_id
    assert after_restart.value.prefect_key == legacy_receipt.prefect_key
    schema = sqlite3.connect(database)
    try:
        columns = {row[1] for row in schema.execute("PRAGMA table_info(mutation_receipts)")}
    finally:
        schema.close()
    assert {"resource_kind", "resource_id"} <= columns

    now = datetime(2026, 8, 10, 12, tzinfo=timezone.utc)
    configuration_receipt = MutationReceipt(
        receipt_id="configuration-mutation-001",
        actor="operator@example.com",
        key_digest=sha256(b"configuration-key").hexdigest(),
        operation="register-config",
        request_fingerprint=sha256(b"configuration-request").hexdigest(),
        reason="operator registered a configuration",
        resource_kind="configuration",
        resource_id="configs",
        created_at=now,
        updated_at=now,
    )
    reserved, created = projection.reserve_mutation(configuration_receipt)
    assert created is True
    assert reserved.run_id is None
    assert reserved.prefect_key is None
    assert reserved.flow_run_id is None
    schema = sqlite3.connect(database)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            schema.execute(
                "UPDATE mutation_receipts SET resource_kind = 'configuration' WHERE receipt_id = ?",
                (legacy_receipt.receipt_id,),
            )
    finally:
        schema.close()


def test_fresh_sqlite_receipt_resource_invariant_rejects_mixed_direct_write(tmp_path: Path) -> None:
    """The fresh schema enforces the same configuration/run shape as a migrated database."""
    database = tmp_path / "fresh.sqlite3"
    SQLiteRunStore(database)
    connection = sqlite3.connect(database)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO mutation_receipts (receipt_id, actor, key_digest, operation, request_fingerprint, reason, "
                "resource_kind, resource_id, run_id, prefect_key, state, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "mixed-receipt",
                    "operator",
                    "a" * 64,
                    "register-config",
                    "b" * 64,
                    "test direct write",
                    "configuration",
                    "configs",
                    "run-001",
                    "c" * 64,
                    "reserved",
                    "2026-08-10T12:00:00+00:00",
                    "2026-08-10T12:00:00+00:00",
                ),
            )
    finally:
        connection.close()


def test_sqlite_concurrent_mutation_reservation_creates_exactly_one_product_run(tmp_path: Path) -> None:
    projection = local_product_projection(tmp_path.resolve())

    def reserve(position: int) -> tuple[MutationReceipt, bool]:
        receipt = _receipt(f"mutation-{position:03d}", run_id=f"run-{position:03d}")
        return projection.reserve_mutation(receipt, run=_run(f"run-{position:03d}"))

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(reserve, range(8)))

    assert sum(created for _, created in results) == 1
    winning_run_ids = {receipt.run_id for receipt, _ in results}
    assert len(winning_run_ids) == 1
    winning_run_id = winning_run_ids.pop()
    assert winning_run_id is not None
    assert projection.lookup_run(winning_run_id).available


def test_write_capable_mutation_admission_is_atomic_on_both_profiles(provider: ProductProjection) -> None:
    provider.create_run(_run())

    def reserve(position: int) -> str:
        receipt = _receipt(
            f"apply-{position}",
            client_key=f"apply-key-{position}",
            operation="apply",
            target_run_id="run-001",
        )
        try:
            provider.reserve_mutation(receipt, admit_write=True)
        except WriteAdmissionConflictError:
            return "refused"
        return "admitted"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(reserve, range(2)))

    assert sorted(outcomes) == ["admitted", "refused"]
    receipts = [
        provider.lookup_mutation("operator@example.com", sha256(f"apply-key-{position}".encode()).hexdigest())
        for position in range(2)
    ]
    assert sum(receipt.available for receipt in receipts) == 1


def test_write_admission_exact_receipt_replay_precedes_the_run_conflict(provider: ProductProjection) -> None:
    provider.create_run(_run())
    receipt = _receipt(
        "apply-001",
        client_key="apply-key",
        operation="apply",
        target_run_id="run-001",
    )

    admitted, created = provider.reserve_mutation(receipt, admit_write=True)
    replayed, replay_created = provider.reserve_mutation(
        receipt.model_copy(update={"receipt_id": "apply-002"}), admit_write=True
    )

    assert created is True
    assert replay_created is False
    assert replayed == admitted


def test_receipt_completion_and_audit_are_durable_and_secret_safe(provider: ProductProjection) -> None:
    token = "token-canary-value"  # noqa: S105 - deliberate non-secret boundary canary.
    receipt = _receipt(reason=f"requested with {token}")
    reserved, created = provider.reserve_mutation(receipt, run=_run(), secrets=(token,))
    assert created
    assert reserved.reason == "requested with ***"

    completed = provider.complete_mutation(
        reserved.receipt_id,
        response_status=202,
        response_body={"run_id": "run-001", "detail": token},
        flow_run_id="flow-001",
        secrets=(token,),
    )
    replayed_completion = provider.complete_mutation(
        reserved.receipt_id,
        response_status=202,
        response_body={"run_id": "run-001", "detail": token},
        flow_run_id="flow-001",
        secrets=(token,),
    )
    converged_completion = provider.complete_mutation(
        reserved.receipt_id,
        response_status=202,
        response_body={"run_id": "racing-response"},
        flow_run_id="flow-001",
    )
    provider.record_audit(
        AuditEvent(
            event_id="audit-001",
            run_id="run-001",
            actor="operator@example.com",
            operation="plan",
            reason=token,
            outcome="accepted",
            created_at=datetime.now(timezone.utc),
        ),
        secrets=(token,),
    )

    assert completed.state == "accepted"
    assert replayed_completion == completed
    assert converged_completion == completed
    assert completed.response_body == {"detail": "***", "run_id": "run-001"}
    assert provider.audit_events("run-001")[0].reason == "***"
    audited_run = provider.lookup_run("run-001").value
    assert audited_run is not None
    assert audited_run.audit_links == ("ticket:change-42", "audit-001")

    with pytest.raises(ValueError, match="different response"):
        provider.complete_mutation(
            reserved.receipt_id,
            response_status=202,
            response_body={"run_id": "different"},
            flow_run_id="flow-002",
        )


def test_record_results_does_not_change_product_lifecycle(provider: ProductProjection) -> None:
    original = _run().model_copy(update={"phase": "planned", "summary": {"total": 1}})
    provider.create_run(original)

    provider.record_results("run-001", {"verification": {"outcome": "verified"}})

    updated = provider.lookup_run("run-001").value
    assert updated is not None
    assert updated.model_dump(exclude={"results"}) == original.model_dump(exclude={"results"})
    assert updated.results == {"verification": {"outcome": "verified"}}


def test_sqlite_foreign_key_failure_passes_through_as_integrity_error(tmp_path: Path) -> None:
    store = SQLiteRunStore(tmp_path / "records.sqlite3")

    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY constraint failed"):
        store.add_prefect_execution(
            "missing-run",
            PrefectExecutionLink(flow_run_id="flow-001", purpose="plan", attempt=1),
        )


def test_configuration_version_foreign_key_is_enforced(tmp_path: Path) -> None:
    """``configuration_versions.config_id`` must be refused for a configuration that was never
    registered. ``add_configuration_version`` on the raw store issues no existence check of its
    own (only ``ProductProjection.add_configuration_version`` does, before delegating) -- so the
    ``configurations`` foreign key is the only thing standing between a nonexistent config_id and
    a durably orphaned version row."""
    store = SQLiteRunStore(tmp_path / "records.sqlite3")

    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY constraint failed"):
        store.add_configuration_version("missing-configuration", _configuration_package())


def test_child_insert_uniqueness_is_not_misreported_as_a_duplicate_run(tmp_path: Path) -> None:
    store = _ChildConflictSQLiteStore(tmp_path / "records.sqlite3")
    link = PrefectExecutionLink(flow_run_id="flow-001", purpose="plan", attempt=1)

    with pytest.raises(sqlite3.IntegrityError, match="child uniqueness"):
        store.create(_run(links=(link,)))

    assert store.lookup("run-001").reason == "run-not-found"


def test_every_prefect_link_field_and_multiple_attempts_round_trip(provider: ProductProjection) -> None:
    links = (
        PrefectExecutionLink(
            flow_run_id="flow-plan-attempt-1",
            deployment_id="deployment-42",
            purpose="plan",
            attempt=1,
            last_observed_state="Failed",
            last_observed_at=datetime(2026, 8, 8, 12, 1, tzinfo=timezone.utc),
        ),
        PrefectExecutionLink(
            flow_run_id="flow-plan-attempt-2",
            deployment_id="deployment-42",
            purpose="plan-retry",
            attempt=2,
            last_observed_state="Completed",
            last_observed_at=datetime(2026, 8, 8, 12, 2, tzinfo=timezone.utc),
        ),
        PrefectExecutionLink(
            flow_run_id="flow-apply-attempt-1",
            deployment_id=None,
            purpose="reviewed-apply",
            attempt=1,
            last_observed_state=None,
            last_observed_at=None,
        ),
    )
    expected = _run()

    provider.create_run(expected)
    for link in links:
        provider.add_prefect_execution(expected.run_id, link)

    loaded = provider.lookup_run(expected.run_id).value
    assert loaded is not None
    assert loaded.prefect_executions == links


def test_execution_link_requires_complete_claim_and_terminal_verdicts() -> None:
    """Liveness fields have exact all-or-none boundaries before persistence."""
    now = datetime(2026, 8, 29, tzinfo=timezone.utc)
    with pytest.raises(ValidationError, match="claim"):
        PrefectExecutionLink(flow_run_id="flow", purpose="plan", attempt=1, submitted_at=now, claimed_at=now)
    with pytest.raises(ValidationError, match="terminal"):
        PrefectExecutionLink(flow_run_id="flow", purpose="plan", attempt=1, submitted_at=now, terminal_at=now)


@pytest.mark.parametrize(
    "field",
    [
        "last_observed_at",
        "submitted_at",
        "claimed_at",
        "stalled_at",
        "cancellation_requested_at",
        "cancellation_recovery_deadline_at",
        "cancellation_acknowledged_at",
        "terminal_at",
    ],
)
def test_execution_link_timestamp_admission_refuses_coercible_integers(field: str) -> None:
    """Every execution timestamp closes Pydantic's epoch-integer coercion path."""
    invalid_timestamp = 1_777_469_600
    now = datetime(2026, 8, 29, tzinfo=timezone.utc)
    payload: dict[str, object] = {
        "flow_run_id": "flow",
        "purpose": "plan",
        "attempt": 1,
        "last_observed_at": now,
        "submitted_at": now,
        "claimed_at": now,
        "claiming_worker_id": "8c1da53d-0e6b-4d3d-a0f1-97b6a9ccebf0",
        "stalled_at": now,
        "cancellation_requested_at": now,
        "cancellation_recovery_deadline_at": now + timedelta(seconds=30),
        "cancellation_receipt_id": "mutation-001",
        "cancellation_acknowledged_at": now,
        "terminal_at": now,
        "terminal_state": "completed",
        "terminal_outcome": "succeeded",
    }
    payload[field] = invalid_timestamp

    with pytest.raises(ValidationError, match="Prefect execution timestamps"):
        PrefectExecutionLink.model_validate(payload)


def test_execution_link_explicitly_hydrates_exact_persisted_iso_timestamps() -> None:
    """Provider rows remain readable without opening general timestamp coercion."""
    persisted = "2026-08-29T12:00:00+00:00"

    link = PrefectExecutionLink.model_validate(
        {
            "flow_run_id": "flow",
            "purpose": "plan",
            "attempt": 1,
            "last_observed_at": persisted,
            "submitted_at": persisted,
            "stalled_at": persisted,
        }
    )

    assert link.last_observed_at == datetime(2026, 8, 29, 12, tzinfo=timezone.utc)
    assert link.submitted_at == datetime(2026, 8, 29, 12, tzinfo=timezone.utc)
    assert link.stalled_at == datetime(2026, 8, 29, 12, tzinfo=timezone.utc)
    assert all(type(value) is datetime for value in (link.last_observed_at, link.submitted_at, link.stalled_at))


def test_execution_link_json_round_trip_hydrates_terminal_z_timestamps() -> None:
    """The Python 3.10 reader accepts the exact UTC form emitted by Pydantic."""
    now = datetime(2026, 8, 29, 12, tzinfo=timezone.utc)
    link = PrefectExecutionLink(
        flow_run_id="flow",
        purpose="plan",
        attempt=1,
        last_observed_at=now,
        submitted_at=now,
        claimed_at=now,
        claiming_worker_id="8c1da53d-0e6b-4d3d-a0f1-97b6a9ccebf0",
        stalled_at=now,
        cancellation_requested_at=now,
        cancellation_recovery_deadline_at=now + timedelta(seconds=30),
        cancellation_receipt_id="mutation-001",
        cancellation_acknowledged_at=now,
        terminal_at=now,
        terminal_state="completed",
        terminal_outcome="succeeded",
    )

    persisted = link.model_dump(mode="json")

    timestamp_fields = (
        "last_observed_at",
        "submitted_at",
        "claimed_at",
        "stalled_at",
        "cancellation_requested_at",
        "cancellation_recovery_deadline_at",
        "cancellation_acknowledged_at",
        "terminal_at",
    )
    assert all(cast("str", persisted[field]).endswith("Z") for field in timestamp_fields)
    assert PrefectExecutionLink.model_validate(persisted) == link


@pytest.mark.parametrize(
    ("terminal_state", "terminal_outcome"),
    [
        (state, outcome)
        for state in ("completed", "failed", "cancelled", "abandoned", "interrupted")
        for outcome in ("succeeded", "failed", "cancelled", "abandoned", "ambiguous")
        if (state, outcome)
        not in {
            ("completed", "succeeded"),
            ("failed", "failed"),
            ("cancelled", "cancelled"),
            ("abandoned", "abandoned"),
            ("interrupted", "ambiguous"),
        }
    ],
)
def test_execution_link_refuses_every_illegal_terminal_verdict_pair(
    terminal_state: str,
    terminal_outcome: str,
) -> None:
    """The closed terminal vocabularies cannot be recombined into invented verdicts."""
    now = datetime(2026, 8, 29, tzinfo=timezone.utc)

    with pytest.raises(ValidationError, match="terminal verdict"):
        PrefectExecutionLink.model_validate(
            {
                "flow_run_id": "flow",
                "purpose": "plan",
                "attempt": 1,
                "submitted_at": now,
                "terminal_at": now,
                "terminal_state": terminal_state,
                "terminal_outcome": terminal_outcome,
            }
        )


def test_execution_link_accepts_canonical_builtin_worker_uuid() -> None:
    now = datetime(2026, 8, 29, tzinfo=timezone.utc)
    worker_id = "8c1da53d-0e6b-4d3d-a0f1-97b6a9ccebf0"

    accepted = PrefectExecutionLink(
        flow_run_id="flow",
        purpose="plan",
        attempt=1,
        submitted_at=now,
        claimed_at=now,
        claiming_worker_id=worker_id,
    )
    assert accepted.claiming_worker_id == worker_id


def test_execution_link_refuses_noncanonical_worker_identity_with_fixed_error() -> None:
    worker_id = "8C1DA53D-0E6B-4D3D-A0F1-97B6A9CCEBF0"
    now = datetime(2026, 8, 29, tzinfo=timezone.utc)

    with pytest.raises(ValidationError) as caught:
        PrefectExecutionLink.model_validate(
            {
                "flow_run_id": "flow",
                "purpose": "plan",
                "attempt": 1,
                "submitted_at": now,
                "claimed_at": now,
                "claiming_worker_id": worker_id,
            }
        )
    errors = caught.value.errors(include_input=False)
    assert errors[0]["msg"] == "Value error, service worker identity is invalid"


def test_new_execution_requires_submitted_at_without_persistence(provider: ProductProjection) -> None:
    """New link admission refuses a missing submission timestamp before provider write."""
    provider.create_run(_run())
    link = PrefectExecutionLink(flow_run_id="flow-001", purpose="plan", attempt=1, submitted_at=None)

    with pytest.raises(ValueError, match=r"^new Prefect execution requires submitted_at$"):
        provider.add_prefect_execution("run-001", link)

    stored = provider.lookup_run("run-001").value
    assert stored is not None
    assert stored.prefect_executions == ()


def test_every_product_admission_refuses_embedded_execution_without_submitted_at(
    provider: ProductProjection,
) -> None:
    """Legacy hydration remains readable, but no public creation path can persist a new legacy link."""
    link = PrefectExecutionLink(flow_run_id="flow-legacy", purpose="plan", attempt=1, submitted_at=None)

    with pytest.raises(ValueError, match=r"^new Prefect execution requires submitted_at$"):
        provider.create_run(_run(links=(link,)))

    receipt = _receipt(run_id="run-reserved")
    with pytest.raises(ValueError, match=r"^new Prefect execution requires submitted_at$"):
        provider.reserve_mutation(receipt, run=_run("run-reserved", links=(link,)))

    assert provider.lookup_run("run-001").reason == "run-not-found"
    assert provider.lookup_run("run-reserved").reason == "run-not-found"


@pytest.mark.parametrize("profile", ["sqlite", "postgresql"])
@pytest.mark.parametrize(
    ("anchor_offset_microseconds", "expected_outcome"),
    [
        (-1, "refused"),
        (0, "refused"),
        (1, "claimed"),
        (-300_000_000, "refused"),
    ],
    ids=["deadline-minus-one-microsecond", "deadline", "deadline-plus-one-microsecond", "well-past-ttl"],
)
def test_migrated_execution_claim_uses_observation_fallback_at_admission_deadline(
    profile: str,
    tmp_path: Path,
    anchor_offset_microseconds: int,
    expected_outcome: str,
) -> None:
    """A migrated row uses its observation as the claim CAS admission anchor."""
    database = tmp_path / f"legacy-{profile}.sqlite3"
    records = SQLiteRunStore(database) if profile == "sqlite" else PostgreSQLRunStore(_connect(database))
    projection = ProductProjection(records, FileArtifactStore(tmp_path / f"objects-{profile}"))
    claimed_at = datetime(2026, 8, 29, 12, tzinfo=timezone.utc)
    admission_deadline_at = claimed_at - timedelta(seconds=300)
    observed_at = (admission_deadline_at + timedelta(microseconds=anchor_offset_microseconds)).astimezone(
        timezone(timedelta(hours=-4))
    )
    projection.create_run(_run())
    projection.add_prefect_execution(
        "run-001",
        PrefectExecutionLink(
            flow_run_id="flow-001",
            purpose="plan",
            attempt=1,
            last_observed_state="pending",
            last_observed_at=observed_at,
            submitted_at=observed_at,
        ),
    )
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE prefect_executions SET submitted_at = NULL WHERE run_id = ? AND flow_run_id = ?",
            ("run-001", "flow-001"),
        )

    loaded = projection.lookup_run("run-001").value
    assert loaded is not None
    assert loaded.prefect_executions[0].submitted_at == observed_at
    with sqlite3.connect(database) as connection:
        stored_submitted_at = connection.execute(
            "SELECT submitted_at FROM prefect_executions WHERE run_id = ? AND flow_run_id = ?",
            ("run-001", "flow-001"),
        ).fetchone()
    assert stored_submitted_at == (None,)
    claimed = projection.claim_execution(
        "run-001",
        "flow-001",
        worker_id="8c1da53d-0e6b-4d3d-a0f1-97b6a9ccebf0",
        claimed_at=claimed_at,
        admission_ttl_seconds=300,
    )
    assert claimed is (expected_outcome == "claimed")


@pytest.mark.parametrize("profile", ["sqlite", "postgresql"])
def test_legacy_execution_without_any_admission_anchor_remains_claimable(profile: str, tmp_path: Path) -> None:
    """Only a row with neither submission nor observation remains legacy-unclassified."""
    database = tmp_path / f"unclassified-{profile}.sqlite3"
    records = SQLiteRunStore(database) if profile == "sqlite" else PostgreSQLRunStore(_connect(database))
    projection = ProductProjection(records, FileArtifactStore(tmp_path / f"objects-{profile}"))
    observed_at = datetime(2026, 8, 29, 12, tzinfo=timezone.utc)
    projection.create_run(_run())
    projection.add_prefect_execution(
        "run-001",
        PrefectExecutionLink(
            flow_run_id="flow-001",
            purpose="plan",
            attempt=1,
            last_observed_state="pending",
            last_observed_at=observed_at,
            submitted_at=observed_at,
        ),
    )
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE prefect_executions SET submitted_at = NULL, last_observed_at = NULL "
            "WHERE run_id = ? AND flow_run_id = ?",
            ("run-001", "flow-001"),
        )

    assert projection.claim_execution(
        "run-001",
        "flow-001",
        worker_id="8c1da53d-0e6b-4d3d-a0f1-97b6a9ccebf0",
        claimed_at=observed_at + timedelta(days=1),
        admission_ttl_seconds=300,
    )


def test_execution_claim_stall_and_terminal_cas_contract(provider: ProductProjection) -> None:
    """A stall is informational; claim and abandonment have mutually exclusive predicates."""
    now = datetime(2026, 8, 29, tzinfo=timezone.utc)
    worker_id = "8c1da53d-0e6b-4d3d-a0f1-97b6a9ccebf0"
    provider.create_run(_run())
    provider.add_prefect_execution(
        "run-001", PrefectExecutionLink(flow_run_id="flow-001", purpose="plan", attempt=1, submitted_at=now)
    )

    assert provider.mark_execution_stalled("run-001", "flow-001", stalled_at=now)
    assert provider.claim_execution("run-001", "flow-001", worker_id=worker_id, claimed_at=now)
    assert not provider.abandon_execution("run-001", "flow-001", terminal_at=now)
    loaded = provider.lookup_run("run-001").value
    assert loaded is not None
    link = loaded.prefect_executions[0]
    assert link.stalled_at == now
    assert link.claimed_at == now
    assert link.claiming_worker_id == worker_id


@pytest.mark.parametrize(
    ("age_microseconds", "expected_outcome"),
    [
        (299_000_000, "claimed"),
        (299_999_999, "claimed"),
        (300_000_000, "refused"),
        (301_000_000, "refused"),
    ],
    ids=["before-boundary", "one-microsecond-before", "exact-boundary", "after-boundary"],
)
def test_execution_claim_respects_inclusive_admission_ttl(
    provider: ProductProjection,
    age_microseconds: int,
    expected_outcome: str,
) -> None:
    """The persistence claim refuses a link at and beyond its admission deadline."""
    claimed_at = datetime(2026, 8, 29, 12, tzinfo=timezone.utc)
    worker_id = "8c1da53d-0e6b-4d3d-a0f1-97b6a9ccebf0"
    provider.create_run(_run())
    provider.add_prefect_execution(
        "run-001",
        PrefectExecutionLink(
            flow_run_id="flow-001",
            purpose="plan",
            attempt=1,
            submitted_at=(claimed_at - timedelta(microseconds=age_microseconds)).astimezone(
                timezone(timedelta(hours=-4))
            ),
        ),
    )

    claimed = provider.claim_execution(
        "run-001",
        "flow-001",
        worker_id=worker_id,
        claimed_at=claimed_at,
        admission_ttl_seconds=300,
    )

    expected_claimed = expected_outcome == "claimed"
    assert claimed is expected_claimed
    run = provider.lookup_run("run-001").value
    assert run is not None
    link = run.prefect_executions[0]
    assert (link.claiming_worker_id == worker_id) is expected_claimed


@pytest.mark.parametrize("mutation", ["claim", "stall", "abandon", "interrupt"])
def test_execution_liveness_mutations_refuse_naive_timestamps_without_writing(
    provider: ProductProjection,
    mutation: str,
) -> None:
    now = datetime(2026, 8, 29, tzinfo=timezone.utc)
    naive = now.replace(tzinfo=None)
    worker_id = "8c1da53d-0e6b-4d3d-a0f1-97b6a9ccebf0"
    provider.create_run(_run())
    provider.add_prefect_execution(
        "run-001", PrefectExecutionLink(flow_run_id="flow-001", purpose="plan", attempt=1, submitted_at=now)
    )
    if mutation == "interrupt":
        provider.claim_execution("run-001", "flow-001", worker_id=worker_id, claimed_at=now)
    before = provider.lookup_run("run-001")
    mutations: dict[str, Callable[[], bool]] = {
        "claim": lambda: provider.claim_execution("run-001", "flow-001", worker_id=worker_id, claimed_at=naive),
        "stall": lambda: provider.mark_execution_stalled("run-001", "flow-001", stalled_at=naive),
        "abandon": lambda: provider.abandon_execution("run-001", "flow-001", terminal_at=naive),
        "interrupt": lambda: provider.interrupt_execution("run-001", "flow-001", terminal_at=naive),
    }

    with pytest.raises(ValueError, match=r"^execution timestamps must include a timezone$"):
        mutations[mutation]()

    assert provider.lookup_run("run-001") == before


@pytest.mark.parametrize("invalid_timestamp", [0, 1_777_469_600], ids=["zero-integer", "integer"])
def test_execution_finish_writeback_refuses_coercible_integer_finished_at(invalid_timestamp: object) -> None:
    with pytest.raises(ValidationError, match="execution writeback timestamps"):
        product_store.ExecutionFinishWriteback.model_validate(
            {
                "phase": "planned",
                "outcome": "planned",
                "finished_at": invalid_timestamp,
                "summary": {},
                "results": {},
            }
        )


@pytest.mark.parametrize("transition", ["abandon", "interrupt", "commit", "cancel", "expire"])
def test_every_execution_terminal_transition_refuses_naive_timestamp_without_writing(
    provider: ProductProjection,
    transition: str,
) -> None:
    now = datetime(2026, 8, 29, tzinfo=timezone.utc)
    worker_id = "8c1da53d-0e6b-4d3d-a0f1-97b6a9ccebf0"
    provider.create_run(_run())
    provider.add_prefect_execution(
        "run-001", PrefectExecutionLink(flow_run_id="flow-001", purpose="plan", attempt=1, submitted_at=now)
    )
    if transition in {"interrupt", "commit"}:
        assert provider.claim_execution("run-001", "flow-001", worker_id=worker_id, claimed_at=now)
    before = provider.lookup_run("run-001")
    terminal_at = now.replace(tzinfo=None)
    actions: dict[str, Callable[[], bool]] = {
        "abandon": lambda: provider.abandon_execution("run-001", "flow-001", terminal_at=terminal_at),
        "interrupt": lambda: provider.interrupt_execution("run-001", "flow-001", terminal_at=terminal_at),
        "commit": lambda: provider.commit_claimed_execution(
            "run-001",
            "flow-001",
            worker_id=worker_id,
            terminal_at=terminal_at,
            terminal_state="completed",
            terminal_outcome="succeeded",
            writeback=product_store.ExecutionFinishWriteback(
                phase="planned",
                outcome="planned",
                finished_at=now,
                summary={},
                results={},
            ),
        ),
        "cancel": lambda: provider.cancel_execution("run-001", "flow-001", terminal_at=terminal_at),
        "expire": lambda: provider.expire_execution_cancellation("run-001", "flow-001", terminal_at=terminal_at),
    }

    with pytest.raises(ValueError, match=r"^execution timestamps must include a timezone$"):
        actions[transition]()

    assert provider.lookup_run("run-001") == before


@pytest.mark.parametrize("age_seconds", [299, 300], ids=["before-boundary", "exact-boundary"])
def test_claim_and_abandon_race_has_one_winner(provider: ProductProjection, age_seconds: int) -> None:
    """Both provider profiles serialize claim against abandonment at the TTL boundary."""
    now = datetime(2026, 8, 29, tzinfo=timezone.utc)
    worker_id = "8c1da53d-0e6b-4d3d-a0f1-97b6a9ccebf0"
    provider.create_run(_run())
    provider.add_prefect_execution(
        "run-001",
        PrefectExecutionLink(
            flow_run_id="flow-001",
            purpose="plan",
            attempt=1,
            submitted_at=now - timedelta(seconds=age_seconds),
        ),
    )
    barrier = Barrier(2)

    def claim() -> bool:
        barrier.wait()
        return provider.claim_execution(
            "run-001",
            "flow-001",
            worker_id=worker_id,
            claimed_at=now,
            admission_ttl_seconds=300,
        )

    def abandon() -> bool:
        barrier.wait()
        return provider.abandon_execution("run-001", "flow-001", terminal_at=now)

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = tuple(pool.map(lambda action: action(), (claim, abandon)))
    assert outcomes.count(True) == 1
    if age_seconds == 300:
        assert outcomes == (False, True)


@pytest.mark.parametrize("profile", ["sqlite", "postgresql"])
def test_migrated_claim_and_abandon_race_at_deadline_has_one_winner(profile: str, tmp_path: Path) -> None:
    """The effective observation anchor excludes claim while abandonment wins the row CAS."""
    database = tmp_path / f"migrated-race-{profile}.sqlite3"
    records = SQLiteRunStore(database) if profile == "sqlite" else PostgreSQLRunStore(_connect(database))
    projection = ProductProjection(records, FileArtifactStore(tmp_path / f"objects-{profile}"))
    now = datetime(2026, 8, 29, 12, tzinfo=timezone.utc)
    worker_id = "8c1da53d-0e6b-4d3d-a0f1-97b6a9ccebf0"
    projection.create_run(_run())
    projection.add_prefect_execution(
        "run-001",
        PrefectExecutionLink(
            flow_run_id="flow-001",
            purpose="plan",
            attempt=1,
            last_observed_at=now - timedelta(seconds=300),
            submitted_at=now - timedelta(seconds=300),
        ),
    )
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE prefect_executions SET submitted_at = NULL WHERE run_id = ? AND flow_run_id = ?",
            ("run-001", "flow-001"),
        )
    barrier = Barrier(2)

    def claim() -> bool:
        barrier.wait()
        return projection.claim_execution(
            "run-001",
            "flow-001",
            worker_id=worker_id,
            claimed_at=now,
            admission_ttl_seconds=300,
        )

    def abandon() -> bool:
        barrier.wait()
        return projection.abandon_execution("run-001", "flow-001", terminal_at=now)

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = tuple(pool.map(lambda action: action(), (claim, abandon)))
    assert outcomes == (False, True)


def test_execution_claim_refuses_invalid_worker_identity_without_mutation(provider: ProductProjection) -> None:
    now = datetime(2026, 8, 29, tzinfo=timezone.utc)
    provider.create_run(_run())
    provider.add_prefect_execution(
        "run-001", PrefectExecutionLink(flow_run_id="flow-001", purpose="plan", attempt=1, submitted_at=now)
    )

    with pytest.raises(ValueError, match="service worker identity is invalid"):
        provider.claim_execution("run-001", "flow-001", worker_id="not-a-uuid", claimed_at=now)

    loaded = provider.lookup_run("run-001").value
    assert loaded is not None
    assert loaded.prefect_executions[0].claimed_at is None


def test_claimed_finish_writeback_is_atomic_and_worker_bound(provider: ProductProjection) -> None:
    """The exact claiming worker commits the business result and link verdict together."""
    now = datetime(2026, 8, 29, 12, tzinfo=timezone.utc)
    worker_id = "8c1da53d-0e6b-4d3d-a0f1-97b6a9ccebf0"
    provider.create_run(_run())
    provider.add_prefect_execution(
        "run-001", PrefectExecutionLink(flow_run_id="flow-001", purpose="plan", attempt=1, submitted_at=now)
    )
    assert provider.claim_execution("run-001", "flow-001", worker_id=worker_id, claimed_at=now)
    writeback = product_store.ExecutionFinishWriteback(
        phase="planned",
        outcome="planned",
        finished_at=now + timedelta(seconds=1),
        summary={"total": 1},
        results={"stage": "plan", "outcome": "planned"},
    )

    assert not provider.commit_claimed_execution(
        "run-001",
        "flow-001",
        worker_id="d08f703b-ce73-4269-a7aa-1bfb00f8cc63",
        terminal_at=now + timedelta(seconds=1),
        terminal_state="completed",
        terminal_outcome="succeeded",
        writeback=writeback,
    )
    assert provider.commit_claimed_execution(
        "run-001",
        "flow-001",
        worker_id=worker_id,
        terminal_at=now + timedelta(seconds=1),
        terminal_state="completed",
        terminal_outcome="succeeded",
        writeback=writeback,
    )

    stored = provider.lookup_run("run-001").value
    assert stored is not None
    assert (stored.phase, stored.outcome, stored.finished_at) == ("planned", "planned", now + timedelta(seconds=1))
    assert stored.results == {"stage": "plan", "outcome": "planned"}
    assert (stored.prefect_executions[0].terminal_state, stored.prefect_executions[0].terminal_outcome) == (
        "completed",
        "succeeded",
    )


@pytest.mark.parametrize(
    "older_transition",
    ["finish", "merge", "abandon", "interrupt", "cancel", "expire", "late-ack"],
)
def test_older_execution_terminalization_settles_only_its_link_and_receipt(
    provider: ProductProjection,
    older_transition: str,
) -> None:
    """Accepted position, not completion timing, decides which link owns product results."""
    now = datetime(2026, 8, 29, 12, tzinfo=timezone.utc)
    worker_id = "8c1da53d-0e6b-4d3d-a0f1-97b6a9ccebf0"
    provider.create_run(_run())
    provider.add_prefect_execution(
        "run-001", PrefectExecutionLink(flow_run_id="flow-older", purpose="plan", attempt=1, submitted_at=now)
    )
    provider.add_prefect_execution(
        "run-001",
        PrefectExecutionLink(
            flow_run_id="flow-latest", purpose="verify", attempt=1, submitted_at=now + timedelta(seconds=1)
        ),
    )
    if older_transition in {"finish", "merge", "interrupt"}:
        assert provider.claim_execution("run-001", "flow-older", worker_id=worker_id, claimed_at=now)

    receipt: MutationReceipt | None = None
    deadline = now + timedelta(seconds=30)
    if older_transition in {"cancel", "expire", "late-ack"}:
        receipt = _receipt(operation="cancel", target_run_id="run-001")
        provider.reserve_mutation(receipt)
        assert provider.claim_mutation(receipt.receipt_id)
        assert provider.request_execution_cancellation(
            "run-001",
            "flow-older",
            requested_at=now,
            recovery_deadline_at=deadline,
            recovery_seconds=30,
            expected_latest_position=1,
            receipt_id=receipt.receipt_id,
        )
    if older_transition == "cancel":
        assert receipt is not None
        assert provider.acknowledge_execution_cancellation(
            "run-001",
            "flow-older",
            acknowledged_at=now + timedelta(seconds=1),
            response_status=202,
            response_body={"accepted": True},
        )
        provider.observe_prefect_execution("run-001", "flow-older", state="cancelled")

    assert provider.claim_execution(
        "run-001", "flow-latest", worker_id=worker_id, claimed_at=now + timedelta(seconds=1)
    )
    newest_finished_at = now + timedelta(seconds=2)
    assert provider.commit_claimed_execution(
        "run-001",
        "flow-latest",
        worker_id=worker_id,
        terminal_at=newest_finished_at,
        terminal_state="completed",
        terminal_outcome="succeeded",
        writeback=product_store.ExecutionFinishWriteback(
            phase="newest-complete",
            outcome="newest-result",
            finished_at=newest_finished_at,
            summary={"winner": "newest"},
            results={"winner": "newest"},
        ),
    )

    older_terminal_at = deadline if older_transition in {"expire", "late-ack"} else now + timedelta(seconds=3)
    if older_transition == "finish":
        settled = provider.commit_claimed_execution(
            "run-001",
            "flow-older",
            worker_id=worker_id,
            terminal_at=older_terminal_at,
            terminal_state="failed",
            terminal_outcome="failed",
            writeback=product_store.ExecutionFinishWriteback(
                phase="older-failed",
                outcome="failed",
                finished_at=older_terminal_at,
                summary={"winner": "older"},
                results={"winner": "older"},
            ),
        )
    elif older_transition == "merge":
        settled = provider.commit_claimed_execution(
            "run-001",
            "flow-older",
            worker_id=worker_id,
            terminal_at=older_terminal_at,
            terminal_state="completed",
            terminal_outcome="succeeded",
            writeback=product_store.ExecutionMergeWriteback(results={"older": "must-not-merge"}),
        )
    elif older_transition == "abandon":
        settled = provider.abandon_execution("run-001", "flow-older", terminal_at=older_terminal_at)
    elif older_transition == "interrupt":
        settled = provider.interrupt_execution("run-001", "flow-older", terminal_at=older_terminal_at)
    elif older_transition == "cancel":
        settled = provider.cancel_execution("run-001", "flow-older", terminal_at=older_terminal_at)
    elif older_transition == "expire":
        settled = provider.expire_execution_cancellation("run-001", "flow-older", terminal_at=older_terminal_at)
    else:
        settled = provider.acknowledge_execution_cancellation(
            "run-001",
            "flow-older",
            acknowledged_at=older_terminal_at,
            response_status=202,
            response_body={"accepted": True},
        )

    assert settled is (older_transition != "late-ack")
    stored = provider.lookup_run("run-001").value
    assert stored is not None
    assert tuple(link.flow_run_id for link in stored.prefect_executions) == ("flow-older", "flow-latest")
    assert (stored.phase, stored.outcome, stored.finished_at) == (
        "newest-complete",
        "newest-result",
        newest_finished_at,
    )
    assert stored.summary == {"winner": "newest"}
    assert stored.results == {"winner": "newest"}
    assert stored.prefect_executions[0].terminal_at == older_terminal_at
    if receipt is not None:
        replay = provider.lookup_mutation(receipt.actor, receipt.key_digest).value
        assert replay is not None
        assert replay.response_status == (202 if older_transition == "cancel" else 503)


def test_cross_link_late_writer_cannot_overwrite_newer_execution_result(provider: ProductProjection) -> None:
    """Independent link writers retain the latest accepted execution's product result."""
    now = datetime(2026, 8, 29, 12, tzinfo=timezone.utc)
    worker_id = "8c1da53d-0e6b-4d3d-a0f1-97b6a9ccebf0"
    provider.create_run(_run())
    for attempt, flow_run_id in enumerate(("flow-older", "flow-latest"), start=1):
        provider.add_prefect_execution(
            "run-001",
            PrefectExecutionLink(flow_run_id=flow_run_id, purpose="plan", attempt=attempt, submitted_at=now),
        )
        assert provider.claim_execution("run-001", flow_run_id, worker_id=worker_id, claimed_at=now)
    start = Barrier(2)
    newest_committed = Event()

    def commit_latest() -> bool:
        start.wait()
        committed = provider.commit_claimed_execution(
            "run-001",
            "flow-latest",
            worker_id=worker_id,
            terminal_at=now + timedelta(seconds=1),
            terminal_state="completed",
            terminal_outcome="succeeded",
            writeback=product_store.ExecutionFinishWriteback(
                phase="newest-complete",
                outcome="newest-result",
                finished_at=now + timedelta(seconds=1),
                summary={"winner": "newest"},
                results={"winner": "newest"},
            ),
        )
        newest_committed.set()
        return committed

    def commit_older_late() -> bool:
        start.wait()
        assert newest_committed.wait(timeout=2)
        return provider.commit_claimed_execution(
            "run-001",
            "flow-older",
            worker_id=worker_id,
            terminal_at=now + timedelta(seconds=2),
            terminal_state="failed",
            terminal_outcome="failed",
            writeback=product_store.ExecutionFinishWriteback(
                phase="older-failed",
                outcome="failed",
                finished_at=now + timedelta(seconds=2),
                summary={"winner": "older"},
                results={"winner": "older"},
            ),
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = tuple(pool.map(lambda action: action(), (commit_latest, commit_older_late)))

    assert outcomes == (True, True)
    stored = provider.lookup_run("run-001").value
    assert stored is not None
    assert (stored.phase, stored.outcome, stored.results) == (
        "newest-complete",
        "newest-result",
        {"winner": "newest"},
    )
    assert all(link.terminal_at is not None for link in stored.prefect_executions)


def test_cancellation_saga_persists_intent_acknowledgement_and_clean_terminal(provider: ProductProjection) -> None:
    """Cancellation proof and its accepted receipt share provider transactions."""
    now = datetime(2026, 8, 29, 12, tzinfo=timezone.utc)
    receipt = _receipt(operation="cancel", target_run_id="run-001")
    provider.create_run(_run())
    provider.add_prefect_execution(
        "run-001", PrefectExecutionLink(flow_run_id="flow-001", purpose="plan", attempt=1, submitted_at=now)
    )
    provider.reserve_mutation(receipt)
    assert provider.claim_mutation(receipt.receipt_id)
    deadline = now + timedelta(seconds=30)

    assert provider.request_execution_cancellation(
        "run-001",
        "flow-001",
        requested_at=now,
        recovery_deadline_at=deadline,
        recovery_seconds=30,
        expected_latest_position=0,
        receipt_id=receipt.receipt_id,
    )
    assert not provider.claim_execution(
        "run-001", "flow-001", worker_id="8c1da53d-0e6b-4d3d-a0f1-97b6a9ccebf0", claimed_at=now
    )
    accepted = {"run": {"run_id": "run-001"}, "orchestration": []}
    assert provider.acknowledge_execution_cancellation(
        "run-001",
        "flow-001",
        acknowledged_at=now + timedelta(seconds=1),
        response_status=202,
        response_body=accepted,
    )
    provider.observe_prefect_execution("run-001", "flow-001", state="Cancelled")
    assert not provider.cancel_execution("run-001", "flow-001", terminal_at=deadline)
    provider.observe_prefect_execution("run-001", "flow-001", state="cancelled")
    assert provider.cancel_execution("run-001", "flow-001", terminal_at=deadline)

    stored = provider.lookup_run("run-001").value
    assert stored is not None
    assert (stored.phase, stored.outcome) == ("cancelled", "cancelled")
    assert stored.prefect_executions[0].terminal_state == "cancelled"
    replay = provider.lookup_mutation(receipt.actor, receipt.key_digest).value
    assert replay is not None
    assert (replay.state, replay.response_status, replay.response_body) == ("accepted", 202, accepted)


@pytest.mark.parametrize("winner", ["append", "intent"])
def test_execution_append_and_cancellation_intent_serialize_in_both_orders(
    provider: ProductProjection,
    winner: str,
) -> None:
    """The first durable operation fences the reciprocal execution-order race."""
    now = datetime(2026, 8, 29, 12, tzinfo=timezone.utc)
    receipt = _receipt(operation="cancel", target_run_id="run-001")
    provider.create_run(_run())
    provider.add_prefect_execution(
        "run-001", PrefectExecutionLink(flow_run_id="flow-old", purpose="plan", attempt=1, submitted_at=now)
    )
    provider.reserve_mutation(receipt)
    assert provider.claim_mutation(receipt.receipt_id)

    def append() -> bool:
        try:
            provider.add_prefect_execution(
                "run-001",
                PrefectExecutionLink(
                    flow_run_id="flow-new",
                    purpose="verify",
                    attempt=1,
                    submitted_at=now + timedelta(seconds=1),
                ),
            )
        except WriteAdmissionConflictError:
            return False
        return True

    def request_intent() -> bool:
        return provider.request_execution_cancellation(
            "run-001",
            "flow-old",
            requested_at=now,
            recovery_deadline_at=now + timedelta(seconds=30),
            recovery_seconds=30,
            expected_latest_position=0,
            receipt_id=receipt.receipt_id,
        )

    outcomes = (append(), request_intent()) if winner == "append" else (request_intent(), append())

    assert outcomes == (True, False)
    stored = provider.lookup_run("run-001").value
    assert stored is not None
    if winner == "append":
        assert [link.flow_run_id for link in stored.prefect_executions] == ["flow-old", "flow-new"]
        assert stored.prefect_executions[0].cancellation_requested_at is None
    else:
        assert [link.flow_run_id for link in stored.prefect_executions] == ["flow-old"]
        assert stored.prefect_executions[0].cancellation_receipt_id == receipt.receipt_id


def test_execution_append_and_cancellation_intent_race_has_one_winner(provider: ProductProjection) -> None:
    """Run-row serialization gives SQLite and PostgreSQL one reciprocal winner."""
    now = datetime(2026, 8, 29, 12, tzinfo=timezone.utc)
    receipt = _receipt(operation="cancel", target_run_id="run-001")
    provider.create_run(_run())
    provider.add_prefect_execution(
        "run-001", PrefectExecutionLink(flow_run_id="flow-old", purpose="plan", attempt=1, submitted_at=now)
    )
    provider.reserve_mutation(receipt)
    assert provider.claim_mutation(receipt.receipt_id)
    barrier = Barrier(2)

    def append() -> bool:
        barrier.wait()
        try:
            provider.add_prefect_execution(
                "run-001",
                PrefectExecutionLink(
                    flow_run_id="flow-new",
                    purpose="verify",
                    attempt=1,
                    submitted_at=now + timedelta(seconds=1),
                ),
            )
        except WriteAdmissionConflictError:
            return False
        return True

    def request_intent() -> bool:
        barrier.wait()
        return provider.request_execution_cancellation(
            "run-001",
            "flow-old",
            requested_at=now,
            recovery_deadline_at=now + timedelta(seconds=30),
            recovery_seconds=30,
            expected_latest_position=0,
            receipt_id=receipt.receipt_id,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = tuple(pool.map(lambda action: action(), (append, request_intent)))

    assert outcomes.count(True) == 1


def test_cancellation_intent_admission_revalidates_only_its_exact_receipt_owner(
    provider: ProductProjection,
) -> None:
    """A retry reuses one intent while a different receipt cannot claim that link."""
    now = datetime(2026, 8, 29, 12, tzinfo=timezone.utc)
    owner = _receipt(operation="cancel", target_run_id="run-001")
    competitor = _receipt(
        "mutation-competitor",
        operation="cancel",
        target_run_id="run-001",
        client_key="competitor-key",
    )
    provider.create_run(_run())
    provider.add_prefect_execution(
        "run-001", PrefectExecutionLink(flow_run_id="flow-old", purpose="plan", attempt=1, submitted_at=now)
    )
    for receipt in (owner, competitor):
        provider.reserve_mutation(receipt)
        assert provider.claim_mutation(receipt.receipt_id)

    def request(receipt: MutationReceipt) -> bool:
        return provider.request_execution_cancellation(
            "run-001",
            "flow-old",
            requested_at=now,
            recovery_deadline_at=now + timedelta(seconds=30),
            recovery_seconds=30,
            expected_latest_position=0,
            receipt_id=receipt.receipt_id,
        )

    assert request(owner)
    assert request(owner)
    assert not request(competitor)
    stored = provider.lookup_run("run-001").value
    assert stored is not None
    assert stored.prefect_executions[0].cancellation_receipt_id == owner.receipt_id


def test_cancellation_intent_rejects_naive_time_without_partial_state(provider: ProductProjection) -> None:
    now = datetime(2026, 8, 29, 12, tzinfo=timezone.utc)
    receipt = _receipt(operation="cancel", target_run_id="run-001")
    provider.create_run(_run())
    provider.add_prefect_execution(
        "run-001", PrefectExecutionLink(flow_run_id="flow-001", purpose="plan", attempt=1, submitted_at=now)
    )
    provider.reserve_mutation(receipt)
    assert provider.claim_mutation(receipt.receipt_id)

    with pytest.raises(ValueError, match="timezone"):
        provider.request_execution_cancellation(
            "run-001",
            "flow-001",
            requested_at=now.replace(tzinfo=None),
            recovery_deadline_at=now + timedelta(seconds=30),
            recovery_seconds=30,
            expected_latest_position=0,
            receipt_id=receipt.receipt_id,
        )

    stored = provider.lookup_run("run-001").value
    assert stored is not None
    assert stored.prefect_executions[0].cancellation_requested_at is None


@pytest.mark.parametrize("offset", [30, 29, 31, -30])
def test_cancellation_intent_requires_the_policy_owned_recovery_deadline(
    provider: ProductProjection, offset: int
) -> None:
    """The persistence CAS cannot accept a caller-chosen cancellation fence."""
    now = datetime(2026, 8, 29, 12, tzinfo=timezone.utc)
    receipt = _receipt(operation="cancel", target_run_id="run-001")
    provider.create_run(_run())
    provider.add_prefect_execution(
        "run-001", PrefectExecutionLink(flow_run_id="flow-001", purpose="plan", attempt=1, submitted_at=now)
    )
    provider.reserve_mutation(receipt)
    assert provider.claim_mutation(receipt.receipt_id)

    if offset == 30:
        assert provider.request_execution_cancellation(
            "run-001",
            "flow-001",
            requested_at=now,
            recovery_deadline_at=now + timedelta(seconds=offset),
            recovery_seconds=30,
            expected_latest_position=0,
            receipt_id=receipt.receipt_id,
        )
    else:
        with pytest.raises(ValueError, match=r"^execution cancellation recovery deadline is invalid$"):
            provider.request_execution_cancellation(
                "run-001",
                "flow-001",
                requested_at=now,
                recovery_deadline_at=now + timedelta(seconds=offset),
                recovery_seconds=30,
                expected_latest_position=0,
                receipt_id=receipt.receipt_id,
            )
        stored = provider.lookup_run("run-001").value
        assert stored is not None
        assert stored.prefect_executions[0].cancellation_requested_at is None


@pytest.mark.parametrize("claim_state", ["unclaimed", "claimed"])
@pytest.mark.parametrize("ack_state", ["unacknowledged", "acknowledged"])
def test_cancellation_expiry_is_inclusive_bounded_and_receipt_stable(
    provider: ProductProjection, claim_state: str, ack_state: str
) -> None:
    """Recovery fences normal liveness only through one fixed inclusive deadline."""
    now = datetime(2026, 8, 29, 12, tzinfo=timezone.utc)
    worker_id = "8c1da53d-0e6b-4d3d-a0f1-97b6a9ccebf0"
    receipt = _receipt(operation="cancel", target_run_id="run-001")
    provider.create_run(_run())
    provider.add_prefect_execution(
        "run-001", PrefectExecutionLink(flow_run_id="flow-001", purpose="plan", attempt=1, submitted_at=now)
    )
    claimed = claim_state == "claimed"
    acknowledged = ack_state == "acknowledged"
    if claimed:
        assert provider.claim_execution("run-001", "flow-001", worker_id=worker_id, claimed_at=now)
    provider.reserve_mutation(receipt)
    assert provider.claim_mutation(receipt.receipt_id)
    deadline = now + timedelta(seconds=30)
    assert provider.request_execution_cancellation(
        "run-001",
        "flow-001",
        requested_at=now,
        recovery_deadline_at=deadline,
        recovery_seconds=30,
        expected_latest_position=0,
        receipt_id=receipt.receipt_id,
    )
    accepted = {"run": {"run_id": "run-001"}, "orchestration": []}
    if acknowledged:
        assert provider.acknowledge_execution_cancellation(
            "run-001",
            "flow-001",
            acknowledged_at=now + timedelta(seconds=1),
            response_status=202,
            response_body=accepted,
        )

    assert not provider.abandon_execution("run-001", "flow-001", terminal_at=deadline - timedelta(seconds=1))
    assert not provider.interrupt_execution("run-001", "flow-001", terminal_at=deadline - timedelta(seconds=1))
    assert not provider.expire_execution_cancellation(
        "run-001", "flow-001", terminal_at=deadline - timedelta(microseconds=1)
    )
    assert provider.expire_execution_cancellation("run-001", "flow-001", terminal_at=deadline)
    assert not provider.expire_execution_cancellation("run-001", "flow-001", terminal_at=deadline)

    stored = provider.lookup_run("run-001").value
    replay = provider.lookup_mutation(receipt.actor, receipt.key_digest).value
    assert stored is not None
    assert replay is not None
    expected = ("interrupted", "ambiguous") if claimed else ("abandoned", "abandoned")
    assert (stored.phase, stored.outcome) == expected
    assert (stored.prefect_executions[0].terminal_state, stored.prefect_executions[0].terminal_outcome) == expected
    assert replay.response_status == (202 if acknowledged else 503)
    assert replay.response_body == (accepted if acknowledged else replay.response_body)
    if not acknowledged:
        assert replay.response_body is not None
        assert replay.response_body["error"]["code"] == "cancellation-unconfirmed"


@pytest.mark.parametrize("acknowledged_offset", [30, 31])
def test_cancellation_acknowledgement_at_or_after_deadline_atomically_expires(
    provider: ProductProjection,
    acknowledged_offset: int,
) -> None:
    """A late remote acknowledgement cannot cross the inclusive recovery fence."""
    now = datetime(2026, 8, 29, 12, tzinfo=timezone.utc)
    deadline = now + timedelta(seconds=30)
    receipt = _receipt(operation="cancel", target_run_id="run-001")
    provider.create_run(_run())
    provider.add_prefect_execution(
        "run-001", PrefectExecutionLink(flow_run_id="flow-001", purpose="plan", attempt=1, submitted_at=now)
    )
    provider.reserve_mutation(receipt)
    assert provider.claim_mutation(receipt.receipt_id)
    assert provider.request_execution_cancellation(
        "run-001",
        "flow-001",
        requested_at=now,
        recovery_deadline_at=deadline,
        recovery_seconds=30,
        expected_latest_position=0,
        receipt_id=receipt.receipt_id,
    )

    assert not provider.acknowledge_execution_cancellation(
        "run-001",
        "flow-001",
        acknowledged_at=now + timedelta(seconds=acknowledged_offset),
        response_status=202,
        response_body={"accepted": True},
    )

    stored = provider.lookup_run("run-001").value
    replay = provider.lookup_mutation(receipt.actor, receipt.key_digest).value
    assert stored is not None
    assert replay is not None
    assert (stored.phase, stored.outcome) == ("abandoned", "abandoned")
    assert stored.prefect_executions[0].cancellation_acknowledged_at is None
    assert stored.prefect_executions[0].terminal_state == "abandoned"
    assert replay.response_status == 503
    assert replay.response_body is not None
    assert replay.response_body["error"]["code"] == "cancellation-unconfirmed"


def test_cancellation_acknowledgement_and_expiry_race_converges_at_deadline(
    provider: ProductProjection,
) -> None:
    """Concurrent acknowledgement cannot revive or overwrite the inclusive expiry verdict."""
    now = datetime(2026, 8, 29, 12, tzinfo=timezone.utc)
    deadline = now + timedelta(seconds=30)
    receipt = _receipt(operation="cancel", target_run_id="run-001")
    provider.create_run(_run())
    provider.add_prefect_execution(
        "run-001", PrefectExecutionLink(flow_run_id="flow-001", purpose="plan", attempt=1, submitted_at=now)
    )
    provider.reserve_mutation(receipt)
    assert provider.claim_mutation(receipt.receipt_id)
    assert provider.request_execution_cancellation(
        "run-001",
        "flow-001",
        requested_at=now,
        recovery_deadline_at=deadline,
        recovery_seconds=30,
        expected_latest_position=0,
        receipt_id=receipt.receipt_id,
    )
    barrier = Barrier(2)

    def acknowledge() -> bool:
        barrier.wait()
        return provider.acknowledge_execution_cancellation(
            "run-001",
            "flow-001",
            acknowledged_at=deadline,
            response_status=202,
            response_body={"accepted": True},
        )

    def expire() -> bool:
        barrier.wait()
        return provider.expire_execution_cancellation("run-001", "flow-001", terminal_at=deadline)

    with ThreadPoolExecutor(max_workers=2) as pool:
        acknowledgement, _expiry = tuple(pool.map(lambda action: action(), (acknowledge, expire)))

    assert not acknowledgement
    stored = provider.lookup_run("run-001").value
    replay = provider.lookup_mutation(receipt.actor, receipt.key_digest).value
    assert stored is not None
    assert replay is not None
    assert stored.prefect_executions[0].terminal_state == "abandoned"
    assert stored.prefect_executions[0].cancellation_acknowledged_at is None
    assert replay.response_status == 503


def test_business_commit_wins_unacknowledged_cancellation_and_settles_receipt(provider: ProductProjection) -> None:
    """A known worker result is authoritative and makes cancellation replay too late."""
    now = datetime(2026, 8, 29, 12, tzinfo=timezone.utc)
    worker_id = "8c1da53d-0e6b-4d3d-a0f1-97b6a9ccebf0"
    receipt = _receipt(operation="cancel", target_run_id="run-001")
    provider.create_run(_run())
    provider.add_prefect_execution(
        "run-001", PrefectExecutionLink(flow_run_id="flow-001", purpose="verify", attempt=1, submitted_at=now)
    )
    assert provider.claim_execution("run-001", "flow-001", worker_id=worker_id, claimed_at=now)
    provider.reserve_mutation(receipt)
    assert provider.claim_mutation(receipt.receipt_id)
    assert provider.request_execution_cancellation(
        "run-001",
        "flow-001",
        requested_at=now,
        recovery_deadline_at=now + timedelta(seconds=30),
        recovery_seconds=30,
        expected_latest_position=0,
        receipt_id=receipt.receipt_id,
    )

    assert provider.commit_claimed_execution(
        "run-001",
        "flow-001",
        worker_id=worker_id,
        terminal_at=now + timedelta(seconds=2),
        terminal_state="completed",
        terminal_outcome="succeeded",
        writeback=product_store.ExecutionMergeWriteback(results={"verification": {"outcome": "verified"}}),
    )

    stored = provider.lookup_run("run-001").value
    replay = provider.lookup_mutation(receipt.actor, receipt.key_digest).value
    assert stored is not None
    assert replay is not None
    assert stored.results == {"verification": {"outcome": "verified"}}
    assert stored.prefect_executions[0].terminal_state == "completed"
    assert replay.response_status == 409
    assert replay.response_body is not None
    assert replay.response_body["error"]["code"] == "execution-terminal"


def test_external_cancelled_observation_without_acknowledgement_is_not_clean(provider: ProductProjection) -> None:
    """External cancellation never fabricates acknowledgement or a clean cancelled verdict."""
    now = datetime(2026, 8, 29, 12, tzinfo=timezone.utc)
    receipt = _receipt(operation="cancel", target_run_id="run-001")
    provider.create_run(_run())
    provider.add_prefect_execution(
        "run-001", PrefectExecutionLink(flow_run_id="flow-001", purpose="plan", attempt=1, submitted_at=now)
    )
    provider.reserve_mutation(receipt)
    assert provider.claim_mutation(receipt.receipt_id)
    deadline = now + timedelta(seconds=30)
    assert provider.request_execution_cancellation(
        "run-001",
        "flow-001",
        requested_at=now,
        recovery_deadline_at=deadline,
        recovery_seconds=30,
        expected_latest_position=0,
        receipt_id=receipt.receipt_id,
    )
    provider.observe_prefect_execution("run-001", "flow-001", state="cancelled")

    assert not provider.cancel_execution("run-001", "flow-001", terminal_at=deadline)
    assert provider.expire_execution_cancellation("run-001", "flow-001", terminal_at=deadline)
    stored = provider.lookup_run("run-001").value
    assert stored is not None
    assert stored.prefect_executions[0].terminal_state == "abandoned"


def test_terminal_cancelled_observation_serializes_before_expiry(provider: ProductProjection) -> None:
    """At equality, durable acknowledged cancelled evidence makes expiry ineligible."""
    now = datetime(2026, 8, 29, 12, tzinfo=timezone.utc)
    receipt = _receipt(operation="cancel", target_run_id="run-001")
    provider.create_run(_run())
    provider.add_prefect_execution(
        "run-001", PrefectExecutionLink(flow_run_id="flow-001", purpose="plan", attempt=1, submitted_at=now)
    )
    provider.reserve_mutation(receipt)
    assert provider.claim_mutation(receipt.receipt_id)
    deadline = now + timedelta(seconds=30)
    assert provider.request_execution_cancellation(
        "run-001",
        "flow-001",
        requested_at=now,
        recovery_deadline_at=deadline,
        recovery_seconds=30,
        expected_latest_position=0,
        receipt_id=receipt.receipt_id,
    )
    assert provider.acknowledge_execution_cancellation(
        "run-001",
        "flow-001",
        acknowledged_at=now + timedelta(seconds=1),
        response_status=202,
        response_body={"run": {"run_id": "run-001"}, "orchestration": []},
    )
    provider.observe_prefect_execution("run-001", "flow-001", state="cancelled")
    barrier = Barrier(2)

    def cancel() -> bool:
        barrier.wait()
        return provider.cancel_execution("run-001", "flow-001", terminal_at=deadline)

    def expire() -> bool:
        barrier.wait()
        return provider.expire_execution_cancellation("run-001", "flow-001", terminal_at=deadline)

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = tuple(pool.map(lambda action: action(), (cancel, expire)))

    assert outcomes.count(True) == 1
    stored = provider.lookup_run("run-001").value
    assert stored is not None
    assert stored.prefect_executions[0].terminal_state == "cancelled"


def test_claimed_success_and_failure_writebacks_have_one_consistent_winner(provider: ProductProjection) -> None:
    """Competing known worker verdicts cannot split link and product state."""
    now = datetime(2026, 8, 29, 12, tzinfo=timezone.utc)
    worker_id = "8c1da53d-0e6b-4d3d-a0f1-97b6a9ccebf0"
    provider.create_run(_run())
    provider.add_prefect_execution(
        "run-001", PrefectExecutionLink(flow_run_id="flow-001", purpose="plan", attempt=1, submitted_at=now)
    )
    assert provider.claim_execution("run-001", "flow-001", worker_id=worker_id, claimed_at=now)
    barrier = Barrier(2)

    def commit_success() -> bool:
        barrier.wait()
        return provider.commit_claimed_execution(
            "run-001",
            "flow-001",
            worker_id=worker_id,
            terminal_at=now + timedelta(seconds=1),
            terminal_state="completed",
            terminal_outcome="succeeded",
            writeback=product_store.ExecutionFinishWriteback(
                phase="planned",
                outcome="planned",
                finished_at=now + timedelta(seconds=1),
                summary={"winner": "success"},
                results={"winner": "success"},
            ),
        )

    def commit_failure() -> bool:
        barrier.wait()
        return provider.commit_claimed_execution(
            "run-001",
            "flow-001",
            worker_id=worker_id,
            terminal_at=now + timedelta(seconds=1),
            terminal_state="failed",
            terminal_outcome="failed",
            writeback=product_store.ExecutionFinishWriteback(
                phase="plan-failed",
                outcome="failed",
                finished_at=now + timedelta(seconds=1),
                summary={"winner": "failure"},
                results={"winner": "failure"},
            ),
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = tuple(pool.map(lambda action: action(), (commit_success, commit_failure)))

    assert outcomes.count(True) == 1
    stored = provider.lookup_run("run-001").value
    assert stored is not None
    winner = stored.results["winner"]
    expected = ("completed", "succeeded") if winner == "success" else ("failed", "failed")
    assert (stored.prefect_executions[0].terminal_state, stored.prefect_executions[0].terminal_outcome) == expected


def test_duplicate_prefect_execution_is_rejected_when_appended(provider: ProductProjection) -> None:
    link = PrefectExecutionLink(flow_run_id="flow-001", purpose="plan", attempt=1)
    provider.create_run(_run())
    provider.add_prefect_execution("run-001", link)

    with pytest.raises(DuplicatePrefectExecutionError, match="already linked"):
        provider.add_prefect_execution("run-001", link)


def test_prefect_position_conflict_retries_without_misreporting_a_duplicate(tmp_path: Path) -> None:
    records = _PositionConflictOnceSQLiteStore(tmp_path / "records.sqlite3")
    projection = ProductProjection(records, FileArtifactStore(tmp_path / "objects"))
    projection.create_run(_run())

    projection.add_prefect_execution(
        "run-001",
        PrefectExecutionLink(flow_run_id="flow-001", purpose="plan", attempt=1),
    )

    assert records.insert_calls == 2
    loaded = projection.lookup_run("run-001").value
    assert loaded is not None
    assert [link.flow_run_id for link in loaded.prefect_executions] == ["flow-001"]


def test_service_prefect_attempt_ordinals_are_allocated_atomically(provider: ProductProjection) -> None:
    provider.create_run(_run())

    def append(position: int) -> PrefectExecutionLink:
        return provider.add_prefect_execution(
            "run-001",
            PrefectExecutionLink(flow_run_id=f"flow-{position}", purpose="verify", attempt=1),
            allocate_attempt=True,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        allocated = list(pool.map(append, range(2)))

    assert sorted(link.attempt for link in allocated) == [1, 2]
    run = provider.lookup_run("run-001").value
    assert run is not None
    assert sorted(link.attempt for link in run.prefect_executions if link.purpose == "verify") == [1, 2]


def test_mutator_existence_checks_do_not_hydrate_the_run(tmp_path: Path) -> None:
    records = _NoHydratingLookupSQLiteStore(tmp_path / "records.sqlite3")
    projection = ProductProjection(records, FileArtifactStore(tmp_path / "objects"))
    records.create(_run())

    projection.add_prefect_execution(
        "run-001",
        PrefectExecutionLink(flow_run_id="flow-001", purpose="plan", attempt=1),
    )
    projection.publish_artifact("run-001", artifact_id="plan", kind="plan", media_type="application/json", data=b"{}")


def test_missing_run_prefect_mutator_raises_specific_public_error(provider: ProductProjection) -> None:
    with pytest.raises(RunNotFoundError, match="unavailable Sync run ID"):
        provider.add_prefect_execution(
            "missing-run",
            PrefectExecutionLink(flow_run_id="flow-001", purpose="plan", attempt=1),
        )


def test_missing_run_artifact_mutator_raises_specific_public_error(provider: ProductProjection) -> None:
    with pytest.raises(RunNotFoundError, match="unavailable Sync run ID"):
        provider.publish_artifact(
            "missing-run", artifact_id="plan", kind="plan", media_type="application/json", data=b"{}"
        )


def test_missing_run_finish_mutator_raises_specific_public_error(provider: ProductProjection) -> None:
    with pytest.raises(RunNotFoundError, match="is unavailable"):
        provider.finish_run("missing-run", phase="planned", outcome="succeeded", summary={}, results={})


def test_finish_detects_a_run_deleted_between_check_and_write(tmp_path: Path) -> None:
    records = _DeleteBeforeFinishSQLiteStore(tmp_path / "records.sqlite3")
    projection = ProductProjection(records, FileArtifactStore(tmp_path / "objects"))
    projection.create_run(_run())

    with pytest.raises(RunNotFoundError, match="Cannot finish unavailable"):
        projection.finish_run("run-001", phase="planned", outcome="succeeded", summary={}, results={})

    assert projection.lookup_run("run-001").reason == "run-not-found"


def test_duplicate_flow_run_id_is_rejected_before_a_provider_write() -> None:
    duplicate = PrefectExecutionLink(flow_run_id="same", purpose="plan", attempt=1)

    with pytest.raises(ValidationError, match="flow-run IDs must be unique"):
        _run(links=(duplicate, duplicate))


def test_stable_sync_run_id_lookup_and_duplicate_rejection(provider: ProductProjection) -> None:
    expected = _run()
    provider.create_run(expected)

    with pytest.raises(DuplicateRunError, match="already exists"):
        provider.create_run(expected)

    missing = provider.lookup_run("run-missing")
    assert not missing.available
    assert missing.reason == "run-not-found"


def test_completed_records_cannot_bypass_artifact_verification(provider: ProductProjection) -> None:
    completed = _run().model_copy(update={"finished_at": datetime.now(timezone.utc), "outcome": "succeeded"})

    with pytest.raises(ValueError, match="must be unfinished"):
        provider.create_run(completed)


def test_published_artifact_is_immutable_and_resolves_only_through_its_run(provider: ProductProjection) -> None:
    provider.create_run(_run())
    reference = provider.publish_artifact(
        "run-001",
        artifact_id="saved-plan",
        kind="plan",
        media_type="application/json",
        data=b'{"planned":true}',
    )

    assert reference.digest in reference.object_key
    assert provider.lookup_artifact("run-001", "saved-plan").value == b'{"planned":true}'
    assert provider.lookup_artifact("run-missing", "saved-plan").reason == "run-not-found"
    assert provider.lookup_artifact("run-001", "not-attached").reason == "artifact-reference-not-found"
    with pytest.raises(DuplicateArtifactError, match="already published"):
        provider.publish_artifact(
            "run-001",
            artifact_id="saved-plan",
            kind="plan",
            media_type="application/json",
            data=b'{"planned":true}',
        )


def test_reviewed_apply_extends_plan_record_without_a_second_run_id(provider: ProductProjection) -> None:
    provider.create_run(_run())
    provider.publish_artifact("run-001", artifact_id="plan", kind="plan", media_type="application/json", data=b"{}")
    provider.finish_run("run-001", phase="planned", outcome="succeeded", summary={"create": 1}, results={})

    provider.publish_artifact(
        "run-001", artifact_id="apply-result", kind="result", media_type="application/json", data=b"{}"
    )
    provider.finish_run("run-001", phase="applied", outcome="succeeded", summary={"applied": 1}, results={"written": 1})

    result = provider.lookup_run("run-001").value
    assert result is not None
    assert result.run_id == "run-001"
    assert result.operation == "plan"
    assert result.phase == "applied"
    assert [reference.artifact_id for reference in result.artifact_refs] == ["apply-result", "plan"]


@pytest.mark.parametrize("profile", ["local", "production"])
def test_interrupted_publication_can_resume_and_finish_the_run(profile: str, tmp_path: Path) -> None:
    fake_s3 = _FakeS3()
    if profile == "local":
        records = SQLiteRunStore(tmp_path / "records.sqlite3")
        artifacts = _FailingFileStore(tmp_path / "objects")
    else:
        records = PostgreSQLRunStore(_connect(tmp_path / "postgres-emulator.sqlite3"))
        artifacts = _FailingS3Store(fake_s3, bucket="artifacts")
    projection = ProductProjection(records, artifacts)
    projection.create_run(_run())

    with pytest.raises(OSError, match="injected interruption"):
        projection.publish_artifact(
            "run-001", artifact_id="plan", kind="plan", media_type="application/json", data=b"{}"
        )

    record = projection.lookup_run("run-001").value
    assert record is not None
    assert record.artifact_refs == ()
    assert record.outcome is None
    assert projection.lookup_artifact("run-001", "plan").reason == "artifact-publication-incomplete"
    with pytest.raises(ArtifactUnavailableError, match="incomplete artifact publication"):
        projection.finish_run("run-001", phase="planned", outcome="succeeded", summary={}, results={})
    projection.finish_run(
        "run-001",
        phase="plan-failed",
        outcome="failed",
        summary={"failed_stage": "plan"},
        results={"plan_failure": {"outcome": "failed"}},
    )
    failed = projection.lookup_run("run-001").value
    assert failed is not None
    assert failed.phase == "plan-failed"
    assert failed.outcome == "failed"
    assert failed.finished_at is not None
    assert projection.lookup_artifact("run-001", "plan").reason == "artifact-publication-incomplete"
    if profile == "production":
        assert not [key for (_, key) in fake_s3.objects if key.endswith("manifest.json")]
    else:
        assert not list((tmp_path / "objects").rglob("manifest.json"))

    if profile == "local":
        restarted = ProductProjection(
            SQLiteRunStore(tmp_path / "records.sqlite3"), FileArtifactStore(tmp_path / "objects")
        )
    else:
        restarted = ProductProjection(
            PostgreSQLRunStore(_connect(tmp_path / "postgres-emulator.sqlite3")),
            S3ArtifactStore(fake_s3, bucket="artifacts"),
        )
    assert restarted.lookup_artifact("run-001", "plan").reason == "artifact-publication-incomplete"

    reference = restarted.publish_artifact(
        "run-001", artifact_id="plan", kind="plan", media_type="application/json", data=b"{}"
    )
    assert restarted.lookup_artifact("run-001", "plan").value == b"{}"
    restarted.finish_run("run-001", phase="planned", outcome="succeeded", summary={}, results={})
    completed = restarted.lookup_run("run-001").value
    assert completed is not None
    assert completed.outcome == "succeeded"
    assert completed.artifact_refs == (reference,)


@pytest.mark.parametrize("profile", ["local", "production"])
def test_manifest_complete_but_relational_mark_failed_resumes_without_republication(
    profile: str, tmp_path: Path
) -> None:
    fake_s3 = _FakeS3()
    if profile == "local":
        records = _FailingMarkSQLiteStore(tmp_path / "records.sqlite3")
        artifacts = FileArtifactStore(tmp_path / "objects")
    else:
        records = _FailingMarkPostgreSQLStore(_connect(tmp_path / "postgres-emulator.sqlite3"))
        artifacts = S3ArtifactStore(fake_s3, bucket="artifacts")
    projection = ProductProjection(records, artifacts)
    projection.create_run(_run())

    with pytest.raises(OSError, match="injected mark failure"):
        projection.publish_artifact(
            "run-001", artifact_id="plan", kind="plan", media_type="application/json", data=b"{}"
        )

    run = projection.lookup_run("run-001").value
    assert run is not None
    assert run.artifact_refs == ()
    assert projection.lookup_artifact("run-001", "plan").reason == "artifact-publication-incomplete"
    if profile == "local":
        assert len(list((tmp_path / "objects").rglob("manifest.json"))) == 1
        counting = _CountingArtifactStore(FileArtifactStore(tmp_path / "objects"))
        restarted = ProductProjection(SQLiteRunStore(tmp_path / "records.sqlite3"), counting)
    else:
        assert len([key for (_, key) in fake_s3.objects if key.endswith("manifest.json")]) == 1
        counting = _CountingArtifactStore(S3ArtifactStore(fake_s3, bucket="artifacts"))
        restarted = ProductProjection(
            PostgreSQLRunStore(_connect(tmp_path / "postgres-emulator.sqlite3")),
            counting,
        )
    assert restarted.lookup_artifact("run-001", "plan").reason == "artifact-publication-incomplete"

    reference = restarted.publish_artifact(
        "run-001", artifact_id="plan", kind="plan", media_type="application/json", data=b"{}"
    )
    assert counting.publish_calls == 0
    assert restarted.lookup_artifact("run-001", "plan").value == b"{}"
    restarted.finish_run("run-001", phase="planned", outcome="succeeded", summary={}, results={})
    completed = restarted.lookup_run("run-001").value
    assert completed is not None
    assert completed.outcome == "succeeded"
    assert completed.artifact_refs == (reference,)
    if profile == "local":
        assert len(list((tmp_path / "objects").rglob("manifest.json"))) == 1
    else:
        assert len([key for (_, key) in fake_s3.objects if key.endswith("manifest.json")]) == 1


@pytest.mark.parametrize("profile", ["local", "production"])
def test_published_row_with_missing_manifest_accepts_only_an_exact_repair(profile: str, tmp_path: Path) -> None:
    fake_s3 = _FakeS3()
    if profile == "local":
        projection = ProductProjection(
            SQLiteRunStore(tmp_path / "records.sqlite3"),
            FileArtifactStore(tmp_path / "objects"),
        )
    else:
        projection = ProductProjection(
            PostgreSQLRunStore(_connect(tmp_path / "postgres-emulator.sqlite3")),
            S3ArtifactStore(fake_s3, bucket="artifacts"),
        )
    projection.create_run(_run())
    reference = projection.publish_artifact(
        "run-001", artifact_id="plan", kind="plan", media_type="application/json", data=b"original"
    )
    if profile == "local":
        (tmp_path / "objects" / reference.manifest_key).unlink()
    else:
        fake_s3.delete(bucket="artifacts", key=reference.manifest_key)

    with pytest.raises(DuplicateArtifactError, match="published record with different content or metadata"):
        projection.publish_artifact(
            "run-001", artifact_id="plan", kind="plan", media_type="application/json", data=b"different"
        )

    repaired = projection.publish_artifact(
        "run-001", artifact_id="plan", kind="plan", media_type="application/json", data=b"original"
    )
    assert repaired == reference
    assert projection.lookup_artifact("run-001", "plan").value == b"original"


def test_interrupted_manifest_repair_remains_retryable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    projection = ProductProjection(
        SQLiteRunStore(tmp_path / "records.sqlite3"),
        FileArtifactStore(tmp_path / "objects"),
    )
    projection.create_run(_run())
    reference = projection.publish_artifact(
        "run-001", artifact_id="plan", kind="plan", media_type="application/json", data=b"original"
    )
    manifest = tmp_path / "objects" / reference.manifest_key
    manifest.unlink()
    original_write = product_store_store._write_fsynced
    interrupted = False

    def interrupt_staged_manifest(path: Path, data: bytes) -> None:
        nonlocal interrupted
        if path.parent.name.startswith(".manifest-repair-") and not interrupted:
            interrupted = True
            path.write_bytes(data[:8])
            msg = "injected staged-manifest interruption"
            raise OSError(msg)
        original_write(path, data)

    monkeypatch.setattr(product_store_store, "_write_fsynced", interrupt_staged_manifest)
    with pytest.raises(OSError, match="staged-manifest interruption"):
        projection.publish_artifact(
            "run-001", artifact_id="plan", kind="plan", media_type="application/json", data=b"original"
        )

    assert interrupted
    assert projection.lookup_artifact("run-001", "plan").reason == "manifest-unavailable"
    assert not list(manifest.parent.glob(".manifest-repair-*"))

    monkeypatch.setattr(product_store_store, "_write_fsynced", original_write)
    repaired = projection.publish_artifact(
        "run-001", artifact_id="plan", kind="plan", media_type="application/json", data=b"original"
    )
    assert repaired == reference
    assert projection.lookup_artifact("run-001", "plan").value == b"original"


def test_filesystem_publication_fsyncs_private_directory_before_atomic_rename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[Path] = []
    original = product_store_store._fsync_directory

    def capture(path: Path) -> None:
        calls.append(path)
        original(path)

    monkeypatch.setattr(product_store_store, "_fsync_directory", capture)
    projection = ProductProjection(
        SQLiteRunStore(tmp_path / "records.sqlite3"),
        FileArtifactStore(tmp_path / "objects"),
    )
    projection.create_run(_run())

    projection.publish_artifact("run-001", artifact_id="plan", kind="plan", media_type="application/json", data=b"{}")

    assert calls[0].name.startswith(".plan-")
    assert calls[1] == calls[0].parent


def test_filesystem_cleanup_failure_does_not_mask_publication_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _FailingFileStore(tmp_path / "objects")

    def fail_cleanup(path: Path) -> None:  # noqa: ARG001
        msg = "injected cleanup failure"
        raise OSError(msg)

    monkeypatch.setattr(product_store_store, "_remove_private_publication", fail_cleanup)

    with pytest.raises(OSError, match="injected interruption"):
        store.publish(_artifact_reference(), b"{}")


@pytest.mark.parametrize(
    ("object_basename", "manifest_basename", "manifest_directory"),
    [("payload", "manifest.json", "same"), ("data", "metadata.json", "same"), ("data", "manifest.json", "separate")],
)
def test_filesystem_rejects_unreadable_key_layout_before_writing(
    object_basename: str,
    manifest_basename: str,
    manifest_directory: str,
    tmp_path: Path,
) -> None:
    root = tmp_path / "objects"
    store = FileArtifactStore(root)
    reference = _artifact_reference(
        object_basename=object_basename,
        manifest_basename=manifest_basename,
        separate_manifest_directory=manifest_directory == "separate",
    )

    with pytest.raises(ValueError, match="relative sibling paths"):
        store.publish(reference, b"{}")

    assert not list(root.rglob("*"))


def test_s3_conditional_manifest_conflict_is_a_duplicate_artifact_error() -> None:
    fake_s3 = _ManifestRaceS3()
    store = S3ArtifactStore(fake_s3, bucket="artifacts")
    data = b"{}"
    digest = sha256(data).hexdigest()
    reference = ArtifactReference(
        artifact_id="plan",
        run_id="run-001",
        kind="plan",
        media_type="application/json",
        digest=digest,
        size=len(data),
        object_key=f"runs/run-001/artifacts/plan/{digest}/data",
        manifest_key=f"runs/run-001/artifacts/plan/{digest}/manifest.json",
        created_at=datetime.now(timezone.utc),
    )

    with pytest.raises(DuplicateArtifactError):
        store.publish(reference, data)

    assert fake_s3.get(bucket="artifacts", key=reference.manifest_key) == b"winner"


def test_s3_cleanup_failure_does_not_mask_transport_failure() -> None:
    fake_s3 = _CopyAndCleanupFailingS3()
    store = S3ArtifactStore(fake_s3, bucket="artifacts")

    with pytest.raises(ConnectionError, match="injected copy failure"):
        store.publish(_artifact_reference(), b"{}")


def test_s3_cleanup_is_best_effort_after_successful_manifest_commit() -> None:
    fake_s3 = _CleanupFailingS3()
    store = S3ArtifactStore(fake_s3, bucket="artifacts")
    reference = _artifact_reference()

    store.publish(reference, b"{}")

    assert store.lookup(reference).value == b"{}"


@pytest.mark.parametrize("profile", ["local", "production"])
@pytest.mark.parametrize(
    "retry",
    [
        {"kind": "different"},
        {"media_type": "text/plain"},
        {"data": b"different"},
    ],
    ids=["kind", "media-type", "content"],
)
def test_mismatched_pending_publication_retry_is_rejected_without_overwrite(
    profile: str, retry: dict[str, str | bytes], tmp_path: Path
) -> None:
    fake_s3 = _FakeS3()
    if profile == "local":
        records = SQLiteRunStore(tmp_path / "records.sqlite3")
        artifacts = _FailingFileStore(tmp_path / "objects")
    else:
        records = PostgreSQLRunStore(_connect(tmp_path / "postgres-emulator.sqlite3"))
        artifacts = _FailingS3Store(fake_s3, bucket="artifacts")
    projection = ProductProjection(records, artifacts)
    projection.create_run(_run())

    with pytest.raises(OSError, match="injected interruption"):
        projection.publish_artifact(
            "run-001", artifact_id="plan", kind="plan", media_type="application/json", data=b"{}"
        )

    if profile == "local":
        counting = _CountingArtifactStore(FileArtifactStore(tmp_path / "objects"))
    else:
        counting = _CountingArtifactStore(S3ArtifactStore(fake_s3, bucket="artifacts"))
    resumed = ProductProjection(records, counting)
    kind = retry.get("kind", "plan")
    media_type = retry.get("media_type", "application/json")
    data = retry.get("data", b"{}")
    assert isinstance(kind, str)
    assert isinstance(media_type, str)
    assert isinstance(data, bytes)
    with pytest.raises(DuplicateArtifactError, match="pending publication with different content or metadata"):
        resumed.publish_artifact("run-001", artifact_id="plan", kind=kind, media_type=media_type, data=data)

    assert counting.publish_calls == 0
    assert resumed.lookup_artifact("run-001", "plan").reason == "artifact-publication-incomplete"
    resumed.publish_artifact("run-001", artifact_id="plan", kind="plan", media_type="application/json", data=b"{}")
    assert resumed.lookup_artifact("run-001", "plan").value == b"{}"


@pytest.mark.parametrize("profile", ["local", "production"])
def test_duplicate_artifact_identity_is_rejected_before_a_second_provider_write(profile: str, tmp_path: Path) -> None:
    fake_s3 = _FakeS3()
    if profile == "local":
        records = SQLiteRunStore(tmp_path / "records.sqlite3")
        counting = _CountingArtifactStore(FileArtifactStore(tmp_path / "objects"))
    else:
        records = PostgreSQLRunStore(_connect(tmp_path / "postgres-emulator.sqlite3"))
        counting = _CountingArtifactStore(S3ArtifactStore(fake_s3, bucket="artifacts"))
    projection = ProductProjection(records, counting)
    projection.create_run(_run())
    projection.publish_artifact(
        "run-001", artifact_id="plan", kind="plan", media_type="application/json", data=b"first"
    )

    with pytest.raises(DuplicateArtifactError):
        projection.publish_artifact(
            "run-001", artifact_id="plan", kind="plan", media_type="application/json", data=b"different"
        )

    assert counting.publish_calls == 1
    run = projection.lookup_run("run-001").value
    assert run is not None
    assert len(run.artifact_refs) == 1
    if profile == "local":
        assert len(list((tmp_path / "objects").rglob("manifest.json"))) == 1
    else:
        assert len([key for (_, key) in fake_s3.objects if key.endswith("manifest.json")]) == 1


@pytest.mark.parametrize("profile", ["local", "production"])
def test_missing_object_is_unavailable_without_hiding_run(profile: str, tmp_path: Path) -> None:
    fake_s3 = _FakeS3()
    if profile == "local":
        projection = local_product_projection(tmp_path / "cache")
    else:
        projection = ProductProjection(
            PostgreSQLRunStore(_connect(tmp_path / "postgres-emulator.sqlite3")),
            S3ArtifactStore(fake_s3, bucket="artifacts"),
        )
    projection.create_run(_run())
    reference = projection.publish_artifact(
        "run-001", artifact_id="plan", kind="plan", media_type="application/json", data=b"{}"
    )
    if profile == "local":
        (tmp_path / "cache" / "artifacts" / reference.object_key).unlink()
    else:
        fake_s3.delete(bucket="artifacts", key=reference.object_key)

    assert projection.lookup_artifact("run-001", "plan").reason == "data-unavailable"
    assert projection.lookup_run("run-001").available
    with pytest.raises(ArtifactUnavailableError, match="data-unavailable"):
        projection.finish_run("run-001", phase="planned", outcome="succeeded", summary={}, results={})
    unchanged = projection.lookup_run("run-001").value
    assert unchanged is not None
    assert unchanged.outcome is None


def test_expired_reference_is_explicit_and_run_remains_readable(provider: ProductProjection) -> None:
    digest = "a" * 64
    expired = ArtifactReference(
        artifact_id="expired-plan",
        run_id="run-001",
        kind="plan",
        media_type="application/json",
        digest=digest,
        size=2,
        object_key=f"runs/run-001/artifacts/expired-plan/{digest}/data",
        manifest_key=f"runs/run-001/artifacts/expired-plan/{digest}/manifest.json",
        created_at=datetime(2026, 8, 8, tzinfo=timezone.utc),
        expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
    )
    provider.create_run(_run())
    provider._records.reserve_artifact(expired)
    provider._records.mark_artifact_published(expired)

    assert provider.lookup_artifact("run-001", "expired-plan").reason == "artifact-expired"
    assert provider.lookup_run("run-001").available


@pytest.mark.parametrize(
    ("created_at", "expires_at"),
    [
        (datetime(2026, 8, 8), None),  # noqa: DTZ001 - deliberate naive-value validation case.
        (
            datetime(2026, 8, 8, tzinfo=timezone.utc),
            datetime(2026, 8, 9),  # noqa: DTZ001 - deliberate naive-value validation case.
        ),
    ],
)
def test_artifact_reference_timestamps_require_timezones(created_at: datetime, expires_at: datetime | None) -> None:
    digest = "a" * 64

    with pytest.raises(ValidationError, match="artifact-reference timestamps must include a timezone"):
        ArtifactReference(
            artifact_id="plan",
            run_id="run-001",
            kind="plan",
            media_type="application/json",
            digest=digest,
            size=2,
            object_key=f"runs/run-001/artifacts/plan/{digest}/data",
            manifest_key=f"runs/run-001/artifacts/plan/{digest}/manifest.json",
            created_at=created_at,
            expires_at=expires_at,
        )


def test_prefect_execution_timestamp_requires_a_timezone() -> None:
    with pytest.raises(ValidationError, match="Prefect execution timestamps must include a timezone"):
        PrefectExecutionLink(
            flow_run_id="flow-001",
            purpose="plan",
            attempt=1,
            last_observed_at=datetime(2026, 8, 8),  # noqa: DTZ001 - deliberate validation case.
        )


def test_filesystem_path_guard_rejects_resolved_parent_escape(tmp_path: Path) -> None:
    store = FileArtifactStore(tmp_path / "objects")

    with pytest.raises(ValueError, match="escapes its configured root"):
        store._path("valid/../../../outside/data")


@pytest.mark.parametrize("manifest", [b"[]", b"42", b"null", b'"manifest"'])
def test_non_mapping_manifest_is_reported_as_invalid(manifest: bytes) -> None:
    data = b"{}"
    digest = sha256(data).hexdigest()
    reference = ArtifactReference(
        artifact_id="plan",
        run_id="run-001",
        kind="plan",
        media_type="application/json",
        digest=digest,
        size=len(data),
        object_key=f"runs/run-001/artifacts/plan/{digest}/data",
        manifest_key=f"runs/run-001/artifacts/plan/{digest}/manifest.json",
        created_at=datetime.now(timezone.utc),
    )

    assert product_store_store._validate_publication(reference, manifest, data).reason == "manifest-invalid"


def test_redaction_precedes_every_relational_and_artifact_write(provider: ProductProjection) -> None:
    secret = "canary-secret-649"  # noqa: S105 - deliberate persistence-boundary canary.
    record = _run().model_copy(
        update={
            "configuration_reference": f"config:{secret}",
            "actor": secret,
            "audit_links": (f"https://{secret}@audit.invalid",),
            "summary": {"nested": [secret]},
        }
    )
    provider.create_run(record, secrets=(secret,))
    provider.publish_artifact(
        "run-001",
        artifact_id="result",
        kind="result",
        media_type="application/json",
        data=f'{{"credential":"{secret}"}}'.encode(),
        secrets=(secret,),
    )
    provider.finish_run(
        "run-001",
        phase="applied",
        outcome="succeeded",
        summary={"message": secret},
        results={"url": f"https://{secret}@example.invalid"},
        secrets=(secret,),
    )

    loaded = provider.lookup_run("run-001").value
    artifact = provider.lookup_artifact("run-001", "result").value
    assert loaded is not None
    assert secret not in loaded.model_dump_json()
    assert artifact is not None
    assert secret.encode() not in artifact
    assert b"***" in artifact


def test_concurrent_result_merges_retain_every_stage_on_both_profiles(provider: ProductProjection) -> None:
    provider.create_run(_run())
    ready = Barrier(2)

    def merge(stage: str) -> None:
        ready.wait()
        provider.merge_results("run-001", {stage: {"outcome": stage}})

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(merge, ("verification", "apply_failure")))

    loaded = provider.lookup_run("run-001").value
    assert loaded is not None
    assert loaded.results == {
        "apply_failure": {"outcome": "apply_failure"},
        "verification": {"outcome": "verification"},
    }


@pytest.mark.parametrize("mutation", ["create", "finish"])
def test_redaction_key_collision_is_rejected_before_a_relational_write(
    mutation: str, provider: ProductProjection
) -> None:
    secret = "secret-key-649"  # noqa: S105 - deliberate redaction collision.
    collision = {secret: "one", "***": "two"}

    if mutation == "create":
        with pytest.raises(ValueError, match="collapse multiple mapping keys"):
            provider.create_run(_run().model_copy(update={"summary": collision}), secrets=(secret,))
        assert provider.lookup_run("run-001").reason == "run-not-found"
    else:
        provider.create_run(_run())
        with pytest.raises(ValueError, match="collapse multiple mapping keys"):
            provider.finish_run(
                "run-001",
                phase="planned",
                outcome="succeeded",
                summary=collision,
                results={},
                secrets=(secret,),
            )
        unchanged = provider.lookup_run("run-001").value
        assert unchanged is not None
        assert unchanged.outcome is None


def test_finish_run_uses_the_create_run_json_normalization_boundary(provider: ProductProjection) -> None:
    provider.create_run(_run().model_copy(update={"summary": {"payload": b"created"}}))

    provider.finish_run(
        "run-001",
        phase="planned",
        outcome="succeeded",
        summary={"payload": b"finished"},
        results={"payload": b"result"},
    )

    loaded = provider.lookup_run("run-001").value
    assert loaded is not None
    assert loaded.summary == {"payload": "finished"}
    assert loaded.results == {"payload": "result"}


def test_finish_run_redacts_secrets_carried_in_bytes_before_persistence(provider: ProductProjection) -> None:
    secret = "bytes-secret-649"  # noqa: S105 - deliberate persistence-boundary canary.
    provider.create_run(_run())

    provider.finish_run(
        "run-001",
        phase="planned",
        outcome="succeeded",
        summary={"payload": f"summary:{secret}".encode()},
        results={"payload": f"result:{secret}".encode()},
        secrets=(secret,),
    )

    loaded = provider.lookup_run("run-001").value
    assert loaded is not None
    assert secret not in loaded.model_dump_json()
    assert loaded.summary == {"payload": "summary:***"}
    assert loaded.results == {"payload": "result:***"}


def test_finish_run_rejects_non_utf8_bytes_before_updating_the_record(provider: ProductProjection) -> None:
    provider.create_run(_run())

    with pytest.raises(UnicodeDecodeError):
        provider.finish_run(
            "run-001",
            phase="planned",
            outcome="succeeded",
            summary={"payload": b"\xff"},
            results={},
        )

    unchanged = provider.lookup_run("run-001").value
    assert unchanged is not None
    assert unchanged.outcome is None


@pytest.mark.parametrize("profile", ["local", "production"])
def test_secret_canary_is_absent_from_raw_provider_contents(profile: str, tmp_path: Path) -> None:
    secret = "raw-provider-canary-649"  # noqa: S105 - deliberate persistence-boundary canary.
    fake_s3 = _FakeS3()
    if profile == "local":
        database = tmp_path / "local.sqlite3"
        projection = ProductProjection(SQLiteRunStore(database), FileArtifactStore(tmp_path / "objects"))
    else:
        database = tmp_path / "postgres-emulator.sqlite3"
        projection = ProductProjection(
            PostgreSQLRunStore(_connect(database)),
            S3ArtifactStore(fake_s3, bucket="artifacts"),
        )
    projection.create_run(
        _run().model_copy(update={"actor": secret, "results": {"credential": secret}}),
        secrets=(secret,),
    )
    projection.add_prefect_execution(
        "run-001",
        PrefectExecutionLink(flow_run_id="flow-001", purpose=f"plan:{secret}", attempt=1),
        secrets=(secret,),
    )
    projection.publish_artifact(
        "run-001",
        artifact_id="result",
        kind="result",
        media_type="text/plain",
        data=f"artifact contains {secret}".encode(),
        secrets=(secret,),
    )

    persisted = database.read_bytes()
    if profile == "local":
        persisted += b"".join(path.read_bytes() for path in (tmp_path / "objects").rglob("*") if path.is_file())
    else:
        persisted += b"".join(fake_s3.objects.values())
    assert secret.encode() not in persisted


@pytest.mark.parametrize("profile", ["local", "production"])
def test_pending_publication_persists_no_secret_canary(profile: str, tmp_path: Path) -> None:
    secret = "pending-provider-canary-649"  # noqa: S105 - deliberate persistence-boundary canary.
    fake_s3 = _FakeS3()
    if profile == "local":
        database = tmp_path / "local.sqlite3"
        projection = ProductProjection(_FailingMarkSQLiteStore(database), FileArtifactStore(tmp_path / "objects"))
    else:
        database = tmp_path / "postgres-emulator.sqlite3"
        projection = ProductProjection(
            _FailingMarkPostgreSQLStore(_connect(database)),
            S3ArtifactStore(fake_s3, bucket="artifacts"),
        )
    projection.create_run(_run())

    with pytest.raises(OSError, match="injected mark failure"):
        projection.publish_artifact(
            "run-001",
            artifact_id="result",
            kind=f"result:{secret}",
            media_type=f"text/{secret}",
            data=f"artifact contains {secret}".encode(),
            secrets=(secret,),
        )

    persisted = database.read_bytes()
    if profile == "local":
        persisted += b"".join(path.read_bytes() for path in (tmp_path / "objects").rglob("*") if path.is_file())
    else:
        persisted += b"".join(fake_s3.objects.values())
    assert secret.encode() not in persisted
    assert projection.lookup_artifact("run-001", "result").reason == "artifact-publication-incomplete"

    if profile == "local":
        resumed = ProductProjection(SQLiteRunStore(database), FileArtifactStore(tmp_path / "objects"))
    else:
        resumed = ProductProjection(
            PostgreSQLRunStore(_connect(database)),
            S3ArtifactStore(fake_s3, bucket="artifacts"),
        )
    resumed.publish_artifact(
        "run-001",
        artifact_id="result",
        kind=f"result:{secret}",
        media_type=f"text/{secret}",
        data=f"artifact contains {secret}".encode(),
        secrets=(secret,),
    )
    assert resumed.lookup_artifact("run-001", "result").value == b"artifact contains ***"

    persisted = database.read_bytes()
    if profile == "local":
        persisted += b"".join(path.read_bytes() for path in (tmp_path / "objects").rglob("*") if path.is_file())
    else:
        persisted += b"".join(fake_s3.objects.values())
    assert secret.encode() not in persisted


def test_local_profile_survives_restart_and_does_not_depend_on_cwd(tmp_path: Path) -> None:
    cache = tmp_path / "explicit-cache"
    first_cwd = tmp_path / "first"
    second_cwd = tmp_path / "second"
    first_cwd.mkdir()
    second_cwd.mkdir()
    create_script = """
from datetime import datetime, timezone
from pathlib import Path
from infrahub_sync.product_store import ProductRun, local_product_projection
p = local_product_projection(Path(sys.argv[1]))
p.create_run(ProductRun(run_id='run-001', operation='sync', configuration_reference='cfg',
    started_at=datetime.now(timezone.utc), phase='syncing'))
p.publish_artifact('run-001', artifact_id='result', kind='result', media_type='text/plain', data=b'durable')
"""
    read_script = """
from pathlib import Path
from infrahub_sync.product_store import local_product_projection
p = local_product_projection(Path(sys.argv[1]))
assert p.lookup_run('run-001').available
assert p.lookup_artifact('run-001', 'result').value == b'durable'
"""
    for cwd, script in ((first_cwd, create_script), (second_cwd, read_script)):
        result = subprocess.run(  # noqa: S603 - fixed interpreter and test-owned script.
            [sys.executable, "-c", "import sys\n" + script, str(cache)],
            cwd=cwd,
            check=False,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
    assert cache.is_dir()
    assert not (first_cwd / ".infrahub-sync-cache").exists()
    assert not (second_cwd / ".infrahub-sync-cache").exists()


@pytest.mark.parametrize("profile", ["local", "production"])
def test_provider_profile_survives_reconstruction(profile: str, tmp_path: Path) -> None:
    fake_s3 = _FakeS3()

    def build() -> ProductProjection:
        if profile == "local":
            return local_product_projection(tmp_path / "cache")
        return ProductProjection(
            PostgreSQLRunStore(_connect(tmp_path / "postgres-emulator.sqlite3")),
            S3ArtifactStore(fake_s3, bucket="artifacts"),
        )

    before_restart = build()
    before_restart.create_run(_run())
    before_restart.publish_artifact(
        "run-001", artifact_id="result", kind="result", media_type="text/plain", data=b"durable"
    )

    after_restart = build()

    assert after_restart.lookup_run("run-001").available
    assert after_restart.lookup_artifact("run-001", "result").value == b"durable"


def test_production_factories_share_exact_configuration_run_and_artifact_state(tmp_path: Path) -> None:
    """Independent API and worker factories observe one production-profile backend."""
    database = tmp_path / "shared-postgresql-emulator.sqlite3"
    fake_s3 = _FakeS3()

    def build() -> ProductProjection:
        return product_store.production_product_projection(
            connect=_connect(database),
            s3_client=fake_s3,
            bucket="shared-artifacts",
            prefix="shared-state",
        )

    api_projection = build()
    worker_projection = build()
    version = api_projection.create_configuration(_configuration_package())
    expected_run = ProductRun(
        run_id="run-shared-production-state",
        operation="plan",
        configuration_reference=f"{version.config_id}@{version.registry_version}",
        config_id=version.config_id,
        registry_version=version.registry_version,
        package_checksum=version.package_checksum,
        actor="api",
        started_at=datetime(2026, 8, 29, 12, tzinfo=timezone.utc),
        phase="accepted",
    )
    api_projection.create_run(expected_run)

    assert worker_projection.lookup_configuration_version(version.config_id, version.registry_version).value == version
    assert worker_projection.lookup_run(expected_run.run_id).value == expected_run

    reference = worker_projection.publish_artifact(
        expected_run.run_id,
        artifact_id="shared-artifact",
        kind="integration-proof",
        media_type="text/plain",
        data=b"shared durable state",
    )
    expected_published_run = expected_run.model_copy(update={"artifact_refs": (reference,)})

    assert api_projection.lookup_run(expected_run.run_id).value == expected_published_run
    assert api_projection.lookup_artifact(expected_run.run_id, "shared-artifact").value == b"shared durable state"


def test_local_profile_rejects_a_cwd_relative_cache_location() -> None:
    with pytest.raises(ValueError, match="cache_location must be absolute"):
        local_product_projection(Path("relative-cache"))


# --- Configuration registry -------------------------------------------------------------------


def test_configuration_version_requires_timezone() -> None:
    with pytest.raises(ValidationError, match="configuration-version timestamps must include a timezone"):
        ConfigurationVersion(
            config_id="20260808T1200-aaaaaaaa",
            registry_version=1,
            package_checksum="a" * 64,
            declared_content={},
            created_at=datetime(2026, 8, 8),  # noqa: DTZ001 - deliberate naive-value validation case.
        )


def test_configuration_summary_requires_timezone() -> None:
    with pytest.raises(ValidationError, match="configuration timestamps must include a timezone"):
        ConfigurationSummary(
            config_id="20260808T1200-aaaaaaaa",
            created_at=datetime(2026, 8, 8),  # noqa: DTZ001 - deliberate naive-value validation case.
        )


@pytest.mark.parametrize("registry_version", [0, -1])
def test_configuration_version_rejects_a_non_positive_registry_version(registry_version: int) -> None:
    with pytest.raises(ValidationError, match="greater than or equal to 1"):
        ConfigurationVersion(
            config_id="20260808T1200-aaaaaaaa",
            registry_version=registry_version,
            package_checksum="a" * 64,
            declared_content={},
            created_at=datetime.now(timezone.utc),
        )


def test_configuration_version_is_frozen() -> None:
    version = ConfigurationVersion(
        config_id="20260808T1200-aaaaaaaa",
        registry_version=1,
        package_checksum="a" * 64,
        declared_content={},
        created_at=datetime.now(timezone.utc),
    )

    with pytest.raises(ValidationError, match="frozen"):
        version.registry_version = 2  # type: ignore[misc]


_ALLOWED_STORE_CONFIGURATION_METHODS = frozenset(
    {
        "configuration_exists",
        "create_configuration",
        "add_configuration_version",
        "lookup_configuration",
        "lookup_configuration_version",
        "list_configurations",
        "list_configuration_versions",
    }
)
_ALLOWED_PROJECTION_CONFIGURATION_METHODS = frozenset(
    {
        "create_configuration",
        "add_configuration_version",
        "lookup_configuration",
        "lookup_configuration_version",
        "list_configurations",
        "list_configuration_versions",
    }
)


def test_no_public_configuration_method_deletes_or_updates_a_version() -> None:
    """Configuration versions are append-only. Pins the exact set of public
    configuration-facing methods on ``ProductProjection`` and both relational stores,
    structurally, rather than by name-guessing for words like "delete": a future
    ``delete_configuration_version()`` or ``update_configuration_version()`` would sail
    through ``test_public_surface_is_exactly_the_supported_contract`` undetected, since that
    test pins only the module's ``__all__``, not method names on these classes.
    """
    for cls, allowed in (
        (SQLiteRunStore, _ALLOWED_STORE_CONFIGURATION_METHODS),
        (PostgreSQLRunStore, _ALLOWED_STORE_CONFIGURATION_METHODS),
        (ProductProjection, _ALLOWED_PROJECTION_CONFIGURATION_METHODS),
    ):
        public = {
            name
            for name in dir(cls)
            if not name.startswith("_") and "configuration" in name.lower() and callable(getattr(cls, name))
        }
        assert public == allowed, f"{cls.__name__} exposes unexpected configuration methods: {public - allowed}"

    # Belt-and-suspenders directly on the SQL surface: no statement anywhere in the store
    # module issues a DELETE or UPDATE against the append-only configuration_versions table.
    source = Path(product_store_store.__file__).read_text(encoding="utf-8")
    assert not re.search(r"\b(DELETE\s+FROM|UPDATE)\s+configuration_versions\b", source, re.IGNORECASE)


def test_created_configuration_round_trips_its_first_version(provider: ProductProjection) -> None:
    package = _configuration_package()

    version = provider.create_configuration(package)

    assert version.registry_version == 1
    assert version.package_checksum == package.checksum()
    assert version.declared_content == package.declared_content()
    summary = provider.lookup_configuration(version.config_id)
    assert summary.available
    assert summary.value is not None
    assert summary.value.config_id == version.config_id
    assert provider.lookup_configuration_version(version.config_id, 1).value == version
    assert provider.list_configuration_versions(version.config_id) == (version,)


def test_insert_configuration_version_row_rejects_a_checksum_content_mismatch(tmp_path: Path) -> None:
    """The store trusts ``package.checksum()`` and separately persists
    ``package.declared_content()``; nothing else checks they correspond, and an append-only
    table gets no later chance to catch drift between the two."""
    store = SQLiteRunStore(tmp_path / "records.sqlite3")
    first = store.create_configuration(_configuration_package())
    mismatched = ConfigurationVersion(
        config_id=first.config_id,
        registry_version=2,
        package_checksum="a" * 64,
        declared_content={"does": "not-match-the-checksum-above"},
        created_at=datetime.now(timezone.utc),
    )

    connection = store._connect()
    try:
        cursor = connection.cursor()
        with pytest.raises(AssertionError, match="does not digest"):
            store._insert_configuration_version_row(cursor, mismatched)
    finally:
        connection.close()


@pytest.mark.parametrize("profile", ["local", "production"])
def test_configuration_registry_survives_store_reconstruction(profile: str, tmp_path: Path) -> None:
    fake_s3 = _FakeS3()

    def build() -> ProductProjection:
        if profile == "local":
            return local_product_projection(tmp_path / "cache")
        return ProductProjection(
            PostgreSQLRunStore(_connect(tmp_path / "postgres-emulator.sqlite3")),
            S3ArtifactStore(fake_s3, bucket="artifacts"),
        )

    before_restart = build()
    first = before_restart.create_configuration(_configuration_package())
    second, _ = before_restart.add_configuration_version(first.config_id, _configuration_package(verify_ssl=False))

    after_restart = build()

    assert after_restart.lookup_configuration(first.config_id).available
    assert after_restart.list_configuration_versions(first.config_id) == (first, second)
    assert after_restart.lookup_configuration_version(first.config_id, 2) == product_store.LookupResult(value=second)


def test_list_configurations_orders_by_created_at_then_config_id_on_both_profiles(
    provider: ProductProjection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The configurations listing carries one total ORDER BY: ``created_at``, then ``config_id``.

    ``created_at`` is server-generated, so on an append-only table insertion order equals
    timestamp order and a missing ORDER BY would pass trivially. The generated IDs and the
    clock are therefore pinned so two rows share one ``created_at`` (only the ``config_id``
    tiebreak separates them) and the last-inserted row carries the earliest timestamp (only
    ordering by ``created_at`` puts it first).
    """
    assert provider.list_configurations() == ()
    registrations = (
        ("config-b", datetime(2026, 8, 8, 12, 30, tzinfo=timezone.utc)),
        ("config-a", datetime(2026, 8, 8, 12, 30, tzinfo=timezone.utc)),
        ("config-z", datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc)),
    )
    ids = iter([config_id for config_id, _ in registrations])
    clock = iter([created_at for _, created_at in registrations])
    monkeypatch.setattr(product_store_store, "_generate_config_id", lambda: next(ids))
    monkeypatch.setattr(product_store_store, "datetime", SimpleNamespace(now=lambda tz: next(clock)))  # noqa: ARG005
    for _ in registrations:
        provider.create_configuration(_configuration_package())

    listed = provider.list_configurations()

    assert listed == (
        ConfigurationSummary(config_id="config-z", created_at=datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc)),
        ConfigurationSummary(config_id="config-a", created_at=datetime(2026, 8, 8, 12, 30, tzinfo=timezone.utc)),
        ConfigurationSummary(config_id="config-b", created_at=datetime(2026, 8, 8, 12, 30, tzinfo=timezone.utc)),
    )
    assert provider.list_configurations() == listed


def test_distinct_configurations_may_share_identical_package_content(provider: ProductProjection) -> None:
    package = _configuration_package()

    first = provider.create_configuration(package)
    second = provider.create_configuration(package)

    assert first.config_id != second.config_id
    assert first.package_checksum == second.package_checksum
    assert first.registry_version == second.registry_version == 1


def test_configuration_reads_are_scoped_to_their_own_configuration_with_two_in_one_store(
    provider: ProductProjection,
) -> None:
    """Every scoped read must stay scoped even when a second configuration exists in the same
    store and happens to share a registry_version number or a package checksum with the first --
    a predicate that also matched every row would be indistinguishable from a correctly scoped
    one until two configurations with overlapping numbers are actually present together.
    """
    package_a = _configuration_package()
    package_b = _configuration_package(url="https://demo.netbox.dev/tenant-b")
    config_a = provider.create_configuration(package_a)
    config_b = provider.create_configuration(package_b)
    config_a_v2, _ = provider.add_configuration_version(config_a.config_id, _configuration_package(verify_ssl=False))
    # config_b's second version shares its checksum with config_a's first version -- a checksum
    # that has never been registered under config_b before, so this is a plain new-checksum
    # insert, not yet the dedup path exercised below.
    config_b_v2, created = provider.add_configuration_version(config_b.config_id, package_a)
    assert created is True
    assert config_b_v2.config_id == config_b.config_id

    # list_configuration_versions: config_a's own two rows, none of config_b's.
    assert provider.list_configuration_versions(config_a.config_id) == (config_a, config_a_v2)
    assert provider.list_configuration_versions(config_b.config_id) == (config_b, config_b_v2)

    # lookup_configuration_version: both configurations have a registry_version 1; a lookup for
    # one must resolve to its own row, never the other's.
    looked_up_a = provider.lookup_configuration_version(config_a.config_id, 1)
    looked_up_b = provider.lookup_configuration_version(config_b.config_id, 1)
    assert looked_up_a.value == config_a
    assert looked_up_b.value == config_b

    # lookup_configuration: a nonexistent ID must be unavailable even though real configurations
    # exist in the same store.
    missing = provider.lookup_configuration("nonexistent-configuration")
    assert not missing.available
    assert missing.reason == "configuration-not-found"

    # The checksum-dedup path: config_b now already has a version with config_a's checksum
    # (config_b_v2, above). Registering that same content again must resolve to config_b's own
    # row, not config_a's, even though config_a also has a version with that checksum.
    replay, replay_created = provider.add_configuration_version(config_b.config_id, package_a)
    assert replay_created is False
    assert replay == config_b_v2


def test_adding_a_version_with_a_new_checksum_allocates_the_next_integer(provider: ProductProjection) -> None:
    first = provider.create_configuration(_configuration_package())

    version, created = provider.add_configuration_version(first.config_id, _configuration_package(verify_ssl=False))

    assert created is True
    assert version.registry_version == 2
    assert provider.list_configuration_versions(first.config_id) == (first, version)


def test_adding_a_version_with_a_known_checksum_returns_the_existing_version_and_creates_no_row(
    provider: ProductProjection,
) -> None:
    package = _configuration_package()
    first = provider.create_configuration(package)

    replay, created = provider.add_configuration_version(first.config_id, package)

    assert created is False
    assert replay == first
    assert provider.list_configuration_versions(first.config_id) == (first,)


def test_create_configuration_raises_a_typed_error_on_a_config_id_collision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A ``config_id`` uniqueness violation must not let the raw driver ``IntegrityError``
    escape a public API, like every sibling method (``DuplicateRunError``,
    ``DuplicateArtifactError``, ``DuplicatePrefectExecutionError``)."""
    store = SQLiteRunStore(tmp_path / "records.sqlite3")
    first = store.create_configuration(_configuration_package())
    monkeypatch.setattr(product_store_store, "_generate_config_id", lambda: first.config_id)

    with pytest.raises(DuplicateConfigurationError, match=re.escape(first.config_id)):
        store.create_configuration(_configuration_package(verify_ssl=False))


def test_add_configuration_version_on_unknown_configuration_is_refused(provider: ProductProjection) -> None:
    with pytest.raises(ConfigurationNotFoundError, match="unavailable"):
        provider.add_configuration_version("missing-configuration", _configuration_package())


def test_unknown_configuration_lookup_and_list_are_explicit(provider: ProductProjection) -> None:
    missing = provider.lookup_configuration("missing-configuration")
    assert not missing.available
    assert missing.reason == "configuration-not-found"
    assert provider.list_configuration_versions("missing-configuration") == ()
    assert provider.lookup_configuration_version("missing-configuration", 1).reason == "configuration-version-not-found"


def test_registration_rejects_an_inline_credential_value_before_any_persistence(tmp_path: Path) -> None:
    database = tmp_path / "local.sqlite3"
    projection = ProductProjection(SQLiteRunStore(database), FileArtifactStore(tmp_path / "objects"))
    canary = "inline-credential-canary-649"
    hostile = ConfigurationPackage.model_validate(_configuration_declaration(token=canary))

    with pytest.raises(CredentialConfigurationError):
        projection.create_configuration(hostile)

    assert canary.encode() not in database.read_bytes()
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM configurations").fetchone() == (0,)


def test_registration_rejects_an_inline_credential_value_on_an_existing_configuration(
    provider: ProductProjection,
) -> None:
    first = provider.create_configuration(_configuration_package())
    canary = "inline-credential-canary-existing-649"
    hostile = ConfigurationPackage.model_validate(_configuration_declaration(token=canary))

    with pytest.raises(CredentialConfigurationError):
        provider.add_configuration_version(first.config_id, hostile)

    assert provider.list_configuration_versions(first.config_id) == (first,)


def test_concurrent_new_checksums_allocate_distinct_sequential_versions_on_both_profiles(
    provider: ProductProjection,
) -> None:
    first = provider.create_configuration(_configuration_package())

    def add(position: int) -> ConfigurationVersion:
        version, _ = provider.add_configuration_version(
            first.config_id, _configuration_package(url=f"https://demo.netbox.dev/{position}")
        )
        return version

    # Two racers is enough to exercise sequential allocation cheaply; the higher fan-out
    # this budget is actually measured to support is covered separately below.
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(add, position) for position in range(2, 4)]
        results = [future.result(timeout=30) for future in futures]

    versions = sorted(version.registry_version for version in results)
    assert versions == [2, 3]
    assert len(provider.list_configuration_versions(first.config_id)) == 3


def test_concurrent_new_checksums_allocate_distinct_versions_at_eight_writers_on_both_profiles(
    provider: ProductProjection,
) -> None:
    """The measured, supported concurrency degree for ``add_configuration_version``: 8
    concurrent distinct-checksum callers (see the comment on
    ``_CONFIGURATION_VERSION_ATTEMPTS``). Runs reliably; not flaky across repeated runs.
    """
    first = provider.create_configuration(_configuration_package())

    def add(position: int) -> ConfigurationVersion:
        version, _ = provider.add_configuration_version(
            first.config_id, _configuration_package(url=f"https://demo.netbox.dev/{position}")
        )
        return version

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(add, position) for position in range(2, 10)]
        results = [future.result(timeout=30) for future in futures]

    versions = sorted(version.registry_version for version in results)
    assert versions == list(range(2, 10))
    assert len(provider.list_configuration_versions(first.config_id)) == 9


def test_configuration_version_allocation_exhaustion_raises_a_typed_error(tmp_path: Path) -> None:
    """Exhausting every allocation attempt raises a ``ValueError`` subclass, consistent with
    the store's ``DuplicateRunError`` / ``WriteAdmissionConflictError`` idiom, not a bare
    ``RuntimeError`` that a caller catching ``ValueError`` would not see."""
    database = tmp_path / "records.sqlite3"
    first = SQLiteRunStore(database).create_configuration(_configuration_package())
    store = _AlwaysConflictingConfigurationVersionStore(database)

    with pytest.raises(ConfigurationVersionAllocationError):
        store.add_configuration_version(first.config_id, _configuration_package(verify_ssl=False))


@pytest.mark.parametrize("profile", ["local", "production"])
def test_concurrent_identical_checksums_deduplicate_to_exactly_one_row_on_both_profiles(
    profile: str, tmp_path: Path
) -> None:
    """Every caller -- winner and losers alike -- must resolve in exactly one
    unique-violation round. A mutant that retries the insert loop before re-querying by
    checksum reaches the same outcome (one row, correct dedup) at three times the
    database work; only the round count distinguishes it, so that is what this asserts.
    """
    if profile == "local":
        records: _RoundCountingSQLiteStore | _RoundCountingPostgreSQLRunStore = _RoundCountingSQLiteStore(
            tmp_path / "records.sqlite3"
        )
        artifacts = FileArtifactStore(tmp_path / "objects")
    else:
        records = _RoundCountingPostgreSQLRunStore(_connect(tmp_path / "postgres-emulator.sqlite3"))
        artifacts = S3ArtifactStore(_FakeS3(), bucket="artifacts")
    provider = ProductProjection(records, artifacts)
    first = provider.create_configuration(_configuration_package())
    package = _configuration_package(verify_ssl=False)

    def add(_: int) -> tuple[ConfigurationVersion, bool, int]:
        records.version_insert_rounds.count = 0
        version, created = provider.add_configuration_version(first.config_id, package)
        return version, created, records.version_insert_rounds.count

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(add, position) for position in range(8)]
        results = [future.result(timeout=30) for future in futures]

    assert sum(created for _, created, _ in results) == 1
    versions = {version.registry_version for version, _, _ in results}
    assert versions == {2}
    assert len(provider.list_configuration_versions(first.config_id)) == 2
    rounds = [rounds for _, _, rounds in results]
    assert rounds == [1] * 8, f"expected every caller to resolve in exactly one round, got {rounds}"


# --- Run-to-configuration binding columns (deliberately inert so far) -------------------------

_LEGACY_PRODUCT_RUNS_TABLE = """
CREATE TABLE product_runs (
    run_id TEXT PRIMARY KEY, operation TEXT NOT NULL, configuration_reference TEXT NOT NULL,
    actor TEXT, audit_links TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT,
    phase TEXT NOT NULL, outcome TEXT, summary TEXT NOT NULL, results TEXT NOT NULL
);
"""
_CONFIGURATION_BINDING_COLUMN_NAMES = ("config_id", "registry_version", "package_checksum")


def test_fresh_sqlite_database_gains_the_nullable_binding_columns(tmp_path: Path) -> None:
    database = tmp_path / "records.sqlite3"
    SQLiteRunStore(database)

    with sqlite3.connect(database) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(product_runs)")}

    assert set(_CONFIGURATION_BINDING_COLUMN_NAMES) <= columns


def test_product_run_model_declares_configuration_binding_fields() -> None:
    assert set(_CONFIGURATION_BINDING_COLUMN_NAMES) <= set(ProductRun.model_fields)


def test_preexisting_database_is_migrated_forward_and_keeps_its_legacy_row(tmp_path: Path) -> None:
    database = tmp_path / "legacy.sqlite3"
    legacy_started_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with sqlite3.connect(database) as connection:
        connection.execute(_LEGACY_PRODUCT_RUNS_TABLE)
        connection.execute(
            "INSERT INTO product_runs (run_id, operation, configuration_reference, actor, audit_links, "
            "started_at, finished_at, phase, outcome, summary, results) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "legacy-run-001",
                "plan",
                "sha256:legacy",
                None,
                "[]",
                legacy_started_at.isoformat(),
                None,
                "planning",
                None,
                "{}",
                "{}",
            ),
        )

    store = SQLiteRunStore(database)

    with sqlite3.connect(database) as connection:
        columns = {row[1]: row for row in connection.execute("PRAGMA table_info(product_runs)")}
        row = connection.execute(
            "SELECT config_id, registry_version, package_checksum FROM product_runs WHERE run_id = ?",
            ("legacy-run-001",),
        ).fetchone()

    assert set(_CONFIGURATION_BINDING_COLUMN_NAMES) <= set(columns)
    assert row == (None, None, None)
    loaded = store.lookup("legacy-run-001")
    assert loaded.available
    assert loaded.value is not None
    assert loaded.value.operation == "plan"
    assert loaded.value.configuration_reference == "sha256:legacy"


def test_migration_is_idempotent_across_repeated_store_construction(tmp_path: Path) -> None:
    database = tmp_path / "records.sqlite3"
    SQLiteRunStore(database)
    SQLiteRunStore(database)
    SQLiteRunStore(database)

    with sqlite3.connect(database) as connection:
        columns = [row[1] for row in connection.execute("PRAGMA table_info(product_runs)")]

    for name in _CONFIGURATION_BINDING_COLUMN_NAMES:
        assert columns.count(name) == 1


def test_existing_run_lifecycle_is_unaffected_by_the_new_binding_columns(provider: ProductProjection) -> None:
    provider.create_run(_run())
    provider.add_prefect_execution("run-001", PrefectExecutionLink(flow_run_id="flow-001", purpose="plan", attempt=1))
    provider.publish_artifact("run-001", artifact_id="plan", kind="plan", media_type="application/json", data=b"{}")

    provider.finish_run("run-001", phase="planned", outcome="succeeded", summary={"create": 1}, results={})

    loaded = provider.lookup_run("run-001").value
    assert loaded is not None
    assert loaded.phase == "planned"
    assert loaded.outcome == "succeeded"
    assert set(_CONFIGURATION_BINDING_COLUMN_NAMES) <= set(ProductRun.model_fields)


class _FakePostgreSQLDatabase:
    """In-memory PostgreSQL catalog modelling the four semantics a literal SQLite file
    cannot: transactional DDL, transaction abort on error, ``ADD CONSTRAINT`` raising
    ``42710`` when the named constraint already exists, and per-column ``is_nullable`` that
    ``ALTER COLUMN ... DROP NOT NULL`` actually changes. This is what proves the schema
    bootstrap fix itself; the general CRUD tests above use the real-SQLite-backed
    ``_ConnectionAdapter`` instead, which does not model those four semantics.
    """

    def __init__(
        self,
        *,
        tables: frozenset[str] = frozenset(),
        columns: dict[str, frozenset[str]] | None = None,
        not_null: dict[str, frozenset[str]] | None = None,
    ) -> None:
        self.tables: set[str] = set(tables)
        self.columns: dict[str, set[str]] = {name: set(cols) for name, cols in (columns or {}).items()}
        self.not_null: dict[str, set[str]] = {name: set(cols) for name, cols in (not_null or {}).items()}
        self.constraints: set[str] = set()
        self.statements: list[str] = []

    def connect(self) -> _FakePostgreSQLConnection:
        return _FakePostgreSQLConnection(self)


class _FakePostgreSQLConnection:
    def __init__(self, database: _FakePostgreSQLDatabase) -> None:
        self.database = database
        self.pending_tables: set[str] = set()
        self.pending_columns: dict[str, set[str]] = {}
        self.pending_dropped_not_null: dict[str, set[str]] = {}
        self.pending_constraints: set[str] = set()
        self.aborted = False

    def cursor(self) -> _FakePostgreSQLCursor:
        return _FakePostgreSQLCursor(self)

    def commit(self) -> None:
        self.database.tables |= self.pending_tables
        for table, added in self.pending_columns.items():
            self.database.columns.setdefault(table, set()).update(added)
        for table, relaxed in self.pending_dropped_not_null.items():
            self.database.not_null.setdefault(table, set()).difference_update(relaxed)
        self.database.constraints |= self.pending_constraints
        self._discard_pending()

    def rollback(self) -> None:
        self._discard_pending()

    def _discard_pending(self) -> None:
        self.pending_tables = set()
        self.pending_columns = {}
        self.pending_dropped_not_null = {}
        self.pending_constraints = set()
        self.aborted = False

    def close(self) -> None:
        pass


_FAKE_CREATE_TABLE = re.compile(r"^CREATE TABLE IF NOT EXISTS (\w+)")
_FAKE_ALTER_ADD_COLUMN = re.compile(r"^ALTER TABLE (\w+) ADD COLUMN (\w+)")
_FAKE_ALTER_ADD_CONSTRAINT = re.compile(r"^ALTER TABLE \w+ ADD CONSTRAINT (\w+)")
_FAKE_ALTER_DROP_NOT_NULL = re.compile(r"^ALTER TABLE (\w+) ALTER COLUMN (\w+) DROP NOT NULL")


class _FakePostgreSQLCursor:
    """Cursor covering only the statements ``_initialize()`` issues against this fake."""

    def __init__(self, connection: _FakePostgreSQLConnection) -> None:
        self._connection = connection
        self._rows: tuple[tuple[Any, ...], ...] = ()

    def execute(self, operation: str, parameters: Sequence[Any] = ()) -> _FakePostgreSQLCursor:
        connection = self._connection
        if connection.aborted:
            raise _FakeDriverError(sqlstate="25P02")  # current transaction is aborted
        connection.database.statements.append(operation.strip())
        try:
            self._rows = self._dispatch(operation.strip(), parameters)
        except _FakeDriverError:
            connection.aborted = True
            raise
        return self

    def _dispatch(self, operation: str, parameters: Sequence[Any]) -> tuple[tuple[Any, ...], ...]:  # noqa: PLR0911
        connection = self._connection
        database = connection.database
        if match := _FAKE_CREATE_TABLE.match(operation):
            table = match.group(1)
            if table not in database.tables:
                connection.pending_tables.add(table)
            if table == "mutation_receipts":
                connection.pending_columns.setdefault(table, set()).update({"resource_kind", "resource_id"})
            return ()
        if operation.startswith(("CREATE UNIQUE INDEX IF NOT EXISTS", "CREATE INDEX IF NOT EXISTS")):
            return ()
        if "information_schema.columns" in operation:
            table = parameters[0]
            visible = database.columns.get(table, set()) | connection.pending_columns.get(table, set())
            not_null = database.not_null.get(table, set()) - connection.pending_dropped_not_null.get(table, set())
            return tuple((name, "NO" if name in not_null else "YES") for name in sorted(visible))
        if match := _FAKE_ALTER_ADD_COLUMN.match(operation):
            table, column = match.groups()
            if table not in (database.tables | connection.pending_tables):
                raise _FakeDriverError(sqlstate="42P01")  # relation "{table}" does not exist
            connection.pending_columns.setdefault(table, set()).add(column)
            return ()
        if operation.startswith("UPDATE mutation_receipts SET resource_kind"):
            return ()
        if match := _FAKE_ALTER_DROP_NOT_NULL.match(operation):
            table, column = match.groups()
            connection.pending_dropped_not_null.setdefault(table, set()).add(column)
            return ()
        if "information_schema.table_constraints" in operation:
            name = parameters[-1]
            visible = database.constraints | connection.pending_constraints
            return ((1,),) if name in visible else ()
        if match := _FAKE_ALTER_ADD_CONSTRAINT.match(operation):
            name = match.group(1)
            visible = database.constraints | connection.pending_constraints
            if name in visible:
                msg = f'constraint "{name}" for relation "product_runs" already exists'
                raise _FakeDriverError(sqlstate="42710")
            connection.pending_constraints.add(name)
            return ()
        msg = f"the fake PostgreSQL database does not model this statement: {operation!r}"
        raise AssertionError(msg)

    @property
    def rowcount(self) -> int:
        return len(self._rows)

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> tuple[tuple[Any, ...], ...]:
        return self._rows

    def close(self) -> None:
        pass


_PARENT_PRODUCT_RUNS_COLUMNS = frozenset(
    {
        "run_id",
        "operation",
        "configuration_reference",
        "actor",
        "audit_links",
        "started_at",
        "finished_at",
        "phase",
        "outcome",
        "summary",
        "results",
    }
)
_PARENT_TABLES = frozenset(
    {"product_runs", "artifact_refs", "prefect_executions", "mutation_receipts", "write_admissions", "audit_events"}
)
# The pre-resource-identity ``mutation_receipts`` shape, whose ``run_id`` and ``prefect_key``
# are still declared NOT NULL.
_LEGACY_MUTATION_RECEIPT_COLUMNS = frozenset(
    {
        "receipt_id",
        "actor",
        "key_digest",
        "operation",
        "target_run_id",
        "request_fingerprint",
        "reason",
        "run_id",
        "prefect_key",
        "state",
        "response_status",
        "response_body",
        "flow_run_id",
        "created_at",
        "updated_at",
    }
)


def _drop_not_null_statements(database: _FakePostgreSQLDatabase) -> list[str]:
    return [statement for statement in database.statements if "DROP NOT NULL" in statement]


def test_postgresql_initialize_succeeds_twice_on_a_fresh_catalog() -> None:
    database = _FakePostgreSQLDatabase()

    PostgreSQLRunStore(database.connect)
    assert {"configurations", "configuration_versions"} <= database.tables

    PostgreSQLRunStore(database.connect)
    assert {"configurations", "configuration_versions"} <= database.tables


def test_postgresql_repeated_initialization_skips_completed_mutation_receipt_nullability_ddl() -> None:
    connections: list[MagicMock] = []

    def connect() -> MagicMock:
        connection = MagicMock()
        connection.cursor.return_value.fetchall.side_effect = [
            (),
            (),
            (
                ("prefect_key", "YES"),
                ("resource_id", "NO"),
                ("resource_kind", "NO"),
                ("run_id", "YES"),
            ),
        ]
        connection.cursor.return_value.fetchone.return_value = (1,)
        connections.append(connection)
        return connection

    PostgreSQLRunStore(connect)
    PostgreSQLRunStore(connect)

    recurring_ddl = [
        call.args[0].strip()
        for connection in connections
        for call in connection.cursor.return_value.execute.call_args_list
        if call.args[0].strip().startswith("ALTER TABLE mutation_receipts ALTER COLUMN")
        and call.args[0].strip().endswith("DROP NOT NULL")
    ]
    assert recurring_ddl == []


def test_postgresql_legacy_mutation_receipt_nullability_migrates_once() -> None:
    """A legacy ``NO`` catalog is relaxed on the first construction and left alone afterwards.

    The two halves belong together: skipping the DDL only because a column is already nullable
    is correct, skipping it while a real ``NOT NULL`` column survives is the legacy-migration
    regression the idempotency work must not cause.
    """
    database = _FakePostgreSQLDatabase(
        tables=_PARENT_TABLES,
        columns={
            "product_runs": _PARENT_PRODUCT_RUNS_COLUMNS,
            "mutation_receipts": _LEGACY_MUTATION_RECEIPT_COLUMNS,
        },
        not_null={"mutation_receipts": frozenset({"run_id", "prefect_key"})},
    )

    PostgreSQLRunStore(database.connect)

    assert _drop_not_null_statements(database) == [
        "ALTER TABLE mutation_receipts ALTER COLUMN run_id DROP NOT NULL",
        "ALTER TABLE mutation_receipts ALTER COLUMN prefect_key DROP NOT NULL",
    ]
    assert database.not_null["mutation_receipts"] == set()

    database.statements.clear()
    PostgreSQLRunStore(database.connect)

    assert _drop_not_null_statements(database) == []


@pytest.mark.parametrize(
    "catalog_rows",
    [
        pytest.param((("run_id",), ("prefect_key", "YES")), id="row-narrower-than-two-cells"),
        pytest.param((("run_id", "UNKNOWN"), ("prefect_key", "YES")), id="value-outside-the-yes-no-domain"),
        pytest.param((("run_id", None), ("prefect_key", "YES")), id="null-value"),
        pytest.param((("run_id", 0), ("prefect_key", "YES")), id="non-string-value"),
    ],
)
def test_postgresql_initialization_refuses_a_malformed_column_catalog_row(
    catalog_rows: tuple[tuple[Any, ...], ...],
) -> None:
    """A catalog answer outside the closed nullability domain fails closed.

    The store selects ``information_schema.columns`` as exactly (column_name, is_nullable),
    and PostgreSQL declares ``is_nullable`` over the ``yes_or_no`` domain, so the only two
    readable answers are ``"YES"`` and ``"NO"``. Anything else -- a narrower row, a value
    outside that domain, or a non-string -- means the provider did not answer the statement
    that was issued. Accepting it as "not NO" would silently skip the one-time legacy
    migration and leave a real ``NOT NULL`` column in place, which no later construction
    would ever repair.
    """
    connection = MagicMock()
    connection.cursor.return_value.fetchall.side_effect = [(), (), catalog_rows]
    connection.cursor.return_value.fetchone.return_value = (1,)

    with pytest.raises(product_store.ProductStoreProviderError):
        PostgreSQLRunStore(lambda: connection)

    connection.rollback.assert_called_once_with()


def test_postgresql_initialize_succeeds_twice_on_a_prepopulated_parent_catalog() -> None:
    """The realistic upgrade path: every parent table already exists, the two registry tables
    do not yet. Regression coverage for the silent partial-initialization defect, where the old
    probe-and-rollback introspection wiped the uncommitted ``CREATE TABLE`` for both new tables
    before they ever committed, while ``_initialize()`` still reported success.
    """
    database = _FakePostgreSQLDatabase(
        tables=_PARENT_TABLES,
        columns={"product_runs": _PARENT_PRODUCT_RUNS_COLUMNS},
    )

    PostgreSQLRunStore(database.connect)
    assert {"configurations", "configuration_versions"} <= database.tables

    PostgreSQLRunStore(database.connect)
    assert {"configurations", "configuration_versions"} <= database.tables


def test_postgresql_dialect_reads_columns_via_information_schema_without_probing_pragma() -> None:
    database = _FakePostgreSQLDatabase(
        tables=frozenset({"product_runs"}),
        columns={"product_runs": frozenset({"run_id", "operation"})},
    )
    store = object.__new__(PostgreSQLRunStore)
    store._placeholder = "%s"
    store._dialect = "postgresql"
    cursor = database.connect().cursor()

    columns = store._read_product_run_columns(cursor)

    assert columns == frozenset({"run_id", "operation"})


def test_postgresql_dialect_binding_constraint_checks_existence_before_creating() -> None:
    database = _FakePostgreSQLDatabase(tables=frozenset({"product_runs"}))
    store = object.__new__(PostgreSQLRunStore)
    store._placeholder = "%s"
    store._dialect = "postgresql"

    first_connection = database.connect()
    store._ensure_configuration_binding_constraint(first_connection.cursor())
    first_connection.commit()

    assert product_store_store._CONFIGURATION_BINDING_CONSTRAINT in database.constraints

    # A second pass over the same catalog must check existence first, not attempt-and-catch:
    # a blind re-attempt would raise 42710 here.
    second_connection = database.connect()
    store._ensure_configuration_binding_constraint(second_connection.cursor())
    second_connection.commit()


def test_postgresql_schema_bootstrap_propagates_a_non_duplicate_alter_failure() -> None:
    """``_is_duplicate_column_error`` must not swallow a genuine ALTER failure."""
    connection = MagicMock()
    connection.cursor.return_value.fetchall.return_value = []
    connection.cursor.return_value.fetchone.return_value = None

    def side_effect(operation: str, parameters: tuple[Any, ...] = ()) -> MagicMock:  # noqa: ARG001
        # pylint: disable=unused-argument
        if operation.strip().startswith("ALTER TABLE product_runs ADD COLUMN"):
            raise _FakeDriverError(sqlstate="42501")
        return connection.cursor.return_value

    connection.cursor.return_value.execute.side_effect = side_effect

    with pytest.raises(_FakeDriverError):
        PostgreSQLRunStore(lambda: connection)

    connection.rollback.assert_called_once_with()
    connection.close.assert_called_once_with()


def _reachable_postgresql_dsn() -> str | None:
    """Return a reachable PostgreSQL DSN from ``PRODUCT_STORE_TEST_POSTGRESQL_DSN``, or None.

    The managed extra supplies ``psycopg``; an absent driver or unreachable endpoint still
    skips this opt-in test before it can contact a service.
    """
    dsn = os.environ.get("PRODUCT_STORE_TEST_POSTGRESQL_DSN")
    if not dsn:
        return None
    try:
        # pylint: disable-next=import-outside-toplevel,import-error
        import psycopg  # ty: ignore[unresolved-import] - TODO: optional managed dependency
    except ImportError:
        return None
    try:
        with psycopg.connect(dsn, connect_timeout=2) as connection:
            connection.execute("SELECT 1")
    except Exception:  # noqa: BLE001 - pylint: disable=broad-exception-caught
        return None  # any connectivity or driver failure means "skip".
    return dsn


@pytest.mark.integration
def test_postgresql_run_store_initializes_against_a_real_server() -> None:
    """Real-server confirmation of the schema-bootstrap fix, and of the run-to-configuration
    binding columns and CHECK constraint that were otherwise only verified by hand.

    WARNING: this test runs ``DROP SCHEMA public CASCADE`` against whatever database the DSN
    points at, which destroys every table in that database's ``public`` schema. Point the DSN
    only at a disposable, single-purpose database (e.g. the throwaway container below) — never
    at a shared or persistent development database.

    Opt in with ``-m integration`` and a reachable ``PRODUCT_STORE_TEST_POSTGRESQL_DSN``, e.g.::

        docker run -d --rm --name cf002-pg-fix -e POSTGRES_PASSWORD=probe -e POSTGRES_DB=probe \\
            -p 55432:5432 postgres:18-alpine
        PRODUCT_STORE_TEST_POSTGRESQL_DSN="host=127.0.0.1 port=55432 user=postgres password=probe dbname=probe" \\
            uv run --with 'psycopg[binary]' python -m pytest -m integration \\
            tests/product_store/test_contract.py::test_postgresql_run_store_initializes_against_a_real_server

    Covers the same three constructions the in-memory fake proves above, now against a real
    server: a fresh catalog (twice consecutively), and the pre-populated parent-tables upgrade
    path (twice consecutively). Also asserts, against the real server, that construction leaves
    ``product_runs`` with its three binding columns and the binding CHECK constraint in place.
    """
    dsn = _reachable_postgresql_dsn()
    if dsn is None:
        pytest.skip("psycopg is not installed, or PRODUCT_STORE_TEST_POSTGRESQL_DSN is unset/unreachable")
    # pylint: disable-next=import-outside-toplevel,import-error
    import psycopg  # ty: ignore[unresolved-import] - TODO: optional managed dependency

    from infrahub_sync.service.storage import PsycopgConnectionFactory

    def connect() -> DBAPIConnection:
        return PsycopgConnectionFactory(psycopg.connect)(dsn)

    def reset_schema() -> None:
        with psycopg.connect(dsn) as admin:
            admin.execute("DROP SCHEMA public CASCADE")
            admin.execute("CREATE SCHEMA public")
            admin.commit()

    def committed_tables() -> set[str]:
        with psycopg.connect(dsn) as admin:
            rows = admin.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
            ).fetchall()
        return {row[0] for row in rows}

    def product_runs_columns() -> set[str]:
        with psycopg.connect(dsn) as admin:
            rows = admin.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'product_runs' AND table_schema = current_schema()"
            ).fetchall()
        return {row[0] for row in rows}

    def binding_constraint_exists() -> bool:
        with psycopg.connect(dsn) as admin:
            row = admin.execute(
                "SELECT 1 FROM information_schema.table_constraints "
                "WHERE table_name = 'product_runs' AND constraint_name = %s AND table_schema = current_schema()",
                (product_store_store._CONFIGURATION_BINDING_CONSTRAINT,),
            ).fetchone()
        return row is not None

    registry_tables = {"configurations", "configuration_versions"}
    binding_columns = {"config_id", "registry_version", "package_checksum"}

    # A fresh catalog, twice consecutively.
    reset_schema()
    PostgreSQLRunStore(connect)
    assert registry_tables <= committed_tables()
    assert binding_columns <= product_runs_columns()
    assert binding_constraint_exists()
    PostgreSQLRunStore(connect)
    assert registry_tables <= committed_tables()
    assert binding_columns <= product_runs_columns()
    assert binding_constraint_exists()

    # The realistic upgrade path: every parent table pre-exists, the two registry tables do not.
    reset_schema()
    parent_statements = [
        statement.strip()
        for statement in product_store_store._SCHEMA.split(";")
        if statement.strip() and not any(table in statement for table in registry_tables)
    ]
    with psycopg.connect(dsn) as admin:
        for statement in parent_statements:
            admin.execute(statement)
        admin.commit()
    PostgreSQLRunStore(connect)
    assert registry_tables <= committed_tables()
    assert binding_columns <= product_runs_columns()
    assert binding_constraint_exists()
    PostgreSQLRunStore(connect)
    assert registry_tables <= committed_tables()
    assert binding_columns <= product_runs_columns()
    assert binding_constraint_exists()

    # The CHECK constraint must actually enforce the binding invariant against a real server,
    # not merely exist by name.
    _assert_real_postgresql_refuses_partial_configuration_binding(dsn)


def _assert_real_postgresql_refuses_partial_configuration_binding(dsn: str) -> None:
    """Attempt every partial-binding combination on INSERT, then a partial UPDATE, against a
    real PostgreSQL server, and confirm the two legal (fully-unbound and fully-bound) states are
    still accepted. Factored out of the test above only to keep that test's statement count
    reasonable; it has exactly one caller.
    """
    # pylint: disable-next=import-outside-toplevel,import-error
    import psycopg  # ty: ignore[unresolved-import] - TODO: optional managed dependency

    def raw_insert(
        run_id: str, *, config_id: str | None, registry_version: int | None, package_checksum: str | None
    ) -> None:
        with psycopg.connect(dsn) as admin:
            admin.execute(
                "INSERT INTO product_runs (run_id, operation, configuration_reference, actor, audit_links, "
                "started_at, finished_at, phase, outcome, summary, results, config_id, registry_version, "
                "package_checksum) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    run_id,
                    "plan",
                    "sha256:raw",
                    None,
                    "[]",
                    datetime.now(timezone.utc).isoformat(),
                    None,
                    "planning",
                    None,
                    "{}",
                    "{}",
                    config_id,
                    registry_version,
                    package_checksum,
                ),
            )

    for index, (config_id, registry_version, package_checksum) in enumerate(_PARTIAL_CONFIGURATION_BINDING_TUPLES):
        with pytest.raises(psycopg.Error):
            raw_insert(
                f"raw-partial-{index}",
                config_id=config_id,
                registry_version=registry_version,
                package_checksum=package_checksum,
            )

    raw_insert("raw-unbound", config_id=None, registry_version=None, package_checksum=None)
    raw_insert("raw-bound", config_id="20260101T0000-aaaaaaaa", registry_version=1, package_checksum="a" * 64)
    with psycopg.connect(dsn) as admin:
        accepted = {
            row[0]
            for row in admin.execute(
                "SELECT run_id FROM product_runs WHERE run_id IN (%s, %s)", ("raw-unbound", "raw-bound")
            ).fetchall()
        }
    assert accepted == {"raw-unbound", "raw-bound"}

    with pytest.raises(psycopg.Error), psycopg.connect(dsn) as admin:
        admin.execute(
            "UPDATE product_runs SET config_id = %s WHERE run_id = %s",
            ("20260101T0000-bbbbbbbb", "raw-unbound"),
        )

    with psycopg.connect(dsn) as admin:
        unchanged = admin.execute(
            "SELECT config_id, registry_version, package_checksum FROM product_runs WHERE run_id = %s",
            ("raw-unbound",),
        ).fetchone()
    assert unchanged == (None, None, None)


_PARTIAL_CONFIGURATION_BINDING_TUPLES: list[tuple[str | None, int | None, str | None]] = [
    ("20260101T0000-aaaaaaaa", None, None),
    (None, 1, None),
    (None, None, "a" * 64),
    ("20260101T0000-aaaaaaaa", 1, None),
    ("20260101T0000-aaaaaaaa", None, "a" * 64),
    (None, 1, "a" * 64),
]


def _raw_insert_product_run(
    connection: sqlite3.Connection,
    run_id: str,
    *,
    config_id: str | None,
    registry_version: int | None,
    package_checksum: str | None,
) -> None:
    connection.execute(
        "INSERT INTO product_runs (run_id, operation, configuration_reference, actor, audit_links, started_at, "
        "finished_at, phase, outcome, summary, results, config_id, registry_version, package_checksum) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            run_id,
            "plan",
            "sha256:raw",
            None,
            "[]",
            datetime.now(timezone.utc).isoformat(),
            None,
            "planning",
            None,
            "{}",
            "{}",
            config_id,
            registry_version,
            package_checksum,
        ),
    )
    connection.commit()


_PARTIAL_CONFIGURATION_BINDING_COMBINATIONS = [
    pytest.param("20260101T0000-aaaaaaaa", None, None, id="only-config-id"),
    pytest.param(None, 1, None, id="only-registry-version"),
    pytest.param(None, None, "a" * 64, id="only-package-checksum"),
    pytest.param("20260101T0000-aaaaaaaa", 1, None, id="missing-package-checksum"),
    pytest.param("20260101T0000-aaaaaaaa", None, "a" * 64, id="missing-registry-version"),
    pytest.param(None, 1, "a" * 64, id="missing-config-id"),
]


@pytest.mark.parametrize(
    ("config_id", "registry_version", "package_checksum"), _PARTIAL_CONFIGURATION_BINDING_COMBINATIONS
)
def test_every_partial_configuration_binding_combination_is_refused_at_insert(
    config_id: str | None, registry_version: int | None, package_checksum: str | None, tmp_path: Path
) -> None:
    database = tmp_path / "records.sqlite3"
    SQLiteRunStore(database)

    with sqlite3.connect(database) as connection, pytest.raises(sqlite3.IntegrityError, match="configuration-binding"):
        _raw_insert_product_run(
            connection,
            "raw-run-001",
            config_id=config_id,
            registry_version=registry_version,
            package_checksum=package_checksum,
        )


def test_fully_unbound_and_fully_bound_rows_are_both_accepted(tmp_path: Path) -> None:
    database = tmp_path / "records.sqlite3"
    SQLiteRunStore(database)

    with sqlite3.connect(database) as connection:
        _raw_insert_product_run(connection, "unbound-run", config_id=None, registry_version=None, package_checksum=None)
        _raw_insert_product_run(
            connection,
            "bound-run",
            config_id="20260101T0000-aaaaaaaa",
            registry_version=1,
            package_checksum="a" * 64,
        )
        rows = connection.execute("SELECT run_id FROM product_runs ORDER BY run_id").fetchall()

    assert [row[0] for row in rows] == ["bound-run", "unbound-run"]


def test_partial_configuration_binding_is_refused_on_update_too(tmp_path: Path) -> None:
    database = tmp_path / "records.sqlite3"
    store = SQLiteRunStore(database)
    store.create(_run("update-target"))

    with sqlite3.connect(database) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="configuration-binding"):
            connection.execute(
                "UPDATE product_runs SET config_id = ? WHERE run_id = ?",
                ("20260101T0000-aaaaaaaa", "update-target"),
            )
        connection.rollback()
        unchanged = connection.execute(
            "SELECT config_id, registry_version, package_checksum FROM product_runs WHERE run_id = ?",
            ("update-target",),
        ).fetchone()

    assert unchanged == (None, None, None)


def test_reconstructing_the_store_restores_a_dropped_update_trigger(tmp_path: Path) -> None:
    """Regression test: SQLite enforcement is a pair of triggers, and the existence check that
    guards recreating them must require both by name, not just the BEFORE INSERT one.

    Drop only the BEFORE UPDATE trigger, reconstruct the store, and confirm a partial UPDATE is
    still refused. Before the fix, the existence check saw the surviving INSERT trigger, reported
    the constraint as already present, and left the UPDATE trigger missing — silently accepting a
    partial UPDATE it should have refused.
    """
    database = tmp_path / "records.sqlite3"
    store = SQLiteRunStore(database)
    store.create(_run("update-target"))

    with sqlite3.connect(database) as connection:
        connection.execute("DROP TRIGGER product_runs_configuration_binding_update")
        connection.commit()

    SQLiteRunStore(database)

    with sqlite3.connect(database) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="configuration-binding"):
            connection.execute(
                "UPDATE product_runs SET config_id = ? WHERE run_id = ?",
                ("20260101T0000-aaaaaaaa", "update-target"),
            )
        connection.rollback()
        unchanged = connection.execute(
            "SELECT config_id, registry_version, package_checksum FROM product_runs WHERE run_id = ?",
            ("update-target",),
        ).fetchone()

    assert unchanged == (None, None, None)


def test_postgresql_dialect_binding_constraint_uses_a_check_constraint_not_a_trigger() -> None:
    database = _FakePostgreSQLDatabase()

    PostgreSQLRunStore(database.connect)

    assert product_store_store._CONFIGURATION_BINDING_CONSTRAINT in database.constraints


_CONFIGURATION_BINDING_COMBINATIONS_AND_LEGALITY = [
    pytest.param(None, None, None, True, id="fully-unbound"),
    pytest.param("20260101T0000-aaaaaaaa", 1, "a" * 64, True, id="fully-bound"),
    pytest.param("20260101T0000-aaaaaaaa", None, None, False, id="only-config-id"),
    pytest.param(None, 1, None, False, id="only-registry-version"),
    pytest.param(None, None, "a" * 64, False, id="only-package-checksum"),
    pytest.param("20260101T0000-aaaaaaaa", 1, None, False, id="missing-package-checksum"),
    pytest.param("20260101T0000-aaaaaaaa", None, "a" * 64, False, id="missing-registry-version"),
    pytest.param(None, 1, "a" * 64, False, id="missing-config-id"),
]


@pytest.mark.parametrize(
    ("config_id", "registry_version", "package_checksum", "legal"),
    _CONFIGURATION_BINDING_COMBINATIONS_AND_LEGALITY,
)
def test_configuration_binding_check_expression_permits_exactly_the_two_legal_states(
    config_id: str | None,
    registry_version: int | None,
    package_checksum: str | None,
    legal: bool,  # noqa: FBT001 - a parametrized case discriminator, not a caller-facing switch
) -> None:
    """Directly evaluate ``_CONFIGURATION_BINDING_CHECK_EXPRESSION`` -- the entire PostgreSQL
    enforcement of the run-to-configuration binding invariant -- against all eight NULL/NOT-NULL
    combinations of the three binding columns.

    This does not go through a real PostgreSQL server, nor through SQLite's independent trigger
    implementation of the same invariant (the triggers embed their own literal copy of this
    logic, so they cannot catch a defect in this expression string). It evaluates the exact
    string PostgreSQL's ``CHECK`` constraint is built from, in SQLite, by binding the three
    candidate values as columns of a one-row subquery.
    """
    connection = sqlite3.connect(":memory:")
    try:
        row = connection.execute(
            f"SELECT ({product_store_store._CONFIGURATION_BINDING_CHECK_EXPRESSION}) "  # noqa: S608
            "FROM (SELECT ? AS config_id, ? AS registry_version, ? AS package_checksum)",
            (config_id, registry_version, package_checksum),
        ).fetchone()
    finally:
        connection.close()

    assert bool(row[0]) is legal
