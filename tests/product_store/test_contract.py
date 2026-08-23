from __future__ import annotations

import sqlite3
import subprocess  # noqa: S404 - fixed local interpreter probes restart durability.
import sys
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace
from typing import Any
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
    DuplicateArtifactError,
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

EXPECTED_PUBLIC_NAMES = {
    "ArtifactReference",
    "ArtifactUnavailableError",
    "AuditEvent",
    "ConfigurationNotFoundError",
    "ConfigurationSummary",
    "ConfigurationVersion",
    "DBAPIConnection",
    "DuplicateArtifactError",
    "DuplicatePrefectExecutionError",
    "DuplicateRunError",
    "LookupResult",
    "MutationReceipt",
    "PrefectExecutionLink",
    "ProductProjection",
    "ProductRun",
    "RunNotFoundError",
    "S3Client",
    "WriteAdmissionConflictError",
    "local_product_projection",
    "production_product_projection",
}


class _CursorAdapter:
    def __init__(self, cursor: sqlite3.Cursor) -> None:
        self._cursor = cursor

    def execute(self, operation: str, parameters=()):
        self._cursor.execute(operation.replace("%s", "?"), parameters)
        return self

    @property
    def rowcount(self) -> int:
        return self._cursor.rowcount

    def fetchone(self):
        return self._cursor.fetchone()

    def fetchall(self):
        return self._cursor.fetchall()

    def close(self) -> None:
        self._cursor.close()


class _ConnectionAdapter:
    def __init__(self, path: Path) -> None:
        self._connection = sqlite3.connect(path)
        self._connection.execute("PRAGMA foreign_keys = ON")

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
        run_id=run_id,
        prefect_key=sha256(receipt_id.encode()).hexdigest(),
        created_at=now,
        updated_at=now,
    )


def _configuration_declaration(**settings_overrides: object) -> dict[str, Any]:
    settings = {
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
    assert projection.lookup_run(winning_run_ids.pop()).available


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


def test_managed_prefect_attempt_ordinals_are_allocated_atomically(provider: ProductProjection) -> None:
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


def test_local_profile_rejects_a_cwd_relative_cache_location() -> None:
    with pytest.raises(ValueError, match="cache_location must be absolute"):
        local_product_projection(Path("relative-cache"))


# --- Configuration registry -------------------------------------------------------------------


def test_configuration_version_requires_timezone() -> None:
    with pytest.raises(ValidationError, match="configuration-version timestamps must include a timezone"):
        ConfigurationVersion(
            config_id="20260808T1200-aaaaaaaa",
            config_version=1,
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


def test_configuration_version_is_frozen() -> None:
    version = ConfigurationVersion(
        config_id="20260808T1200-aaaaaaaa",
        config_version=1,
        package_checksum="a" * 64,
        declared_content={},
        created_at=datetime.now(timezone.utc),
    )

    with pytest.raises(ValidationError, match="frozen"):
        version.config_version = 2  # type: ignore[misc]


def test_created_configuration_round_trips_its_first_version(provider: ProductProjection) -> None:
    package = _configuration_package()

    version = provider.create_configuration(package)

    assert version.config_version == 1
    assert version.package_checksum == package.checksum()
    assert version.declared_content == package.declared_content()
    summary = provider.lookup_configuration(version.config_id)
    assert summary.available
    assert summary.value is not None
    assert summary.value.config_id == version.config_id
    assert provider.lookup_configuration_version(version.config_id, 1).value == version
    assert provider.list_configuration_versions(version.config_id) == (version,)


def test_distinct_configurations_may_share_identical_package_content(provider: ProductProjection) -> None:
    package = _configuration_package()

    first = provider.create_configuration(package)
    second = provider.create_configuration(package)

    assert first.config_id != second.config_id
    assert first.package_checksum == second.package_checksum
    assert first.config_version == second.config_version == 1


def test_adding_a_version_with_a_new_checksum_allocates_the_next_integer(provider: ProductProjection) -> None:
    first = provider.create_configuration(_configuration_package())

    version, created = provider.add_configuration_version(first.config_id, _configuration_package(verify_ssl=False))

    assert created is True
    assert version.config_version == 2
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

    # Two racers, matching the bounded-retry idiom's own concurrency test
    # (test_managed_prefect_attempt_ordinals_are_allocated_atomically): the shared
    # allocation attempt budget is 3, so higher fan-out risks legitimate exhaustion
    # under SQLite's single-writer locking rather than exercising the atomicity guarantee.
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(add, position) for position in range(2, 4)]
        results = [future.result(timeout=30) for future in futures]

    versions = sorted(version.config_version for version in results)
    assert versions == [2, 3]
    assert len(provider.list_configuration_versions(first.config_id)) == 3


def test_concurrent_identical_checksums_deduplicate_to_exactly_one_row_on_both_profiles(
    provider: ProductProjection,
) -> None:
    first = provider.create_configuration(_configuration_package())
    package = _configuration_package(verify_ssl=False)

    def add(_: int) -> tuple[ConfigurationVersion, bool]:
        return provider.add_configuration_version(first.config_id, package)

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(add, position) for position in range(8)]
        results = [future.result(timeout=30) for future in futures]

    assert sum(created for _, created in results) == 1
    versions = {version.config_version for version, _ in results}
    assert versions == {2}
    assert len(provider.list_configuration_versions(first.config_id)) == 2


# --- Run-to-configuration binding columns (inert this slice) ---------------------------------

_LEGACY_PRODUCT_RUNS_TABLE = """
CREATE TABLE product_runs (
    run_id TEXT PRIMARY KEY, operation TEXT NOT NULL, configuration_reference TEXT NOT NULL,
    actor TEXT, audit_links TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT,
    phase TEXT NOT NULL, outcome TEXT, summary TEXT NOT NULL, results TEXT NOT NULL
);
"""
_CONFIGURATION_BINDING_COLUMN_NAMES = ("config_id", "config_version", "package_checksum")


def test_fresh_sqlite_database_gains_the_nullable_binding_columns(tmp_path: Path) -> None:
    database = tmp_path / "records.sqlite3"
    SQLiteRunStore(database)

    with sqlite3.connect(database) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(product_runs)")}

    assert set(_CONFIGURATION_BINDING_COLUMN_NAMES) <= columns


def test_product_run_model_declares_no_configuration_binding_fields() -> None:
    assert not set(_CONFIGURATION_BINDING_COLUMN_NAMES) & set(ProductRun.model_fields)


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
            "SELECT config_id, config_version, package_checksum FROM product_runs WHERE run_id = ?",
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
    assert "config_id" not in ProductRun.model_fields


class _PostgresLikeCursor:
    """Fake cursor that rejects SQLite's introspection pragma like real PostgreSQL does."""

    def __init__(self, connection: _PostgresLikeConnection) -> None:
        self._connection = connection
        self._rows: tuple[tuple[Any, ...], ...] = ()

    def execute(self, operation: str, parameters: tuple[Any, ...] = ()) -> _PostgresLikeCursor:  # noqa: ARG002
        if "PRAGMA" in operation:
            raise _FakeDriverError(sqlstate="42601")
        if "information_schema.columns" in operation:
            self._connection.information_schema_queries += 1
            self._rows = tuple((name,) for name in self._connection.columns)
        else:
            self._rows = ()
        return self

    @property
    def rowcount(self) -> int:
        return len(self._rows)

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> tuple[tuple[Any, ...], ...]:
        return self._rows

    def close(self) -> None:
        pass


class _PostgresLikeConnection:
    """Fake DB-API connection standing in for a real PostgreSQL driver's dialect."""

    def __init__(self, columns: tuple[str, ...]) -> None:
        self.columns = columns
        self.information_schema_queries = 0
        self.rollback_calls = 0

    def cursor(self) -> _PostgresLikeCursor:
        return _PostgresLikeCursor(self)

    def commit(self) -> None:
        pass

    def rollback(self) -> None:
        self.rollback_calls += 1

    def close(self) -> None:
        pass


def test_column_introspection_falls_back_to_information_schema_when_pragma_is_rejected() -> None:
    store = object.__new__(PostgreSQLRunStore)
    store._placeholder = "%s"
    connection = _PostgresLikeConnection(columns=("run_id", "operation", "config_id"))

    columns, is_sqlite = store._read_product_run_columns(connection)

    assert is_sqlite is False
    assert columns == frozenset(connection.columns)
    assert connection.information_schema_queries == 1
    assert connection.rollback_calls == 1


def _raw_insert_product_run(
    connection: sqlite3.Connection,
    run_id: str,
    *,
    config_id: str | None,
    config_version: int | None,
    package_checksum: str | None,
) -> None:
    connection.execute(
        "INSERT INTO product_runs (run_id, operation, configuration_reference, actor, audit_links, started_at, "
        "finished_at, phase, outcome, summary, results, config_id, config_version, package_checksum) "
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
            config_version,
            package_checksum,
        ),
    )
    connection.commit()


_PARTIAL_CONFIGURATION_BINDING_COMBINATIONS = [
    pytest.param("20260101T0000-aaaaaaaa", None, None, id="only-config-id"),
    pytest.param(None, 1, None, id="only-config-version"),
    pytest.param(None, None, "a" * 64, id="only-package-checksum"),
    pytest.param("20260101T0000-aaaaaaaa", 1, None, id="missing-package-checksum"),
    pytest.param("20260101T0000-aaaaaaaa", None, "a" * 64, id="missing-config-version"),
    pytest.param(None, 1, "a" * 64, id="missing-config-id"),
]


@pytest.mark.parametrize(
    ("config_id", "config_version", "package_checksum"), _PARTIAL_CONFIGURATION_BINDING_COMBINATIONS
)
def test_every_partial_configuration_binding_combination_is_refused_at_insert(
    config_id: str | None, config_version: int | None, package_checksum: str | None, tmp_path: Path
) -> None:
    database = tmp_path / "records.sqlite3"
    SQLiteRunStore(database)

    with sqlite3.connect(database) as connection, pytest.raises(sqlite3.IntegrityError, match="configuration-binding"):
        _raw_insert_product_run(
            connection,
            "raw-run-001",
            config_id=config_id,
            config_version=config_version,
            package_checksum=package_checksum,
        )


def test_fully_unbound_and_fully_bound_rows_are_both_accepted(tmp_path: Path) -> None:
    database = tmp_path / "records.sqlite3"
    SQLiteRunStore(database)

    with sqlite3.connect(database) as connection:
        _raw_insert_product_run(connection, "unbound-run", config_id=None, config_version=None, package_checksum=None)
        _raw_insert_product_run(
            connection,
            "bound-run",
            config_id="20260101T0000-aaaaaaaa",
            config_version=1,
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
            "SELECT config_id, config_version, package_checksum FROM product_runs WHERE run_id = ?",
            ("update-target",),
        ).fetchone()

    assert unchanged == (None, None, None)


class _RecordingPostgresLikeConnection(_PostgresLikeConnection):
    """Fake PostgreSQL-flavored connection that records every executed statement."""

    def __init__(self, columns: tuple[str, ...]) -> None:
        super().__init__(columns)
        self.executed: list[str] = []

    def cursor(self) -> _RecordingPostgresLikeCursor:
        return _RecordingPostgresLikeCursor(self)


class _RecordingPostgresLikeCursor(_PostgresLikeCursor):
    def execute(self, operation: str, parameters: tuple[Any, ...] = ()) -> _RecordingPostgresLikeCursor:
        self._connection.executed.append(operation)  # type: ignore[attr-defined]
        return super().execute(operation, parameters)


def test_postgresql_dialect_binding_constraint_uses_a_check_constraint_not_a_trigger() -> None:
    store = object.__new__(PostgreSQLRunStore)
    store._placeholder = "%s"
    store._schema_conflict_codes = product_store_store._POSTGRESQL_SCHEMA_CONFLICT_CODES
    connection = _RecordingPostgresLikeConnection(columns=("run_id", "operation"))
    store._connect = lambda: connection

    store._initialize()

    assert any("ADD CONSTRAINT" in statement and "CHECK" in statement for statement in connection.executed)
    assert not any("CREATE TRIGGER" in statement for statement in connection.executed)
