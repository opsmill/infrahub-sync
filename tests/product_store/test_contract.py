from __future__ import annotations

import sqlite3
import subprocess  # noqa: S404 - fixed local interpreter probes restart durability.
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from infrahub_sync import product_store
from infrahub_sync.product_store import (
    ArtifactReference,
    ArtifactUnavailableError,
    DuplicateArtifactError,
    DuplicatePrefectExecutionError,
    DuplicateRunError,
    PrefectExecutionLink,
    ProductProjection,
    ProductRun,
    local_product_projection,
)
from infrahub_sync.product_store import store as product_store_store
from infrahub_sync.product_store.store import FileArtifactStore, PostgreSQLRunStore, S3ArtifactStore, SQLiteRunStore

EXPECTED_PUBLIC_NAMES = {
    "ArtifactReference",
    "ArtifactUnavailableError",
    "DBAPIConnection",
    "DuplicateArtifactError",
    "DuplicatePrefectExecutionError",
    "DuplicateRunError",
    "LookupResult",
    "PrefectExecutionLink",
    "ProductProjection",
    "ProductRun",
    "S3Client",
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


def _connect(path: Path):
    return lambda: _ConnectionAdapter(path)


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


def test_sqlite_foreign_key_failure_passes_through_as_integrity_error(tmp_path: Path) -> None:
    store = SQLiteRunStore(tmp_path / "records.sqlite3")

    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY constraint failed"):
        store.add_prefect_execution(
            "missing-run",
            PrefectExecutionLink(flow_run_id="flow-001", purpose="plan", attempt=1),
        )


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
    restarted = ProductProjection(records, counting)
    kind = retry.get("kind", "plan")
    media_type = retry.get("media_type", "application/json")
    data = retry.get("data", b"{}")
    assert isinstance(kind, str)
    assert isinstance(media_type, str)
    assert isinstance(data, bytes)
    with pytest.raises(DuplicateArtifactError, match="pending publication with different content or metadata"):
        restarted.publish_artifact("run-001", artifact_id="plan", kind=kind, media_type=media_type, data=data)

    assert counting.publish_calls == 0
    assert restarted.lookup_artifact("run-001", "plan").reason == "artifact-publication-incomplete"
    restarted.publish_artifact("run-001", artifact_id="plan", kind="plan", media_type="application/json", data=b"{}")
    assert restarted.lookup_artifact("run-001", "plan").value == b"{}"


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


def test_filesystem_path_guard_rejects_resolved_parent_escape(tmp_path: Path) -> None:
    store = FileArtifactStore(tmp_path / "objects")

    with pytest.raises(ValueError, match="escapes its configured root"):
        store._path("valid/../../../outside/data")


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
