"""Local accounts, their API tokens and their sessions in the product store.

Each case runs on three profiles. ``local`` is the SQLite store. ``postgresql-emulated`` is the
PostgreSQL store class over a SQLite file: it proves the ``%s`` placeholders, not PostgreSQL's
behavior. ``postgresql`` is a real server, marked ``integration``; it needs
``PRODUCT_STORE_TEST_POSTGRESQL_DSN`` and runs each case in a generated schema that it drops.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from typing import TYPE_CHECKING, Any, cast

import pytest

from infrahub_sync.product_store import (
    Account,
    AccountIdentifierAllocationError,
    AccountNameTakenError,
    AccountTokenNameTakenError,
    DuplicateCredentialError,
    ProductProjection,
)
from infrahub_sync.product_store import accounts as account_statements
from infrahub_sync.product_store import store as product_store_store
from tests.product_store.test_contract import _PROFILES_WITH_POSTGRESQL, _projection_profile

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence
    from pathlib import Path

_T0 = datetime(2026, 11, 17, 9, 0, tzinfo=timezone.utc)
# A stored hash is opaque text to the store. This one has the shape of research R2 and no real key.
_HASH = "scrypt$32768$8$3$c2FsdA$a2V5"
_OTHER_HASH = "scrypt$32768$8$3$b3RoZXI$a2V5Mg"


@pytest.fixture(params=_PROFILES_WITH_POSTGRESQL)
def projection(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[ProductProjection]:
    yield from _projection_profile(request.param, tmp_path, "the account store's PostgreSQL parameter")


def _digest(label: str) -> str:
    """Return a digest of the shape the store holds. No token value exists behind it."""
    return sha256(label.encode()).hexdigest()


def _execute(projection: ProductProjection, statement: str, parameters: Sequence[Any] = ()) -> None:
    """Change rows directly, through the store's own connection and placeholders."""
    records = cast("Any", projection._records)
    with records._transaction() as cursor:
        cursor.execute(records._sql(statement), parameters)


def _stored(projection: ProductProjection, statement: str, parameters: Sequence[Any] = ()) -> Any:  # noqa: ANN401
    """Return the first column of the first row that a statement reads."""
    records = cast("Any", projection._records)
    with records._reading() as cursor:
        cursor.execute(records._sql(statement), parameters)
        row = cursor.fetchone()
    return None if row is None else row[0]


def _empty_name(projection: ProductProjection, account_id: str) -> None:
    """Empty the name, the key and the hash of one row, as the deletion of a later task does."""
    _execute(
        projection,
        "UPDATE accounts SET name = NULL, name_key = NULL, password_hash = NULL, deleted_at = ? WHERE account_id = ?",
        (_T0.isoformat(), account_id),
    )


def _account(projection: ProductProjection, name: str = "Alice", **fields: Any) -> Account:  # noqa: ANN401
    return projection.create_account(name=name, password_hash=fields.pop("password_hash", _HASH), now=_T0, **fields)


def test_a_created_account_has_new_identifiers_and_is_active(projection: ProductProjection) -> None:
    account = _account(projection)

    assert re.fullmatch(r"acct_[0-9a-f]{32}", account.account_id)
    assert re.fullmatch(r"[0-9a-f]{8}", account.short_id)
    assert (account.name, account.name_key) == ("Alice", "alice")
    assert (account.administrator, account.disabled, account.environment_managed) == (False, False, False)
    assert account.created_at == account.updated_at == account.password_changed_at == _T0
    assert account.deleted_at is None
    assert projection.lookup_account(account.account_id) == account


def test_an_account_is_created_with_its_level_and_its_environment_flag(projection: ProductProjection) -> None:
    account = _account(projection, "admin", administrator=True, environment_managed=True)

    stored = projection.lookup_account(account.account_id)
    assert stored is not None
    assert (stored.administrator, stored.environment_managed) == (True, True)


@pytest.mark.parametrize("typed", ["Alice", "alice", "ALICE"])
def test_an_account_is_read_by_its_name_in_any_letter_case(projection: ProductProjection, typed: str) -> None:
    account = _account(projection)

    assert projection.lookup_account_by_name(typed) == account


def test_an_unknown_identifier_or_name_reads_nothing(projection: ProductProjection) -> None:
    _account(projection)

    assert projection.lookup_account("acct_" + "0" * 32) is None
    assert projection.lookup_account_by_name("bob") is None


def test_the_password_hash_is_stored_and_left_out_of_every_printed_form(projection: ProductProjection) -> None:
    account = _account(projection)

    stored = projection.lookup_account(account.account_id)
    assert stored is not None
    assert stored.password_hash == _HASH
    assert _HASH not in repr(stored)
    assert _HASH not in stored.model_dump_json()
    assert "password_hash" not in stored.model_dump()


@pytest.mark.parametrize("second", ["Alice", "alice", "ALICE"])
def test_a_name_that_is_taken_in_any_letter_case_is_refused(projection: ProductProjection, second: str) -> None:
    first = _account(projection)

    with pytest.raises(AccountNameTakenError) as refusal:
        _account(projection, second)

    assert refusal.value.__cause__ is None
    assert refusal.value.__context__ is None
    assert projection.list_accounts() == (first,)


def test_the_unique_index_refuses_a_second_row_with_the_same_key(projection: ProductProjection) -> None:
    first = _account(projection)
    row = ("acct_" + "1" * 32, "1" * 8, "ALICE", first.name_key, False, False, False, *(_T0.isoformat(),) * 3)

    with pytest.raises(Exception) as refusal:
        _execute(
            projection,
            "INSERT INTO accounts (account_id, short_id, name, name_key, administrator, disabled, "
            "environment_managed, created_at, updated_at, password_changed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            row,
        )

    assert product_store_store._is_unique_violation(refusal.value)


def test_a_name_is_free_again_after_its_key_is_emptied(projection: ProductProjection) -> None:
    first = _account(projection)
    _empty_name(projection, first.account_id)

    second = _account(projection)

    assert second.account_id != first.account_id
    assert second.short_id != first.short_id
    assert projection.lookup_account_by_name("alice") == second


def test_several_accounts_have_an_empty_key_at_the_same_time(projection: ProductProjection) -> None:
    emptied = []
    for _ in range(2):
        account = _account(projection)
        _empty_name(projection, account.account_id)
        emptied.append(account)
    third = _account(projection)

    stored = projection.lookup_accounts([account.account_id for account in emptied])
    assert len(stored) == 2
    assert {(account.name, account.name_key, account.password_hash) for account in stored.values()} == {
        (None, None, None)
    }
    assert all(account.deleted_at == _T0 for account in stored.values())
    assert projection.list_accounts() == (third,)


def test_an_identifier_that_is_in_use_is_chosen_again(
    projection: ProductProjection, monkeypatch: pytest.MonkeyPatch
) -> None:
    values = iter(["a" * 32, "a" * 8, "a" * 32, "a" * 8, "b" * 32, "b" * 8])
    monkeypatch.setattr(account_statements.secrets, "token_hex", lambda _size: next(values))
    first = _account(projection, "alice")

    second = _account(projection, "bob")

    assert first.account_id == "acct_" + "a" * 32
    assert (second.account_id, second.short_id) == ("acct_" + "b" * 32, "b" * 8)


def test_a_short_identifier_that_is_in_use_is_chosen_again(
    projection: ProductProjection, monkeypatch: pytest.MonkeyPatch
) -> None:
    values = iter(["a" * 32, "a" * 8, "b" * 32, "a" * 8, "c" * 32, "c" * 8])
    monkeypatch.setattr(account_statements.secrets, "token_hex", lambda _size: next(values))
    _account(projection, "alice")

    second = _account(projection, "bob")

    assert (second.account_id, second.short_id) == ("acct_" + "c" * 32, "c" * 8)


def test_creation_stops_when_every_chosen_identifier_is_in_use(
    projection: ProductProjection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(account_statements.secrets, "token_hex", lambda size: "a" * (size * 2))
    _account(projection, "alice")

    with pytest.raises(AccountIdentifierAllocationError) as refusal:
        _account(projection, "bob")

    assert refusal.value.__cause__ is None
    assert refusal.value.__context__ is None
    assert projection.lookup_account_by_name("bob") is None


def test_accounts_are_read_by_a_list_of_identifiers(projection: ProductProjection) -> None:
    alice = _account(projection, "alice")
    bob = _account(projection, "bob")
    _account(projection, "carol")
    unknown = "acct_" + "0" * 32

    found = projection.lookup_accounts([bob.account_id, alice.account_id, unknown, bob.account_id, "operator"])

    assert found == {alice.account_id: alice, bob.account_id: bob}


def test_an_empty_list_of_identifiers_reads_nothing(projection: ProductProjection) -> None:
    _account(projection)

    assert projection.lookup_accounts([]) == {}


def test_accounts_are_listed_by_state_and_by_name(projection: ProductProjection) -> None:
    carol = _account(projection, "Carol")
    alice = _account(projection, "alice")
    bob = _account(projection, "Bob")
    assert projection.disable_account(bob.account_id, now=_T0)
    disabled_bob = projection.lookup_account(bob.account_id)

    assert projection.list_accounts() == (alice, disabled_bob, carol)
    assert projection.list_accounts(disabled=False) == (alice, carol)
    assert projection.list_accounts(disabled=True) == (disabled_bob,)


def test_the_level_of_an_account_is_set_and_removed(projection: ProductProjection) -> None:
    account = _account(projection)
    later = _T0 + timedelta(hours=1)

    assert projection.set_account_level(account.account_id, administrator=True, now=later)
    promoted = projection.lookup_account(account.account_id)
    assert promoted == account.model_copy(update={"administrator": True, "updated_at": later})

    assert projection.set_account_level(account.account_id, administrator=False, now=later)
    demoted = projection.lookup_account(account.account_id)
    assert demoted is not None
    assert demoted.administrator is False


def test_a_change_to_an_unknown_account_changes_nothing(projection: ProductProjection) -> None:
    account = _account(projection)
    unknown = "acct_" + "0" * 32

    assert not projection.set_account_level(unknown, administrator=True, now=_T0)
    assert not projection.set_account_password_hash(unknown, password_hash=_OTHER_HASH, now=_T0)
    assert not projection.disable_account(unknown, now=_T0)
    assert not projection.enable_account(unknown, now=_T0)
    assert projection.list_accounts() == (account,)


def test_a_new_password_hash_replaces_the_old_one_and_records_the_time(projection: ProductProjection) -> None:
    account = _account(projection)
    later = _T0 + timedelta(days=2)

    assert projection.set_account_password_hash(account.account_id, password_hash=_OTHER_HASH, now=later)

    stored = projection.lookup_account(account.account_id)
    assert stored is not None
    assert stored.password_hash == _OTHER_HASH
    assert (stored.password_changed_at, stored.updated_at, stored.created_at) == (later, later, _T0)


def test_disabling_an_account_deletes_its_tokens_and_sessions_only(projection: ProductProjection) -> None:
    alice = _account(projection, "alice")
    bob = _account(projection, "bob")
    for account in (alice, bob):
        projection.create_account_token(account.account_id, name="ci", digest=_digest(f"t-{account.name}"), now=_T0)
        projection.create_account_session(account.account_id, digest=_digest(f"s-{account.name}"), now=_T0)
    later = _T0 + timedelta(hours=1)

    assert projection.disable_account(alice.account_id, now=later)

    stored = projection.lookup_account(alice.account_id)
    assert stored == alice.model_copy(update={"disabled": True, "updated_at": later})
    assert projection.list_account_tokens(alice.account_id) == ()
    assert projection.list_account_sessions(alice.account_id) == ()
    assert len(projection.list_account_tokens(bob.account_id)) == 1
    assert len(projection.list_account_sessions(bob.account_id)) == 1


def test_an_enabled_account_keeps_its_name_and_has_no_token_or_session(projection: ProductProjection) -> None:
    account = _account(projection)
    projection.create_account_token(account.account_id, name="ci", digest=_digest("t"), now=_T0)
    projection.create_account_session(account.account_id, digest=_digest("s"), now=_T0)
    projection.disable_account(account.account_id, now=_T0)
    later = _T0 + timedelta(hours=1)

    assert projection.enable_account(account.account_id, now=later)

    assert projection.lookup_account_by_name("alice") == account.model_copy(update={"updated_at": later})
    assert projection.list_account_tokens(account.account_id) == ()
    assert projection.list_account_sessions(account.account_id) == ()
    with pytest.raises(AccountNameTakenError):
        _account(projection)


def test_a_token_row_is_created_and_listed_without_its_digest(projection: ProductProjection) -> None:
    account = _account(projection)
    expires_at = _T0 + timedelta(days=30)
    digest = _digest("first")

    first = projection.create_account_token(
        account.account_id, name="ci", digest=digest, expires_at=expires_at, now=_T0
    )
    second = projection.create_account_token(
        account.account_id, name="admin", digest=_digest("second"), environment_managed=True, now=_T0 + timedelta(1)
    )

    assert (first.account_id, first.name, first.created_at, first.expires_at) == (
        account.account_id,
        "ci",
        _T0,
        expires_at,
    )
    assert (first.last_used_at, first.environment_managed) == (None, False)
    assert (second.expires_at, second.environment_managed) == (None, True)
    assert projection.list_account_tokens(account.account_id) == (first, second)
    assert "digest" not in type(first).model_fields
    assert digest not in repr(first)


def test_a_token_name_is_unique_for_one_account_only(projection: ProductProjection) -> None:
    alice = _account(projection, "alice")
    bob = _account(projection, "bob")
    first = projection.create_account_token(alice.account_id, name="ci", digest=_digest("alice-1"), now=_T0)

    with pytest.raises(AccountTokenNameTakenError) as refusal:
        projection.create_account_token(alice.account_id, name="ci", digest=_digest("alice-2"), now=_T0)
    other = projection.create_account_token(bob.account_id, name="ci", digest=_digest("bob-1"), now=_T0)

    assert refusal.value.__cause__ is None
    assert refusal.value.__context__ is None
    assert projection.list_account_tokens(alice.account_id) == (first,)
    assert projection.list_account_tokens(bob.account_id) == (other,)


def test_a_digest_that_the_store_holds_is_refused_without_the_driver_error(projection: ProductProjection) -> None:
    account = _account(projection)
    digest = _digest("shared")
    projection.create_account_token(account.account_id, name="ci", digest=digest, now=_T0)
    projection.create_account_session(account.account_id, digest=_digest("session"), now=_T0)

    with pytest.raises(DuplicateCredentialError) as token_refusal:
        projection.create_account_token(account.account_id, name="other", digest=digest, now=_T0)
    with pytest.raises(DuplicateCredentialError) as session_refusal:
        projection.create_account_session(account.account_id, digest=_digest("session"), now=_T0)

    for refusal in (token_refusal, session_refusal):
        assert refusal.value.__cause__ is None
        assert refusal.value.__context__ is None
    assert digest not in str(token_refusal.value)
    assert _digest("session") not in str(session_refusal.value)
    assert len(projection.list_account_tokens(account.account_id)) == 1
    assert len(projection.list_account_sessions(account.account_id)) == 1


def test_one_token_is_deleted_by_its_owner_only(projection: ProductProjection) -> None:
    alice = _account(projection, "alice")
    bob = _account(projection, "bob")
    kept = projection.create_account_token(alice.account_id, name="kept", digest=_digest("kept"), now=_T0)
    removed = projection.create_account_token(alice.account_id, name="removed", digest=_digest("removed"), now=_T0)

    assert not projection.delete_account_token(bob.account_id, removed.token_id)
    assert projection.delete_account_token(alice.account_id, removed.token_id)
    assert not projection.delete_account_token(alice.account_id, removed.token_id)

    assert projection.list_account_tokens(alice.account_id) == (kept,)


def test_every_token_of_one_account_is_deleted(projection: ProductProjection) -> None:
    alice = _account(projection, "alice")
    bob = _account(projection, "bob")
    for name in ("one", "two"):
        projection.create_account_token(alice.account_id, name=name, digest=_digest(f"alice-{name}"), now=_T0)
    other = projection.create_account_token(bob.account_id, name="one", digest=_digest("bob-one"), now=_T0)

    assert projection.delete_account_tokens(alice.account_id) == 2

    assert projection.list_account_tokens(alice.account_id) == ()
    assert projection.list_account_tokens(bob.account_id) == (other,)


def test_a_session_row_is_created_listed_and_deleted(projection: ProductProjection) -> None:
    account = _account(projection)
    first = projection.create_account_session(account.account_id, digest=_digest("one"), now=_T0)
    second = projection.create_account_session(account.account_id, digest=_digest("two"), now=_T0 + timedelta(1))

    assert (first.account_id, first.created_at, first.last_used_at) == (account.account_id, _T0, _T0)
    assert "digest" not in type(first).model_fields
    assert projection.list_account_sessions(account.account_id) == (first, second)

    assert projection.delete_account_session(first.session_id)
    assert not projection.delete_account_session(first.session_id)
    assert projection.list_account_sessions(account.account_id) == (second,)


def test_every_session_of_one_account_is_deleted(projection: ProductProjection) -> None:
    alice = _account(projection, "alice")
    bob = _account(projection, "bob")
    for label in ("one", "two"):
        projection.create_account_session(alice.account_id, digest=_digest(f"alice-{label}"), now=_T0)
    other = projection.create_account_session(bob.account_id, digest=_digest("bob-one"), now=_T0)

    assert projection.delete_account_sessions(alice.account_id) == 2

    assert projection.list_account_sessions(alice.account_id) == ()
    assert projection.list_account_sessions(bob.account_id) == (other,)


def test_a_token_digest_resolves_to_its_token_and_its_account(projection: ProductProjection) -> None:
    account = _account(projection, administrator=True)
    expires_at = _T0 + timedelta(days=30)
    token = projection.create_account_token(
        account.account_id, name="ci", digest=_digest("token"), expires_at=expires_at, now=_T0
    )

    resolved = projection.resolve_digest(_digest("token"), now=_T0 + timedelta(hours=1))

    assert resolved is not None
    assert resolved.account == account
    assert (resolved.kind, resolved.credential_id) == ("token", token.token_id)
    assert (resolved.expires_at, resolved.last_used_at) == (expires_at, None)


def test_a_session_digest_resolves_to_its_session_and_its_account(projection: ProductProjection) -> None:
    account = _account(projection)
    session = projection.create_account_session(account.account_id, digest=_digest("session"), now=_T0)

    resolved = projection.resolve_digest(_digest("session"), now=_T0 + timedelta(seconds=5))

    assert resolved is not None
    assert resolved.account == account
    assert (resolved.kind, resolved.credential_id) == ("session", session.session_id)
    assert (resolved.expires_at, resolved.last_used_at) == (None, _T0)


def test_an_expired_token_resolves_with_its_expiry_date_for_the_caller_to_refuse(
    projection: ProductProjection,
) -> None:
    account = _account(projection)
    expires_at = _T0 + timedelta(days=1)
    projection.create_account_token(
        account.account_id, name="ci", digest=_digest("token"), expires_at=expires_at, now=_T0
    )
    now = _T0 + timedelta(days=2)

    resolved = projection.resolve_digest(_digest("token"), now=now)

    assert resolved is not None
    assert resolved.expires_at == expires_at
    assert expires_at < now
    assert resolved.account.disabled is False


def test_the_tokens_and_sessions_of_a_disabled_account_no_longer_resolve(projection: ProductProjection) -> None:
    account = _account(projection)
    projection.create_account_token(account.account_id, name="ci", digest=_digest("token"), now=_T0)
    projection.create_account_session(account.account_id, digest=_digest("session"), now=_T0)

    projection.disable_account(account.account_id, now=_T0)

    assert projection.resolve_digest(_digest("token"), now=_T0) is None
    assert projection.resolve_digest(_digest("session"), now=_T0) is None


def test_a_row_of_a_disabled_account_resolves_with_the_state_for_the_caller_to_refuse(
    projection: ProductProjection,
) -> None:
    account = _account(projection)
    projection.disable_account(account.account_id, now=_T0)
    projection.create_account_session(account.account_id, digest=_digest("late"), now=_T0)

    resolved = projection.resolve_digest(_digest("late"), now=_T0)

    assert resolved is not None
    assert resolved.account.disabled is True


def test_an_unknown_digest_resolves_to_nothing(projection: ProductProjection) -> None:
    account = _account(projection)
    projection.create_account_token(account.account_id, name="ci", digest=_digest("token"), now=_T0)

    assert projection.resolve_digest(_digest("unknown"), now=_T0) is None


@pytest.mark.parametrize(
    ("kind", "table", "key"),
    [("token", "account_tokens", "token_id"), ("session", "account_sessions", "session_id")],
)
@pytest.mark.parametrize(
    ("elapsed", "written"),
    [
        pytest.param(timedelta(seconds=30), False, id="half-a-minute"),
        pytest.param(timedelta(seconds=60), False, id="one-minute-exactly"),
        pytest.param(timedelta(seconds=61), True, id="more-than-a-minute"),
    ],
)
def test_a_use_is_recorded_only_when_the_last_one_is_more_than_a_minute_old(  # noqa: PLR0913
    projection: ProductProjection, *, kind: str, table: str, key: str, elapsed: timedelta, written: bool
) -> None:
    account = _account(projection)
    digest = _digest(kind)
    if kind == "token":
        row_id = projection.create_account_token(account.account_id, name="ci", digest=digest, now=_T0).token_id
        # The first use of a token finds an empty time and records it.
        first = projection.resolve_digest(digest, now=_T0)
        assert first is not None
        assert first.last_used_at is None
    else:
        row_id = projection.create_account_session(account.account_id, digest=digest, now=_T0).session_id
    now = _T0 + elapsed

    resolved = projection.resolve_digest(digest, now=now)

    assert resolved is not None
    assert resolved.last_used_at == _T0
    stored = _stored(projection, f"SELECT last_used_at FROM {table} WHERE {key} = ?", (row_id,))  # noqa: S608
    assert datetime.fromisoformat(stored) == (now if written else _T0)
    following = projection.resolve_digest(digest, now=now)
    assert following is not None
    assert following.last_used_at == (now if written else _T0)
