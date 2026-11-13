"""Durable relational and immutable artifact providers for product records."""

# pylint: disable=too-many-lines  # one module owns the provider contract and both backends

from __future__ import annotations

import json
import logging
import os
import sqlite3
from collections.abc import Callable, Iterable, Mapping, Sequence
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from math import isfinite
from pathlib import Path, PurePosixPath
from tempfile import mkdtemp
from time import sleep
from typing import TYPE_CHECKING, Any, Literal, Protocol, cast
from uuid import UUID

from pydantic import TypeAdapter, ValidationError

from infrahub_sync.cache.paths import generate_run_id
from infrahub_sync.configuration import ConfigurationPackage, validate_package_credentials
from infrahub_sync.execution import REDACTED, json_string_forms, redact, redact_ordered, redaction_order
from infrahub_sync.plan.canonical import canonical_json_bytes
from infrahub_sync.product_store.bundle import (
    FINAL_CHECKPOINT_ARTIFACT_ID,
    MAX_BUNDLE_BYTES,
    PLAN_CHECKPOINT_ARTIFACT_ID,
)
from infrahub_sync.product_store.models import (
    DEFAULT_LIMIT,
    RUN_ALREADY_CANCELLED_MESSAGE,
    RUN_CANCELLED_MESSAGE,
    ApplyProgress,
    ApplyProgressSave,
    ArtifactReference,
    AuditEvent,
    BaselineWriteback,
    ConfigurationBaseline,
    ConfigurationPage,
    ConfigurationSummary,
    ConfigurationVersion,
    ConfigurationVersionPage,
    ConfigurationVersionSummary,
    EventFilters,
    EventPage,
    ExecutionFinishWriteback,
    ExecutionMergeWriteback,
    ExecutionWriteback,
    LookupResult,
    MutationReceipt,
    PrefectExecutionLink,
    ProductRun,
    RunApproval,
    RunFilters,
    RunPage,
    RunReference,
    WorkerEnvironment,
    already_approved_body,
    approval_error_body,
    not_approvable_message,
)

if TYPE_CHECKING:
    # Typing only: importing the engine package at runtime would load the whole engine into the store.
    from infrahub_sync.potenda.read_progress import Side

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS product_runs (
    run_id TEXT PRIMARY KEY, operation TEXT NOT NULL, configuration_reference TEXT NOT NULL,
    actor TEXT, audit_links TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT,
    phase TEXT NOT NULL, outcome TEXT, summary TEXT NOT NULL, results TEXT NOT NULL,
    reconciliation_required BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE TABLE IF NOT EXISTS artifact_refs (
    run_id TEXT NOT NULL, artifact_id TEXT NOT NULL, kind TEXT NOT NULL, media_type TEXT NOT NULL,
    digest TEXT NOT NULL, size INTEGER NOT NULL, object_key TEXT NOT NULL UNIQUE,
    manifest_key TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL, expires_at TEXT, published INTEGER NOT NULL,
    visibility TEXT NOT NULL DEFAULT 'public',
    PRIMARY KEY (run_id, artifact_id), FOREIGN KEY (run_id) REFERENCES product_runs(run_id)
);
CREATE TABLE IF NOT EXISTS configuration_baselines (
    config_id TEXT PRIMARY KEY, source_row_counts TEXT NOT NULL,
    runs_since_full_extract INTEGER NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS mutation_receipts (
    receipt_id TEXT PRIMARY KEY, actor TEXT NOT NULL, key_digest TEXT NOT NULL,
    operation TEXT NOT NULL, target_run_id TEXT, request_fingerprint TEXT NOT NULL,
    reason TEXT, resource_kind TEXT NOT NULL DEFAULT 'run', resource_id TEXT NOT NULL, run_id TEXT, prefect_key TEXT,
    state TEXT NOT NULL, response_status INTEGER, response_body TEXT, flow_run_id TEXT,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    UNIQUE (actor, key_digest)
);
CREATE TABLE IF NOT EXISTS prefect_executions (
    run_id TEXT NOT NULL, flow_run_id TEXT NOT NULL, deployment_id TEXT, purpose TEXT NOT NULL,
    attempt INTEGER NOT NULL, last_observed_state TEXT, last_observed_at TEXT, submitted_at TEXT,
    claimed_at TEXT, claiming_worker_id TEXT, stalled_at TEXT, cancellation_requested_at TEXT,
    cancellation_recovery_deadline_at TEXT, cancellation_receipt_id TEXT, cancellation_acknowledged_at TEXT,
    terminal_at TEXT, terminal_state TEXT, terminal_outcome TEXT, position INTEGER NOT NULL,
    mutation_receipt_id TEXT UNIQUE, source_read TEXT, destination_read TEXT, apply_progress TEXT,
    PRIMARY KEY (run_id, flow_run_id), FOREIGN KEY (run_id) REFERENCES product_runs(run_id),
    FOREIGN KEY (mutation_receipt_id) REFERENCES mutation_receipts(receipt_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS prefect_executions_run_position
    ON prefect_executions (run_id, position);
CREATE UNIQUE INDEX IF NOT EXISTS prefect_executions_run_purpose_attempt
    ON prefect_executions (run_id, purpose, attempt);
CREATE TABLE IF NOT EXISTS write_admissions (
    run_id TEXT PRIMARY KEY, receipt_id TEXT NOT NULL UNIQUE, operation TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES product_runs(run_id),
    FOREIGN KEY (receipt_id) REFERENCES mutation_receipts(receipt_id)
);
CREATE TABLE IF NOT EXISTS resume_admissions (
    run_id TEXT NOT NULL, resume_number INTEGER NOT NULL, receipt_id TEXT NOT NULL UNIQUE,
    PRIMARY KEY (run_id, resume_number), FOREIGN KEY (run_id) REFERENCES product_runs(run_id),
    FOREIGN KEY (receipt_id) REFERENCES mutation_receipts(receipt_id)
);
CREATE TABLE IF NOT EXISTS audit_events (
    event_id TEXT PRIMARY KEY, run_id TEXT, actor TEXT NOT NULL, operation TEXT NOT NULL,
    reason TEXT, outcome TEXT NOT NULL, created_at TEXT NOT NULL,
    event_type TEXT, config_id TEXT, registry_version INTEGER, branch TEXT
);
CREATE INDEX IF NOT EXISTS audit_events_run_created
    ON audit_events (run_id, created_at);
CREATE INDEX IF NOT EXISTS audit_events_created
    ON audit_events (created_at, event_id);
CREATE TABLE IF NOT EXISTS audit_event_resources (
    event_id TEXT NOT NULL, kind TEXT NOT NULL, resource_id TEXT NOT NULL,
    PRIMARY KEY (event_id, kind, resource_id), FOREIGN KEY (event_id) REFERENCES audit_events(event_id)
);
CREATE INDEX IF NOT EXISTS audit_event_resources_kind_resource
    ON audit_event_resources (kind, resource_id);
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
CREATE TABLE IF NOT EXISTS worker_environments (
    worker_id TEXT PRIMARY KEY, credential_names TEXT NOT NULL, observed_at TEXT NOT NULL
);
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
# A run in phase ``cancelled`` keeps it: a withdrawn plan admits only a ``verify``, and that
# verify being abandoned or interrupted must not move the run to a phase that admits an apply.
_UPDATE_LATEST_EXECUTION_TERMINAL = (
    "UPDATE product_runs SET phase = ?, outcome = ?, finished_at = ? WHERE run_id = ? AND phase <> 'cancelled' "
    "AND (SELECT latest.flow_run_id FROM prefect_executions AS latest "
    "WHERE latest.run_id = product_runs.run_id ORDER BY latest.position DESC LIMIT 1) = ?"
)
_UPDATE_LATEST_EXECUTION_CANCELLED = (
    "UPDATE product_runs SET phase = 'cancelled', outcome = 'cancelled', finished_at = ? WHERE run_id = ? "
    "AND (SELECT latest.flow_run_id FROM prefect_executions AS latest "
    "WHERE latest.run_id = product_runs.run_id ORDER BY latest.position DESC LIMIT 1) = ?"
)

# Nullable run-to-configuration binding columns on ``product_runs``. Added by an
# introspection-guarded ALTER (see ``_migrate_added_columns``) rather than baked into
# the ``product_runs`` definition above, so a pre-existing deployment's table is migrated the
# same way a brand-new one is initialized. ``ProductRun.config_id``, ``registry_version`` and
# ``package_checksum`` read and write them; a row's three binding columns are either all NULL
# (unregistered run) or all NOT NULL (registered run), enforced by a CHECK constraint on
# PostgreSQL and a pair of triggers on SQLite.
_CONFIGURATION_BINDING_COLUMNS: tuple[tuple[str, str], ...] = (
    ("config_id", "TEXT"),
    ("registry_version", "INTEGER"),
    ("package_checksum", "TEXT"),
)
# Nullable approval columns on ``product_runs``, read and written as ``ProductRun.approval``.
# ``approved_by``, ``approved_at`` (UTC ISO 8601 text, like the other run timestamps) and
# ``approved_checksum`` are all NULL (never approved) or all NOT NULL; ``approval_reason`` is
# the optional audit reason. Runs stored before approval existed keep NULL and read as unapproved.
_APPROVAL_COLUMNS: tuple[tuple[str, str], ...] = (
    ("approved_by", "TEXT"),
    ("approved_at", "TEXT"),
    ("approved_checksum", "TEXT"),
    ("approval_reason", "TEXT"),
)
# Every nullable column the start-up migration adds to a pre-existing table, per table. Each is
# added only when the catalog does not list it, so a second start-up issues no ALTER. Runs
# created before ``destination_branch`` existed keep NULL; nothing backfills it.
_ADDED_NULLABLE_COLUMNS: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = (
    ("product_runs", (*_CONFIGURATION_BINDING_COLUMNS, ("destination_branch", "TEXT"), *_APPROVAL_COLUMNS)),
    ("configuration_versions", (("registered_by", "TEXT"), ("registration_reason", "TEXT"))),
    ("configurations", (("name", "TEXT"),)),
)
# Indexes for listing runs newest first: unfiltered, filtered by configuration, and filtered
# by configuration and version; for each version's most recently applied run, a partial index
# over applied runs only, in the order ``_SELECT_LATEST_VERSION_RUNS`` ranks them; and for the
# configuration list, its name filter and its ``(created_at, config_id)`` order. Created after
# the column migration because these indexes name columns a pre-existing table only gains
# there. ``IF NOT EXISTS`` makes each re-run a no-op. SQLite and PostgreSQL both scan an
# ascending index backwards for a descending order, and both support partial indexes.
_RUN_LISTING_INDEXES: tuple[str, ...] = (
    "CREATE INDEX IF NOT EXISTS product_runs_started ON product_runs (started_at, run_id)",
    "CREATE INDEX IF NOT EXISTS product_runs_config_started ON product_runs (config_id, started_at, run_id)",
    "CREATE INDEX IF NOT EXISTS product_runs_binding_started "
    "ON product_runs (config_id, registry_version, started_at, run_id)",
    "CREATE INDEX IF NOT EXISTS product_runs_binding_applied "
    "ON product_runs (config_id, registry_version, finished_at, run_id) WHERE phase = 'applied'",
    "CREATE INDEX IF NOT EXISTS configurations_name ON configurations (name)",
    "CREATE INDEX IF NOT EXISTS configurations_created ON configurations (created_at, config_id)",
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
# The read state of each side of a plan or sync execution: one JSON document per side, so the
# two read threads never write the same column (spec 018, research R3).
_SIDE_READ_COLUMNS: dict[Side, str] = {"source": "source_read", "destination": "destination_read"}
# The fields and values a side's read document may hold. The store checks them before it writes,
# so a document cannot carry an exception message, an address, or a secret into the run record.
# The read modes repeat `ReadMode` in the engine, which the store does not import at run time.
_SIDE_READ_FIELDS = frozenset({"state", "started_at", "ended_at", "read_mode", "nodes_by_kind", "error_class"})
_SIDE_READ_STATES = frozenset({"waiting", "reading", "succeeded", "failed"})
_SIDE_READ_MODES = frozenset({"full", "incremental"})
_SIDE_READ_ERROR_CLASS_MAX_LENGTH = 200
_PREFECT_EXECUTION_READ_COLUMNS: tuple[tuple[str, str], ...] = tuple(
    (column, "TEXT") for column in _SIDE_READ_COLUMNS.values()
)
# The last progress marker of a write execution, one JSON document (spec 022, research R2).
_PREFECT_EXECUTION_PROGRESS_COLUMNS: tuple[tuple[str, str], ...] = (("apply_progress", "TEXT"),)
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
# What schema initialization retries on PostgreSQL: the duplicate-catalog conflicts above,
# and a deadlock (40P01). The advisory lock below serializes initializers, but not a writer
# that is already running: `add_prefect_execution` updates `product_runs` and then inserts
# into `prefect_executions`, while an initializer locks the two tables in the opposite
# order, so PostgreSQL can abort the initializer with 40P01.
_POSTGRESQL_SCHEMA_INITIALIZATION_RETRY_CODES = _POSTGRESQL_SCHEMA_CONFLICT_CODES | {"40P01"}
_POSTGRESQL_DUPLICATE_COLUMN_CODE = "42701"
# On PostgreSQL, ``_column_nullability`` is the one catalog read that needs nullability, and
# it projects exactly (column_name, is_nullable). The other two read column names alone. PostgreSQL
# declares ``information_schema.columns.is_nullable`` over the ``yes_or_no`` domain, so these
# two strings are the whole set of readable answers.
_CATALOG_NULLABILITY_ROW_WIDTH = 2
_CATALOG_NULLABILITY_VALUES = frozenset({"YES", "NO"})
# The stages one run's admission arbitrates against each other. Cancellation is
# deliberately outside the set: it targets the execution a write already owns and never
# reserves a stage of its own, so a claimed write stays cancellable. A resume (spec 022)
# reserves through `reserve_resume`, and its unresolved receipt blocks every other stage.
_ARBITRATED_OPERATIONS = ("plan", "verify", "apply", "sync", "resume")
# Another unresolved receipt of an arbitrated stage on the run. Built from the tuple above, so
# an operation the service answers itself, such as ``approve``, is outside it without a second list.
_SELECT_UNRESOLVED_ARBITRATED_RECEIPT = (
    "SELECT 1 FROM mutation_receipts WHERE run_id = ? AND receipt_id <> ? AND state <> 'accepted' "  # noqa: S608 - placeholders only.
    f"AND operation IN ({', '.join('?' for _ in _ARBITRATED_OPERATIONS)})"
)
# The two stages that reach a destination. The store's SQL spells them literally; this is
# the Python-side membership test for the same pair.
_WRITE_OPERATIONS = ("apply", "sync")
# Every execution purpose that reaches a destination: the two admitted writes, and a
# `resume` of an interrupted one (spec 022). A resume holds a `resume_admissions` row, not
# the run's `write_admissions` row, so it is kept out of `_WRITE_OPERATIONS`. Public, so the
# Sync API's run progress view tests the same set (`service/resume_view.py`).
WRITE_PURPOSES: tuple[str, ...] = ("apply", "sync", "resume")
# The artifact ID of a run's published plan review document. The plan stage and a `sync`
# publish it before any write, so a run without it wrote nothing. Public, so the Sync API
# reads the same plan the store's resumable-run rule tests (spec 022, research R14).
PLAN_ARTIFACT_ID = "plan-review"
# Why a run whose write stage stopped before it published its plan is not resumable.
NO_SAVED_PLAN_MESSAGE = (
    "this Sync run saved no plan, so its write stage stopped before any write and there is "
    "nothing to resume; start a new `plan` or `sync` run"
)
_RUN_NOT_RESUMABLE = "run-not-resumable"
# Whether the run's plan review document is published: the same predicate as the references
# a run lookup returns (`_SELECT_RUN_ARTIFACT_REFERENCES`). Parameters: the run ID, the artifact ID.
_SELECT_PLAN_PUBLISHED = (
    "SELECT 1 FROM artifact_refs WHERE run_id = ? AND artifact_id = ? AND published = 1 AND visibility = 'public'"
)
# The resumable-run rule (`_run_is_resumable`). Parameters: the run ID three times, the
# plan's artifact ID.
_SELECT_RESUMABLE_RUN = (
    "SELECT 1 FROM product_runs WHERE run_id = ? AND reconciliation_required AND phase <> 'cancelled' "
    "AND NOT EXISTS (SELECT 1 FROM prefect_executions WHERE prefect_executions.run_id = ? "
    "AND prefect_executions.terminal_at IS NULL) "
    "AND EXISTS (SELECT 1 FROM artifact_refs WHERE artifact_refs.run_id = ? AND artifact_refs.artifact_id = ? "
    "AND artifact_refs.published = 1 AND artifact_refs.visibility = 'public')"
)
_VERIFY_REQUIRED = "verify-required"
_RESUME_IN_PROGRESS_MESSAGE = "another resume of this Sync run is in progress"
_VERIFY_REQUIRED_MESSAGE = (
    "no verify passed on this Sync run after its latest write; run verify, review its report, then resume"
)
_RESUMED_APPEND_REFUSED = "appending a resume execution requires the mutation receipt that holds its resume admission"
_RUN_EXECUTION_CONFLICT = "run-execution-conflict"
_RUN_EXECUTION_CONFLICT_MESSAGE = (
    "this Sync run already has a write admission, an unresolved submission, or a running "
    "execution; review the run and create a new plan or sync run"
)
# Gives back the write admission a `sync` reserved at creation. Only the `sync`'s own
# admission matches, never an apply's, and never on a run that requires reconciliation.
_RELEASE_STOPPED_SYNC_ADMISSION = (
    "DELETE FROM write_admissions WHERE run_id = ? AND operation = 'sync' AND NOT EXISTS "
    "(SELECT 1 FROM product_runs WHERE run_id = ? AND reconciliation_required)"
)
_ADMITTED_APPEND_REFUSED = (
    "appending a write execution requires the mutation receipt that holds this run's write admission"
)
_UNRESOLVED_APPEND_REFUSED = "appending an execution requires an unresolved mutation receipt for this run"
_REPEATED_APPEND_REFUSED = "this mutation receipt has already appended its one execution"
_PREFECT_POSITION_ATTEMPTS = 3
# The run-bundle protocol fixes these two identifiers, so their one writer is a product
# stage that may repeat a completed publication after losing the response to it.
_FIXED_CHECKPOINT_ARTIFACT_IDS = frozenset({PLAN_CHECKPOINT_ARTIFACT_ID, FINAL_CHECKPOINT_ARTIFACT_ID})
_RESULT_MERGE_ATTEMPTS = 5
_SCHEMA_INITIALIZATION_ATTEMPTS = 2
# Serializes schema initialization across processes that start together, per schema. Each
# `CREATE INDEX IF NOT EXISTS` takes a SHARE lock on its table even when the index exists,
# and the audit-event fill that follows needs ROW EXCLUSIVE on the same table, so two
# unserialized initializers deadlock. The transaction-level lock is released at commit or
# rollback.
_POSTGRESQL_SCHEMA_INITIALIZATION_LOCK = (
    "SELECT pg_advisory_xact_lock(hashtextextended('infrahub_sync.product_store.schema.' || current_schema(), 0))"
)
# Measured, not guessed: across roughly 4,800 trials of 8 concurrent distinct-checksum
# add_configuration_version() callers against SQLite (the tightest case, single-writer
# locking), the worst observed attempt count for any one caller was 5-7. This budget
# supports up to 8 concurrent writers with margin above that observed worst case.
_CONFIGURATION_VERSION_ATTEMPTS = 8
_JSON_MAPPING_ADAPTER = TypeAdapter(dict[str, Any])
_JSON_LIST_ADAPTER = TypeAdapter(list[str])
_INVALID_SERVICE_WORKER_ID = "service worker identity is invalid"

_INSERT_CONFIGURATION = "INSERT INTO configurations (config_id, created_at) VALUES (?, ?)"
# A configuration summary: identity, current name and the highest registered version.
_CONFIGURATION_SUMMARY_COLUMNS = """configurations.config_id, configurations.created_at, configurations.name,
(SELECT MAX(versions.registry_version) FROM configuration_versions AS versions
WHERE versions.config_id = configurations.config_id)"""
_SELECT_CONFIGURATION = "SELECT " + _CONFIGURATION_SUMMARY_COLUMNS + " FROM configurations WHERE config_id = ?"  # noqa: S608 - module constants only.
# The one stated configurations listing order. ``created_at`` alone is not total — the clock
# can give two registrations one timestamp — so ``config_id`` makes the order deterministic.
_SELECT_CONFIGURATIONS = (
    "SELECT " + _CONFIGURATION_SUMMARY_COLUMNS + " FROM configurations ORDER BY created_at, config_id"  # noqa: S608 - module constants only.
)
# The paged configuration list is assembled from these fixed fragments; every value is bound.
# Newest first, with ``config_id`` breaking equal creation times, keyset paged like the run list.
_SELECT_CONFIGURATION_LIST = "SELECT " + _CONFIGURATION_SUMMARY_COLUMNS + " FROM configurations"  # noqa: S608 - module constants only.
_CONFIGURATION_LIST_NAME = "configurations.name = ?"
_CONFIGURATION_LIST_AFTER = (
    "(configurations.created_at < ? OR (configurations.created_at = ? AND configurations.config_id < ?))"
)
_CONFIGURATION_LIST_ORDER = " ORDER BY configurations.created_at DESC, configurations.config_id DESC LIMIT ?"
_UPDATE_CONFIGURATION_NAME = "UPDATE configurations SET name = ? WHERE config_id = ?"
# Fill of ``configurations.name`` for rows registered before the column existed, run by the
# start-up that adds the column: the latest version's package of every configuration whose
# name is still NULL.
_SELECT_UNNAMED_CONFIGURATION_PACKAGES = """SELECT configurations.config_id, versions.declared_content
FROM configurations JOIN configuration_versions AS versions ON versions.config_id = configurations.config_id
WHERE configurations.name IS NULL AND versions.registry_version = (SELECT MAX(latest.registry_version)
FROM configuration_versions AS latest WHERE latest.config_id = configurations.config_id)"""
_FILL_CONFIGURATION_NAME = "UPDATE configurations SET name = ? WHERE config_id = ? AND name IS NULL"
_INSERT_CONFIGURATION_VERSION = """INSERT INTO configuration_versions (config_id, registry_version, package_checksum,
declared_content, created_at, registered_by, registration_reason) VALUES (?, ?, ?, ?, ?, ?, ?)"""
# The paged version list: summaries without the package, newest version first.
_SELECT_CONFIGURATION_VERSION_LIST = """SELECT config_id, registry_version, package_checksum, created_at,
registered_by, registration_reason FROM configuration_versions WHERE config_id = ?"""
_CONFIGURATION_VERSION_LIST_AFTER = " AND registry_version < ?"
_CONFIGURATION_VERSION_LIST_ORDER = " ORDER BY registry_version DESC LIMIT ?"
# For every version on one page, the newest run and the most recently applied run, in one query.
# The first branch ranks the version's runs by ``(started_at, run_id)`` descending. The applied
# branch ranks by ``(finished_at, run_id)`` descending: an apply advances the plan run in place,
# so ``started_at`` is when the plan was created, while ``finished_at`` is written with
# ``phase = 'applied'`` when the apply stage finishes. Each branch keeps its first row;
# ``ROW_NUMBER`` is available on SQLite 3.25+ and PostgreSQL. ``{0}`` is a list of ``?``.
_SELECT_LATEST_VERSION_RUNS = """SELECT registry_version, kind, run_id, phase, outcome, started_at, finished_at FROM (
SELECT registry_version, 'latest' AS kind, run_id, phase, outcome, started_at, finished_at,
ROW_NUMBER() OVER (PARTITION BY registry_version ORDER BY started_at DESC, run_id DESC) AS position
FROM product_runs WHERE config_id = ? AND registry_version IN ({0})
UNION ALL
SELECT registry_version, 'applied' AS kind, run_id, phase, outcome, started_at, finished_at,
ROW_NUMBER() OVER (PARTITION BY registry_version ORDER BY finished_at DESC, run_id DESC) AS position
FROM product_runs WHERE config_id = ? AND registry_version IN ({0}) AND phase = 'applied'
) ranked WHERE position = 1"""
_SELECT_CONFIGURATION_VERSION = """SELECT config_id, registry_version, package_checksum, declared_content, created_at
FROM configuration_versions WHERE config_id = ? AND registry_version = ?"""
_CONFIGURATION_VERSION_EXISTS = "SELECT 1 FROM configuration_versions WHERE config_id = ? AND registry_version = ?"
_SELECT_CONFIGURATION_VERSION_BY_CHECKSUM = """SELECT config_id, registry_version, package_checksum, declared_content,
created_at FROM configuration_versions WHERE config_id = ? AND package_checksum = ?"""
_SELECT_CONFIGURATION_VERSIONS = """SELECT config_id, registry_version, package_checksum, declared_content, created_at
FROM configuration_versions WHERE config_id = ? ORDER BY registry_version"""
_SELECT_NEXT_CONFIGURATION_VERSION = (
    "SELECT COALESCE(MAX(registry_version) + 1, 1) FROM configuration_versions WHERE config_id = ?"
)

_INSERT_PRODUCT_RUN = """INSERT INTO product_runs (run_id, operation, configuration_reference, config_id,
registry_version, package_checksum, actor, audit_links, started_at, finished_at, phase, outcome, summary, results,
reconciliation_required, destination_branch, approved_by, approved_at, approved_checksum, approval_reason)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""
_PRODUCT_RUN_COLUMNS = """run_id, operation, configuration_reference, actor, audit_links, started_at,
finished_at, phase, outcome, summary, results, config_id, registry_version, package_checksum, reconciliation_required,
destination_branch, approved_by, approved_at, approved_checksum, approval_reason"""
_SELECT_PRODUCT_RUN = "SELECT " + _PRODUCT_RUN_COLUMNS + " FROM product_runs WHERE run_id = ?"  # noqa: S608 - module constants only.
# The run list is assembled from these fixed fragments only; every value is a bound parameter.
# The keyset predicate is the row comparison ``(started_at, run_id) < (?, ?)`` written out as an
# OR, which SQLite and PostgreSQL both evaluate identically over the UTC text timestamps.
_SELECT_PRODUCT_RUN_LIST = "SELECT " + _PRODUCT_RUN_COLUMNS + " FROM product_runs"  # noqa: S608 - module constants only.
_RUN_LIST_CONFIGURATION = "config_id = ?"
_RUN_LIST_VERSION = "registry_version = ?"
_RUN_LIST_AFTER = "(started_at < ? OR (started_at = ? AND run_id < ?))"
_RUN_LIST_ORDER = " ORDER BY started_at DESC, run_id DESC LIMIT ?"
_SELECT_AUDIT_LINKS_FOR_RUNS = (
    "SELECT run_id, event_id FROM audit_events WHERE run_id IN ({}) ORDER BY created_at, event_id"
)
_SELECT_RUN_ARTIFACT_REFERENCES = """SELECT run_id, artifact_id, kind, media_type, digest, size, object_key,
manifest_key, created_at, expires_at, published, visibility FROM artifact_refs
WHERE run_id = ? AND published = 1 AND visibility = 'public' ORDER BY artifact_id"""
_SELECT_ARTIFACT_REFERENCE = """SELECT run_id, artifact_id, kind, media_type, digest, size, object_key,
manifest_key, created_at, expires_at, published, visibility FROM artifact_refs WHERE run_id = ? AND artifact_id = ?"""
_SELECT_ARTIFACT_REFERENCE_BY_VISIBILITY = """SELECT run_id, artifact_id, kind, media_type, digest, size, object_key,
manifest_key, created_at, expires_at, published, visibility FROM artifact_refs
WHERE run_id = ? AND artifact_id = ? AND visibility = ?"""
_SELECT_CONFIGURATION_BASELINE = """SELECT config_id, source_row_counts, runs_since_full_extract, updated_at
FROM configuration_baselines WHERE config_id = ?"""
_UPSERT_WORKER_ENVIRONMENT = """INSERT INTO worker_environments (worker_id, credential_names, observed_at)
VALUES (?, ?, ?)
ON CONFLICT (worker_id) DO UPDATE SET credential_names = EXCLUDED.credential_names,
observed_at = EXCLUDED.observed_at"""
_SELECT_WORKER_ENVIRONMENTS = """SELECT worker_id, credential_names, observed_at FROM worker_environments
ORDER BY worker_id"""
_UPSERT_CONFIGURATION_BASELINE = """INSERT INTO configuration_baselines (config_id, source_row_counts,
runs_since_full_extract, updated_at) VALUES (?, ?, ?, ?)
ON CONFLICT (config_id) DO UPDATE SET source_row_counts = EXCLUDED.source_row_counts,
runs_since_full_extract = EXCLUDED.runs_since_full_extract, updated_at = EXCLUDED.updated_at"""
_INSERT_ARTIFACT_REFERENCE = """INSERT INTO artifact_refs (run_id, artifact_id, kind, media_type, digest, size,
object_key, manifest_key, created_at, expires_at, published, visibility)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""
_SELECT_PREFECT_EXECUTIONS = """SELECT run_id, flow_run_id, deployment_id, purpose, attempt, last_observed_state,
last_observed_at, submitted_at, claimed_at, claiming_worker_id, stalled_at, cancellation_requested_at,
cancellation_recovery_deadline_at, cancellation_receipt_id, cancellation_acknowledged_at, terminal_at, terminal_state,
terminal_outcome, position, source_read, destination_read, apply_progress FROM prefect_executions WHERE run_id = ?
ORDER BY position"""
_INSERT_PREFECT_EXECUTION = """INSERT INTO prefect_executions (run_id, flow_run_id, deployment_id, purpose, attempt,
last_observed_state, last_observed_at, submitted_at, claimed_at, claiming_worker_id, stalled_at,
cancellation_requested_at, cancellation_recovery_deadline_at, cancellation_receipt_id, cancellation_acknowledged_at,
terminal_at, terminal_state, terminal_outcome, position, mutation_receipt_id, source_read, destination_read,
apply_progress) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""
_INSERT_MUTATION_RECEIPT = """INSERT INTO mutation_receipts (receipt_id, actor, key_digest, operation,
target_run_id, request_fingerprint, reason, resource_kind, resource_id, run_id, prefect_key, state, response_status, response_body,
flow_run_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""
_SELECT_MUTATION_RECEIPT = """SELECT receipt_id, actor, key_digest, operation, target_run_id,
request_fingerprint, reason, resource_kind, resource_id, run_id, prefect_key, state, response_status, response_body, flow_run_id,
created_at, updated_at FROM mutation_receipts WHERE actor = ? AND key_digest = ?"""
# The receipt of one operation that holds a flow run ID; the oldest wins if two ever do.
_SELECT_MUTATION_RECEIPT_FOR_EXECUTION = """SELECT receipt_id, actor, key_digest, operation, target_run_id,
request_fingerprint, reason, resource_kind, resource_id, run_id, prefect_key, state, response_status, response_body, flow_run_id,
created_at, updated_at FROM mutation_receipts WHERE run_id = ? AND flow_run_id = ? AND operation = ?
ORDER BY created_at, receipt_id"""
_INSERT_AUDIT_EVENT = """INSERT INTO audit_events (event_id, run_id, actor, operation, reason, outcome, created_at,
event_type, config_id, registry_version, branch) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""
_INSERT_AUDIT_EVENT_RESOURCE = "INSERT INTO audit_event_resources (event_id, kind, resource_id) VALUES (?, ?, ?)"
_AUDIT_EVENT_COLUMNS = (
    "event_id, run_id, actor, operation, reason, outcome, created_at, event_type, config_id, registry_version, branch"
)
_SELECT_AUDIT_EVENTS = f"SELECT {_AUDIT_EVENT_COLUMNS} FROM audit_events WHERE run_id = ? ORDER BY created_at, event_id"  # noqa: S608
_SELECT_ALL_AUDIT_EVENTS = f"SELECT {_AUDIT_EVENT_COLUMNS} FROM audit_events ORDER BY created_at, event_id"  # noqa: S608
_SELECT_AUDIT_EVENT_RESOURCES = "SELECT event_id, kind, resource_id FROM audit_event_resources WHERE event_id IN ({})"
# An event names one related resource; bound to (kind, resource_id).
_EVENT_HAS_RESOURCE = (
    "EXISTS (SELECT 1 FROM audit_event_resources AS resource WHERE resource.event_id = audit_events.event_id "
    "AND resource.kind = ? AND resource.resource_id = ?)"
)
# Event IDs read per related-resource query, well under SQLite's bound-parameter limit.
_EVENT_RESOURCE_BATCH = 500
# The columns spec 017 adds to an audit event; all nullable, so a row written before the change stays valid.
_AUDIT_EVENT_ADDED_COLUMNS = (
    ("event_type", "TEXT"),
    ("config_id", "TEXT"),
    ("registry_version", "INTEGER"),
    ("branch", "TEXT"),
)
_CONFIGURATION_OPERATIONS = "('register-config', 'create-config-version')"
# The type of an event recorded before spec 017, from its (operation, outcome) pair (data-model.md, "Event types").
# A request refused for a missing or invalid token keeps no type, so the event list never returns it (research R6).
# Those rows are left out of the update: they stay NULL for good, and rewriting every one of them on
# each start-up would cost more as refused requests accumulate.
_BACKFILL_AUDIT_EVENT_TYPES = f"""UPDATE audit_events SET event_type = CASE
WHEN operation = 'register-config' AND outcome = 'accepted' THEN 'infrahub_sync.config.registered'
WHEN operation = 'create-config-version' AND outcome = 'accepted' THEN 'infrahub_sync.config.version_registered'
WHEN operation IN {_CONFIGURATION_OPERATIONS} AND substr(outcome, 1, 8) = 'refused-' THEN 'infrahub_sync.config.refused'
WHEN operation IN {_CONFIGURATION_OPERATIONS} AND outcome = 'replayed' THEN 'infrahub_sync.config.replayed'
WHEN operation IN {_CONFIGURATION_OPERATIONS} AND outcome = 'unavailable' THEN 'infrahub_sync.config.unavailable'
WHEN outcome = 'accepted' THEN 'infrahub_sync.run.accepted'
WHEN outcome = 'replayed' THEN 'infrahub_sync.run.replayed'
WHEN substr(outcome, 1, 8) = 'refused-' THEN 'infrahub_sync.run.refused'
WHEN outcome = 'unavailable' THEN 'infrahub_sync.run.unavailable'
END WHERE event_type IS NULL AND outcome <> 'refused-authentication'"""  # noqa: S608 - module-owned literals only.
# The actor's account is the one related resource an event recorded before spec 017 can name.
_BACKFILL_AUDIT_EVENT_ACCOUNTS = """INSERT INTO audit_event_resources (event_id, kind, resource_id)
SELECT event_id, 'account', actor FROM audit_events WHERE NOT EXISTS (
SELECT 1 FROM audit_event_resources AS resource
WHERE resource.event_id = audit_events.event_id AND resource.kind = 'account')"""
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
    """A product run cannot admit another execution while a cancellation is in flight."""


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
        self, run_id: str, artifact_id: str, *, visibility: Literal["public", "internal"] | None = None
    ) -> LookupResult[tuple[ArtifactReference, bool]]: ...

    def has_pending_artifacts(self, run_id: str) -> bool: ...

    def lookup_configuration_baseline(self, config_id: str) -> LookupResult[ConfigurationBaseline]: ...

    def publish_worker_environment(self, environment: WorkerEnvironment) -> None: ...

    def worker_environments(self) -> tuple[WorkerEnvironment, ...]: ...

    def add_prefect_execution(
        self, run_id: str, link: PrefectExecutionLink, *, receipt_id: str, allocate_attempt: bool = False
    ) -> PrefectExecutionLink: ...

    def observe_prefect_execution(
        self, run_id: str, flow_run_id: str, *, state: str | None, observed_at: datetime
    ) -> None: ...

    def record_side_reads(self, run_id: str, flow_run_id: str, documents: Mapping[Side, Mapping[str, Any]]) -> bool: ...

    def record_apply_progress(self, run_id: str, flow_run_id: str, document: ApplyProgress) -> ApplyProgressSave: ...

    def run_is_resumable(self, run_id: str) -> bool: ...

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

    def interrupt_execution(
        self, run_id: str, flow_run_id: str, *, terminal_at: datetime, event: AuditEvent | None = None
    ) -> bool: ...

    def commit_claimed_execution(
        self,
        run_id: str,
        flow_run_id: str,
        *,
        worker_id: str,
        terminal_at: datetime,
        terminal_state: Literal["completed", "failed", "interrupted"],
        terminal_outcome: Literal["succeeded", "failed", "ambiguous"],
        writeback: ExecutionWriteback,
        event: AuditEvent | None = None,
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
        event: AuditEvent | None = None,
        refusal_events: Mapping[int, AuditEvent] | None = None,
    ) -> bool: ...

    def cancel_execution(
        self, run_id: str, flow_run_id: str, *, terminal_at: datetime, event: AuditEvent | None = None
    ) -> bool: ...

    def expire_execution_cancellation(self, run_id: str, flow_run_id: str, *, terminal_at: datetime) -> bool: ...

    def pending_executions(self) -> tuple[tuple[str, PrefectExecutionLink], ...]: ...

    def reserve_mutation(
        self, receipt: MutationReceipt, run: ProductRun | None, *, admit_write: bool = False
    ) -> tuple[MutationReceipt, bool]: ...

    def reserve_resume(self, run_id: str, receipt: MutationReceipt) -> tuple[MutationReceipt, bool]: ...

    def lookup_mutation(self, actor: str, key_digest: str) -> LookupResult[MutationReceipt]: ...

    def record_approval(
        self,
        run_id: str,
        approval: RunApproval,
        receipt: MutationReceipt,
        response: Mapping[str, Any],
        *,
        updated_at: datetime,
        events: Sequence[AuditEvent] = (),
    ) -> bool: ...

    def cancel_planned_run(  # pylint: disable=too-many-arguments
        self,
        run_id: str,
        receipt: MutationReceipt,
        response: Mapping[str, Any],
        *,
        cancelled_at: datetime,
        updated_at: datetime,
        events: Sequence[AuditEvent] = (),
    ) -> bool: ...

    def lookup_mutation_for_execution(
        self, run_id: str, flow_run_id: str, operation: str
    ) -> LookupResult[MutationReceipt]: ...

    def complete_mutation(
        self,
        receipt_id: str,
        *,
        response_status: int,
        response_body: Mapping[str, Any],
        flow_run_id: str | None,
        updated_at: datetime,
        event: AuditEvent | None = None,
    ) -> MutationReceipt: ...

    def claim_mutation(self, receipt_id: str, *, updated_at: datetime) -> bool: ...

    def release_mutation(self, receipt_id: str, *, updated_at: datetime) -> None: ...

    def record_audit(self, event: AuditEvent) -> None: ...

    def audit_events(self, run_id: str | None = None) -> tuple[AuditEvent, ...]: ...

    def list_events(self, filters: EventFilters, *, limit: int, offset: int) -> EventPage: ...

    def configuration_exists(self, config_id: str) -> bool: ...

    def create_configuration(
        self,
        package: ConfigurationPackage,
        *,
        registered_by: str | None = None,
        registration_reason: str | None = None,
    ) -> ConfigurationVersion: ...

    def add_configuration_version(
        self,
        config_id: str,
        package: ConfigurationPackage,
        *,
        registered_by: str | None = None,
        registration_reason: str | None = None,
    ) -> tuple[ConfigurationVersion, bool]: ...

    def lookup_configuration(self, config_id: str) -> LookupResult[ConfigurationSummary]: ...

    def lookup_configuration_version(
        self, config_id: str, registry_version: int
    ) -> LookupResult[ConfigurationVersion]: ...

    def configuration_version_exists(self, config_id: str, registry_version: int) -> bool: ...

    def list_configurations(self) -> tuple[ConfigurationSummary, ...]: ...

    def list_configuration_versions(self, config_id: str) -> tuple[ConfigurationVersion, ...]: ...

    def list_configuration_page(
        self, *, name: str | None = None, after: tuple[datetime, str] | None = None, limit: int = DEFAULT_LIMIT
    ) -> ConfigurationPage: ...

    def list_configuration_version_page(
        self, config_id: str, *, after: int | None = None, limit: int = DEFAULT_LIMIT
    ) -> ConfigurationVersionPage: ...

    def list_runs(
        self, filters: RunFilters, *, after: tuple[datetime, str] | None = None, limit: int = DEFAULT_LIMIT
    ) -> RunPage: ...

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
        initialization_retry_codes: frozenset[str] = frozenset(),
    ) -> None:
        self._connect = connect
        self._placeholder = placeholder
        self._dialect = dialect
        self._initialization_retry_codes = initialization_retry_codes
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
                    if self._dialect == "postgresql":
                        cursor.execute(_POSTGRESQL_SCHEMA_INITIALIZATION_LOCK)
                    for statement in _SCHEMA.split(";"):
                        if statement.strip():
                            cursor.execute(statement)
                    added = self._migrate_added_columns(cursor)
                    if ("configurations", "name") in added:
                        self._fill_configuration_names(cursor)
                    self._ensure_run_listing_indexes(cursor)
                    self._migrate_prefect_execution_columns(cursor)
                    self._migrate_mutation_receipts(cursor)
                    self._migrate_audit_events(cursor)
                    self._ensure_mutation_receipt_resource_constraint(cursor)
                    self._ensure_configuration_binding_constraint(cursor)
                    connection.commit()
                except Exception as exc:  # pylint: disable=broad-exception-caught
                    # Synchronous DB-API drivers do not share a common error base; retry only
                    # the PostgreSQL SQLSTATEs in `initialization_retry_codes` (duplicate
                    # catalog entries and a deadlock) and duplicate-column races from a
                    # concurrent ALTER TABLE ADD COLUMN on either backend.
                    connection.rollback()
                    if _sqlstate(exc) not in self._initialization_retry_codes and not _is_duplicate_column_error(exc):
                        raise
                    if attempt + 1 == _SCHEMA_INITIALIZATION_ATTEMPTS:
                        raise
                else:
                    return
                finally:
                    cursor.close()
            finally:
                connection.close()

    def _read_table_columns(self, cursor: _Cursor, table: str) -> frozenset[str]:
        """Return the existing columns on ``table`` for this store's own dialect.

        The statement is selected from ``self._dialect``, never discovered by running a
        statement to see whether it fails: on PostgreSQL, a probe-driven failure would abort
        the surrounding transaction and force a ``rollback()`` that discards every other
        uncommitted ``CREATE TABLE`` this same call still needs to commit. ``table`` is always
        one of this module's own table names, never caller input.
        """
        if self._dialect == "sqlite":
            cursor.execute(f"PRAGMA table_info({table})")
            return frozenset(str(row[1]) for row in cursor.fetchall())
        cursor.execute(
            self._sql(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = ? AND table_schema = current_schema()"
            ),
            (table,),
        )
        return frozenset(str(row[0]) for row in cursor.fetchall())

    def _migrate_added_columns(self, cursor: _Cursor) -> frozenset[tuple[str, str]]:
        """Additively bring pre-existing tables up to the current nullable column set.

        Covers ``product_runs`` (configuration binding, ``destination_branch`` and approval),
        ``configuration_versions`` (``registered_by``, ``registration_reason``) and
        ``configurations`` (``name``). Every column is nullable, so no existing row needs a
        value, and a column the catalog already lists is skipped. Returns the
        ``(table, column)`` pairs this call added.
        """
        added: set[tuple[str, str]] = set()
        for table, columns in _ADDED_NULLABLE_COLUMNS:
            existing = self._read_table_columns(cursor, table)
            for column, sql_type in columns:
                if column not in existing:
                    cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {sql_type}")
                    added.add((table, column))
        return frozenset(added)

    def _fill_configuration_names(self, cursor: _Cursor) -> None:
        """Fill a NULL ``configurations.name`` from the configuration's latest version's package.

        Runs only in the start-up that adds the column, because every configuration
        registered after that writes its name. Covers configurations registered before the
        column existed. A package that declares
        no name, or whose stored content does not decode, leaves the name NULL; content that
        does not decode is logged as a warning with the ``config_id`` only. Only NULL names are
        written, so a second start-up changes nothing.
        """
        cursor.execute(self._sql(_SELECT_UNNAMED_CONFIGURATION_PACKAGES))
        for config_id, declared_content in cursor.fetchall():
            try:
                content = json.loads(declared_content)
            except (TypeError, ValueError):
                # Never log the content: a stored package can hold credentials.
                logger.warning("Configuration %s: the stored package does not decode; its name stays unset", config_id)
                continue
            name = _declared_configuration_name(content)
            if name is not None:
                cursor.execute(self._sql(_FILL_CONFIGURATION_NAME), (name, config_id))

    @staticmethod
    def _ensure_run_listing_indexes(cursor: _Cursor) -> None:
        """Create the run-listing indexes once the binding columns are guaranteed to exist."""
        for statement in _RUN_LISTING_INDEXES:
            cursor.execute(statement)

    def _migrate_prefect_execution_columns(self, cursor: _Cursor) -> None:
        """Add liveness, read-state and apply-progress columns without fabricating values for legacy links."""
        existing = self._read_table_columns(cursor, "prefect_executions")
        for column, sql_type in (
            *_PREFECT_EXECUTION_LIVENESS_COLUMNS,
            *_PREFECT_EXECUTION_READ_COLUMNS,
            *_PREFECT_EXECUTION_PROGRESS_COLUMNS,
        ):
            if column not in existing:
                cursor.execute(f"ALTER TABLE prefect_executions ADD COLUMN {column} {sql_type}")

    def _column_nullability(self, cursor: _Cursor, table: str) -> dict[str, str]:
        """Return each column of ``table`` mapped to ``"YES"`` (nullable) or ``"NO"``.

        SQLite reports ``notnull`` through ``PRAGMA table_info``; PostgreSQL answers from
        ``information_schema``, whose rows ``_catalog_nullability`` refuses unless each one is
        exactly readable.
        """
        if self._dialect == "sqlite":
            cursor.execute(f"PRAGMA table_info({table})")
            return {str(row[1]): "NO" if row[3] else "YES" for row in cursor.fetchall()}
        cursor.execute(
            self._sql(
                "SELECT column_name, is_nullable FROM information_schema.columns "
                "WHERE table_name = ? AND table_schema = current_schema()"
            ),
            (table,),
        )
        return _catalog_nullability(cursor.fetchall())

    def _migrate_mutation_receipts(self, cursor: _Cursor) -> None:
        """Add resource identity columns and preserve every legacy receipt in place."""
        column_nullability = self._column_nullability(cursor, "mutation_receipts")
        if "resource_kind" not in column_nullability:
            cursor.execute("ALTER TABLE mutation_receipts ADD COLUMN resource_kind TEXT")
        if "resource_id" not in column_nullability:
            cursor.execute("ALTER TABLE mutation_receipts ADD COLUMN resource_id TEXT")
        cursor.execute(
            "UPDATE mutation_receipts SET resource_kind = 'run', resource_id = run_id "
            "WHERE resource_kind IS NULL OR resource_id IS NULL"
        )
        required = [column for column in ("run_id", "prefect_key", "reason") if column_nullability.get(column) == "NO"]
        if not required:
            return
        if self._dialect == "sqlite":
            # SQLite has no supported additive ALTER that removes NOT NULL. The table has a
            # fixed, repository-owned legacy shape and a rebuild would also reconstruct the
            # dependent write_admissions foreign key. Updating these three declarations is the
            # only in-place migration that preserves rows, indexes, and references.
            cursor.execute("PRAGMA writable_schema = ON")
            cursor.execute(
                "UPDATE sqlite_master SET sql = REPLACE(REPLACE(REPLACE(sql, 'run_id TEXT NOT NULL', 'run_id TEXT'), "
                "'prefect_key TEXT NOT NULL', 'prefect_key TEXT'), 'reason TEXT NOT NULL', 'reason TEXT') "
                "WHERE type = 'table' AND name = 'mutation_receipts'"
            )
            cursor.execute("PRAGMA writable_schema = OFF")
        else:
            for column in required:
                cursor.execute(f"ALTER TABLE mutation_receipts ALTER COLUMN {column} DROP NOT NULL")

    def _migrate_audit_events(self, cursor: _Cursor) -> None:
        """Bring legacy audit events up to the typed event, preserving every event in place.

        Spec 017 adds the nullable ``event_type``, ``config_id``, ``registry_version`` and
        ``branch`` columns, fills ``event_type`` from each row's ``(operation, outcome)`` pair
        and writes one ``account`` resource per event. A legacy row names no configuration or
        branch, because the old record did not hold them.

        Both fills run on every start-up, not only the one that adds the columns: SQLite
        commits ``ALTER TABLE`` at once, so a start-up that failed after adding the columns
        would otherwise leave the rows untyped for good. Each fill touches only rows it has
        not filled yet, so a later start-up changes nothing.
        """
        existing = self._read_table_columns(cursor, "audit_events")
        for column, sql_type in _AUDIT_EVENT_ADDED_COLUMNS:
            if column not in existing:
                cursor.execute(f"ALTER TABLE audit_events ADD COLUMN {column} {sql_type}")
        cursor.execute(_BACKFILL_AUDIT_EVENT_TYPES)
        cursor.execute(_BACKFILL_AUDIT_EVENT_ACCOUNTS)
        self._allow_audit_events_without_reason(cursor)

    def _allow_audit_events_without_reason(self, cursor: _Cursor) -> None:
        """Allow an audit event without a reason, preserving every legacy event in place.

        A request may omit its audit reason, and the event then stores NULL rather than a
        made-up text. Legacy tables declared ``reason TEXT NOT NULL``; this drops that
        constraint the same way ``_migrate_mutation_receipts`` does, and does nothing on a
        table that already allows NULL.
        """
        if self._column_nullability(cursor, "audit_events").get("reason") != "NO":
            return
        if self._dialect == "sqlite":
            # The same in-place declaration update as ``_migrate_mutation_receipts``, for
            # the same reason: SQLite has no ALTER that removes NOT NULL.
            cursor.execute("PRAGMA writable_schema = ON")
            cursor.execute(
                "UPDATE sqlite_master SET sql = REPLACE(sql, 'reason TEXT NOT NULL', 'reason TEXT') "
                "WHERE type = 'table' AND name = 'audit_events'"
            )
            cursor.execute("PRAGMA writable_schema = OFF")
            return
        cursor.execute("ALTER TABLE audit_events ALTER COLUMN reason DROP NOT NULL")

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

        The PostgreSQL query is schema-qualified for the same reason ``_read_table_columns``
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
                    # A link embedded in the run being created has no receipt to own it:
                    # `reserve_mutation` is what mints one, and it runs against a run that
                    # already exists. Stated rather than defaulted so a caller that does
                    # have a receipt cannot append without naming it.
                    self._insert_prefect_execution(cursor, run.run_id, link, position, mutation_receipt_id=None)
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
            self._insert_prefect_execution(cursor, run.run_id, link, position, mutation_receipt_id=None)

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
                # Stored as UTC so that text order equals time order when runs are listed,
                # even when an injected service clock is not UTC.
                _utc_iso(run.started_at),
                None if run.finished_at is None else _utc_iso(run.finished_at),
                run.phase,
                run.outcome,
                _json(run.summary),
                _json(run.results),
                run.reconciliation_required,
                run.destination_branch,
                *_approval_columns(run.approval),
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

    def lookup_artifact_reference(
        self, run_id: str, artifact_id: str, *, visibility: Literal["public", "internal"] | None = None
    ) -> LookupResult[tuple[ArtifactReference, bool]]:
        """Resolve one reference, optionally only when it carries `visibility`."""
        connection = self._connect()
        try:
            cursor = connection.cursor()
            try:
                if visibility is None:
                    cursor.execute(self._sql(_SELECT_ARTIFACT_REFERENCE), (run_id, artifact_id))
                else:
                    cursor.execute(
                        self._sql(_SELECT_ARTIFACT_REFERENCE_BY_VISIBILITY),
                        (run_id, artifact_id, visibility),
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
        receipt_id: str,
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
                    self._require_append_owner(cursor, run_id, receipt_id, purpose=link.purpose)
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
                    self._insert_prefect_execution(
                        cursor, run_id, allocated_link, position, mutation_receipt_id=receipt_id
                    )
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

    def _require_append_owner(self, cursor: _Cursor, run_id: str, receipt_id: str, *, purpose: str) -> None:
        """Refuse an execution append that no unresolved receipt of this run owns.

        A receipt is unresolved until it carries a stored response, refusals included, so
        the append belongs to a submission still in flight, and it appends at most once. A
        write append additionally requires the run's write-admission row to name this exact
        receipt and this exact operation: the admission is what one request won, and nothing
        else may spend it. A run holding a write admission accepts no read append, except the
        ``verify`` a resumable run admitted (spec 022, research R5): its receipt must be a
        ``verify`` receipt, and the run must still be resumable under the lock the caller holds.
        A ``resume`` append requires a ``resume_admissions`` row naming this exact receipt.
        """
        cursor.execute(
            self._sql("SELECT 1 FROM mutation_receipts WHERE receipt_id = ? AND run_id = ? AND state <> 'accepted'"),
            (receipt_id, run_id),
        )
        if cursor.fetchone() is None:
            raise ValueError(_UNRESOLVED_APPEND_REFUSED)
        cursor.execute(
            self._sql("SELECT 1 FROM prefect_executions WHERE mutation_receipt_id = ?"),
            (receipt_id,),
        )
        if cursor.fetchone() is not None:
            raise ValueError(_REPEATED_APPEND_REFUSED)
        if purpose == "resume":
            cursor.execute(
                self._sql("SELECT 1 FROM resume_admissions WHERE run_id = ? AND receipt_id = ?"), (run_id, receipt_id)
            )
            if cursor.fetchone() is None:
                raise ValueError(_RESUMED_APPEND_REFUSED)
            return
        cursor.execute(self._sql("SELECT receipt_id, operation FROM write_admissions WHERE run_id = ?"), (run_id,))
        admission = cursor.fetchone()
        if purpose in _WRITE_OPERATIONS:
            if admission is None or (str(admission[0]), str(admission[1])) != (receipt_id, purpose):
                raise ValueError(_ADMITTED_APPEND_REFUSED)
            return
        if admission is not None and not (
            purpose == "verify"
            and self._receipt_operation(cursor, receipt_id) == "verify"
            and self._run_is_resumable(cursor, run_id)
        ):
            raise ValueError(_ADMITTED_APPEND_REFUSED)

    def _receipt_operation(self, cursor: _Cursor, receipt_id: str) -> str | None:
        cursor.execute(self._sql("SELECT operation FROM mutation_receipts WHERE receipt_id = ?"), (receipt_id,))
        row = cursor.fetchone()
        return None if row is None else str(row[0])

    def _insert_prefect_execution(
        self,
        cursor: _Cursor,
        run_id: str,
        link: PrefectExecutionLink,
        position: int,
        *,
        mutation_receipt_id: str | None,
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
                mutation_receipt_id,
                None if link.source_read is None else _json(link.source_read),
                None if link.destination_read is None else _json(link.destination_read),
                None if link.apply_progress is None else _json(link.apply_progress.model_dump(mode="json")),
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

    def record_side_reads(self, run_id: str, flow_run_id: str, documents: Mapping[Side, Mapping[str, Any]]) -> bool:
        """Store one or both sides' read documents in one write while the execution is not terminal.

        Returns whether a row changed. A write that arrives after the execution ended, for
        example from a worker that kept running after it was marked interrupted, changes
        nothing. Each side selects a column, so the sides are checked before any SQL runs.
        """
        if not documents:
            msg = "record_side_reads needs at least one side"
            raise ValueError(msg)
        columns = [_side_read_column(side) for side in documents]
        for side, document in documents.items():
            _check_side_read_document(side, document)
        assignments = ", ".join(f"{column} = ?" for column in columns)
        return self._execution_update(
            f"UPDATE prefect_executions SET {assignments} "  # noqa: S608 - columns from a fixed two-name map
            "WHERE run_id = ? AND flow_run_id = ? AND terminal_at IS NULL",
            (*(_json(dict(document)) for document in documents.values()), run_id, flow_run_id),
        )

    def record_apply_progress(self, run_id: str, flow_run_id: str, document: ApplyProgress) -> ApplyProgressSave:
        """Store a write execution's progress marker while the execution is not terminal.

        Returns `saved` when the row changed. A write that arrives after the execution ended,
        for example from a worker that kept running after liveness marked it interrupted,
        changes nothing and returns `ended`, so the worker can stop at once (spec 022, research
        R2 and R4); a write to an execution the run does not have returns `unknown`. The
        document is validated again before any SQL runs, so a caller that bypassed the model's
        checks cannot store another shape.

        The update and the read that explains a refused update share one transaction. An
        execution's `terminal_at` is only ever set, never cleared, so `ended` stays true once read.
        """
        checked = ApplyProgress.model_validate(document.model_dump())
        connection = self._connect()
        try:
            cursor = connection.cursor()
            try:
                cursor.execute(
                    self._sql(
                        "UPDATE prefect_executions SET apply_progress = ? "
                        "WHERE run_id = ? AND flow_run_id = ? AND terminal_at IS NULL"
                    ),
                    (_json(checked.model_dump(mode="json")), run_id, flow_run_id),
                )
                answer: ApplyProgressSave = "saved"
                if cursor.rowcount != 1:
                    cursor.execute(
                        self._sql("SELECT 1 FROM prefect_executions WHERE run_id = ? AND flow_run_id = ?"),
                        (run_id, flow_run_id),
                    )
                    answer = "unknown" if cursor.fetchone() is None else "ended"
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            else:
                return answer
            finally:
                cursor.close()
        finally:
            connection.close()

    def run_is_resumable(self, run_id: str) -> bool:
        """Whether the run is resumable: see `_run_is_resumable` (spec 022 data-model.md "State rules")."""
        connection = self._connect()
        try:
            cursor = connection.cursor()
            try:
                return self._run_is_resumable(cursor, run_id)
            finally:
                cursor.close()
        finally:
            connection.close()

    def _run_is_resumable(self, cursor: _Cursor, run_id: str) -> bool:
        """Whether the run's write was interrupted and nothing holds the run now.

        A run is resumable when `reconciliation_required` is true, its phase is not
        `cancelled`, none of its executions is unfinished, and its plan is published (spec
        022 data-model.md "State rules", research R1 and R14). A `sync` whose worker was
        ended while it was still planning requires reconciliation, because a worker that
        liveness ended can still publish its plan and write; until that plan exists the run
        wrote nothing and there is nothing to resume.
        """
        cursor.execute(self._sql(_SELECT_RESUMABLE_RUN), (run_id, run_id, run_id, PLAN_ARTIFACT_ID))
        return cursor.fetchone() is not None

    def _plan_published(self, cursor: _Cursor, run_id: str) -> bool:
        """Whether the run's plan review document is published (see `_run_is_resumable`)."""
        cursor.execute(self._sql(_SELECT_PLAN_PUBLISHED), (run_id, PLAN_ARTIFACT_ID))
        return cursor.fetchone() is not None

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

    def interrupt_execution(
        self, run_id: str, flow_run_id: str, *, terminal_at: datetime, event: AuditEvent | None = None
    ) -> bool:
        """Atomically terminalize an orphaned claimed execution and its product run, with its event."""
        return self._terminalize_execution(
            run_id,
            flow_run_id,
            terminal_at=terminal_at,
            claimed=True,
            terminal_state="interrupted",
            terminal_outcome="ambiguous",
            phase="interrupted",
            outcome="ambiguous",
            event=event,
        )

    def commit_claimed_execution(
        self,
        run_id: str,
        flow_run_id: str,
        *,
        worker_id: str,
        terminal_at: datetime,
        terminal_state: Literal["completed", "failed", "interrupted"],
        terminal_outcome: Literal["succeeded", "failed", "ambiguous"],
        writeback: ExecutionWriteback,
        event: AuditEvent | None = None,
    ) -> bool:
        """Commit one claimed worker verdict, its business writeback and its event together."""
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
                            _utc_iso(writeback.finished_at),
                            _json(writeback.summary),
                            _json(writeback.results),
                            run_id,
                            flow_run_id,
                        ),
                    )
                    # Only a write that actually succeeded may become the next run's
                    # safety reference. A failed or ambiguous terminal leaves the previous
                    # baseline standing, because it is still the last state known good.
                    if writeback.baseline is not None and (terminal_state, terminal_outcome) == (
                        "completed",
                        "succeeded",
                    ):
                        self._record_configuration_baseline(cursor, run_id, writeback.baseline, terminal_at)
                    # A `sync` that stopped after its plan, before any dispatch, gives back
                    # its write admission in this same transaction, so the team can apply
                    # the reviewed plan once (spec 020 FR-012, ADR 0020).
                    if _releases_write_admission(writeback, terminal_state, terminal_outcome):
                        cursor.execute(self._sql(_RELEASE_STOPPED_SYNC_ADMISSION), (run_id, run_id))
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
                self._record_write_ambiguity(
                    cursor,
                    run_id,
                    flow_run_id,
                    terminal_state=terminal_state,
                    terminal_outcome=terminal_outcome,
                )
                self._settle_resume(
                    cursor, run_id, flow_run_id, terminal_state=terminal_state, terminal_outcome=terminal_outcome
                )
                if event is not None:
                    self._insert_event(cursor, event)
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

    def _record_configuration_baseline(
        self,
        cursor: _Cursor,
        run_id: str,
        baseline: BaselineWriteback,
        updated_at: datetime,
    ) -> None:
        """Replace the run's configuration baseline inside the caller's transaction.

        The counts replace rather than merge: the baseline describes one run's source, so
        carrying forward a resource this configuration no longer extracts would let the
        next run's guardrail compare against something nothing produces any more.
        """
        cursor.execute(self._sql("SELECT config_id FROM product_runs WHERE run_id = ?"), (run_id,))
        run_row = cursor.fetchone()
        if run_row is None or run_row[0] is None:
            # A run with no registered configuration has nothing to scope a baseline to.
            return
        config_id = str(run_row[0])
        cursor.execute(self._sql(_SELECT_CONFIGURATION_BASELINE), (config_id,))
        stored = cursor.fetchone()
        previous = 0 if stored is None else int(stored[2])
        runs_since_full_extract = 0 if baseline.full_extract else previous + 1
        cursor.execute(
            self._sql(_UPSERT_CONFIGURATION_BASELINE),
            (
                config_id,
                _json(baseline.source_row_counts),
                runs_since_full_extract,
                updated_at.isoformat(),
            ),
        )

    def publish_worker_environment(self, environment: WorkerEnvironment) -> None:
        """Replace one worker's published credential variable names (spec 020 FR-035)."""
        connection = self._connect()
        try:
            cursor = connection.cursor()
            try:
                cursor.execute(
                    self._sql(_UPSERT_WORKER_ENVIRONMENT),
                    (
                        environment.worker_id,
                        _json(list(environment.credential_names)),
                        environment.observed_at.isoformat(),
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

    def worker_environments(self) -> tuple[WorkerEnvironment, ...]:
        """Return every worker's last published credential variable names, by worker."""
        connection = self._connect()
        try:
            cursor = connection.cursor()
            try:
                cursor.execute(self._sql(_SELECT_WORKER_ENVIRONMENTS))
                rows = cursor.fetchall()
            finally:
                cursor.close()
        finally:
            connection.close()
        return tuple(
            WorkerEnvironment.model_validate(
                {
                    "worker_id": row[0],
                    "credential_names": tuple(_JSON_LIST_ADAPTER.validate_json(str(row[1]))),
                    "observed_at": row[2],
                }
            )
            for row in rows
        )

    def lookup_configuration_baseline(self, config_id: str) -> LookupResult[ConfigurationBaseline]:
        """Return the configuration's durable safety reference, if a write has set one."""
        connection = self._connect()
        try:
            cursor = connection.cursor()
            try:
                cursor.execute(self._sql(_SELECT_CONFIGURATION_BASELINE), (config_id,))
                row = cursor.fetchone()
            finally:
                cursor.close()
        finally:
            connection.close()
        if row is None:
            return LookupResult(value=None, reason="configuration-baseline-not-found")
        return LookupResult(
            value=ConfigurationBaseline.model_validate(
                {
                    "config_id": row[0],
                    "source_row_counts": _JSON_MAPPING_ADAPTER.validate_json(str(row[1])),
                    "runs_since_full_extract": row[2],
                    "updated_at": row[3],
                }
            )
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
        event: AuditEvent | None = None,
        refusal_events: Mapping[int, AuditEvent] | None = None,
    ) -> bool:
        """Persist remote acknowledgement, the replay result and its event in one transaction.

        ``event`` is written only when the acknowledgement is accepted. When it is turned into
        an ambiguous (409) or unconfirmed (503) refusal, the event of ``refusal_events`` for
        that status is written instead, in the transaction that stores the refusal (FR-014).
        """
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
                        "cancellation_acknowledged_at, purpose FROM prefect_executions "
                        "WHERE run_id = ? AND flow_run_id = ?"
                    ),
                    (run_id, flow_run_id),
                )
                row = _require_cancellation_receipt_row(cursor.fetchone())
                if row[3] is not None:
                    connection.commit()
                    return False
                receipt_id = str(row[2])
                claimed = row[0] is not None
                # A claimed apply or sync may already have reached the destination, so
                # acknowledgement cannot promise a clean stop however early it arrives.
                claimed_write = claimed and str(row[4]) in WRITE_PURPOSES
                if claimed_write or acknowledged_at >= datetime.fromisoformat(str(row[1])):
                    state, outcome, phase = (
                        ("interrupted", "ambiguous", "interrupted")
                        if claimed
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
                        (phase, outcome, _utc_iso(acknowledged_at), run_id, flow_run_id),
                    )
                    self._record_write_ambiguity(
                        cursor, run_id, flow_run_id, terminal_state=state, terminal_outcome=outcome
                    )
                    self._keep_interrupted_run_resumable(cursor, run_id)
                    refusal = (
                        _cancellation_ambiguous_response(run_id, receipt_id)
                        if claimed_write
                        else _cancellation_unconfirmed_response(run_id, receipt_id)
                    )
                    refusal_status = int(refusal["error"]["status"])
                    self._complete_receipt_row(
                        cursor,
                        receipt_id,
                        response_status=refusal_status,
                        response_body=refusal,
                        flow_run_id=flow_run_id,
                        updated_at=acknowledged_at,
                    )
                    refusal_event = (refusal_events or {}).get(refusal_status)
                    if refusal_event is not None:
                        self._insert_event(cursor, refusal_event)
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
                if event is not None:
                    self._insert_event(cursor, event)
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

    def cancel_execution(
        self, run_id: str, flow_run_id: str, *, terminal_at: datetime, event: AuditEvent | None = None
    ) -> bool:
        """Commit clean cancellation, and its event, only from acknowledged durable cancelled observation."""
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
                    (_utc_iso(terminal_at), run_id, flow_run_id),
                )
                self._keep_interrupted_run_resumable(cursor, run_id)
                if event is not None:
                    self._insert_event(cursor, event)
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
                    (phase, outcome, _utc_iso(terminal_at), run_id, flow_run_id),
                )
                self._record_write_ambiguity(
                    cursor, run_id, flow_run_id, terminal_state=state, terminal_outcome=outcome
                )
                self._keep_interrupted_run_resumable(cursor, run_id)
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

    def _record_write_ambiguity(
        self,
        cursor: _Cursor,
        run_id: str,
        flow_run_id: str,
        *,
        terminal_state: str,
        terminal_outcome: str,
    ) -> None:
        """Record durable write ambiguity in the transaction that made it terminal.

        The condition is derived from the verdict and the execution's own purpose rather
        than passed in, so no caller can write the column false and no write-uncertain
        terminal path can forget it. `interrupted` / `ambiguous` is only ever written to a
        claimed execution, so an apply or sync bearing it is a worker that may already have
        reached the destination.
        """
        if (terminal_state, terminal_outcome) != ("interrupted", "ambiguous"):
            return
        cursor.execute(
            self._sql(
                "UPDATE product_runs SET reconciliation_required = TRUE WHERE run_id = ? AND EXISTS "
                "(SELECT 1 FROM prefect_executions WHERE run_id = ? AND flow_run_id = ? "
                "AND purpose IN ('apply', 'sync', 'resume'))"
            ),
            (run_id, run_id, flow_run_id),
        )

    def _keep_interrupted_run_resumable(self, cursor: _Cursor, run_id: str) -> None:
        """Keep a run whose write was interrupted in phase ``interrupted`` when a later execution ends.

        On a run that requires reconciliation, only a ``verify`` or a ``resume`` can still run
        (spec 022, research R5). When one of them is cancelled or abandoned, the run's write
        is still interrupted and possibly partly applied, so the run keeps phase
        ``interrupted`` and outcome ``ambiguous``: it stays resumable, and the next resume
        requires a new ``verify``. ``reconciliation_required`` is never cleared here. Before
        spec 022 no run that required reconciliation admitted another execution, so this
        changes nothing for an earlier run.
        """
        cursor.execute(
            self._sql(
                "UPDATE product_runs SET phase = 'interrupted', outcome = 'ambiguous' "
                "WHERE run_id = ? AND reconciliation_required AND phase IN ('cancelled', 'abandoned')"
            ),
            (run_id,),
        )

    def _settle_resume(
        self, cursor: _Cursor, run_id: str, flow_run_id: str, *, terminal_state: str, terminal_outcome: str
    ) -> None:
        """Clear ``reconciliation_required`` and set phase ``applied`` when a resume succeeded.

        A ``resume`` execution that ends ``completed`` / ``succeeded`` applied every operation
        after the run's saved position, so the whole plan is applied (spec 022, research R8).
        Any other verdict leaves the run requiring reconciliation; an interrupted resume is
        recorded by `_record_write_ambiguity` like an interrupted apply.
        """
        if (terminal_state, terminal_outcome) != ("completed", "succeeded"):
            return
        cursor.execute(
            self._sql(
                "UPDATE product_runs SET reconciliation_required = FALSE, phase = 'applied' WHERE run_id = ? "
                "AND EXISTS (SELECT 1 FROM prefect_executions WHERE run_id = ? AND flow_run_id = ? "
                "AND purpose = 'resume')"
            ),
            (run_id, run_id, flow_run_id),
        )

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
        event: AuditEvent | None = None,
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
                    (phase, outcome, _utc_iso(terminal_at), run_id, flow_run_id),
                )
                self._record_write_ambiguity(
                    cursor,
                    run_id,
                    flow_run_id,
                    terminal_state=terminal_state,
                    terminal_outcome=terminal_outcome,
                )
                self._keep_interrupted_run_resumable(cursor, run_id)
                if event is not None:
                    self._insert_event(cursor, event)
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
        """Atomically arbitrate one run stage, reserve actor/key, and create any new run.

        The arbitration and the reservation are one transaction on purpose: whichever
        request wins a run's single write admission is decided here, before either
        competitor can submit anything to Prefect. A loser is answered durably, by a stored
        refusal on its own receipt, so replaying its client key replays the refusal rather
        than racing again.
        """
        connection = self._connect()
        try:
            cursor = connection.cursor()
            try:
                try:
                    stored = self._arbitrate_reservation(cursor, receipt, run, admit_write=admit_write)
                    cursor.execute(self._sql(_INSERT_MUTATION_RECEIPT), _receipt_values(stored))
                    if run is not None:
                        self._insert_product_run(cursor, run)
                    if admit_write and stored.state != "accepted":
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
                    if run is not None and self.exists(run.run_id):
                        msg = f"Sync run ID {run.run_id!r} already exists"
                        raise DuplicateRunError(msg) from exc
                    raise
            finally:
                cursor.close()
        finally:
            connection.close()
        return stored, True

    def _arbitrate_reservation(
        self,
        cursor: _Cursor,
        receipt: MutationReceipt,
        run: ProductRun | None,
        *,
        admit_write: bool,
    ) -> MutationReceipt:
        """Return the receipt to store: the request itself, or its stored refusal.

        A run being created cannot lose: its own insert is the serialization point and it
        has no records to arbitrate against. For an existing run, the run's authoritative
        row is locked with the store's write-first pattern before anything is read, so two
        competing reservations cannot both observe the same unadmitted run.
        """
        if run is not None or receipt.resource_kind != "run" or receipt.operation not in _ARBITRATED_OPERATIONS:
            return receipt
        run_id = receipt.run_id
        assert run_id is not None  # a run receipt carries its run ID (MutationReceipt invariant).
        cursor.execute(self._sql("UPDATE product_runs SET run_id = run_id WHERE run_id = ?"), (run_id,))
        if not self._reservation_refused(
            cursor, run_id, receipt.receipt_id, operation=receipt.operation, admit_write=admit_write
        ):
            return receipt
        return receipt.model_copy(
            update={
                "state": "accepted",
                "response_status": 409,
                "response_body": _run_execution_conflict_response(run_id, receipt.receipt_id),
            }
        )

    def _reservation_refused(
        self, cursor: _Cursor, run_id: str, receipt_id: str, *, operation: str, admit_write: bool
    ) -> bool:
        """Answer whether this run may still admit the requested stage.

        A run that has admitted its one write never admits another stage of any kind, even
        after that write becomes terminal: terminal write evidence is immutable and the
        operator's next step is a new run. The one exception is a ``verify`` on a resumable
        run (spec 022, research R5, critique E1): it reads the destination and writes nothing,
        and it is admitted only while no other receipt of the run is unresolved, so two
        ``verify`` requests never both win. A write additionally needs the run quiet — no
        other unresolved submission and no unfinished execution — so an apply cannot race
        a verify that has reserved but not yet submitted. A write is also refused once the
        run is in phase ``cancelled``: the caller holds the same row lock as
        ``cancel_planned_run``, so a cancel and an apply never both succeed.
        """
        cursor.execute(self._sql("SELECT 1 FROM write_admissions WHERE run_id = ?"), (run_id,))
        if cursor.fetchone() is not None:
            if operation != "verify" or admit_write or not self._run_is_resumable(cursor, run_id):
                return True
            return self._other_receipt_unresolved(cursor, run_id, receipt_id)
        if not admit_write:
            return False
        cursor.execute(self._sql("SELECT phase FROM product_runs WHERE run_id = ?"), (run_id,))
        row = cursor.fetchone()
        if row is not None and row[0] == "cancelled":
            return True
        # Every stage a run arbitrates is every run mutation except cancellation, which
        # targets the execution a write already owns rather than reserving a stage, and
        # approval, which submits no execution: the approval columns decide what an apply
        # may do, and an approve receipt left reserved by a crash must not block every write.
        if self._other_receipt_unresolved(cursor, run_id, receipt_id):
            return True
        cursor.execute(
            self._sql("SELECT 1 FROM prefect_executions WHERE run_id = ? AND terminal_at IS NULL"),
            (run_id,),
        )
        return cursor.fetchone() is not None

    def _other_receipt_unresolved(self, cursor: _Cursor, run_id: str, receipt_id: str) -> bool:
        """Whether another stage receipt of the run is still unresolved.

        Cancellation and approval are left out: a cancellation targets the execution a stage
        already owns, and an approval submits no execution.
        """
        cursor.execute(
            self._sql(_SELECT_UNRESOLVED_ARBITRATED_RECEIPT),
            (run_id, receipt_id, *_ARBITRATED_OPERATIONS),
        )
        return cursor.fetchone() is not None

    def reserve_resume(self, run_id: str, receipt: MutationReceipt) -> tuple[MutationReceipt, bool]:
        """Reserve the actor/key of a ``resume`` request and, when admitted, its resume admission.

        One transaction on the run's locked row (the write-first pattern every reservation
        uses) decides the request, so two concurrent resumes of one run never both win (spec
        022, research R5). The resume is admitted when no other resume of the run is
        unfinished, no other stage receipt is unresolved, the run is resumable, and the run's
        latest ``verify`` execution comes after its latest write execution and succeeded. A
        refused request is stored with its refusal, so replaying its client key replays the
        refusal. The run's ``write_admissions`` row is kept.

        Returns:
            The stored receipt, and whether this call created it.

        Raises:
            ValueError: The receipt is not a ``resume`` receipt for ``run_id``.
        """
        if receipt.operation != "resume" or receipt.resource_kind != "run" or receipt.run_id != run_id:
            msg = "a resume reservation requires a resume mutation receipt for the same Sync run"
            raise ValueError(msg)
        connection = self._connect()
        try:
            cursor = connection.cursor()
            try:
                try:
                    cursor.execute(self._sql("UPDATE product_runs SET run_id = run_id WHERE run_id = ?"), (run_id,))
                    refusal = self._resume_refusal(cursor, run_id, receipt.receipt_id)
                    stored = (
                        receipt
                        if refusal is None
                        else receipt.model_copy(
                            update={"state": "accepted", "response_status": 409, "response_body": refusal}
                        )
                    )
                    cursor.execute(self._sql(_INSERT_MUTATION_RECEIPT), _receipt_values(stored))
                    if refusal is None:
                        cursor.execute(
                            self._sql(
                                "INSERT INTO resume_admissions (run_id, resume_number, receipt_id) "
                                "SELECT ?, COALESCE(MAX(resume_number), 0) + 1, ? FROM resume_admissions "
                                "WHERE run_id = ?"
                            ),
                            (run_id, receipt.receipt_id, run_id),
                        )
                    connection.commit()
                except Exception as exc:  # pylint: disable=broad-exception-caught
                    connection.rollback()
                    if not _is_unique_violation(exc):
                        raise
                    existing = self.lookup_mutation(receipt.actor, receipt.key_digest)
                    if existing.value is not None:
                        return existing.value, False
                    raise
            finally:
                cursor.close()
        finally:
            connection.close()
        return stored, True

    def _resume_refusal(self, cursor: _Cursor, run_id: str, receipt_id: str) -> dict[str, Any] | None:
        """Return the refusal a ``resume`` request gets on this run, or ``None`` to admit it.

        The checks run in this order, so each refusal names the first thing in the way:

        1. ``run-execution-conflict``: an earlier resume of the run is unfinished, because its
           receipt is not answered yet or its execution has not ended.
        2. ``run-not-resumable``: another stage receipt of the run is unresolved, or the run
           does not require reconciliation, is cancelled, or has an unfinished execution.
        3. ``verify-required``: the run's latest ``verify`` execution does not come after its
           latest write execution, or did not succeed.
        """
        cursor.execute(
            self._sql(
                "SELECT 1 FROM resume_admissions JOIN mutation_receipts "
                "ON mutation_receipts.receipt_id = resume_admissions.receipt_id "
                "WHERE resume_admissions.run_id = ? AND resume_admissions.receipt_id <> ? "
                "AND (mutation_receipts.state <> 'accepted' OR EXISTS (SELECT 1 FROM prefect_executions "
                "WHERE prefect_executions.mutation_receipt_id = resume_admissions.receipt_id "
                "AND prefect_executions.terminal_at IS NULL))"
            ),
            (run_id, receipt_id),
        )
        if cursor.fetchone() is not None:
            return _resume_refusal_response(_RUN_EXECUTION_CONFLICT, _RESUME_IN_PROGRESS_MESSAGE, run_id, receipt_id)
        not_resumable = self._not_resumable_reason(cursor, run_id, receipt_id)
        if not_resumable is not None:
            return _resume_refusal_response(_RUN_NOT_RESUMABLE, not_resumable, run_id, receipt_id)
        if not self._verify_passed_after_latest_write(cursor, run_id):
            return _resume_refusal_response(_VERIFY_REQUIRED, _VERIFY_REQUIRED_MESSAGE, run_id, receipt_id)
        return None

    def _not_resumable_reason(self, cursor: _Cursor, run_id: str, receipt_id: str) -> str | None:
        """Say why the run is not resumable now, or return ``None`` when it is."""
        cursor.execute(self._sql("SELECT reconciliation_required, phase FROM product_runs WHERE run_id = ?"), (run_id,))
        row = cursor.fetchone()
        if row is None or not bool(row[0]):
            return "this Sync run has no interrupted write to resume"
        if row[1] == "cancelled":
            return "this Sync run is cancelled"
        if not self._plan_published(cursor, run_id):
            return NO_SAVED_PLAN_MESSAGE
        if self._other_receipt_unresolved(cursor, run_id, receipt_id) or not self._run_is_resumable(cursor, run_id):
            return "this Sync run has an unfinished execution; wait for it to end"
        return None

    def _verify_passed_after_latest_write(self, cursor: _Cursor, run_id: str) -> bool:
        """Whether the run's latest ``verify`` execution follows its latest write execution and succeeded.

        Executions are ordered by their position in the run, never by time (spec 022 FR-012).
        """
        cursor.execute(
            self._sql(
                "SELECT position, terminal_state, terminal_outcome FROM prefect_executions "
                "WHERE run_id = ? AND purpose = 'verify' ORDER BY position DESC LIMIT 1"
            ),
            (run_id,),
        )
        verify = cursor.fetchone()
        if verify is None or (verify[1], verify[2]) != ("completed", "succeeded"):
            return False
        cursor.execute(
            self._sql(
                "SELECT MAX(position) FROM prefect_executions WHERE run_id = ? AND purpose IN ('apply', 'sync', 'resume')"
            ),
            (run_id,),
        )
        write = cursor.fetchone()
        return write is None or write[0] is None or int(verify[0]) > int(write[0])

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

    def lookup_mutation_for_execution(
        self, run_id: str, flow_run_id: str, operation: str
    ) -> LookupResult[MutationReceipt]:
        connection = self._connect()
        try:
            cursor = connection.cursor()
            try:
                cursor.execute(self._sql(_SELECT_MUTATION_RECEIPT_FOR_EXECUTION), (run_id, flow_run_id, operation))
                row = cursor.fetchone()
            finally:
                cursor.close()
        finally:
            connection.close()
        if row is None:
            return LookupResult(value=None, reason="mutation-receipt-not-found")
        return LookupResult(value=_receipt_from_row(row))

    def record_approval(
        self,
        run_id: str,
        approval: RunApproval,
        receipt: MutationReceipt,
        response: Mapping[str, Any],
        *,
        updated_at: datetime,
        events: Sequence[AuditEvent] = (),
    ) -> bool:
        """Store a run's one approval and complete its request's receipt in one transaction.

        The run's row is locked first, with the write-first pattern a write admission and a
        withdrawal take, so the execution check and the update below see one state. The
        conditional update stores the approval only on a run that has none and no unfinished
        execution, and that is in phase ``planned`` or is resumable: its write stopped part-way,
        so it requires reconciliation, and it is not cancelled (spec 022 FR-019). Of two
        concurrent approvals exactly one stores, and no
        approval is stored while an execution such as an ``apply`` is running. A write admitted
        but not yet submitted does not block the approval: once approval is required, retrying
        that ``apply`` needs this approval. The loser's receipt is completed with a stored
        refusal instead: ``already-approved`` with the approval that won, ``run-cancelled`` for
        a withdrawn run, or ``run-not-approvable`` when an execution is unfinished or the run is
        neither planned nor resumable. Either way a retry with the same key replays what this
        transaction stored. ``events`` are written only with a stored approval, in the same
        transaction (spec 017, FR-014).

        Returns:
            Whether this call stored the approval.

        Raises:
            ValueError: The receipt is not awaiting completion; nothing is stored.
        """
        connection = self._connect()
        try:
            cursor = connection.cursor()
            try:
                cursor.execute(self._sql("UPDATE product_runs SET run_id = run_id WHERE run_id = ?"), (run_id,))
                active_stage = self._unfinished_execution_purpose(cursor, run_id)
                stored = False
                if active_stage is None:
                    cursor.execute(
                        self._sql(
                            "UPDATE product_runs SET approved_by = ?, approved_at = ?, approved_checksum = ?, "
                            "approval_reason = ? WHERE run_id = ? AND approved_by IS NULL "
                            "AND (phase = 'planned' OR (reconciliation_required AND phase <> 'cancelled'))"
                        ),
                        (*_approval_columns(approval), run_id),
                    )
                    stored = cursor.rowcount == 1
                if stored:
                    for event in events:
                        self._insert_event(cursor, event)
                status, body = (
                    (200, response)
                    if stored
                    else self._approval_refusal(cursor, run_id, receipt.receipt_id, active_stage=active_stage)
                )
                self._answer_receipt_row(
                    cursor, receipt.receipt_id, response_status=status, response_body=body, updated_at=updated_at
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                cursor.close()
        finally:
            connection.close()
        return stored

    def cancel_planned_run(  # pylint: disable=too-many-arguments
        self,
        run_id: str,
        receipt: MutationReceipt,
        response: Mapping[str, Any],
        *,
        cancelled_at: datetime,
        updated_at: datetime,
        events: Sequence[AuditEvent] = (),
    ) -> bool:
        """Withdraw a finished plan: set phase ``cancelled`` and complete the cancel receipt, in one transaction.

        The run's row is locked with the write-first pattern before anything is read, the same
        lock a write admission and an approval take, so a cancel and an ``apply`` or ``approve``
        of the run never both succeed. The run is withdrawn only while it is in phase
        ``planned`` with no admitted write, no unresolved receipt other than a cancel, and no
        unfinished execution. Otherwise the run is left unchanged and the receipt is completed
        with the matching refusal: ``run-execution-conflict`` when other work holds the run,
        ``run-already-cancelled`` for a cancelled run, ``execution-terminal`` for any other phase.
        A retry with the same key replays what this transaction stored. ``events`` are written
        only with a withdrawal, in the same transaction (spec 017, FR-014).

        Returns:
            Whether this call withdrew the run.

        Raises:
            ValueError: The receipt is not awaiting completion; nothing is stored.
        """
        connection = self._connect()
        try:
            cursor = connection.cursor()
            try:
                cursor.execute(self._sql("UPDATE product_runs SET run_id = run_id WHERE run_id = ?"), (run_id,))
                cursor.execute(self._sql("SELECT phase FROM product_runs WHERE run_id = ?"), (run_id,))
                row = cursor.fetchone()
                refusal = self._withdrawal_refusal(cursor, run_id, receipt.receipt_id, None if row is None else row[0])
                withdrawn = refusal is None
                if refusal is None:
                    cursor.execute(
                        self._sql(
                            "UPDATE product_runs SET phase = 'cancelled', outcome = 'cancelled', finished_at = ? "
                            "WHERE run_id = ? AND phase = 'planned'"
                        ),
                        (_utc_iso(cancelled_at), run_id),
                    )
                    for event in events:
                        self._insert_event(cursor, event)
                    status, body = 200, response
                else:
                    status, body = refusal
                self._answer_receipt_row(
                    cursor, receipt.receipt_id, response_status=status, response_body=body, updated_at=updated_at
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                cursor.close()
        finally:
            connection.close()
        return withdrawn

    def _withdrawal_refusal(
        self, cursor: _Cursor, run_id: str, receipt_id: str, phase: str | None
    ) -> tuple[int, dict[str, Any]] | None:
        """Return the refusal a withdrawal stores, or None when the locked run may be withdrawn."""
        if phase is None:
            return 404, approval_error_body(
                404, "run-not-found", "the requested Sync run does not exist", run_id=run_id, mutation_id=receipt_id
            )
        if phase == "cancelled":
            return 409, approval_error_body(
                409, "run-already-cancelled", RUN_ALREADY_CANCELLED_MESSAGE, run_id=run_id, mutation_id=receipt_id
            )
        if phase != "planned":
            return 409, _cancellation_terminal_response(run_id, receipt_id)
        if self._reservation_refused(cursor, run_id, receipt_id, operation="cancel", admit_write=True):
            return 409, _run_execution_conflict_response(run_id, receipt_id)
        return None

    def _answer_receipt_row(
        self,
        cursor: _Cursor,
        receipt_id: str,
        *,
        response_status: int,
        response_body: Mapping[str, Any],
        updated_at: datetime,
    ) -> None:
        """Complete a receipt the service answers itself, with no flow run, in the caller's transaction."""
        cursor.execute(
            self._sql(
                "UPDATE mutation_receipts SET state = 'accepted', response_status = ?, response_body = ?, "
                "updated_at = ? WHERE receipt_id = ? AND state IN ('reserved', 'processing')"
            ),
            (response_status, _json(response_body), updated_at.isoformat(), receipt_id),
        )
        if cursor.rowcount != 1:
            msg = f"Mutation receipt {receipt_id!r} is unavailable for completion"
            raise ValueError(msg)

    def _unfinished_execution_purpose(self, cursor: _Cursor, run_id: str) -> str | None:
        """Return the stage of the run's latest unfinished execution, or None when every execution finished."""
        cursor.execute(
            self._sql(
                "SELECT purpose FROM prefect_executions WHERE run_id = ? AND terminal_at IS NULL "
                "ORDER BY position DESC LIMIT 1"
            ),
            (run_id,),
        )
        row = cursor.fetchone()
        return None if row is None else str(row[0])

    def _approval_refusal(
        self, cursor: _Cursor, run_id: str, receipt_id: str, *, active_stage: str | None = None
    ) -> tuple[int, dict[str, Any]]:
        """Return the status and refusal a losing approval stores, read in the losing transaction.

        ``active_stage`` names the unfinished execution that refused the approval, if any; it is
        reported before an existing approval, in the order the service checks the two.
        """
        cursor.execute(
            self._sql(
                "SELECT phase, approved_by, approved_at, approved_checksum, approval_reason "
                "FROM product_runs WHERE run_id = ?"
            ),
            (run_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return 404, approval_error_body(
                404, "run-not-found", "the requested Sync run does not exist", run_id=run_id, mutation_id=receipt_id
            )
        if row[0] == "cancelled":
            return 409, approval_error_body(
                409, "run-cancelled", RUN_CANCELLED_MESSAGE, run_id=run_id, mutation_id=receipt_id
            )
        existing = RunApproval.from_columns(row[1], row[2], row[3], row[4])
        if existing is not None and active_stage is None:
            return 409, already_approved_body(existing, run_id=run_id, mutation_id=receipt_id)
        return 409, approval_error_body(
            409,
            "run-not-approvable",
            not_approvable_message(str(row[0]), active_stage),
            run_id=run_id,
            mutation_id=receipt_id,
        )

    def complete_mutation(
        self,
        receipt_id: str,
        *,
        response_status: int,
        response_body: Mapping[str, Any],
        flow_run_id: str | None,
        updated_at: datetime,
        event: AuditEvent | None = None,
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
                if completed and event is not None:
                    self._insert_event(cursor, event)
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

    def _insert_event(self, cursor: _Cursor, event: AuditEvent) -> None:
        """Write one event row and its related-resource rows with the caller's cursor.

        The caller commits or rolls back, so an event written with a decision is saved in
        that decision's transaction (FR-014).
        """
        cursor.execute(
            self._sql(_INSERT_AUDIT_EVENT),
            (
                event.event_id,
                event.run_id,
                event.actor,
                event.operation,
                event.reason,
                event.outcome,
                _utc_iso(event.created_at),
                event.event_type,
                event.config_id,
                event.registry_version,
                event.branch,
            ),
        )
        for resource in event.related:
            cursor.execute(self._sql(_INSERT_AUDIT_EVENT_RESOURCE), (event.event_id, resource.kind, resource.id))

    def _read_event_resources(self, cursor: _Cursor, rows: Sequence[Sequence[Any]]) -> dict[str, list[tuple[str, str]]]:
        """Return the related resources of the events in ``rows``, by event ID."""
        resources: dict[str, list[tuple[str, str]]] = {str(row[0]): [] for row in rows}
        event_ids = tuple(resources)
        for start in range(0, len(event_ids), _EVENT_RESOURCE_BATCH):
            batch = event_ids[start : start + _EVENT_RESOURCE_BATCH]
            placeholders = ", ".join("?" for _ in batch)
            cursor.execute(self._sql(_SELECT_AUDIT_EVENT_RESOURCES.format(placeholders)), batch)
            for event_id, kind, resource_id in cursor.fetchall():
                resources[str(event_id)].append((str(kind), str(resource_id)))
        return resources

    def record_audit(self, event: AuditEvent) -> None:
        connection = self._connect()
        try:
            cursor = connection.cursor()
            try:
                self._insert_event(cursor, event)
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
                    cursor.execute(_SELECT_ALL_AUDIT_EVENTS)
                else:
                    cursor.execute(self._sql(_SELECT_AUDIT_EVENTS), (run_id,))
                rows = cursor.fetchall()
                resources = self._read_event_resources(cursor, rows)
            finally:
                cursor.close()
        finally:
            connection.close()
        return tuple(_audit_from_row(row, resources[str(row[0])]) for row in rows)

    def list_events(self, filters: EventFilters, *, limit: int, offset: int) -> EventPage:
        """Return up to ``limit`` typed events matching ``filters`` after the first ``offset``, oldest first.

        Events are ordered by ``created_at`` and then ``event_id``. An event without a type,
        a request refused for a missing or invalid token, is never listed (research R6).

        Raises:
            ValueError: ``limit`` is less than 1 or ``offset`` is negative.
        """
        if offset < 0:
            msg = "offset must be zero or a positive integer"
            raise ValueError(msg)
        conditions = ["event_type IS NOT NULL"]
        parameters: list[object] = []
        if filters.run_id is not None:
            conditions.append("run_id = ?")
            parameters.append(filters.run_id)
        if filters.config_id is not None:
            conditions.append(f"(config_id = ? OR {_EVENT_HAS_RESOURCE})")
            parameters.extend((filters.config_id, "config", filters.config_id))
        if filters.event_types:
            conditions.append(f"event_type IN ({', '.join('?' for _ in filters.event_types)})")
            parameters.extend(filters.event_types)
        for resource in filters.related:
            conditions.append(_EVENT_HAS_RESOURCE)
            parameters.extend((resource.kind, resource.id))
        if filters.since is not None:
            conditions.append("created_at >= ?")
            parameters.append(_utc_iso(filters.since))
        if filters.until is not None:
            conditions.append("created_at < ?")
            parameters.append(_utc_iso(filters.until))
        statement = (
            f"SELECT {_AUDIT_EVENT_COLUMNS} FROM audit_events WHERE {' AND '.join(conditions)} "  # noqa: S608
            "ORDER BY created_at, event_id LIMIT ? OFFSET ?"
        )
        resources: dict[str, list[tuple[str, str]]] = {}

        def read_resources(cursor: _Cursor, page: Sequence[Sequence[Any]]) -> None:
            resources.update(self._read_event_resources(cursor, page))

        rows, has_more = self._read_page(statement, parameters, limit, read_resources, trailing=(offset,))
        return EventPage(items=tuple(_audit_from_row(row, resources[str(row[0])]) for row in rows), has_more=has_more)

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

    def create_configuration(
        self,
        package: ConfigurationPackage,
        *,
        registered_by: str | None = None,
        registration_reason: str | None = None,
    ) -> ConfigurationVersion:
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
                self._insert_configuration_version_row(
                    cursor,
                    version,
                    name=package.configuration.name or None,
                    registered_by=registered_by,
                    registration_reason=registration_reason,
                )
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

    def _insert_configuration_version_row(
        self,
        cursor: _Cursor,
        version: ConfigurationVersion,
        *,
        name: str | None,
        registered_by: str | None = None,
        registration_reason: str | None = None,
    ) -> None:
        """Insert one version and make ``name``, its package's name, the configuration's name.

        Runs inside the transaction that allocates the version, which is always the newest
        one, so the configuration's name is always its latest version's name. ``name`` is
        ``None`` when the package declares an empty name.
        """
        _require_checksum_digests_content(version.package_checksum, version.declared_content)
        cursor.execute(
            self._sql(_INSERT_CONFIGURATION_VERSION),
            (
                version.config_id,
                version.registry_version,
                version.package_checksum,
                _json(version.declared_content),
                version.created_at.isoformat(),
                registered_by,
                registration_reason,
            ),
        )
        cursor.execute(
            self._sql(_UPDATE_CONFIGURATION_NAME),
            (name, version.config_id),
        )

    def add_configuration_version(
        self,
        config_id: str,
        package: ConfigurationPackage,
        *,
        registered_by: str | None = None,
        registration_reason: str | None = None,
    ) -> tuple[ConfigurationVersion, bool]:
        """Atomically allocate the next version, or return the existing one for a known checksum.

        An existing version is returned unchanged: its actor and reason stay those of the
        request that created it.
        """
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
                    self._insert_configuration_version_row(
                        cursor,
                        version,
                        name=package.configuration.name or None,
                        registered_by=registered_by,
                        registration_reason=registration_reason,
                    )
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
        return LookupResult(value=_configuration_summary_from_row(row))

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

    def configuration_version_exists(self, config_id: str, registry_version: int) -> bool:
        """Return whether one configuration version exists, without reading its package."""
        connection = self._connect()
        try:
            cursor = connection.cursor()
            try:
                cursor.execute(self._sql(_CONFIGURATION_VERSION_EXISTS), (config_id, registry_version))
                return cursor.fetchone() is not None
            finally:
                cursor.close()
        finally:
            connection.close()

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
        return tuple(_configuration_summary_from_row(row) for row in rows)

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

    def list_configuration_page(
        self, *, name: str | None = None, after: tuple[datetime, str] | None = None, limit: int = DEFAULT_LIMIT
    ) -> ConfigurationPage:
        """Return up to ``limit`` configurations, newest first, after the ``after`` key.

        Configurations are ordered by ``(created_at, config_id)`` descending; ``after`` is the
        last key of the previous page. ``name`` keeps only configurations whose current name
        is exactly ``name``. The query reads one row past the page to detect a further page.
        """
        conditions: list[str] = []
        parameters: list[object] = []
        if name is not None:
            conditions.append(_CONFIGURATION_LIST_NAME)
            parameters.append(name)
        if after is not None:
            conditions.append(_CONFIGURATION_LIST_AFTER)
            parameters.extend(_keyset_parameters(after))
        statement = _filtered(_SELECT_CONFIGURATION_LIST, conditions) + _CONFIGURATION_LIST_ORDER
        rows, has_more = self._read_page(statement, parameters, limit)
        return ConfigurationPage(items=tuple(_configuration_summary_from_row(row) for row in rows), has_more=has_more)

    def _read_page(
        self,
        statement: str,
        parameters: Sequence[object],
        limit: int,
        follow_up: Callable[[_Cursor, Sequence[Sequence[Any]]], None] | None = None,
        *,
        trailing: Sequence[object] = (),
    ) -> tuple[Sequence[Sequence[Any]], bool]:
        """Read up to ``limit`` rows of ``statement`` and whether more rows follow them.

        ``statement`` has a ``LIMIT ?`` placeholder that this method binds to ``limit + 1``,
        so one row past the page tells whether a further page exists. ``trailing`` binds the
        placeholders after it, such as an ``OFFSET ?``.
        ``follow_up`` runs on the same cursor with the page's rows when the page is not empty.

        Raises:
            ValueError: ``limit`` is less than 1.
        """
        if limit < 1:
            msg = "limit must be a positive integer"
            raise ValueError(msg)
        connection = self._connect()
        try:
            cursor = connection.cursor()
            try:
                cursor.execute(self._sql(statement), (*parameters, limit + 1, *trailing))
                rows = cursor.fetchall()
                page = rows[:limit]
                if follow_up is not None and page:
                    follow_up(cursor, page)
            finally:
                cursor.close()
        finally:
            connection.close()
        return page, len(rows) > limit

    def list_configuration_version_page(
        self, config_id: str, *, after: int | None = None, limit: int = DEFAULT_LIMIT
    ) -> ConfigurationVersionPage:
        """Return up to ``limit`` version summaries of one configuration, newest version first.

        ``after`` is the last ``registry_version`` of the previous page. A second query, one
        per page, reads each listed version's newest run and newest applied run.
        """
        statement = _SELECT_CONFIGURATION_VERSION_LIST
        parameters: list[object] = [config_id]
        if after is not None:
            statement += _CONFIGURATION_VERSION_LIST_AFTER
            parameters.append(after)
        statement += _CONFIGURATION_VERSION_LIST_ORDER
        latest: dict[tuple[int, str], RunReference] = {}

        def read_latest_runs(cursor: _Cursor, page: Sequence[Sequence[Any]]) -> None:
            versions = tuple(int(row[1]) for row in page)
            placeholders = ", ".join("?" for _ in versions)
            cursor.execute(
                self._sql(_SELECT_LATEST_VERSION_RUNS.format(placeholders)),
                (config_id, *versions, config_id, *versions),
            )
            for registry_version, kind, run_id, phase, outcome, started_at, finished_at in cursor.fetchall():
                latest[int(registry_version), str(kind)] = RunReference(
                    run_id=run_id, phase=phase, outcome=outcome, started_at=started_at, finished_at=finished_at
                )

        rows, has_more = self._read_page(statement, parameters, limit, read_latest_runs)
        return ConfigurationVersionPage(
            items=tuple(_configuration_version_summary_from_row(row, latest) for row in rows), has_more=has_more
        )

    def list_runs(
        self, filters: RunFilters, *, after: tuple[datetime, str] | None = None, limit: int = DEFAULT_LIMIT
    ) -> RunPage:
        """Return up to ``limit`` runs matching ``filters``, newest first, after the ``after`` key.

        Runs are ordered by ``(started_at, run_id)`` descending; ``after`` is the last key of
        the previous page, so a run created since then is newer and never appears. One query
        reads one row past the page to detect a further page, and one more reads the page's
        audit links. Artifact references and execution links are not loaded.
        """
        conditions: list[str] = []
        parameters: list[object] = []
        if filters.config_id is not None:
            conditions.append(_RUN_LIST_CONFIGURATION)
            parameters.append(filters.config_id)
        if filters.registry_version is not None:
            conditions.append(_RUN_LIST_VERSION)
            parameters.append(filters.registry_version)
        if after is not None:
            conditions.append(_RUN_LIST_AFTER)
            parameters.extend(_keyset_parameters(after))
        statement = _filtered(_SELECT_PRODUCT_RUN_LIST, conditions) + _RUN_LIST_ORDER
        audit_links: dict[str, list[Sequence[Any]]] = {}

        def read_audit_links(cursor: _Cursor, page: Sequence[Sequence[Any]]) -> None:
            audit_links.update({row[0]: [] for row in page})
            placeholders = ", ".join("?" for _ in page)
            cursor.execute(self._sql(_SELECT_AUDIT_LINKS_FOR_RUNS.format(placeholders)), tuple(audit_links))
            for run_id, event_id in cursor.fetchall():
                audit_links[run_id].append((event_id,))

        rows, has_more = self._read_page(statement, parameters, limit, read_audit_links)
        return RunPage(items=tuple(_run_from_rows(row, (), (), audit_links[row[0]]) for row in rows), has_more=has_more)

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
                reference.visibility,
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
                    (phase, outcome, _utc_iso(finished_at), _json(summary), _json(results), run_id),
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
            initialization_retry_codes=_POSTGRESQL_SCHEMA_INITIALIZATION_RETRY_CODES,
        )


class _ArtifactStore(Protocol):
    def publish(self, reference: ArtifactReference, data: bytes) -> None: ...

    def lookup(self, reference: ArtifactReference) -> LookupResult[bytes]: ...

    def read_bounded(self, reference: ArtifactReference, *, limit: int) -> LookupResult[bytes]:
        """Return the object only if it is at most `limit` bytes.

        The stored size is checked from provider metadata first, so an oversized object
        is refused without transferring it; the transfer is then itself bounded, because
        metadata is part of the same store the object came from.
        """


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

    def read_bounded(self, reference: ArtifactReference, *, limit: int) -> LookupResult[bytes]:
        if _expired(reference):
            return LookupResult(value=None, reason="artifact-expired")
        try:
            manifest = self._path(reference.manifest_key).read_bytes()
        except (FileNotFoundError, NotADirectoryError):
            return LookupResult(value=None, reason="manifest-unavailable")
        path = self._path(reference.object_key)
        try:
            if path.stat().st_size > limit:
                return LookupResult(value=None, reason="artifact-too-large")
            with path.open("rb") as handle:
                data = handle.read(limit + 1)
        except (FileNotFoundError, NotADirectoryError):
            return LookupResult(value=None, reason="data-unavailable")
        if len(data) > limit:
            return LookupResult(value=None, reason="artifact-too-large")
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

    def head(self, *, bucket: str, key: str) -> int | None:
        """Return the stored object's length, or None when the key is absent."""

    def get_bounded(self, *, bucket: str, key: str, limit: int) -> bytes | None:
        """Return at most `limit + 1` bytes, so an overrun is visible without buffering it."""

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

    def read_bounded(self, reference: ArtifactReference, *, limit: int) -> LookupResult[bytes]:
        if _expired(reference):
            return LookupResult(value=None, reason="artifact-expired")
        manifest = self._client.get(bucket=self._bucket, key=self._key(reference.manifest_key))
        if manifest is None:
            return LookupResult(value=None, reason="manifest-unavailable")
        object_key = self._key(reference.object_key)
        stored = self._client.head(bucket=self._bucket, key=object_key)
        if stored is not None and stored > limit:
            return LookupResult(value=None, reason="artifact-too-large")
        # An absent object reports itself the same way through either call, so the
        # missing-metadata case needs no branch of its own.
        data = self._client.get_bounded(bucket=self._bucket, key=object_key, limit=limit)
        if data is None:
            return LookupResult(value=None, reason="data-unavailable")
        if len(data) > limit:
            return LookupResult(value=None, reason="artifact-too-large")
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
        receipt_id: str,
        allocate_attempt: bool = False,
        secrets: Sequence[str] = (),
    ) -> PrefectExecutionLink:
        """Append the one execution `receipt_id` owns, allocating its ordinal atomically.

        `receipt_id` is the exact unresolved mutation receipt this submission reserved. It
        is the store's internal owner relation and appears in no public resource.
        """
        if link.submitted_at is None:
            msg = "new Prefect execution requires submitted_at"
            raise ValueError(msg)
        if not self._records.exists(run_id):
            msg = f"Cannot link a Prefect execution to unavailable Sync run ID {run_id!r}"
            raise RunNotFoundError(msg)
        sanitized = PrefectExecutionLink.model_validate(_redact_value(link.model_dump(mode="json"), secrets))
        return self._records.add_prefect_execution(
            run_id, sanitized, receipt_id=redact(receipt_id, secrets), allocate_attempt=allocate_attempt
        )

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
            raise ValueError(_INVALID_SERVICE_WORKER_ID)
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

    def record_side_reads(self, run_id: str, flow_run_id: str, documents: Mapping[Side, Mapping[str, Any]]) -> bool:
        """Store read documents, keyed by side, on a non-terminal execution; return whether it changed.

        Each document holds a state, times, a read mode, node counts and an error class name,
        never an exception message (spec 018 data-model.md). An unknown side is refused.
        """
        return self._records.record_side_reads(run_id, flow_run_id, documents)

    def record_apply_progress(self, run_id: str, flow_run_id: str, document: ApplyProgress) -> ApplyProgressSave:
        """Store a write execution's progress marker on a non-terminal execution.

        Returns `saved`, `ended` when the execution is terminal, or `unknown` when the run has
        no such execution (`_RelationalRunStore.record_apply_progress`).

        The marker holds positions, an operation identifier and a time, never a value
        (spec 022 data-model.md "Execution progress marker").
        """
        return self._records.record_apply_progress(run_id, flow_run_id, document)

    def run_is_resumable(self, run_id: str) -> bool:
        """Whether the run's write was interrupted, the run is not cancelled, and no execution is unfinished."""
        return self._records.run_is_resumable(run_id)

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

    def interrupt_execution(
        self,
        run_id: str,
        flow_run_id: str,
        *,
        terminal_at: datetime | None = None,
        event: AuditEvent | None = None,
        secrets: Sequence[str] = (),
    ) -> bool:
        """CAS a claimed execution into the terminal interrupted/ambiguous verdict, with its event."""
        effective_terminal_at = terminal_at if terminal_at is not None else datetime.now(timezone.utc)
        _require_execution_timestamp(effective_terminal_at)
        return self._records.interrupt_execution(
            run_id, flow_run_id, terminal_at=effective_terminal_at, event=_redacted_event(event, secrets)
        )

    def commit_claimed_execution(
        self,
        run_id: str,
        flow_run_id: str,
        *,
        worker_id: str,
        terminal_at: datetime,
        terminal_state: Literal["completed", "failed", "interrupted"],
        terminal_outcome: Literal["succeeded", "failed", "ambiguous"],
        writeback: ExecutionWriteback,
        event: AuditEvent | None = None,
        secrets: Sequence[str] = (),
    ) -> bool:
        """Atomically commit one claimed verdict and its business writeback.

        A live worker that may already have reached the destination commits
        `interrupted` / `ambiguous`; the same transaction records that the run requires
        reconciliation, so the verdict and its safety evidence cannot come apart.
        """
        if not _is_canonical_uuid(worker_id):
            raise ValueError(_INVALID_SERVICE_WORKER_ID)
        if (terminal_state, terminal_outcome) not in {
            ("completed", "succeeded"),
            ("failed", "failed"),
            ("interrupted", "ambiguous"),
        }:
            msg = "claimed execution terminal verdict is invalid"
            raise ValueError(msg)
        _require_execution_timestamp(terminal_at)
        if isinstance(writeback, ExecutionFinishWriteback):
            if terminal_state == "completed" and self._records.has_pending_artifacts(run_id):
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
                baseline=_redacted_baseline(writeback.baseline, secrets),
                release_write_admission=writeback.release_write_admission,
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
            event=_redacted_event(event, secrets),
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
        event: AuditEvent | None = None,
        refusal_events: Mapping[int, AuditEvent] | None = None,
        secrets: Sequence[str] = (),
    ) -> bool:
        """Commit acknowledgement, its replay response and its event together.

        ``refusal_events`` maps the status of a late or claimed-write refusal (409 or 503) to
        the event saved with it.
        """
        _require_execution_timestamp(acknowledged_at)
        sanitized = cast("Mapping[str, Any]", _redact_value(_normalize_mapping(response_body), secrets))
        return self._records.acknowledge_execution_cancellation(
            redact(run_id, secrets),
            redact(flow_run_id, secrets),
            acknowledged_at=acknowledged_at,
            response_status=response_status,
            response_body=sanitized,
            event=_redacted_event(event, secrets),
            refusal_events={
                status: _redact_event(refusal, secrets) for status, refusal in (refusal_events or {}).items()
            },
        )

    def cancel_execution(
        self,
        run_id: str,
        flow_run_id: str,
        *,
        terminal_at: datetime | None = None,
        event: AuditEvent | None = None,
        secrets: Sequence[str] = (),
    ) -> bool:
        """Commit clean cancellation, and its event, only after durable acknowledgement and observation."""
        if terminal_at is not None:
            _require_execution_timestamp(terminal_at)
        return self._records.cancel_execution(
            run_id,
            flow_run_id,
            terminal_at=terminal_at if terminal_at is not None else datetime.now(timezone.utc),
            event=_redacted_event(event, secrets),
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

    def reserve_resume(
        self, run_id: str, receipt: MutationReceipt, *, secrets: Sequence[str] = ()
    ) -> tuple[MutationReceipt, bool]:
        """Reserve a ``resume`` request and, when the run admits it, its resume admission, atomically.

        A refused request is stored with its refusal (``run-execution-conflict``,
        ``run-not-resumable`` or ``verify-required``) on its receipt (spec 022, research R5).
        """
        sanitized_receipt = MutationReceipt.model_validate(_redact_value(receipt.model_dump(mode="json"), secrets))
        return self._records.reserve_resume(run_id, sanitized_receipt)

    def lookup_mutation(self, actor: str, key_digest: str) -> LookupResult[MutationReceipt]:
        """Look up the durable receipt for an actor and hashed client key."""
        return self._records.lookup_mutation(actor, key_digest)

    def record_approval(
        self,
        run_id: str,
        approval: RunApproval,
        receipt: MutationReceipt,
        response: Mapping[str, Any],
        *,
        events: Sequence[AuditEvent] = (),
        secrets: Sequence[str] = (),
    ) -> bool:
        """Store a run's one approval with its request's secret-safe response and events, or the stored refusal.

        Returns:
            Whether this call stored the approval; when it did not, the receipt holds the refusal
            and no event of ``events`` is written.
        """
        # Only the caller's text is redacted. The checksum and the time are written by the service,
        # and a secret that occurs inside either would corrupt the values the approval is checked by.
        sanitized_approval = approval.model_copy(
            update={
                "approved_by": redact(approval.approved_by, secrets),
                "reason": None if approval.reason is None else redact(approval.reason, secrets),
            }
        )
        sanitized = cast("Mapping[str, Any]", _redact_value(_normalize_mapping(response), secrets))
        return self._records.record_approval(
            run_id,
            sanitized_approval,
            receipt,
            sanitized,
            updated_at=datetime.now(timezone.utc),
            events=tuple(_redact_event(event, secrets) for event in events),
        )

    def cancel_planned_run(
        self,
        run_id: str,
        receipt: MutationReceipt,
        response: Mapping[str, Any],
        *,
        cancelled_at: datetime,
        events: Sequence[AuditEvent] = (),
        secrets: Sequence[str] = (),
    ) -> bool:
        """Withdraw a finished plan with its request's secret-safe response and events, or store the refusal.

        Returns:
            Whether this call withdrew the run; when it did not, the receipt holds the refusal
            and no event of ``events`` is written.
        """
        sanitized = cast("Mapping[str, Any]", _redact_value(_normalize_mapping(response), secrets))
        return self._records.cancel_planned_run(
            run_id,
            receipt,
            sanitized,
            cancelled_at=cancelled_at,
            updated_at=datetime.now(timezone.utc),
            events=tuple(_redact_event(event, secrets) for event in events),
        )

    def lookup_mutation_for_execution(
        self, run_id: str, flow_run_id: str, operation: str
    ) -> LookupResult[MutationReceipt]:
        """Look up the receipt of ``operation`` that holds a run's flow run ID, such as the request that started it."""
        return self._records.lookup_mutation_for_execution(run_id, flow_run_id, operation)

    def complete_mutation(
        self,
        receipt_id: str,
        *,
        response_status: int,
        response_body: Mapping[str, Any],
        flow_run_id: str | None,
        event: AuditEvent | None = None,
        secrets: Sequence[str] = (),
    ) -> MutationReceipt:
        """Persist the exact accepted HTTP response, Prefect identity and the decision's event together."""
        sanitized = cast("Mapping[str, Any]", _redact_value(_normalize_mapping(response_body), secrets))
        return self._records.complete_mutation(
            redact(receipt_id, secrets),
            response_status=response_status,
            response_body=sanitized,
            flow_run_id=None if flow_run_id is None else redact(flow_run_id, secrets),
            updated_at=datetime.now(timezone.utc),
            event=_redacted_event(event, secrets),
        )

    def claim_mutation(self, receipt_id: str, *, secrets: Sequence[str] = ()) -> bool:
        """Atomically make one retryable receipt the sole in-flight service caller."""
        return self._records.claim_mutation(redact(receipt_id, secrets), updated_at=datetime.now(timezone.utc))

    def release_mutation(self, receipt_id: str, *, secrets: Sequence[str] = ()) -> None:
        """Make a failed in-flight receipt available for a later retry."""
        self._records.release_mutation(redact(receipt_id, secrets), updated_at=datetime.now(timezone.utc))

    def record_audit(self, event: AuditEvent, *, secrets: Sequence[str] = ()) -> None:
        """Append one immutable, secret-safe event that saves nothing else, such as a refusal."""
        self._records.record_audit(_redact_event(event, secrets))

    def audit_events(self, run_id: str | None = None) -> tuple[AuditEvent, ...]:
        """Return all audit evidence, optionally narrowed to one Sync run."""
        return self._records.audit_events(run_id)

    def list_events(self, filters: EventFilters, *, limit: int = DEFAULT_LIMIT, offset: int = 0) -> EventPage:
        """Return one page of typed events matching ``filters``, oldest first.

        Raises:
            ValueError: ``limit`` is less than 1 or ``offset`` is negative.
        """
        return self._records.list_events(filters, limit=limit, offset=offset)

    def create_configuration(
        self,
        package: ConfigurationPackage,
        *,
        registered_by: str | None = None,
        registration_reason: str | None = None,
        secrets: Sequence[str] = (),
    ) -> ConfigurationVersion:
        """Validate, then durably register a brand-new configuration's first version.

        ``registered_by`` and ``registration_reason`` are recorded on the version with every
        value in ``secrets`` redacted.
        """
        validate_package_credentials(package)
        actor, reason = _redacted_registration(registered_by, registration_reason, secrets)
        return self._records.create_configuration(package, registered_by=actor, registration_reason=reason)

    def add_configuration_version(
        self,
        config_id: str,
        package: ConfigurationPackage,
        *,
        registered_by: str | None = None,
        registration_reason: str | None = None,
        secrets: Sequence[str] = (),
    ) -> tuple[ConfigurationVersion, bool]:
        """Validate, then atomically add a version to an existing configuration.

        ``registered_by`` and ``registration_reason`` are recorded on a created version with
        every value in ``secrets`` redacted.
        """
        validate_package_credentials(package)
        if not self._records.configuration_exists(config_id):
            msg = f"Cannot add a version to unavailable configuration ID {config_id!r}"
            raise ConfigurationNotFoundError(msg)
        actor, reason = _redacted_registration(registered_by, registration_reason, secrets)
        return self._records.add_configuration_version(
            config_id, package, registered_by=actor, registration_reason=reason
        )

    def lookup_configuration(self, config_id: str) -> LookupResult[ConfigurationSummary]:
        """Look up a configuration's registry identity by its server-generated ID."""
        return self._records.lookup_configuration(config_id)

    def lookup_configuration_version(self, config_id: str, registry_version: int) -> LookupResult[ConfigurationVersion]:
        """Look up one immutable configuration version by its integer ordinal."""
        return self._records.lookup_configuration_version(config_id, registry_version)

    def configuration_version_exists(self, config_id: str, registry_version: int) -> bool:
        """Return whether one configuration version exists, without reading or parsing its package."""
        return self._records.configuration_version_exists(config_id, registry_version)

    def list_configurations(self) -> tuple[ConfigurationSummary, ...]:
        """Return every registered configuration's identity, ordered by creation time then ID."""
        return self._records.list_configurations()

    def list_configuration_versions(self, config_id: str) -> tuple[ConfigurationVersion, ...]:
        """Return every registered version of one configuration, oldest first."""
        return self._records.list_configuration_versions(config_id)

    def list_configuration_page(
        self, *, name: str | None = None, after: tuple[datetime, str] | None = None, limit: int = DEFAULT_LIMIT
    ) -> ConfigurationPage:
        """Return one page of configurations, newest first, optionally only those named ``name``."""
        return self._records.list_configuration_page(name=name, after=after, limit=limit)

    def list_configuration_version_page(
        self, config_id: str, *, after: int | None = None, limit: int = DEFAULT_LIMIT
    ) -> ConfigurationVersionPage:
        """Return one page of a configuration's version summaries, newest version first."""
        return self._records.list_configuration_version_page(config_id, after=after, limit=limit)

    def list_runs(
        self, filters: RunFilters, *, after: tuple[datetime, str] | None = None, limit: int = DEFAULT_LIMIT
    ) -> RunPage:
        """Return one page of runs, newest first, without artifact references or execution links."""
        return self._records.list_runs(filters, after=after, limit=limit)

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
        visibility: Literal["public", "internal"] = "public",
        secrets: Sequence[str] = (),
    ) -> ArtifactReference:
        """Reserve the immutable reference, publish bytes and manifest, then expose it.

        Internal bytes are stored exactly as given. Redaction rewrites a byte sequence
        wherever it occurs, which inside a binary container corrupts the archive rather
        than protecting anything: no client ever receives these bytes, and their
        integrity is established by stored size and digest before anything parses them.
        """
        if not self._records.exists(run_id):
            msg = f"Cannot publish an artifact for unavailable Sync run ID {run_id!r}"
            raise RunNotFoundError(msg)
        sanitized = data if visibility == "internal" else _redacted_public_bytes(data, media_type, secrets)
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
            visibility=visibility,
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
                if not published:
                    self._records.mark_artifact_published(reference)
                    return reference
                # A fixed internal checkpoint has one writer — the product's own stage —
                # which repeats the publication when it loses the response rather than
                # the write. Reaching here means the request's metadata equals the
                # committed reference and the stored bytes have just been validated
                # complete against it, so this retry is the publication that already
                # succeeded. Any other artifact keeps the conflict: a repeat there is a
                # second author, not a lost answer.
                if _is_fixed_internal_checkpoint(reference):
                    return reference
                msg = f"Artifact {artifact_id!r} is already published on run {run_id!r}"
                raise DuplicateArtifactError(msg)
            if publication.reason != "manifest-unavailable":
                msg = f"Artifact {artifact_id!r} has an incomplete publication that cannot be resumed: {publication.reason}"
                raise ArtifactUnavailableError(msg)
        self._artifacts.publish(reference, sanitized)
        if not already_marked:
            self._records.mark_artifact_published(reference)
        return reference

    def lookup_configuration_baseline(self, config_id: str) -> LookupResult[ConfigurationBaseline]:
        """Return one configuration's durable source-count and full-extract reference."""
        return self._records.lookup_configuration_baseline(config_id)

    def publish_worker_environment(
        self, worker_id: str, credential_names: Iterable[str], *, observed_at: datetime | None = None
    ) -> None:
        """Record the credential variable NAMES one worker holds now; never a value (spec 020 FR-035).

        Each call replaces that worker's previous names, so the record follows the variables
        the worker holds at its latest poll.
        """
        self._records.publish_worker_environment(
            WorkerEnvironment(
                worker_id=worker_id,
                credential_names=tuple(credential_names),
                observed_at=observed_at or datetime.now(timezone.utc),
            )
        )

    def live_worker_environments(self, *, observed_after: datetime) -> tuple[WorkerEnvironment, ...]:
        """Return the publications of the workers that published at or after `observed_after`, by worker."""
        return tuple(
            environment
            for environment in self._records.worker_environments()
            if environment.observed_at >= observed_after
        )

    def lookup_artifact(self, run_id: str, artifact_id: str) -> LookupResult[bytes]:
        """Resolve only a published, public, run-owned reference, with explicit unavailability."""
        return self._resolve_artifact(run_id, artifact_id, visibility="public")

    def lookup_internal_artifact(
        self, run_id: str, artifact_id: str, *, limit: int = MAX_BUNDLE_BYTES
    ) -> LookupResult[bytes]:
        """Resolve one of the product's own internal artifacts for a later stage."""
        stored = self._internal_reference(run_id, artifact_id)
        if stored.value is None:
            return LookupResult(value=None, reason=stored.reason)
        reference = stored.value[0]
        # The committed reference already records the size, so an oversized artifact is
        # refused here without the provider being contacted at all.
        if reference.size > limit:
            return LookupResult(value=None, reason="artifact-too-large")
        return self._artifacts.read_bounded(reference, limit=limit)

    def lookup_internal_reference(self, run_id: str, artifact_id: str) -> LookupResult[ArtifactReference]:
        """Return the committed internal reference, which is a stage's bundle evidence."""
        stored = self._internal_reference(run_id, artifact_id)
        if stored.value is None:
            return LookupResult(value=None, reason=stored.reason)
        return LookupResult(value=stored.value[0])

    def _resolve_artifact(
        self, run_id: str, artifact_id: str, *, visibility: Literal["public", "internal"]
    ) -> LookupResult[bytes]:
        stored = (
            self._internal_reference(run_id, artifact_id)
            if visibility == "internal"
            else self._public_reference(run_id, artifact_id)
        )
        if stored.value is None:
            return LookupResult(value=None, reason=stored.reason)
        return self._artifacts.lookup(stored.value[0])

    def _public_reference(self, run_id: str, artifact_id: str) -> LookupResult[tuple[ArtifactReference, bool]]:
        if not self._records.exists(run_id):
            return LookupResult(value=None, reason="run-not-found")
        stored = self._records.lookup_artifact_reference(run_id, artifact_id, visibility="public")
        if stored.value is None:
            return LookupResult(value=None, reason=stored.reason)
        if not stored.value[1]:
            return LookupResult(value=None, reason="artifact-publication-incomplete")
        return stored

    def _internal_reference(self, run_id: str, artifact_id: str) -> LookupResult[tuple[ArtifactReference, bool]]:
        if not self._records.exists(run_id):
            return LookupResult(value=None, reason="run-not-found")
        stored = self._records.lookup_artifact_reference(run_id, artifact_id, visibility="internal")
        if stored.value is None:
            return LookupResult(value=None, reason=stored.reason)
        if not stored.value[1]:
            return LookupResult(value=None, reason="artifact-publication-incomplete")
        return stored

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


def _side_read_column(side: str) -> str:
    """The column that stores `side`'s read document; refuses any other side name."""
    # Compared, not looked up: `side` comes from a caller and may be any string.
    column = next((column for name, column in _SIDE_READ_COLUMNS.items() if name == side), None)
    if column is None:
        msg = f"unknown read side {side!r}; expected 'source' or 'destination'"
        raise ValueError(msg)
    return column


def _check_side_read_document(side: str, document: Mapping[str, Any]) -> None:
    """Refuse a read document that holds a field or a value the read state does not allow.

    The error names the side and the field, never the value, because a refused value may be
    an exception message or a secret.
    """
    if not isinstance(document, Mapping):
        msg = f"the {side} read document is not a mapping"
        raise ValueError(msg)  # noqa: TRY004 - one error type for every refused document
    for key in document:
        if not isinstance(key, str) or key not in _SIDE_READ_FIELDS:
            msg = f"the {side} read document holds a field that is not allowed"
            raise ValueError(msg)
    for key, value in document.items():
        if not _side_read_value_allowed(key, value):
            msg = f"the {side} read document has a value for {key!r} that is not allowed"
            raise ValueError(msg)
    if "state" not in document:
        msg = f"the {side} read document has no 'state'"
        raise ValueError(msg)


def _side_read_value_allowed(key: str, value: Any) -> bool:
    """Whether `value` is an allowed value for the read-document field `key`."""
    if key == "state":
        return isinstance(value, str) and value in _SIDE_READ_STATES
    if key == "nodes_by_kind":
        return isinstance(value, Mapping) and all(
            isinstance(kind, str) and isinstance(count, int) and not isinstance(count, bool) and count >= 0
            for kind, count in value.items()
        )
    if value is None or not isinstance(value, str):
        return value is None
    if key == "read_mode":
        return value in _SIDE_READ_MODES
    if key == "error_class":
        return len(value) <= _SIDE_READ_ERROR_CLASS_MAX_LENGTH and value.isidentifier()
    return _is_iso_timestamp(value)


def _is_iso_timestamp(value: str) -> bool:
    """Whether `value` is an ISO 8601 timestamp, the form of `started_at` and `ended_at`."""
    try:
        datetime.fromisoformat(value)
    except ValueError:
        return False
    return True


def _json(value: Any) -> str:
    return canonical_json_bytes(value).decode()


def _iso(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def _utc_iso(value: datetime) -> str:
    """Format an aware timestamp in UTC, so that stored text order equals time order.

    ``started_at`` and ``finished_at`` are ranked as text (run lists, the most recently
    applied run). An injected clock that is not UTC would otherwise break that order.
    """
    return value.astimezone(timezone.utc).isoformat()


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


def _approval_columns(approval: RunApproval | None) -> tuple[str | None, str | None, str | None, str | None]:
    """Return the four stored ``product_runs`` approval columns for ``approval``."""
    if approval is None:
        return None, None, None, None
    return approval.approved_by, _utc_iso(approval.approved_at), approval.plan_checksum, approval.reason


def _stored_apply_progress(value: str | None, *, run_id: str) -> ApplyProgress | None:
    """Parse the stored progress marker; a value that does not parse reads as ``None``.

    The marker is a progress report, so a damaged value must not stop a run from being read.
    The warning names the run and the error class, never the stored text.
    """
    if value is None:
        return None
    try:
        return ApplyProgress.model_validate_json(value)
    except ValidationError as exc:
        logger.warning(
            "Ignoring an unreadable apply-progress column: run_id=%s error=%s",
            run_id,
            type(exc).__name__,
        )
        return None


def _stored_side_read(value: str | None, *, run_id: str, column: str) -> dict[str, Any] | None:
    """Parse one stored side-read column; a value that is not a JSON object reads as ``None``.

    A side's read state is a progress report, so a damaged value must not stop a run from being
    read. The warning names the run, the column and the error class, never the stored text.
    """
    if value is None:
        return None
    try:
        document = json.loads(value)
    except (json.JSONDecodeError, TypeError) as exc:
        logger.warning(
            "Ignoring an unreadable side-read column: run_id=%s column=%s error=%s",
            run_id,
            column,
            type(exc).__name__,
        )
        return None
    if not isinstance(document, dict):
        logger.warning(
            "Ignoring a side-read column that is not a JSON object: run_id=%s column=%s type=%s",
            run_id,
            column,
            type(document).__name__,
        )
        return None
    return document


def _run_from_rows(
    row: Sequence[Any],
    references: Sequence[Sequence[Any]],
    links: Sequence[Sequence[Any]],
    service_audit_links: Sequence[Sequence[Any]],
) -> ProductRun:
    audit_links = tuple(dict.fromkeys((*json.loads(row[4]), *(str(item[0]) for item in service_audit_links))))
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
            # SQLite stores a BOOLEAN column as 0/1; PostgreSQL returns a bool.
            "reconciliation_required": bool(row[14]),
            "destination_branch": row[15],
            "approval": RunApproval.from_columns(row[16], row[17], row[18], row[19]),
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
                    "source_read": _stored_side_read(item[19], run_id=row[0], column="source_read"),
                    "destination_read": _stored_side_read(item[20], run_id=row[0], column="destination_read"),
                    "apply_progress": _stored_apply_progress(item[21], run_id=row[0]),
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


def _releases_write_admission(writeback: ExecutionFinishWriteback, terminal_state: str, terminal_outcome: str) -> bool:
    """Answer whether a finish writeback gives back the run's write admission.

    The writeback must ask for the release, and the run record it commits must show that no
    operation was dispatched: a succeeded verdict, the `planned` phase, and no applied
    operation in the summary or the results. The SQL statement adds the last two conditions:
    the admission is a `sync`'s, and the run does not require reconciliation.
    """
    return (
        writeback.release_write_admission
        and (terminal_state, terminal_outcome) == ("completed", "succeeded")
        and writeback.phase == "planned"
        and not writeback.summary.get("applied_operations")
        and not writeback.results.get("applied_operations")
    )


def _run_execution_conflict_response(run_id: str, receipt_id: str) -> dict[str, Any]:
    """The one refusal a losing plan/verify/apply/sync reservation stores on its receipt."""
    return {
        "error": {
            "code": _RUN_EXECUTION_CONFLICT,
            "message": _RUN_EXECUTION_CONFLICT_MESSAGE,
            "status": 409,
            "run_id": run_id,
            "mutation_id": receipt_id,
        }
    }


def _resume_refusal_response(code: str, message: str, run_id: str, receipt_id: str) -> dict[str, Any]:
    """The refusal a ``resume`` reservation stores on its receipt (spec 022, contracts/http-api.md)."""
    return {
        "error": {
            "code": code,
            "message": message,
            "status": 409,
            "run_id": run_id,
            "mutation_id": receipt_id,
        }
    }


def run_apply_progress(run: ProductRun) -> ApplyProgress | None:
    """Return the run's position: the highest saved progress marker over all its write executions.

    A resume stopped before its first save has no marker of its own, so taking the latest
    write execution's marker would show no progress; the highest position over every
    ``apply``, ``sync`` and ``resume`` execution is the run's position (spec 022, critique
    E2). Position 0 is a real position. Returns ``None`` when no write execution saved one.
    """
    markers = [
        link.apply_progress
        for link in run.prefect_executions
        if link.purpose in WRITE_PURPOSES and link.apply_progress is not None
    ]
    if not markers:
        return None
    return max(markers, key=lambda marker: (marker.position, marker.saved_at))


def _cancellation_ambiguous_response(run_id: str, receipt_id: str) -> dict[str, Any]:
    """The answer to cancelling a claimed write: terminal, and possibly already written."""
    return {
        "error": {
            "code": "cancellation-ambiguous",
            "message": (
                "the cancelled write may already have reached the destination; inspect it and create a new plan run"
            ),
            "status": 409,
            "run_id": run_id,
            "mutation_id": receipt_id,
        }
    }


def _cancellation_terminal_response(run_id: str, receipt_id: str) -> dict[str, Any]:
    return {
        "error": {
            "code": "execution-terminal",
            "message": "the service execution is already terminal",
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


# Event fields this module or the clock writes, never caller text, so no secret can be in them.
# Redacting them could only break their validation: a secret such as `infrahub` would turn the
# event type into text the type pattern refuses, and fail the decision the event is saved with.
_EVENT_FIXED_FIELDS = frozenset({"event_id", "event_type", "created_at", "registry_version"})


def _redact_event(event: AuditEvent, secrets: Sequence[str]) -> AuditEvent:
    """Return ``event`` with every configured secret removed from its caller-supplied values."""
    ordered = redaction_order(secrets)
    return AuditEvent.model_validate(
        {
            name: value if name in _EVENT_FIXED_FIELDS else _redact_ordered_value(value, ordered, numbers=False)
            for name, value in event.model_dump(mode="json").items()
        }
    )


def _redacted_event(event: AuditEvent | None, secrets: Sequence[str]) -> AuditEvent | None:
    return None if event is None else _redact_event(event, secrets)


def _audit_from_row(row: Sequence[Any], related: Sequence[tuple[str, str]] = ()) -> AuditEvent:
    return AuditEvent.model_validate(
        {
            "event_id": row[0],
            "run_id": row[1],
            "actor": row[2],
            "operation": row[3],
            "reason": row[4],
            "outcome": row[5],
            "created_at": row[6],
            "event_type": row[7],
            "config_id": row[8],
            "registry_version": row[9],
            "branch": row[10],
            "related": tuple({"kind": kind, "id": resource_id} for kind, resource_id in related),
        }
    )


def _generate_config_id() -> str:
    """Return a sortable, low-collision configuration identifier.

    Delegates to ``infrahub_sync.cache.paths.generate_run_id`` so the registry shares one
    identifier scheme and implementation with run IDs, rather than maintaining a second one
    that a docstring claims mirrors it but nothing enforces.
    """
    return generate_run_id()


def _redacted_registration(
    registered_by: str | None, registration_reason: str | None, secrets: Sequence[str]
) -> tuple[str | None, str | None]:
    """Return a registration's actor and reason with every value in ``secrets`` redacted."""
    return _redact_value(registered_by, secrets), _redact_value(registration_reason, secrets)


def _configuration_summary_from_row(row: Sequence[Any]) -> ConfigurationSummary:
    return ConfigurationSummary(config_id=row[0], created_at=row[1], name=row[2], latest_version=row[3])


def _configuration_version_summary_from_row(
    row: Sequence[Any], latest: Mapping[tuple[int, str], RunReference]
) -> ConfigurationVersionSummary:
    """Build one version summary; ``latest`` maps ``(registry_version, kind)`` to that run."""
    return ConfigurationVersionSummary(
        config_id=row[0],
        registry_version=row[1],
        package_checksum=row[2],
        created_at=row[3],
        registered_by=row[4],
        registration_reason=row[5],
        latest_run=latest.get((int(row[1]), "latest")),
        latest_applied_run=latest.get((int(row[1]), "applied")),
    )


def _keyset_parameters(after: tuple[datetime, str]) -> tuple[str, str, str]:
    """Return the bound values of a ``(timestamp, identifier)`` descending keyset clause.

    The clause compares the UTC ISO timestamp twice (strictly older, or equal with a
    smaller identifier), so the timestamp is bound twice before the identifier.
    """
    when = after[0].astimezone(timezone.utc).isoformat()
    return when, when, after[1]


def _filtered(statement: str, conditions: Sequence[str]) -> str:
    """Return ``statement`` with ``conditions`` joined into one ``WHERE`` clause, if any."""
    return statement + " WHERE " + " AND ".join(conditions) if conditions else statement


def _declared_configuration_name(declared_content: Any) -> str | None:
    """Return ``configuration.name`` from declared package content, or ``None`` when absent."""
    if not isinstance(declared_content, dict):
        return None
    configuration = declared_content.get("configuration")
    if not isinstance(configuration, dict):
        return None
    name = configuration.get("name")
    return name if isinstance(name, str) and name else None


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
            "visibility": row[11],
        }
    )


def _is_fixed_internal_checkpoint(reference: ArtifactReference) -> bool:
    """Whether this reference is one of the two run-bundle checkpoints the protocol fixes."""
    return reference.visibility == "internal" and reference.artifact_id in _FIXED_CHECKPOINT_ARTIFACT_IDS


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
    """Read ``(column_name, is_nullable)`` catalog rows, refusing any other row.

    A row qualifies only as exactly two cells whose second cell is already one of the
    ``yes_or_no`` strings; nothing is coerced into that domain. Nullability decides whether
    the legacy ``DROP NOT NULL`` migration still has work to do, so an answer that cannot
    report it is a provider failure rather than a default. Reading any such answer as "not
    NO" would skip the one-time migration and leave a real ``NOT NULL`` column in place,
    which no later construction would repair.
    """
    nullability: dict[str, str] = {}
    for row in rows:
        if len(row) != _CATALOG_NULLABILITY_ROW_WIDTH:
            raise ProductStoreProviderError
        is_nullable = row[1]
        if not isinstance(is_nullable, str) or is_nullable not in _CATALOG_NULLABILITY_VALUES:
            raise ProductStoreProviderError
        nullability[str(row[0])] = is_nullable
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


def _redacted_baseline(baseline: BaselineWriteback | None, secrets: Sequence[str]) -> BaselineWriteback | None:
    """Sanitize a baseline's resource names, which reach the store from a worker."""
    if baseline is None:
        return None
    return BaselineWriteback(
        source_row_counts=cast("dict[str, int]", _redact_value(baseline.source_row_counts, secrets)),
        full_extract=baseline.full_extract,
    )


def _redact_value(value: Any, secrets: Sequence[str], *, numbers: bool = False) -> Any:
    """Redact every string, and every mapping key, reachable from `value`.

    With `numbers`, a number whose JSON text carries a secret is masked whole.
    Only a published document asks for it: a stored record keeps its typed fields.
    """
    return _redact_ordered_value(value, redaction_order(secrets), numbers=numbers)


def _redact_ordered_value(value: Any, ordered: Sequence[str], *, numbers: bool) -> Any:
    """Apply `_redact_value` with values already in `redaction_order`, ordered once per document."""
    if isinstance(value, str):
        return redact_ordered(value, ordered)
    if numbers and isinstance(value, (int, float)) and not isinstance(value, bool):
        text = json.dumps(value)
        cleaned = redact_ordered(text, ordered)
        return value if cleaned == text else REDACTED
    if isinstance(value, Mapping):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            sanitized_key = redact_ordered(str(key), ordered)
            if sanitized_key in sanitized:
                msg = f"Redaction would collapse multiple mapping keys into {sanitized_key!r}"
                raise ValueError(msg)
            sanitized[sanitized_key] = _redact_ordered_value(item, ordered, numbers=numbers)
        return sanitized
    if isinstance(value, (list, tuple)):
        return [_redact_ordered_value(item, ordered, numbers=numbers) for item in value]
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


def _redacted_public_bytes(data: bytes, media_type: str, secrets: Sequence[str]) -> bytes:
    """Return public artifact bytes carrying no collected value in any form they encode it in.

    A JSON document is redacted on its decoded values first. A serializer escapes a quote, a
    backslash, a control character and, with ``ensure_ascii``, every non-ASCII letter, so the
    bytes can hold a secret in a spelling no raw match finds; the decoded strings hold it
    exactly, whatever the serializer did. Bytes are re-serialized only when something was
    replaced, so a document without a secret is published byte for byte.

    The byte pass then runs over every artifact, JSON or not, for the raw value and its JSON
    string forms: a payload that is not a JSON document, and text that quotes JSON inside it.
    """
    if _is_json_media_type(media_type):
        data = _redacted_json_bytes(data, secrets)
    return _redact_bytes(data, secrets)


def _is_json_media_type(media_type: str) -> bool:
    essence = media_type.split(";", 1)[0].strip().lower()
    return essence == "application/json" or essence.endswith("+json")


def _redacted_json_bytes(data: bytes, secrets: Sequence[str]) -> bytes:
    """Redact decoded JSON values using original and normalized number spellings."""
    # A decoded string can itself hold JSON text, such as a field that stores a serialized
    # document, so it is matched against the JSON string forms as well as the raw value.
    forms = json_string_forms(secrets)
    if not forms:
        # With no secret there is nothing to replace, so the document is not decoded.
        return data
    number_redacted = False

    def number_contains_secret(token: str, value: float) -> bool:
        """Check the document's number spelling before conversion loses it."""
        nonlocal number_redacted
        if redact_ordered(token, forms) != token or _redact_ordered_value(value, forms, numbers=True) == REDACTED:
            number_redacted = True
            return True
        return False

    def parse_integer(token: str) -> int | str:
        """Preserve integer values unless either spelling contains a secret."""
        value = int(token)
        return REDACTED if number_contains_secret(token, value) else value

    def parse_decimal(token: str) -> float | str:
        """Preserve float values unless either spelling contains a secret."""
        value = float(token)
        return REDACTED if number_contains_secret(token, value) else value

    try:
        document = json.loads(data, parse_int=parse_integer, parse_float=parse_decimal)
    except (ValueError, RecursionError):
        # Declared JSON that does not parse gets the byte pass alone.
        return data
    sanitized = _redact_ordered_value(document, forms, numbers=True)
    if not number_redacted and sanitized == document:
        return data
    return json.dumps(sanitized, ensure_ascii=False, separators=(",", ":")).encode()


def _redact_bytes(data: bytes, secrets: Sequence[str]) -> bytes:
    for form in json_string_forms(secrets):
        data = data.replace(form.encode(), REDACTED.encode())
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
