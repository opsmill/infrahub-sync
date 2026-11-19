"""The deletion of a local account in the product store (spec 022, research R7, SC-007).

A deletion empties the name, the key and the password hash of one row, deletes the tokens, the
sessions and the failed sign-ins of the name, and writes its audit event in the same
transaction. No other record changes, and after it the name is found in no stored record except
the text that a person typed.

Each case runs on three profiles. ``local`` is the SQLite store. ``postgresql-emulated`` is the
PostgreSQL store class over a SQLite file: it proves the ``%s`` placeholders, not PostgreSQL's
behavior. ``postgresql`` is a real server, marked ``integration``; it needs
``PRODUCT_STORE_TEST_POSTGRESQL_DSN`` and runs each case in a generated schema that it drops.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from typing import TYPE_CHECKING, Any, cast
from uuid import uuid4

import pytest

from infrahub_sync.product_store import AuditEvent, ProductProjection, RelatedResource
from infrahub_sync.product_store import store as product_store_store
from tests.product_store.test_contract import _PROFILES_WITH_POSTGRESQL, _projection_profile

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from infrahub_sync.product_store import Account

_T0 = datetime(2026, 11, 18, 9, 0, tzinfo=timezone.utc)
_T1 = _T0 + timedelta(hours=1)
# The store reads no password, so any text stands for a hash here.
_HASH = "scrypt$32768$8$3$c2FsdA$a2V5"
# A name that no other value of the store can hold by chance, so a search for it is exact.
_NAME = "Zelda.Quartz-0022"
_SCHEMA_TEXT: str = product_store_store._SCHEMA
_CONSTRAINT_WORDS = frozenset({"PRIMARY", "FOREIGN", "UNIQUE", "CHECK", "CONSTRAINT"})


def _schema_columns() -> dict[str, tuple[str, ...]]:
    """Return each table that the schema declares, with its columns in the order of the declaration.

    The schema text is the source, so a table or a column added later is searched too. A query
    reads ``SELECT *`` and pairs each value with these names strictly, so a column the text misses
    fails the search instead of being skipped.
    """
    tables: dict[str, tuple[str, ...]] = {}
    for match in re.finditer(r"CREATE TABLE IF NOT EXISTS (\w+) \((.*?)\n\);", _SCHEMA_TEXT, re.DOTALL):
        items, depth, current = [], 0, ""
        for character in match.group(2):
            depth += {"(": 1, ")": -1}.get(character, 0)
            if character == "," and depth == 0:
                items.append(current)
                current = ""
            else:
                current += character
        items.append(current)
        words = [item.split()[0] for item in items if item.strip()]
        tables[match.group(1)] = tuple(word for word in words if word.upper() not in _CONSTRAINT_WORDS)
    return tables


_COLUMNS = _schema_columns()
_TABLES = tuple(_COLUMNS)
# The columns that hold text a person typed in a request: the optional `reason` of a run
# mutation, of a configuration registration and of an account action, stored as it was sent
# (research R7, FR-030). A person can type any name there, so the name of a deleted account can
# remain in them. Every other column holds an identifier, a time, a state, a checksum, a
# configuration or a stored answer.
_FREE_TEXT_COLUMNS = frozenset(
    {
        ("audit_events", "reason"),
        ("mutation_receipts", "reason"),
        ("product_runs", "approval_reason"),
        ("configuration_versions", "registration_reason"),
    }
)
# In a stored answer (`mutation_receipts.response_body`), the keys whose value is the reason a
# person typed: an answer repeats the reason of the request and of the approval.
_FREE_TEXT_KEYS = frozenset({"reason", "approval_reason", "registration_reason"})
# The tables of an account and of its credentials, which the deletion changes. Every other table
# keeps each of its rows; the deletion adds its own event to the two audit tables.
_ACCOUNT_TABLES = frozenset({"accounts", "account_tokens", "account_sessions", "sign_in_attempts"})


class _EventWriteError(RuntimeError):
    """The write of the deletion's event failed."""


@pytest.fixture(params=_PROFILES_WITH_POSTGRESQL)
def projection(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[ProductProjection]:
    yield from _projection_profile(request.param, tmp_path, "the account deletion's PostgreSQL parameter")


def _digest(label: str) -> str:
    """Return a digest of the shape the store holds. No token value exists behind it."""
    return sha256(label.encode()).hexdigest()


def _deletion_event(actor: str, account_id: str, *, reason: str | None = None) -> AuditEvent:
    return AuditEvent(
        event_id=f"a-{uuid4().hex}",
        run_id=None,
        actor=actor,
        operation="delete-account",
        reason=reason,
        outcome="accepted",
        created_at=_T1,
        event_type="infrahub_sync.account.deleted",
        related=tuple(
            RelatedResource(kind="account", id=identifier) for identifier in dict.fromkeys((actor, account_id))
        ),
    )


def _with_credentials(projection: ProductProjection, name: str) -> Account:
    """Create an account with one token, one session and one failed sign-in of its name."""
    account = projection.create_account(name=name, password_hash=_HASH, now=_T0)
    projection.create_account_token(account.account_id, name="ci", digest=_digest(f"token-{name}"), now=_T0)
    projection.create_account_session(account.account_id, digest=_digest(f"session-{name}"), now=_T0)
    projection.record_sign_in_failure(name.upper(), now=_T0)
    return account


def _table_rows(projection: ProductProjection) -> dict[str, list[dict[str, Any]]]:
    """Return every row of every table that the schema declares, as column names and values."""
    records = cast("Any", projection._records)
    rows: dict[str, list[dict[str, Any]]] = {}
    with records._reading() as cursor:
        for table in _TABLES:
            cursor.execute(f"SELECT * FROM {table}")  # noqa: S608 - a table name of the schema.
            rows[table] = [dict(zip(_COLUMNS[table], row, strict=True)) for row in cursor.fetchall()]
    return rows


def _paths_holding(value: object, needle: str, path: tuple[str, ...] = ()) -> list[tuple[str, ...]]:
    """Return the path of each string of a JSON value that holds the needle, in any letter case."""
    if isinstance(value, dict):
        return [found for key, item in value.items() for found in _paths_holding(item, needle, (*path, str(key)))]
    if isinstance(value, list):
        return [found for item in value for found in _paths_holding(item, needle, path)]
    return [path] if needle in str(value).lower() else []


def test_a_deletion_empties_the_name_the_key_and_the_hash_and_keeps_the_identifiers(
    projection: ProductProjection,
) -> None:
    administrator = projection.create_account(name="root", password_hash=_HASH, administrator=True, now=_T0)
    account = _with_credentials(projection, _NAME)
    other = _with_credentials(projection, "bob")
    event = _deletion_event(administrator.account_id, account.account_id, reason="asked by the person")

    deleted = projection.delete_account(account.account_id, event=event, now=_T1)

    assert deleted == account
    stored = projection.lookup_account(account.account_id)
    assert stored is not None
    assert (stored.name, stored.name_key, stored.password_hash) == (None, None, None)
    assert (stored.account_id, stored.short_id) == (account.account_id, account.short_id)
    assert (stored.deleted_at, stored.updated_at, stored.disabled) == (_T1, _T1, False)
    assert projection.list_account_tokens(account.account_id) == ()
    assert projection.list_account_sessions(account.account_id) == ()
    assert projection.read_sign_in_attempt(_NAME) is None
    assert projection.lookup_account_by_name(_NAME) is None
    assert [entry.account_id for entry in projection.list_accounts()] == [other.account_id, administrator.account_id]
    # Another account keeps its credentials and its failed sign-ins.
    assert len(projection.list_account_tokens(other.account_id)) == 1
    assert len(projection.list_account_sessions(other.account_id)) == 1
    assert projection.read_sign_in_attempt("bob") is not None
    assert projection.audit_events() == (event,)


def test_a_deleted_or_unknown_account_is_not_deleted_and_no_event_is_written(projection: ProductProjection) -> None:
    account = projection.create_account(name=_NAME, password_hash=_HASH, now=_T0)
    first = _deletion_event(account.account_id, account.account_id)
    assert projection.delete_account(account.account_id, event=first, now=_T1) is not None

    again = projection.delete_account(account.account_id, event=_deletion_event("acct_x", account.account_id), now=_T1)
    unknown = projection.delete_account("acct_" + "0" * 32, event=_deletion_event("acct_x", "acct_y"), now=_T1)

    assert (again, unknown) == (None, None)
    assert projection.audit_events() == (first,)
    stored = projection.lookup_account(account.account_id)
    assert stored is not None
    assert stored.deleted_at == _T1


def test_a_failed_event_rolls_the_whole_deletion_back(
    projection: ProductProjection, monkeypatch: pytest.MonkeyPatch
) -> None:
    account = _with_credentials(projection, _NAME)

    def fail(_cursor: object, _event: AuditEvent) -> None:
        message = "the event could not be written"
        raise _EventWriteError(message)

    monkeypatch.setattr(cast("Any", projection._records), "_insert_event", fail)

    with pytest.raises(_EventWriteError):
        projection.delete_account(
            account.account_id, event=_deletion_event(account.account_id, account.account_id), now=_T1
        )

    assert projection.lookup_account_by_name(_NAME) == account
    assert len(projection.list_account_tokens(account.account_id)) == 1
    assert len(projection.list_account_sessions(account.account_id)) == 1
    assert projection.read_sign_in_attempt(_NAME) is not None
    assert projection.audit_events() == ()


def test_the_name_of_a_deleted_account_can_be_given_to_a_new_account(projection: ProductProjection) -> None:
    account = projection.create_account(name=_NAME, password_hash=_HASH, now=_T0)
    projection.delete_account(account.account_id, event=_deletion_event(account.account_id, account.account_id))

    again = projection.create_account(name=_NAME.lower(), password_hash=_HASH, now=_T1)

    assert again.account_id != account.account_id
    assert again.short_id != account.short_id
    assert projection.lookup_account_by_name(_NAME) == again
    deleted = projection.lookup_account(account.account_id)
    assert deleted is not None
    assert deleted.name is None
    assert deleted.deleted_at is not None


def _seed_records_of_the_account(projection: ProductProjection) -> tuple[Any, dict[str, str]]:
    """Seed, through the routes of the Sync API, every kind of record that the account can make.

    The account registers a configuration, starts a run, replays the same request, and approves a
    planned run; one reason holds its name. Return the HTTP client and the header of an
    administrator who is not the account.
    """
    # pylint: disable=import-outside-toplevel
    from fastapi.testclient import TestClient

    from infrahub_sync.product_store import ProductRun
    from infrahub_sync.service.app import create_app
    from infrahub_sync.service.auth import StoreAccountResolver
    from infrahub_sync.service.config_routes import ConfigurationRoutes
    from infrahub_sync.service.credentials import digest, new_api_token
    from infrahub_sync.service.models import PlanOperationResource, PlanResource, PlanSummaryResource
    from infrahub_sync.service.service import PLAN_ARTIFACT_ID, RunService
    from tests.configuration.validation_packages import package_data
    from tests.service.account_api import Orchestration

    def headers(name: str, *, administrator: bool) -> dict[str, str]:
        account = projection.create_account(name=name, password_hash=_HASH, administrator=administrator, now=_T0)
        value = new_api_token()
        projection.create_account_token(account.account_id, name="ci", digest=digest(value), now=_T0)
        return {"Authorization": f"Bearer {value}"}

    root, zelda = headers("root", administrator=True), headers(_NAME, administrator=True)
    resolver = StoreAccountResolver(projection, idle_seconds=600)
    service = RunService(projection, cast("Any", Orchestration()), secrets=resolver.secret_values)
    routes = ConfigurationRoutes(product_projection=projection, secrets=resolver.secret_values)
    client = TestClient(create_app(service, resolver, routes))

    registered = client.post(
        "/configs",
        headers={**zelda, "Idempotency-Key": "register-1"},
        json={"package": package_data(), "reason": f"registered by {_NAME}"},
    )
    assert registered.status_code == 201, registered.text
    version = registered.json()["version"]
    run_request = {
        "operation": "plan",
        "config_id": version["config_id"],
        "registry_version": version["registry_version"],
        "reason": "nightly review",
    }
    for _ in range(2):  # The second request replays the stored answer.
        created = client.post("/runs", headers={**zelda, "Idempotency-Key": "run-1"}, json=run_request)
        assert created.status_code == 202, created.text

    zelda_id = projection.lookup_account_by_name(_NAME)
    assert zelda_id is not None
    stored_version = projection.lookup_configuration_version(version["config_id"], version["registry_version"]).value
    assert stored_version is not None
    projection.create_run(
        ProductRun(
            run_id="run-planned-001",
            operation="plan",
            configuration_reference=f"{version['config_id']}@{version['registry_version']}",
            config_id=version["config_id"],
            registry_version=version["registry_version"],
            package_checksum=stored_version.package_checksum,
            actor=zelda_id.account_id,
            started_at=_T0,
            phase="planned",
        )
    )
    plan = PlanResource(
        run_id="run-planned-001",
        checksum="c" * 64,
        checksum_ok=True,
        verification_notes=(),
        summary=PlanSummaryResource(
            by_action={"create": 1},
            by_kind={"Device": 1},
            total=1,
            delete_operations_computed=True,
            deletes_not_executed=0,
        ),
        operations=(
            PlanOperationResource(
                operation_id="op-001", action="create", kind="Device", identity={"name": "edge-01"}, tier=0
            ),
        ),
    )
    projection.publish_artifact(
        "run-planned-001",
        artifact_id=PLAN_ARTIFACT_ID,
        kind="saved-plan-review",
        media_type="application/json",
        data=plan.model_dump_json().encode(),
    )
    approved = client.post(
        "/runs/run-planned-001/approve",
        headers={**zelda, "Idempotency-Key": "approve-1"},
        json={"expected_checksum": "c" * 64, "reason": f"checked by {_NAME}"},
    )
    assert approved.status_code == 200, approved.text
    projection.create_account_session(zelda_id.account_id, digest=_digest("session"), now=_T0)
    projection.record_sign_in_failure(_NAME, now=_T0)
    return client, root


def test_after_a_deletion_the_name_is_stored_only_in_typed_text_and_no_other_record_changed(
    projection: ProductProjection,
) -> None:
    """SC-007 and FR-032a: a search of every table of the schema and of every stored answer."""
    pytest.importorskip("fastapi")
    pytest.importorskip("prefect")
    client, root = _seed_records_of_the_account(projection)
    account = projection.lookup_account_by_name(_NAME)
    assert account is not None
    before = _table_rows(projection)
    # The seed makes the name appear where a record would hold it if records held names.
    assert {row["actor"] for row in before["product_runs"]} == {account.account_id}

    deleted = client.delete(f"/accounts/{_NAME}", headers=root)

    assert deleted.status_code == 200, deleted.text
    after = _table_rows(projection)
    for table in _TABLES:
        if table in _ACCOUNT_TABLES:
            continue
        added = [row for row in after[table] if row not in before[table]]
        assert [row for row in before[table] if row not in after[table]] == [], f"{table} lost or changed a row"
        if table == "audit_events":
            assert [(row["event_type"], row["reason"]) for row in added] == [("infrahub_sync.account.deleted", None)]
        elif table == "audit_event_resources":
            assert {row["resource_id"] for row in added} >= {account.account_id}
        else:
            assert added == [], f"{table} gained a row"

    needle = _NAME.lower()
    found: set[tuple[str, str]] = set()
    answer_paths: set[tuple[str, ...]] = set()
    for table, rows in after.items():
        for row in rows:
            for column, value in row.items():
                if value is None or needle not in str(value).lower():
                    continue
                found.add((table, column))
                if (table, column) == ("mutation_receipts", "response_body"):
                    answer_paths.update(_paths_holding(json.loads(value), needle))
    in_answers = ("mutation_receipts", "response_body")
    assert found - {in_answers} <= _FREE_TEXT_COLUMNS, found
    assert all(path and path[-1] in _FREE_TEXT_KEYS for path in answer_paths), answer_paths
    # The exception is real: the reasons typed with the name keep it.
    assert ("product_runs", "approval_reason") in found
    assert ("configuration_versions", "registration_reason") in found
    assert answer_paths == {("run", "approval", "reason")}


def test_the_search_reads_every_table_and_every_column_that_the_schema_declares() -> None:
    assert len(_TABLES) == _SCHEMA_TEXT.count("CREATE TABLE IF NOT EXISTS")
    assert _COLUMNS["product_runs"][-1] == "scheduled_start_at"
    assert _COLUMNS["mutation_receipts"][-1] == "updated_at"
    assert "response_body" in _COLUMNS["mutation_receipts"]
    assert all(column in _COLUMNS[table] for table, column in _FREE_TEXT_COLUMNS)
    assert not any(word in _CONSTRAINT_WORDS for columns in _COLUMNS.values() for word in columns)
