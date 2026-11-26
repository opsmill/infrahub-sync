"""The client methods of the local accounts: each calls its route and keeps every secret out of what it prints."""

from __future__ import annotations

import json
import traceback
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

import httpx
import pytest

from infrahub_sync.client import (
    AccountResource,
    APIError,
    ClientInputError,
    CreatedTokenResource,
    NotSyncAPIError,
    ProtocolError,
    SessionResource,
    SyncClient,
    TokenResource,
)

if TYPE_CHECKING:
    from collections.abc import Callable

URL = "https://sync.example.test"
BEARER = "bearer-value-1"
PASSWORD = "correct horse battery staple"  # noqa: S105 - a canary that must never be printed.
NEW_PASSWORD = "  another long passphrase  "  # noqa: S105 - leading and trailing spaces must be kept.
SESSION_TOKEN = "ifs_session_canary"  # noqa: S105 - a canary that must never be printed.
TOKEN_VALUE = "ifs_token_canary"  # noqa: S105 - a canary that must never be printed.
TOKEN_ID = "0b5c4e0e-9a51-4f0b-8f0e-3f1f6f6f0a11"  # noqa: S105 - an identifier, not a value.
NOW = datetime(2026, 11, 18, 12, tzinfo=timezone.utc)
VERSION = {"server_version": "3.0.0", "api_versions": ["v3-unstable"], "stability": "unstable"}


def _account(name: str = "alice", **extra: object) -> dict[str, object]:
    return {
        "name": name,
        "administrator": False,
        "disabled": False,
        "environment_managed": False,
        "created_at": NOW.isoformat(),
        "sign_in_refused_until": None,
        **extra,
    }


def _token(**extra: object) -> dict[str, object]:
    return {
        "token_id": TOKEN_ID,
        "name": "ci",
        "created_at": NOW.isoformat(),
        "expires_at": None,
        "last_used_at": None,
        **extra,
    }


SESSION = {"token": SESSION_TOKEN, "account": _account(), "idle_seconds": 28800}
NO_CONTENT = httpx.Response(204)
DELETED = {"placeholder": "Deleted account 0badc0de"}


class _Recorder:
    """Answer `/api/version`, record every other request and return one prepared answer."""

    def __init__(self, response: httpx.Response) -> None:
        self.response = response
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/version":
            return httpx.Response(200, json=VERSION)
        self.requests.append(request)
        return self.response

    @property
    def request(self) -> httpx.Request:
        assert len(self.requests) == 1
        return self.requests[0]

    def body(self) -> object:
        return json.loads(self.request.content)


def _client(recorder: _Recorder) -> SyncClient:
    return SyncClient(URL, BEARER, transport=httpx.MockTransport(recorder))


def _error(status: int, code: str, message: str = "server text") -> httpx.Response:
    return httpx.Response(status, json={"error": {"code": code, "message": message, "status": status}})


# One row for each client method: the call, the HTTP method, the path, the query, the request
# body, the answer of the Sync API and a check of what the method returns.
CALLS: list[tuple[str, Callable[[SyncClient], object], str, str, dict[str, str], object, httpx.Response]] = [
    ("sign_out", lambda c: c.sign_out(), "POST", "/api/auth/sign-out", {}, None, NO_CONTENT),
    (
        "change_password",
        lambda c: c.change_password(PASSWORD, NEW_PASSWORD, reason="rotation"),
        "PUT",
        "/api/me/password",
        {},
        {"current_password": PASSWORD, "new_password": NEW_PASSWORD, "reason": "rotation"},
        NO_CONTENT,
    ),
    ("list_tokens", lambda c: c.list_tokens(), "GET", "/api/me/tokens", {}, None, httpx.Response(200, json=[_token()])),
    (
        "create_token",
        lambda c: c.create_token("ci", expires_at=NOW, reason="pipeline"),
        "POST",
        "/api/me/tokens",
        {},
        {"name": "ci", "expires_at": NOW.isoformat(), "reason": "pipeline"},
        httpx.Response(201, json=_token(token=TOKEN_VALUE)),
    ),
    (
        "create_token_without_expiry",
        lambda c: c.create_token("ci"),
        "POST",
        "/api/me/tokens",
        {},
        {"name": "ci"},
        httpx.Response(201, json=_token(token=TOKEN_VALUE)),
    ),
    (
        "delete_token",
        lambda c: c.delete_token(TOKEN_ID, reason="leaked"),
        "DELETE",
        f"/api/me/tokens/{TOKEN_ID}",
        {"reason": "leaked"},
        None,
        NO_CONTENT,
    ),
    (
        "delete_token_without_reason",
        lambda c: c.delete_token(TOKEN_ID),
        "DELETE",
        f"/api/me/tokens/{TOKEN_ID}",
        {},
        None,
        NO_CONTENT,
    ),
    (
        "list_accounts",
        lambda c: c.list_accounts("disabled"),
        "GET",
        "/api/accounts",
        {"state": "disabled"},
        None,
        httpx.Response(200, json=[_account(), _account("bob")]),
    ),
    (
        "create_account",
        lambda c: c.create_account("alice", PASSWORD, administrator=True, reason="new colleague"),
        "POST",
        "/api/accounts",
        {},
        {"name": "alice", "password": PASSWORD, "administrator": True, "reason": "new colleague"},
        httpx.Response(201, json=_account()),
    ),
    (
        "get_account",
        lambda c: c.get_account("a.b_c@example-1"),
        "GET",
        "/api/accounts/a.b_c@example-1",
        {},
        None,
        httpx.Response(200, json=_account("a.b_c@example-1")),
    ),
    (
        "set_account_disabled",
        lambda c: c.set_account_disabled("alice", True, reason="left"),  # noqa: FBT003
        "PATCH",
        "/api/accounts/alice",
        {},
        {"disabled": True, "reason": "left"},
        httpx.Response(200, json=_account(disabled=True)),
    ),
    (
        "set_account_password",
        lambda c: c.set_account_password("alice", NEW_PASSWORD),
        "PUT",
        "/api/accounts/alice/password",
        {},
        {"new_password": NEW_PASSWORD},
        NO_CONTENT,
    ),
    (
        "unlock_account",
        lambda c: c.unlock_account("alice", reason="confirmed by phone"),
        "POST",
        "/api/accounts/alice/unlock",
        {},
        {"reason": "confirmed by phone"},
        NO_CONTENT,
    ),
    (
        "unlock_account_without_reason",
        lambda c: c.unlock_account("alice"),
        "POST",
        "/api/accounts/alice/unlock",
        {},
        None,
        NO_CONTENT,
    ),
    (
        "set_account_level",
        lambda c: c.set_account_level("alice", True, reason="team lead"),  # noqa: FBT003
        "PATCH",
        "/api/accounts/alice",
        {},
        {"administrator": True, "reason": "team lead"},
        httpx.Response(200, json=_account(administrator=True)),
    ),
    (
        "set_account_level_lower",
        lambda c: c.set_account_level("alice", False),  # noqa: FBT003
        "PATCH",
        "/api/accounts/alice",
        {},
        {"administrator": False},
        httpx.Response(200, json=_account()),
    ),
    (
        "delete_account",
        lambda c: c.delete_account("alice", reason="left the company"),
        "DELETE",
        "/api/accounts/alice",
        {"reason": "left the company"},
        None,
        httpx.Response(200, json=DELETED),
    ),
    (
        "delete_account_without_reason",
        lambda c: c.delete_account("alice"),
        "DELETE",
        "/api/accounts/alice",
        {},
        None,
        httpx.Response(200, json=DELETED),
    ),
    (
        "list_account_tokens",
        lambda c: c.list_account_tokens("alice"),
        "GET",
        "/api/accounts/alice/tokens",
        {},
        None,
        httpx.Response(200, json=[_token()]),
    ),
    (
        "delete_account_token",
        lambda c: c.delete_account_token("alice", TOKEN_ID, reason="leaked"),
        "DELETE",
        f"/api/accounts/alice/tokens/{TOKEN_ID}",
        {"reason": "leaked"},
        None,
        NO_CONTENT,
    ),
]


@pytest.mark.parametrize(
    ("call", "method", "path", "query", "body", "response"),
    [pytest.param(*row[1:], id=row[0]) for row in CALLS],
)
def test_each_method_calls_its_route_with_the_token_and_the_body(  # noqa: PLR0913, PLR0917
    call: Callable[[SyncClient], object],
    method: str,
    path: str,
    query: dict[str, str],
    body: object,
    response: httpx.Response,
) -> None:
    recorder = _Recorder(response)

    call(_client(recorder))

    request = recorder.request
    assert (request.method, request.url.path) == (method, path)
    assert dict(request.url.params) == query
    assert request.headers["Authorization"] == f"Bearer {BEARER}"
    if body is None:
        assert request.content == b""
    else:
        assert recorder.body() == body
    # A password travels in the body only: never in the address and never in a header.
    for secret in (PASSWORD, NEW_PASSWORD.strip()):
        assert secret not in str(request.url)
        assert all(secret not in value for value in request.headers.values())


def test_sign_in_sends_no_token_and_returns_the_session() -> None:
    recorder = _Recorder(httpx.Response(200, json=SESSION))
    client = SyncClient.without_token(URL, transport=httpx.MockTransport(recorder))

    session = client.sign_in("alice", PASSWORD)

    request = recorder.request
    assert (request.method, request.url.path) == ("POST", "/api/auth/sign-in")
    assert "Authorization" not in request.headers
    assert recorder.body() == {"name": "alice", "password": PASSWORD}
    assert PASSWORD not in str(request.url)
    assert isinstance(session, SessionResource)
    assert session.token == SESSION_TOKEN
    assert session.account.name == "alice"
    assert session.idle_seconds == 28800


def test_sign_in_sends_no_token_even_when_the_client_holds_one() -> None:
    recorder = _Recorder(httpx.Response(200, json=SESSION))

    _client(recorder).sign_in("alice", PASSWORD)

    assert "Authorization" not in recorder.request.headers


@pytest.mark.parametrize(
    "call",
    [
        lambda c: c.sign_out(),
        lambda c: c.me(),
        lambda c: c.list_tokens(),
        lambda c: c.list_accounts(),
        lambda c: c.change_password(PASSWORD, NEW_PASSWORD),
        lambda c: c.create_token("ci"),
        lambda c: c.list_configs(),
    ],
)
def test_a_client_without_a_token_sends_only_the_sign_in(call: Callable[[SyncClient], object]) -> None:
    recorder = _Recorder(NO_CONTENT)
    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return recorder(request)

    client = SyncClient.without_token(URL, transport=httpx.MockTransport(handler))

    with pytest.raises(ClientInputError) as raised:
        call(client)

    assert raised.value.argument == "token"
    assert sent == []


def test_the_methods_return_typed_resources() -> None:
    tokens = _client(_Recorder(httpx.Response(200, json=[_token(last_used_at=NOW.isoformat())]))).list_tokens()
    accounts = _client(
        _Recorder(httpx.Response(200, json=[_account(), _account("bob", disabled=True)]))
    ).list_accounts()
    created = _client(_Recorder(httpx.Response(201, json=_token(token=TOKEN_VALUE)))).create_token("ci")
    account = _client(_Recorder(httpx.Response(200, json=_account(administrator=True)))).get_account("alice")

    assert tokens == (TokenResource.model_validate(_token(last_used_at=NOW.isoformat())),)
    assert tokens[0].last_used_at == NOW
    assert [entry.name for entry in accounts] == ["alice", "bob"]
    assert all(isinstance(entry, AccountResource) for entry in accounts)
    assert isinstance(created, CreatedTokenResource)
    assert created.token == TOKEN_VALUE
    assert created.token_id == TOKEN_ID
    assert account.administrator is True


def test_the_printed_form_of_a_session_and_of_a_created_token_holds_no_value() -> None:
    session = SessionResource.model_validate(SESSION)
    created = CreatedTokenResource.model_validate(_token(token=TOKEN_VALUE))

    for printed in (repr(session), str(session), f"{session}", repr([session])):
        assert SESSION_TOKEN not in printed
        assert "alice" in printed
    for printed in (repr(created), str(created), f"{created}", repr((created,))):
        assert TOKEN_VALUE not in printed
        assert "ci" in printed
    # Rich and other pretty-printers read `__repr_args__`, not `__repr__`.
    assert "token" not in dict(session.__repr_args__())
    assert "token" not in dict(created.__repr_args__())


@pytest.mark.parametrize(
    ("call", "status", "code"),
    [
        pytest.param(lambda c: c.sign_in("alice", PASSWORD), 401, "sign-in-refused", id="401-sign-in"),
        pytest.param(lambda c: c.list_tokens(), 401, "unauthenticated", id="401-list"),
        pytest.param(lambda c: c.sign_out(), 401, "token-expired", id="401-no-content"),
        pytest.param(lambda c: c.list_accounts(), 403, "forbidden", id="403-list"),
        pytest.param(lambda c: c.change_password(PASSWORD, NEW_PASSWORD), 403, "current-password-refused", id="403"),
        pytest.param(lambda c: c.get_account("ghost"), 404, "account-not-found", id="404-model"),
        pytest.param(lambda c: c.delete_token(TOKEN_ID), 404, "token-not-found", id="404-no-content"),
        pytest.param(lambda c: c.create_account("alice", PASSWORD), 409, "account-name-taken", id="409-model"),
        pytest.param(
            lambda c: c.set_account_password("admin", NEW_PASSWORD), 409, "environment-managed-account", id="409-empty"
        ),
        pytest.param(lambda c: c.create_token("ci"), 409, "token-name-taken", id="409-token"),
        pytest.param(lambda c: c.create_account("alice", "short"), 422, "password-too-short", id="422-model"),
        pytest.param(lambda c: c.sign_out(), 422, "not-a-session", id="422-no-content"),
        pytest.param(lambda c: c.sign_in("alice", PASSWORD), 503, "sign-in-busy", id="503-sign-in"),
        pytest.param(lambda c: c.set_account_disabled("alice", True), 503, "sign-in-busy", id="503-model"),  # noqa: FBT003
        pytest.param(lambda c: c.delete_account("admin"), 409, "environment-managed-account", id="409-delete"),
        pytest.param(lambda c: c.delete_account("ghost"), 404, "account-not-found", id="404-delete"),
        pytest.param(lambda c: c.list_account_tokens("ghost"), 404, "account-not-found", id="404-account-tokens"),
        pytest.param(
            lambda c: c.delete_account_token("alice", TOKEN_ID), 404, "token-not-found", id="404-account-token"
        ),
        pytest.param(lambda c: c.set_account_level("alice", True), 403, "forbidden", id="403-level"),  # noqa: FBT003
    ],
)
def test_a_refusal_is_a_typed_api_error_without_the_answer_text(
    call: Callable[[SyncClient], object], status: int, code: str
) -> None:
    marker = f"server-text-{PASSWORD}"
    recorder = _Recorder(_error(status, code, marker))

    with pytest.raises(APIError) as raised:
        call(_client(recorder))

    error = raised.value
    assert (error.status, error.code) == (status, code)
    printed = "".join(traceback.format_exception(type(error), error, error.__traceback__)) + repr(error)
    for secret in (marker, PASSWORD, NEW_PASSWORD, BEARER):
        assert secret not in printed
    assert error.__cause__ is None


@pytest.mark.parametrize(
    ("call", "argument"),
    [
        pytest.param(lambda c: c.get_account("a/b"), "name", id="account-name-with-slash"),
        pytest.param(lambda c: c.get_account(""), "name", id="account-name-empty"),
        pytest.param(lambda c: c.unlock_account("a b"), "name", id="account-name-with-space"),
        pytest.param(lambda c: c.set_account_password("../me", NEW_PASSWORD), "name", id="account-name-path"),
        pytest.param(lambda c: c.set_account_disabled("alice", "yes"), "disabled", id="disabled-not-bool"),
        pytest.param(lambda c: c.delete_token("a/b"), "token_id", id="token-id"),
        pytest.param(lambda c: c.list_accounts("deleted"), "state", id="state"),
        pytest.param(lambda c: c.create_token("ci", expires_at=datetime(2027, 1, 1)), "expires_at", id="naive-expiry"),  # noqa: DTZ001
        pytest.param(lambda c: c.create_account("alice", PASSWORD, administrator="yes"), "administrator", id="level"),
        pytest.param(lambda c: c.sign_in("alice", None), "password", id="password-not-text"),
        pytest.param(lambda c: c.change_password(None, NEW_PASSWORD), "current_password", id="current-not-text"),
        pytest.param(lambda c: c.change_password(PASSWORD, 1), "new_password", id="new-not-text"),
        pytest.param(lambda c: c.unlock_account("alice", reason=""), "reason", id="empty-reason"),
        pytest.param(lambda c: c.set_account_level("alice", "yes"), "administrator", id="level-not-bool"),
        pytest.param(lambda c: c.delete_account("a/b"), "name", id="delete-account-name-path"),
        pytest.param(lambda c: c.delete_account("alice", reason=""), "reason", id="delete-empty-reason"),
        pytest.param(lambda c: c.unlock_account("alice", reason="x" * 513), "reason", id="reason-too-long"),
        pytest.param(lambda c: c.unlock_account("alice", reason=" spaced"), "reason", id="reason-with-a-space"),
        pytest.param(lambda c: c.unlock_account("alice", reason="two\nlines"), "reason", id="reason-not-printable"),
        pytest.param(lambda c: c.list_account_tokens("../x"), "name", id="account-tokens-name-path"),
        pytest.param(lambda c: c.delete_account_token("alice", "a/b"), "token_id", id="account-token-id"),
        pytest.param(lambda c: c.delete_account_token("a b", TOKEN_ID), "name", id="account-token-name"),
    ],
)
def test_an_argument_the_client_refuses_sends_no_request(call: Callable[[Any], object], argument: str) -> None:
    recorder = _Recorder(NO_CONTENT)

    with pytest.raises(ClientInputError) as raised:
        call(_client(recorder))

    assert raised.value.argument == argument
    assert recorder.requests == []
    assert PASSWORD not in repr(raised.value)


@pytest.mark.parametrize(
    ("call", "response"),
    [
        pytest.param(lambda c: c.list_tokens(), httpx.Response(200, json={"items": []}), id="list-not-an-array"),
        pytest.param(lambda c: c.list_accounts(), httpx.Response(200, json=[{"name": "alice"}]), id="list-item-shape"),
        pytest.param(lambda c: c.sign_out(), httpx.Response(200, json={}), id="no-content-answers-200"),
        pytest.param(lambda c: c.get_account("alice"), httpx.Response(200, json={"name": 1}), id="model-shape"),
        pytest.param(lambda c: c.create_token("ci"), httpx.Response(201, json=_token()), id="created-token-no-value"),
        pytest.param(lambda c: c.delete_account("alice"), httpx.Response(200, json={}), id="deletion-no-placeholder"),
    ],
)
def test_an_answer_of_another_shape_is_a_protocol_error(
    call: Callable[[SyncClient], object], response: httpx.Response
) -> None:
    with pytest.raises(ProtocolError):
        call(_client(_Recorder(response)))


def test_a_web_page_in_place_of_an_empty_answer_is_not_a_sync_api() -> None:
    page = httpx.Response(502, text="<html>bad gateway</html>", headers={"Content-Type": "text/html"})

    with pytest.raises(NotSyncAPIError):
        _client(_Recorder(page)).sign_out()


def test_from_environment_reads_the_two_variables_and_no_session_file(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse_read(*_args: object, **_kwargs: object) -> None:
        message = "from_environment must not read the session file"
        raise AssertionError(message)

    monkeypatch.setattr("infrahub_sync.cli_session.default_directory", refuse_read)
    monkeypatch.setattr("infrahub_sync.cli_session.read_session", refuse_read)
    monkeypatch.setattr("typer.get_app_dir", refuse_read)
    monkeypatch.setenv("INFRAHUB_SYNC_API_URL", URL)
    monkeypatch.setenv("INFRAHUB_SYNC_API_TOKEN", BEARER)
    recorder = _Recorder(httpx.Response(200, json=[]))

    client = SyncClient.from_environment(transport=httpx.MockTransport(recorder))

    assert client.list_tokens() == ()
    assert recorder.request.headers["Authorization"] == f"Bearer {BEARER}"


def test_from_environment_without_the_token_variable_refuses_and_reads_no_session_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def refuse_read(*_args: object, **_kwargs: object) -> None:
        message = "from_environment must not read the session file"
        raise AssertionError(message)

    monkeypatch.setattr("infrahub_sync.cli_session.default_directory", refuse_read)
    monkeypatch.setattr("typer.get_app_dir", refuse_read)
    monkeypatch.setenv("INFRAHUB_SYNC_API_URL", URL)
    monkeypatch.delenv("INFRAHUB_SYNC_API_TOKEN", raising=False)

    with pytest.raises(ClientInputError) as raised:
        SyncClient.from_environment()

    assert raised.value.argument == "token"


def test_the_client_package_imports_no_service_and_no_session_module() -> None:
    import subprocess  # noqa: S404 - a fresh interpreter shows what the import alone loads.
    import sys

    script = (
        "import sys, infrahub_sync.client\n"
        "loaded = [name for name in sys.modules if name.startswith(('infrahub_sync.service', "
        "'infrahub_sync.product_store', 'infrahub_sync.cli_session', 'infrahub_sync.cli_accounts', "
        "'fastapi', 'psycopg'))]\n"
        "print(loaded)\n"
    )
    result = subprocess.run([sys.executable, "-c", script], check=True, capture_output=True, text=True)  # noqa: S603

    assert result.stdout.strip() == "[]"


def test_the_account_methods_of_an_administrator_return_typed_values() -> None:
    placeholder = _client(_Recorder(httpx.Response(200, json=DELETED))).delete_account("alice")
    tokens = _client(_Recorder(httpx.Response(200, json=[_token()]))).list_account_tokens("alice")
    account = _client(_Recorder(httpx.Response(200, json=_account(administrator=True)))).set_account_level(
        "alice",
        True,  # noqa: FBT003
    )

    assert placeholder == "Deleted account 0badc0de"
    assert tokens == (TokenResource.model_validate(_token()),)
    assert isinstance(account, AccountResource)
    assert account.administrator is True
