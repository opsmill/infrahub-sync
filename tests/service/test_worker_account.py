"""The ``worker`` account that ``INFRAHUB_SYNC_WORKER_TOKEN`` defines, applied when the service starts.

The store cases run on the profiles of ``tests/product_store/test_accounts.py``: SQLite, the
PostgreSQL store class over SQLite, and a real PostgreSQL server (``integration``). Token values are
compared through their digests; no assertion message holds a token.
"""

from __future__ import annotations

import os
import threading
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, cast

import pytest
from structlog.testing import capture_logs

pytest.importorskip("fastapi")
pytest.importorskip("prefect")

from fastapi.testclient import TestClient

from infrahub_sync.product_store import (
    EnvironmentAccountConflictError,
    ProductProjection,
    local_product_projection,
)
from infrahub_sync.service import credentials, serve
from infrahub_sync.service.app import create_app
from infrahub_sync.service.auth import (
    WORKER_ACCOUNT_NAME,
    StoreAccountResolver,
    apply_worker_account,
    is_worker_account,
)
from infrahub_sync.service.credentials import digest, new_api_token
from infrahub_sync.service.service import RunService
from infrahub_sync.service.settings import (
    ADMIN_ACCOUNT_PASSWORD_ENV,
    ADMIN_ACCOUNT_TOKEN_ENV,
    ENCRYPTION_KEY_ENV,
    WORKER_TOKEN_ENV,
    CredentialSettingsError,
    worker_account_token,
)
from tests.product_store.test_contract import _PROFILES_WITH_POSTGRESQL, _projection_profile

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Sequence
    from pathlib import Path

    from httpx import Response

_TOKEN = "worker-token-of-the-environment-1"  # noqa: S105 - a test value, not a secret.
_OTHER_TOKEN = "worker-token-of-the-environment-2"  # noqa: S105 - a test value, not a secret.
_ADMIN_PASSWORD = "first-admin-password"  # noqa: S105 - a test value, not a secret.
_ADMIN_TOKEN = "admin-token-of-the-environment"  # noqa: S105 - a test value, not a secret.
_WORKER_PASSWORD = "a-password-written-by-hand"  # noqa: S105 - a test value, not a secret.
# The store reads no password, so any text stands for a hash.
_STORED_TEXT = "scrypt$16$1$1$c2FsdA$a2V5"
_T0 = datetime(2026, 11, 20, 9, 0, tzinfo=timezone.utc)
_SNAPSHOT_STATEMENTS = (
    "SELECT account_id, short_id, name, name_key, password_hash, administrator, disabled, environment_managed, "
    "created_at, updated_at, password_changed_at, deleted_at FROM accounts ORDER BY account_id",
    "SELECT token_id, account_id, name, digest, created_at, expires_at, last_used_at, environment_managed "
    "FROM account_tokens ORDER BY token_id",
)


@pytest.fixture(params=_PROFILES_WITH_POSTGRESQL)
def projection(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[ProductProjection]:
    yield from _projection_profile(request.param, tmp_path, "the worker account's PostgreSQL parameter")


@pytest.fixture(autouse=True)
def _environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (ADMIN_ACCOUNT_PASSWORD_ENV, ADMIN_ACCOUNT_TOKEN_ENV, WORKER_TOKEN_ENV, ENCRYPTION_KEY_ENV):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("INFRAHUB_SYNC_REQUIRE_APPROVAL", raising=False)


def _rows(projection: ProductProjection, statement: str, parameters: Sequence[Any] = ()) -> list[tuple[Any, ...]]:
    records = cast("Any", projection._records)
    with records._reading() as cursor:
        cursor.execute(records._sql(statement), parameters)
        return [tuple(row) for row in cursor.fetchall()]


def _execute(projection: ProductProjection, statement: str, parameters: Sequence[Any] = ()) -> None:
    records = cast("Any", projection._records)
    with records._transaction() as cursor:
        cursor.execute(records._sql(statement), parameters)


def _snapshot(projection: ProductProjection) -> list[list[tuple[Any, ...]]]:
    return [_rows(projection, statement) for statement in _SNAPSHOT_STATEMENTS]


def _token_digests(projection: ProductProjection, account_id: str) -> list[str]:
    rows = _rows(projection, "SELECT digest FROM account_tokens WHERE account_id = ? ORDER BY digest", (account_id,))
    return [row[0] for row in rows]


# The store.


def test_the_first_start_creates_the_worker_account_with_its_token(projection: ProductProjection) -> None:
    applied = apply_worker_account(projection, _TOKEN)

    assert applied.name == WORKER_ACCOUNT_NAME
    assert applied.environment_managed is True
    assert applied.administrator is False
    assert applied.disabled is False
    assert applied.password_hash is None
    assert is_worker_account(applied)
    assert _token_digests(projection, applied.account_id) == [digest(_TOKEN)]
    tokens = projection.list_account_tokens(applied.account_id)
    assert [(token.name, token.environment_managed) for token in tokens] == [("environment", True)]


def test_a_start_with_the_same_token_changes_no_row(projection: ProductProjection) -> None:
    apply_worker_account(projection, _TOKEN)
    before = _snapshot(projection)

    apply_worker_account(projection, _TOKEN)

    assert _snapshot(projection) == before


def test_a_start_with_a_new_token_replaces_the_previous_one(projection: ProductProjection) -> None:
    first = apply_worker_account(projection, _TOKEN)

    second = apply_worker_account(projection, _OTHER_TOKEN)

    assert second.account_id == first.account_id
    assert _token_digests(projection, first.account_id) == [digest(_OTHER_TOKEN)]
    assert projection.resolve_digest(digest(_TOKEN)) is None


def test_a_worker_account_that_was_changed_by_hand_gets_its_states_back(projection: ProductProjection) -> None:
    account = apply_worker_account(projection, _TOKEN)
    _execute(
        projection,
        "UPDATE accounts SET administrator = ?, disabled = ? WHERE account_id = ?",
        (True, True, account.account_id),
    )

    restored = apply_worker_account(projection, _TOKEN)

    assert (restored.administrator, restored.disabled, restored.environment_managed) == (False, False, True)


def test_an_account_named_worker_that_the_environment_does_not_manage_stops_the_apply(
    projection: ProductProjection,
) -> None:
    projection.create_account(name="worker", password_hash=_STORED_TEXT, now=_T0)
    before = _snapshot(projection)

    with pytest.raises(EnvironmentAccountConflictError) as refused:
        apply_worker_account(projection, _TOKEN)

    assert "worker" in str(refused.value)
    assert _snapshot(projection) == before


def test_the_resolver_marks_the_worker_principal_and_no_other(projection: ProductProjection) -> None:
    worker = apply_worker_account(projection, _TOKEN)
    other = projection.create_account(name="alice", password_hash=None, administrator=True, now=_T0)
    other_token = new_api_token()
    projection.create_account_token(other.account_id, name="ci", digest=digest(other_token), now=_T0)
    resolver = StoreAccountResolver(projection, idle_seconds=600)

    worker_principal = resolver.resolve(_TOKEN).principal
    other_principal = resolver.resolve(other_token).principal

    assert worker_principal is not None
    assert worker_principal.worker is True
    assert worker_principal.administrator is False
    assert other_principal is not None
    assert other_principal.worker is False
    scheduled = resolver.principal_of(worker.account_id)
    assert scheduled is not None
    assert scheduled.worker is True


def test_an_account_named_worker_that_the_environment_does_not_manage_is_not_the_worker(
    projection: ProductProjection,
) -> None:
    account = projection.create_account(name="worker", password_hash=None, now=_T0)

    assert not is_worker_account(account)
    principal = StoreAccountResolver(projection, idle_seconds=600).principal_of(account.account_id)
    assert principal is not None
    assert principal.worker is False


# The variable.


@pytest.mark.parametrize("environment", [{}, {WORKER_TOKEN_ENV: ""}], ids=("unset", "empty"))
def test_an_unset_worker_token_applies_no_worker_account_for_now(environment: dict[str, str]) -> None:
    assert worker_account_token(environment) is None


def test_a_worker_token_equal_to_the_admin_token_is_refused() -> None:
    with pytest.raises(CredentialSettingsError) as refused:
        worker_account_token({WORKER_TOKEN_ENV: _TOKEN, ADMIN_ACCOUNT_TOKEN_ENV: _TOKEN})

    assert WORKER_TOKEN_ENV in str(refused.value)
    assert _TOKEN not in str(refused.value)


def test_the_worker_token_does_not_need_the_encryption_key_yet() -> None:
    assert worker_account_token({WORKER_TOKEN_ENV: _TOKEN}) == _TOKEN


# The start of the service.


def _build(
    monkeypatch: pytest.MonkeyPatch, projection: ProductProjection, **environment: str
) -> tuple[Any, dict[str, Any]]:
    """Build the application as the service does, and capture the secret values its parts receive."""
    monkeypatch.setenv(ADMIN_ACCOUNT_PASSWORD_ENV, _ADMIN_PASSWORD)
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    captured: dict[str, Any] = {}
    real_service = serve.RunService

    def service_factory(*args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        captured["service_secrets"] = kwargs["secrets"]
        return real_service(*args, **kwargs)

    app = serve.build_app(
        projection_factory=lambda: projection, run_service_factory=service_factory, start_check=lambda _pool: None
    )
    return app, captured


def test_the_start_applies_the_worker_account_and_redacts_its_token(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    projection = local_product_projection(tmp_path)

    _app, captured = _build(
        monkeypatch, projection, **{ADMIN_ACCOUNT_TOKEN_ENV: _ADMIN_TOKEN, WORKER_TOKEN_ENV: _TOKEN}
    )

    account = projection.lookup_account_by_name(WORKER_ACCOUNT_NAME)
    assert account is not None
    assert is_worker_account(account)
    assert _token_digests(projection, account.account_id) == [digest(_TOKEN)]
    # The admin token first, then the worker token: both are redacted at every boundary.
    assert [digest(value) for value in captured["service_secrets"]] == [digest(_ADMIN_TOKEN), digest(_TOKEN)]


def test_a_second_start_with_a_new_worker_token_replaces_it(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    projection = local_product_projection(tmp_path)
    _build(monkeypatch, projection, **{WORKER_TOKEN_ENV: _TOKEN})

    _build(monkeypatch, projection, **{WORKER_TOKEN_ENV: _OTHER_TOKEN})

    account = projection.lookup_account_by_name(WORKER_ACCOUNT_NAME)
    assert account is not None
    assert _token_digests(projection, account.account_id) == [digest(_OTHER_TOKEN)]


def test_a_start_without_the_worker_token_applies_no_worker_account_and_logs_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    projection = local_product_projection(tmp_path)

    with capture_logs() as logs:
        _build(monkeypatch, projection)

    assert projection.lookup_account_by_name(WORKER_ACCOUNT_NAME) is None
    assert any(log.get("variable") == WORKER_TOKEN_ENV and log["log_level"] == "warning" for log in logs)


def test_an_account_named_worker_that_the_environment_does_not_manage_stops_the_start(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    projection = local_product_projection(tmp_path)
    projection.create_account(name="worker", password_hash=_STORED_TEXT, now=_T0)

    with pytest.raises(CredentialSettingsError) as refused:
        _build(monkeypatch, projection, **{WORKER_TOKEN_ENV: _TOKEN})

    message = str(refused.value)
    assert "named worker" in message
    assert WORKER_TOKEN_ENV in message
    assert _TOKEN not in message


def test_a_worker_token_that_another_account_holds_stops_the_start(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    projection = local_product_projection(tmp_path)
    alice = projection.create_account(name="alice", password_hash=None, now=_T0)
    projection.create_account_token(alice.account_id, name="ci", digest=digest(_TOKEN), now=_T0)

    with pytest.raises(CredentialSettingsError) as refused:
        _build(monkeypatch, projection, **{WORKER_TOKEN_ENV: _TOKEN})

    message = str(refused.value)
    assert WORKER_TOKEN_ENV in message
    assert _TOKEN not in message
    assert digest(_TOKEN) not in message


def test_a_bad_worker_token_stops_the_start_before_the_store_is_built(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ADMIN_ACCOUNT_PASSWORD_ENV, _ADMIN_PASSWORD)
    monkeypatch.setenv(WORKER_TOKEN_ENV, "short")
    built: list[str] = []

    def projection_factory() -> ProductProjection:
        built.append("projection")
        raise AssertionError

    with pytest.raises(CredentialSettingsError) as refused:
        serve.build_app(projection_factory=projection_factory, start_check=lambda _pool: None)

    assert WORKER_TOKEN_ENV in str(refused.value)
    assert built == []
    assert os.environ[WORKER_TOKEN_ENV] == "short"


# Sign-in and the account actions.


class _CountingGate:
    """A gate that compares texts and counts its checks, so a test computes no scrypt hash."""

    def __init__(self) -> None:
        self.checks = 0
        self._lock = threading.Lock()

    def verify(self, password: str, stored_hash: str, *, admit: Callable[[], bool] | None = None) -> bool:
        if admit is not None and not admit():
            raise credentials.VerificationNotAdmittedError
        with self._lock:
            self.checks += 1
        return stored_hash == f"text-hash-of:{password}"


@pytest.fixture(name="api")
def _api_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, ProductProjection, Any]:
    gate = _CountingGate()
    monkeypatch.setattr(credentials, "PASSWORD_VERIFICATION_GATE", gate)
    store = local_product_projection(tmp_path.resolve())
    apply_worker_account(store, _TOKEN)
    service = RunService(store, cast("Any", object()))
    resolver = StoreAccountResolver(store, idle_seconds=600, worker_token=_TOKEN)
    return TestClient(create_app(service, resolver)), store, gate


def _administrator_headers(store: ProductProjection) -> dict[str, str]:
    account = store.create_account(name="alice", password_hash=None, administrator=True, now=_T0)
    value = new_api_token()
    store.create_account_token(account.account_id, name="ci", digest=digest(value), now=_T0)
    return {"Authorization": f"Bearer {value}"}


def test_sign_in_as_worker_is_refused_even_with_a_stored_password(
    api: tuple[TestClient, ProductProjection, Any],
) -> None:
    client, store, gate = api
    # A hash written by hand: the refusal does not depend on the account having no password.
    _execute(
        store, "UPDATE accounts SET password_hash = ? WHERE name = ?", (f"text-hash-of:{_WORKER_PASSWORD}", "worker")
    )

    response = client.post("/auth/sign-in", json={"name": "worker", "password": _WORKER_PASSWORD})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "sign-in-refused"
    # One check, as for every other refusal: the answer takes no less time.
    assert gate.checks == 1
    assert _rows(store, "SELECT COUNT(*) FROM account_sessions") == [(0,)]


def test_sign_in_as_worker_without_a_password_is_refused(api: tuple[TestClient, ProductProjection, Any]) -> None:
    client, _store, gate = api

    response = client.post("/auth/sign-in", json={"name": "worker", "password": _WORKER_PASSWORD})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "sign-in-refused"
    assert gate.checks == 1


def _worker_snapshot(store: ProductProjection) -> list[list[tuple[Any, ...]]]:
    """Return the rows of ``worker``: the administrator's token records each use, so it is left out."""
    return [
        _rows(store, statement.replace(" ORDER BY", " WHERE name = 'worker' ORDER BY", 1))
        if "FROM accounts" in statement
        else _rows(
            store,
            statement.replace(
                " ORDER BY",
                " WHERE account_id IN (SELECT account_id FROM accounts WHERE name = 'worker') ORDER BY",
                1,
            ),
        )
        for statement in _SNAPSHOT_STATEMENTS
    ]


def _token_id(store: ProductProjection) -> str:
    account = store.lookup_account_by_name("worker")
    assert account is not None
    return store.list_account_tokens(account.account_id)[0].token_id


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("PATCH", "/accounts/worker", {"disabled": True}),
        ("PATCH", "/accounts/worker", {"administrator": True}),
        ("DELETE", "/accounts/worker", None),
        ("PUT", "/accounts/worker/password", {"new_password": "a-new-password-of-the-worker"}),
        ("DELETE", "/accounts/worker/tokens/{token_id}", None),
    ],
    ids=("disable", "set-level", "delete", "set-password", "delete-token"),
)
def test_every_account_action_on_worker_answers_environment_managed_account(
    api: tuple[TestClient, ProductProjection, Any], method: str, path: str, body: dict[str, Any] | None
) -> None:
    client, store, _gate = api
    headers = _administrator_headers(store)
    before = _worker_snapshot(store)

    response: Response = client.request(method, path.format(token_id=_token_id(store)), json=body, headers=headers)

    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "environment-managed-account"
    assert WORKER_TOKEN_ENV in error["message"]
    assert "admin" not in error["message"]
    assert _worker_snapshot(store) == before


def test_an_account_named_worker_cannot_be_created(api: tuple[TestClient, ProductProjection, Any]) -> None:
    client, store, _gate = api
    headers = _administrator_headers(store)

    response = client.post(
        "/accounts",
        json={"name": "Worker", "password": "a-password-of-twelve", "administrator": False},
        headers=headers,
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "account-name-invalid"


def test_the_account_listing_shows_worker_as_environment_managed(
    api: tuple[TestClient, ProductProjection, Any],
) -> None:
    client, store, _gate = api

    response = client.get("/accounts", headers=_administrator_headers(store))

    assert response.status_code == 200
    worker = next(account for account in response.json() if account["name"] == "worker")
    assert (worker["environment_managed"], worker["administrator"]) == (True, False)
