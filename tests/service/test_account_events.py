"""The audit event of each account action, and the event list filtered by an account name.

An event names an account by its identifier in the store. The event list shows the account name,
and takes a name in its ``related`` filter.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("prefect")

from infrahub_sync.product_store import AuditEvent, RelatedResource
from infrahub_sync.product_store.accounts import SIGN_IN_FAILURE_LIMIT
from infrahub_sync.product_store.models import EVENT_TYPE_PATTERN
from infrahub_sync.service import events as event_types
from tests.service.account_api import (
    NEW_PASSWORD,
    PASSWORD,
    SECOND,
    WRONG_PASSWORD,
    AccountApi,
    build_account_api,
    text_hash,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

_REASON = "asked by the network team"


@pytest.fixture(name="api")
def _api_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AccountApi:
    return build_account_api(tmp_path, monkeypatch)


def _create_account(api: AccountApi, caller: dict[str, str]) -> None:
    body = {"name": "carol", "password": PASSWORD, "reason": _REASON}
    assert api.call("POST", "/accounts", caller, json=body).status_code == 201


def _disable(api: AccountApi, caller: dict[str, str]) -> None:
    assert api.call("PATCH", "/accounts/alice", caller, json={"disabled": True, "reason": _REASON}).status_code == 200


def _enable(api: AccountApi, caller: dict[str, str]) -> None:
    api.store.disable_account(api.identifier("alice"))
    assert api.call("PATCH", "/accounts/alice", caller, json={"disabled": False, "reason": _REASON}).status_code == 200


def _set_password(api: AccountApi, caller: dict[str, str]) -> None:
    body = {"new_password": NEW_PASSWORD, "reason": _REASON}
    assert api.call("PUT", "/accounts/alice/password", caller, json=body).status_code == 204


def _unlock(api: AccountApi, caller: dict[str, str]) -> None:
    assert api.call("POST", "/accounts/alice/unlock", caller, json={"reason": _REASON}).status_code == 204


def _change_password(api: AccountApi, caller: dict[str, str]) -> None:
    body = {"current_password": PASSWORD, "new_password": NEW_PASSWORD, "reason": _REASON}
    assert api.call("PUT", "/me/password", caller, json=body).status_code == 204


def _create_token(api: AccountApi, caller: dict[str, str]) -> None:
    created = api.client.post("/me/tokens", headers=caller, json={"name": "ci", "reason": _REASON})
    assert created.status_code == 201
    api.received_values.append(created.json()["token"])


def _delete_token(api: AccountApi, caller: dict[str, str]) -> None:
    token_id = api.store.list_account_tokens(api.identifier("alice"))[0].token_id
    assert api.call("DELETE", f"/me/tokens/{token_id}", caller, params={"reason": _REASON}).status_code == 204


# Each action: the event type, the operation, the caller, the account acted on, and the request.
_ACTIONS: list[tuple[str, str, str, str, Callable[[AccountApi, dict[str, str]], None]]] = [
    ("created", "create-account", "root", "carol", _create_account),
    ("disabled", "disable-account", "root", "alice", _disable),
    ("enabled", "enable-account", "root", "alice", _enable),
    ("password_set", "set-account-password", "root", "alice", _set_password),
    ("unlocked", "unlock-account", "root", "alice", _unlock),
    ("password_changed", "change-password", "alice", "alice", _change_password),
    ("token_created", "create-token", "alice", "alice", _create_token),
    ("token_deleted", "delete-token", "alice", "alice", _delete_token),
]


@pytest.mark.parametrize("case", _ACTIONS, ids=[row[0] for row in _ACTIONS])
def test_each_account_action_writes_one_event_that_names_the_account_acted_on(
    api: AccountApi, case: tuple[str, str, str, str, Callable[[AccountApi, dict[str, str]], None]]
) -> None:
    action, operation, caller, target, request_action = case
    headers = api.token_headers(caller)

    request_action(api, headers)

    (event,) = api.account_events()
    assert event.event_type == f"infrahub_sync.account.{action}"
    assert (event.operation, event.outcome, event.reason) == (operation, "accepted", _REASON)
    assert event.actor == api.identifier(caller)
    assert RelatedResource(kind="account", id=api.identifier(target)) in event.related
    assert {resource.id for resource in event.related} == {api.identifier(caller), api.identifier(target)}
    assert (event.run_id, event.config_id, event.primary_resource) == (None, None, None)
    stored = api.stored_text()
    for secret in (PASSWORD, NEW_PASSWORD, text_hash(PASSWORD), text_hash(NEW_PASSWORD), headers["Authorization"][7:]):
        assert secret not in stored
    assert not any(value in stored for value in api.received_values)


def test_every_account_event_type_matches_the_pattern_of_the_store() -> None:
    names = [name for name in dir(event_types) if name.startswith("ACCOUNT_")]

    assert len(names) == 11
    for name in names:
        value = getattr(event_types, name)
        assert value.startswith("infrahub_sync.account.")
        AuditEvent.model_validate(
            {
                "event_id": "a-1",
                "actor": "acct_" + "0" * 32,
                "operation": "any",
                "reason": None,
                "outcome": "accepted",
                "created_at": "2026-11-18T09:00:00Z",
                "event_type": value,
            }
        )
    assert EVENT_TYPE_PATTERN.endswith("[a-z_]+$")


def test_a_sign_in_and_a_sign_out_write_one_event_each(api: AccountApi) -> None:
    session = api.session_headers("alice")
    assert api.call("POST", "/auth/sign-out", session).status_code == 204

    assert sorted(event.event_type or "" for event in api.account_events()) == [
        "infrahub_sync.account.signed_in",
        "infrahub_sync.account.signed_out",
    ]
    assert all(event.actor == api.identifier("alice") for event in api.account_events())
    assert session["Authorization"][7:] not in api.stored_text()


@pytest.mark.parametrize("name", ["alice", "nobody", "Alice", "disabled-one"])
def test_a_refused_sign_in_writes_no_name_and_no_account(api: AccountApi, name: str) -> None:
    api.add("disabled-one")
    api.store.disable_account(api.identifier("disabled-one"))
    identifiers = {account.account_id for account in api.store.list_accounts()}

    for _ in range(SIGN_IN_FAILURE_LIMIT + 1):
        assert api.sign_in(name, WRONG_PASSWORD).status_code == 401
        api.clock.now += SECOND

    events = api.events()
    assert len(events) == SIGN_IN_FAILURE_LIMIT + 1
    for event in events:
        assert (event.actor, event.outcome, event.reason) == (
            "unauthenticated",
            "refused-authentication",
            "sign-in-refused",
        )
        assert event.event_type is None
        assert {resource.id for resource in event.related} == {"unauthenticated"}
    stored = api.stored_text().lower()
    assert name.lower() not in stored
    assert WRONG_PASSWORD not in stored
    assert not any(identifier in stored for identifier in identifiers)


def test_a_wrong_current_password_writes_a_refusal_without_a_password(api: AccountApi) -> None:
    alice = api.token_headers("alice")
    body = {"current_password": WRONG_PASSWORD, "new_password": NEW_PASSWORD, "reason": _REASON}

    assert api.call("PUT", "/me/password", alice, json=body).status_code == 403

    (event,) = api.account_events()
    assert (event.event_type, event.outcome) == ("infrahub_sync.account.refused", "refused-current-password")
    assert (event.actor, event.reason) == (api.identifier("alice"), None)
    assert WRONG_PASSWORD not in api.stored_text()
    assert NEW_PASSWORD not in api.stored_text()


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("POST", "/accounts", {"name": "alice", "password": PASSWORD}),
        ("POST", "/accounts", {"name": "two words", "password": PASSWORD}),
        ("POST", "/accounts", {"name": "carol", "password": "short"}),
        ("PATCH", "/accounts/nobody", {"disabled": True}),
        ("PATCH", "/accounts/admin", {"disabled": True}),
        ("PUT", "/accounts/alice/password", {"new_password": "short"}),
        ("GET", "/accounts", None),
        ("GET", "/accounts/alice", None),
        ("GET", "/me/tokens", None),
        ("DELETE", "/me/tokens/no-such-token", None),
    ],
)
def test_a_read_and_a_request_refused_for_its_content_write_no_account_event(
    api: AccountApi, method: str, path: str, body: dict[str, Any] | None
) -> None:
    root = api.token_headers("root")

    assert api.call(method, path, root, json=body).status_code in {200, 404, 409, 422}

    assert api.account_events() == []


def _listed(api: AccountApi, headers: dict[str, str], query: str) -> list[dict[str, Any]]:
    response = api.call("GET", f"/events{query}", headers)
    assert response.status_code == 200, response.text
    return response.json()["items"]


def test_the_event_list_shows_account_names_and_never_an_identifier(api: AccountApi) -> None:
    root = api.token_headers("root")
    _disable(api, root)

    (item,) = _listed(api, root, "?event_type=infrahub_sync.account.disabled")

    assert item["actor"] == "root"
    assert item["primary_resource"] is None
    assert item["related"] == [
        {"kind": "account", "id": name} for name in sorted(["alice", "root"], key=api.identifier)
    ]
    assert item["reason"] == _REASON


@pytest.mark.parametrize("name", ["alice", "Alice", "ALICE"])
def test_the_event_list_is_filtered_by_an_account_name_in_any_letter_case(api: AccountApi, name: str) -> None:
    root = api.token_headers("root")
    _disable(api, root)
    _create_account(api, root)

    items = _listed(api, root, f"?related=account:{name}")

    assert [item["event_type"] for item in items] == ["infrahub_sync.account.disabled"]


def test_the_event_list_filtered_by_two_accounts_names_the_events_of_both(api: AccountApi) -> None:
    root = api.token_headers("root")
    _disable(api, root)
    _create_token(api, api.token_headers("root"))

    both = _listed(api, root, "?related=account:alice&related=account:root")
    of_root = _listed(api, root, "?related=account:root")

    assert [item["event_type"] for item in both] == ["infrahub_sync.account.disabled"]
    assert sorted(item["event_type"] for item in of_root) == [
        "infrahub_sync.account.disabled",
        "infrahub_sync.account.token_created",
    ]


def test_the_event_list_accepts_an_account_identifier_in_the_filter(api: AccountApi) -> None:
    root = api.token_headers("root")
    _disable(api, root)

    items = api.client.get(f"/events?related=account:{api.identifier('alice')}", headers=root).json()["items"]

    assert [item["event_type"] for item in items] == ["infrahub_sync.account.disabled"]
    assert "acct_" not in repr(items)


@pytest.mark.parametrize("name", ["nobody", "two words", "nul\x00name", "acct_not-an-identifier"])
def test_the_event_list_filtered_by_an_unknown_account_name_is_empty(api: AccountApi, name: str) -> None:
    root = api.token_headers("root")
    _disable(api, root)

    response = api.call("GET", "/events", root, params={"related": f"account:{name}"})

    assert response.status_code == 200, response.text
    assert response.json()["items"] == []


def test_the_event_list_still_finds_a_record_that_holds_a_plain_name(api: AccountApi) -> None:
    """A record made before the accounts existed holds a principal's name, which no account has."""
    api.service.record_account_event(
        event_types.ACCOUNT_UNLOCKED, actor="legacy-operator", account_id="legacy-operator", operation="unlock-account"
    )
    root = api.token_headers("root")

    (item,) = _listed(api, root, "?related=account:legacy-operator")

    assert (item["actor"], item["related"]) == ("legacy-operator", [{"kind": "account", "id": "legacy-operator"}])


def test_the_event_list_filter_follows_the_account_after_its_events(api: AccountApi) -> None:
    """The events of a run are found by the name of the account that started it."""
    alice, root = api.token_headers("alice"), api.token_headers("root")
    assert api.create_run(alice).status_code == 202

    items = _listed(api, root, "?related=account:alice&event_type=infrahub_sync.run.accepted")

    assert [(item["actor"], item["operation"]) for item in items] == [("alice", "plan")]
