"""The stored event: its type and resources, the migration of older stores, and the event list.

Spec 017 (`dev/specs/017-sync-run-events-logs/data-model.md`, "Event") adds a dotted type, a
configuration binding, a branch and related resources to the audit event. A store created
before that change keeps its rows: opening it fills each row's type from its
``(operation, outcome)`` pair and writes one ``account`` resource for its actor. A request
refused for a missing or invalid token keeps no type, so the event list never returns it
(research R6).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

import pytest
from pydantic import ValidationError

from infrahub_sync.product_store import (
    AuditEvent,
    EventFilters,
    ProductProjection,
    RelatedResource,
)
from infrahub_sync.product_store.store import FileArtifactStore, PostgreSQLRunStore, SQLiteRunStore
from tests.product_store.postgresql_isolation import dsn_or_skip, isolated_schema
from tests.product_store.test_contract import _connect

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path

_NOW = datetime(2026, 11, 6, 10, tzinfo=timezone.utc)
_LEGACY_AUDIT_TABLE = (
    "CREATE TABLE audit_events (event_id TEXT PRIMARY KEY, run_id TEXT, actor TEXT NOT NULL, "
    "operation TEXT NOT NULL, reason TEXT, outcome TEXT NOT NULL, created_at TEXT NOT NULL)"
)

# (event_id, run_id, actor, operation, outcome, expected event_type), from data-model.md's table.
_LEGACY_ROWS: tuple[tuple[str, str | None, str, str, str, str | None], ...] = (
    ("a-01", "run-1", "alice", "plan", "accepted", "infrahub_sync.run.accepted"),
    ("a-02", "run-1", "alice", "plan", "replayed", "infrahub_sync.run.replayed"),
    ("a-03", "run-1", "bob", "apply", "refused-checksum", "infrahub_sync.run.refused"),
    ("a-04", "run-1", "bob", "cancel", "refused-run-execution-conflict", "infrahub_sync.run.refused"),
    ("a-05", "run-1", "alice", "sync", "unavailable", "infrahub_sync.run.unavailable"),
    ("a-06", "run-1", "alice", "cancel", "accepted", "infrahub_sync.run.accepted"),
    ("a-07", None, "alice", "register-config", "accepted", "infrahub_sync.config.registered"),
    ("a-08", None, "alice", "create-config-version", "accepted", "infrahub_sync.config.version_registered"),
    ("a-09", None, "bob", "register-config", "refused-authorization", "infrahub_sync.config.refused"),
    ("a-10", None, "bob", "create-config-version", "refused-idempotency-in-progress", "infrahub_sync.config.refused"),
    ("a-11", None, "alice", "create-config-version", "replayed", "infrahub_sync.config.replayed"),
    ("a-12", None, "alice", "register-config", "unavailable", "infrahub_sync.config.unavailable"),
    ("a-13", None, "unauthenticated", "plan", "refused-authentication", None),
    ("a-14", None, "unauthenticated", "register-config", "refused-authentication", None),
)


def _event(**overrides: Any) -> AuditEvent:  # noqa: ANN401 - field values of any type.
    values: dict[str, Any] = {
        "event_id": "a-event",
        "run_id": "run-1",
        "actor": "alice",
        "operation": "plan",
        "reason": None,
        "outcome": "accepted",
        "created_at": _NOW,
        "event_type": "infrahub_sync.run.accepted",
    }
    values.update(overrides)
    return AuditEvent.model_validate(values)


# --- The event model (T002) -------------------------------------------------------------


def test_event_accepts_its_type_binding_branch_and_related_resources() -> None:
    event = _event(
        config_id="c-1",
        registry_version=3,
        branch="main",
        related=(
            {"kind": "account", "id": "alice"},
            {"kind": "config", "id": "c-1"},
            {"kind": "config_version", "id": "c-1@3"},
            {"kind": "branch", "id": "main"},
        ),
    )

    assert event.event_type == "infrahub_sync.run.accepted"
    assert (event.config_id, event.registry_version, event.branch) == ("c-1", 3, "main")
    assert event.related == (
        RelatedResource(kind="account", id="alice"),
        RelatedResource(kind="config", id="c-1"),
        RelatedResource(kind="config_version", id="c-1@3"),
        RelatedResource(kind="branch", id="main"),
    )


def test_event_without_the_new_fields_is_still_valid() -> None:
    event = AuditEvent(
        event_id="a-legacy",
        run_id=None,
        actor="unauthenticated",
        operation="plan",
        reason=None,
        outcome="refused-authentication",
        created_at=_NOW,
    )

    assert event.event_type is None
    assert (event.config_id, event.registry_version, event.branch) == (None, None, None)
    assert event.related == ()


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"run_id": "run-1", "config_id": "c-1"}, ("run", "run-1")),
        ({"run_id": None, "config_id": "c-1"}, ("config", "c-1")),
        ({"run_id": None, "config_id": None}, None),
    ],
    ids=["run", "config", "none"],
)
def test_primary_resource_is_the_run_else_the_configuration(
    overrides: dict[str, Any], expected: tuple[str, str] | None
) -> None:
    primary = _event(**overrides).primary_resource

    assert (None if primary is None else (primary.kind, primary.id)) == expected


@pytest.mark.parametrize(
    "event_type",
    [
        "infrahub_sync.run.planned",
        "infrahub_sync.run.version_registered",
        "infrahub_sync.config.version_registered",
    ],
)
def test_event_accepts_a_dotted_run_or_config_type(event_type: str) -> None:
    assert _event(event_type=event_type).event_type == event_type


@pytest.mark.parametrize(
    "event_type",
    [
        "infrahub_sync.flow.planned",
        "infrahub_sync.run.Planned",
        "infrahub_sync.run.",
        "infrahub_sync.run.planned.again",
        "run.planned",
        "infrahub_sync.run.plan-ned",
        "",
    ],
)
def test_event_refuses_a_malformed_type(event_type: str) -> None:
    with pytest.raises(ValidationError):
        _event(event_type=event_type)


@pytest.mark.parametrize("kind", ["run", "user", "Account", ""])
def test_event_refuses_a_related_resource_of_another_kind(kind: str) -> None:
    with pytest.raises(ValidationError):
        _event(related=({"kind": kind, "id": "x"},))


def test_event_refuses_a_related_resource_without_an_id() -> None:
    with pytest.raises(ValidationError):
        _event(related=({"kind": "account", "id": ""},))


def test_event_refuses_a_repeated_related_resource() -> None:
    with pytest.raises(ValidationError):
        _event(related=({"kind": "account", "id": "alice"}, {"kind": "account", "id": "alice"}))


# --- Migration of a store created before the change (T003) ------------------------------


@dataclass(frozen=True)
class _Database:
    """One database in one profile: a raw DB-API connection and the store opened over it."""

    raw: Callable[[], Any]
    placeholder: str
    open_store: Callable[[], SQLiteRunStore | PostgreSQLRunStore]

    def execute(self, statement: str, parameters: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
        connection = self.raw()
        try:
            cursor = connection.cursor()
            cursor.execute(statement.replace("?", self.placeholder), parameters)
            rows = [tuple(row) for row in cursor.fetchall()] if cursor.description else []
            connection.commit()
        finally:
            connection.close()
        return rows


_MIGRATION_PROFILES = ("sqlite", "postgresql-emulated", pytest.param("postgresql", marks=pytest.mark.integration))


@pytest.fixture(params=_MIGRATION_PROFILES)
def database(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[_Database]:
    """An empty database in each profile; a real PostgreSQL case runs in its own dropped schema."""
    path = tmp_path / "store.sqlite3"
    if request.param == "sqlite":
        yield _Database(lambda: sqlite3.connect(path), "?", lambda: SQLiteRunStore(path))
        return
    if request.param == "postgresql-emulated":
        yield _Database(lambda: sqlite3.connect(path), "?", lambda: PostgreSQLRunStore(_connect(path)))
        return
    schema = isolated_schema(dsn_or_skip("the audit-event migration's PostgreSQL parameter"))
    schema.create()
    # pylint: disable-next=import-outside-toplevel,import-error
    import psycopg  # ty: ignore[unresolved-import] - TODO: optional service dependency

    from infrahub_sync.service.storage import PsycopgConnectionFactory

    try:
        yield _Database(
            lambda: psycopg.connect(schema.dsn),
            "%s",
            lambda: PostgreSQLRunStore(lambda: PsycopgConnectionFactory(psycopg.connect)(schema.dsn)),
        )
    finally:
        schema.drop()


def _create_legacy_store(database: _Database) -> None:
    database.execute(_LEGACY_AUDIT_TABLE)
    for offset, (event_id, run_id, actor, operation, outcome, _) in enumerate(_LEGACY_ROWS):
        database.execute(
            "INSERT INTO audit_events (event_id, run_id, actor, operation, reason, outcome, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (event_id, run_id, actor, operation, None, outcome, (_NOW + timedelta(seconds=offset)).isoformat()),
        )


def _snapshot(database: _Database) -> tuple[list[tuple[Any, ...]], list[tuple[Any, ...]]]:
    events = database.execute(
        "SELECT event_id, run_id, actor, operation, reason, outcome, created_at, event_type, config_id, "
        "registry_version, branch FROM audit_events ORDER BY event_id"
    )
    resources = database.execute(
        "SELECT event_id, kind, resource_id FROM audit_event_resources ORDER BY event_id, kind, resource_id"
    )
    return events, resources


def test_opening_an_older_store_types_every_event_and_records_its_account(database: _Database) -> None:
    _create_legacy_store(database)

    database.open_store()

    events, resources = _snapshot(database)
    assert [(row[0], row[7]) for row in events] == [(row[0], row[5]) for row in _LEGACY_ROWS]
    assert all(row[8:] == (None, None, None) for row in events)
    assert resources == [(event_id, "account", actor) for event_id, _, actor, *_ in _LEGACY_ROWS]


def test_opening_a_migrated_store_again_changes_nothing(database: _Database) -> None:
    _create_legacy_store(database)
    database.open_store()
    first = _snapshot(database)

    database.open_store()

    assert _snapshot(database) == first


def test_opening_a_store_whose_columns_were_added_but_not_filled_types_its_events(database: _Database) -> None:
    """SQLite commits ALTER TABLE at once, so a start-up that failed after it left rows untyped."""
    _create_legacy_store(database)
    for column in ("event_type TEXT", "config_id TEXT", "registry_version INTEGER", "branch TEXT"):
        database.execute(f"ALTER TABLE audit_events ADD COLUMN {column}")

    database.open_store()

    events, resources = _snapshot(database)
    assert [(row[0], row[7]) for row in events] == [(row[0], row[5]) for row in _LEGACY_ROWS]
    assert resources == [(event_id, "account", actor) for event_id, _, actor, *_ in _LEGACY_ROWS]


def test_migrated_events_read_back_with_their_type_and_account(database: _Database, tmp_path: Path) -> None:
    _create_legacy_store(database)
    projection = ProductProjection(database.open_store(), FileArtifactStore(tmp_path / "objects"))

    events = {event.event_id: event for event in projection.audit_events()}

    assert events["a-07"].event_type == "infrahub_sync.config.registered"
    assert events["a-07"].related == (RelatedResource(kind="account", id="alice"),)
    assert events["a-13"].event_type is None


# --- The event list (T009) ---------------------------------------------------------------


_PROJECTION_PROFILES = ("local", "postgresql-emulated", pytest.param("postgresql", marks=pytest.mark.integration))


@pytest.fixture(params=_PROJECTION_PROFILES)
def projection(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[ProductProjection]:
    """A fresh projection in each profile; a real PostgreSQL case runs in its own dropped schema."""
    if request.param == "local":
        yield ProductProjection(SQLiteRunStore(tmp_path / "local.sqlite3"), FileArtifactStore(tmp_path / "objects"))
        return
    if request.param == "postgresql-emulated":
        yield ProductProjection(
            PostgreSQLRunStore(_connect(tmp_path / "emulated.sqlite3")), FileArtifactStore(tmp_path / "objects")
        )
        return
    schema = isolated_schema(dsn_or_skip("the event list's PostgreSQL parameter"))
    schema.create()
    # pylint: disable-next=import-outside-toplevel,import-error
    import psycopg  # ty: ignore[unresolved-import] - TODO: optional service dependency

    from infrahub_sync.service.storage import PsycopgConnectionFactory

    try:
        yield ProductProjection(
            PostgreSQLRunStore(lambda: PsycopgConnectionFactory(psycopg.connect)(schema.dsn)),
            FileArtifactStore(tmp_path / "objects"),
        )
    finally:
        schema.drop()


def _timeline() -> tuple[AuditEvent, ...]:
    """Six events over six minutes, recorded out of order by the caller."""
    return (
        _event(
            event_id="a-1",
            created_at=_NOW,
            config_id="c-1",
            registry_version=1,
            related=({"kind": "account", "id": "alice"}, {"kind": "config", "id": "c-1"}),
        ),
        _event(
            event_id="a-2",
            created_at=_NOW + timedelta(minutes=1),
            event_type="infrahub_sync.run.planned",
            config_id="c-1",
            branch="main",
            related=(
                {"kind": "account", "id": "alice"},
                {"kind": "config", "id": "c-1"},
                {"kind": "branch", "id": "main"},
            ),
        ),
        _event(
            event_id="a-3",
            run_id=None,
            created_at=_NOW + timedelta(minutes=2),
            operation="register-config",
            event_type="infrahub_sync.config.registered",
            config_id="c-2",
            related=({"kind": "account", "id": "bob"},),
        ),
        _event(
            event_id="a-4",
            run_id="run-2",
            created_at=_NOW + timedelta(minutes=3),
            event_type="infrahub_sync.run.refused",
            outcome="refused-authorization",
            related=({"kind": "account", "id": "bob"}, {"kind": "config", "id": "c-2"}),
        ),
        _event(
            event_id="a-5",
            run_id=None,
            created_at=_NOW + timedelta(minutes=4),
            actor="unauthenticated",
            outcome="refused-authentication",
            event_type=None,
        ),
        # Same time as a-4's successor: event_id breaks the tie.
        _event(
            event_id="a-0",
            run_id="run-2",
            created_at=_NOW + timedelta(minutes=5),
            event_type="infrahub_sync.run.failed",
            outcome="failed",
            related=({"kind": "account", "id": "bob"},),
        ),
        _event(
            event_id="a-6",
            run_id="run-2",
            created_at=_NOW + timedelta(minutes=5),
            event_type="infrahub_sync.run.failed",
            outcome="failed",
            related=({"kind": "account", "id": "bob"},),
        ),
    )


def _ids(projection: ProductProjection, filters: EventFilters, *, limit: int = 50, offset: int = 0) -> list[str]:
    return [event.event_id for event in projection.list_events(filters, limit=limit, offset=offset).items]


@pytest.fixture
def recorded(projection: ProductProjection) -> ProductProjection:
    for event in reversed(_timeline()):
        projection.record_audit(event)
    return projection


def test_event_list_is_in_time_order_and_leaves_out_untyped_events(recorded: ProductProjection) -> None:
    page = recorded.list_events(EventFilters(), limit=50, offset=0)

    assert [event.event_id for event in page.items] == ["a-1", "a-2", "a-3", "a-4", "a-0", "a-6"]
    assert page.has_more is False
    assert page.items[1] == _timeline()[1]


@pytest.mark.parametrize(
    ("filters", "expected"),
    [
        ({"run_id": "run-2"}, ["a-4", "a-0", "a-6"]),
        ({"config_id": "c-1"}, ["a-1", "a-2"]),
        ({"config_id": "c-2"}, ["a-3", "a-4"]),
        ({"event_types": ("infrahub_sync.run.failed", "infrahub_sync.config.registered")}, ["a-3", "a-0", "a-6"]),
        ({"related": ({"kind": "branch", "id": "main"},)}, ["a-2"]),
        ({"related": ({"kind": "account", "id": "bob"},)}, ["a-3", "a-4", "a-0", "a-6"]),
        (
            {"related": ({"kind": "account", "id": "bob"}, {"kind": "config", "id": "c-2"})},
            ["a-4"],
        ),
        ({"since": _NOW + timedelta(minutes=2)}, ["a-3", "a-4", "a-0", "a-6"]),
        ({"until": _NOW + timedelta(minutes=2)}, ["a-1", "a-2"]),
        (
            {"since": _NOW + timedelta(minutes=1), "until": _NOW + timedelta(minutes=3)},
            ["a-2", "a-3"],
        ),
        ({"since": (_NOW + timedelta(minutes=3)).astimezone(timezone(timedelta(hours=2)))}, ["a-4", "a-0", "a-6"]),
        ({"run_id": "run-404"}, []),
    ],
    ids=[
        "run",
        "config-column-or-related",
        "config-column-and-related",
        "event-types",
        "related-branch",
        "related-account",
        "related-all-of",
        "since-inclusive",
        "until-exclusive",
        "since-and-until",
        "since-other-offset",
        "unknown-run",
    ],
)
def test_event_list_filters(recorded: ProductProjection, filters: dict[str, Any], expected: list[str]) -> None:
    assert _ids(recorded, EventFilters.model_validate(filters)) == expected


def test_event_list_pages_with_limit_and_offset(recorded: ProductProjection) -> None:
    first = recorded.list_events(EventFilters(), limit=4, offset=0)
    second = recorded.list_events(EventFilters(), limit=4, offset=4)
    exact = recorded.list_events(EventFilters(), limit=2, offset=4)
    past_end = recorded.list_events(EventFilters(), limit=4, offset=10)

    assert [event.event_id for event in first.items] == ["a-1", "a-2", "a-3", "a-4"]
    assert first.has_more is True
    assert [event.event_id for event in second.items] == ["a-0", "a-6"]
    assert second.has_more is False
    assert [event.event_id for event in exact.items] == ["a-0", "a-6"]
    assert exact.has_more is False
    assert past_end.items == ()
    assert past_end.has_more is False


@pytest.mark.parametrize(("limit", "offset"), [(0, 0), (1, -1)])
def test_event_list_refuses_a_bad_limit_or_offset(recorded: ProductProjection, limit: int, offset: int) -> None:
    with pytest.raises(ValueError, match="must be"):
        recorded.list_events(EventFilters(), limit=limit, offset=offset)


@pytest.mark.parametrize(
    "filters",
    [
        {"event_types": ("infrahub_sync.flow.x",)},
        {"related": ({"kind": "run", "id": "run-1"},)},
        {"since": datetime(2026, 11, 6)},  # noqa: DTZ001 - a naive time is the refused input.
        {"run_id": "not an identifier"},
        {"unknown": "x"},
    ],
    ids=["bad-type", "bad-related-kind", "naive-since", "bad-run-id", "unknown-field"],
)
def test_event_filters_refuse_malformed_values(filters: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        EventFilters.model_validate(filters)
