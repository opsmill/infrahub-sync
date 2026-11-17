"""Statements for the local accounts of the Sync API, their API tokens and their sessions.

Every function takes a cursor and the store's placeholder translator, so the caller owns the
connection and the transaction. ``store.py`` imports this module, never the reverse at run time.

The functions apply no policy. They do not check a name against a rule, refuse a change to an
account that the environment manages, or decide that a token has expired: their callers do.

Two rules protect the values a person signs in with:

- A function receives the SHA-256 digest of a token or of a session token, never the value, and
  no record returns a digest.
- A function that inserts a row raises the driver's own error when a unique index refuses it.
  That error can quote the refused value, a digest included. The store method that owns the
  transaction turns it into one of the exceptions below and does not chain the driver's error.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Literal
from uuid import uuid4

from infrahub_sync.product_store.models import Account, AccountSession, AccountToken, ResolvedCredential

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from infrahub_sync.product_store.store import _Cursor

    # Turns the ``?`` placeholders of a statement into the placeholders of the store's driver.
    Sql = Callable[[str], str]

ACCOUNT_ID_PREFIX = "acct_"
# A use is recorded when the stored time is empty or older than this, so that a busy token or
# session causes at most one write a minute.
LAST_USED_WRITE_INTERVAL = timedelta(minutes=1)
_IDENTIFIER_BATCH = 500

_ACCOUNT_FIELDS = (
    "account_id",
    "short_id",
    "name",
    "name_key",
    "password_hash",
    "administrator",
    "disabled",
    "environment_managed",
    "created_at",
    "updated_at",
    "password_changed_at",
    "deleted_at",
)
_ACCOUNT_COLUMNS = ", ".join(f"accounts.{field}" for field in _ACCOUNT_FIELDS)
_INSERT_ACCOUNT = (
    f"INSERT INTO accounts ({', '.join(_ACCOUNT_FIELDS)}) "  # noqa: S608 - module constants only.
    f"VALUES ({', '.join('?' for _ in _ACCOUNT_FIELDS)})"
)
_SELECT_ACCOUNT = f"SELECT {_ACCOUNT_COLUMNS} FROM accounts WHERE accounts.account_id = ?"  # noqa: S608
_SELECT_ACCOUNT_BY_NAME_KEY = f"SELECT {_ACCOUNT_COLUMNS} FROM accounts WHERE accounts.name_key = ?"  # noqa: S608
_SELECT_ACCOUNTS_BY_ID = f"SELECT {_ACCOUNT_COLUMNS} FROM accounts WHERE accounts.account_id IN ({{}})"  # noqa: S608
# A deleted account has no name, so it is in no list.
_SELECT_ACCOUNT_LIST = f"SELECT {_ACCOUNT_COLUMNS} FROM accounts WHERE accounts.deleted_at IS NULL"  # noqa: S608
_ACCOUNT_LIST_DISABLED = " AND accounts.disabled = ?"
_ACCOUNT_LIST_ORDER = " ORDER BY accounts.name_key, accounts.account_id"
# A deleted account keeps its row and takes no further change.
_UPDATE_ACCOUNT_LEVEL = (
    "UPDATE accounts SET administrator = ?, updated_at = ? WHERE account_id = ? AND deleted_at IS NULL"
)
_UPDATE_ACCOUNT_PASSWORD_HASH = (
    "UPDATE accounts SET password_hash = ?, password_changed_at = ?, updated_at = ? "
    "WHERE account_id = ? AND deleted_at IS NULL"
)
_UPDATE_ACCOUNT_DISABLED = (
    "UPDATE accounts SET disabled = ?, updated_at = ? WHERE account_id = ? AND deleted_at IS NULL"
)

_TOKEN_COLUMNS = "token_id, account_id, name, created_at, expires_at, last_used_at, environment_managed"
_INSERT_TOKEN = (
    "INSERT INTO account_tokens (token_id, account_id, name, digest, created_at, expires_at, last_used_at, "
    "environment_managed) VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
)
_SELECT_TOKENS = f"SELECT {_TOKEN_COLUMNS} FROM account_tokens WHERE account_id = ? ORDER BY created_at, token_id"  # noqa: S608
_TOKEN_NAME_EXISTS = "SELECT 1 FROM account_tokens WHERE account_id = ? AND name = ?"
_DELETE_TOKEN = "DELETE FROM account_tokens WHERE account_id = ? AND token_id = ?"
_DELETE_TOKENS = "DELETE FROM account_tokens WHERE account_id = ?"
_UPDATE_TOKEN_LAST_USED = "UPDATE account_tokens SET last_used_at = ? WHERE token_id = ?"

_SESSION_COLUMNS = "session_id, account_id, created_at, last_used_at"
_INSERT_SESSION = (
    "INSERT INTO account_sessions (session_id, account_id, digest, created_at, last_used_at) VALUES (?, ?, ?, ?, ?)"
)
_SELECT_SESSIONS = (
    f"SELECT {_SESSION_COLUMNS} FROM account_sessions WHERE account_id = ? "  # noqa: S608 - module constants only.
    "ORDER BY created_at, session_id"
)
_DELETE_SESSION = "DELETE FROM account_sessions WHERE session_id = ?"
_DELETE_SESSIONS = "DELETE FROM account_sessions WHERE account_id = ?"
_UPDATE_SESSION_LAST_USED = "UPDATE account_sessions SET last_used_at = ? WHERE session_id = ?"

# The row of a digest with its account: the row identifier, the expiry date, the last recorded
# use, then the account columns.
_SELECT_TOKEN_BY_DIGEST = (
    f"SELECT account_tokens.token_id, account_tokens.expires_at, account_tokens.last_used_at, {_ACCOUNT_COLUMNS} "  # noqa: S608
    "FROM account_tokens JOIN accounts ON accounts.account_id = account_tokens.account_id "
    "WHERE account_tokens.digest = ?"
)
_SELECT_SESSION_BY_DIGEST = (
    f"SELECT account_sessions.session_id, NULL, account_sessions.last_used_at, {_ACCOUNT_COLUMNS} "  # noqa: S608
    "FROM account_sessions JOIN accounts ON accounts.account_id = account_sessions.account_id "
    "WHERE account_sessions.digest = ?"
)
_CREDENTIAL_PREFIX_COLUMNS = 3
# A token is looked for first: the token of the account that the environment manages has no prefix
# that tells its kind, so the kind comes from the table that holds the digest.
_CREDENTIAL_STATEMENTS: tuple[tuple[Literal["token", "session"], str, str], ...] = (
    ("token", _SELECT_TOKEN_BY_DIGEST, _UPDATE_TOKEN_LAST_USED),
    ("session", _SELECT_SESSION_BY_DIGEST, _UPDATE_SESSION_LAST_USED),
)


class AccountNameTakenError(ValueError):
    """An account that is not deleted already has this name, in any letter case."""


class AccountTokenNameTakenError(ValueError):
    """The account already has an API token with this name."""


class AccountIdentifierAllocationError(RuntimeError):
    """Every attempt to choose an unused account identifier and short identifier collided."""


class DuplicateCredentialError(ValueError):
    """The store already holds this token or session digest, or the generated row identifier."""


def _utc_iso(value: datetime) -> str:
    """Format an aware time in UTC, as the other tables of the store hold their times."""
    if value.utcoffset() is None:
        msg = "account timestamps must include a timezone"
        raise ValueError(msg)
    return value.astimezone(timezone.utc).isoformat()


def _optional_utc_iso(value: datetime | None) -> str | None:
    return None if value is None else _utc_iso(value)


def _account_from_row(row: Sequence[Any]) -> Account:
    return Account(
        account_id=row[0],
        short_id=row[1],
        name=row[2],
        name_key=row[3],
        password_hash=row[4],
        administrator=bool(row[5]),
        disabled=bool(row[6]),
        environment_managed=bool(row[7]),
        created_at=row[8],
        updated_at=row[9],
        password_changed_at=row[10],
        deleted_at=row[11],
    )


def _token_from_row(row: Sequence[Any]) -> AccountToken:
    return AccountToken(
        token_id=row[0],
        account_id=row[1],
        name=row[2],
        created_at=row[3],
        expires_at=row[4],
        last_used_at=row[5],
        environment_managed=bool(row[6]),
    )


def _session_from_row(row: Sequence[Any]) -> AccountSession:
    return AccountSession(session_id=row[0], account_id=row[1], created_at=row[2], last_used_at=row[3])


def name_key(name: str) -> str:
    """Return the lower-case key that makes an account name unique without regard to case."""
    return name.lower()


def create_account(
    cursor: _Cursor,
    sql: Sql,
    *,
    name: str,
    password_hash: str | None,
    administrator: bool = False,
    environment_managed: bool = False,
    now: datetime,
) -> Account:
    """Insert an active account with a new identifier and a new short identifier.

    The unique indexes refuse a name that is taken and an identifier that is in use, with the
    driver's error. The store method reads the name again to tell the two cases apart.
    """
    created_at = _utc_iso(now)
    row = (
        ACCOUNT_ID_PREFIX + secrets.token_hex(16),
        secrets.token_hex(4),
        name,
        name_key(name),
        password_hash,
        administrator,
        False,
        environment_managed,
        created_at,
        created_at,
        created_at,
        None,
    )
    cursor.execute(sql(_INSERT_ACCOUNT), row)
    return _account_from_row(row)


def read_account(cursor: _Cursor, sql: Sql, account_id: str) -> Account | None:
    """Return the account with this identifier, deleted or not."""
    cursor.execute(sql(_SELECT_ACCOUNT), (account_id,))
    row = cursor.fetchone()
    return None if row is None else _account_from_row(row)


def read_account_by_name(cursor: _Cursor, sql: Sql, name: str) -> Account | None:
    """Return the account that has this name, in any letter case. A deleted account has no name."""
    cursor.execute(sql(_SELECT_ACCOUNT_BY_NAME_KEY), (name_key(name),))
    row = cursor.fetchone()
    return None if row is None else _account_from_row(row)


def read_accounts(cursor: _Cursor, sql: Sql, account_ids: Sequence[str]) -> dict[str, Account]:
    """Return the accounts among these identifiers, deleted or not, by identifier."""
    wanted = sorted(set(account_ids))
    found: dict[str, Account] = {}
    for start in range(0, len(wanted), _IDENTIFIER_BATCH):
        batch = wanted[start : start + _IDENTIFIER_BATCH]
        cursor.execute(sql(_SELECT_ACCOUNTS_BY_ID.format(", ".join("?" for _ in batch))), batch)
        for row in cursor.fetchall():
            account = _account_from_row(row)
            found[account.account_id] = account
    return found


def list_accounts(cursor: _Cursor, sql: Sql, *, disabled: bool | None = None) -> tuple[Account, ...]:
    """Return the accounts that are not deleted, by name; only one state when ``disabled`` is given."""
    statement = _SELECT_ACCOUNT_LIST
    parameters: tuple[Any, ...] = ()
    if disabled is not None:
        statement += _ACCOUNT_LIST_DISABLED
        parameters = (disabled,)
    cursor.execute(sql(statement + _ACCOUNT_LIST_ORDER), parameters)
    return tuple(_account_from_row(row) for row in cursor.fetchall())


def set_account_level(cursor: _Cursor, sql: Sql, account_id: str, *, administrator: bool, now: datetime) -> bool:
    """Set whether the account is an administrator. Return whether an account changed."""
    cursor.execute(sql(_UPDATE_ACCOUNT_LEVEL), (administrator, _utc_iso(now), account_id))
    return cursor.rowcount == 1


def set_account_password_hash(cursor: _Cursor, sql: Sql, account_id: str, *, password_hash: str, now: datetime) -> bool:
    """Replace the password hash and record the time of the change. Return whether an account changed."""
    changed_at = _utc_iso(now)
    cursor.execute(sql(_UPDATE_ACCOUNT_PASSWORD_HASH), (password_hash, changed_at, changed_at, account_id))
    return cursor.rowcount == 1


def disable_account(cursor: _Cursor, sql: Sql, account_id: str, *, now: datetime) -> bool:
    """Disable the account and delete its tokens and its sessions. Return whether an account changed."""
    cursor.execute(sql(_UPDATE_ACCOUNT_DISABLED), (True, _utc_iso(now), account_id))
    if cursor.rowcount != 1:
        return False
    delete_tokens(cursor, sql, account_id)
    delete_sessions(cursor, sql, account_id)
    return True


def enable_account(cursor: _Cursor, sql: Sql, account_id: str, *, now: datetime) -> bool:
    """Enable the account. It has no token and no session until it creates one."""
    cursor.execute(sql(_UPDATE_ACCOUNT_DISABLED), (False, _utc_iso(now), account_id))
    return cursor.rowcount == 1


def create_token(
    cursor: _Cursor,
    sql: Sql,
    account_id: str,
    *,
    name: str,
    digest: str,
    expires_at: datetime | None = None,
    environment_managed: bool = False,
    now: datetime,
) -> AccountToken:
    """Insert an API token row that holds the digest of the token value."""
    token = AccountToken(
        token_id=str(uuid4()),
        account_id=account_id,
        name=name,
        created_at=now,
        expires_at=expires_at,
        environment_managed=environment_managed,
    )
    cursor.execute(
        sql(_INSERT_TOKEN),
        (
            token.token_id,
            account_id,
            name,
            digest,
            _utc_iso(now),
            _optional_utc_iso(expires_at),
            None,
            environment_managed,
        ),
    )
    return token


def token_name_exists(cursor: _Cursor, sql: Sql, account_id: str, name: str) -> bool:
    """Return whether the account has an API token with this name."""
    cursor.execute(sql(_TOKEN_NAME_EXISTS), (account_id, name))
    return cursor.fetchone() is not None


def list_tokens(cursor: _Cursor, sql: Sql, account_id: str) -> tuple[AccountToken, ...]:
    """Return the API tokens of the account, oldest first."""
    cursor.execute(sql(_SELECT_TOKENS), (account_id,))
    return tuple(_token_from_row(row) for row in cursor.fetchall())


def delete_token(cursor: _Cursor, sql: Sql, account_id: str, token_id: str) -> bool:
    """Delete one API token of the account. Return whether a token was deleted."""
    cursor.execute(sql(_DELETE_TOKEN), (account_id, token_id))
    return cursor.rowcount == 1


def delete_tokens(cursor: _Cursor, sql: Sql, account_id: str) -> int:
    """Delete every API token of the account. Return the number deleted."""
    cursor.execute(sql(_DELETE_TOKENS), (account_id,))
    return cursor.rowcount


def create_session(cursor: _Cursor, sql: Sql, account_id: str, *, digest: str, now: datetime) -> AccountSession:
    """Insert a session row that holds the digest of the session token; its first use is ``now``."""
    session = AccountSession(session_id=str(uuid4()), account_id=account_id, created_at=now, last_used_at=now)
    created_at = _utc_iso(now)
    cursor.execute(sql(_INSERT_SESSION), (session.session_id, account_id, digest, created_at, created_at))
    return session


def list_sessions(cursor: _Cursor, sql: Sql, account_id: str) -> tuple[AccountSession, ...]:
    """Return the sessions of the account, oldest first."""
    cursor.execute(sql(_SELECT_SESSIONS), (account_id,))
    return tuple(_session_from_row(row) for row in cursor.fetchall())


def delete_session(cursor: _Cursor, sql: Sql, session_id: str) -> bool:
    """Delete one session. Return whether a session was deleted."""
    cursor.execute(sql(_DELETE_SESSION), (session_id,))
    return cursor.rowcount == 1


def delete_sessions(cursor: _Cursor, sql: Sql, account_id: str) -> int:
    """Delete every session of the account. Return the number deleted."""
    cursor.execute(sql(_DELETE_SESSIONS), (account_id,))
    return cursor.rowcount


def resolve_digest(cursor: _Cursor, sql: Sql, digest: str, *, now: datetime) -> ResolvedCredential | None:
    """Return the API token or the session that holds this digest, with its account.

    The use is recorded when the stored time is empty or more than a minute old. The result
    carries the time stored before this call, so the caller measures an idle period from the
    previous use. The function refuses nothing: the caller checks the account's state, the
    expiry date of a token and the idle period of a session.
    """
    for kind, select, update in _CREDENTIAL_STATEMENTS:
        cursor.execute(sql(select), (digest,))
        row = cursor.fetchone()
        if row is None:
            continue
        resolved = ResolvedCredential(
            account=_account_from_row(row[_CREDENTIAL_PREFIX_COLUMNS:]),
            kind=kind,
            credential_id=row[0],
            expires_at=row[1],
            last_used_at=row[2],
        )
        previous = resolved.last_used_at
        if previous is None or now - previous > LAST_USED_WRITE_INTERVAL:
            cursor.execute(sql(update), (_utc_iso(now), resolved.credential_id))
        return resolved
    return None
