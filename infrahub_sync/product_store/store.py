"""Durable relational and immutable artifact providers for product records."""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from math import isfinite
from pathlib import Path, PurePosixPath
from tempfile import mkdtemp
from time import sleep
from typing import Any, Literal, Protocol, cast
from uuid import UUID

from pydantic import TypeAdapter

from infrahub_sync.cache.paths import generate_run_id
from infrahub_sync.configuration import ConfigurationPackage, validate_package_credentials
from infrahub_sync.execution import REDACTED, redact
from infrahub_sync.plan.canonical import canonical_json_bytes
from infrahub_sync.product_store.models import (
    ArtifactReference,
    AuditEvent,
    ConfigurationSummary,
    ConfigurationVersion,
    ExecutionFinishWriteback,
    ExecutionMergeWriteback,
    ExecutionWriteback,
    LookupResult,
    MutationReceipt,
    PrefectExecutionLink,
    ProductRun,
)

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
    attempt INTEGER NOT NULL, last_observed_state TEXT, last_observed_at TEXT, submitted_at TEXT,
    claimed_at TEXT, claiming_worker_id TEXT, stalled_at TEXT, cancellation_requested_at TEXT,
    cancellation_recovery_deadline_at TEXT, cancellation_receipt_id TEXT, cancellation_acknowledged_at TEXT,
    terminal_at TEXT, terminal_state TEXT, terminal_outcome TEXT, position INTEGER NOT NULL,
    PRIMARY KEY (run_id, flow_run_id), FOREIGN KEY (run_id) REFERENCES product_runs(run_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS prefect_executions_run_position
    ON prefect_executions (run_id, position);
CREATE UNIQUE INDEX IF NOT EXISTS prefect_executions_run_purpose_attempt
    ON prefect_executions (run_id, purpose, attempt);
CREATE TABLE IF NOT EXISTS mutation_receipts (
    receipt_id TEXT PRIMARY KEY, actor TEXT NOT NULL, key_digest TEXT NOT NULL,
    operation TEXT NOT NULL, target_run_id TEXT, request_fingerprint TEXT NOT NULL,
    reason TEXT NOT NULL, resource_kind TEXT NOT NULL DEFAULT 'run', resource_id TEXT NOT NULL, run_id TEXT, prefect_key TEXT,
    state TEXT NOT NULL, response_status INTEGER, response_body TEXT, flow_run_id TEXT,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    UNIQUE (actor, key_digest)
);
CREATE TABLE IF NOT EXISTS write_admissions (
    run_id TEXT PRIMARY KEY, receipt_id TEXT NOT NULL UNIQUE, operation TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES product_runs(run_id),
    FOREIGN KEY (receipt_id) REFERENCES mutation_receipts(receipt_id)
);
CREATE TABLE IF NOT EXISTS audit_events (
    event_id TEXT PRIMARY KEY, run_id TEXT, actor TEXT NOT NULL, operation TEXT NOT NULL,
    reason TEXT NOT NULL, outcome TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS audit_events_run_created
    ON audit_events (run_id, created_at);
CREATE TABLE IF NOT EXISTS configurations (
    config_id TEXT PRIMARY KEY, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS configuration_versions (
    config_id TEXT NOT NULL, registry_version INTEGER NOT NULL, package_checksum TEXT NOT NULL,
    declared_content TEXT NOT NULL, created_at TEXT NOT NULL,
    PRIMARY KEY (config_id, registry_version),
    FOREIGN KEY (config_id) REFERENCES configurations(config_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS configuration_versions_checksum
    ON configuration_versions (config_id, package_checksum);
"""
_SQLITE_EXECUTION_TIMESTAMP_FUNCTION = "infrahub_sync_execution_timestamp_microseconds"
_UNIX_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_CANCELLATION_DEADLINE_ERROR = "execution cancellation recovery deadline is invalid"
_CANCELLATION_POSITION_ERROR = "execution cancellation position is invalid"
_SELECT_LATEST_EXECUTION_RESULTS = (
    "SELECT results FROM product_runs WHERE run_id = ? AND (SELECT latest.flow_run_id "
    "FROM prefect_executions AS latest WHERE latest.run_id = product_runs.run_id "
    "ORDER BY latest.position DESC LIMIT 1) = ?"
)
_UPDATE_LATEST_EXECUTION_RESULTS = (
    "UPDATE product_runs SET results = ? WHERE run_id = ? AND (SELECT latest.flow_run_id "
    "FROM prefect_executions AS latest WHERE latest.run_id = product_runs.run_id "
    "ORDER BY latest.position DESC LIMIT 1) = ?"
)
_UPDATE_LATEST_EXECUTION_FINISH = (
    "UPDATE product_runs SET phase = ?, outcome = ?, finished_at = ?, summary = ?, results = ? "
    "WHERE run_id = ? AND (SELECT latest.flow_run_id FROM prefect_executions AS latest "
    "WHERE latest.run_id = product_runs.run_id ORDER BY latest.position DESC LIMIT 1) = ?"
)
_UPDATE_LATEST_EXECUTION_TERMINAL = (
    "UPDATE product_runs SET phase = ?, outcome = ?, finished_at = ? WHERE run_id = ? "
    "AND (SELECT latest.flow_run_id FROM prefect_executions AS latest "
    "WHERE latest.run_id = product_runs.run_id ORDER BY latest.position DESC LIMIT 1) = ?"
)
_UPDATE_LATEST_EXECUTION_CANCELLED = (
    "UPDATE product_runs SET phase = 'cancelled', outcome = 'cancelled', finished_at = ? WHERE run_id = ? "
    "AND (SELECT latest.flow_run_id FROM prefect_executions AS latest "
    "WHERE latest.run_id = product_runs.run_id ORDER BY latest.position DESC LIMIT 1) = ?"
)

# Nullable run-to-configuration binding columns on ``product_runs``. Added by an
# introspection-guarded ALTER (see ``_migrate_product_runs_columns``) rather than baked into
# the ``product_runs`` definition above, so a pre-existing deployment's table is migrated the
# same way a brand-new one is initialized. Deliberately inert so far: no ``ProductRun`` field
# reads or writes them, and nothing allocates a run into a registered configuration yet — only
# the invariant that a row's three binding columns are either all NULL (unregistered) or all
# NOT NULL (registered), so that a future writer inherits an already-enforced constraint
# rather than introducing one.
_CONFIGURATION_BINDING_COLUMNS: tuple[tuple[str, str], ...] = (
    ("config_id", "TEXT"),
    ("registry_version", "INTEGER"),
    ("package_checksum", "TEXT"),
)
_PREFECT_EXECUTION_LIVENESS_COLUMNS: tuple[tuple[str, str], ...] = (
    ("submitted_at", "TEXT"),
    ("claimed_at", "TEXT"),
    ("claiming_worker_id", "TEXT"),
    ("stalled_at", "TEXT"),
    ("cancellation_requested_at", "TEXT"),
    ("cancellation_recovery_deadline_at", "TEXT"),
    ("cancellation_receipt_id", "TEXT"),
    ("cancellation_acknowledged_at", "TEXT"),
    ("terminal_at", "TEXT"),
    ("terminal_state", "TEXT"),
    ("terminal_outcome", "TEXT"),
)
_CONFIGURATION_BINDING_CONSTRAINT = "product_runs_configuration_binding_consistent"
_CONFIGURATION_BINDING_CHECK_EXPRESSION = (
    "(config_id IS NULL AND registry_version IS NULL AND package_checksum IS NULL) "
    "OR (config_id IS NOT NULL AND registry_version IS NOT NULL AND package_checksum IS NOT NULL)"
)
_SQLITE_MUTATION_RECEIPT_RESOURCE_TRIGGER_NAMES = (
    "mutation_receipts_resource_consistent_insert",
    "mutation_receipts_resource_consistent_update",
)
_SQLITE_MUTATION_RECEIPT_RESOURCE_CHECK = """NOT (
    (NEW.resource_kind = 'run' AND NEW.run_id IS NOT NULL AND NEW.prefect_key IS NOT NULL)
    OR (NEW.resource_kind IN ('configuration', 'configuration-registry') AND NEW.run_id IS NULL
        AND NEW.prefect_key IS NULL AND NEW.target_run_id IS NULL AND NEW.flow_run_id IS NULL)
)"""
_SQLITE_MUTATION_RECEIPT_RESOURCE_INSERT_TRIGGER = f"""
CREATE TRIGGER {_SQLITE_MUTATION_RECEIPT_RESOURCE_TRIGGER_NAMES[0]}
BEFORE INSERT ON mutation_receipts
FOR EACH ROW WHEN {_SQLITE_MUTATION_RECEIPT_RESOURCE_CHECK}
BEGIN SELECT RAISE(ABORT, 'mutation receipt resource identity is inconsistent'); END;
"""
_SQLITE_MUTATION_RECEIPT_RESOURCE_UPDATE_TRIGGER = f"""
CREATE TRIGGER {_SQLITE_MUTATION_RECEIPT_RESOURCE_TRIGGER_NAMES[1]}
BEFORE UPDATE ON mutation_receipts
FOR EACH ROW WHEN {_SQLITE_MUTATION_RECEIPT_RESOURCE_CHECK}
BEGIN SELECT RAISE(ABORT, 'mutation receipt resource identity is inconsistent'); END;
"""
_MUTATION_RECEIPT_RESOURCE_CONSTRAINT = "mutation_receipts_resource_consistent"
_MUTATION_RECEIPT_RESOURCE_CHECK = (
    "(resource_kind = 'run' AND run_id IS NOT NULL AND prefect_key IS NOT NULL) OR "
    "(resource_kind IN ('configuration', 'configuration-registry') AND run_id IS NULL "
    "AND prefect_key IS NULL AND target_run_id IS NULL AND flow_run_id IS NULL)"
)
_SQLITE_CONFIGURATION_BINDING_INSERT_TRIGGER_NAME = "product_runs_configuration_binding_insert"
_SQLITE_CONFIGURATION_BINDING_UPDATE_TRIGGER_NAME = "product_runs_configuration_binding_update"
# Both names must exist for the constraint to be considered present: SQLite enforcement is this
# pair of triggers, not either one alone (see ``_configuration_binding_constraint_exists``).
_SQLITE_CONFIGURATION_BINDING_TRIGGER_NAMES = (
    _SQLITE_CONFIGURATION_BINDING_INSERT_TRIGGER_NAME,
    _SQLITE_CONFIGURATION_BINDING_UPDATE_TRIGGER_NAME,
)
_SQLITE_CONFIGURATION_BINDING_INSERT_TRIGGER = f"""
CREATE TRIGGER IF NOT EXISTS {_SQLITE_CONFIGURATION_BINDING_INSERT_TRIGGER_NAME}
BEFORE INSERT ON product_runs
WHEN NOT (
    (NEW.config_id IS NULL AND NEW.registry_version IS NULL AND NEW.package_checksum IS NULL)
    OR (NEW.config_id IS NOT NULL AND NEW.registry_version IS NOT NULL AND NEW.package_checksum IS NOT NULL)
)
BEGIN
    SELECT RAISE(ABORT, 'product_runs configuration-binding columns must be all NULL or all NOT NULL');
END
"""
_SQLITE_CONFIGURATION_BINDING_UPDATE_TRIGGER = f"""
CREATE TRIGGER IF NOT EXISTS {_SQLITE_CONFIGURATION_BINDING_UPDATE_TRIGGER_NAME}
BEFORE UPDATE ON product_runs
WHEN NOT (
    (NEW.config_id IS NULL AND NEW.registry_version IS NULL AND NEW.package_checksum IS NULL)
    OR (NEW.config_id IS NOT NULL AND NEW.registry_version IS NOT NULL AND NEW.package_checksum IS NOT NULL)
)
BEGIN
    SELECT RAISE(ABORT, 'product_runs configuration-binding columns must be all NULL or all NOT NULL');
END
"""

# Stable SQLite extended result codes. Python 3.10's sqlite3 module does not expose
# their symbolic names even when an exception provides ``sqlite_errorcode``.
_SQLITE_UNIQUE_CONSTRAINT_CODES = frozenset({1555, 2067})
_POSTGRESQL_SCHEMA_CONFLICT_CODES = frozenset({"23505", "42P07", "42710"})
_POSTGRESQL_DUPLICATE_COLUMN_CODE = "42701"
# ``_migrate_mutation_receipts`` is the one catalog read that needs nullability, and it projects
# exactly (column_name, is_nullable). The other two read column names alone.
_CATALOG_NULLABILITY_ROW_WIDTH = 2
_PREFECT_POSITION_ATTEMPTS = 3
_RESULT_MERGE_ATTEMPTS = 5
_SCHEMA_INITIALIZATION_ATTEMPTS = 2
# Measured, not guessed: across roughly 4,800 trials of 8 concurrent distinct-checksum
# add_configuration_version() callers against SQLite (the tightest case, single-writer
# locking), the worst observed attempt count for any one caller was 5-7. This budget
# supports up to 8 concurrent writers with margin above that observed worst case.
_CONFIGURATION_VERSION_ATTEMPTS = 8
_JSON_MAPPING_ADAPTER = TypeAdapter(dict[str, Any])
_INVALID_MANAGED_WORKER_ID = "managed worker identity is invalid"

_INSERT_CONFIGURATION = "INSERT INTO configurations (config_id, created_at) VALUES (?, ?)"
_SELECT_CONFIGURATION = "SELECT config_id, created_at FROM configurations WHERE config_id = ?"
# The one stated configurations listing order. ``created_at`` alone is not total — the clock
# can give two registrations one timestamp — so ``config_id`` makes the order deterministic.
_SELECT_CONFIGURATIONS = "SELECT config_id, created_at FROM configurations ORDER BY created_at, config_id"
_INSERT_CONFIGURATION_VERSION = """INSERT INTO configuration_versions (config_id, registry_version, package_checksum,
declared_content, created_at) VALUES (?, ?, ?, ?, ?)"""
_SELECT_CONFIGURATION_VERSION = """SELECT config_id, registry_version, package_checksum, declared_content, created_at
FROM configuration_versions WHERE config_id = ? AND registry_version = ?"""
_SELECT_CONFIGURATION_VERSION_BY_CHECKSUM = """SELECT config_id, registry_version, package_checksum, declared_content,
created_at FROM configuration_versions WHERE config_id = ? AND package_checksum = ?"""
_SELECT_CONFIGURATION_VERSIONS = """SELECT config_id, registry_version, package_checksum, declared_content, created_at
FROM configuration_versions WHERE config_id = ? ORDER BY registry_version"""
_SELECT_NEXT_CONFIGURATION_VERSION = (
    "SELECT COALESCE(MAX(registry_version) + 1, 1) FROM configuration_versions WHERE config_id = ?"
)

_INSERT_PRODUCT_RUN = """INSERT INTO product_runs (run_id, operation, configuration_reference, config_id,
registry_version, package_checksum, actor, audit_links, started_at, finished_at, phase, outcome, summary, results)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""
_SELECT_PRODUCT_RUN = """SELECT run_id, operation, configuration_reference, actor, audit_links, started_at,
finished_at, phase, outcome, summary, results, config_id, registry_version, package_checksum
FROM product_runs WHERE run_id = ?"""
_SELECT_RUN_ARTIFACT_REFERENCES = """SELECT run_id, artifact_id, kind, media_type, digest, size, object_key,
manifest_key, created_at, expires_at, published FROM artifact_refs WHERE run_id = ? AND published = 1 ORDER BY artifact_id"""
_SELECT_ARTIFACT_REFERENCE = """SELECT run_id, artifact_id, kind, media_type, digest, size, object_key,
manifest_key, created_at, expires_at, published FROM artifact_refs WHERE run_id = ? AND artifact_id = ?"""
_INSERT_ARTIFACT_REFERENCE = """INSERT INTO artifact_refs (run_id, artifact_id, kind, media_type, digest, size,
object_key, manifest_key, created_at, expires_at, published) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""
_SELECT_PREFECT_EXECUTIONS = """SELECT run_id, flow_run_id, deployment_id, purpose, attempt, last_observed_state,
last_observed_at, submitted_at, claimed_at, claiming_worker_id, stalled_at, cancellation_requested_at,
cancellation_recovery_deadline_at, cancellation_receipt_id, cancellation_acknowledged_at, terminal_at, terminal_state,
terminal_outcome, position FROM prefect_executions WHERE run_id = ? ORDER BY position"""
_INSERT_PREFECT_EXECUTION = """INSERT INTO prefect_executions (run_id, flow_run_id, deployment_id, purpose, attempt,
last_observed_state, last_observed_at, submitted_at, claimed_at, claiming_worker_id, stalled_at,
cancellation_requested_at, cancellation_recovery_deadline_at, cancellation_receipt_id, cancellation_acknowledged_at,
terminal_at, terminal_state, terminal_outcome, position) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""
_INSERT_MUTATION_RECEIPT = """INSERT INTO mutation_receipts (receipt_id, actor, key_digest, operation,
target_run_id, request_fingerprint, reason, resource_kind, resource_id, run_id, prefect_key, state, response_status, response_body,
flow_run_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""
_SELECT_MUTATION_RECEIPT = """SELECT receipt_id, actor, key_digest, operation, target_run_id,
request_fingerprint, reason, resource_kind, resource_id, run_id, prefect_key, state, response_status, response_body, flow_run_id,
created_at, updated_at FROM mutation_receipts WHERE actor = ? AND key_digest = ?"""
_INSERT_AUDIT_EVENT = """INSERT INTO audit_events (event_id, run_id, actor, operation, reason, outcome, created_at)
VALUES (?, ?, ?, ?, ?, ?, ?)"""
_SELECT_AUDIT_EVENTS = """SELECT event_id, run_id, actor, operation, reason, outcome, created_at
FROM audit_events WHERE run_id = ? ORDER BY created_at, event_id"""
_SELECT_AUDIT_LINKS = "SELECT event_id FROM audit_events WHERE run_id = ? ORDER BY created_at, event_id"


class DuplicateRunError(ValueError):
    """The requested stable Sync run ID already exists."""


class ProductStoreProviderError(RuntimeError):
    """A driver-neutral durable-provider failure with an optional SQLSTATE."""

    def __init__(self, *, sqlstate: str | None = None) -> None:
        super().__init__("product store provider failed")
        self.sqlstate = sqlstate


class DuplicateArtifactError(ValueError):
    """The artifact ID or immutable object key is already reserved or published."""


class DuplicatePrefectExecutionError(ValueError):
    """The Prefect flow-run ID is already linked to this Sync run."""


class WriteAdmissionConflictError(ValueError):
    """A product run already owns its one durable write-capable admission."""


class ArtifactUnavailableError(RuntimeError):
    """A referenced artifact is not available as a complete, valid publication."""


class RunNotFoundError(ValueError):
    """A requested mutation targets a Sync run ID that does not exist."""


class ConfigurationNotFoundError(ValueError):
    """A requested mutation targets a configuration ID that does not exist."""


class ConfigurationVersionAllocationError(ValueError):
    """Every allocation attempt for a new configuration version was exhausted by contention."""


class DuplicateConfigurationError(ValueError):
    """The generated configuration ID already exists."""


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


class _RunStore(Protocol):  # pylint: disable=too-many-public-methods
    def create(self, run: ProductRun) -> None: ...

    def lookup(self, run_id: str) -> LookupResult[ProductRun]: ...

    def exists(self, run_id: str) -> bool: ...

    def reserve_artifact(self, reference: ArtifactReference) -> None: ...

    def mark_artifact_published(self, reference: ArtifactReference) -> None: ...

    def lookup_artifact_reference(
        self, run_id: str, artifact_id: str
    ) -> LookupResult[tuple[ArtifactReference, bool]]: ...

    def has_pending_artifacts(self, run_id: str) -> bool: ...

    def add_prefect_execution(
        self, run_id: str, link: PrefectExecutionLink, *, allocate_attempt: bool = False
    ) -> PrefectExecutionLink: ...

    def observe_prefect_execution(
        self, run_id: str, flow_run_id: str, *, state: str | None, observed_at: datetime
    ) -> None: ...

    def claim_execution(
        self,
        run_id: str,
        flow_run_id: str,
        *,
        worker_id: str,
        claimed_at: datetime,
        admission_deadline_at: datetime,
    ) -> bool: ...

    def mark_execution_stalled(self, run_id: str, flow_run_id: str, *, stalled_at: datetime) -> bool: ...

    def abandon_execution(self, run_id: str, flow_run_id: str, *, terminal_at: datetime) -> bool: ...

    def interrupt_execution(self, run_id: str, flow_run_id: str, *, terminal_at: datetime) -> bool: ...

    def commit_claimed_execution(
        self,
        run_id: str,
        flow_run_id: str,
        *,
        worker_id: str,
        terminal_at: datetime,
        terminal_state: Literal["completed", "failed"],
        terminal_outcome: Literal["succeeded", "failed"],
        writeback: ExecutionWriteback,
    ) -> bool: ...

    def request_execution_cancellation(
        self,
        run_id: str,
        flow_run_id: str,
        *,
        requested_at: datetime,
        recovery_deadline_at: datetime,
        recovery_seconds: float,
        expected_latest_position: int,
        receipt_id: str,
    ) -> bool: ...

    def acknowledge_execution_cancellation(
        self,
        run_id: str,
        flow_run_id: str,
        *,
        acknowledged_at: datetime,
        response_status: int,
        response_body: Mapping[str, Any],
    ) -> bool: ...

    def cancel_execution(self, run_id: str, flow_run_id: str, *, terminal_at: datetime) -> bool: ...

    def expire_execution_cancellation(self, run_id: str, flow_run_id: str, *, terminal_at: datetime) -> bool: ...

    def pending_executions(self) -> tuple[tuple[str, PrefectExecutionLink], ...]: ...

    def reserve_mutation(
        self, receipt: MutationReceipt, run: ProductRun | None, *, admit_write: bool = False
    ) -> tuple[MutationReceipt, bool]: ...

    def lookup_mutation(self, actor: str, key_digest: str) -> LookupResult[MutationReceipt]: ...

    def complete_mutation(
        self,
        receipt_id: str,
        *,
        response_status: int,
        response_body: Mapping[str, Any],
        flow_run_id: str | None,
        updated_at: datetime,
    ) -> MutationReceipt: ...

    def claim_mutation(self, receipt_id: str, *, updated_at: datetime) -> bool: ...

    def release_mutation(self, receipt_id: str, *, updated_at: datetime) -> None: ...

    def record_audit(self, event: AuditEvent) -> None: ...

    def audit_events(self, run_id: str | None = None) -> tuple[AuditEvent, ...]: ...

    def configuration_exists(self, config_id: str) -> bool: ...

    def create_configuration(self, package: ConfigurationPackage) -> ConfigurationVersion: ...

    def add_configuration_version(
        self, config_id: str, package: ConfigurationPackage
    ) -> tuple[ConfigurationVersion, bool]: ...

    def lookup_configuration(self, config_id: str) -> LookupResult[ConfigurationSummary]: ...

    def lookup_configuration_version(
        self, config_id: str, registry_version: int
    ) -> LookupResult[ConfigurationVersion]: ...

    def list_configurations(self) -> tuple[ConfigurationSummary, ...]: ...

    def list_configuration_versions(self, config_id: str) -> tuple[ConfigurationVersion, ...]: ...

    def record_results(self, run_id: str, results: Mapping[str, Any]) -> None: ...

    def merge_results(self, run_id: str, results: Mapping[str, Any]) -> None: ...

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


class _RelationalRunStore:  # pylint: disable=too-many-public-methods
    """Small SQL implementation shared by the SQLite and PostgreSQL profiles."""

    def __init__(
        self,
        connect: Callable[[], DBAPIConnection],
        *,
        placeholder: str,
        dialect: Literal["sqlite", "postgresql"],
        schema_conflict_codes: frozenset[str] = frozenset(),
    ) -> None:
        self._connect = connect
        self._placeholder = placeholder
        self._dialect = dialect
        self._schema_conflict_codes = schema_conflict_codes
        self._initialize()

    def _sql(self, statement: str) -> str:
        """Translate placeholders in module-owned SQL without string literals containing ``?``."""
        return statement.replace("?", self._placeholder)

    def _initialize(self) -> None:
        for attempt in range(_SCHEMA_INITIALIZATION_ATTEMPTS):
            connection = self._connect()
            try:
                cursor = connection.cursor()
                try:
                    for statement in _SCHEMA.split(";"):
                        if statement.strip():
                            cursor.execute(statement)
                    self._migrate_product_runs_columns(cursor)
                    self._migrate_prefect_execution_columns(cursor)
                    self._migrate_mutation_receipts(cursor)
                    self._ensure_mutation_receipt_resource_constraint(cursor)
                    self._ensure_configuration_binding_constraint(cursor)
                    connection.commit()
                except Exception as exc:  # pylint: disable=broad-exception-caught
                    # Synchronous DB-API drivers do not share a common error base; retry only
                    # documented PostgreSQL duplicate-catalog SQLSTATEs and duplicate-column
                    # races from a concurrent ALTER TABLE ADD COLUMN on either backend.
                    connection.rollback()
                    if _sqlstate(exc) not in self._schema_conflict_codes and not _is_duplicate_column_error(exc):
                        raise
                    if attempt + 1 == _SCHEMA_INITIALIZATION_ATTEMPTS:
                        raise
                else:
                    return
                finally:
                    cursor.close()
            finally:
                connection.close()

    def _read_product_run_columns(self, cursor: _Cursor) -> frozenset[str]:
        """Return the existing columns on ``product_runs`` for this store's own dialect.

        The statement is selected from ``self._dialect``, never discovered by running a
        statement to see whether it fails: on PostgreSQL, a probe-driven failure would abort
        the surrounding transaction and force a ``rollback()`` that discards every other
        uncommitted ``CREATE TABLE`` this same call still needs to commit.
        """
        if self._dialect == "sqlite":
            cursor.execute("PRAGMA table_info(product_runs)")
            return frozenset(str(row[1]) for row in cursor.fetchall())
        cursor.execute(
            self._sql(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = ? AND table_schema = current_schema()"
            ),
            ("product_runs",),
        )
        return frozenset(str(row[0]) for row in cursor.fetchall())

    def _migrate_product_runs_columns(self, cursor: _Cursor) -> None:
        """Additively bring a pre-existing ``product_runs`` table up to the current column set."""
        existing = self._read_product_run_columns(cursor)
        for column, sql_type in _CONFIGURATION_BINDING_COLUMNS:
            if column not in existing:
                cursor.execute(f"ALTER TABLE product_runs ADD COLUMN {column} {sql_type}")

    def _migrate_prefect_execution_columns(self, cursor: _Cursor) -> None:
        """Add liveness columns without fabricating timestamps for legacy links."""
        if self._dialect == "sqlite":
            cursor.execute("PRAGMA table_info(prefect_executions)")
            existing = frozenset(str(row[1]) for row in cursor.fetchall())
        else:
            cursor.execute(
                self._sql(
                    "SELECT column_name FROM information_schema.columns WHERE table_name = ? "
                    "AND table_schema = current_schema()"
                ),
                ("prefect_executions",),
            )
            existing = frozenset(str(row[0]) for row in cursor.fetchall())
        for column, sql_type in _PREFECT_EXECUTION_LIVENESS_COLUMNS:
            if column not in existing:
                cursor.execute(f"ALTER TABLE prefect_executions ADD COLUMN {column} {sql_type}")

    def _migrate_mutation_receipts(self, cursor: _Cursor) -> None:
        """Add resource identity columns and preserve every legacy receipt in place."""
        column_nullability: dict[str, str] = {}
        if self._dialect == "sqlite":
            cursor.execute("PRAGMA table_info(mutation_receipts)")
            columns = frozenset(str(row[1]) for row in cursor.fetchall())
        else:
            cursor.execute(
                self._sql(
                    "SELECT column_name, is_nullable FROM information_schema.columns "
                    "WHERE table_name = ? AND table_schema = current_schema()"
                ),
                ("mutation_receipts",),
            )
            column_nullability = _catalog_nullability(cursor.fetchall())
            columns = frozenset(column_nullability)
        if "resource_kind" not in columns:
            cursor.execute("ALTER TABLE mutation_receipts ADD COLUMN resource_kind TEXT")
        if "resource_id" not in columns:
            cursor.execute("ALTER TABLE mutation_receipts ADD COLUMN resource_id TEXT")
        cursor.execute(
            "UPDATE mutation_receipts SET resource_kind = 'run', resource_id = run_id "
            "WHERE resource_kind IS NULL OR resource_id IS NULL"
        )
        if self._dialect == "sqlite":
            # SQLite has no supported additive ALTER that removes NOT NULL. The table has a
            # fixed, repository-owned legacy shape and a rebuild would also reconstruct the
            # dependent write_admissions foreign key. Updating these two declarations is the
            # only in-place migration that preserves rows, indexes, and references.
            cursor.execute("PRAGMA writable_schema = ON")
            cursor.execute(
                "UPDATE sqlite_master SET sql = REPLACE(REPLACE(sql, 'run_id TEXT NOT NULL', 'run_id TEXT'), "
                "'prefect_key TEXT NOT NULL', 'prefect_key TEXT') WHERE type = 'table' AND name = 'mutation_receipts'"
            )
            cursor.execute("PRAGMA writable_schema = OFF")
        else:
            if column_nullability.get("run_id") == "NO":
                cursor.execute("ALTER TABLE mutation_receipts ALTER COLUMN run_id DROP NOT NULL")
            if column_nullability.get("prefect_key") == "NO":
                cursor.execute("ALTER TABLE mutation_receipts ALTER COLUMN prefect_key DROP NOT NULL")

    def _mutation_receipt_resource_constraint_exists(self, cursor: _Cursor) -> bool:
        if self._dialect == "sqlite":
            cursor.execute(
                self._sql("SELECT name FROM sqlite_master WHERE type = 'trigger' AND name IN (?, ?)"),
                _SQLITE_MUTATION_RECEIPT_RESOURCE_TRIGGER_NAMES,
            )
            return {str(row[0]) for row in cursor.fetchall()} >= set(_SQLITE_MUTATION_RECEIPT_RESOURCE_TRIGGER_NAMES)
        cursor.execute(
            self._sql(
                "SELECT 1 FROM information_schema.table_constraints "
                "WHERE table_name = ? AND constraint_name = ? AND table_schema = current_schema()"
            ),
            ("mutation_receipts", _MUTATION_RECEIPT_RESOURCE_CONSTRAINT),
        )
        return cursor.fetchone() is not None

    def _ensure_mutation_receipt_resource_constraint(self, cursor: _Cursor) -> None:
        """Give fresh and legacy receipts the same database-enforced resource shape."""
        if self._mutation_receipt_resource_constraint_exists(cursor):
            return
        if self._dialect == "sqlite":
            cursor.execute(_SQLITE_MUTATION_RECEIPT_RESOURCE_INSERT_TRIGGER)
            cursor.execute(_SQLITE_MUTATION_RECEIPT_RESOURCE_UPDATE_TRIGGER)
        else:
            cursor.execute(
                f"ALTER TABLE mutation_receipts ADD CONSTRAINT {_MUTATION_RECEIPT_RESOURCE_CONSTRAINT} "
                f"CHECK ({_MUTATION_RECEIPT_RESOURCE_CHECK})"
            )

    def _configuration_binding_constraint_exists(self, cursor: _Cursor) -> bool:
        """Check for the binding safeguard by name instead of attempting and hoping.

        PostgreSQL has no ``ADD CONSTRAINT IF NOT EXISTS``, and a retry loop that tolerates
        the resulting ``42710`` only buys another attempt, not success: it re-raises once
        attempts run out, so a repeated construction over an already-migrated database would
        still fail without this check.

        SQLite's enforcement is a *pair* of triggers (``BEFORE INSERT`` and ``BEFORE UPDATE``):
        both names must be present for the constraint to count as existing. Reporting existence
        from only one would let the other stay missing forever once construction is repeated,
        since a later construction would see the survivor and skip recreating the pair.

        The PostgreSQL query is schema-qualified for the same reason ``_read_product_run_columns``
        is: without ``table_schema = current_schema()``, a same-named constraint in another schema
        would satisfy this check and leave the current schema's table unguarded.
        """
        if self._dialect == "sqlite":
            cursor.execute(
                self._sql("SELECT name FROM sqlite_master WHERE type = 'trigger' AND name IN (?, ?)"),
                _SQLITE_CONFIGURATION_BINDING_TRIGGER_NAMES,
            )
            found = {str(row[0]) for row in cursor.fetchall()}
            return found >= set(_SQLITE_CONFIGURATION_BINDING_TRIGGER_NAMES)
        cursor.execute(
            self._sql(
                "SELECT 1 FROM information_schema.table_constraints "
                "WHERE table_name = ? AND constraint_name = ? AND table_schema = current_schema()"
            ),
            ("product_runs", _CONFIGURATION_BINDING_CONSTRAINT),
        )
        return cursor.fetchone() is not None

    def _ensure_configuration_binding_constraint(self, cursor: _Cursor) -> None:
        """Refuse a partially bound run row before any writer exists to produce one.

        SQLite's enforcement is a pair of ``BEFORE INSERT``/``BEFORE UPDATE`` triggers;
        PostgreSQL's is a genuine multi-column ``CHECK`` constraint. Both are created only
        after ``_configuration_binding_constraint_exists`` reports they are still missing, so
        repeated construction over the same database is idempotent on both backends without
        depending on either an already-exists error or the surrounding retry loop.
        """
        if self._configuration_binding_constraint_exists(cursor):
            return
        if self._dialect == "sqlite":
            cursor.execute(_SQLITE_CONFIGURATION_BINDING_INSERT_TRIGGER)
            cursor.execute(_SQLITE_CONFIGURATION_BINDING_UPDATE_TRIGGER)
        else:
            cursor.execute(
                f"ALTER TABLE product_runs ADD CONSTRAINT {_CONFIGURATION_BINDING_CONSTRAINT} "
                f"CHECK ({_CONFIGURATION_BINDING_CHECK_EXPRESSION})"
            )

    def create(self, run: ProductRun) -> None:
        connection = self._connect()
        try:
            cursor = connection.cursor()
            try:
                try:
                    self._insert_product_run_row(cursor, run)
                except Exception as exc:  # pylint: disable=broad-exception-caught
                    # Synchronous DB-API drivers do not share an integrity-error
                    # base class; inspect uniqueness only for the stable run ID.
                    if _is_unique_violation(exc):
                        msg = f"Sync run ID {run.run_id!r} already exists"
                        raise DuplicateRunError(msg) from exc
                    raise
                for position, link in enumerate(run.prefect_executions):
                    self._insert_prefect_execution(cursor, run.run_id, link, position)
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                cursor.close()
        finally:
            connection.close()

    def _insert_product_run(self, cursor: _Cursor, run: ProductRun) -> None:
        self._insert_product_run_row(cursor, run)
        for position, link in enumerate(run.prefect_executions):
            self._insert_prefect_execution(cursor, run.run_id, link, position)

    def _insert_product_run_row(self, cursor: _Cursor, run: ProductRun) -> None:
        cursor.execute(
            self._sql(_INSERT_PRODUCT_RUN),
            (
                run.run_id,
                run.operation,
                run.configuration_reference,
                run.config_id,
                run.registry_version,
                run.package_checksum,
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

    def lookup(self, run_id: str) -> LookupResult[ProductRun]:
        connection = self._connect()
        try:
            cursor = connection.cursor()
            try:
                cursor.execute(
                    self._sql(_SELECT_PRODUCT_RUN),
                    (run_id,),
                )
                row = cursor.fetchone()
                if row is None:
                    return LookupResult(value=None, reason="run-not-found")
                cursor.execute(
                    self._sql(_SELECT_RUN_ARTIFACT_REFERENCES),
                    (run_id,),
                )
                references = cursor.fetchall()
                cursor.execute(
                    self._sql(_SELECT_PREFECT_EXECUTIONS),
                    (run_id,),
                )
                links = cursor.fetchall()
                cursor.execute(self._sql(_SELECT_AUDIT_LINKS), (run_id,))
                audit_links = cursor.fetchall()
            finally:
                cursor.close()
        finally:
            connection.close()
        return LookupResult(value=_run_from_rows(row, references, links, audit_links))

    def exists(self, run_id: str) -> bool:
        """Return run existence without hydrating child records."""
        connection = self._connect()
        try:
            cursor = connection.cursor()
            try:
                cursor.execute(self._sql("SELECT 1 FROM product_runs WHERE run_id = ?"), (run_id,))
                return cursor.fetchone() is not None
            finally:
                cursor.close()
        finally:
            connection.close()

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
                    self._sql(_SELECT_ARTIFACT_REFERENCE),
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

    def add_prefect_execution(
        self,
        run_id: str,
        link: PrefectExecutionLink,
        *,
        allocate_attempt: bool = False,
    ) -> PrefectExecutionLink:
        last_conflict: BaseException | None = None
        for attempt in range(_PREFECT_POSITION_ATTEMPTS):
            connection = self._connect()
            try:
                cursor = connection.cursor()
                try:
                    cursor.execute(
                        self._sql("UPDATE product_runs SET run_id = run_id WHERE run_id = ?"),
                        (run_id,),
                    )
                    cursor.execute(
                        self._sql(
                            "SELECT 1 FROM prefect_executions WHERE run_id = ? AND terminal_at IS NULL "
                            "AND cancellation_requested_at IS NOT NULL"
                        ),
                        (run_id,),
                    )
                    if cursor.fetchone() is not None:
                        _raise_active_cancellation_conflict(run_id)
                    cursor.execute(
                        self._sql("SELECT COALESCE(MAX(position) + 1, 0) FROM prefect_executions WHERE run_id = ?"),
                        (run_id,),
                    )
                    position_row = cursor.fetchone()
                    position = int(position_row[0]) if position_row is not None else 0
                    allocated_link = link
                    if allocate_attempt:
                        cursor.execute(
                            self._sql(
                                "SELECT COALESCE(MAX(attempt) + 1, 1) FROM prefect_executions "
                                "WHERE run_id = ? AND purpose = ?"
                            ),
                            (run_id, link.purpose),
                        )
                        attempt_row = cursor.fetchone()
                        ordinal = int(attempt_row[0]) if attempt_row is not None else 1
                        allocated_link = link.model_copy(update={"attempt": ordinal})
                    self._insert_prefect_execution(cursor, run_id, allocated_link, position)
                    connection.commit()
                except Exception as exc:  # pylint: disable=broad-exception-caught
                    # DB-API drivers do not share an integrity-error base class;
                    # inspect only documented SQLite/PostgreSQL uniqueness markers.
                    connection.rollback()
                    if not _is_unique_violation(exc):
                        raise
                    last_conflict = exc
                else:
                    return allocated_link
                finally:
                    cursor.close()
            finally:
                connection.close()

            if self._prefect_execution_exists(run_id, link.flow_run_id):
                msg = f"Prefect flow-run ID {link.flow_run_id!r} is already linked to Sync run {run_id!r}"
                raise DuplicatePrefectExecutionError(msg) from last_conflict
            if attempt + 1 == _PREFECT_POSITION_ATTEMPTS:
                msg = f"Could not allocate a Prefect execution position for Sync run {run_id!r}"
                raise RuntimeError(msg) from last_conflict
        msg = "Prefect execution allocation loop exited unexpectedly"
        raise AssertionError(msg)

    def _insert_prefect_execution(
        self, cursor: _Cursor, run_id: str, link: PrefectExecutionLink, position: int
    ) -> None:
        cursor.execute(
            self._sql(_INSERT_PREFECT_EXECUTION),
            (
                run_id,
                link.flow_run_id,
                link.deployment_id,
                link.purpose,
                link.attempt,
                link.last_observed_state,
                _iso(link.last_observed_at),
                _iso(link.submitted_at),
                _iso(link.claimed_at),
                link.claiming_worker_id,
                _iso(link.stalled_at),
                _iso(link.cancellation_requested_at),
                _iso(link.cancellation_recovery_deadline_at),
                link.cancellation_receipt_id,
                _iso(link.cancellation_acknowledged_at),
                _iso(link.terminal_at),
                link.terminal_state,
                link.terminal_outcome,
                position,
            ),
        )

    def _prefect_execution_exists(self, run_id: str, flow_run_id: str) -> bool:
        connection = self._connect()
        try:
            cursor = connection.cursor()
            try:
                cursor.execute(
                    self._sql("SELECT 1 FROM prefect_executions WHERE run_id = ? AND flow_run_id = ?"),
                    (run_id, flow_run_id),
                )
                return cursor.fetchone() is not None
            finally:
                cursor.close()
        finally:
            connection.close()

    def observe_prefect_execution(
        self, run_id: str, flow_run_id: str, *, state: str | None, observed_at: datetime
    ) -> None:
        connection = self._connect()
        try:
            cursor = connection.cursor()
            try:
                cursor.execute(
                    self._sql(
                        "UPDATE prefect_executions SET last_observed_state = ?, last_observed_at = ? "
                        "WHERE run_id = ? AND flow_run_id = ?"
                    ),
                    (state, observed_at.isoformat(), run_id, flow_run_id),
                )
                _require_execution_observed(cursor, run_id, flow_run_id)
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                cursor.close()
        finally:
            connection.close()

    def claim_execution(
        self,
        run_id: str,
        flow_run_id: str,
        *,
        worker_id: str,
        claimed_at: datetime,
        admission_deadline_at: datetime,
    ) -> bool:
        """Atomically assign an unclaimed, non-terminal execution to one worker."""
        statement = (
            "UPDATE prefect_executions SET claimed_at = ?, claiming_worker_id = ? "
            "WHERE run_id = ? AND flow_run_id = ? AND claimed_at IS NULL AND terminal_at IS NULL "
            "AND cancellation_requested_at IS NULL "
            "AND (COALESCE(submitted_at, last_observed_at) IS NULL OR "
            "infrahub_sync_execution_timestamp_microseconds(COALESCE(submitted_at, last_observed_at)) "
            "> infrahub_sync_execution_timestamp_microseconds(?))"
            if self._dialect == "sqlite"
            else "UPDATE prefect_executions SET claimed_at = ?, claiming_worker_id = ? "
            "WHERE run_id = ? AND flow_run_id = ? AND claimed_at IS NULL AND terminal_at IS NULL "
            "AND cancellation_requested_at IS NULL "
            "AND (COALESCE(submitted_at, last_observed_at) IS NULL OR "
            "CAST(COALESCE(submitted_at, last_observed_at) AS TIMESTAMPTZ) > CAST(? AS TIMESTAMPTZ))"
        )
        return self._execution_update(
            statement,
            (claimed_at.isoformat(), worker_id, run_id, flow_run_id, admission_deadline_at.isoformat()),
        )

    def mark_execution_stalled(self, run_id: str, flow_run_id: str, *, stalled_at: datetime) -> bool:
        """Record the first informational stall without changing claim eligibility."""
        return self._execution_update(
            "UPDATE prefect_executions SET stalled_at = ? WHERE run_id = ? AND flow_run_id = ? "
            "AND stalled_at IS NULL AND claimed_at IS NULL AND terminal_at IS NULL",
            (stalled_at.isoformat(), run_id, flow_run_id),
        )

    def abandon_execution(self, run_id: str, flow_run_id: str, *, terminal_at: datetime) -> bool:
        """Atomically terminalize an unclaimed execution and its owning product run."""
        return self._terminalize_execution(
            run_id,
            flow_run_id,
            terminal_at=terminal_at,
            claimed=False,
            terminal_state="abandoned",
            terminal_outcome="abandoned",
            phase="abandoned",
            outcome="abandoned",
        )

    def interrupt_execution(self, run_id: str, flow_run_id: str, *, terminal_at: datetime) -> bool:
        """Atomically terminalize an orphaned claimed execution and its product run."""
        return self._terminalize_execution(
            run_id,
            flow_run_id,
            terminal_at=terminal_at,
            claimed=True,
            terminal_state="interrupted",
            terminal_outcome="ambiguous",
            phase="interrupted",
            outcome="ambiguous",
        )

    def commit_claimed_execution(
        self,
        run_id: str,
        flow_run_id: str,
        *,
        worker_id: str,
        terminal_at: datetime,
        terminal_state: Literal["completed", "failed"],
        terminal_outcome: Literal["succeeded", "failed"],
        writeback: ExecutionWriteback,
    ) -> bool:
        """Commit one claimed worker verdict and its business writeback together."""
        connection = self._connect()
        try:
            cursor = connection.cursor()
            try:
                cursor.execute(
                    self._sql(
                        "UPDATE prefect_executions SET terminal_at = ?, terminal_state = ?, terminal_outcome = ? "
                        "WHERE run_id = ? AND flow_run_id = ? AND claimed_at IS NOT NULL "
                        "AND claiming_worker_id = ? AND terminal_at IS NULL"
                    ),
                    (
                        terminal_at.isoformat(),
                        terminal_state,
                        terminal_outcome,
                        run_id,
                        flow_run_id,
                        worker_id,
                    ),
                )
                if cursor.rowcount != 1:
                    connection.commit()
                    return False
                if isinstance(writeback, ExecutionFinishWriteback):
                    cursor.execute(
                        self._sql(_UPDATE_LATEST_EXECUTION_FINISH),
                        (
                            writeback.phase,
                            writeback.outcome,
                            writeback.finished_at.isoformat(),
                            _json(writeback.summary),
                            _json(writeback.results),
                            run_id,
                            flow_run_id,
                        ),
                    )
                else:
                    cursor.execute(
                        self._sql(_SELECT_LATEST_EXECUTION_RESULTS),
                        (run_id, flow_run_id),
                    )
                    row = cursor.fetchone()
                    if row is not None:
                        current = _JSON_MAPPING_ADAPTER.validate_json(str(row[0]))
                        cursor.execute(
                            self._sql(_UPDATE_LATEST_EXECUTION_RESULTS),
                            (_json({**current, **writeback.results}), run_id, flow_run_id),
                        )
                cursor.execute(
                    self._sql(
                        "SELECT cancellation_receipt_id FROM prefect_executions WHERE run_id = ? AND flow_run_id = ? "
                        "AND cancellation_requested_at IS NOT NULL AND cancellation_acknowledged_at IS NULL"
                    ),
                    (run_id, flow_run_id),
                )
                cancellation = cursor.fetchone()
                if cancellation is not None:
                    receipt_id = str(cancellation[0])
                    self._complete_receipt_row(
                        cursor,
                        receipt_id,
                        response_status=409,
                        response_body=_cancellation_terminal_response(run_id, receipt_id),
                        flow_run_id=flow_run_id,
                        updated_at=terminal_at,
                    )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            else:
                return True
            finally:
                cursor.close()
        finally:
            connection.close()

    def request_execution_cancellation(
        self,
        run_id: str,
        flow_run_id: str,
        *,
        requested_at: datetime,
        recovery_deadline_at: datetime,
        recovery_seconds: float,
        expected_latest_position: int,
        receipt_id: str,
    ) -> bool:
        """Persist first cancellation intent on one link for one claimed receipt."""
        if not isfinite(recovery_seconds) or recovery_seconds <= 0:
            raise ValueError(_CANCELLATION_DEADLINE_ERROR)
        if recovery_deadline_at != requested_at + timedelta(seconds=recovery_seconds):
            raise ValueError(_CANCELLATION_DEADLINE_ERROR)
        connection = self._connect()
        try:
            cursor = connection.cursor()
            try:
                cursor.execute(self._sql("UPDATE product_runs SET run_id = run_id WHERE run_id = ?"), (run_id,))
                cursor.execute(
                    self._sql(
                        "UPDATE prefect_executions SET cancellation_requested_at = ?, "
                        "cancellation_recovery_deadline_at = ?, cancellation_receipt_id = ? "
                        "WHERE run_id = ? AND flow_run_id = ? AND terminal_at IS NULL "
                        "AND (SELECT COALESCE(MAX(position), -1) FROM prefect_executions WHERE run_id = ?) = ? "
                        "AND ((cancellation_requested_at IS NULL AND NOT EXISTS (SELECT 1 FROM prefect_executions "
                        "WHERE run_id = ? AND terminal_at IS NULL AND cancellation_requested_at IS NOT NULL)) "
                        "OR (cancellation_requested_at = ? AND cancellation_recovery_deadline_at = ? "
                        "AND cancellation_receipt_id = ?)) AND EXISTS (SELECT 1 FROM mutation_receipts "
                        "WHERE receipt_id = ? AND run_id = ? AND operation = 'cancel' AND state = 'processing')"
                    ),
                    (
                        requested_at.isoformat(),
                        recovery_deadline_at.isoformat(),
                        receipt_id,
                        run_id,
                        flow_run_id,
                        run_id,
                        expected_latest_position,
                        run_id,
                        requested_at.isoformat(),
                        recovery_deadline_at.isoformat(),
                        receipt_id,
                        receipt_id,
                        run_id,
                    ),
                )
                updated = cursor.rowcount == 1
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                cursor.close()
        finally:
            connection.close()
        return updated

    def acknowledge_execution_cancellation(
        self,
        run_id: str,
        flow_run_id: str,
        *,
        acknowledged_at: datetime,
        response_status: int,
        response_body: Mapping[str, Any],
    ) -> bool:
        """Persist remote acknowledgement and the replay result in one transaction."""
        connection = self._connect()
        try:
            cursor = connection.cursor()
            try:
                cursor.execute(
                    self._sql(
                        "UPDATE prefect_executions SET flow_run_id = flow_run_id "
                        "WHERE run_id = ? AND flow_run_id = ? AND terminal_at IS NULL "
                        "AND cancellation_requested_at IS NOT NULL"
                    ),
                    (run_id, flow_run_id),
                )
                if cursor.rowcount != 1:
                    connection.commit()
                    return False
                cursor.execute(
                    self._sql(
                        "SELECT claimed_at, cancellation_recovery_deadline_at, cancellation_receipt_id, "
                        "cancellation_acknowledged_at FROM prefect_executions WHERE run_id = ? AND flow_run_id = ?"
                    ),
                    (run_id, flow_run_id),
                )
                row = _require_cancellation_receipt_row(cursor.fetchone())
                if row[3] is not None:
                    connection.commit()
                    return False
                receipt_id = str(row[2])
                if acknowledged_at >= datetime.fromisoformat(str(row[1])):
                    state, outcome, phase = (
                        ("interrupted", "ambiguous", "interrupted")
                        if row[0] is not None
                        else ("abandoned", "abandoned", "abandoned")
                    )
                    cursor.execute(
                        self._sql(
                            "UPDATE prefect_executions SET terminal_at = ?, terminal_state = ?, terminal_outcome = ? "
                            "WHERE run_id = ? AND flow_run_id = ? AND terminal_at IS NULL "
                            "AND cancellation_acknowledged_at IS NULL"
                        ),
                        (acknowledged_at.isoformat(), state, outcome, run_id, flow_run_id),
                    )
                    if cursor.rowcount != 1:
                        connection.commit()
                        return False
                    cursor.execute(
                        self._sql(_UPDATE_LATEST_EXECUTION_TERMINAL),
                        (phase, outcome, acknowledged_at.isoformat(), run_id, flow_run_id),
                    )
                    self._complete_receipt_row(
                        cursor,
                        receipt_id,
                        response_status=503,
                        response_body=_cancellation_unconfirmed_response(run_id, receipt_id),
                        flow_run_id=flow_run_id,
                        updated_at=acknowledged_at,
                    )
                    connection.commit()
                    return False
                cursor.execute(
                    self._sql(
                        "UPDATE prefect_executions SET cancellation_acknowledged_at = ? "
                        "WHERE run_id = ? AND flow_run_id = ? AND terminal_at IS NULL "
                        "AND cancellation_acknowledged_at IS NULL"
                    ),
                    (acknowledged_at.isoformat(), run_id, flow_run_id),
                )
                if cursor.rowcount != 1:
                    connection.commit()
                    return False
                self._complete_receipt_row(
                    cursor,
                    receipt_id,
                    response_status=response_status,
                    response_body=response_body,
                    flow_run_id=flow_run_id,
                    updated_at=acknowledged_at,
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            else:
                return True
            finally:
                cursor.close()
        finally:
            connection.close()

    def cancel_execution(self, run_id: str, flow_run_id: str, *, terminal_at: datetime) -> bool:
        """Commit clean cancellation only from acknowledged durable cancelled observation."""
        connection = self._connect()
        try:
            cursor = connection.cursor()
            try:
                cursor.execute(
                    self._sql(
                        "UPDATE prefect_executions SET terminal_at = ?, terminal_state = 'cancelled', "
                        "terminal_outcome = 'cancelled' WHERE run_id = ? AND flow_run_id = ? "
                        "AND terminal_at IS NULL AND cancellation_acknowledged_at IS NOT NULL "
                        "AND last_observed_state = 'cancelled'"
                    ),
                    (terminal_at.isoformat(), run_id, flow_run_id),
                )
                if cursor.rowcount != 1:
                    connection.commit()
                    return False
                cursor.execute(
                    self._sql(_UPDATE_LATEST_EXECUTION_CANCELLED),
                    (terminal_at.isoformat(), run_id, flow_run_id),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            else:
                return True
            finally:
                cursor.close()
        finally:
            connection.close()

    def expire_execution_cancellation(self, run_id: str, flow_run_id: str, *, terminal_at: datetime) -> bool:
        """Expire bounded cancellation recovery under one serialized row decision."""
        connection = self._connect()
        try:
            cursor = connection.cursor()
            try:
                cursor.execute(
                    self._sql(
                        "UPDATE prefect_executions SET flow_run_id = flow_run_id WHERE run_id = ? AND flow_run_id = ? "
                        "AND terminal_at IS NULL AND cancellation_requested_at IS NOT NULL"
                    ),
                    (run_id, flow_run_id),
                )
                if cursor.rowcount != 1:
                    connection.commit()
                    return False
                cursor.execute(
                    self._sql(
                        "SELECT claimed_at, cancellation_recovery_deadline_at, cancellation_receipt_id, "
                        "cancellation_acknowledged_at, last_observed_state FROM prefect_executions "
                        "WHERE run_id = ? AND flow_run_id = ?"
                    ),
                    (run_id, flow_run_id),
                )
                row = cursor.fetchone()
                if row is None or datetime.fromisoformat(str(row[1])) > terminal_at:
                    connection.commit()
                    return False
                acknowledged = row[3] is not None
                observed_state = None if row[4] is None else str(row[4])
                if acknowledged and observed_state == "cancelled":
                    connection.commit()
                    return False
                claimed = row[0] is not None
                state, outcome, phase = (
                    ("interrupted", "ambiguous", "interrupted") if claimed else ("abandoned", "abandoned", "abandoned")
                )
                cursor.execute(
                    self._sql(
                        "UPDATE prefect_executions SET terminal_at = ?, terminal_state = ?, terminal_outcome = ? "
                        "WHERE run_id = ? AND flow_run_id = ? AND terminal_at IS NULL"
                    ),
                    (terminal_at.isoformat(), state, outcome, run_id, flow_run_id),
                )
                if cursor.rowcount != 1:
                    connection.commit()
                    return False
                cursor.execute(
                    self._sql(_UPDATE_LATEST_EXECUTION_TERMINAL),
                    (phase, outcome, terminal_at.isoformat(), run_id, flow_run_id),
                )
                if not acknowledged:
                    receipt_id = str(row[2])
                    self._complete_receipt_row(
                        cursor,
                        receipt_id,
                        response_status=503,
                        response_body=_cancellation_unconfirmed_response(run_id, receipt_id),
                        flow_run_id=flow_run_id,
                        updated_at=terminal_at,
                    )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            else:
                return True
            finally:
                cursor.close()
        finally:
            connection.close()

    def pending_executions(self) -> tuple[tuple[str, PrefectExecutionLink], ...]:
        """Return durable non-terminal execution links and their owning run IDs."""
        connection = self._connect()
        try:
            cursor = connection.cursor()
            try:
                cursor.execute(self._sql("SELECT DISTINCT run_id FROM prefect_executions WHERE terminal_at IS NULL"))
                run_ids = tuple(str(row[0]) for row in cursor.fetchall())
            finally:
                cursor.close()
        finally:
            connection.close()
        pending: list[tuple[str, PrefectExecutionLink]] = []
        for run_id in run_ids:
            run = self.lookup(run_id).value
            if run is not None:
                pending.extend((run_id, link) for link in run.prefect_executions if link.terminal_at is None)
        return tuple(pending)

    def _execution_update(self, statement: str, values: tuple[Any, ...]) -> bool:
        connection = self._connect()
        try:
            cursor = connection.cursor()
            try:
                cursor.execute(self._sql(statement), values)
                updated = cursor.rowcount == 1
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            else:
                return updated
            finally:
                cursor.close()
        finally:
            connection.close()

    def _terminalize_execution(
        self,
        run_id: str,
        flow_run_id: str,
        *,
        terminal_at: datetime,
        claimed: bool,
        terminal_state: str,
        terminal_outcome: str,
        phase: str,
        outcome: str,
    ) -> bool:
        connection = self._connect()
        try:
            cursor = connection.cursor()
            try:
                statement = (
                    "UPDATE prefect_executions SET terminal_at = ?, terminal_state = ?, terminal_outcome = ? "
                    "WHERE run_id = ? AND flow_run_id = ? AND claimed_at IS NOT NULL AND terminal_at IS NULL "
                    "AND cancellation_requested_at IS NULL"
                    if claimed
                    else "UPDATE prefect_executions SET terminal_at = ?, terminal_state = ?, terminal_outcome = ? "
                    "WHERE run_id = ? AND flow_run_id = ? AND claimed_at IS NULL AND terminal_at IS NULL "
                    "AND cancellation_requested_at IS NULL"
                )
                cursor.execute(
                    self._sql(statement),
                    (terminal_at.isoformat(), terminal_state, terminal_outcome, run_id, flow_run_id),
                )
                if cursor.rowcount != 1:
                    connection.commit()
                    return False
                cursor.execute(
                    self._sql(_UPDATE_LATEST_EXECUTION_TERMINAL),
                    (phase, outcome, terminal_at.isoformat(), run_id, flow_run_id),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            else:
                return True
            finally:
                cursor.close()
        finally:
            connection.close()

    def reserve_mutation(
        self,
        receipt: MutationReceipt,
        run: ProductRun | None,
        *,
        admit_write: bool = False,
    ) -> tuple[MutationReceipt, bool]:
        """Atomically reserve actor/key and optionally create its product run."""
        connection = self._connect()
        try:
            cursor = connection.cursor()
            try:
                try:
                    cursor.execute(self._sql(_INSERT_MUTATION_RECEIPT), _receipt_values(receipt))
                    if run is not None:
                        self._insert_product_run(cursor, run)
                    if admit_write:
                        cursor.execute(
                            self._sql("INSERT INTO write_admissions (run_id, receipt_id, operation) VALUES (?, ?, ?)"),
                            (receipt.run_id, receipt.receipt_id, receipt.operation),
                        )
                    connection.commit()
                except Exception as exc:  # pylint: disable=broad-exception-caught
                    connection.rollback()
                    if not _is_unique_violation(exc):
                        raise
                    existing = self.lookup_mutation(receipt.actor, receipt.key_digest)
                    if existing.value is not None:
                        return existing.value, False
                    if admit_write:
                        assert receipt.run_id is not None
                        if self._write_admission_exists(receipt.run_id):
                            msg = f"Sync run {receipt.run_id!r} already has a write-capable admission"
                            raise WriteAdmissionConflictError(msg) from exc
                    if run is not None and self.exists(run.run_id):
                        msg = f"Sync run ID {run.run_id!r} already exists"
                        raise DuplicateRunError(msg) from exc
                    raise
            finally:
                cursor.close()
        finally:
            connection.close()
        return receipt, True

    def _write_admission_exists(self, run_id: str) -> bool:
        connection = self._connect()
        try:
            cursor = connection.cursor()
            try:
                cursor.execute(self._sql("SELECT 1 FROM write_admissions WHERE run_id = ?"), (run_id,))
                return cursor.fetchone() is not None
            finally:
                cursor.close()
        finally:
            connection.close()

    def lookup_mutation(self, actor: str, key_digest: str) -> LookupResult[MutationReceipt]:
        connection = self._connect()
        try:
            cursor = connection.cursor()
            try:
                cursor.execute(self._sql(_SELECT_MUTATION_RECEIPT), (actor, key_digest))
                row = cursor.fetchone()
            finally:
                cursor.close()
        finally:
            connection.close()
        if row is None:
            return LookupResult(value=None, reason="mutation-receipt-not-found")
        return LookupResult(value=_receipt_from_row(row))

    def complete_mutation(
        self,
        receipt_id: str,
        *,
        response_status: int,
        response_body: Mapping[str, Any],
        flow_run_id: str | None,
        updated_at: datetime,
    ) -> MutationReceipt:
        connection = self._connect()
        try:
            cursor = connection.cursor()
            try:
                cursor.execute(
                    self._sql(
                        "UPDATE mutation_receipts SET state = 'accepted', response_status = ?, response_body = ?, "
                        "flow_run_id = ?, updated_at = ? WHERE receipt_id = ? AND state IN ('reserved', 'processing')"
                    ),
                    (response_status, _json(response_body), flow_run_id, updated_at.isoformat(), receipt_id),
                )
                completed = cursor.rowcount == 1
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                cursor.close()
        finally:
            connection.close()
        receipt = self._lookup_mutation_by_id(receipt_id)
        if completed:
            return receipt
        if (
            receipt.state == "accepted"
            and receipt.response_status == response_status
            and receipt.flow_run_id == flow_run_id
        ):
            return receipt
        msg = f"Mutation receipt {receipt_id!r} is already complete with a different response"
        raise ValueError(msg)

    def _complete_receipt_row(
        self,
        cursor: _Cursor,
        receipt_id: str,
        *,
        response_status: int,
        response_body: Mapping[str, Any],
        flow_run_id: str,
        updated_at: datetime,
    ) -> None:
        """Complete a processing receipt inside its caller's open transaction."""
        cursor.execute(
            self._sql(
                "UPDATE mutation_receipts SET state = 'accepted', response_status = ?, response_body = ?, "
                "flow_run_id = ?, updated_at = ? WHERE receipt_id = ? AND state = 'processing'"
            ),
            (
                response_status,
                _json(response_body),
                flow_run_id,
                updated_at.isoformat(),
                receipt_id,
            ),
        )
        if cursor.rowcount != 1:
            msg = f"Mutation receipt {receipt_id!r} is unavailable for completion"
            raise ValueError(msg)

    def claim_mutation(self, receipt_id: str, *, updated_at: datetime) -> bool:
        """Claim one retryable receipt before its service call begins."""
        connection = self._connect()
        try:
            cursor = connection.cursor()
            try:
                cursor.execute(
                    self._sql(
                        "UPDATE mutation_receipts SET state = 'processing', updated_at = ? "
                        "WHERE receipt_id = ? AND state = 'reserved'"
                    ),
                    (updated_at.isoformat(), receipt_id),
                )
                claimed = cursor.rowcount == 1
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                cursor.close()
        finally:
            connection.close()
        return claimed

    def release_mutation(self, receipt_id: str, *, updated_at: datetime) -> None:
        """Return an unsuccessful in-flight receipt to retryable reservation state."""
        connection = self._connect()
        try:
            cursor = connection.cursor()
            try:
                cursor.execute(
                    self._sql(
                        "UPDATE mutation_receipts SET state = 'reserved', updated_at = ? "
                        "WHERE receipt_id = ? AND state = 'processing'"
                    ),
                    (updated_at.isoformat(), receipt_id),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                cursor.close()
        finally:
            connection.close()

    def _lookup_mutation_by_id(self, receipt_id: str) -> MutationReceipt:
        connection = self._connect()
        try:
            cursor = connection.cursor()
            try:
                cursor.execute(
                    self._sql(
                        "SELECT receipt_id, actor, key_digest, operation, target_run_id, request_fingerprint, "
                        "reason, resource_kind, resource_id, run_id, prefect_key, state, response_status, response_body, flow_run_id, "
                        "created_at, updated_at FROM mutation_receipts WHERE receipt_id = ?"
                    ),
                    (receipt_id,),
                )
                row = cursor.fetchone()
            finally:
                cursor.close()
        finally:
            connection.close()
        if row is None:
            msg = f"Mutation receipt {receipt_id!r} is unavailable"
            raise RunNotFoundError(msg)
        return _receipt_from_row(row)

    def record_audit(self, event: AuditEvent) -> None:
        connection = self._connect()
        try:
            cursor = connection.cursor()
            try:
                cursor.execute(
                    self._sql(_INSERT_AUDIT_EVENT),
                    (
                        event.event_id,
                        event.run_id,
                        event.actor,
                        event.operation,
                        event.reason,
                        event.outcome,
                        event.created_at.isoformat(),
                    ),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                cursor.close()
        finally:
            connection.close()

    def audit_events(self, run_id: str | None = None) -> tuple[AuditEvent, ...]:
        connection = self._connect()
        try:
            cursor = connection.cursor()
            try:
                if run_id is None:
                    cursor.execute(
                        "SELECT event_id, run_id, actor, operation, reason, outcome, created_at "
                        "FROM audit_events ORDER BY created_at, event_id"
                    )
                else:
                    cursor.execute(self._sql(_SELECT_AUDIT_EVENTS), (run_id,))
                rows = cursor.fetchall()
            finally:
                cursor.close()
        finally:
            connection.close()
        return tuple(_audit_from_row(row) for row in rows)

    def configuration_exists(self, config_id: str) -> bool:
        """Return configuration existence without hydrating its versions."""
        connection = self._connect()
        try:
            cursor = connection.cursor()
            try:
                cursor.execute(self._sql(_SELECT_CONFIGURATION), (config_id,))
                return cursor.fetchone() is not None
            finally:
                cursor.close()
        finally:
            connection.close()

    def create_configuration(self, package: ConfigurationPackage) -> ConfigurationVersion:
        """Durably register a brand-new configuration and its first version."""
        config_id = _generate_config_id()
        version = ConfigurationVersion(
            config_id=config_id,
            registry_version=1,
            package_checksum=package.checksum(),
            declared_content=package.declared_content(),
            created_at=datetime.now(timezone.utc),
        )
        connection = self._connect()
        try:
            cursor = connection.cursor()
            try:
                cursor.execute(self._sql(_INSERT_CONFIGURATION), (config_id, version.created_at.isoformat()))
                self._insert_configuration_version_row(cursor, version)
                connection.commit()
            except Exception as exc:  # pylint: disable=broad-exception-caught
                # DB-API drivers do not share an integrity-error base class;
                # inspect only documented SQLite/PostgreSQL uniqueness markers.
                connection.rollback()
                if _is_unique_violation(exc):
                    msg = f"Configuration ID {config_id!r} already exists"
                    raise DuplicateConfigurationError(msg) from exc
                raise
            finally:
                cursor.close()
        finally:
            connection.close()
        return version

    def _insert_configuration_version_row(self, cursor: _Cursor, version: ConfigurationVersion) -> None:
        _require_checksum_digests_content(version.package_checksum, version.declared_content)
        cursor.execute(
            self._sql(_INSERT_CONFIGURATION_VERSION),
            (
                version.config_id,
                version.registry_version,
                version.package_checksum,
                _json(version.declared_content),
                version.created_at.isoformat(),
            ),
        )

    def add_configuration_version(
        self, config_id: str, package: ConfigurationPackage
    ) -> tuple[ConfigurationVersion, bool]:
        """Atomically allocate the next version, or return the existing one for a known checksum."""
        checksum = package.checksum()
        last_conflict: BaseException | None = None
        for attempt in range(_CONFIGURATION_VERSION_ATTEMPTS):
            connection = self._connect()
            try:
                cursor = connection.cursor()
                try:
                    cursor.execute(self._sql(_SELECT_NEXT_CONFIGURATION_VERSION), (config_id,))
                    next_row = cursor.fetchone()
                    next_version = int(next_row[0]) if next_row is not None else 1
                    version = ConfigurationVersion(
                        config_id=config_id,
                        registry_version=next_version,
                        package_checksum=checksum,
                        declared_content=package.declared_content(),
                        created_at=datetime.now(timezone.utc),
                    )
                    self._insert_configuration_version_row(cursor, version)
                    connection.commit()
                except Exception as exc:  # pylint: disable=broad-exception-caught
                    # DB-API drivers do not share an integrity-error base class;
                    # inspect only documented SQLite/PostgreSQL uniqueness markers.
                    connection.rollback()
                    if not _is_unique_violation(exc):
                        raise
                    last_conflict = exc
                else:
                    return version, True
                finally:
                    cursor.close()
            finally:
                connection.close()

            existing = self._lookup_configuration_version_by_checksum(config_id, checksum)
            if existing is not None:
                return existing, False
            if attempt + 1 == _CONFIGURATION_VERSION_ATTEMPTS:
                msg = f"Could not allocate a configuration version for {config_id!r}"
                raise ConfigurationVersionAllocationError(msg) from last_conflict
        msg = "Configuration version allocation loop exited unexpectedly"
        raise AssertionError(msg)

    def _lookup_configuration_version_by_checksum(self, config_id: str, checksum: str) -> ConfigurationVersion | None:
        connection = self._connect()
        try:
            cursor = connection.cursor()
            try:
                cursor.execute(self._sql(_SELECT_CONFIGURATION_VERSION_BY_CHECKSUM), (config_id, checksum))
                row = cursor.fetchone()
            finally:
                cursor.close()
        finally:
            connection.close()
        return None if row is None else _configuration_version_from_row(row)

    def lookup_configuration(self, config_id: str) -> LookupResult[ConfigurationSummary]:
        connection = self._connect()
        try:
            cursor = connection.cursor()
            try:
                cursor.execute(self._sql(_SELECT_CONFIGURATION), (config_id,))
                row = cursor.fetchone()
            finally:
                cursor.close()
        finally:
            connection.close()
        if row is None:
            return LookupResult(value=None, reason="configuration-not-found")
        return LookupResult(value=ConfigurationSummary(config_id=row[0], created_at=row[1]))

    def lookup_configuration_version(self, config_id: str, registry_version: int) -> LookupResult[ConfigurationVersion]:
        connection = self._connect()
        try:
            cursor = connection.cursor()
            try:
                cursor.execute(self._sql(_SELECT_CONFIGURATION_VERSION), (config_id, registry_version))
                row = cursor.fetchone()
            finally:
                cursor.close()
        finally:
            connection.close()
        if row is None:
            return LookupResult(value=None, reason="configuration-version-not-found")
        return LookupResult(value=_configuration_version_from_row(row))

    def list_configurations(self) -> tuple[ConfigurationSummary, ...]:
        """Return every registered configuration in the one stated order (see the SQL)."""
        connection = self._connect()
        try:
            cursor = connection.cursor()
            try:
                cursor.execute(self._sql(_SELECT_CONFIGURATIONS))
                rows = cursor.fetchall()
            finally:
                cursor.close()
        finally:
            connection.close()
        return tuple(ConfigurationSummary(config_id=row[0], created_at=row[1]) for row in rows)

    def list_configuration_versions(self, config_id: str) -> tuple[ConfigurationVersion, ...]:
        connection = self._connect()
        try:
            cursor = connection.cursor()
            try:
                cursor.execute(self._sql(_SELECT_CONFIGURATION_VERSIONS), (config_id,))
                rows = cursor.fetchall()
            finally:
                cursor.close()
        finally:
            connection.close()
        return tuple(_configuration_version_from_row(row) for row in rows)

    def record_results(self, run_id: str, results: Mapping[str, Any]) -> None:
        connection = self._connect()
        try:
            cursor = connection.cursor()
            try:
                cursor.execute(
                    self._sql("UPDATE product_runs SET results = ? WHERE run_id = ?"),
                    (_json(results), run_id),
                )
                _require_run_results_recorded(cursor, run_id)
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                cursor.close()
        finally:
            connection.close()

    def merge_results(self, run_id: str, results: Mapping[str, Any]) -> None:
        """Merge result fields with optimistic concurrency across DB-API providers."""
        last_conflict: BaseException | None = None
        for attempt in range(_RESULT_MERGE_ATTEMPTS):
            connection = self._connect()
            retry = False
            try:
                cursor = connection.cursor()
                try:
                    cursor.execute(self._sql("SELECT results FROM product_runs WHERE run_id = ?"), (run_id,))
                    row = _require_result_row(cursor.fetchone(), run_id)
                    current_text = str(row[0])
                    current = _JSON_MAPPING_ADAPTER.validate_json(current_text)
                    merged = {**current, **results}
                    cursor.execute(
                        self._sql("UPDATE product_runs SET results = ? WHERE run_id = ? AND results = ?"),
                        (_json(merged), run_id, current_text),
                    )
                    if cursor.rowcount == 1:
                        connection.commit()
                        return
                    connection.rollback()
                    retry = True
                except Exception as exc:  # pylint: disable=broad-exception-caught
                    connection.rollback()
                    if not _is_retryable_write_conflict(exc):
                        raise
                    last_conflict = exc
                    retry = True
                finally:
                    cursor.close()
            finally:
                connection.close()
            if retry and attempt + 1 < _RESULT_MERGE_ATTEMPTS:
                sleep(0.01)
        msg = f"Could not merge concurrent results for Sync run ID {run_id!r}"
        raise RuntimeError(msg) from last_conflict

    def _insert_reference(self, cursor: _Cursor, reference: ArtifactReference, *, published: bool) -> None:
        cursor.execute(
            self._sql(_INSERT_ARTIFACT_REFERENCE),
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
                _require_run_finished(cursor, run_id)
                connection.commit()
            except Exception:
                connection.rollback()
                raise
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
            connection.create_function(
                _SQLITE_EXECUTION_TIMESTAMP_FUNCTION,
                1,
                _execution_timestamp_microseconds,
                deterministic=True,
            )
            return cast("DBAPIConnection", connection)

        super().__init__(connect, placeholder="?", dialect="sqlite")


class PostgreSQLRunStore(_RelationalRunStore):
    """PostgreSQL DB-API provider; the caller supplies its connection factory."""

    def __init__(self, connect: Callable[[], DBAPIConnection]) -> None:
        super().__init__(
            connect,
            placeholder="%s",
            dialect="postgresql",
            schema_conflict_codes=_POSTGRESQL_SCHEMA_CONFLICT_CODES,
        )


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
        _validate_file_artifact_layout(reference)
        final_dir = self._path(reference.manifest_key).parent
        if final_dir.exists():
            manifest = final_dir / "manifest.json"
            if manifest.exists():
                msg = f"Artifact {reference.artifact_id!r} is already published"
                raise DuplicateArtifactError(msg)
            existing_data = final_dir / "data"
            if not existing_data.is_file() or not _matches_reference(reference, existing_data.read_bytes()):
                msg = f"Artifact {reference.artifact_id!r} has an incomplete publication with different data"
                raise DuplicateArtifactError(msg)
            self._publication_checkpoint()
            temporary = Path(mkdtemp(prefix=".manifest-repair-", dir=final_dir))
            try:
                staged_manifest = temporary / "manifest.json"
                _write_fsynced(staged_manifest, _manifest_bytes(reference))
                _fsync_directory(temporary)
                staged_manifest.replace(manifest)
                temporary.rmdir()
                _fsync_directory(final_dir)
                _fsync_directory(final_dir.parent)
            except BaseException:
                with suppress(BaseException):
                    _remove_private_publication(temporary)
                raise
            return
        final_dir.parent.mkdir(parents=True, exist_ok=True)
        temporary = Path(mkdtemp(prefix=f".{reference.artifact_id}-", dir=final_dir.parent))
        try:
            data_path = temporary / "data"
            _write_fsynced(data_path, data)
            self._publication_checkpoint()
            _write_fsynced(temporary / "manifest.json", _manifest_bytes(reference))
            _fsync_directory(temporary)
            temporary.replace(final_dir)
            _fsync_directory(final_dir.parent)
        except BaseException:
            with suppress(BaseException):
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

    def put(self, *, bucket: str, key: str, data: bytes, if_absent: bool = False) -> None:
        """Write one object.

        When ``if_absent`` is true, the write must be atomic and create-only.
        Implementations must raise :class:`DuplicateArtifactError` if the key
        already exists instead of replacing it.
        """

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
        except BaseException:
            with suppress(BaseException):
                self._client.delete(bucket=self._bucket, key=temporary_key)
            raise
        with suppress(BaseException):
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


class ProductProjection:  # pylint: disable=too-many-public-methods
    """One product-record contract over interchangeable relational/object providers."""

    def __init__(self, records: _RunStore, artifacts: _ArtifactStore) -> None:
        self._records = records
        self._artifacts = artifacts

    def create_run(self, run: ProductRun, *, secrets: Sequence[str] = ()) -> None:
        """Create an unfinished run with metadata/links; reject completion fields/artifacts."""
        if run.finished_at is not None or run.outcome is not None or run.artifact_refs:
            msg = "a new product record must be unfinished and have no artifact references"
            raise ValueError(msg)
        _require_submitted_executions(run)
        self._records.create(_redacted_run(run, secrets))

    def lookup_run(self, run_id: str) -> LookupResult[ProductRun]:
        """Look up a product record by stable Sync run ID."""
        return self._records.lookup(run_id)

    def add_prefect_execution(
        self,
        run_id: str,
        link: PrefectExecutionLink,
        *,
        allocate_attempt: bool = False,
        secrets: Sequence[str] = (),
    ) -> PrefectExecutionLink:
        """Append one purpose-labelled execution, optionally allocating its ordinal atomically."""
        if link.submitted_at is None:
            msg = "new Prefect execution requires submitted_at"
            raise ValueError(msg)
        if not self._records.exists(run_id):
            msg = f"Cannot link a Prefect execution to unavailable Sync run ID {run_id!r}"
            raise RunNotFoundError(msg)
        sanitized = PrefectExecutionLink.model_validate(_redact_value(link.model_dump(mode="json"), secrets))
        return self._records.add_prefect_execution(run_id, sanitized, allocate_attempt=allocate_attempt)

    def observe_prefect_execution(
        self,
        run_id: str,
        flow_run_id: str,
        *,
        state: str | None,
        secrets: Sequence[str] = (),
    ) -> None:
        """Update live detail for an existing link without creating a retry link."""
        self._records.observe_prefect_execution(
            redact(run_id, secrets),
            redact(flow_run_id, secrets),
            state=None if state is None else redact(state, secrets),
            observed_at=datetime.now(timezone.utc),
        )

    def claim_execution(
        self,
        run_id: str,
        flow_run_id: str,
        *,
        worker_id: str,
        claimed_at: datetime | None = None,
        admission_ttl_seconds: int = 300,
    ) -> bool:
        """Claim one pending execution; only a canonical Prefect worker UUID is accepted."""
        if not _is_canonical_uuid(worker_id):
            raise ValueError(_INVALID_MANAGED_WORKER_ID)
        effective_claimed_at = claimed_at if claimed_at is not None else datetime.now(timezone.utc)
        _require_execution_timestamp(effective_claimed_at)
        admission_deadline_at = effective_claimed_at - timedelta(seconds=admission_ttl_seconds)
        return self._records.claim_execution(
            run_id,
            flow_run_id,
            worker_id=worker_id,
            claimed_at=effective_claimed_at,
            admission_deadline_at=admission_deadline_at,
        )

    def mark_execution_stalled(self, run_id: str, flow_run_id: str, *, stalled_at: datetime | None = None) -> bool:
        """Set the first stall marker while leaving a pre-TTL execution claimable."""
        effective_stalled_at = stalled_at if stalled_at is not None else datetime.now(timezone.utc)
        _require_execution_timestamp(effective_stalled_at)
        return self._records.mark_execution_stalled(run_id, flow_run_id, stalled_at=effective_stalled_at)

    def abandon_execution(self, run_id: str, flow_run_id: str, *, terminal_at: datetime | None = None) -> bool:
        """CAS an unclaimed execution into the terminal abandoned verdict."""
        effective_terminal_at = terminal_at if terminal_at is not None else datetime.now(timezone.utc)
        _require_execution_timestamp(effective_terminal_at)
        return self._records.abandon_execution(run_id, flow_run_id, terminal_at=effective_terminal_at)

    def interrupt_execution(self, run_id: str, flow_run_id: str, *, terminal_at: datetime | None = None) -> bool:
        """CAS a claimed execution into the terminal interrupted/ambiguous verdict."""
        effective_terminal_at = terminal_at if terminal_at is not None else datetime.now(timezone.utc)
        _require_execution_timestamp(effective_terminal_at)
        return self._records.interrupt_execution(run_id, flow_run_id, terminal_at=effective_terminal_at)

    def commit_claimed_execution(
        self,
        run_id: str,
        flow_run_id: str,
        *,
        worker_id: str,
        terminal_at: datetime,
        terminal_state: Literal["completed", "failed"],
        terminal_outcome: Literal["succeeded", "failed"],
        writeback: ExecutionWriteback,
        secrets: Sequence[str] = (),
    ) -> bool:
        """Atomically commit one claimed verdict and its business writeback."""
        if not _is_canonical_uuid(worker_id):
            raise ValueError(_INVALID_MANAGED_WORKER_ID)
        if (terminal_state, terminal_outcome) not in {("completed", "succeeded"), ("failed", "failed")}:
            msg = "claimed execution terminal verdict is invalid"
            raise ValueError(msg)
        _require_execution_timestamp(terminal_at)
        if isinstance(writeback, ExecutionFinishWriteback):
            if writeback.outcome != "failed" and self._records.has_pending_artifacts(run_id):
                msg = f"Sync run ID {run_id!r} has an incomplete artifact publication"
                raise ArtifactUnavailableError(msg)
            run = self.lookup_run(run_id).value
            if run is None:
                msg = f"Sync run ID {run_id!r} is unavailable"
                raise RunNotFoundError(msg)
            for reference in run.artifact_refs:
                result = self._artifacts.lookup(reference)
                if not result.available:
                    msg = f"Artifact {reference.artifact_id!r} is unavailable: {result.reason}"
                    raise ArtifactUnavailableError(msg)
            sanitized: ExecutionWriteback = ExecutionFinishWriteback(
                phase=redact(writeback.phase, secrets),
                outcome=redact(writeback.outcome, secrets),
                finished_at=writeback.finished_at,
                summary=cast("dict[str, Any]", _redact_value(_normalize_mapping(writeback.summary), secrets)),
                results=cast("dict[str, Any]", _redact_value(_normalize_mapping(writeback.results), secrets)),
            )
        else:
            sanitized = ExecutionMergeWriteback(
                results=cast("dict[str, Any]", _redact_value(_normalize_mapping(writeback.results), secrets))
            )
        return self._records.commit_claimed_execution(
            redact(run_id, secrets),
            redact(flow_run_id, secrets),
            worker_id=worker_id,
            terminal_at=terminal_at,
            terminal_state=terminal_state,
            terminal_outcome=terminal_outcome,
            writeback=sanitized,
        )

    def request_execution_cancellation(
        self,
        run_id: str,
        flow_run_id: str,
        *,
        requested_at: datetime,
        recovery_deadline_at: datetime,
        recovery_seconds: float,
        expected_latest_position: int,
        receipt_id: str,
        secrets: Sequence[str] = (),
    ) -> bool:
        """Persist cancellation intent before any remote cancellation request."""
        _require_execution_timestamp(requested_at)
        _require_execution_timestamp(recovery_deadline_at)
        if expected_latest_position < 0:
            raise ValueError(_CANCELLATION_POSITION_ERROR)
        return self._records.request_execution_cancellation(
            redact(run_id, secrets),
            redact(flow_run_id, secrets),
            requested_at=requested_at,
            recovery_deadline_at=recovery_deadline_at,
            recovery_seconds=recovery_seconds,
            expected_latest_position=expected_latest_position,
            receipt_id=redact(receipt_id, secrets),
        )

    def acknowledge_execution_cancellation(
        self,
        run_id: str,
        flow_run_id: str,
        *,
        acknowledged_at: datetime,
        response_status: int,
        response_body: Mapping[str, Any],
        secrets: Sequence[str] = (),
    ) -> bool:
        """Commit acknowledgement and the accepted replay response together."""
        _require_execution_timestamp(acknowledged_at)
        sanitized = cast("Mapping[str, Any]", _redact_value(_normalize_mapping(response_body), secrets))
        return self._records.acknowledge_execution_cancellation(
            redact(run_id, secrets),
            redact(flow_run_id, secrets),
            acknowledged_at=acknowledged_at,
            response_status=response_status,
            response_body=sanitized,
        )

    def cancel_execution(self, run_id: str, flow_run_id: str, *, terminal_at: datetime | None = None) -> bool:
        """Commit clean cancellation only after durable acknowledgement and observation."""
        if terminal_at is not None:
            _require_execution_timestamp(terminal_at)
        return self._records.cancel_execution(
            run_id,
            flow_run_id,
            terminal_at=terminal_at if terminal_at is not None else datetime.now(timezone.utc),
        )

    def expire_execution_cancellation(
        self, run_id: str, flow_run_id: str, *, terminal_at: datetime | None = None
    ) -> bool:
        """Expire bounded cancellation recovery at its inclusive durable deadline."""
        if terminal_at is not None:
            _require_execution_timestamp(terminal_at)
        return self._records.expire_execution_cancellation(
            run_id,
            flow_run_id,
            terminal_at=terminal_at if terminal_at is not None else datetime.now(timezone.utc),
        )

    def pending_executions(self) -> tuple[tuple[str, PrefectExecutionLink], ...]:
        """Return executions that have not received a Sync-owned terminal verdict."""
        return self._records.pending_executions()

    def reserve_mutation(
        self,
        receipt: MutationReceipt,
        *,
        run: ProductRun | None = None,
        admit_write: bool = False,
        secrets: Sequence[str] = (),
    ) -> tuple[MutationReceipt, bool]:
        """Reserve one actor/key mutation and optional run/write admission atomically."""
        sanitized_receipt = MutationReceipt.model_validate(_redact_value(receipt.model_dump(mode="json"), secrets))
        sanitized_run = None if run is None else _redacted_run(run, secrets)
        if sanitized_run is not None:
            if (
                sanitized_run.finished_at is not None
                or sanitized_run.outcome is not None
                or sanitized_run.artifact_refs
            ):
                msg = "a mutation-created product record must be unfinished and have no artifact references"
                raise ValueError(msg)
            _require_submitted_executions(sanitized_run)
            if sanitized_run.run_id != sanitized_receipt.run_id:
                msg = "a mutation receipt and its atomically created product run must share one run ID"
                raise ValueError(msg)
        return self._records.reserve_mutation(sanitized_receipt, sanitized_run, admit_write=admit_write)

    def lookup_mutation(self, actor: str, key_digest: str) -> LookupResult[MutationReceipt]:
        """Look up the durable receipt for an actor and hashed client key."""
        return self._records.lookup_mutation(actor, key_digest)

    def complete_mutation(
        self,
        receipt_id: str,
        *,
        response_status: int,
        response_body: Mapping[str, Any],
        flow_run_id: str | None,
        secrets: Sequence[str] = (),
    ) -> MutationReceipt:
        """Persist the exact accepted HTTP response and Prefect identity."""
        sanitized = cast("Mapping[str, Any]", _redact_value(_normalize_mapping(response_body), secrets))
        return self._records.complete_mutation(
            redact(receipt_id, secrets),
            response_status=response_status,
            response_body=sanitized,
            flow_run_id=None if flow_run_id is None else redact(flow_run_id, secrets),
            updated_at=datetime.now(timezone.utc),
        )

    def claim_mutation(self, receipt_id: str, *, secrets: Sequence[str] = ()) -> bool:
        """Atomically make one retryable receipt the sole in-flight service caller."""
        return self._records.claim_mutation(redact(receipt_id, secrets), updated_at=datetime.now(timezone.utc))

    def release_mutation(self, receipt_id: str, *, secrets: Sequence[str] = ()) -> None:
        """Make a failed in-flight receipt available for a later retry."""
        self._records.release_mutation(redact(receipt_id, secrets), updated_at=datetime.now(timezone.utc))

    def record_audit(self, event: AuditEvent, *, secrets: Sequence[str] = ()) -> None:
        """Append one immutable, secret-safe authorization or mutation event."""
        sanitized = AuditEvent.model_validate(_redact_value(event.model_dump(mode="json"), secrets))
        self._records.record_audit(sanitized)

    def audit_events(self, run_id: str | None = None) -> tuple[AuditEvent, ...]:
        """Return all audit evidence, optionally narrowed to one Sync run."""
        return self._records.audit_events(run_id)

    def create_configuration(self, package: ConfigurationPackage) -> ConfigurationVersion:
        """Validate, then durably register a brand-new configuration's first version."""
        validate_package_credentials(package)
        return self._records.create_configuration(package)

    def add_configuration_version(
        self, config_id: str, package: ConfigurationPackage
    ) -> tuple[ConfigurationVersion, bool]:
        """Validate, then atomically add a version to an existing configuration."""
        validate_package_credentials(package)
        if not self._records.configuration_exists(config_id):
            msg = f"Cannot add a version to unavailable configuration ID {config_id!r}"
            raise ConfigurationNotFoundError(msg)
        return self._records.add_configuration_version(config_id, package)

    def lookup_configuration(self, config_id: str) -> LookupResult[ConfigurationSummary]:
        """Look up a configuration's registry identity by its server-generated ID."""
        return self._records.lookup_configuration(config_id)

    def lookup_configuration_version(self, config_id: str, registry_version: int) -> LookupResult[ConfigurationVersion]:
        """Look up one immutable configuration version by its integer ordinal."""
        return self._records.lookup_configuration_version(config_id, registry_version)

    def list_configurations(self) -> tuple[ConfigurationSummary, ...]:
        """Return every registered configuration's identity, ordered by creation time then ID."""
        return self._records.list_configurations()

    def list_configuration_versions(self, config_id: str) -> tuple[ConfigurationVersion, ...]:
        """Return every registered version of one configuration, oldest first."""
        return self._records.list_configuration_versions(config_id)

    def record_results(
        self,
        run_id: str,
        results: Mapping[str, Any],
        *,
        secrets: Sequence[str] = (),
    ) -> None:
        """Update retained result evidence without changing the run lifecycle."""
        sanitized = cast("Mapping[str, Any]", _redact_value(_normalize_mapping(results), secrets))
        self._records.record_results(redact(run_id, secrets), sanitized)

    def merge_results(
        self,
        run_id: str,
        results: Mapping[str, Any],
        *,
        secrets: Sequence[str] = (),
    ) -> None:
        """Atomically merge retained result fields without a stale read/replace race."""
        sanitized = cast("Mapping[str, Any]", _redact_value(_normalize_mapping(results), secrets))
        self._records.merge_results(redact(run_id, secrets), sanitized)

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
        if not self._records.exists(run_id):
            msg = f"Cannot publish an artifact for unavailable Sync run ID {run_id!r}"
            raise RunNotFoundError(msg)
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
        already_marked = False
        if stored.value is None:
            self._records.reserve_artifact(reference)
        else:
            existing, published = stored.value
            if not _same_publication(existing, reference):
                state = "published record" if published else "pending publication"
                msg = f"Artifact {artifact_id!r} has a {state} with different content or metadata; retry rejected"
                raise DuplicateArtifactError(msg)
            reference = existing
            already_marked = published
            publication = self._artifacts.lookup(reference)
            if publication.available:
                if published:
                    msg = f"Artifact {artifact_id!r} is already published on run {run_id!r}"
                    raise DuplicateArtifactError(msg)
                self._records.mark_artifact_published(reference)
                return reference
            if publication.reason != "manifest-unavailable":
                msg = f"Artifact {artifact_id!r} has an incomplete publication that cannot be resumed: {publication.reason}"
                raise ArtifactUnavailableError(msg)
        self._artifacts.publish(reference, sanitized)
        if not already_marked:
            self._records.mark_artifact_published(reference)
        return reference

    def lookup_artifact(self, run_id: str, artifact_id: str) -> LookupResult[bytes]:
        """Resolve only a run-owned immutable reference, with explicit unavailability."""
        if not self._records.exists(run_id):
            return LookupResult(value=None, reason="run-not-found")
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
        """Finish a run when artifacts are available, or retain a safe failed terminal state."""
        run = self.lookup_run(run_id)
        if run.value is None:
            msg = f"Sync run ID {run_id!r} is unavailable"
            raise RunNotFoundError(msg)
        if outcome != "failed" and self._records.has_pending_artifacts(run_id):
            msg = f"Sync run ID {run_id!r} has an incomplete artifact publication"
            raise ArtifactUnavailableError(msg)
        for reference in run.value.artifact_refs:
            result = self._artifacts.lookup(reference)
            if not result.available:
                msg = f"Artifact {reference.artifact_id!r} is unavailable: {result.reason}"
                raise ArtifactUnavailableError(msg)
        sanitized_summary = cast(
            "Mapping[str, Any]",
            _redact_value(_normalize_mapping(summary), secrets),
        )
        sanitized_results = cast(
            "Mapping[str, Any]",
            _redact_value(_normalize_mapping(results), secrets),
        )
        self._records.finish(
            run_id,
            phase=redact(phase, secrets),
            outcome=redact(outcome, secrets),
            finished_at=datetime.now(timezone.utc),
            summary=sanitized_summary,
            results=sanitized_results,
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


def _execution_timestamp_microseconds(value: str) -> int:
    """Convert one persisted aware ISO timestamp to a comparable integer."""
    parsed = datetime.fromisoformat(value)
    if parsed.utcoffset() is None:
        msg = "persisted execution timestamps must include a timezone"
        raise ValueError(msg)
    elapsed = parsed.astimezone(timezone.utc) - _UNIX_EPOCH
    return ((elapsed.days * 86400) + elapsed.seconds) * 1_000_000 + elapsed.microseconds


def _is_canonical_uuid(value: str) -> bool:
    """Accept only a canonical UUID string, never a non-canonical spelling."""
    try:
        return str(UUID(value)) == value
    except ValueError:
        return False


def _run_from_rows(
    row: Sequence[Any],
    references: Sequence[Sequence[Any]],
    links: Sequence[Sequence[Any]],
    managed_audit_links: Sequence[Sequence[Any]],
) -> ProductRun:
    audit_links = tuple(dict.fromkeys((*json.loads(row[4]), *(str(item[0]) for item in managed_audit_links))))
    return ProductRun.model_validate(
        {
            "run_id": row[0],
            "operation": row[1],
            "configuration_reference": row[2],
            "actor": row[3],
            "audit_links": audit_links,
            "started_at": row[5],
            "finished_at": row[6],
            "phase": row[7],
            "outcome": row[8],
            "summary": json.loads(row[9]),
            "results": json.loads(row[10]),
            "config_id": row[11],
            "registry_version": row[12],
            "package_checksum": row[13],
            "artifact_refs": [_reference_from_row(item).model_dump() for item in references],
            "prefect_executions": [
                {
                    "flow_run_id": item[1],
                    "deployment_id": item[2],
                    "purpose": item[3],
                    "attempt": item[4],
                    "last_observed_state": item[5],
                    "last_observed_at": item[6],
                    "submitted_at": item[7] if item[7] is not None else item[6],
                    "claimed_at": item[8],
                    "claiming_worker_id": item[9],
                    "stalled_at": item[10],
                    "cancellation_requested_at": item[11],
                    "cancellation_recovery_deadline_at": item[12],
                    "cancellation_receipt_id": item[13],
                    "cancellation_acknowledged_at": item[14],
                    "terminal_at": item[15],
                    "terminal_state": item[16],
                    "terminal_outcome": item[17],
                }
                for item in links
            ],
        }
    )


def _receipt_values(receipt: MutationReceipt) -> tuple[Any, ...]:
    return (
        receipt.receipt_id,
        receipt.actor,
        receipt.key_digest,
        receipt.operation,
        receipt.target_run_id,
        receipt.request_fingerprint,
        receipt.reason,
        receipt.resource_kind,
        receipt.resource_id,
        receipt.run_id,
        receipt.prefect_key,
        receipt.state,
        receipt.response_status,
        None if receipt.response_body is None else _json(receipt.response_body),
        receipt.flow_run_id,
        receipt.created_at.isoformat(),
        receipt.updated_at.isoformat(),
    )


def _receipt_from_row(row: Sequence[Any]) -> MutationReceipt:
    return MutationReceipt.model_validate(
        {
            "receipt_id": row[0],
            "actor": row[1],
            "key_digest": row[2],
            "operation": row[3],
            "target_run_id": row[4],
            "request_fingerprint": row[5],
            "reason": row[6],
            "resource_kind": row[7],
            "resource_id": row[8],
            "run_id": row[9],
            "prefect_key": row[10],
            "state": row[11],
            "response_status": row[12],
            "response_body": None if row[13] is None else json.loads(row[13]),
            "flow_run_id": row[14],
            "created_at": row[15],
            "updated_at": row[16],
        }
    )


def _cancellation_terminal_response(run_id: str, receipt_id: str) -> dict[str, Any]:
    return {
        "error": {
            "code": "execution-terminal",
            "message": "the managed execution is already terminal",
            "status": 409,
            "run_id": run_id,
            "mutation_id": receipt_id,
        }
    }


def _cancellation_unconfirmed_response(run_id: str, receipt_id: str) -> dict[str, Any]:
    return {
        "error": {
            "code": "cancellation-unconfirmed",
            "message": "Prefect did not confirm cancellation before the recovery deadline",
            "status": 503,
            "run_id": run_id,
            "mutation_id": receipt_id,
        }
    }


def _audit_from_row(row: Sequence[Any]) -> AuditEvent:
    return AuditEvent.model_validate(
        {
            "event_id": row[0],
            "run_id": row[1],
            "actor": row[2],
            "operation": row[3],
            "reason": row[4],
            "outcome": row[5],
            "created_at": row[6],
        }
    )


def _generate_config_id() -> str:
    """Return a sortable, low-collision configuration identifier.

    Delegates to ``infrahub_sync.cache.paths.generate_run_id`` so the registry shares one
    identifier scheme and implementation with run IDs, rather than maintaining a second one
    that a docstring claims mirrors it but nothing enforces.
    """
    return generate_run_id()


def _configuration_version_from_row(row: Sequence[Any]) -> ConfigurationVersion:
    return ConfigurationVersion.model_validate(
        {
            "config_id": row[0],
            "registry_version": row[1],
            "package_checksum": row[2],
            "declared_content": json.loads(row[3]),
            "created_at": row[4],
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


def _is_retryable_write_conflict(exc: BaseException) -> bool:
    if isinstance(exc, sqlite3.OperationalError):
        return "database is locked" in str(exc).lower()
    return _sqlstate(exc) in {"40001", "40P01"}


def _sqlstate(exc: BaseException) -> str | None:
    direct = getattr(exc, "sqlstate", None) or getattr(exc, "pgcode", None)
    if isinstance(direct, str):
        return direct
    diagnostic = getattr(exc, "diag", None)
    value = getattr(diagnostic, "sqlstate", None)
    return value if isinstance(value, str) else None


def _is_duplicate_column_error(exc: BaseException) -> bool:
    """Recognize a concurrent ``ALTER TABLE ADD COLUMN`` race, not a genuine schema failure."""
    if isinstance(exc, sqlite3.OperationalError):
        return "duplicate column name" in str(exc).lower()
    return _sqlstate(exc) == _POSTGRESQL_DUPLICATE_COLUMN_CODE


def _require_checksum_digests_content(checksum: str, declared_content: Mapping[str, Any]) -> None:
    """Refuse to persist a configuration version whose checksum does not digest its own
    declared content.

    ``create_configuration``/``add_configuration_version`` trust ``package.checksum()`` and
    separately persist ``package.declared_content()``; nothing else checks the two
    correspond, and ``configuration_versions`` is append-only, so any future drift between
    them would write a permanently mismatched row with no later chance to catch it.
    """
    expected = sha256(canonical_json_bytes(declared_content, kind="configuration-package")).hexdigest()
    if checksum != expected:
        msg = f"package checksum {checksum!r} does not digest its own declared content"
        raise AssertionError(msg)


def _require_publication_marked(cursor: _Cursor, reference: ArtifactReference) -> None:
    if cursor.rowcount != 1:
        msg = f"Artifact {reference.artifact_id!r} on run {reference.run_id!r} has no matching pending publication"
        raise ArtifactUnavailableError(msg)


def _require_run_finished(cursor: _Cursor, run_id: str) -> None:
    if cursor.rowcount != 1:
        msg = f"Cannot finish unavailable Sync run ID {run_id!r}"
        raise RunNotFoundError(msg)


def _require_execution_observed(cursor: _Cursor, run_id: str, flow_run_id: str) -> None:
    if cursor.rowcount != 1:
        msg = f"Prefect flow-run ID {flow_run_id!r} is not linked to Sync run {run_id!r}"
        raise RunNotFoundError(msg)


def _require_run_results_recorded(cursor: _Cursor, run_id: str) -> None:
    if cursor.rowcount != 1:
        msg = f"Cannot record results for unavailable Sync run ID {run_id!r}"
        raise RunNotFoundError(msg)


def _require_result_row(row: Sequence[Any] | None, run_id: str) -> Sequence[Any]:
    if row is None:
        msg = f"Cannot merge results for unavailable Sync run ID {run_id!r}"
        raise RunNotFoundError(msg)
    return row


def _catalog_nullability(rows: Sequence[Sequence[Any]]) -> dict[str, str]:
    """Read ``(column_name, is_nullable)`` catalog rows, refusing any other row shape.

    Nullability decides whether the legacy ``DROP NOT NULL`` migration still has work to do,
    so a row that cannot report it is a provider failure rather than a default. Treating a
    narrower row as "already nullable" would skip the one-time migration and leave a real
    ``NOT NULL`` column in place, which no later construction would repair.
    """
    nullability: dict[str, str] = {}
    for row in rows:
        if len(row) != _CATALOG_NULLABILITY_ROW_WIDTH:
            raise ProductStoreProviderError
        nullability[str(row[0])] = str(row[1])
    return nullability


def _require_cancellation_receipt_row(row: Sequence[Any] | None) -> Sequence[Any]:
    if row is None:
        msg = "execution cancellation receipt is unavailable"
        raise RunNotFoundError(msg)
    return row


def _require_execution_timestamp(value: datetime) -> None:
    if value.utcoffset() is None:
        msg = "execution timestamps must include a timezone"
        raise ValueError(msg)


def _redact_value(value: Any, secrets: Sequence[str]) -> Any:
    if isinstance(value, str):
        return redact(value, secrets)
    if isinstance(value, Mapping):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            sanitized_key = redact(str(key), secrets)
            if sanitized_key in sanitized:
                msg = f"Redaction would collapse multiple mapping keys into {sanitized_key!r}"
                raise ValueError(msg)
            sanitized[sanitized_key] = _redact_value(item, secrets)
        return sanitized
    if isinstance(value, (list, tuple)):
        return [_redact_value(item, secrets) for item in value]
    return value


def _redacted_run(run: ProductRun, secrets: Sequence[str]) -> ProductRun:
    return ProductRun.model_validate(_redact_value(run.model_dump(mode="json"), secrets))


def _require_submitted_executions(run: ProductRun) -> None:
    if any(link.submitted_at is None for link in run.prefect_executions):
        msg = "new Prefect execution requires submitted_at"
        raise ValueError(msg)


def _raise_active_cancellation_conflict(run_id: str) -> None:
    msg = f"Sync run {run_id!r} has an active execution cancellation"
    raise WriteAdmissionConflictError(msg)


def _redact_bytes(data: bytes, secrets: Sequence[str]) -> bytes:
    for secret in secrets:
        if secret:
            data = data.replace(secret.encode(), REDACTED.encode())
    return data


def _manifest_bytes(reference: ArtifactReference) -> bytes:
    return canonical_json_bytes({"format_version": 1, **reference.model_dump(mode="json")})


def _validate_publication(reference: ArtifactReference, manifest: bytes, data: bytes) -> LookupResult[bytes]:
    try:
        stored = json.loads(manifest)
        if not isinstance(stored, Mapping):
            return LookupResult(value=None, reason="manifest-invalid")
        parsed = ArtifactReference.model_validate(
            {key: value for key, value in stored.items() if key != "format_version"}
        )
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        return LookupResult(value=None, reason="manifest-invalid")
    if parsed != reference or sha256(data).hexdigest() != reference.digest or len(data) != reference.size:
        return LookupResult(value=None, reason="artifact-integrity-failed")
    return LookupResult(value=data)


def _expired(reference: ArtifactReference) -> bool:
    return reference.expires_at is not None and reference.expires_at <= datetime.now(timezone.utc)


def _matches_reference(reference: ArtifactReference, data: bytes) -> bool:
    return sha256(data).hexdigest() == reference.digest and len(data) == reference.size


def _validate_file_artifact_layout(reference: ArtifactReference) -> None:
    object_key = PurePosixPath(reference.object_key)
    manifest_key = PurePosixPath(reference.manifest_key)
    if (
        object_key.is_absolute()
        or manifest_key.is_absolute()
        or object_key.parent != manifest_key.parent
        or object_key.name != "data"
        or manifest_key.name != "manifest.json"
    ):
        msg = "filesystem artifact keys must be relative sibling paths ending in 'data' and 'manifest.json'"
        raise ValueError(msg)


def _normalize_mapping(value: Any) -> Mapping[str, Any]:
    validated = _JSON_MAPPING_ADAPTER.validate_python(value)
    return _JSON_MAPPING_ADAPTER.dump_python(validated, mode="json")


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
