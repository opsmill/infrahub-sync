"""Statements for stored credentials, their revisions, the credentials a version names and those a run received.

Every function takes a cursor and the store's placeholder translator, so the caller owns the
connection and the transaction, as in ``product_store/accounts.py``. ``store.py`` imports this
module, never the reverse at run time.

The store never holds a credential value. A value is encrypted in the service layer, which owns
the key (``service/credential_crypto.py``); this module receives the key identifier, the nonce and
the ciphertext through an ``encrypt`` callable that it calls with the credential identifier and the
revision once the row is locked, so the ciphertext is bound to the row it is written to.

The database holds the nonce and the ciphertext as base64 text: the store has no binary column
on either dialect, and one schema text serves SQLite and PostgreSQL. The records hold bytes.

A function that inserts a row raises the driver's own error when a unique index refuses it. The
store method that owns the transaction turns it into one of the exceptions below and does not
chain the driver's error.
"""

from __future__ import annotations

import base64
import re
import secrets
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Literal

from infrahub_sync.product_store.models import (
    STORED_CREDENTIAL_DESCRIPTION_MAX_LENGTH,
    STORED_CREDENTIAL_NAME_PATTERN,
    CredentialBlock,
    CredentialUse,
    StoredCredential,
    StoredCredentialPage,
    StoredCredentialRevision,
    is_credential_field_set,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Mapping, Sequence

    from infrahub_sync.product_store.store import _Cursor

    # Turns the ``?`` placeholders of a statement into the placeholders of the store's driver.
    Sql = Callable[[str], str]
    # Returns the key identifier, the nonce and the ciphertext of the value, for a credential
    # identifier and a revision. It runs inside the store's transaction and must not write.
    Encrypt = Callable[[str, int], tuple[str, bytes, bytes]]

CREDENTIAL_ID_PREFIX = "cred_"
_NAME = re.compile(STORED_CREDENTIAL_NAME_PATTERN)

_CREDENTIAL_FIELDS = (
    "credential_id",
    "name",
    "description",
    "current_revision",
    "created_by",
    "created_at",
    "updated_by",
    "updated_at",
)
_INSERT_CREDENTIAL = (
    f"INSERT INTO stored_credentials ({', '.join(_CREDENTIAL_FIELDS)}) "  # noqa: S608 - module constants only.
    f"VALUES ({', '.join('?' for _ in _CREDENTIAL_FIELDS)})"
)
# The credential with the fields of its current revision.
_SELECT_CREDENTIALS = (
    f"SELECT {', '.join(f'stored_credentials.{field}' for field in _CREDENTIAL_FIELDS)}, "  # noqa: S608
    "stored_credential_revisions.fields FROM stored_credentials JOIN stored_credential_revisions "
    "ON stored_credential_revisions.credential_id = stored_credentials.credential_id "
    "AND stored_credential_revisions.revision = stored_credentials.current_revision"
)
_SELECT_CREDENTIAL_BY_NAME = f"{_SELECT_CREDENTIALS} WHERE stored_credentials.name = ?"
# Byte order on both dialects. PostgreSQL's default collation can order a hyphen differently
# from SQLite, and a name holds only lower-case letters, digits and hyphens.
_LIST_ORDER: dict[str, str] = {
    "sqlite": " ORDER BY stored_credentials.name",
    "postgresql": ' ORDER BY stored_credentials.name COLLATE "C"',
}
_LIST_PAGE = " LIMIT ? OFFSET ?"
# Changes no value and takes the lock of the credential row until the transaction ends: a row lock
# on PostgreSQL, the write lock of the database on SQLite.
_LOCK_CREDENTIAL_BY_NAME = "UPDATE stored_credentials SET updated_at = updated_at WHERE name = ?"
_SELECT_LOCKED_CREDENTIAL = "SELECT credential_id, current_revision FROM stored_credentials WHERE name = ?"
_UPDATE_CURRENT_REVISION = (
    "UPDATE stored_credentials SET current_revision = ?, updated_by = ?, updated_at = ? WHERE credential_id = ?"
)
_UPDATE_DESCRIPTION = "UPDATE stored_credentials SET description = ?, updated_by = ?, updated_at = ? WHERE name = ?"

_REVISION_FIELDS = ("credential_id", "revision", "fields", "key_id", "nonce", "ciphertext", "created_by", "created_at")
_INSERT_REVISION = (
    f"INSERT INTO stored_credential_revisions ({', '.join(_REVISION_FIELDS)}) "  # noqa: S608
    f"VALUES ({', '.join('?' for _ in _REVISION_FIELDS)})"
)
_SELECT_REVISION = (
    f"SELECT {', '.join(_REVISION_FIELDS)} FROM stored_credential_revisions "  # noqa: S608
    "WHERE credential_id = ? AND revision = ?"
)

_DELETE_REVISIONS = "DELETE FROM stored_credential_revisions WHERE credential_id = ?"
_DELETE_CREDENTIAL = "DELETE FROM stored_credentials WHERE credential_id = ?"
# The configurations whose latest version names the credential. The latest version is the highest
# one of ``configuration_versions``: the highest one that names a credential can be an older one.
_SELECT_CONFIGURATIONS_IN_USE = (
    "SELECT DISTINCT named.config_id FROM configuration_version_credentials named "
    "WHERE named.credential_name = ? AND named.registry_version = ("
    "SELECT MAX(versions.registry_version) FROM configuration_versions versions "
    "WHERE versions.config_id = named.config_id)"
)
# The schedules that are not deleted and pin a version that names the credential, latest or not.
_SELECT_SCHEDULES_IN_USE = (
    "SELECT DISTINCT schedules.name FROM schedules JOIN configuration_version_credentials named "
    "ON named.config_id = schedules.config_id AND named.registry_version = schedules.registry_version "
    "WHERE named.credential_name = ? AND schedules.state <> 'deleted'"
)

_INSERT_VERSION_CREDENTIAL = (
    "INSERT INTO configuration_version_credentials (config_id, registry_version, block, credential_name) "
    "VALUES (?, ?, ?, ?)"
)
_SELECT_VERSION_CREDENTIALS = (
    "SELECT block, credential_name FROM configuration_version_credentials "
    "WHERE config_id = ? AND registry_version = ? ORDER BY block"
)

_USE_FIELDS = (
    "run_id",
    "flow_run_id",
    "stage",
    "block",
    "credential_id",
    "credential_name",
    "revision",
    "released_at",
)
# A second release for the same execution and block, after a worker retry, replaces the row.
_DELETE_USE = "DELETE FROM run_credential_uses WHERE run_id = ? AND flow_run_id = ? AND block = ?"
_INSERT_USE = (
    f"INSERT INTO run_credential_uses ({', '.join(_USE_FIELDS)}) "  # noqa: S608
    f"VALUES ({', '.join('?' for _ in _USE_FIELDS)})"
)
_SELECT_USES = (
    f"SELECT {', '.join(_USE_FIELDS)} FROM run_credential_uses WHERE run_id = ? "  # noqa: S608
    "ORDER BY released_at, flow_run_id, block"
)


class StoredCredentialInvalidError(ValueError):
    """A name, a description or a set of fields breaks a rule of stored credentials.

    The message names the rule and repeats no submitted text.
    """


class CredentialNameTakenError(ValueError):
    """A stored credential already has this name."""


class StoredCredentialNotFoundError(LookupError):
    """No stored credential has this name."""


class CredentialInUseError(RuntimeError):
    """The latest version of a configuration, or a schedule that is not deleted, names the credential.

    ``configurations`` holds the identifiers of those configurations and ``schedules`` the names
    of those schedules, each sorted. The message repeats neither.
    """

    def __init__(self, configurations: Sequence[str], schedules: Sequence[str]) -> None:
        self.configurations = tuple(configurations)
        self.schedules = tuple(schedules)
        super().__init__("the stored credential is in use")


class VersionCredentialsMissingError(LookupError):
    """A configuration version names stored credentials that do not exist.

    ``names`` holds the missing names, sorted. Names are not secret; the message lists them.
    """

    def __init__(self, names: Sequence[str]) -> None:
        self.names = tuple(sorted(names))
        super().__init__(
            "the configuration version names stored credentials that do not exist: " + ", ".join(self.names)
        )


class CredentialIdentifierAllocationError(RuntimeError):
    """Every attempt to choose an unused credential identifier collided."""


def _utc_iso(value: datetime) -> str:
    """Format an aware time in UTC, as the other tables of the store hold their times."""
    if value.utcoffset() is None:
        msg = "stored credential timestamps must include a timezone"
        raise ValueError(msg)
    return value.astimezone(timezone.utc).isoformat()


def check_name(name: str) -> None:
    """Refuse a name that does not match ``^[a-z][a-z0-9-]{0,62}$``.

    Raises:
        StoredCredentialInvalidError: the name breaks the pattern.
    """
    if not _NAME.fullmatch(name):
        msg = "a credential name must start with a lower-case letter and hold at most 63 lower-case letters, digits and hyphens"
        raise StoredCredentialInvalidError(msg)


def check_description(description: str) -> None:
    """Refuse a description longer than 500 characters.

    The check that refuses token prefixes in free text belongs to the Sync API, which owns it.

    Raises:
        StoredCredentialInvalidError: the description is too long.
    """
    if len(description) > STORED_CREDENTIAL_DESCRIPTION_MAX_LENGTH:
        msg = f"a credential description must hold at most {STORED_CREDENTIAL_DESCRIPTION_MAX_LENGTH} characters"
        raise StoredCredentialInvalidError(msg)


def check_fields(fields: Collection[str]) -> str:
    """Return the stored form of a set of field names: sorted and comma-separated.

    Raises:
        StoredCredentialInvalidError: the set is not ``{token}``, ``{username, password}`` or
            ``{username, token}``.
    """
    if not is_credential_field_set(fields):
        msg = "a credential holds a token, a username and a password, or a username and a token"
        raise StoredCredentialInvalidError(msg)
    return ",".join(sorted(fields))


def new_credential_id() -> str:
    """Return a new random credential identifier."""
    return CREDENTIAL_ID_PREFIX + secrets.token_hex(16)


def _credential_from_row(row: Sequence[Any]) -> StoredCredential:
    # Validated: the stored field names become the record's literal field names.
    return StoredCredential.model_validate(
        {
            "credential_id": row[0],
            "name": row[1],
            "description": row[2],
            "current_revision": int(row[3]),
            "created_by": row[4],
            "created_at": row[5],
            "updated_by": row[6],
            "updated_at": row[7],
            "fields": tuple(str(row[8]).split(",")),
        }
    )


def _revision_from_row(row: Sequence[Any]) -> StoredCredentialRevision:
    return StoredCredentialRevision.model_validate(
        {
            "credential_id": row[0],
            "revision": int(row[1]),
            "fields": tuple(str(row[2]).split(",")),
            "key_id": row[3],
            "nonce": base64.b64decode(row[4]),
            "ciphertext": base64.b64decode(row[5]),
            "created_by": row[6],
            "created_at": row[7],
        }
    )


def _use_from_row(row: Sequence[Any]) -> CredentialUse:
    return CredentialUse(
        run_id=row[0],
        flow_run_id=row[1],
        stage=row[2],
        block=row[3],
        credential_id=row[4],
        credential_name=row[5],
        revision=int(row[6]),
        released_at=row[7],
    )


def _insert_revision(
    cursor: _Cursor,
    sql: Sql,
    *,
    credential_id: str,
    revision: int,
    stored_fields: str,
    encrypt: Encrypt,
    actor: str,
    at: str,
) -> None:
    key_id, nonce, ciphertext = encrypt(credential_id, revision)
    cursor.execute(
        sql(_INSERT_REVISION),
        (
            credential_id,
            revision,
            stored_fields,
            key_id,
            base64.b64encode(nonce).decode("ascii"),
            base64.b64encode(ciphertext).decode("ascii"),
            actor,
            at,
        ),
    )


def create_credential(
    cursor: _Cursor,
    sql: Sql,
    *,
    name: str,
    description: str,
    fields: Collection[str],
    encrypt: Encrypt,
    actor: str,
    now: datetime,
) -> StoredCredential:
    """Insert a credential with a new identifier and its revision 1.

    Raises:
        StoredCredentialInvalidError: the name, the description or the set of fields breaks a rule.
    """
    check_name(name)
    check_description(description)
    stored_fields = check_fields(fields)
    credential_id = new_credential_id()
    at = _utc_iso(now)
    cursor.execute(sql(_INSERT_CREDENTIAL), (credential_id, name, description, 1, actor, at, actor, at))
    _insert_revision(
        cursor,
        sql,
        credential_id=credential_id,
        revision=1,
        stored_fields=stored_fields,
        encrypt=encrypt,
        actor=actor,
        at=at,
    )
    return _credential_from_row((credential_id, name, description, 1, actor, at, actor, at, stored_fields))


def read_credential_by_name(cursor: _Cursor, sql: Sql, name: str) -> StoredCredential | None:
    """Return the credential with this name, or ``None``."""
    cursor.execute(sql(_SELECT_CREDENTIAL_BY_NAME), (name,))
    row = cursor.fetchone()
    return None if row is None else _credential_from_row(row)


def list_credentials(
    cursor: _Cursor,
    sql: Sql,
    *,
    dialect: Literal["sqlite", "postgresql"],
    offset: int,
    limit: int,
) -> StoredCredentialPage:
    """Return one page of credentials sorted by name, in byte order on both dialects."""
    if offset < 0 or limit < 1:
        msg = "a credential page needs an offset of at least 0 and a limit of at least 1"
        raise ValueError(msg)
    # One row past the page tells whether more follow.
    cursor.execute(sql(_SELECT_CREDENTIALS + _LIST_ORDER[dialect] + _LIST_PAGE), (limit + 1, offset))
    rows = cursor.fetchall()
    return StoredCredentialPage(
        items=tuple(_credential_from_row(row) for row in rows[:limit]),
        has_more=len(rows) > limit,
    )


def change_value(
    cursor: _Cursor,
    sql: Sql,
    *,
    name: str,
    fields: Collection[str],
    encrypt: Encrypt,
    actor: str,
    now: datetime,
) -> StoredCredential:
    """Lock the credential, write its next revision with the whole new set of fields, and make it current.

    Raises:
        StoredCredentialInvalidError: the set of fields breaks a rule.
        StoredCredentialNotFoundError: no credential has this name.
    """
    stored_fields = check_fields(fields)
    cursor.execute(sql(_LOCK_CREDENTIAL_BY_NAME), (name,))
    cursor.execute(sql(_SELECT_LOCKED_CREDENTIAL), (name,))
    row = cursor.fetchone()
    if row is None:
        msg = "no stored credential has this name"
        raise StoredCredentialNotFoundError(msg)
    credential_id, revision = str(row[0]), int(row[1]) + 1
    at = _utc_iso(now)
    _insert_revision(
        cursor,
        sql,
        credential_id=credential_id,
        revision=revision,
        stored_fields=stored_fields,
        encrypt=encrypt,
        actor=actor,
        at=at,
    )
    cursor.execute(sql(_UPDATE_CURRENT_REVISION), (revision, actor, at, credential_id))
    changed = read_credential_by_name(cursor, sql, name)
    if changed is None:
        msg = "no stored credential has this name"
        raise StoredCredentialNotFoundError(msg)
    return changed


def set_description(
    cursor: _Cursor, sql: Sql, *, name: str, description: str, actor: str, now: datetime
) -> StoredCredential:
    """Replace the description of a credential; its revision stays.

    Raises:
        StoredCredentialInvalidError: the description is too long.
        StoredCredentialNotFoundError: no credential has this name.
    """
    check_description(description)
    cursor.execute(sql(_UPDATE_DESCRIPTION), (description, actor, _utc_iso(now), name))
    changed = read_credential_by_name(cursor, sql, name)
    if changed is None:
        msg = "no stored credential has this name"
        raise StoredCredentialNotFoundError(msg)
    return changed


def read_in_use(cursor: _Cursor, sql: Sql, name: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return the configurations and the schedules that name the credential (FR-010, research R8).

    A configuration names it when its latest version does. A schedule names it when it is not
    deleted and the version it pins does, also when that version is an older one. The first
    tuple holds configuration identifiers and the second schedule names, each sorted in Python,
    so both dialects give the same order.
    """
    cursor.execute(sql(_SELECT_CONFIGURATIONS_IN_USE), (name,))
    configurations = tuple(sorted(str(row[0]) for row in cursor.fetchall()))
    cursor.execute(sql(_SELECT_SCHEDULES_IN_USE), (name,))
    schedules = tuple(sorted(str(row[0]) for row in cursor.fetchall()))
    return configurations, schedules


def delete_credential(cursor: _Cursor, sql: Sql, name: str) -> StoredCredential:
    """Lock the credential, check that nothing current names it, and remove it with every revision.

    The lock is the first statement, before the check. A registration that names the credential
    takes the same lock first (``lock_and_read_fields``), so one of the two waits for the other
    to commit: a deletion that waited reads the new version and is refused, and a registration
    that waited finds no credential and is refused (critique E4). A schedule that is created, or
    changed to pin another version, takes the same lock for each credential that version names
    (``lock_version_credentials``), with the same outcome. The rows of
    ``configuration_version_credentials`` and ``run_credential_uses`` stay: they hold the name,
    so an older version and a run record still show which credential they named.

    Returns:
        The credential as it was, with its last revision, for the event of the deletion.

    Raises:
        StoredCredentialNotFoundError: no credential has this name.
        CredentialInUseError: the latest version of a configuration, or a schedule that is not
            deleted, names the credential. Nothing was removed.
    """
    cursor.execute(sql(_LOCK_CREDENTIAL_BY_NAME), (name,))
    credential = read_credential_by_name(cursor, sql, name)
    if credential is None:
        msg = "no stored credential has this name"
        raise StoredCredentialNotFoundError(msg)
    configurations, schedules = read_in_use(cursor, sql, name)
    if configurations or schedules:
        raise CredentialInUseError(configurations, schedules)
    cursor.execute(sql(_DELETE_REVISIONS), (credential.credential_id,))
    cursor.execute(sql(_DELETE_CREDENTIAL), (credential.credential_id,))
    return credential


def read_revision(cursor: _Cursor, sql: Sql, credential_id: str, revision: int) -> StoredCredentialRevision | None:
    """Return one revision of a credential, with its key identifier, nonce and ciphertext."""
    cursor.execute(sql(_SELECT_REVISION), (credential_id, revision))
    row = cursor.fetchone()
    return None if row is None else _revision_from_row(row)


def lock_and_read_fields(cursor: _Cursor, sql: Sql, names: Collection[str]) -> dict[str, tuple[str, ...] | None]:
    """Lock these credentials until the transaction ends, and return the field names each one holds.

    A name that no credential has maps to ``None``. The lock is the one a value change and a
    deletion take, so a registration, or a schedule (``lock_version_credentials``), that passed
    its check cannot commit beside a deletion of a credential it names (critique E4). The names
    are locked in sorted order, so two callers cannot wait for each other.
    """
    held: dict[str, tuple[str, ...] | None] = {}
    for name in sorted(set(names)):
        cursor.execute(sql(_LOCK_CREDENTIAL_BY_NAME), (name,))
        credential = read_credential_by_name(cursor, sql, name)
        held[name] = None if credential is None else tuple(credential.fields)
    return held


def lock_version_credentials(cursor: _Cursor, sql: Sql, *, config_id: str, registry_version: int) -> None:
    """Lock the credentials a version names until the transaction ends, and check that each exists.

    A schedule that pins the version runs this in the transaction that stores it. The lock is
    the one a deletion takes first, so a deletion either commits before and the schedule is
    refused here, or waits and then finds the schedule and is refused (``read_in_use``).

    Raises:
        VersionCredentialsMissingError: a credential the version names does not exist.
    """
    named = read_version_credentials(cursor, sql, config_id=config_id, registry_version=registry_version)
    held = lock_and_read_fields(cursor, sql, named.values())
    missing = [name for name, fields in held.items() if fields is None]
    if missing:
        raise VersionCredentialsMissingError(missing)


def write_version_credentials(
    cursor: _Cursor,
    sql: Sql,
    *,
    config_id: str,
    registry_version: int,
    credentials: Mapping[CredentialBlock, str],
) -> None:
    """Record the credential name each block of a version names, in the caller's transaction.

    Raises:
        StoredCredentialInvalidError: a name breaks the pattern.
    """
    for block in sorted(credentials):
        name = credentials[block]
        check_name(name)
        cursor.execute(sql(_INSERT_VERSION_CREDENTIAL), (config_id, registry_version, block, name))


def read_version_credentials(
    cursor: _Cursor, sql: Sql, *, config_id: str, registry_version: int
) -> dict[CredentialBlock, str]:
    """Return the credential name each block of a version names; a block that names none is absent."""
    cursor.execute(sql(_SELECT_VERSION_CREDENTIALS), (config_id, registry_version))
    return {row[0]: str(row[1]) for row in cursor.fetchall()}


def record_credential_uses(cursor: _Cursor, sql: Sql, uses: Sequence[CredentialUse]) -> None:
    """Record the revision each block of a stage execution received, replacing an earlier release."""
    for use in uses:
        cursor.execute(sql(_DELETE_USE), (use.run_id, use.flow_run_id, use.block))
        cursor.execute(
            sql(_INSERT_USE),
            (
                use.run_id,
                use.flow_run_id,
                use.stage,
                use.block,
                use.credential_id,
                use.credential_name,
                use.revision,
                _utc_iso(use.released_at),
            ),
        )


def read_credential_uses(cursor: _Cursor, sql: Sql, run_id: str) -> tuple[CredentialUse, ...]:
    """Return the credentials every stage execution of a run received, oldest release first."""
    cursor.execute(sql(_SELECT_USES), (run_id,))
    return tuple(_use_from_row(row) for row in cursor.fetchall())
