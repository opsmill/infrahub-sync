"""The routes of an administrator on the accounts.

Create, list, read, disable, enable, set a password, unlock, set the level, delete, and list and
delete the tokens of another account. The cases compare texts instead of hashing, except the one
that hashes a real password through the gate of the process.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, cast

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("prefect")

from infrahub_sync.client.models import CreateRunRequest
from infrahub_sync.product_store import ProductRun, RelatedResource
from infrahub_sync.product_store.accounts import SIGN_IN_FAILURE_LIMIT, SIGN_IN_REFUSAL_PERIOD
from infrahub_sync.service import account_actions, credentials
from infrahub_sync.service.account_actions import PASSWORD_MAX_LENGTH
from infrahub_sync.service.auth import StoreAccountResolver
from infrahub_sync.service.models import PlanOperationResource, PlanResource, PlanSummaryResource
from infrahub_sync.service.service import PLAN_ARTIFACT_ID
from infrahub_sync.service.settings import (
    ADMIN_ACCOUNT_PASSWORD_ENV,
    ADMIN_ACCOUNT_TOKEN_ENV,
    PASSWORD_MIN_LENGTH,
    REQUIRE_APPROVAL_ENV,
)
from tests.configuration.validation_packages import package_data
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

_SOME_TOKEN_ID = "0b5c4e0e-9a51-4f0b-8f0e-3f1f6f6f0a11"  # noqa: S105 - an identifier, not a value.
# Every route of an administrator, with a body that its schema accepts.
_ADMINISTRATOR_ROUTES: list[tuple[str, str, dict[str, Any] | None]] = [
    ("GET", "/accounts", None),
    ("POST", "/accounts", {"name": "carol", "password": PASSWORD}),
    ("GET", "/accounts/alice", None),
    ("PATCH", "/accounts/alice", {"disabled": True}),
    ("PUT", "/accounts/alice/password", {"new_password": NEW_PASSWORD}),
    ("POST", "/accounts/alice/unlock", None),
    ("PATCH", "/accounts/alice", {"administrator": True}),
    ("DELETE", "/accounts/alice", None),
    ("GET", "/accounts/alice/tokens", None),
    ("DELETE", f"/accounts/alice/tokens/{_SOME_TOKEN_ID}", None),
]
# A `PATCH` is named by the field it changes, since two rows share its route.
_ROUTE_IDS = [
    f"{method} {path}" + (f" {next(iter(body))}" if method == "PATCH" and body else "")
    for method, path, body in _ADMINISTRATOR_ROUTES
]


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
        ("PATCH", "/accounts/nobody", {"administrator": True}),
        ("DELETE", "/accounts/nobody", None),
        ("GET", "/accounts/nobody/tokens", None),
        ("DELETE", f"/accounts/nobody/tokens/{_SOME_TOKEN_ID}", None),
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
        pytest.param({"reason": "nothing else"}, id="a-reason-alone"),
        pytest.param({"administrator": "yes"}, id="a-level-that-is-not-a-boolean"),
        pytest.param({"disabled": "yes"}, id="a-state-that-is-not-a-boolean"),
        pytest.param({"disabled": True, "level": "high"}, id="an-unknown-field"),
    ],
)
def test_patch_refuses_a_body_that_names_no_valid_change(
    api: AccountApi, root: dict[str, str], body: dict[str, Any]
) -> None:
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
    assert (account.disabled, account.administrator, account.password_hash) == (False, False, text_hash(PASSWORD))
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


# --- User Story 3: delete an account, set a level, and the tokens of another account ---------

_CHECKSUM = "a" * 64
_REASON = "the person asked for it"


def _planned_run(api: AccountApi, actor: str, run_id: str = "run-us3-001") -> str:
    """Store a planned run of the registered configuration whose actor is this identifier, with a saved plan."""
    version = api.register()
    config_id, registry_version = version["config_id"], version["registry_version"]
    stored = api.store.lookup_configuration_version(config_id, registry_version).value
    assert stored is not None
    api.store.create_run(
        ProductRun(
            run_id=run_id,
            operation="plan",
            configuration_reference=f"{config_id}@{registry_version}",
            config_id=config_id,
            registry_version=registry_version,
            package_checksum=stored.package_checksum,
            actor=actor,
            started_at=T0,
            phase="planned",
        )
    )
    plan = PlanResource(
        run_id=run_id,
        checksum=_CHECKSUM,
        checksum_ok=True,
        verification_notes=(),
        summary=PlanSummaryResource(
            by_action={"create": 1},
            by_kind={"Device": 1},
            total=1,
            delete_operations_computed=True,
            deletes_not_executed=0,
        ),
        operations=(
            PlanOperationResource(
                operation_id="op-001", action="create", kind="Device", identity={"name": "edge-01"}, tier=0
            ),
        ),
    )
    api.store.publish_artifact(
        run_id,
        artifact_id=PLAN_ARTIFACT_ID,
        kind="saved-plan-review",
        media_type="application/json",
        data=plan.model_dump_json().encode(),
    )
    return run_id


def _mutate(api: AccountApi, headers: dict[str, str], run_id: str, action: str, key: str) -> Response:
    """Send one run mutation with a body that its schema accepts.

    The answer holds the plan checksum, which has the shape of a digest, so it is not sent through
    ``AccountApi.call``.
    """
    bodies: dict[str, dict[str, Any]] = {
        "approve": {"expected_checksum": _CHECKSUM, "reason": "reviewed"},
        "apply": {"expected_checksum": _CHECKSUM, "confirm_writes": True},
        "cancel": {"reason": "no longer needed"},
        "resume": {"expected_checksum": _CHECKSUM, "confirm_writes": True},
    }
    return api.client.post(f"/runs/{run_id}/{action}", headers={**headers, "Idempotency-Key": key}, json=bodies[action])


def _delete(api: AccountApi, name: str, headers: dict[str, str], **params: str) -> Response:
    return api.call("DELETE", f"/accounts/{name}", headers, params=params or None)


def _values(body: object, keys: frozenset[str]) -> list[str]:
    """Return every value of these keys at any depth, and the identifier of each related account."""
    found: list[str] = []
    if isinstance(body, list):
        for item in body:
            found.extend(_values(item, keys))
    elif isinstance(body, dict):
        for key, item in body.items():
            if key in keys and isinstance(item, str):
                found.append(item)
            elif key == "related" and isinstance(item, list):
                entries = cast("list[dict[str, str]]", item)
                found.extend(entry["id"] for entry in entries if entry.get("kind") == "account")
            else:
                found.extend(_values(item, keys))
    return found


def test_delete_returns_the_placeholder_and_refuses_every_credential_of_the_account(
    api: AccountApi, root: dict[str, str]
) -> None:
    alice = api.store.lookup_account_by_name("alice")
    assert alice is not None
    session, token = api.session_headers("alice"), api.token_headers("alice")
    assert api.sign_in("alice", WRONG_PASSWORD).status_code == 401

    response = _delete(api, "alice", root)

    assert response.status_code == 200, response.text
    assert response.json() == {"placeholder": f"Deleted account {alice.short_id}"}
    _error(api.call("GET", "/me", session), 401, "unauthenticated")
    _error(api.call("GET", "/me", token), 401, "unauthenticated")
    _error(api.sign_in("alice"), 401, "sign-in-refused")
    _error(api.call("GET", "/accounts/alice", root), 404, "account-not-found")
    assert "alice" not in [account["name"] for account in api.call("GET", "/accounts", root).json()]
    stored = api.store.lookup_account(alice.account_id)
    assert stored is not None
    assert (stored.name, stored.name_key, stored.password_hash) == (None, None, None)
    assert (stored.account_id, stored.short_id, stored.deleted_at) == (alice.account_id, alice.short_id, T0)


def test_a_second_delete_and_every_other_route_find_no_account(api: AccountApi, root: dict[str, str]) -> None:
    assert _delete(api, "alice", root).status_code == 200
    events = len(api.account_events())

    _error(_delete(api, "alice", root), 404, "account-not-found")
    _error(api.call("PATCH", "/accounts/alice", root, json={"disabled": True}), 404, "account-not-found")
    _error(api.call("GET", "/accounts/alice/tokens", root), 404, "account-not-found")

    assert len(api.account_events()) == events


def test_delete_writes_one_event_that_the_event_list_shows_with_the_placeholder(
    api: AccountApi, root: dict[str, str]
) -> None:
    alice_id, root_id = api.identifier("alice"), api.identifier("root")

    placeholder = _delete(api, "alice", root, reason=_REASON).json()["placeholder"]

    (event,) = api.account_events()
    assert (event.event_type, event.operation, event.outcome) == (
        "infrahub_sync.account.deleted",
        "delete-account",
        "accepted",
    )
    assert (event.actor, event.reason, event.created_at) == (root_id, _REASON, T0)
    assert RelatedResource(kind="account", id=alice_id) in event.related
    listed = api.call("GET", "/events", root, params={"event_type": "infrahub_sync.account.deleted"})
    assert listed.status_code == 200, listed.text
    (item,) = listed.json()["items"]
    assert item["actor"] == "root"
    assert {entry["id"] for entry in item["related"] if entry["kind"] == "account"} == {"root", placeholder}


def test_delete_refuses_a_reason_that_holds_a_token_prefix(api: AccountApi, root: dict[str, str]) -> None:
    _error(_delete(api, "alice", root, reason="pasted ifs_pat_value"), 422, "secret-parameter-refused")

    assert api.store.lookup_account_by_name("alice") is not None


def test_past_records_of_a_deleted_account_display_one_placeholder(api: AccountApi, root: dict[str, str]) -> None:
    """Scenario 3 of User Story 3: runs, approvals, events and configuration versions."""
    alice_id = api.identifier("alice")
    alice = api.token_headers("alice")
    run_id = _planned_run(api, alice_id)
    assert _mutate(api, alice, run_id, "approve", "approve-1").status_code == 200
    created = api.create_run(alice, reason=None)
    assert created.status_code == 202, created.text
    # A configuration version registered by the account, which needs the administrator level.
    assert api.call("PATCH", "/accounts/alice", root, json={"administrator": True}).status_code == 200
    package = package_data()
    package["configuration"]["name"] = "registered-by-the-deleted-account"
    registered = api.client.post(
        "/configs", headers={**alice, "Idempotency-Key": "register-us3"}, json={"package": package}
    )
    assert registered.status_code == 201, registered.text
    config_id = registered.json()["configuration"]["config_id"]

    placeholder = _delete(api, "alice", root).json()["placeholder"]

    # A run and a version hold checksums with the shape of a digest: they are read directly.
    answers = [
        api.client.get(f"/runs/{run_id}", headers=root),
        api.client.get("/runs", headers=root),
        api.call("GET", "/events", root),
        api.client.get(f"/configs/{config_id}/versions", headers=root),
    ]
    assert all("acct_" not in answer.text for answer in answers)
    assert all(answer.status_code == 200 for answer in answers), [answer.text for answer in answers]
    run = answers[0].json()["run"]
    assert (run["actor"], run["approval"]["approved_by"]) == (placeholder, placeholder)
    assert {run["actor"] for run in answers[1].json()["items"]} == {placeholder}
    fields = frozenset({"actor", "approved_by", "registered_by"})
    shown = {value for answer in answers for value in _values(answer.json(), fields)}
    assert placeholder in shown
    assert {value for value in shown if value.startswith("Deleted account")} == {placeholder}
    assert all("alice" not in answer.text for answer in answers)


def test_an_approval_of_a_deleted_account_stays_valid_and_an_administrator_applies_the_plan(
    api: AccountApi, root: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Scenario 2 of User Story 3, on a site that requires an approved plan."""
    monkeypatch.setenv(REQUIRE_APPROVAL_ENV, "true")
    run_id = _planned_run(api, api.identifier("alice"))
    assert _mutate(api, api.token_headers("alice"), run_id, "approve", "approve-1").status_code == 200
    approved = api.store.lookup_run(run_id).value
    assert approved is not None
    assert approved.approval is not None
    assert approved.approval.approved_by == api.identifier("alice")

    assert _delete(api, "alice", root).status_code == 200
    applied = _mutate(api, root, run_id, "apply", "apply-1")

    assert applied.status_code == 202, applied.text
    after = api.store.lookup_run(run_id).value
    assert after is not None
    assert after.approval == approved.approval


@pytest.mark.parametrize("action", ["approve", "apply", "cancel", "resume"])
def test_a_new_account_with_the_name_of_a_deleted_account_cannot_act_on_its_run(
    api: AccountApi, root: dict[str, str], action: str
) -> None:
    """Scenario 4 of User Story 3: rights compare identifiers, so the name gives no right."""
    old_id = api.identifier("alice")
    run_id = _planned_run(api, old_id)
    before = api.store.lookup_run(run_id)
    assert _delete(api, "alice", root).status_code == 200
    new = api.call("POST", "/accounts", root, json={"name": "alice", "password": PASSWORD})
    assert new.status_code == 201, new.text
    assert api.identifier("alice") != old_id

    refused = _mutate(api, api.token_headers("alice"), run_id, action, f"{action}-new-alice")

    _error(refused, 403, "forbidden")
    after = api.store.lookup_run(run_id).value
    assert before.value is not None
    assert after is not None
    # The refusal adds its audit link to the run, and changes nothing else.
    assert after.model_copy(update={"audit_links": before.value.audit_links}) == before.value
    shown = api.client.get(f"/runs/{run_id}", headers=root).json()["run"]["actor"]
    assert shown.startswith("Deleted account ")


def test_a_write_made_with_a_principal_resolved_before_the_deletion_displays_the_placeholder(
    api: AccountApi, root: dict[str, str]
) -> None:
    """FR-032: a request in progress during the deletion writes the identifier, never the name."""
    resolver = StoreAccountResolver(api.store, idle_seconds=IDLE_SECONDS, clock=api.clock)
    principal = resolver.resolve(api.token("alice")).principal
    assert principal is not None
    version = api.register()
    placeholder = _delete(api, "alice", root).json()["placeholder"]

    status, body = asyncio.run(
        api.service.create_run(
            CreateRunRequest(
                operation="plan", config_id=version["config_id"], registry_version=version["registry_version"]
            ),
            principal,
            "written-after-the-deletion",
        )
    )

    assert status == 202
    run_id = body["run"]["run_id"]
    stored = api.store.lookup_run(run_id).value
    assert stored is not None
    assert stored.actor == principal.actor
    assert api.client.get(f"/runs/{run_id}", headers=root).json()["run"]["actor"] == placeholder
    assert api.call("POST", "/accounts", root, json={"name": "alice", "password": PASSWORD}).status_code == 201
    assert api.client.get(f"/runs/{run_id}", headers=root).json()["run"]["actor"] == placeholder


def test_a_run_in_progress_is_not_stopped_by_the_deletion_and_an_administrator_cancels_it(
    api: AccountApi, root: dict[str, str]
) -> None:
    """Scenario 1 of User Story 3."""
    created = api.create_run(api.token_headers("alice"))
    assert created.status_code == 202, created.text
    run_id = created.json()["run"]["run_id"]
    before = api.store.lookup_run(run_id)
    run_events = len(api.store.audit_events(run_id))

    assert _delete(api, "alice", root).status_code == 200

    assert api.store.lookup_run(run_id) == before
    assert len(api.store.audit_events(run_id)) == run_events
    cancelled = _mutate(api, root, run_id, "cancel", "cancel-by-root")
    assert cancelled.status_code == 202, cancelled.text


def test_a_new_level_applies_to_the_next_request_with_no_new_sign_in(api: AccountApi, root: dict[str, str]) -> None:
    """Scenario 5 of User Story 3, for a token and for a session."""
    token, session = api.token_headers("alice"), api.session_headers("alice")
    _error(api.call("GET", "/accounts", token), 403, "forbidden")

    raised = api.call("PATCH", "/accounts/alice", root, json={"administrator": True, "reason": _REASON})

    assert raised.status_code == 200
    assert raised.json()["administrator"] is True
    for headers in (token, session):
        assert api.call("GET", "/accounts", headers).status_code == 200
        assert api.call("GET", "/me", headers).json()["administrator"] is True
    lowered = api.call("PATCH", "/accounts/alice", root, json={"administrator": False})
    assert lowered.json()["administrator"] is False
    for headers in (token, session):
        _error(api.call("GET", "/accounts", headers), 403, "forbidden")


def test_an_administrator_lowers_its_own_level_and_cannot_raise_it_again(api: AccountApi, root: dict[str, str]) -> None:
    assert api.call("PATCH", "/accounts/root", root, json={"administrator": False}).status_code == 200

    _error(api.call("PATCH", "/accounts/root", root, json={"administrator": True}), 403, "forbidden")
    account = api.store.lookup_account_by_name("root")
    assert account is not None
    assert account.administrator is False


@pytest.mark.parametrize(("name", "administrator"), [("root", True), ("alice", False)])
def test_a_level_that_the_account_has_changes_nothing_and_writes_no_event(
    api: AccountApi, root: dict[str, str], name: str, *, administrator: bool
) -> None:
    response = api.call("PATCH", f"/accounts/{name}", root, json={"administrator": administrator})

    assert response.status_code == 200
    assert response.json()["administrator"] is administrator
    assert api.account_events() == []


def test_a_patch_with_a_state_and_a_level_writes_one_event_for_each_change(
    api: AccountApi, root: dict[str, str]
) -> None:
    response = api.call("PATCH", "/accounts/alice", root, json={"disabled": True, "administrator": True})

    assert response.status_code == 200
    assert (response.json()["disabled"], response.json()["administrator"]) == (True, True)
    assert sorted(event.event_type or "" for event in api.account_events()) == [
        "infrahub_sync.account.disabled",
        "infrahub_sync.account.level_set",
    ]


def test_an_administrator_lists_and_deletes_the_tokens_of_another_account(
    api: AccountApi, root: dict[str, str]
) -> None:
    """Scenario 6 of User Story 3."""
    value = api.token("alice")
    assert api.call("GET", "/me", bearer(value)).status_code == 200

    listed = api.call("GET", "/accounts/alice/tokens", root)

    assert listed.status_code == 200
    (token,) = listed.json()
    assert set(token) == {"token_id", "name", "created_at", "expires_at", "last_used_at"}
    assert value not in listed.text
    deleted = api.call("DELETE", f"/accounts/ALICE/tokens/{token['token_id']}", root, params={"reason": _REASON})
    assert deleted.status_code == 204
    _error(api.call("GET", "/me", bearer(value)), 401, "unauthenticated")
    assert api.call("GET", "/accounts/alice/tokens", root).json() == []


def test_a_token_of_another_account_is_not_found_under_this_account(api: AccountApi, root: dict[str, str]) -> None:
    root_token_id = api.store.list_account_tokens(api.identifier("root"))[0].token_id

    _error(api.call("DELETE", f"/accounts/alice/tokens/{root_token_id}", root), 404, "token-not-found")

    assert api.call("GET", "/me", root).status_code == 200


@pytest.mark.parametrize(
    ("method", "path", "body", "variable"),
    [
        ("PATCH", "/accounts/admin", {"administrator": False}, ADMIN_ACCOUNT_PASSWORD_ENV),
        ("DELETE", "/accounts/Admin", None, ADMIN_ACCOUNT_PASSWORD_ENV),
        ("DELETE", "/accounts/admin/tokens/{token_id}", None, ADMIN_ACCOUNT_TOKEN_ENV),
    ],
    ids=["set-level", "delete", "delete-token"],
)
@pytest.mark.parametrize("caller", ["root", "admin"])
def test_admin_cannot_be_deleted_or_changed_and_the_refusal_names_the_variable(  # noqa: PLR0913, PLR0917
    api: AccountApi, method: str, path: str, body: dict[str, Any] | None, variable: str, caller: str
) -> None:
    """Scenario 7 of User Story 3."""
    headers = bearer(ADMIN_TOKEN) if caller == "admin" else api.token_headers("root")
    (admin_token,) = api.store.list_account_tokens(api.identifier("admin"))
    events = len(api.account_events())

    response = api.call(method, path.format(token_id=admin_token.token_id), headers, json=body)

    assert variable in _error(response, 409, "environment-managed-account")["message"]
    admin = api.store.lookup_account_by_name("admin")
    assert admin is not None
    assert (admin.administrator, admin.deleted_at) == (True, None)
    assert [token.token_id for token in api.store.list_account_tokens(admin.account_id)] == [admin_token.token_id]
    assert len(api.account_events()) == events
    assert api.call("GET", "/me", bearer(ADMIN_TOKEN)).status_code == 200


def test_an_administrator_can_delete_its_own_account_and_its_next_request_is_refused(
    api: AccountApi, root: dict[str, str]
) -> None:
    assert _delete(api, "root", root).status_code == 200

    _error(api.call("GET", "/accounts", root), 401, "unauthenticated")
    assert api.call("GET", "/accounts", bearer(ADMIN_TOKEN)).status_code == 200
