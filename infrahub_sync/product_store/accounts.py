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

from infrahub_sync.product_store.models import (
    Account,
    AccountSession,
    AccountToken,
    ResolvedCredential,
    SignInAttempt,
)

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
# The refusal after repeated failed sign-ins for one name (research R6): the failures of one
# window are counted, and the name is refused for a period when the count reaches the limit.
SIGN_IN_FAILURE_WINDOW = timedelta(minutes=15)
SIGN_IN_FAILURE_LIMIT = 5
SIGN_IN_REFUSAL_PERIOD = timedelta(minutes=15)
# The largest number of rows of ``sign_in_attempts``, so that failed sign-ins for names that no
# account has cannot fill the store.
SIGN_IN_ATTEMPTS_BOUND = 10_000
# The longest name that gets a row. A longer name is refused by the caller before any write.
SIGN_IN_NAME_MAX_LENGTH = 64

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
# The deletion empties what names the account and what signs it in, and keeps the two identifiers
# that every record holds or displays (research R7).
_UPDATE_ACCOUNT_DELETED = (
    "UPDATE accounts SET name = NULL, name_key = NULL, password_hash = NULL, deleted_at = ?, updated_at = ? "
    "WHERE account_id = ? AND deleted_at IS NULL"
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

# The statements that apply the account the environment defines. The token digest is compared in
# SQL, so no digest is read out of the store.
_UPDATE_ACCOUNT_ENVIRONMENT_FLAGS = (
    "UPDATE accounts SET administrator = ?, disabled = ?, environment_managed = ?, updated_at = ? WHERE account_id = ?"
)
_COUNT_ENVIRONMENT_TOKENS = "SELECT COUNT(*) FROM account_tokens WHERE account_id = ? AND environment_managed = ?"
_COUNT_MATCHING_ENVIRONMENT_TOKENS = (
    "SELECT COUNT(*) FROM account_tokens "
    "WHERE account_id = ? AND environment_managed = ? AND name = ? AND digest = ? AND expires_at IS NULL"
)
_DELETE_ENVIRONMENT_TOKENS = "DELETE FROM account_tokens WHERE account_id = ? AND environment_managed = ?"
_DELETE_SIGN_IN_ATTEMPTS = "DELETE FROM sign_in_attempts WHERE name_key = ?"
_SELECT_SIGN_IN_ATTEMPT = (
    "SELECT name_key, failed_count, window_started_at, refused_until FROM sign_in_attempts WHERE name_key = ?"
)
_INSERT_SIGN_IN_ATTEMPT = (
    "INSERT INTO sign_in_attempts (name_key, failed_count, window_started_at, refused_until) VALUES (?, ?, ?, ?)"
)
_UPDATE_SIGN_IN_ATTEMPT = (
    "UPDATE sign_in_attempts SET failed_count = ?, window_started_at = ?, refused_until = ? WHERE name_key = ?"
)
_COUNT_SIGN_IN_ATTEMPTS = "SELECT COUNT(*) FROM sign_in_attempts"
# Every time of the table is written by ``_utc_iso``, so the order of the text is the order of
# the times.
_DELETE_OLDEST_SIGN_IN_ATTEMPTS = (
    "DELETE FROM sign_in_attempts WHERE name_key IN ("
    "SELECT name_key FROM sign_in_attempts ORDER BY window_started_at, name_key LIMIT ?)"
)

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


def delete_account(cursor: _Cursor, sql: Sql, account_id: str, *, now: datetime) -> Account | None:
    """Delete the account: empty its name, its key and its password hash, and set its deletion time.

    The row keeps the identifier and the short identifier. The tokens, the sessions and the failed
    sign-ins counted for the name of the account are deleted. No other table changes. Return the
    account as it was before the deletion, or nothing when no account that is not deleted has the
    identifier; nothing is then written.
    """
    account = read_account(cursor, sql, account_id)
    if account is None or account.deleted_at is not None:
        return None
    deleted_at = _utc_iso(now)
    cursor.execute(sql(_UPDATE_ACCOUNT_DELETED), (deleted_at, deleted_at, account_id))
    if cursor.rowcount != 1:
        # Another transaction deleted the account after the read.
        return None
    delete_tokens(cursor, sql, account_id)
    delete_sessions(cursor, sql, account_id)
    if account.name_key is not None:
        cursor.execute(sql(_DELETE_SIGN_IN_ATTEMPTS), (account.name_key,))
    return account


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


def _count(cursor: _Cursor) -> int:
    """Return the number that a ``SELECT COUNT(*)`` statement just read."""
    row = cursor.fetchone()
    return 0 if row is None else int(row[0])


def apply_environment_account(
    cursor: _Cursor,
    sql: Sql,
    *,
    name: str,
    password_hash_for: Callable[[str | None], str | None],
    token_name: str,
    token_digest: str | None,
    now: datetime,
) -> Account:
    """Make the account that the environment defines match the environment, and return it.

    The caller owns the transaction and, on PostgreSQL, the lock that serializes two processes.

    - The account is created when no account has the name, as an active administrator that the
      environment manages. An existing account gets those three states back when one differs.
    - ``password_hash_for`` receives the stored hash, or nothing for a new account. It returns the
      hash to store, or nothing when the stored hash already fits the password. A new hash for an
      existing account ends the sessions of the account.
    - The account keeps one token that the environment manages, with ``token_digest``; it has none
      when ``token_digest`` is empty.
    - The record of failed sign-in attempts for the name is deleted.

    A statement that would change nothing is not sent, so a second call with the same environment
    leaves every row as it is.
    """
    account = read_account_by_name(cursor, sql, name)
    if account is None:
        account = create_account(
            cursor,
            sql,
            name=name,
            password_hash=password_hash_for(None),
            administrator=True,
            environment_managed=True,
            now=now,
        )
    else:
        if not account.administrator or account.disabled or not account.environment_managed:
            cursor.execute(
                sql(_UPDATE_ACCOUNT_ENVIRONMENT_FLAGS), (True, False, True, _utc_iso(now), account.account_id)
            )
        password_hash = password_hash_for(account.password_hash)
        if password_hash is not None:
            set_account_password_hash(cursor, sql, account.account_id, password_hash=password_hash, now=now)
            delete_sessions(cursor, sql, account.account_id)
    account_id = account.account_id

    cursor.execute(sql(_COUNT_ENVIRONMENT_TOKENS), (account_id, True))
    managed_tokens = _count(cursor)
    matching_tokens = 0
    if token_digest is not None:
        cursor.execute(sql(_COUNT_MATCHING_ENVIRONMENT_TOKENS), (account_id, True, token_name, token_digest))
        matching_tokens = _count(cursor)
    wanted_tokens = 0 if token_digest is None else 1
    if (managed_tokens, matching_tokens) != (wanted_tokens, wanted_tokens):
        cursor.execute(sql(_DELETE_ENVIRONMENT_TOKENS), (account_id, True))
        if token_digest is not None:
            create_token(
                cursor, sql, account_id, name=token_name, digest=token_digest, environment_managed=True, now=now
            )

    cursor.execute(sql(_DELETE_SIGN_IN_ATTEMPTS), (name_key(name),))
    applied = read_account(cursor, sql, account_id)
    if applied is None:  # pragma: no cover - the row was written or read in this transaction.
        msg = "the account that the environment defines is missing after it was applied"
        raise RuntimeError(msg)
    return applied


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


def _sign_in_attempt_from_row(row: Sequence[Any]) -> SignInAttempt:
    return SignInAttempt(name_key=row[0], failed_count=row[1], window_started_at=row[2], refused_until=row[3])


def read_sign_in_attempt(cursor: _Cursor, sql: Sql, name: str) -> SignInAttempt | None:
    """Return the failed sign-ins counted for this name, in any letter case, or nothing.

    The row can hold a refusal that has passed: the caller compares ``refused_until`` with its clock.
    """
    cursor.execute(sql(_SELECT_SIGN_IN_ATTEMPT), (name_key(name),))
    row = cursor.fetchone()
    return None if row is None else _sign_in_attempt_from_row(row)


def clear_sign_in_attempts(cursor: _Cursor, sql: Sql, name: str) -> bool:
    """Delete the failed sign-ins counted for this name. Return whether a row was deleted."""
    cursor.execute(sql(_DELETE_SIGN_IN_ATTEMPTS), (name_key(name),))
    return cursor.rowcount == 1


def record_sign_in_failure(
    cursor: _Cursor, sql: Sql, name: str, *, now: datetime, bound: int = SIGN_IN_ATTEMPTS_BOUND
) -> SignInAttempt:
    """Count one failed sign-in for this name, and return what the store then holds for it.

    The caller owns the transaction and the lock that makes two recordings run one after the
    other: the function reads the row and then writes it.

    - A name that is refused at ``now`` keeps its row as it is: the attempt is not counted and
      ``refused_until`` does not move.
    - A row whose refusal has passed, or whose window started more than
      ``SIGN_IN_FAILURE_WINDOW`` ago, starts a new window with a count of one.
    - Otherwise the count grows by one. When it reaches ``SIGN_IN_FAILURE_LIMIT``, the name is
      refused until ``now`` plus ``SIGN_IN_REFUSAL_PERIOD``.
    - A new row is added only after the rows with the oldest window are deleted to make room, so
      the table never holds more than ``bound`` rows.

    Raises:
        ValueError: the lower-case name is empty, longer than ``SIGN_IN_NAME_MAX_LENGTH`` or
            holds a NUL character, or ``bound`` is below one. Nothing is written.
    """
    key = name_key(name)
    if not key or len(key) > SIGN_IN_NAME_MAX_LENGTH or "\x00" in key:
        # The name is not quoted: it can be anything a caller typed in a sign-in request.
        # PostgreSQL stores no NUL character in a text.
        msg = f"a failed sign-in is counted for a name of 1 to {SIGN_IN_NAME_MAX_LENGTH} characters with no NUL"
        raise ValueError(msg)
    if bound < 1:
        msg = "the bound of the failed sign-in table must be at least one row"
        raise ValueError(msg)
    current = read_sign_in_attempt(cursor, sql, key)
    if current is not None and current.refused_until is not None and current.refused_until > now:
        return current
    window_expired = current is not None and (
        current.refused_until is not None or now - current.window_started_at > SIGN_IN_FAILURE_WINDOW
    )
    if current is None or window_expired:
        failed_count, window_started_at = 1, now
    else:
        failed_count, window_started_at = current.failed_count + 1, current.window_started_at
    refused_until = now + SIGN_IN_REFUSAL_PERIOD if failed_count >= SIGN_IN_FAILURE_LIMIT else None
    attempt = SignInAttempt(
        name_key=key, failed_count=failed_count, window_started_at=window_started_at, refused_until=refused_until
    )
    values = (failed_count, _utc_iso(window_started_at), _optional_utc_iso(refused_until))
    if current is None:
        cursor.execute(sql(_COUNT_SIGN_IN_ATTEMPTS))
        excess = _count(cursor) - bound + 1
        if excess > 0:
            cursor.execute(sql(_DELETE_OLDEST_SIGN_IN_ATTEMPTS), (excess,))
        cursor.execute(sql(_INSERT_SIGN_IN_ATTEMPT), (key, *values))
    else:
        cursor.execute(sql(_UPDATE_SIGN_IN_ATTEMPT), (*values, key))
    return attempt
