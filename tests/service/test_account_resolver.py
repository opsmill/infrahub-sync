"""The resolver that reads the caller of a request from the accounts of the product store.

The store cases run on the profiles of ``tests/product_store/test_accounts.py``: SQLite, the
PostgreSQL store class over SQLite, and a real PostgreSQL server (``integration``).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, cast

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("prefect")

from fastapi.testclient import TestClient

from infrahub_sync.product_store import Account, ProductProjection, local_product_projection
from infrahub_sync.service.app import create_app
from infrahub_sync.service.auth import Principal, Resolution, StoreAccountResolver
from infrahub_sync.service.credentials import digest, new_api_token, new_session_token
from infrahub_sync.service.service import RunService
from tests.product_store.test_contract import _PROFILES_WITH_POSTGRESQL, _projection_profile

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

_T0 = datetime(2026, 11, 17, 9, 0, tzinfo=timezone.utc)
_IDLE_SECONDS = 600
_ENVIRONMENT_TOKEN = "environment-token-0001"  # noqa: S105 - a test value, not a secret.
# The store reads no password, so any text stands for a hash here.
_HASH = "scrypt$32768$8$3$c2FsdA$a2V5"


class _Clock:
    """A clock that the test moves."""

    def __init__(self) -> None:
        self.now = _T0

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **period: float) -> None:
        self.now += timedelta(**period)


@pytest.fixture(params=_PROFILES_WITH_POSTGRESQL)
def projection(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[ProductProjection]:
    yield from _projection_profile(request.param, tmp_path, "the account resolver's PostgreSQL parameter")


@pytest.fixture
def clock() -> _Clock:
    return _Clock()


@pytest.fixture
def resolver(projection: ProductProjection, clock: _Clock) -> StoreAccountResolver:
    return StoreAccountResolver(projection, idle_seconds=_IDLE_SECONDS, clock=clock)


def _account(projection: ProductProjection, name: str = "Alice", *, administrator: bool = False) -> Account:
    return projection.create_account(name=name, password_hash=_HASH, administrator=administrator, now=_T0)


def _token(projection: ProductProjection, account: Account, *, expires_at: datetime | None = None) -> str:
    value = new_api_token()
    projection.create_account_token(account.account_id, name="ci", digest=digest(value), expires_at=expires_at, now=_T0)
    return value


def _session(projection: ProductProjection, account: Account) -> tuple[str, str]:
    """Create a session; return its token value and its identifier."""
    value = new_session_token()
    session = projection.create_account_session(account.account_id, digest=digest(value), now=_T0)
    return value, session.session_id


def _mark_deleted(projection: ProductProjection, account: Account) -> None:
    """Set the deletion time of the row. The store has no deletion function yet."""
    records = cast("Any", projection._records)
    with records._transaction() as cursor:
        cursor.execute(
            records._sql("UPDATE accounts SET deleted_at = ? WHERE account_id = ?"),
            (_T0.isoformat(), account.account_id),
        )


def test_an_api_token_resolves_to_the_account_identifier_and_name(
    projection: ProductProjection, resolver: StoreAccountResolver
) -> None:
    account = _account(projection, administrator=True)
    value = _token(projection, account)

    resolution = resolver.resolve(value)

    assert resolution == Resolution.accepted(
        Principal(actor=account.account_id, administrator=True, name="Alice", kind="token")
    )
    assert resolution.principal is not None
    assert resolution.principal.actor.startswith("acct_")
    assert resolution.principal.session_id is None


def test_a_session_token_resolves_to_the_account_and_names_its_session(
    projection: ProductProjection, resolver: StoreAccountResolver
) -> None:
    account = _account(projection)
    value, session_id = _session(projection, account)

    resolution = resolver.resolve(value)

    assert resolution == Resolution.accepted(
        Principal(actor=account.account_id, administrator=False, name="Alice", kind="session", session_id=session_id)
    )


def test_a_token_with_a_later_expiry_date_resolves(
    projection: ProductProjection, resolver: StoreAccountResolver
) -> None:
    value = _token(projection, _account(projection), expires_at=_T0 + timedelta(seconds=1))

    assert resolver.resolve(value).principal is not None


@pytest.mark.parametrize("value", ["", "unknown-value", "ifs_pat_unknown", "ifs_ses_unknown"])
def test_an_unknown_value_is_refused_as_unauthenticated(
    projection: ProductProjection, resolver: StoreAccountResolver, value: str
) -> None:
    _token(projection, _account(projection))

    assert resolver.resolve(value) == Resolution.refused("unauthenticated")


def test_a_deleted_account_is_refused_as_unauthenticated(
    projection: ProductProjection, resolver: StoreAccountResolver
) -> None:
    account = _account(projection)
    token = _token(projection, account)
    session, _session_id = _session(projection, account)
    _mark_deleted(projection, account)

    assert resolver.resolve(token) == Resolution.refused("unauthenticated")
    assert resolver.resolve(session) == Resolution.refused("unauthenticated")


def test_a_disabled_account_whose_rows_remain_is_refused_as_unauthenticated(
    projection: ProductProjection, resolver: StoreAccountResolver
) -> None:
    """The store applies no policy: a row of a disabled account resolves, and the resolver refuses it."""
    account = _account(projection)
    token = _token(projection, account, expires_at=_T0 - timedelta(days=1))
    session, _session_id = _session(projection, account)
    # The flag is set without the disable, which would delete the rows: they stand for rows that
    # a disable did not delete. The store refuses to create a row for a disabled account.
    records = cast("Any", projection._records)
    with records._transaction() as cursor:
        cursor.execute(
            records._sql("UPDATE accounts SET disabled = ? WHERE account_id = ?"), (True, account.account_id)
        )

    # The token is also expired: a disabled account is not told so.
    assert resolver.resolve(token) == Resolution.refused("unauthenticated")
    assert resolver.resolve(session) == Resolution.refused("unauthenticated")


@pytest.mark.parametrize("age", [timedelta(0), timedelta(days=1)], ids=("at-the-expiry-date", "a-day-after"))
def test_an_expired_token_is_refused_as_token_expired(
    projection: ProductProjection, resolver: StoreAccountResolver, clock: _Clock, age: timedelta
) -> None:
    value = _token(projection, _account(projection), expires_at=_T0 + timedelta(hours=1))
    clock.now = _T0 + timedelta(hours=1) + age

    assert resolver.resolve(value) == Resolution.refused("token-expired")


def test_a_session_used_within_the_idle_period_stays_valid(
    projection: ProductProjection, resolver: StoreAccountResolver, clock: _Clock
) -> None:
    value, _session_id = _session(projection, _account(projection))

    for _use in range(3):
        clock.advance(seconds=_IDLE_SECONDS - 1)
        assert resolver.resolve(value).principal is not None


def test_an_idle_session_is_refused_and_deleted(
    projection: ProductProjection, resolver: StoreAccountResolver, clock: _Clock
) -> None:
    account = _account(projection)
    value, _session_id = _session(projection, account)
    clock.advance(seconds=_IDLE_SECONDS + 1)

    assert resolver.resolve(value) == Resolution.refused("unauthenticated")
    assert projection.list_account_sessions(account.account_id) == ()
    # The refused request recorded no use that would make the session valid again.
    assert resolver.resolve(value) == Resolution.refused("unauthenticated")


def test_an_idle_period_does_not_end_an_api_token(
    projection: ProductProjection, resolver: StoreAccountResolver, clock: _Clock
) -> None:
    value = _token(projection, _account(projection))
    clock.advance(seconds=_IDLE_SECONDS * 100)

    assert resolver.resolve(value).principal is not None


def test_the_resolver_lists_the_token_of_the_environment_as_its_only_secret(
    projection: ProductProjection,
) -> None:
    account = _account(projection)
    stored_token = _token(projection, account)

    with_token = StoreAccountResolver(projection, idle_seconds=_IDLE_SECONDS, admin_token=_ENVIRONMENT_TOKEN)
    without_token = StoreAccountResolver(projection, idle_seconds=_IDLE_SECONDS)

    assert with_token.secret_values == (_ENVIRONMENT_TOKEN,)
    assert without_token.secret_values == ()
    assert stored_token not in with_token.secret_values


def test_a_resolution_is_a_principal_or_a_refusal_and_never_both() -> None:
    principal = Principal(actor="acct_1")

    assert Resolution.refused() == Resolution(refusal="unauthenticated")
    with pytest.raises(ValueError, match="only one"):
        Resolution()
    with pytest.raises(ValueError, match="only one"):
        Resolution(principal=principal, refusal="token-expired")


def test_a_principal_built_without_the_new_fields_keeps_working() -> None:
    principal = Principal(actor="alice", administrator=True)

    assert (principal.name, principal.kind, principal.session_id) == (None, None, None)


# The HTTP cases below need one store only: the resolver's store cases above cover the dialects.


@pytest.fixture
def api(tmp_path: Path, clock: _Clock) -> tuple[TestClient, ProductProjection, RunService]:
    store = local_product_projection(tmp_path)
    service = RunService(store, cast("Any", object()))
    resolver = StoreAccountResolver(store, idle_seconds=_IDLE_SECONDS, clock=clock)
    return TestClient(create_app(service, resolver)), store, service


def _bearer(value: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {value}"}


def _refusals(store: ProductProjection) -> list[tuple[str, str | None, str]]:
    return [(event.actor, event.reason, event.outcome) for event in store.audit_events()]


def test_me_returns_the_account_name_and_never_the_identifier(
    api: tuple[TestClient, ProductProjection, RunService],
) -> None:
    client, store, _service = api
    account = _account(store, administrator=True)

    for value in (_token(store, account), _session(store, account)[0]):
        response = client.get("/me", headers=_bearer(value))

        assert response.status_code == 200
        assert response.json()["actor"] == "Alice"
        assert response.json()["administrator"] is True
        assert account.account_id not in response.text


def test_a_request_after_a_disable_is_refused_with_no_restart(
    api: tuple[TestClient, ProductProjection, RunService],
) -> None:
    client, store, _service = api
    account = _account(store)
    token = _token(store, account)
    session, _session_id = _session(store, account)
    assert client.get("/me", headers=_bearer(token)).status_code == 200
    assert client.get("/me", headers=_bearer(session)).status_code == 200

    store.disable_account(account.account_id)

    for value in (token, session):
        refused = client.get("/me", headers=_bearer(value))
        assert refused.status_code == 401
        assert refused.json()["error"]["code"] == "unauthenticated"


def test_an_expired_token_answers_401_token_expired_and_records_the_refusal(
    api: tuple[TestClient, ProductProjection, RunService], clock: _Clock
) -> None:
    client, store, _service = api
    value = _token(store, _account(store), expires_at=_T0 + timedelta(hours=1))
    clock.advance(hours=2)

    refused = client.get("/me", headers=_bearer(value))

    assert refused.status_code == 401
    assert refused.json()["error"]["code"] == "token-expired"
    assert value not in refused.text
    assert _refusals(store) == [("unauthenticated", "expired-bearer-token", "refused-authentication")]


def test_an_unknown_value_answers_401_unauthenticated_and_records_the_refusal(
    api: tuple[TestClient, ProductProjection, RunService],
) -> None:
    client, store, _service = api

    refused = client.get("/me", headers=_bearer("ifs_pat_unknown"))

    assert refused.status_code == 401
    assert refused.json()["error"]["code"] == "unauthenticated"
    assert _refusals(store) == [("unauthenticated", "invalid-bearer-token", "refused-authentication")]


def test_an_idle_session_answers_401_unauthenticated(
    api: tuple[TestClient, ProductProjection, RunService], clock: _Clock
) -> None:
    client, store, _service = api
    value, _session_id = _session(store, _account(store))
    clock.advance(seconds=_IDLE_SECONDS + 1)

    refused = client.get("/me", headers=_bearer(value))

    assert refused.status_code == 401
    assert refused.json()["error"]["code"] == "unauthenticated"
