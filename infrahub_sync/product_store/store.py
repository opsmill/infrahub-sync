"""Durable relational and immutable artifact providers for product records."""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from tempfile import mkdtemp
from typing import Any, Protocol, cast

from infrahub_sync.execution import redact
from infrahub_sync.plan.canonical import canonical_json_bytes
from infrahub_sync.product_store.models import ArtifactReference, LookupResult, PrefectExecutionLink, ProductRun

_SCHEMA = """
CREATE TABLE IF NOT EXISTS product_runs (
    run_id TEXT PRIMARY KEY, operation TEXT NOT NULL, configuration_reference TEXT NOT NULL,
    actor TEXT, audit_links TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT,
    phase TEXT NOT NULL, outcome TEXT, summary TEXT NOT NULL, results TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS artifact_refs (
    run_id TEXT NOT NULL, artifact_id TEXT NOT NULL, kind TEXT NOT NULL, media_type TEXT NOT NULL,
    digest TEXT NOT NULL, size INTEGER NOT NULL, object_key TEXT NOT NULL UNIQUE,
    manifest_key TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL, expires_at TEXT, published INTEGER NOT NULL,
    PRIMARY KEY (run_id, artifact_id), FOREIGN KEY (run_id) REFERENCES product_runs(run_id)
);
CREATE TABLE IF NOT EXISTS prefect_executions (
    run_id TEXT NOT NULL, flow_run_id TEXT NOT NULL, deployment_id TEXT, purpose TEXT NOT NULL,
    attempt INTEGER NOT NULL, last_observed_state TEXT, last_observed_at TEXT, position INTEGER NOT NULL,
    PRIMARY KEY (run_id, flow_run_id), FOREIGN KEY (run_id) REFERENCES product_runs(run_id)
);
"""

# Stable SQLite extended result codes. Python 3.10's sqlite3 module does not expose
# their symbolic names even when an exception provides ``sqlite_errorcode``.
_SQLITE_UNIQUE_CONSTRAINT_CODES = frozenset({1555, 2067})


class DuplicateRunError(ValueError):
    """The requested stable Sync run ID already exists."""


class DuplicateArtifactError(ValueError):
    """The artifact ID or immutable object key is already reserved or published."""


class DuplicatePrefectExecutionError(ValueError):
    """The Prefect flow-run ID is already linked to this Sync run."""


class ArtifactUnavailableError(RuntimeError):
    """A referenced artifact is not available as a complete, valid publication."""


class _Cursor(Protocol):
    rowcount: int

    def execute(self, operation: str, parameters: Sequence[Any] = ()) -> _Cursor: ...

    def fetchone(self) -> Sequence[Any] | None: ...

    def fetchall(self) -> Sequence[Sequence[Any]]: ...

    def close(self) -> None: ...


class DBAPIConnection(Protocol):
    """Minimal synchronous DB-API connection required by the PostgreSQL profile."""

    def cursor(self) -> _Cursor: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...

    def close(self) -> None: ...


class _RunStore(Protocol):
    def create(self, run: ProductRun) -> None: ...

    def lookup(self, run_id: str) -> LookupResult[ProductRun]: ...

    def reserve_artifact(self, reference: ArtifactReference) -> None: ...

    def mark_artifact_published(self, reference: ArtifactReference) -> None: ...

    def lookup_artifact_reference(
        self, run_id: str, artifact_id: str
    ) -> LookupResult[tuple[ArtifactReference, bool]]: ...

    def has_pending_artifacts(self, run_id: str) -> bool: ...

    def add_prefect_execution(self, run_id: str, link: PrefectExecutionLink) -> None: ...

    def finish(
        self,
        run_id: str,
        *,
        phase: str,
        outcome: str,
        finished_at: datetime,
        summary: Mapping[str, Any],
        results: Mapping[str, Any],
    ) -> None: ...


class _RelationalRunStore:
    """Small SQL implementation shared by the SQLite and PostgreSQL profiles."""

    def __init__(self, connect: Callable[[], DBAPIConnection], *, placeholder: str) -> None:
        self._connect = connect
        self._placeholder = placeholder
        self._initialize()

    def _sql(self, statement: str) -> str:
        return statement.replace("?", self._placeholder)

    def _initialize(self) -> None:
        connection = self._connect()
        try:
            cursor = connection.cursor()
            try:
                for statement in _SCHEMA.split(";"):
                    if statement.strip():
                        cursor.execute(statement)
                connection.commit()
            finally:
                cursor.close()
        finally:
            connection.close()

    def create(self, run: ProductRun) -> None:
        connection = self._connect()
        try:
            cursor = connection.cursor()
            try:
                cursor.execute(
                    self._sql("INSERT INTO product_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"),
                    (
                        run.run_id,
                        run.operation,
                        run.configuration_reference,
                        run.actor,
                        _json(run.audit_links),
                        run.started_at.isoformat(),
                        _iso(run.finished_at),
                        run.phase,
                        run.outcome,
                        _json(run.summary),
                        _json(run.results),
                    ),
                )
                for position, link in enumerate(run.prefect_executions):
                    cursor.execute(
                        self._sql("INSERT INTO prefect_executions VALUES (?, ?, ?, ?, ?, ?, ?, ?)"),
                        (
                            run.run_id,
                            link.flow_run_id,
                            link.deployment_id,
                            link.purpose,
                            link.attempt,
                            link.last_observed_state,
                            _iso(link.last_observed_at),
                            position,
                        ),
                    )
                connection.commit()
            except Exception as exc:
                connection.rollback()
                if _is_unique_violation(exc):
                    msg = f"Sync run ID {run.run_id!r} already exists"
                    raise DuplicateRunError(msg) from exc
                raise
            finally:
                cursor.close()
        finally:
            connection.close()

    def lookup(self, run_id: str) -> LookupResult[ProductRun]:
        connection = self._connect()
        try:
            cursor = connection.cursor()
            try:
                cursor.execute(self._sql("SELECT * FROM product_runs WHERE run_id = ?"), (run_id,))
                row = cursor.fetchone()
                if row is None:
                    return LookupResult(value=None, reason="run-not-found")
                cursor.execute(
                    self._sql("SELECT * FROM artifact_refs WHERE run_id = ? AND published = 1 ORDER BY artifact_id"),
                    (run_id,),
                )
                references = cursor.fetchall()
                cursor.execute(
                    self._sql("SELECT * FROM prefect_executions WHERE run_id = ? ORDER BY position"),
                    (run_id,),
                )
                links = cursor.fetchall()
            finally:
                cursor.close()
        finally:
            connection.close()
        return LookupResult(value=_run_from_rows(row, references, links))

    def reserve_artifact(self, reference: ArtifactReference) -> None:
        connection = self._connect()
        try:
            cursor = connection.cursor()
            try:
                self._insert_reference(cursor, reference, published=False)
                connection.commit()
            except Exception as exc:
                connection.rollback()
                if _is_unique_violation(exc):
                    msg = (
                        f"Artifact {reference.artifact_id!r} already has a pending or published reservation "
                        f"on run {reference.run_id!r}"
                    )
                    raise DuplicateArtifactError(msg) from exc
                raise
            finally:
                cursor.close()
        finally:
            connection.close()

    def mark_artifact_published(self, reference: ArtifactReference) -> None:
        connection = self._connect()
        try:
            cursor = connection.cursor()
            try:
                cursor.execute(
                    self._sql(
                        "UPDATE artifact_refs SET published = 1 WHERE run_id = ? AND artifact_id = ? "
                        "AND digest = ? AND object_key = ? AND manifest_key = ? AND published = 0"
                    ),
                    (
                        reference.run_id,
                        reference.artifact_id,
                        reference.digest,
                        reference.object_key,
                        reference.manifest_key,
                    ),
                )
                _require_publication_marked(cursor, reference)
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                cursor.close()
        finally:
            connection.close()

    def lookup_artifact_reference(self, run_id: str, artifact_id: str) -> LookupResult[tuple[ArtifactReference, bool]]:
        connection = self._connect()
        try:
            cursor = connection.cursor()
            try:
                cursor.execute(
                    self._sql("SELECT * FROM artifact_refs WHERE run_id = ? AND artifact_id = ?"),
                    (run_id, artifact_id),
                )
                row = cursor.fetchone()
            finally:
                cursor.close()
        finally:
            connection.close()
        if row is None:
            return LookupResult(value=None, reason="artifact-reference-not-found")
        return LookupResult(value=(_reference_from_row(row), bool(row[10])))

    def has_pending_artifacts(self, run_id: str) -> bool:
        connection = self._connect()
        try:
            cursor = connection.cursor()
            try:
                cursor.execute(
                    self._sql("SELECT COUNT(*) FROM artifact_refs WHERE run_id = ? AND published = 0"),
                    (run_id,),
                )
                row = cursor.fetchone()
            finally:
                cursor.close()
        finally:
            connection.close()
        return row is not None and int(row[0]) > 0

    def add_prefect_execution(self, run_id: str, link: PrefectExecutionLink) -> None:
        connection = self._connect()
        try:
            cursor = connection.cursor()
            try:
                cursor.execute(
                    self._sql("SELECT COUNT(*) FROM prefect_executions WHERE run_id = ?"),
                    (run_id,),
                )
                count_row = cursor.fetchone()
                position = int(count_row[0]) if count_row is not None else 0
                cursor.execute(
                    self._sql("INSERT INTO prefect_executions VALUES (?, ?, ?, ?, ?, ?, ?, ?)"),
                    (
                        run_id,
                        link.flow_run_id,
                        link.deployment_id,
                        link.purpose,
                        link.attempt,
                        link.last_observed_state,
                        _iso(link.last_observed_at),
                        position,
                    ),
                )
                connection.commit()
            except Exception as exc:
                connection.rollback()
                if _is_unique_violation(exc):
                    msg = f"Prefect flow-run ID {link.flow_run_id!r} is already linked to Sync run {run_id!r}"
                    raise DuplicatePrefectExecutionError(msg) from exc
                raise
            finally:
                cursor.close()
        finally:
            connection.close()

    def _insert_reference(self, cursor: _Cursor, reference: ArtifactReference, *, published: bool) -> None:
        cursor.execute(
            self._sql("INSERT INTO artifact_refs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"),
            (
                reference.run_id,
                reference.artifact_id,
                reference.kind,
                reference.media_type,
                reference.digest,
                reference.size,
                reference.object_key,
                reference.manifest_key,
                reference.created_at.isoformat(),
                _iso(reference.expires_at),
                int(published),
            ),
        )

    def finish(
        self,
        run_id: str,
        *,
        phase: str,
        outcome: str,
        finished_at: datetime,
        summary: Mapping[str, Any],
        results: Mapping[str, Any],
    ) -> None:
        connection = self._connect()
        try:
            cursor = connection.cursor()
            try:
                cursor.execute(
                    self._sql(
                        "UPDATE product_runs SET phase = ?, outcome = ?, finished_at = ?, summary = ?, results = ? "
                        "WHERE run_id = ?"
                    ),
                    (phase, outcome, finished_at.isoformat(), _json(summary), _json(results), run_id),
                )
                connection.commit()
            finally:
                cursor.close()
        finally:
            connection.close()


class SQLiteRunStore(_RelationalRunStore):
    """SQLite relational provider rooted at an explicit cache location."""

    def __init__(self, database: Path) -> None:
        database.parent.mkdir(parents=True, exist_ok=True)

        def connect() -> DBAPIConnection:
            connection = sqlite3.connect(database)
            connection.execute("PRAGMA foreign_keys = ON")
            return cast("DBAPIConnection", connection)

        super().__init__(connect, placeholder="?")


class PostgreSQLRunStore(_RelationalRunStore):
    """PostgreSQL DB-API provider; the caller supplies its connection factory."""

    def __init__(self, connect: Callable[[], DBAPIConnection]) -> None:
        super().__init__(connect, placeholder="%s")


class _ArtifactStore(Protocol):
    def publish(self, reference: ArtifactReference, data: bytes) -> None: ...

    def lookup(self, reference: ArtifactReference) -> LookupResult[bytes]: ...


class FileArtifactStore:
    """Filesystem artifact provider using an atomic directory rename as commit."""

    def __init__(self, root: Path) -> None:
        self.root = root
        root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        root = self.root.resolve()
        path = self.root.joinpath(*key.split("/")).resolve()
        if not path.is_relative_to(root):
            msg = f"artifact key escapes its configured root: {key!r}"
            raise ValueError(msg)
        return path

    def _publication_checkpoint(self) -> None:
        """Private fault-injection seam after data and before manifest publication."""

    def publish(self, reference: ArtifactReference, data: bytes) -> None:
        final_dir = self._path(reference.manifest_key).parent
        if final_dir.exists():
            msg = f"Artifact {reference.artifact_id!r} is already published"
            raise DuplicateArtifactError(msg)
        final_dir.parent.mkdir(parents=True, exist_ok=True)
        temporary = Path(mkdtemp(prefix=f".{reference.artifact_id}-", dir=final_dir.parent))
        try:
            data_path = temporary / "data"
            _write_fsynced(data_path, data)
            self._publication_checkpoint()
            _write_fsynced(temporary / "manifest.json", _manifest_bytes(reference))
            temporary.replace(final_dir)
            _fsync_directory(final_dir.parent)
        except BaseException:
            _remove_private_publication(temporary)
            raise

    def lookup(self, reference: ArtifactReference) -> LookupResult[bytes]:
        if _expired(reference):
            return LookupResult(value=None, reason="artifact-expired")
        try:
            manifest = self._path(reference.manifest_key).read_bytes()
        except (FileNotFoundError, NotADirectoryError):
            return LookupResult(value=None, reason="manifest-unavailable")
        try:
            data = self._path(reference.object_key).read_bytes()
        except (FileNotFoundError, NotADirectoryError):
            return LookupResult(value=None, reason="data-unavailable")
        return _validate_publication(reference, manifest, data)


class S3Client(Protocol):
    """Minimal operations required from an S3-compatible object client."""

    def put(self, *, bucket: str, key: str, data: bytes, if_absent: bool = False) -> None: ...

    def get(self, *, bucket: str, key: str) -> bytes | None: ...

    def copy(self, *, bucket: str, source: str, destination: str) -> None: ...

    def delete(self, *, bucket: str, key: str) -> None: ...


class S3ArtifactStore:
    """S3-compatible provider that commits publication with a manifest put."""

    def __init__(self, client: S3Client, *, bucket: str, prefix: str = "") -> None:
        self._client = client
        self._bucket = bucket
        self._prefix = prefix.strip("/")

    def _key(self, key: str) -> str:
        return f"{self._prefix}/{key}" if self._prefix else key

    def _publication_checkpoint(self) -> None:
        """Private fault-injection seam after data and before manifest publication."""

    def publish(self, reference: ArtifactReference, data: bytes) -> None:
        manifest_key = self._key(reference.manifest_key)
        if self._client.get(bucket=self._bucket, key=manifest_key) is not None:
            msg = f"Artifact {reference.artifact_id!r} is already published"
            raise DuplicateArtifactError(msg)
        object_key = self._key(reference.object_key)
        temporary_key = f"{object_key}.uncommitted"
        self._client.put(bucket=self._bucket, key=temporary_key, data=data)
        try:
            self._client.copy(bucket=self._bucket, source=temporary_key, destination=object_key)
            self._publication_checkpoint()
            self._client.put(
                bucket=self._bucket,
                key=manifest_key,
                data=_manifest_bytes(reference),
                if_absent=True,
            )
        finally:
            self._client.delete(bucket=self._bucket, key=temporary_key)

    def lookup(self, reference: ArtifactReference) -> LookupResult[bytes]:
        if _expired(reference):
            return LookupResult(value=None, reason="artifact-expired")
        manifest = self._client.get(bucket=self._bucket, key=self._key(reference.manifest_key))
        if manifest is None:
            return LookupResult(value=None, reason="manifest-unavailable")
        data = self._client.get(bucket=self._bucket, key=self._key(reference.object_key))
        if data is None:
            return LookupResult(value=None, reason="data-unavailable")
        return _validate_publication(reference, manifest, data)


class ProductProjection:
    """One product-record contract over interchangeable relational/object providers."""

    def __init__(self, records: _RunStore, artifacts: _ArtifactStore) -> None:
        self._records = records
        self._artifacts = artifacts

    def create_run(self, run: ProductRun, *, secrets: Sequence[str] = ()) -> None:
        """Create one stable run record, rejecting duplicate Sync identity."""
        if run.finished_at is not None or run.outcome is not None or run.artifact_refs:
            msg = "a new product record must be unfinished and have no artifact references"
            raise ValueError(msg)
        self._records.create(_redacted_run(run, secrets))

    def lookup_run(self, run_id: str) -> LookupResult[ProductRun]:
        """Look up a product record by stable Sync run ID."""
        return self._records.lookup(run_id)

    def add_prefect_execution(self, run_id: str, link: PrefectExecutionLink, *, secrets: Sequence[str] = ()) -> None:
        """Append one purpose-labelled execution without changing Sync identity."""
        if not self.lookup_run(run_id).available:
            msg = f"Cannot link a Prefect execution to unavailable Sync run ID {run_id!r}"
            raise ArtifactUnavailableError(msg)
        sanitized = PrefectExecutionLink.model_validate(_redact_value(link.model_dump(mode="json"), secrets))
        self._records.add_prefect_execution(run_id, sanitized)

    def publish_artifact(
        self,
        run_id: str,
        *,
        artifact_id: str,
        kind: str,
        media_type: str,
        data: bytes,
        secrets: Sequence[str] = (),
    ) -> ArtifactReference:
        """Reserve the immutable reference, publish bytes and manifest, then expose it."""
        if not self.lookup_run(run_id).available:
            msg = f"Cannot publish an artifact for unavailable Sync run ID {run_id!r}"
            raise ArtifactUnavailableError(msg)
        sanitized = _redact_bytes(data, secrets)
        artifact_id = redact(artifact_id, secrets)
        kind = redact(kind, secrets)
        media_type = redact(media_type, secrets)
        digest = sha256(sanitized).hexdigest()
        base = f"runs/{run_id}/artifacts/{artifact_id}/{digest}"
        reference = ArtifactReference(
            artifact_id=artifact_id,
            run_id=run_id,
            kind=kind,
            media_type=media_type,
            digest=digest,
            size=len(sanitized),
            object_key=f"{base}/data",
            manifest_key=f"{base}/manifest.json",
            created_at=datetime.now(timezone.utc),
        )
        stored = self._records.lookup_artifact_reference(run_id, artifact_id)
        if stored.value is None:
            self._records.reserve_artifact(reference)
        else:
            existing, published = stored.value
            if published:
                msg = f"Artifact {artifact_id!r} is already published on run {run_id!r}"
                raise DuplicateArtifactError(msg)
            if not _same_publication(existing, reference):
                msg = (
                    f"Artifact {artifact_id!r} has a pending publication with different content or metadata; "
                    "retry rejected"
                )
                raise DuplicateArtifactError(msg)
            reference = existing
            publication = self._artifacts.lookup(reference)
            if publication.available:
                self._records.mark_artifact_published(reference)
                return reference
            if publication.reason != "manifest-unavailable":
                msg = f"Artifact {artifact_id!r} has a pending publication that cannot be resumed: {publication.reason}"
                raise ArtifactUnavailableError(msg)
        self._artifacts.publish(reference, sanitized)
        self._records.mark_artifact_published(reference)
        return reference

    def lookup_artifact(self, run_id: str, artifact_id: str) -> LookupResult[bytes]:
        """Resolve only a run-owned immutable reference, with explicit unavailability."""
        run = self.lookup_run(run_id)
        if run.value is None:
            return LookupResult(value=None, reason=run.reason)
        stored = self._records.lookup_artifact_reference(run_id, artifact_id)
        if stored.value is None:
            return LookupResult(value=None, reason=stored.reason)
        reference, published = stored.value
        if not published:
            return LookupResult(value=None, reason="artifact-publication-incomplete")
        return self._artifacts.lookup(reference)

    def finish_run(
        self,
        run_id: str,
        *,
        phase: str,
        outcome: str,
        summary: Mapping[str, Any],
        results: Mapping[str, Any],
        secrets: Sequence[str] = (),
    ) -> None:
        """Complete or extend a run only while every attached artifact is available."""
        run = self.lookup_run(run_id)
        if run.value is None:
            msg = f"Sync run ID {run_id!r} is unavailable"
            raise ArtifactUnavailableError(msg)
        if self._records.has_pending_artifacts(run_id):
            msg = f"Sync run ID {run_id!r} has an incomplete artifact publication"
            raise ArtifactUnavailableError(msg)
        for reference in run.value.artifact_refs:
            result = self._artifacts.lookup(reference)
            if not result.available:
                msg = f"Artifact {reference.artifact_id!r} is unavailable: {result.reason}"
                raise ArtifactUnavailableError(msg)
        self._records.finish(
            run_id,
            phase=redact(phase, secrets),
            outcome=redact(outcome, secrets),
            finished_at=datetime.now(timezone.utc),
            summary=cast("Mapping[str, Any]", _redact_value(summary, secrets)),
            results=cast("Mapping[str, Any]", _redact_value(results, secrets)),
        )


def local_product_projection(cache_location: Path) -> ProductProjection:
    """Build the SQLite/filesystem profile at an explicit, cwd-independent root."""
    root = cache_location.expanduser()
    if not root.is_absolute():
        msg = f"cache_location must be absolute after user expansion, got {str(cache_location)!r}"
        raise ValueError(msg)
    return ProductProjection(SQLiteRunStore(root / "product-records.sqlite3"), FileArtifactStore(root / "artifacts"))


def production_product_projection(
    *,
    connect: Callable[[], DBAPIConnection],
    s3_client: S3Client,
    bucket: str,
    prefix: str = "infrahub-sync",
) -> ProductProjection:
    """Build the PostgreSQL/S3-compatible profile from production clients."""
    return ProductProjection(
        PostgreSQLRunStore(connect),
        S3ArtifactStore(s3_client, bucket=bucket, prefix=prefix),
    )


def _json(value: Any) -> str:
    return canonical_json_bytes(value).decode()


def _iso(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def _run_from_rows(
    row: Sequence[Any], references: Sequence[Sequence[Any]], links: Sequence[Sequence[Any]]
) -> ProductRun:
    return ProductRun.model_validate(
        {
            "run_id": row[0],
            "operation": row[1],
            "configuration_reference": row[2],
            "actor": row[3],
            "audit_links": json.loads(row[4]),
            "started_at": row[5],
            "finished_at": row[6],
            "phase": row[7],
            "outcome": row[8],
            "summary": json.loads(row[9]),
            "results": json.loads(row[10]),
            "artifact_refs": [_reference_from_row(item).model_dump() for item in references],
            "prefect_executions": [
                {
                    "flow_run_id": item[1],
                    "deployment_id": item[2],
                    "purpose": item[3],
                    "attempt": item[4],
                    "last_observed_state": item[5],
                    "last_observed_at": item[6],
                }
                for item in links
            ],
        }
    )


def _reference_from_row(row: Sequence[Any]) -> ArtifactReference:
    return ArtifactReference.model_validate(
        {
            "run_id": row[0],
            "artifact_id": row[1],
            "kind": row[2],
            "media_type": row[3],
            "digest": row[4],
            "size": row[5],
            "object_key": row[6],
            "manifest_key": row[7],
            "created_at": row[8],
            "expires_at": row[9],
        }
    )


def _same_publication(existing: ArtifactReference, requested: ArtifactReference) -> bool:
    return existing.model_dump(exclude={"created_at"}) == requested.model_dump(exclude={"created_at"})


def _is_unique_violation(exc: BaseException) -> bool:
    if isinstance(exc, sqlite3.IntegrityError):
        error_code = getattr(exc, "sqlite_errorcode", None)
        if error_code is not None:
            return error_code in _SQLITE_UNIQUE_CONSTRAINT_CODES
        return str(exc).startswith(("UNIQUE constraint failed", "PRIMARY KEY constraint failed"))

    if getattr(exc, "sqlstate", None) == "23505" or getattr(exc, "pgcode", None) == "23505":
        return True
    diagnostic = getattr(exc, "diag", None)
    return getattr(diagnostic, "sqlstate", None) == "23505"


def _require_publication_marked(cursor: _Cursor, reference: ArtifactReference) -> None:
    if cursor.rowcount != 1:
        msg = f"Artifact {reference.artifact_id!r} on run {reference.run_id!r} has no matching pending publication"
        raise ArtifactUnavailableError(msg)


def _redact_value(value: Any, secrets: Sequence[str]) -> Any:
    if isinstance(value, str):
        return redact(value, secrets)
    if isinstance(value, Mapping):
        return {redact(str(key), secrets): _redact_value(item, secrets) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact_value(item, secrets) for item in value]
    return value


def _redacted_run(run: ProductRun, secrets: Sequence[str]) -> ProductRun:
    return ProductRun.model_validate(_redact_value(run.model_dump(mode="json"), secrets))


def _redact_bytes(data: bytes, secrets: Sequence[str]) -> bytes:
    for secret in secrets:
        if secret:
            data = data.replace(secret.encode(), b"***")
    return data


def _manifest_bytes(reference: ArtifactReference) -> bytes:
    return canonical_json_bytes({"format_version": 1, **reference.model_dump(mode="json")})


def _validate_publication(reference: ArtifactReference, manifest: bytes, data: bytes) -> LookupResult[bytes]:
    try:
        stored = json.loads(manifest)
        parsed = ArtifactReference.model_validate(
            {key: value for key, value in stored.items() if key != "format_version"}
        )
    except (json.JSONDecodeError, ValueError):
        return LookupResult(value=None, reason="manifest-invalid")
    if parsed != reference or sha256(data).hexdigest() != reference.digest or len(data) != reference.size:
        return LookupResult(value=None, reason="artifact-integrity-failed")
    return LookupResult(value=data)


def _expired(reference: ArtifactReference) -> bool:
    return reference.expires_at is not None and reference.expires_at <= datetime.now(timezone.utc)


def _write_fsynced(path: Path, data: bytes) -> None:
    with path.open("xb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _remove_private_publication(path: Path) -> None:
    if not path.exists():
        return
    for child in path.iterdir():
        child.unlink()
    path.rmdir()
