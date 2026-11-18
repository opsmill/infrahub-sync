"""The routes of an account on itself: its API tokens and its own password.

The cases compare texts instead of hashing, except the one that changes a real password.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("prefect")

from infrahub_sync.product_store.accounts import SIGN_IN_FAILURE_LIMIT, SIGN_IN_REFUSAL_PERIOD
from infrahub_sync.service import account_actions, credentials
from infrahub_sync.service.account_actions import (
    PASSWORD_MAX_LENGTH,
    CreatedTokenResource,
    CreateTokenRequest,
    record_failed_sign_in,
)
from infrahub_sync.service.auth import StoreAccountResolver
from infrahub_sync.service.service import ServiceAPIError
from infrahub_sync.service.settings import ADMIN_ACCOUNT_PASSWORD_ENV, ADMIN_ACCOUNT_TOKEN_ENV
from tests.service.account_api import (
    ADMIN_PASSWORD,
    ADMIN_TOKEN,
    IDLE_SECONDS,
    NEW_PASSWORD,
    PASSWORD,
    SECOND,
    T0,
    WRONG_PASSWORD,
    AccountApi,
    bearer,
    build_account_api,
    text_hash,
)

if TYPE_CHECKING:
    from pathlib import Path

    from httpx import Response

_HOUR = timedelta(hours=1)
_CHANGE = {"current_password": PASSWORD, "new_password": NEW_PASSWORD}


@pytest.fixture(name="api")
def _api_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AccountApi:
    return build_account_api(tmp_path, monkeypatch)


@pytest.fixture(name="alice")
def _alice_fixture(api: AccountApi) -> dict[str, str]:
    """The header of a session of ``alice``."""
    return api.session_headers("alice")


def _error(response: Response, status: int, code: str) -> dict[str, Any]:
    assert response.status_code == status, response.text
    error = response.json()["error"]
    assert error["code"] == code
    return error


def _create(api: AccountApi, headers: dict[str, str], **body: object) -> Response:
    """Create a token. The answer holds the value, so only its other fields are checked for secrets."""
    response = api.client.post("/me/tokens", headers=headers, json=body)
    assert "acct_" not in response.text
    assert "scrypt$" not in response.text
    return response


def test_create_returns_the_value_once_and_the_value_resolves_to_the_account(
    api: AccountApi, alice: dict[str, str]
) -> None:
    response = _create(api, alice, name="ci")

    assert response.status_code == 201
    body = response.json()
    assert set(body) == {"token_id", "name", "created_at", "expires_at", "last_used_at", "token"}
    assert (body["name"], body["expires_at"], body["last_used_at"]) == ("ci", None, None)
    assert body["token"].startswith(credentials.API_TOKEN_PREFIX)
    assert credentials.digest(body["token"]) not in response.text
    me = api.call("GET", "/me", bearer(body["token"]))
    assert me.status_code == 200
    assert me.json()["actor"] == "alice"
    assert body["token"] not in api.stored_text()


def test_list_never_shows_a_value_or_a_digest(api: AccountApi, alice: dict[str, str]) -> None:
    value = _create(api, alice, name="first").json()["token"]
    # The list is by creation time: the second token is created one second later.
    api.clock.now += SECOND
    expires_at = (T0 + _HOUR).isoformat()
    assert _create(api, alice, name="second", expires_at=expires_at).status_code == 201

    response = api.call("GET", "/me/tokens", alice)

    assert response.status_code == 200
    tokens = response.json()
    assert [token["name"] for token in tokens] == ["first", "second"]
    assert all(set(token) == {"token_id", "name", "created_at", "expires_at", "last_used_at"} for token in tokens)
    assert tokens[1]["expires_at"] == "2026-11-18T10:00:00Z"
    assert value not in response.text
    assert credentials.digest(value) not in response.text


def test_list_shows_the_tokens_of_the_caller_only(api: AccountApi, alice: dict[str, str]) -> None:
    assert _create(api, alice, name="mine").status_code == 201

    assert api.call("GET", "/me/tokens", api.session_headers("root")).json() == []


def test_the_printed_form_of_a_created_token_holds_no_value() -> None:
    created = CreatedTokenResource(token_id="t-1", name="ci", created_at=T0, token="ifs_pat_value")  # noqa: S106

    assert repr(created) == str(created) == "CreatedTokenResource(name='ci')"


def test_create_refuses_a_name_the_account_already_uses(api: AccountApi, alice: dict[str, str]) -> None:
    assert _create(api, alice, name="ci").status_code == 201

    _error(_create(api, alice, name="ci"), 409, "token-name-taken")

    assert _create(api, api.session_headers("root"), name="ci").status_code == 201
    assert len(api.call("GET", "/me/tokens", alice).json()) == 1


@pytest.mark.parametrize("offset", [timedelta(0), -SECOND, -_HOUR], ids=["now", "one-second-ago", "one-hour-ago"])
def test_create_refuses_an_expiry_that_is_not_in_the_future(
    api: AccountApi, alice: dict[str, str], offset: timedelta
) -> None:
    _error(_create(api, alice, name="ci", expires_at=(T0 + offset).isoformat()), 422, "expiry-in-the-past")

    assert api.call("GET", "/me/tokens", alice).json() == []


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({}, id="no-name"),
        pytest.param({"name": ""}, id="empty-name"),
        pytest.param({"name": "n" * 65}, id="65-characters"),
        pytest.param({"name": " ci "}, id="name-with-spaces-around"),
        pytest.param({"name": "c\x00i"}, id="name-with-nul"),
        pytest.param({"name": "ci", "expires_at": "2026-11-19T09:00:00"}, id="expiry-without-a-time-zone"),
        pytest.param({"name": "ci", "expires_at": "tomorrow"}, id="expiry-that-is-not-a-time"),
        pytest.param({"name": "ci", "token": "chosen-by-the-caller"}, id="a-value-chosen-by-the-caller"),
    ],
)
def test_create_refuses_a_request_that_does_not_match_the_schema(
    api: AccountApi, alice: dict[str, str], body: dict[str, Any]
) -> None:
    _error(_create(api, alice, **body), 422, "request-invalid")


def test_create_refuses_a_token_pasted_as_a_name(api: AccountApi, alice: dict[str, str]) -> None:
    pasted = credentials.new_api_token()

    response = _create(api, alice, name=pasted)

    _error(response, 422, "secret-parameter-refused")
    assert pasted not in response.text
    assert pasted not in api.stored_text()
    assert api.call("GET", "/me/tokens", alice).json() == []


def test_a_deleted_token_is_refused_as_unauthenticated(api: AccountApi, alice: dict[str, str]) -> None:
    created = _create(api, alice, name="ci").json()
    kept = _create(api, alice, name="kept").json()

    response = api.call("DELETE", f"/me/tokens/{created['token_id']}", alice)

    assert response.status_code == 204
    assert response.content == b""
    _error(api.call("GET", "/me", bearer(created["token"])), 401, "unauthenticated")
    assert api.call("GET", "/me", bearer(kept["token"])).status_code == 200
    assert [token["name"] for token in api.call("GET", "/me/tokens", alice).json()] == ["kept"]


def test_an_expired_token_is_refused_with_its_own_code(api: AccountApi, alice: dict[str, str]) -> None:
    created = _create(api, alice, name="ci", expires_at=(T0 + _HOUR).isoformat()).json()
    assert api.call("GET", "/me", bearer(created["token"])).status_code == 200

    api.clock.now = T0 + _HOUR + SECOND

    _error(api.call("GET", "/me", bearer(created["token"])), 401, "token-expired")


def test_delete_refuses_a_token_that_is_not_the_callers(api: AccountApi, alice: dict[str, str]) -> None:
    root = api.session_headers("root")
    created = _create(api, root, name="ci").json()

    _error(api.call("DELETE", f"/me/tokens/{created['token_id']}", alice), 404, "token-not-found")
    _error(api.call("DELETE", "/me/tokens/no-such-token", alice), 404, "token-not-found")

    assert api.call("GET", "/me", bearer(created["token"])).status_code == 200


def test_a_token_deletes_itself(api: AccountApi) -> None:
    session = api.session_headers("alice")
    created = _create(api, session, name="ci").json()
    token = bearer(created["token"])

    assert api.call("DELETE", f"/me/tokens/{created['token_id']}", token).status_code == 204

    _error(api.call("GET", "/me", token), 401, "unauthenticated")


def test_admin_lists_the_token_of_the_environment(api: AccountApi) -> None:
    response = api.call("GET", "/me/tokens", bearer(ADMIN_TOKEN))

    assert response.status_code == 200
    assert [token["name"] for token in response.json()] == ["environment"]
    assert ADMIN_TOKEN not in response.text


@pytest.mark.parametrize(
    "change",
    [
        ("PUT", "/me/password", {"current_password": ADMIN_PASSWORD, "new_password": NEW_PASSWORD}, "PASSWORD"),
        ("POST", "/me/tokens", {"name": "ci"}, "TOKEN"),
        ("DELETE", "/me/tokens/{token_id}", None, "TOKEN"),
    ],
    ids=["change-password", "create-token", "delete-token"],
)
@pytest.mark.parametrize("credential", ["token", "session"])
def test_every_change_of_admin_to_itself_is_refused_and_names_the_variable(
    api: AccountApi, change: tuple[str, str, dict[str, Any] | None, str], credential: str
) -> None:
    method, path, body, variable = change
    headers = bearer(ADMIN_TOKEN) if credential == "token" else api.session_headers("admin", ADMIN_PASSWORD)
    token_id = api.call("GET", "/me/tokens", headers).json()[0]["token_id"]
    expected = {"PASSWORD": ADMIN_ACCOUNT_PASSWORD_ENV, "TOKEN": ADMIN_ACCOUNT_TOKEN_ENV}[variable]

    response = api.call(method, path.format(token_id=token_id), headers, json=body)

    message = _error(response, 409, "environment-managed-account")["message"]
    assert expected in message
    assert (ADMIN_ACCOUNT_PASSWORD_ENV in message) != (ADMIN_ACCOUNT_TOKEN_ENV in message)
    assert api.call("GET", "/me", bearer(ADMIN_TOKEN)).status_code == 200
    assert len(api.call("GET", "/me/tokens", bearer(ADMIN_TOKEN)).json()) == 1
    assert api.sign_in("admin", ADMIN_PASSWORD).status_code == 200


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("PUT", "/me/password", _CHANGE),
        ("GET", "/me/tokens", None),
        ("POST", "/me/tokens", {"name": "ci"}),
        ("DELETE", "/me/tokens/any", None),
    ],
)
def test_every_route_answers_unauthenticated_without_a_token(
    api: AccountApi, method: str, path: str, body: dict[str, Any] | None
) -> None:
    _error(api.call(method, path, json=body), 401, "unauthenticated")


def test_a_password_change_ends_the_other_sessions_and_keeps_the_tokens(api: AccountApi, alice: dict[str, str]) -> None:
    other = api.session_headers("alice")
    token = api.token_headers("alice")

    response = api.call("PUT", "/me/password", alice, json=_CHANGE)

    assert response.status_code == 204
    assert response.content == b""
    assert api.call("GET", "/me", alice).status_code == 200
    _error(api.call("GET", "/me", other), 401, "unauthenticated")
    assert api.call("GET", "/me", token).status_code == 200
    assert api.sign_in("alice", PASSWORD).status_code == 401
    assert api.sign_in("alice", NEW_PASSWORD).status_code == 200
    assert api.gate.hashes == 1


def test_a_password_change_with_an_api_token_ends_every_session(api: AccountApi, alice: dict[str, str]) -> None:
    other = api.session_headers("alice")
    token = api.token_headers("alice")

    assert api.call("PUT", "/me/password", token, json=_CHANGE).status_code == 204

    _error(api.call("GET", "/me", alice), 401, "unauthenticated")
    _error(api.call("GET", "/me", other), 401, "unauthenticated")
    assert api.call("GET", "/me", token).status_code == 200


def test_a_password_change_leaves_the_sessions_of_another_account(api: AccountApi, alice: dict[str, str]) -> None:
    root = api.session_headers("root")

    assert api.call("PUT", "/me/password", alice, json=_CHANGE).status_code == 204

    assert api.call("GET", "/me", root).status_code == 200
    assert api.sign_in("root", PASSWORD).status_code == 200


def test_a_password_change_checks_and_stores_a_real_hash(api: AccountApi, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(credentials, "PASSWORD_VERIFICATION_GATE", credentials.VerificationGate())
    account = api.store.create_account(name="carol", password_hash=credentials.hash_password(PASSWORD), now=T0)
    carol = api.token_headers("carol")

    _error(
        api.call("PUT", "/me/password", carol, json={"current_password": WRONG_PASSWORD, "new_password": NEW_PASSWORD}),
        403,
        "current-password-refused",
    )
    assert api.call("PUT", "/me/password", carol, json=_CHANGE).status_code == 204

    stored = api.store.lookup_account(account.account_id)
    assert stored is not None
    assert stored.password_hash is not None
    assert credentials.verify_password(NEW_PASSWORD, stored.password_hash)
    assert not credentials.verify_password(PASSWORD, stored.password_hash)


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({"new_password": NEW_PASSWORD}, id="no-current-password"),
        pytest.param({"current_password": PASSWORD}, id="no-new-password"),
    ],
)
def test_a_password_change_needs_both_passwords(api: AccountApi, alice: dict[str, str], body: dict[str, str]) -> None:
    response = api.call("PUT", "/me/password", alice, json=body)

    _error(response, 422, "request-invalid")
    assert PASSWORD not in response.text
    assert NEW_PASSWORD not in response.text
    assert api.sign_in("alice", PASSWORD).status_code == 200


@pytest.mark.parametrize(
    ("new_password", "code"),
    [("short", "password-too-short"), ("p" * (PASSWORD_MAX_LENGTH + 1), "password-too-long")],
    ids=["too-short", "too-long"],
)
def test_a_password_change_refuses_a_new_password_outside_the_length_rule(
    api: AccountApi, alice: dict[str, str], new_password: str, code: str
) -> None:
    other = api.session_headers("alice")

    response = api.call("PUT", "/me/password", alice, json={"current_password": PASSWORD, "new_password": new_password})

    _error(response, 422, code)
    assert api.call("GET", "/me", other).status_code == 200
    assert api.sign_in("alice", PASSWORD).status_code == 200


def test_a_wrong_current_password_is_refused_and_counts_as_a_failed_attempt(
    api: AccountApi, alice: dict[str, str]
) -> None:
    other = api.session_headers("alice")
    wrong = {"current_password": WRONG_PASSWORD, "new_password": NEW_PASSWORD}

    response = api.call("PUT", "/me/password", alice, json=wrong)

    _error(response, 403, "current-password-refused")
    attempt = api.store.read_sign_in_attempt("alice")
    assert attempt is not None
    assert attempt.failed_count == 1
    assert api.call("GET", "/me", other).status_code == 200
    account = api.store.lookup_account_by_name("alice")
    assert account is not None
    assert account.password_hash == text_hash(PASSWORD)
    assert WRONG_PASSWORD not in api.stored_text()
    assert NEW_PASSWORD not in api.stored_text()


def test_the_fifth_wrong_current_password_refuses_the_right_one_and_the_sign_in(
    api: AccountApi, alice: dict[str, str]
) -> None:
    wrong = {"current_password": WRONG_PASSWORD, "new_password": NEW_PASSWORD}
    for _ in range(SIGN_IN_FAILURE_LIMIT):
        _error(api.call("PUT", "/me/password", alice, json=wrong), 403, "current-password-refused")
        api.clock.now += SECOND
    checks = api.gate.checks

    refused = api.call("PUT", "/me/password", alice, json=_CHANGE)

    _error(refused, 403, "current-password-refused")
    assert api.gate.checks == checks
    assert api.sign_in("alice", PASSWORD).status_code == 401
    assert api.call("GET", "/me", alice).status_code == 200
    attempt = api.store.read_sign_in_attempt("alice")
    assert attempt is not None
    assert attempt.failed_count == SIGN_IN_FAILURE_LIMIT


def test_a_refused_name_gets_the_refusal_before_the_rule_of_the_new_password(
    api: AccountApi, alice: dict[str, str]
) -> None:
    for _ in range(SIGN_IN_FAILURE_LIMIT):
        assert api.sign_in("alice", WRONG_PASSWORD).status_code == 401
        api.clock.now += SECOND

    response = api.call("PUT", "/me/password", alice, json={"current_password": PASSWORD, "new_password": "short"})

    _error(response, 403, "current-password-refused")


def test_a_password_change_clears_the_failed_attempts(api: AccountApi, alice: dict[str, str]) -> None:
    wrong = {"current_password": WRONG_PASSWORD, "new_password": NEW_PASSWORD}
    _error(api.call("PUT", "/me/password", alice, json=wrong), 403, "current-password-refused")

    assert api.call("PUT", "/me/password", alice, json=_CHANGE).status_code == 204

    assert api.store.read_sign_in_attempt("alice") is None


@pytest.mark.parametrize("current", ["", "p" * (PASSWORD_MAX_LENGTH + 1)], ids=["empty", "too-long"])
def test_a_current_password_that_no_account_has_is_refused_without_a_check(
    api: AccountApi, alice: dict[str, str], current: str
) -> None:
    checks = api.gate.checks

    response = api.call("PUT", "/me/password", alice, json={"current_password": current, "new_password": NEW_PASSWORD})

    _error(response, 403, "current-password-refused")
    assert api.gate.checks == checks
    assert api.store.read_sign_in_attempt("alice") is None


def test_a_password_change_whose_name_is_refused_while_it_waits_gets_the_refusal_with_no_check(
    api: AccountApi, alice: dict[str, str]
) -> None:
    for _ in range(SIGN_IN_FAILURE_LIMIT):
        record_failed_sign_in(api.service, "alice")
    refused_before = api.call("PUT", "/me/password", alice, json=_CHANGE)
    api.store.clear_sign_in_attempts("alice")
    checks = api.gate.checks

    def refuse() -> None:
        # Other requests fail 5 times for the name while this one waits for a place of the gate.
        for _ in range(SIGN_IN_FAILURE_LIMIT):
            record_failed_sign_in(api.service, "alice")

    api.gate.while_waiting = refuse

    response = api.call("PUT", "/me/password", alice, json=_CHANGE)

    _error(response, 403, "current-password-refused")
    assert (response.status_code, response.content) == (refused_before.status_code, refused_before.content)
    assert api.gate.checks == checks
    assert api.gate.hashes == 0
    account = api.store.lookup_account_by_name("alice")
    assert account is not None
    assert account.password_hash == text_hash(PASSWORD)
    attempt = api.store.read_sign_in_attempt("alice")
    assert attempt is not None
    assert attempt.failed_count == SIGN_IN_FAILURE_LIMIT
    assert attempt.refused_until == T0 + SIGN_IN_REFUSAL_PERIOD


def test_a_token_created_while_the_account_is_disabled_does_not_exist_when_it_is_enabled(
    api: AccountApi, alice: dict[str, str]
) -> None:
    session = alice["Authorization"].removeprefix("Bearer ")
    resolved = StoreAccountResolver(api.store, idle_seconds=IDLE_SECONDS, clock=api.clock).resolve(session)
    assert resolved.principal is not None
    account = api.store.lookup_account_by_name("alice")
    assert account is not None
    # The request was resolved; the account is disabled before its token is created.
    api.store.disable_account(account.account_id, now=T0)

    with pytest.raises(ServiceAPIError) as refused:
        account_actions.create_own_token(api.service, resolved.principal, CreateTokenRequest(name="ci"))
    api.store.enable_account(account.account_id, now=T0)

    assert (refused.value.status, refused.value.code) == (401, "unauthenticated")
    assert api.store.list_account_tokens(account.account_id) == ()
    assert [event for event in api.events() if event.operation == "create-token"] == []


_SURROGATE = '"correct-horse-\\ud800-battery"'


def _change_password_bytes(api: AccountApi, headers: dict[str, str], body: str) -> Response:
    """Send a password change whose body holds a lone surrogate, as bytes the client does not encode."""
    return api.call("PUT", "/me/password", {**headers, "Content-Type": "application/json"}, content=body.encode())


def test_a_new_password_that_utf_8_cannot_encode_is_refused_as_invalid(api: AccountApi, alice: dict[str, str]) -> None:
    checks, hashes = api.gate.checks, api.gate.hashes

    response = _change_password_bytes(api, alice, f'{{"current_password": "{PASSWORD}", "new_password": {_SURROGATE}}}')

    _error(response, 422, "password-invalid")
    assert "correct-horse" not in response.text
    assert (api.gate.checks, api.gate.hashes) == (checks, hashes)
    assert api.store.read_sign_in_attempt("alice") is None
    assert api.sign_in("alice", PASSWORD).status_code == 200


def test_a_current_password_that_utf_8_cannot_encode_is_refused_without_a_check(
    api: AccountApi, alice: dict[str, str]
) -> None:
    checks = api.gate.checks

    response = _change_password_bytes(
        api, alice, f'{{"current_password": {_SURROGATE}, "new_password": "{NEW_PASSWORD}"}}'
    )

    _error(response, 403, "current-password-refused")
    assert api.gate.checks == checks
    assert api.store.read_sign_in_attempt("alice") is None
