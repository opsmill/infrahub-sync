"""The ``admin`` account that the environment defines, applied when the service starts.

The store cases run on the profiles of ``tests/product_store/test_accounts.py``: SQLite, the
PostgreSQL store class over SQLite, and a real PostgreSQL server (``integration``).
"""

from __future__ import annotations

import os
import threading
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, cast

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("prefect")

from fastapi.testclient import TestClient

from infrahub_sync.product_store import DuplicateCredentialError, ProductProjection, local_product_projection
from infrahub_sync.service import serve
from infrahub_sync.service.auth import apply_admin_account
from infrahub_sync.service.credentials import digest, new_session_token, verify_password
from infrahub_sync.service.settings import (
    ADMIN_ACCOUNT_PASSWORD_ENV,
    ADMIN_ACCOUNT_TOKEN_ENV,
    PASSWORD_MAX_LENGTH,
    SESSION_IDLE_SECONDS_ENV,
    AccountSettingsError,
    AdminAccountSettings,
    admin_account_settings,
    session_idle_seconds,
    validate_settings,
)
from tests.product_store.test_contract import _PROFILES_WITH_POSTGRESQL, _projection_profile

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence
    from pathlib import Path

_PASSWORD = "first-admin-password"  # noqa: S105 - a test value, not a secret.
_OTHER_PASSWORD = "second-admin-password"  # noqa: S105 - a test value, not a secret.
_TOKEN = "environment-token-0001"  # noqa: S105 - a test value, not a secret.
_OTHER_TOKEN = "environment-token-0002"  # noqa: S105 - a test value, not a secret.
# The store reads no password, so any text stands for the hash of another account.
_STORED_TEXT = "scrypt$16$1$1$c2FsdA$a2V5"
_SETTINGS = AdminAccountSettings(password=_PASSWORD, token=_TOKEN)
_T0 = datetime(2026, 11, 17, 9, 0, tzinfo=timezone.utc)
# Every column of the three tables that a start can write. The digest is compared, never printed.
_SNAPSHOT_STATEMENTS = (
    "SELECT account_id, short_id, name, name_key, password_hash, administrator, disabled, environment_managed, "
    "created_at, updated_at, password_changed_at, deleted_at FROM accounts ORDER BY account_id",
    "SELECT token_id, account_id, name, digest, created_at, expires_at, last_used_at, environment_managed "
    "FROM account_tokens ORDER BY token_id",
    "SELECT session_id, account_id, digest, created_at, last_used_at FROM account_sessions ORDER BY session_id",
)


@pytest.fixture(params=_PROFILES_WITH_POSTGRESQL)
def projection(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[ProductProjection]:
    yield from _projection_profile(request.param, tmp_path, "the admin account's PostgreSQL parameter")


# The PostgreSQL store class over SQLite is left out: its cursor discards the advisory lock, so
# that profile does not serialize two callers and proves nothing about them.
@pytest.fixture(params=("local", pytest.param("postgresql", marks=pytest.mark.integration)))
def serialized_projection(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[ProductProjection]:
    yield from _projection_profile(request.param, tmp_path, "the admin account's PostgreSQL parameter")


@pytest.fixture(autouse=True)
def _environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (ADMIN_ACCOUNT_PASSWORD_ENV, ADMIN_ACCOUNT_TOKEN_ENV, SESSION_IDLE_SECONDS_ENV):
        monkeypatch.delenv(name, raising=False)


def _execute(projection: ProductProjection, statement: str, parameters: Sequence[Any] = ()) -> None:
    records = cast("Any", projection._records)
    with records._transaction() as cursor:
        cursor.execute(records._sql(statement), parameters)


def _rows(projection: ProductProjection, statement: str, parameters: Sequence[Any] = ()) -> list[tuple[Any, ...]]:
    records = cast("Any", projection._records)
    with records._reading() as cursor:
        cursor.execute(records._sql(statement), parameters)
        return [tuple(row) for row in cursor.fetchall()]


def _snapshot(projection: ProductProjection) -> list[list[tuple[Any, ...]]]:
    return [_rows(projection, statement) for statement in _SNAPSHOT_STATEMENTS]


def _token_digests(projection: ProductProjection) -> list[str]:
    return [row[0] for row in _rows(projection, "SELECT digest FROM account_tokens ORDER BY digest")]


def test_the_first_start_creates_the_admin_account(projection: ProductProjection) -> None:
    applied = apply_admin_account(projection, _SETTINGS)

    account = projection.lookup_account_by_name("admin")
    assert account is not None
    assert account == applied
    assert (account.name, account.administrator, account.disabled, account.environment_managed) == (
        "admin",
        True,
        False,
        True,
    )
    assert account.password_hash is not None
    assert account.password_hash != _PASSWORD
    assert verify_password(_PASSWORD, account.password_hash)
    assert not verify_password(_OTHER_PASSWORD, account.password_hash)


def test_the_first_start_stores_the_token_of_the_environment_as_a_digest(projection: ProductProjection) -> None:
    account = apply_admin_account(projection, _SETTINGS)

    (token,) = projection.list_account_tokens(account.account_id)
    assert (token.name, token.environment_managed, token.expires_at) == ("environment", True, None)
    assert _token_digests(projection) == [digest(_TOKEN)]
    resolved = projection.resolve_digest(digest(_TOKEN))
    assert resolved is not None
    assert (resolved.kind, resolved.account.account_id) == ("token", account.account_id)


def test_a_second_start_with_the_same_variables_changes_nothing(projection: ProductProjection) -> None:
    account = apply_admin_account(projection, _SETTINGS)
    projection.create_account_session(account.account_id, digest=digest(new_session_token()), now=_T0)
    before = _snapshot(projection)

    assert apply_admin_account(projection, _SETTINGS) == account

    assert _snapshot(projection) == before


def test_a_changed_password_replaces_the_hash_and_ends_the_sessions(projection: ProductProjection) -> None:
    account = apply_admin_account(projection, _SETTINGS)
    projection.create_account_session(account.account_id, digest=digest(new_session_token()), now=_T0)
    projection.create_account_session(account.account_id, digest=digest(new_session_token()), now=_T0)

    changed = apply_admin_account(projection, AdminAccountSettings(password=_OTHER_PASSWORD, token=_TOKEN))

    assert changed.account_id == account.account_id
    assert changed.password_hash is not None
    assert changed.password_hash != account.password_hash
    assert verify_password(_OTHER_PASSWORD, changed.password_hash)
    assert not verify_password(_PASSWORD, changed.password_hash)
    assert changed.password_changed_at > account.password_changed_at
    assert projection.list_account_sessions(account.account_id) == ()
    # The token of the environment did not change, so its row is the same row.
    assert projection.list_account_tokens(account.account_id) == projection.list_account_tokens(changed.account_id)
    assert _token_digests(projection) == [digest(_TOKEN)]


def test_an_unchanged_password_keeps_the_sessions(projection: ProductProjection) -> None:
    account = apply_admin_account(projection, _SETTINGS)
    session = projection.create_account_session(account.account_id, digest=digest(new_session_token()), now=_T0)

    apply_admin_account(projection, AdminAccountSettings(password=_PASSWORD, token=_OTHER_TOKEN))

    assert projection.list_account_sessions(account.account_id) == (session,)


def test_a_removed_token_variable_removes_the_token(projection: ProductProjection) -> None:
    account = apply_admin_account(projection, _SETTINGS)

    apply_admin_account(projection, AdminAccountSettings(password=_PASSWORD))

    assert projection.list_account_tokens(account.account_id) == ()
    assert projection.resolve_digest(digest(_TOKEN)) is None


def test_a_changed_token_variable_replaces_the_token(projection: ProductProjection) -> None:
    account = apply_admin_account(projection, _SETTINGS)

    apply_admin_account(projection, AdminAccountSettings(password=_PASSWORD, token=_OTHER_TOKEN))

    (token,) = projection.list_account_tokens(account.account_id)
    assert (token.name, token.environment_managed) == ("environment", True)
    assert _token_digests(projection) == [digest(_OTHER_TOKEN)]
    assert projection.resolve_digest(digest(_TOKEN)) is None


def test_a_token_variable_added_later_adds_the_token(projection: ProductProjection) -> None:
    account = apply_admin_account(projection, AdminAccountSettings(password=_PASSWORD))
    assert projection.list_account_tokens(account.account_id) == ()

    apply_admin_account(projection, _SETTINGS)

    assert _token_digests(projection) == [digest(_TOKEN)]


def test_a_start_ends_the_refusal_after_repeated_failures_for_admin_only(projection: ProductProjection) -> None:
    for name_key in ("admin", "alice"):
        _execute(
            projection,
            "INSERT INTO sign_in_attempts (name_key, failed_count, window_started_at, refused_until) "
            "VALUES (?, ?, ?, ?)",
            (name_key, 5, _T0.isoformat(), (_T0 + timedelta(minutes=15)).isoformat()),
        )

    apply_admin_account(projection, _SETTINGS)

    assert _rows(projection, "SELECT name_key FROM sign_in_attempts") == [("alice",)]


def test_a_start_gives_admin_its_states_back(projection: ProductProjection) -> None:
    account = apply_admin_account(projection, _SETTINGS)
    _execute(
        projection,
        "UPDATE accounts SET administrator = ?, disabled = ?, environment_managed = ? WHERE account_id = ?",
        (False, True, False, account.account_id),
    )

    restored = apply_admin_account(projection, _SETTINGS)

    assert (restored.administrator, restored.disabled, restored.environment_managed) == (True, False, True)
    assert restored.password_hash == account.password_hash


def test_a_start_leaves_the_other_accounts_and_their_tokens(projection: ProductProjection) -> None:
    alice = projection.create_account(name="alice", password_hash=_STORED_TEXT, now=_T0)
    projection.create_account_token(alice.account_id, name="ci", digest=digest("alice-token-value"), now=_T0)
    session = projection.create_account_session(alice.account_id, digest=digest("alice-session-value"), now=_T0)

    apply_admin_account(projection, _SETTINGS)
    apply_admin_account(projection, AdminAccountSettings(password=_OTHER_PASSWORD))

    assert projection.lookup_account(alice.account_id) == alice
    assert [token.name for token in projection.list_account_tokens(alice.account_id)] == ["ci"]
    assert projection.list_account_sessions(alice.account_id) == (session,)


def test_a_token_that_another_account_holds_is_refused_without_its_digest(projection: ProductProjection) -> None:
    alice = projection.create_account(name="alice", password_hash=None, now=_T0)
    projection.create_account_token(alice.account_id, name="ci", digest=digest(_TOKEN), now=_T0)

    with pytest.raises(DuplicateCredentialError) as refused:
        apply_admin_account(projection, _SETTINGS)

    assert digest(_TOKEN) not in str(refused.value)
    assert refused.value.__cause__ is None
    assert refused.value.__context__ is None
    # The transaction was rolled back: no admin account without its token.
    assert projection.lookup_account_by_name("admin") is None


def _apply_from_two_threads(projection: ProductProjection) -> list[str]:
    """Apply the variables from two threads that start together; return the types of their failures."""
    start = threading.Barrier(2)
    failures: list[str] = []

    def apply() -> None:
        try:
            start.wait(timeout=10)
            apply_admin_account(projection, _SETTINGS)
        except BaseException as failure:  # noqa: BLE001 - reported to the caller, which asserts on it.
            failures.append(type(failure).__name__)

    threads = [threading.Thread(target=apply) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    return failures


def test_two_threads_that_apply_the_variables_together_both_succeed(
    serialized_projection: ProductProjection,
) -> None:
    """Each round starts from a store with no ``admin``, where both threads want to create it."""
    projection = serialized_projection
    for _round in range(10):
        _execute(projection, "DELETE FROM account_tokens")
        _execute(projection, "DELETE FROM account_sessions")
        _execute(projection, "DELETE FROM accounts")

        assert _apply_from_two_threads(projection) == []

        (account,) = projection.list_accounts()
        assert account.name == "admin"
        assert account.password_hash is not None
        assert verify_password(_PASSWORD, account.password_hash)
        assert _token_digests(projection) == [digest(_TOKEN)]


# The variables.


def test_the_variables_of_the_admin_account_are_read_from_the_environment() -> None:
    settings = admin_account_settings({ADMIN_ACCOUNT_PASSWORD_ENV: _PASSWORD, ADMIN_ACCOUNT_TOKEN_ENV: _TOKEN})

    assert (settings.password, settings.token) == (_PASSWORD, _TOKEN)
    assert _PASSWORD not in repr(settings)
    assert _TOKEN not in repr(settings)


@pytest.mark.parametrize("environment", [{}, {ADMIN_ACCOUNT_TOKEN_ENV: ""}], ids=("unset", "empty"))
def test_an_unset_or_empty_token_variable_means_no_token(environment: dict[str, str]) -> None:
    settings = admin_account_settings({ADMIN_ACCOUNT_PASSWORD_ENV: _PASSWORD, **environment})

    assert settings.token is None


@pytest.mark.parametrize(
    ("environment", "variable"),
    [
        pytest.param({}, ADMIN_ACCOUNT_PASSWORD_ENV, id="password-unset"),
        pytest.param({ADMIN_ACCOUNT_PASSWORD_ENV: ""}, ADMIN_ACCOUNT_PASSWORD_ENV, id="password-empty"),
        pytest.param({ADMIN_ACCOUNT_PASSWORD_ENV: "short-secre"}, ADMIN_ACCOUNT_PASSWORD_ENV, id="password-of-11"),
        pytest.param(
            {ADMIN_ACCOUNT_PASSWORD_ENV: "p" * (PASSWORD_MAX_LENGTH + 1)},
            ADMIN_ACCOUNT_PASSWORD_ENV,
            id="password-of-1025",
        ),
        # A byte that is not UTF-8 reaches Python as a lone surrogate.
        pytest.param(
            {ADMIN_ACCOUNT_PASSWORD_ENV: _PASSWORD + "\udcff"}, ADMIN_ACCOUNT_PASSWORD_ENV, id="password-not-utf-8"
        ),
        pytest.param(
            {ADMIN_ACCOUNT_PASSWORD_ENV: _PASSWORD, ADMIN_ACCOUNT_TOKEN_ENV: "short-token-015"},
            ADMIN_ACCOUNT_TOKEN_ENV,
            id="token-of-15",
        ),
        pytest.param(
            {ADMIN_ACCOUNT_PASSWORD_ENV: _PASSWORD, ADMIN_ACCOUNT_TOKEN_ENV: "ifs_pat_chosen-by-the-operator"},
            ADMIN_ACCOUNT_TOKEN_ENV,
            id="token-with-the-api-token-prefix",
        ),
        pytest.param(
            {ADMIN_ACCOUNT_PASSWORD_ENV: _PASSWORD, ADMIN_ACCOUNT_TOKEN_ENV: "ifs_ses_chosen-by-the-operator"},
            ADMIN_ACCOUNT_TOKEN_ENV,
            id="token-with-the-session-prefix",
        ),
        # A request presents the token in an HTTP header: such a token could never match.
        pytest.param(
            {ADMIN_ACCOUNT_PASSWORD_ENV: _PASSWORD, ADMIN_ACCOUNT_TOKEN_ENV: "token-chosen-by-the-opérateur"},
            ADMIN_ACCOUNT_TOKEN_ENV,
            id="token-not-ascii",
        ),
        pytest.param(
            {ADMIN_ACCOUNT_PASSWORD_ENV: _PASSWORD, ADMIN_ACCOUNT_TOKEN_ENV: "token-chosen-by-the-operator\udcff"},
            ADMIN_ACCOUNT_TOKEN_ENV,
            id="token-not-utf-8",
        ),
        pytest.param(
            {ADMIN_ACCOUNT_PASSWORD_ENV: _PASSWORD, ADMIN_ACCOUNT_TOKEN_ENV: " token-chosen-by-the-operator"},
            ADMIN_ACCOUNT_TOKEN_ENV,
            id="token-with-a-leading-space",
        ),
    ],
)
def test_a_bad_variable_stops_the_start_with_a_message_that_names_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, environment: dict[str, str], variable: str
) -> None:
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    built: list[str] = []

    def projection_factory() -> ProductProjection:
        built.append("projection")
        return local_product_projection(tmp_path)

    with pytest.raises(AccountSettingsError) as refused:
        serve.build_app(projection_factory=projection_factory)

    message = str(refused.value)
    assert variable in message
    assert all(value not in message for value in environment.values() if value)
    # The variables are checked before any dependency is built.
    assert built == []


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, 28800), ("", 28800), ("60", 60), (" 3600 ", 3600)],
    ids=("unset", "empty", "minimum", "spaces"),
)
def test_the_idle_period_is_read_from_the_environment(value: str | None, expected: int) -> None:
    environment = {} if value is None else {SESSION_IDLE_SECONDS_ENV: value}

    assert session_idle_seconds(environment) == expected


@pytest.mark.parametrize("value", ["59", "7", "-60", "3600.5", "1e4", "an hour", "9" * 5000])
def test_an_idle_period_that_is_not_a_whole_number_of_at_least_60_is_refused(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv(SESSION_IDLE_SECONDS_ENV, value)

    with pytest.raises(AccountSettingsError) as refused:
        validate_settings()

    assert SESSION_IDLE_SECONDS_ENV in str(refused.value)
    assert value not in str(refused.value)


def test_main_stops_with_the_message_of_a_bad_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(serve, "install_custom_ca_bundle", lambda: None)

    with pytest.raises(SystemExit) as stopped:
        serve.main()

    assert ADMIN_ACCOUNT_PASSWORD_ENV in str(stopped.value)


# The served application.


def _build(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, **environment: str) -> tuple[Any, dict[str, Any]]:
    """Build the application as the service does, and capture what its parts receive."""
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("INFRAHUB_SYNC_REQUIRE_APPROVAL", raising=False)
    captured: dict[str, Any] = {}
    real_routes = serve.ConfigurationRoutes
    real_service = serve.RunService

    def routes_factory(**kwargs: Any) -> Any:  # noqa: ANN401
        captured["configuration_secrets"] = kwargs["secrets"]
        return real_routes(**kwargs)

    def service_factory(*args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        captured["service_secrets"] = kwargs["secrets"]
        return real_service(*args, **kwargs)

    projection = local_product_projection(tmp_path)
    captured["projection"] = projection
    app = serve.build_app(
        projection_factory=lambda: projection,
        run_service_factory=service_factory,
        configuration_routes_factory=routes_factory,
        start_check=lambda _pool: None,
    )
    return app, captured


def test_a_token_that_another_account_holds_stops_the_start_with_a_message_that_names_the_variable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    projection = local_product_projection(tmp_path)
    alice = projection.create_account(name="alice", password_hash=None, now=_T0)
    projection.create_account_token(alice.account_id, name="ci", digest=digest(_TOKEN), now=_T0)
    monkeypatch.setenv(ADMIN_ACCOUNT_PASSWORD_ENV, _PASSWORD)
    monkeypatch.setenv(ADMIN_ACCOUNT_TOKEN_ENV, _TOKEN)

    with pytest.raises(AccountSettingsError) as refused:
        serve.build_app(projection_factory=lambda: projection, start_check=lambda _pool: None)

    message = str(refused.value)
    assert ADMIN_ACCOUNT_TOKEN_ENV in message
    assert _TOKEN not in message
    assert digest(_TOKEN) not in message


def test_the_password_is_absent_from_the_environment_and_from_the_secret_values_afterwards(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _app, captured = _build(
        monkeypatch, tmp_path, **{ADMIN_ACCOUNT_PASSWORD_ENV: _PASSWORD, ADMIN_ACCOUNT_TOKEN_ENV: _TOKEN}
    )

    assert ADMIN_ACCOUNT_PASSWORD_ENV not in os.environ
    assert _PASSWORD not in captured["configuration_secrets"]
    assert _PASSWORD not in captured["service_secrets"]
    # The token of the environment stays a known secret, as a principal token was.
    assert _TOKEN in captured["configuration_secrets"]
    assert captured["service_secrets"] == (_TOKEN,)
    assert os.environ[ADMIN_ACCOUNT_TOKEN_ENV] == _TOKEN


def test_the_served_application_resolves_the_token_of_the_environment_to_admin(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    app, captured = _build(
        monkeypatch, tmp_path, **{ADMIN_ACCOUNT_PASSWORD_ENV: _PASSWORD, ADMIN_ACCOUNT_TOKEN_ENV: _TOKEN}
    )
    account = captured["projection"].lookup_account_by_name("admin")

    with TestClient(app) as client:
        response = client.get("/me", headers={"Authorization": f"Bearer {_TOKEN}"})
        refused = client.get("/me", headers={"Authorization": f"Bearer {_PASSWORD}"})

    assert response.status_code == 200
    assert response.json()["actor"] == "admin"
    assert response.json()["administrator"] is True
    assert account.account_id not in response.text
    assert refused.status_code == 401


def test_the_removed_variable_is_no_longer_read(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    old_token = "old-principal-token-0001"  # noqa: S105 - a test value, not a secret.
    monkeypatch.setenv(
        "INFRAHUB_SYNC_SERVICE_BEARER_TOKENS", f'{{"alice": {{"token": "{old_token}", "administrator": true}}}}'
    )

    app, _captured = _build(monkeypatch, tmp_path, **{ADMIN_ACCOUNT_PASSWORD_ENV: _PASSWORD})

    with TestClient(app) as client:
        assert client.get("/me", headers={"Authorization": f"Bearer {old_token}"}).status_code == 401


def test_a_resolver_given_to_the_factory_leaves_the_store_without_an_admin_account(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The seam the tests use until they build accounts in the store: no variable is needed."""
    projection = local_product_projection(tmp_path)

    class _Resolver:
        secret_values: tuple[str, ...] = ()

    monkeypatch.delenv("INFRAHUB_SYNC_REQUIRE_APPROVAL", raising=False)
    serve.build_app(projection_factory=lambda: projection, resolver_factory=_Resolver, start_check=lambda _pool: None)

    assert projection.lookup_account_by_name("admin") is None


def test_a_password_variable_of_1024_characters_is_accepted() -> None:
    password = "p" * PASSWORD_MAX_LENGTH

    assert admin_account_settings({ADMIN_ACCOUNT_PASSWORD_ENV: password}).password == password


@pytest.mark.parametrize(
    ("password", "rule"),
    [
        ("p" * (PASSWORD_MAX_LENGTH + 1), "must have at most 1024 characters"),
        (_PASSWORD + "\ud800", "must be valid UTF-8 text"),
    ],
    ids=("over-the-maximum", "lone-surrogate"),
)
def test_a_password_variable_that_no_sign_in_can_use_is_refused_with_the_rule(password: str, rule: str) -> None:
    with pytest.raises(AccountSettingsError) as refused:
        admin_account_settings({ADMIN_ACCOUNT_PASSWORD_ENV: password})

    assert str(refused.value) == f"{ADMIN_ACCOUNT_PASSWORD_ENV} {rule}"
    assert password not in str(refused.value)
    assert refused.value.__cause__ is None
