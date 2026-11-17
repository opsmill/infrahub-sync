"""The stored event: its type and resources, and the event list.

Spec 012 (`dev/specs/archive/012-sync-run-events-logs/data-model.md`, "Event") gives the audit
event a dotted type, a configuration binding, a branch and related resources. A request
refused for a missing or invalid token keeps no type, so the event list never returns it
(research R6).
"""

from __future__ import annotations

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
    from collections.abc import Iterator
    from pathlib import Path

_NOW = datetime(2026, 11, 6, 10, tzinfo=timezone.utc)


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


def test_redaction_keeps_the_event_type_and_redacts_caller_text(projection: ProductProjection) -> None:
    """A secret that is part of the fixed event type, such as `infrahub`, must not break the event."""
    projection.record_audit(
        _event(
            event_type="infrahub_sync.run.planned",
            actor="infrahub-admin",
            related=({"kind": "account", "id": "infrahub-admin"},),
        ),
        secrets=("infrahub",),
    )

    (stored,) = projection.audit_events("run-1")
    assert stored.event_type == "infrahub_sync.run.planned"
    assert stored.actor == "***-admin"
    assert stored.related == (RelatedResource(kind="account", id="***-admin"),)
