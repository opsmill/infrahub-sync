"""The routes of an administrator on the accounts: create, list, read, disable, enable, set a password, unlock.

The cases compare texts instead of hashing, except the one that hashes a real password through
the gate of the process.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("prefect")

from infrahub_sync.product_store.accounts import SIGN_IN_FAILURE_LIMIT, SIGN_IN_REFUSAL_PERIOD
from infrahub_sync.service import account_actions, credentials
from infrahub_sync.service.account_actions import PASSWORD_MAX_LENGTH
from infrahub_sync.service.settings import ADMIN_ACCOUNT_PASSWORD_ENV, PASSWORD_MIN_LENGTH
from tests.service.account_api import (
    ADMIN_PASSWORD,
    ADMIN_TOKEN,
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

# Every route of an administrator, with a body that its schema accepts.
_ADMINISTRATOR_ROUTES: list[tuple[str, str, dict[str, Any] | None]] = [
    ("GET", "/accounts", None),
    ("POST", "/accounts", {"name": "carol", "password": PASSWORD}),
    ("GET", "/accounts/alice", None),
    ("PATCH", "/accounts/alice", {"disabled": True}),
    ("PUT", "/accounts/alice/password", {"new_password": NEW_PASSWORD}),
    ("POST", "/accounts/alice/unlock", None),
]
_ROUTE_IDS = [f"{method} {path}" for method, path, _body in _ADMINISTRATOR_ROUTES]


@pytest.fixture(name="api")
def _api_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AccountApi:
    return build_account_api(tmp_path, monkeypatch)


@pytest.fixture(name="root")
def _root_fixture(api: AccountApi) -> dict[str, str]:
    """The header of ``root``, an administrator that the environment does not manage."""
    return api.token_headers("root")


def _error(response: Response, status: int, code: str) -> dict[str, Any]:
    assert response.status_code == status, response.text
    error = response.json()["error"]
    assert error["code"] == code
    return error


def _fail_sign_ins(api: AccountApi, name: str, times: int = SIGN_IN_FAILURE_LIMIT) -> None:
    for _ in range(times):
        assert api.sign_in(name, WRONG_PASSWORD).status_code == 401
        api.clock.now += SECOND


def test_create_returns_the_account_without_its_identifier_or_its_password(
    api: AccountApi, root: dict[str, str]
) -> None:
    response = api.call("POST", "/accounts", root, json={"name": "Carol", "password": PASSWORD})

    assert response.status_code == 201
    assert response.json() == {
        "name": "Carol",
        "administrator": False,
        "disabled": False,
        "environment_managed": False,
        "created_at": "2026-11-18T09:00:00Z",
        "sign_in_refused_until": None,
    }
    assert PASSWORD not in response.text
    assert api.gate.hashes == 1
    assert api.sign_in("carol").status_code == 200


def test_create_an_administrator_gives_the_account_the_administrator_routes(
    api: AccountApi, root: dict[str, str]
) -> None:
    created = api.call("POST", "/accounts", root, json={"name": "dave", "password": PASSWORD, "administrator": True})

    assert created.status_code == 201
    assert created.json()["administrator"] is True
    assert api.call("GET", "/accounts", api.session_headers("dave")).status_code == 200


def test_create_hashes_a_real_password_through_the_gate_of_the_process(
    api: AccountApi, root: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(credentials, "PASSWORD_VERIFICATION_GATE", credentials.VerificationGate())

    assert api.call("POST", "/accounts", root, json={"name": "erin", "password": PASSWORD}).status_code == 201

    stored = api.store.lookup_account_by_name("erin")
    assert stored is not None
    assert stored.password_hash is not None
    assert stored.password_hash.startswith("scrypt$")
    assert api.sign_in("erin").status_code == 200
    assert api.sign_in("erin", WRONG_PASSWORD).status_code == 401


def test_create_answers_busy_when_the_gate_has_no_free_place(
    api: AccountApi, root: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    gate = credentials.VerificationGate(limit=1, wait_seconds=0.01)
    monkeypatch.setattr(credentials, "PASSWORD_VERIFICATION_GATE", gate)

    with gate.place():
        response = api.call("POST", "/accounts", root, json={"name": "frank", "password": PASSWORD})

    _error(response, 503, "sign-in-busy")
    assert api.store.lookup_account_by_name("frank") is None


@pytest.mark.parametrize("name", ["alice", "ALICE", "Alice"])
def test_create_refuses_a_name_that_is_taken_in_any_letter_case(
    api: AccountApi, root: dict[str, str], name: str
) -> None:
    response = api.call("POST", "/accounts", root, json={"name": name, "password": PASSWORD})

    _error(response, 409, "account-name-taken")
    assert api.gate.hashes == 0


def test_create_refuses_the_name_of_a_disabled_account(api: AccountApi, root: dict[str, str]) -> None:
    assert api.call("PATCH", "/accounts/alice", root, json={"disabled": True}).status_code == 200

    _error(api.call("POST", "/accounts", root, json={"name": "alice", "password": PASSWORD}), 409, "account-name-taken")


@pytest.mark.parametrize(
    "name",
    [
        pytest.param("", id="empty"),
        pytest.param("a" * 65, id="65-characters"),
        pytest.param("two words", id="space"),
        pytest.param(".dot-first", id="starts-with-a-dot"),
        pytest.param("slash/name", id="slash"),
        pytest.param("nul\x00name", id="nul"),
        pytest.param("admin", id="reserved-admin"),
        pytest.param("Admin", id="reserved-admin-in-another-case"),
        pytest.param("unknown", id="reserved-unknown"),
        pytest.param("UNKNOWN", id="reserved-unknown-in-upper-case"),
        pytest.param("unauthenticated", id="reserved-unauthenticated"),
        pytest.param("acct_0123", id="starts-like-an-identifier"),
        pytest.param("ACCT_other", id="starts-like-an-identifier-in-upper-case"),
        pytest.param("ifs_pat_pasted-token", id="holds-a-token-prefix"),
    ],
)
def test_create_refuses_an_invalid_or_reserved_name(api: AccountApi, root: dict[str, str], name: str) -> None:
    response = api.call("POST", "/accounts", root, json={"name": name, "password": PASSWORD})

    error = _error(response, 422, "account-name-invalid")
    assert not name or name not in error["message"]
    assert api.gate.hashes == 0
    assert [account.name for account in api.store.list_accounts()] == ["admin", "alice", "root"]


@pytest.mark.parametrize("name", ["a", "z" * 64, "carol@example.com", "c.d_e-f", "9lives"])
def test_create_accepts_a_name_that_matches_the_rule(api: AccountApi, root: dict[str, str], name: str) -> None:
    assert api.call("POST", "/accounts", root, json={"name": name, "password": PASSWORD}).status_code == 201


@pytest.mark.parametrize(
    ("password", "code"),
    [
        pytest.param("p" * (PASSWORD_MIN_LENGTH - 1), "password-too-short", id="11-characters"),
        pytest.param("", "password-too-short", id="empty"),
        pytest.param("p" * (PASSWORD_MAX_LENGTH + 1), "password-too-long", id="1025-characters"),
    ],
)
def test_create_refuses_a_password_outside_the_length_rule(
    api: AccountApi, root: dict[str, str], password: str, code: str
) -> None:
    response = api.call("POST", "/accounts", root, json={"name": "carol", "password": password})

    _error(response, 422, code)
    assert api.gate.hashes == 0
    assert api.store.lookup_account_by_name("carol") is None


@pytest.mark.parametrize("length", [PASSWORD_MIN_LENGTH, PASSWORD_MAX_LENGTH])
def test_create_accepts_a_password_at_each_end_of_the_length_rule(
    api: AccountApi, root: dict[str, str], length: int
) -> None:
    assert api.call("POST", "/accounts", root, json={"name": "carol", "password": "p" * length}).status_code == 201


def test_a_request_that_does_not_match_the_schema_does_not_show_the_password(
    api: AccountApi, root: dict[str, str]
) -> None:
    response = api.call("POST", "/accounts", root, json={"name": "carol", "password": PASSWORD, "level": "high"})

    _error(response, 422, "request-invalid")
    assert PASSWORD not in response.text


def test_list_returns_the_accounts_by_name_and_by_state(api: AccountApi, root: dict[str, str]) -> None:
    api.add("Bob")
    assert api.call("PATCH", "/accounts/alice", root, json={"disabled": True}).status_code == 200

    def names(query: str) -> list[str]:
        response = api.call("GET", f"/accounts{query}", root)
        assert response.status_code == 200
        return [account["name"] for account in response.json()]

    assert names("") == ["admin", "alice", "Bob", "root"]
    assert names("?state=all") == ["admin", "alice", "Bob", "root"]
    assert names("?state=active") == ["admin", "Bob", "root"]
    assert names("?state=disabled") == ["alice"]
    _error(api.call("GET", "/accounts?state=deleted", root), 422, "request-invalid")


def test_list_and_read_show_a_refusal_that_is_in_force(api: AccountApi, root: dict[str, str]) -> None:
    _fail_sign_ins(api, "alice")
    refused_until = (api.clock.now - SECOND + SIGN_IN_REFUSAL_PERIOD).isoformat().replace("+00:00", "Z")

    listed = {account["name"]: account for account in api.call("GET", "/accounts", root).json()}
    read = api.call("GET", "/accounts/ALICE", root)

    assert listed["alice"]["sign_in_refused_until"] == refused_until
    assert listed["root"]["sign_in_refused_until"] is None
    assert read.status_code == 200
    assert read.json()["name"] == "alice"
    assert read.json()["sign_in_refused_until"] == refused_until


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", "/accounts/nobody", None),
        ("GET", "/accounts/two%20words", None),
        ("PATCH", "/accounts/nobody", {"disabled": True}),
        ("PUT", "/accounts/nobody/password", {"new_password": NEW_PASSWORD}),
        ("POST", "/accounts/nobody/unlock", None),
    ],
)
def test_an_unknown_name_is_not_found(
    api: AccountApi, root: dict[str, str], method: str, path: str, body: dict[str, Any] | None
) -> None:
    _error(api.call(method, path, root, json=body), 404, "account-not-found")


def test_set_password_ends_every_session_keeps_the_tokens_and_ends_a_refusal(
    api: AccountApi, root: dict[str, str]
) -> None:
    first, second = api.session_headers("alice"), api.session_headers("alice")
    token = api.token_headers("alice")
    _fail_sign_ins(api, "alice")

    response = api.call("PUT", "/accounts/alice/password", root, json={"new_password": NEW_PASSWORD})

    assert response.status_code == 204
    assert response.content == b""
    assert api.call("GET", "/me", first).status_code == 401
    assert api.call("GET", "/me", second).status_code == 401
    assert api.call("GET", "/me", token).status_code == 200
    assert api.sign_in("alice", PASSWORD).status_code == 401
    assert api.sign_in("alice", NEW_PASSWORD).status_code == 200


@pytest.mark.parametrize(
    ("password", "code"),
    [("short", "password-too-short"), ("p" * (PASSWORD_MAX_LENGTH + 1), "password-too-long")],
    ids=["too-short", "too-long"],
)
def test_set_password_refuses_a_password_outside_the_length_rule(
    api: AccountApi, root: dict[str, str], password: str, code: str
) -> None:
    session = api.session_headers("alice")

    _error(api.call("PUT", "/accounts/alice/password", root, json={"new_password": password}), 422, code)

    assert api.call("GET", "/me", session).status_code == 200
    assert api.sign_in("alice").status_code == 200


def test_disable_refuses_the_password_the_session_and_the_token_and_keeps_the_name_on_a_past_run(
    api: AccountApi, root: dict[str, str]
) -> None:
    session, token = api.session_headers("alice"), api.token_headers("alice")
    run = api.create_run(token)
    assert run.status_code == 202, run.text
    run_id = run.json()["run"]["run_id"]
    wrong_password = api.sign_in("alice", WRONG_PASSWORD)

    response = api.call("PATCH", "/accounts/alice", root, json={"disabled": True})

    assert response.status_code == 200
    assert response.json()["disabled"] is True
    refused = api.sign_in("alice")
    assert (refused.status_code, refused.content) == (wrong_password.status_code, wrong_password.content)
    _error(api.call("GET", "/me", session), 401, "unauthenticated")
    _error(api.call("GET", "/me", token), 401, "unauthenticated")
    assert api.store.list_account_tokens(api.identifier("alice")) == ()
    assert api.store.list_account_sessions(api.identifier("alice")) == ()
    past_run = api.client.get(f"/runs/{run_id}", headers=root)
    assert past_run.status_code == 200
    assert past_run.json()["run"]["actor"] == "alice"
    assert "acct_" not in past_run.text


def test_enable_restores_the_password_and_no_token(api: AccountApi, root: dict[str, str]) -> None:
    session, token = api.session_headers("alice"), api.token_headers("alice")
    assert api.call("PATCH", "/accounts/alice", root, json={"disabled": True}).status_code == 200

    response = api.call("PATCH", "/accounts/alice", root, json={"disabled": False})

    assert response.status_code == 200
    assert response.json()["disabled"] is False
    assert api.sign_in("alice").status_code == 200
    _error(api.call("GET", "/me", token), 401, "unauthenticated")
    _error(api.call("GET", "/me", session), 401, "unauthenticated")
    assert api.call("GET", "/me/tokens", api.session_headers("alice")).json() == []


@pytest.mark.parametrize("disabled", [True, False])
def test_a_request_for_the_state_an_account_has_changes_nothing_and_writes_no_event(
    api: AccountApi, root: dict[str, str], *, disabled: bool
) -> None:
    first = api.call("PATCH", "/accounts/alice", root, json={"disabled": disabled})
    events = len(api.account_events())

    again = api.call("PATCH", "/accounts/alice", root, json={"disabled": disabled})

    assert again.status_code == 200
    assert again.json() == first.json()
    assert len(api.account_events()) == events


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({}, id="no-change"),
        pytest.param({"administrator": True}, id="the-level-is-not-a-field-yet"),
        pytest.param({"disabled": True, "administrator": True}, id="the-level-with-a-state"),
        pytest.param({"disabled": "yes"}, id="a-state-that-is-not-a-boolean"),
    ],
)
def test_patch_accepts_the_state_only(api: AccountApi, root: dict[str, str], body: dict[str, Any]) -> None:
    _error(api.call("PATCH", "/accounts/alice", root, json=body), 422, "request-invalid")

    account = api.store.lookup_account_by_name("alice")
    assert account is not None
    assert (account.disabled, account.administrator) == (False, False)


def test_unlock_ends_a_refusal_after_repeated_failures(api: AccountApi, root: dict[str, str]) -> None:
    """Scenario 13 of the spec: the refusal, the token that still works, the unlock, the sign-in."""
    token = api.token_headers("alice")
    _fail_sign_ins(api, "alice")

    assert api.sign_in("alice").status_code == 401
    assert api.call("GET", "/me", token).status_code == 200
    unlock = api.call("POST", "/accounts/alice/unlock", root)

    assert unlock.status_code == 204
    assert api.store.read_sign_in_attempt("alice") is None
    assert api.sign_in("alice").status_code == 200


def test_unlock_answers_the_same_when_no_refusal_is_in_force(api: AccountApi, root: dict[str, str]) -> None:
    assert api.call("POST", "/accounts/alice/unlock", root, json={"reason": "asked by phone"}).status_code == 204
    assert api.call("POST", "/accounts/alice/unlock", root).status_code == 204


def test_an_administrator_ends_the_refusal_of_admin(api: AccountApi, root: dict[str, str]) -> None:
    _fail_sign_ins(api, "admin")
    assert api.sign_in("admin", ADMIN_PASSWORD).status_code == 401

    assert api.call("POST", "/accounts/admin/unlock", root).status_code == 204

    assert api.sign_in("admin", ADMIN_PASSWORD).status_code == 200


@pytest.mark.parametrize(("method", "path", "body"), _ADMINISTRATOR_ROUTES, ids=_ROUTE_IDS)
@pytest.mark.parametrize("valid_body", [True, False], ids=["valid-body", "invalid-body"])
def test_every_route_answers_forbidden_to_an_account_that_is_not_an_administrator(
    api: AccountApi, method: str, path: str, body: dict[str, Any] | None, *, valid_body: bool
) -> None:
    """The refusal comes before every other check: a body that matches no schema gets it too."""
    alice = api.token_headers("alice")
    sent = body if valid_body else {"unexpected": ["field"]}

    response = api.call(method, path, alice, json=sent)

    assert _error(response, 403, "forbidden")["message"] == account_actions.ADMINISTRATOR_REQUIRED_MESSAGE
    (refusal,) = api.refusals("refused-authorization")
    assert refusal.event_type == "infrahub_sync.account.refused"
    assert refusal.actor == api.identifier("alice")
    assert refusal.reason is None
    account = api.store.lookup_account_by_name("alice")
    assert account is not None
    assert (account.disabled, account.password_hash) == (False, text_hash(PASSWORD))
    assert api.store.lookup_account_by_name("carol") is None


@pytest.mark.parametrize(("method", "path", "body"), _ADMINISTRATOR_ROUTES, ids=_ROUTE_IDS)
def test_every_route_answers_unauthenticated_without_a_token(
    api: AccountApi, method: str, path: str, body: dict[str, Any] | None
) -> None:
    _error(api.call(method, path, json=body), 401, "unauthenticated")


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("PATCH", "/accounts/admin", {"disabled": True}),
        ("PATCH", "/accounts/Admin", {"disabled": False}),
        ("PUT", "/accounts/admin/password", {"new_password": NEW_PASSWORD}),
    ],
    ids=["disable", "enable", "set-password"],
)
@pytest.mark.parametrize("caller", ["root", "admin"])
def test_every_change_to_admin_is_refused_and_names_the_variable(
    api: AccountApi, method: str, path: str, body: dict[str, Any], caller: str
) -> None:
    headers = bearer(ADMIN_TOKEN) if caller == "admin" else api.token_headers("root")
    events = len(api.account_events())

    response = api.call(method, path, headers, json=body)

    assert ADMIN_ACCOUNT_PASSWORD_ENV in _error(response, 409, "environment-managed-account")["message"]
    admin = api.store.lookup_account_by_name("admin")
    assert admin is not None
    assert (admin.disabled, admin.password_hash) == (False, text_hash(ADMIN_PASSWORD))
    assert api.gate.hashes == 0
    assert len(api.account_events()) == events
    assert api.call("GET", "/me", bearer(ADMIN_TOKEN)).status_code == 200


def test_admin_is_read_and_listed_as_managed_by_the_environment(api: AccountApi, root: dict[str, str]) -> None:
    read = api.call("GET", "/accounts/admin", root)

    assert read.status_code == 200
    assert read.json() == {
        "name": "admin",
        "administrator": True,
        "disabled": False,
        "environment_managed": True,
        "created_at": T0.isoformat().replace("+00:00", "Z"),
        "sign_in_refused_until": None,
    }
