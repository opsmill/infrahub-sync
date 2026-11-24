"""Stored credentials, their revisions, the credentials a version names and those a run received.

Each case runs on three profiles. ``local`` is the SQLite store. ``postgresql-emulated`` is the
PostgreSQL store class over a SQLite file: it proves the ``%s`` placeholders, not PostgreSQL's
behavior. ``postgresql`` is a real server, marked ``integration``; it needs
``PRODUCT_STORE_TEST_POSTGRESQL_DSN`` and runs each case in a generated schema that it drops.

The store holds no value: these tests hand it a fake ``encrypt`` that returns fixed bytes, and
check that the bytes come back unchanged and bound to the identifier and revision it was called with.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, cast

import pytest

from infrahub_sync.product_store import (
    AuditEvent,
    CredentialNameTakenError,
    CredentialUse,
    MutationReceipt,
    ProductProjection,
    StoredCredential,
    StoredCredentialInvalidError,
    StoredCredentialNotFoundError,
)
from infrahub_sync.product_store.models import CREDENTIAL_FIELD_SETS
from infrahub_sync.product_store.store import FileArtifactStore, PostgreSQLRunStore, SQLiteRunStore
from tests.product_store.postgresql_isolation import dsn_or_skip, isolated_schema
from tests.product_store.test_contract import (
    _PROFILES_WITH_POSTGRESQL,
    _configuration_package,
    _projection_profile,
    _run,
)

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

_T0 = datetime(2026, 11, 20, 9, 0, tzinfo=timezone.utc)
_ADMIN = "acct_" + "a" * 32
_OTHER_ADMIN = "acct_" + "b" * 32
_KEY_ID = "0123456789abcdef"
_FLOW_RUN = "00000000-0000-4000-8000-000000000001"


class _FakeEncrypt:
    """Return bytes derived from the credential identifier and the revision it was called with.

    No value exists behind them; the calls show that the store binds each ciphertext to its row.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    @staticmethod
    def ciphertext(credential_id: str, revision: int) -> bytes:
        return hashlib.sha256(f"{credential_id}|{revision}".encode()).digest() + b"\x00\xff"

    def __call__(self, credential_id: str, revision: int) -> tuple[str, bytes, bytes]:
        self.calls.append((credential_id, revision))
        return _KEY_ID, bytes(range(12)), self.ciphertext(credential_id, revision)


@pytest.fixture(params=_PROFILES_WITH_POSTGRESQL)
def projection(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[ProductProjection]:
    yield from _projection_profile(request.param, tmp_path, "the stored credential store's PostgreSQL parameter")


def _create(
    projection: ProductProjection,
    name: str = "netbox-lab",
    fields: tuple[str, ...] = ("token",),
    description: str = "",
    encrypt: _FakeEncrypt | None = None,
) -> StoredCredential:
    return projection.create_stored_credential(
        name=name,
        description=description,
        fields=fields,
        encrypt=encrypt or _FakeEncrypt(),
        actor=_ADMIN,
        now=_T0,
    )


def test_a_created_credential_has_revision_1_and_its_encrypted_value(projection: ProductProjection) -> None:
    encrypt = _FakeEncrypt()

    credential = _create(projection, description="NetBox lab, read-only token", encrypt=encrypt)

    assert re.fullmatch(r"cred_[0-9a-f]{32}", credential.credential_id)
    assert (credential.name, credential.description) == ("netbox-lab", "NetBox lab, read-only token")
    assert (credential.current_revision, credential.fields) == (1, ("token",))
    assert (credential.created_by, credential.updated_by) == (_ADMIN, _ADMIN)
    assert credential.created_at == credential.updated_at == _T0
    assert encrypt.calls == [(credential.credential_id, 1)]
    revision = projection.lookup_stored_credential_revision(credential.credential_id, 1)
    assert revision is not None
    assert (revision.key_id, revision.nonce, revision.fields) == (_KEY_ID, bytes(range(12)), ("token",))
    assert revision.ciphertext == _FakeEncrypt.ciphertext(credential.credential_id, 1)


def test_a_credential_is_read_by_name(projection: ProductProjection) -> None:
    created = _create(projection, fields=("username", "password"))

    assert projection.lookup_stored_credential("netbox-lab") == created
    assert created.fields == ("password", "username")
    assert projection.lookup_stored_credential("netbox-other") is None


def test_the_list_is_sorted_by_name_in_byte_order_with_offset_and_limit(projection: ProductProjection) -> None:
    # A hyphen sorts before a digit and a letter in byte order; a locale collation can ignore it.
    for name in ("ab", "a-c", "a0", "b", "a-b"):
        _create(projection, name=name)

    first = projection.list_stored_credentials(offset=0, limit=2)
    second = projection.list_stored_credentials(offset=2, limit=2)
    last = projection.list_stored_credentials(offset=4, limit=2)

    assert [item.name for item in first.items] == ["a-b", "a-c"]
    assert first.has_more
    assert [item.name for item in second.items] == ["a0", "ab"]
    assert second.has_more
    assert [item.name for item in last.items] == ["b"]
    assert not last.has_more


def test_a_value_change_writes_the_next_revision(projection: ProductProjection) -> None:
    created = _create(projection)
    encrypt = _FakeEncrypt()
    later = _T0 + timedelta(hours=1)

    changed = projection.change_stored_credential_value(
        name="netbox-lab", fields=("username", "token"), encrypt=encrypt, actor=_OTHER_ADMIN, now=later
    )

    assert encrypt.calls == [(created.credential_id, 2)]
    assert (changed.credential_id, changed.current_revision, changed.fields) == (
        created.credential_id,
        2,
        ("token", "username"),
    )
    assert (changed.created_by, changed.created_at) == (_ADMIN, _T0)
    assert (changed.updated_by, changed.updated_at) == (_OTHER_ADMIN, later)
    first = projection.lookup_stored_credential_revision(created.credential_id, 1)
    second = projection.lookup_stored_credential_revision(created.credential_id, 2)
    assert first is not None
    assert second is not None
    assert first.fields == ("token",)
    assert second.ciphertext == _FakeEncrypt.ciphertext(created.credential_id, 2)


def test_a_value_change_of_a_missing_credential_is_refused(projection: ProductProjection) -> None:
    encrypt = _FakeEncrypt()

    with pytest.raises(StoredCredentialNotFoundError):
        projection.change_stored_credential_value(
            name="netbox-lab", fields=("token",), encrypt=encrypt, actor=_ADMIN, now=_T0
        )

    assert encrypt.calls == []


def test_a_description_change_keeps_the_revision(projection: ProductProjection) -> None:
    created = _create(projection)
    later = _T0 + timedelta(minutes=5)

    changed = projection.set_stored_credential_description(
        name="netbox-lab", description="Read-only token", actor=_OTHER_ADMIN, now=later
    )

    assert changed.description == "Read-only token"
    assert changed.current_revision == created.current_revision == 1
    assert (changed.updated_by, changed.updated_at) == (_OTHER_ADMIN, later)
    assert projection.lookup_stored_credential_revision(created.credential_id, 2) is None


def test_a_description_change_of_a_missing_credential_is_refused(projection: ProductProjection) -> None:
    with pytest.raises(StoredCredentialNotFoundError):
        projection.set_stored_credential_description(name="netbox-lab", description="", actor=_ADMIN, now=_T0)


def test_a_taken_name_is_refused_and_writes_nothing(projection: ProductProjection) -> None:
    created = _create(projection)

    with pytest.raises(CredentialNameTakenError) as raised:
        _create(projection, fields=("username", "password"))

    assert raised.value.__cause__ is None
    assert projection.lookup_stored_credential("netbox-lab") == created
    assert len(projection.list_stored_credentials().items) == 1


@pytest.mark.parametrize(
    "fields",
    [
        pytest.param(("token",), id="token"),
        pytest.param(("username", "password"), id="username-password"),
        pytest.param(("username", "token"), id="username-token"),
    ],
)
def test_each_allowed_field_set_is_stored(projection: ProductProjection, fields: tuple[str, ...]) -> None:
    credential = _create(projection, fields=fields)

    assert credential.fields == tuple(sorted(fields))
    assert frozenset(credential.fields) in CREDENTIAL_FIELD_SETS


@pytest.mark.parametrize(
    "fields",
    [
        pytest.param(("username", "password", "token"), id="all-three"),
        pytest.param((), id="none"),
        pytest.param(("password",), id="password-only"),
        pytest.param(("username",), id="username-only"),
        pytest.param(("password", "token"), id="password-token"),
        pytest.param(("token", "token"), id="repeated"),
        pytest.param(("secret",), id="unknown-field"),
    ],
)
def test_a_field_set_outside_the_allowed_sets_is_refused(
    projection: ProductProjection, fields: tuple[str, ...]
) -> None:
    encrypt = _FakeEncrypt()

    with pytest.raises(StoredCredentialInvalidError):
        _create(projection, fields=fields, encrypt=encrypt)

    assert encrypt.calls == []
    assert projection.lookup_stored_credential("netbox-lab") is None


def test_a_value_change_to_all_three_fields_is_refused(projection: ProductProjection) -> None:
    _create(projection)

    with pytest.raises(StoredCredentialInvalidError):
        projection.change_stored_credential_value(
            name="netbox-lab",
            fields=("username", "password", "token"),
            encrypt=_FakeEncrypt(),
            actor=_ADMIN,
            now=_T0,
        )

    credential = projection.lookup_stored_credential("netbox-lab")
    assert credential is not None
    assert credential.current_revision == 1


@pytest.mark.parametrize(
    "name",
    [
        pytest.param("", id="empty"),
        pytest.param("NetBox", id="upper-case"),
        pytest.param("1netbox", id="digit-first"),
        pytest.param("-netbox", id="hyphen-first"),
        pytest.param("netbox_lab", id="underscore"),
        pytest.param("a" * 64, id="64-characters"),
    ],
)
def test_a_name_outside_the_pattern_is_refused(projection: ProductProjection, name: str) -> None:
    with pytest.raises(StoredCredentialInvalidError):
        _create(projection, name=name)


def test_a_name_of_63_characters_is_accepted(projection: ProductProjection) -> None:
    assert _create(projection, name="a" * 63).name == "a" * 63


def test_a_description_longer_than_500_characters_is_refused(projection: ProductProjection) -> None:
    assert _create(projection, description="d" * 500).description == "d" * 500

    with pytest.raises(StoredCredentialInvalidError):
        _create(projection, name="other", description="d" * 501)
    with pytest.raises(StoredCredentialInvalidError):
        projection.set_stored_credential_description(name="netbox-lab", description="d" * 501, actor=_ADMIN, now=_T0)


def test_the_event_is_written_with_the_credential(projection: ProductProjection) -> None:
    def event_for(credential: StoredCredential) -> AuditEvent:
        return AuditEvent(
            event_id=f"evt-{credential.name}-{credential.current_revision}",
            run_id=None,
            actor=_ADMIN,
            operation="credential.created",
            reason=None,
            outcome="accepted",
            created_at=_T0,
        )

    projection.create_stored_credential(
        name="netbox-lab",
        description="",
        fields=("token",),
        encrypt=_FakeEncrypt(),
        actor=_ADMIN,
        now=_T0,
        event_for=event_for,
    )

    # The event type of credentials belongs to the Sync API's routes; the row is read directly.
    records = cast("Any", projection._records)
    with records._reading() as cursor:
        cursor.execute(records._sql("SELECT event_id, operation FROM audit_events"))
        rows = [tuple(row) for row in cursor.fetchall()]
    assert rows == [("evt-netbox-lab-1", "credential.created")]


def test_the_credentials_a_version_names_are_recorded_with_the_version(projection: ProductProjection) -> None:
    # The test records the names itself, so the package names no stored credential.
    version = projection.create_configuration(_configuration_package(credentials=False))

    projection.record_version_credentials(
        version.config_id,
        version.registry_version,
        {"source": "netbox-lab", "destination": "infrahub-netbox-sync"},
    )

    assert projection.version_credentials(version.config_id, version.registry_version) == {
        "destination": "infrahub-netbox-sync",
        "source": "netbox-lab",
    }
    assert projection.version_credentials(version.config_id, version.registry_version + 1) == {}


def test_a_version_credential_name_outside_the_pattern_is_refused(projection: ProductProjection) -> None:
    # The test records the names itself, so the package names no stored credential.
    version = projection.create_configuration(_configuration_package(credentials=False))

    with pytest.raises(StoredCredentialInvalidError):
        projection.record_version_credentials(version.config_id, version.registry_version, {"source": "NetBox"})

    assert projection.version_credentials(version.config_id, version.registry_version) == {}


def _use(credential: StoredCredential, *, revision: int, released_at: datetime) -> CredentialUse:
    return CredentialUse(
        run_id="run-001",
        flow_run_id=_FLOW_RUN,
        stage="plan",
        block="source",
        credential_id=credential.credential_id,
        credential_name=credential.name,
        revision=revision,
        released_at=released_at,
    )


def test_a_second_release_for_the_same_execution_and_block_replaces_the_use(projection: ProductProjection) -> None:
    projection.create_run(_run())
    source = _create(projection)
    destination = _create(projection, name="infrahub-netbox-sync")
    destination_use = _use(destination, revision=1, released_at=_T0).model_copy(update={"block": "destination"})
    projection.record_credential_uses([_use(source, revision=1, released_at=_T0), destination_use])

    retried = _use(source, revision=2, released_at=_T0 + timedelta(minutes=1))
    projection.record_credential_uses([retried])

    uses = projection.credential_uses("run-001")
    assert sorted((use.block, use.revision) for use in uses) == [("destination", 1), ("source", 2)]
    assert retried in uses
    assert projection.credential_uses("run-002") == ()


def _credential_receipt(key: str = "a") -> MutationReceipt:
    return MutationReceipt(
        receipt_id=f"m-{key * 8}",
        actor=_ADMIN,
        key_digest=key * 64,
        operation="credential-create",
        request_fingerprint="f" * 64,
        reason=None,
        resource_kind="credential",
        resource_id="netbox-lab",
        created_at=_T0,
        updated_at=_T0,
    )


def test_a_credential_receipt_is_reserved(projection: ProductProjection) -> None:
    stored, created = projection.reserve_mutation(_credential_receipt())

    assert created
    assert (stored.resource_kind, stored.resource_id) == ("credential", "netbox-lab")


# The receipt check of a build before stored credentials, which does not accept the kind ``credential``.
_EARLIER_RECEIPT_CHECK = (
    "(resource_kind = 'run' AND run_id IS NOT NULL AND prefect_key IS NOT NULL) OR "
    "(resource_kind IN ('configuration', 'configuration-registry', 'schedule') AND run_id IS NULL "
    "AND prefect_key IS NULL AND target_run_id IS NULL AND flow_run_id IS NULL)"
)
_EARLIER_RECEIPT_CONSTRAINT = "mutation_receipts_resource_consistent"
_EARLIER_SQLITE_TRIGGERS = (
    "mutation_receipts_resource_consistent_insert",
    "mutation_receipts_resource_consistent_update",
)


def _earlier_sqlite_trigger(name: str, event: str) -> str:
    condition = _EARLIER_RECEIPT_CHECK.replace("resource_kind", "NEW.resource_kind")
    for column in ("run_id", "prefect_key", "target_run_id", "flow_run_id"):
        condition = re.sub(rf"(?<![\w.]){column}", f"NEW.{column}", condition)
    return (
        f"CREATE TRIGGER {name} BEFORE {event} ON mutation_receipts FOR EACH ROW WHEN NOT ({condition}) "
        "BEGIN SELECT RAISE(ABORT, 'mutation receipt resource identity is inconsistent'); END"
    )


def test_a_sqlite_database_of_an_earlier_build_accepts_a_credential_receipt_after_the_start(tmp_path: Path) -> None:
    """A database created before stored credentials holds receipt triggers that refuse the kind ``credential``.

    The start of this build replaces them, and the replacement still refuses an inconsistent receipt.
    """
    database = tmp_path / "earlier.sqlite3"
    SQLiteRunStore(database)
    with sqlite3.connect(database) as connection:
        for name in [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'")]:
            if name.startswith("mutation_receipts_"):
                connection.execute(f"DROP TRIGGER {name}")
        connection.execute(_earlier_sqlite_trigger(_EARLIER_SQLITE_TRIGGERS[0], "INSERT"))
        connection.execute(_earlier_sqlite_trigger(_EARLIER_SQLITE_TRIGGERS[1], "UPDATE"))
        connection.commit()

    projection = ProductProjection(SQLiteRunStore(database), FileArtifactStore(tmp_path / "objects"))
    _stored, created = projection.reserve_mutation(_credential_receipt())

    assert created
    with sqlite3.connect(database) as connection:
        triggers = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'")}
        assert not triggers & set(_EARLIER_SQLITE_TRIGGERS)
        with pytest.raises(sqlite3.IntegrityError, match="mutation receipt resource identity is inconsistent"):
            connection.execute(
                "INSERT INTO mutation_receipts (receipt_id, actor, key_digest, operation, request_fingerprint, "
                "resource_kind, resource_id, run_id, state, created_at, updated_at) "
                "VALUES ('m-2', 'operator', 'digest', 'credential-create', 'fingerprint', 'credential', 'x', "
                "'run-1', 'reserved', 'now', 'now')"
            )


@pytest.mark.integration
def test_a_postgresql_database_of_an_earlier_build_accepts_a_credential_receipt_after_the_start(
    tmp_path: Path,
) -> None:
    """The receipt check of an earlier build is replaced at start-up on PostgreSQL too."""
    schema = isolated_schema(dsn_or_skip("the earlier receipt check's PostgreSQL parameter"))
    schema.create()
    # pylint: disable-next=import-outside-toplevel,import-error
    import psycopg  # ty: ignore[unresolved-import] - TODO: optional service dependency

    from infrahub_sync.service.storage import PsycopgConnectionFactory

    def connect() -> Any:  # noqa: ANN401
        return PsycopgConnectionFactory(psycopg.connect)(schema.dsn)

    try:
        PostgreSQLRunStore(connect)
        with psycopg.connect(schema.dsn) as admin:
            names = [
                row[0]
                for row in admin.execute(
                    "SELECT constraint_name FROM information_schema.table_constraints "
                    "WHERE table_name = 'mutation_receipts' AND constraint_type = 'CHECK' "
                    "AND constraint_name LIKE 'mutation_receipts_resource%%' AND table_schema = %s",
                    (schema.name,),
                ).fetchall()
            ]
            for name in names:
                admin.execute(
                    psycopg.sql.SQL("ALTER TABLE mutation_receipts DROP CONSTRAINT {}").format(
                        psycopg.sql.Identifier(name)
                    )
                )
            admin.execute(
                f"ALTER TABLE mutation_receipts ADD CONSTRAINT {_EARLIER_RECEIPT_CONSTRAINT} "
                f"CHECK ({_EARLIER_RECEIPT_CHECK})"
            )

        projection = ProductProjection(PostgreSQLRunStore(connect), FileArtifactStore(tmp_path / "objects"))
        _stored, created = projection.reserve_mutation(_credential_receipt())

        assert created
        with psycopg.connect(schema.dsn) as admin:
            remaining = admin.execute(
                "SELECT 1 FROM information_schema.table_constraints WHERE table_name = 'mutation_receipts' "
                "AND constraint_name = %s AND table_schema = %s",
                (_EARLIER_RECEIPT_CONSTRAINT, schema.name),
            ).fetchone()
        assert remaining is None
    finally:
        schema.drop()
