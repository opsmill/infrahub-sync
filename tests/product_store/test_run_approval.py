"""The stored approval of a run's plan: the ``product_runs`` columns and their migration.

The ``postgresql`` parameter needs ``PRODUCT_STORE_TEST_POSTGRESQL_DSN`` and runs each case in
its own generated schema, dropped afterwards.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from typing_extensions import LiteralString

from infrahub_sync.product_store import ProductRun
from infrahub_sync.product_store.models import RunApproval
from infrahub_sync.product_store.store import PostgreSQLRunStore, SQLiteRunStore
from tests.product_store.postgresql_isolation import dsn_or_skip, isolated_schema

_APPROVAL_COLUMN_NAMES = ("approved_by", "approved_at", "approved_checksum", "approval_reason")
_CHECKSUM = "a" * 64
# ``product_runs`` as it was before approval existed: the clean V3 table plus the columns
# earlier migrations added, and none of the four approval columns.
_PRE_APPROVAL_PRODUCT_RUNS_TABLE = """
CREATE TABLE product_runs (
    run_id TEXT PRIMARY KEY, operation TEXT NOT NULL, configuration_reference TEXT NOT NULL,
    actor TEXT, audit_links TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT,
    phase TEXT NOT NULL, outcome TEXT, summary TEXT NOT NULL, results TEXT NOT NULL,
    reconciliation_required BOOLEAN NOT NULL DEFAULT FALSE,
    config_id TEXT, registry_version INTEGER, package_checksum TEXT, destination_branch TEXT
)
"""
_INSERT_PRE_APPROVAL_RUN = (
    "INSERT INTO product_runs (run_id, operation, configuration_reference, actor, audit_links, started_at, "
    "phase, summary, results) VALUES ('legacy-run-001', 'plan', 'sha256:legacy', NULL, '[]', "
    "'2026-01-01T00:00:00+00:00', 'planned', '{}', '{}')"
)


@dataclass(frozen=True)
class _Backend:
    """One storage backend: how to build its run store and read its ``product_runs`` columns."""

    make_store: Callable[[], SQLiteRunStore | PostgreSQLRunStore]
    execute: Callable[[LiteralString], None]
    product_run_columns: Callable[[], list[str]]


def _sqlite_backend(database: Path) -> _Backend:
    def execute(statement: LiteralString) -> None:
        with sqlite3.connect(database) as connection:
            connection.execute(statement)

    def product_run_columns() -> list[str]:
        with sqlite3.connect(database) as connection:
            return [str(row[1]) for row in connection.execute("PRAGMA table_info(product_runs)")]

    return _Backend(lambda: SQLiteRunStore(database), execute, product_run_columns)


@pytest.fixture(name="backend", params=("sqlite", pytest.param("postgresql", marks=pytest.mark.integration)))
def _backend_fixture(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[_Backend]:
    """An empty database on SQLite or, with a reachable DSN, in a generated PostgreSQL schema."""
    if request.param == "sqlite":
        yield _sqlite_backend(tmp_path / "records.sqlite3")
        return
    schema = isolated_schema(dsn_or_skip("the run-approval migration's PostgreSQL case"))
    # pylint: disable-next=import-outside-toplevel,import-error
    import psycopg  # ty: ignore[unresolved-import] - TODO: optional service dependency

    # pylint: disable-next=import-outside-toplevel
    from infrahub_sync.service.storage import PsycopgConnectionFactory

    def connect() -> Any:  # noqa: ANN401 - the provider's own connection wrapper.
        return PsycopgConnectionFactory(psycopg.connect)(schema.dsn)

    def execute(statement: LiteralString) -> None:
        with psycopg.connect(schema.dsn) as connection:
            connection.execute(statement)
            connection.commit()

    def product_run_columns() -> list[str]:
        with psycopg.connect(schema.dsn) as connection:
            return [
                str(row[0])
                for row in connection.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = current_schema() AND table_name = 'product_runs'"
                )
            ]

    schema.create()
    try:
        yield _Backend(lambda: PostgreSQLRunStore(connect), execute, product_run_columns)
    finally:
        schema.drop()


def _approval(**overrides: object) -> RunApproval:
    values: dict[str, object] = {
        "approved_by": "reviewer@example.com",
        "approved_at": datetime(2026, 8, 8, 14, 30, tzinfo=timezone(timedelta(hours=2))),
        "plan_checksum": _CHECKSUM,
        "reason": "CHG-42 reviewed",
    }
    values.update(overrides)
    return RunApproval.model_validate(values)


def _run(*, approval: RunApproval | None = None) -> ProductRun:
    return ProductRun(
        run_id="run-001",
        operation="plan",
        configuration_reference="sha256:configuration",
        actor="operator@example.com",
        started_at=datetime(2026, 8, 8, 12, tzinfo=timezone.utc),
        finished_at=datetime(2026, 8, 8, 12, 5, tzinfo=timezone.utc),
        phase="planned",
        outcome="succeeded",
        approval=approval,
    )


def test_pre_approval_table_gains_the_approval_columns_once_and_its_rows_read_unapproved(backend: _Backend) -> None:
    backend.execute(_PRE_APPROVAL_PRODUCT_RUNS_TABLE)
    backend.execute(_INSERT_PRE_APPROVAL_RUN)

    store = backend.make_store()
    migrated = backend.product_run_columns()
    backend.make_store()
    restarted = backend.product_run_columns()

    for name in _APPROVAL_COLUMN_NAMES:
        assert migrated.count(name) == 1, name
    assert sorted(restarted) == sorted(migrated)
    loaded = store.lookup("legacy-run-001")
    assert loaded.value is not None
    assert loaded.value.approval is None


def test_fresh_database_has_the_approval_columns(backend: _Backend) -> None:
    backend.make_store()

    assert set(_APPROVAL_COLUMN_NAMES) <= set(backend.product_run_columns())


@pytest.mark.parametrize("reason", ["CHG-42 reviewed", None])
def test_stored_approval_round_trips(backend: _Backend, reason: str | None) -> None:
    store = backend.make_store()
    expected = _run(approval=_approval(reason=reason))
    store.create(expected)

    loaded = store.lookup(expected.run_id).value

    assert loaded is not None
    assert loaded.approval == expected.approval
    assert loaded.approval is not None
    assert loaded.approval.approved_at.utcoffset() == timedelta(0)


def test_run_without_approval_round_trips_as_none(backend: _Backend) -> None:
    store = backend.make_store()
    store.create(_run())

    loaded = store.lookup("run-001").value

    assert loaded is not None
    assert loaded.approval is None


@pytest.mark.parametrize(
    ("columns", "message"),
    [
        (("reviewer", None, _CHECKSUM, None), "all absent or all present"),
        ((None, "2026-08-08T12:00:00+00:00", None, None), "all absent or all present"),
        (("reviewer", "2026-08-08T12:00:00+00:00", None, "reason"), "all absent or all present"),
        ((None, None, None, "reason"), "approval reason requires a stored approval"),
    ],
)
def test_partial_approval_columns_are_refused(columns: tuple[Any, Any, Any, Any], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        RunApproval.from_columns(*columns)


def test_absent_approval_columns_read_as_no_approval() -> None:
    assert RunApproval.from_columns(None, None, None, None) is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"approved_by": ""},
        {"approved_at": datetime(2026, 8, 8, 12)},  # noqa: DTZ001 - the naive timestamp under test.
        {"plan_checksum": "A" * 64},
        {"plan_checksum": "a" * 63},
    ],
)
def test_invalid_approval_is_refused(overrides: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        _approval(**overrides)
