"""The routes of stored credentials for accounts (spec 027, contracts/http-api.md).

Create, list, view, change the value and change the description, for an administrator; list and
view for every signed-in account; the refusals; and the rule that no answer, audit event or
stored receipt holds a value (FR-001, FR-002, FR-012 to FR-015).

Every value is a marker text. A check compares with ``in`` and asserts the boolean, so a failed
check prints no value.
"""

from __future__ import annotations

import hashlib
import sqlite3
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

import httpx
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("prefect")
pytest.importorskip("cryptography")

from infrahub_sync.client import SyncClient
from infrahub_sync.product_store import RelatedResource
from infrahub_sync.service import serve
from infrahub_sync.service.credential_crypto import CredentialCipher
from infrahub_sync.service.settings import ENCRYPTION_KEY_ENV, CredentialSettingsError, encryption_key
from tests.helpers.stored_credentials import (
    TEST_ENCRYPTION_KEY,
    TEST_ENCRYPTION_KEY_BASE64,
    create_credentials_through_api,
    decrypt_stored_credential,
)
from tests.service.account_api import ADMIN_TOKEN, SECOND, AccountApi, build_account_api

if TYPE_CHECKING:
    from pathlib import Path

    from httpx import Response

    from infrahub_sync.product_store import AuditEvent

TOKEN_VALUE = "credential-marker-token-7f3a"  # noqa: S105 - a test value, not a secret.
NEW_TOKEN_VALUE = "credential-marker-token-9c1d"  # noqa: S105 - a test value, not a secret.
PASSWORD_VALUE = "credential-marker-password-2b8e"  # noqa: S105 - a test value, not a secret.
USERNAME_VALUE = "credential-marker-user-5e0c"
MARKERS = (TOKEN_VALUE, NEW_TOKEN_VALUE, PASSWORD_VALUE, USERNAME_VALUE)
EVENT_PREFIX = "infrahub_sync.credential."

_keys = iter(range(1_000_000))


def _key() -> str:
    return f"credential-test-{next(_keys)}"


def _holds_a_value(text: str) -> bool:
    """Return whether the text holds a marker value; the caller asserts the boolean."""
    return any(marker in text for marker in MARKERS)


@pytest.fixture
def api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AccountApi:
    return build_account_api(tmp_path, monkeypatch, credential_key=TEST_ENCRYPTION_KEY)


def _send(
    api: AccountApi,
    account: str,
    method: str,
    path: str,
    *,
    key: str | None = None,
    **arguments: Any,  # noqa: ANN401
) -> Response:
    """Send one request with an API token of the account, and check that its answer holds no value."""
    headers = api.token_headers(account)
    if method != "GET":
        headers["Idempotency-Key"] = key or _key()
    response = api.call(method, path, headers=headers, **arguments)
    leaked = _holds_a_value(response.text)
    assert not leaked
    return response


def _create(api: AccountApi, body: dict[str, Any] | None = None, *, account: str = "root") -> Response:
    return _send(api, account, "POST", "/credentials", json=body or {"name": "netbox-lab", "token": TOKEN_VALUE})


def _revision(api: AccountApi, name: str = "netbox-lab") -> int:
    credential = api.store.lookup_stored_credential(name)
    assert credential is not None
    return credential.current_revision


def _credential_events(api: AccountApi) -> list[AuditEvent]:
    return [event for event in api.events() if (event.event_type or "").startswith(EVENT_PREFIX)]


def test_an_administrator_creates_a_credential_and_the_answer_holds_no_value(api: AccountApi) -> None:
    response = _create(api, {"name": "netbox-lab", "description": "NetBox lab, read-only", "token": TOKEN_VALUE})

    assert response.status_code == 201, response.json()["error"]["code"]
    body = response.json()
    assert body["name"] == "netbox-lab"
    assert body["description"] == "NetBox lab, read-only"
    assert body["fields"] == ["token"]
    assert body["revision"] == 1
    assert body["created_by"] == "root"
    assert body["updated_by"] == "root"
    assert {"created_at", "updated_at"} <= body.keys()
    assert "credential_id" not in body
    fields = decrypt_stored_credential(api.store, "netbox-lab")
    stored_as_sent = fields == {"token": TOKEN_VALUE}
    assert stored_as_sent


def test_the_list_is_sorted_by_name_with_offset_and_limit(api: AccountApi) -> None:
    for name in ("zabbix", "aci-lab", "netbox-lab"):
        assert _create(api, {"name": name, "token": TOKEN_VALUE}).status_code == 201

    first = _send(api, "root", "GET", "/credentials", params={"limit": 2})
    second = _send(api, "root", "GET", "/credentials", params={"offset": 2, "limit": 2})

    assert first.status_code == 200
    assert [item["name"] for item in first.json()["items"]] == ["aci-lab", "netbox-lab"]
    assert first.json()["next_offset"] == 2
    assert [item["name"] for item in second.json()["items"]] == ["zabbix"]
    assert second.json()["next_offset"] is None


def test_an_administrator_views_one_credential(api: AccountApi) -> None:
    _create(api, {"name": "netbox-lab", "username": USERNAME_VALUE, "password": PASSWORD_VALUE})

    response = _send(api, "root", "GET", "/credentials/netbox-lab")

    assert response.status_code == 200
    assert response.json()["fields"] == ["password", "username"]


def test_an_unknown_credential_is_not_found(api: AccountApi) -> None:
    response = _send(api, "root", "GET", "/credentials/missing")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "credential-not-found"


def test_a_value_change_writes_the_next_revision(api: AccountApi) -> None:
    _create(api)
    api.clock.now += SECOND

    response = _send(
        api,
        "admin",
        "PUT",
        "/credentials/netbox-lab/value",
        json={"username": USERNAME_VALUE, "token": NEW_TOKEN_VALUE},
    )

    assert response.status_code == 200, response.json()["error"]["code"]
    body = response.json()
    assert body["revision"] == 2
    assert body["fields"] == ["token", "username"]
    assert body["created_by"] == "root"
    assert body["updated_by"] == "admin"
    fields = decrypt_stored_credential(api.store, "netbox-lab")
    stored_as_sent = fields == {"username": USERNAME_VALUE, "token": NEW_TOKEN_VALUE}
    assert stored_as_sent


def test_a_value_change_of_an_unknown_credential_is_not_found(api: AccountApi) -> None:
    response = _send(api, "root", "PUT", "/credentials/missing/value", json={"token": TOKEN_VALUE})

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "credential-not-found"


def test_a_description_change_keeps_the_revision(api: AccountApi) -> None:
    _create(api)

    response = _send(api, "root", "PATCH", "/credentials/netbox-lab", json={"description": "the lab NetBox"})

    assert response.status_code == 200
    assert response.json()["description"] == "the lab NetBox"
    assert response.json()["revision"] == 1


def test_an_account_that_is_not_an_administrator_lists_and_views(api: AccountApi) -> None:
    _create(api)

    listed = _send(api, "alice", "GET", "/credentials")
    viewed = _send(api, "alice", "GET", "/credentials/netbox-lab")

    assert listed.status_code == 200
    assert [item["name"] for item in listed.json()["items"]] == ["netbox-lab"]
    assert viewed.status_code == 200
    assert viewed.json()["fields"] == ["token"]


@pytest.mark.parametrize(
    ("method", "path"),
    [("POST", "/credentials"), ("PUT", "/credentials/netbox-lab/value"), ("PATCH", "/credentials/netbox-lab")],
)
@pytest.mark.parametrize("body", [{"unexpected": TOKEN_VALUE}, {"token": 5}], ids=["unknown-key", "wrong-type"])
def test_a_change_is_refused_to_an_account_that_is_not_an_administrator_before_the_body_is_checked(
    api: AccountApi, method: str, path: str, body: dict[str, Any]
) -> None:
    _create(api)

    response = _send(api, "alice", method, path, json=body)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"
    refusals = [event for event in _credential_events(api) if event.outcome == "refused-authorization"]
    assert len(refusals) == 1
    assert refusals[0].actor == api.identifier("alice")
    assert refusals[0].details is not None
    assert refusals[0].details.cause == "manage-credentials"


def test_a_change_without_an_idempotency_key_is_refused_to_a_non_administrator_with_403(api: AccountApi) -> None:
    response = api.call("POST", "/credentials", headers=api.token_headers("alice"), json={"name": "x"})

    assert response.status_code == 403


def test_a_taken_name_is_refused(api: AccountApi) -> None:
    _create(api)

    response = _create(api, {"name": "netbox-lab", "token": NEW_TOKEN_VALUE})

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "credential-exists"
    assert _revision(api) == 1


_INVALID_FIELD_SETS: list[tuple[str, dict[str, str]]] = [
    ("no-field", {}),
    ("all-three", {"username": USERNAME_VALUE, "password": PASSWORD_VALUE, "token": TOKEN_VALUE}),
    ("password-only", {"password": PASSWORD_VALUE}),
    ("username-only", {"username": USERNAME_VALUE}),
    ("empty-value", {"token": ""}),
    ("too-long", {"token": TOKEN_VALUE + "x" * 4096}),
    ("control-character", {"token": TOKEN_VALUE + "\n"}),
]


@pytest.mark.parametrize(
    "fields", [fields for _, fields in _INVALID_FIELD_SETS], ids=[i for i, _ in _INVALID_FIELD_SETS]
)
def test_a_field_set_outside_the_rules_is_refused_on_create_without_repeating_the_body(
    api: AccountApi, fields: dict[str, str]
) -> None:
    response = _create(api, {"name": "netbox-lab", **fields})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "credential-fields-invalid"
    assert api.store.lookup_stored_credential("netbox-lab") is None


@pytest.mark.parametrize(
    "fields", [fields for _, fields in _INVALID_FIELD_SETS], ids=[i for i, _ in _INVALID_FIELD_SETS]
)
def test_a_field_set_outside_the_rules_is_refused_on_a_value_change(api: AccountApi, fields: dict[str, str]) -> None:
    _create(api)

    response = _send(api, "root", "PUT", "/credentials/netbox-lab/value", json=fields)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "credential-fields-invalid"
    assert _revision(api) == 1


@pytest.mark.parametrize(
    "body",
    [
        {"name": "NetBox", "token": TOKEN_VALUE},
        {"name": "netbox-lab", "token": TOKEN_VALUE, "extra": TOKEN_VALUE},
        {"name": "netbox-lab", "token": TOKEN_VALUE, "description": "d" * 501},
        {"name": "netbox-lab", "password": ["not", "text", PASSWORD_VALUE], "username": USERNAME_VALUE},
    ],
    ids=["name-pattern", "unknown-key", "long-description", "wrong-type"],
)
def test_an_invalid_request_is_refused_without_repeating_the_body(api: AccountApi, body: dict[str, Any]) -> None:
    response = _create(api, body)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "request-invalid"


def test_a_description_that_holds_a_submitted_value_is_refused(api: AccountApi) -> None:
    response = _create(api, {"name": "netbox-lab", "token": TOKEN_VALUE, "description": f"token {TOKEN_VALUE}"})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "secret-parameter-refused"
    assert api.store.lookup_stored_credential("netbox-lab") is None


def test_a_description_that_holds_a_token_prefix_is_refused(api: AccountApi) -> None:
    _create(api)

    response = _send(api, "root", "PATCH", "/credentials/netbox-lab", json={"description": "ifs_pat_something"})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "secret-parameter-refused"


def test_a_repeated_request_returns_the_first_answer_and_writes_no_second_revision(api: AccountApi) -> None:
    _create(api)
    key = _key()
    body = {"token": NEW_TOKEN_VALUE}

    first = _send(api, "root", "PUT", "/credentials/netbox-lab/value", key=key, json=body)
    second = _send(api, "root", "PUT", "/credentials/netbox-lab/value", key=key, json=body)

    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert _revision(api) == 2


def test_a_repeated_key_with_another_value_is_refused(api: AccountApi) -> None:
    _create(api)
    key = _key()
    _send(api, "root", "PUT", "/credentials/netbox-lab/value", key=key, json={"token": NEW_TOKEN_VALUE})

    response = _send(api, "root", "PUT", "/credentials/netbox-lab/value", key=key, json={"token": TOKEN_VALUE})

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "idempotency-conflict"
    assert _revision(api) == 2


def test_the_audit_events_hold_names_and_revisions_and_no_value(api: AccountApi) -> None:
    _create(api)
    _send(api, "root", "PUT", "/credentials/netbox-lab/value", json={"token": NEW_TOKEN_VALUE})
    _send(api, "root", "PATCH", "/credentials/netbox-lab", json={"description": "the lab"})

    accepted = [event for event in _credential_events(api) if event.outcome == "accepted"]

    assert sorted((event.event_type, event.details.revision if event.details else None) for event in accepted) == [
        ("infrahub_sync.credential.created", 1),
        ("infrahub_sync.credential.updated", 2),
        ("infrahub_sync.credential.value_changed", 2),
    ]
    for event in accepted:
        assert RelatedResource(kind="credential", id="netbox-lab") in event.related
        assert event.actor == api.identifier("root")
    leaked = _holds_a_value(api.stored_text())
    assert not leaked


def test_the_database_holds_no_value_in_any_table(api: AccountApi, tmp_path: Path) -> None:
    """The receipts store the answer for a replay; neither they nor any other row hold a value."""
    _create(api, {"name": "netbox-lab", "username": USERNAME_VALUE, "password": PASSWORD_VALUE})
    _send(api, "root", "PUT", "/credentials/netbox-lab/value", json={"token": NEW_TOKEN_VALUE})
    _create(api, {"name": "netbox-lab", "token": TOKEN_VALUE})

    databases = sorted(tmp_path.rglob("*.sqlite3"))
    assert databases
    for database in databases:
        with sqlite3.connect(database) as connection:
            dump = "\n".join(connection.iterdump())
            receipts = connection.execute(
                "SELECT response_body FROM mutation_receipts WHERE resource_kind = 'credential'"
            ).fetchall()
        leaked = _holds_a_value(dump)
        assert not leaked
        assert receipts


def test_the_credential_routes_answer_503_when_no_key_is_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    api = build_account_api(tmp_path, monkeypatch)

    created = _create(api)
    listed = _send(api, "root", "GET", "/credentials")

    assert created.status_code == 503
    assert created.json()["error"]["code"] == "credential-key-not-configured"
    assert "INFRAHUB_SYNC_ENCRYPTION_KEY" in created.json()["error"]["message"]
    assert listed.status_code == 200


def test_the_api_helper_creates_then_changes_credentials_through_the_real_routes(api: AccountApi) -> None:
    wanted = {"netbox-lab": {"token": TOKEN_VALUE}, "aci-lab": {"username": USERNAME_VALUE, "password": PASSWORD_VALUE}}

    created = create_credentials_through_api("http://testserver", ADMIN_TOKEN, wanted, client=api.client)
    changed = create_credentials_through_api(
        "http://testserver", ADMIN_TOKEN, {"netbox-lab": {"token": NEW_TOKEN_VALUE}}, client=api.client
    )

    assert {name: resource["revision"] for name, resource in created.items()} == {"netbox-lab": 1, "aci-lab": 1}
    assert changed["netbox-lab"]["revision"] == 2
    leaked = _holds_a_value(repr(created) + repr(changed))
    assert not leaked


def test_an_unauthenticated_request_is_refused(api: AccountApi) -> None:
    response = api.call("GET", "/credentials")

    assert response.status_code == 401


def test_a_session_of_an_administrator_creates_a_credential(api: AccountApi) -> None:
    headers = {**api.session_headers("root"), "Idempotency-Key": _key()}

    response = api.call("POST", "/credentials", headers=headers, json={"name": "netbox-lab", "token": TOKEN_VALUE})

    assert response.status_code == 201


@pytest.mark.parametrize("value", [None, "", "  "], ids=["unset", "empty", "spaces"])
def test_an_unset_key_gives_the_routes_no_key(value: str | None) -> None:
    environ = {} if value is None else {ENCRYPTION_KEY_ENV: value}

    assert encryption_key(environ) is None


def test_a_key_that_is_set_but_not_accepted_stops_the_start() -> None:
    with pytest.raises(CredentialSettingsError, match=ENCRYPTION_KEY_ENV):
        encryption_key({ENCRYPTION_KEY_ENV: "bm90LTMyLWJ5dGVz"})


def test_the_served_api_receives_a_cipher_of_the_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENCRYPTION_KEY_ENV, TEST_ENCRYPTION_KEY_BASE64)
    monkeypatch.setattr(serve, "RunLivenessReconciler", lambda *_arguments, **_settings: object())

    built = serve.build_app(
        projection_factory=object,
        resolver_factory=lambda: SimpleNamespace(secret_values=()),
        run_service_factory=lambda *_arguments, **_settings: object(),
        configuration_routes_factory=lambda **_settings: object(),
        app_factory=lambda *_arguments, **settings: settings,
        start_check=lambda _pool: None,
        schedule_service_factory=lambda *_arguments, **_settings: object(),
    )

    cipher = cast("dict[str, object]", built)["credential_cipher"]
    assert isinstance(cipher, CredentialCipher)
    assert cipher.key_id == CredentialCipher(TEST_ENCRYPTION_KEY).key_id


def test_the_client_runs_every_operation_through_the_real_routes(api: AccountApi) -> None:
    """FR-001: the client creates, lists, views, changes the value of and describes a credential."""

    def forward(request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/api")
        answer = api.client.request(
            request.method, path, params=request.url.params, headers=dict(request.headers), content=request.content
        )
        return httpx.Response(answer.status_code, headers=answer.headers, content=answer.content)

    client = SyncClient("http://sync.test", ADMIN_TOKEN, transport=httpx.MockTransport(forward))

    created = client.create_credential("netbox-lab", token=TOKEN_VALUE, description="lab")
    changed = client.set_credential_value("netbox-lab", username=USERNAME_VALUE, password=PASSWORD_VALUE)
    described = client.set_credential_description("netbox-lab", "the lab")
    listed = list(client.iter_credentials(limit=1))
    viewed = client.get_credential("netbox-lab")

    assert (created.revision, changed.revision, described.revision) == (1, 2, 2)
    assert created.created_by == "admin"
    assert [item.name for item in listed] == ["netbox-lab"]
    assert viewed.fields == ("password", "username")
    assert viewed.description == "the lab"
    leaked = _holds_a_value(repr((created, changed, described, listed, viewed)))
    assert not leaked


def test_a_username_equal_to_the_credential_name_leaves_the_name_intact(api: AccountApi) -> None:
    """A service account is often named like its credential; the name is not a value to redact."""
    body = {"name": "netbox-sync", "username": "netbox-sync", "password": PASSWORD_VALUE}
    key = _key()

    first = _send(api, "root", "POST", "/credentials", key=key, json=body)
    repeated = _send(api, "root", "POST", "/credentials", key=key, json=body)

    assert first.status_code == 201, first.json()["error"]["code"]
    assert first.json()["name"] == "netbox-sync"
    assert repeated.status_code == 201
    assert repeated.json() == first.json()
    created = [e for e in _credential_events(api) if e.event_type == "infrahub_sync.credential.created"]
    assert len(created) == 1
    assert RelatedResource(kind="credential", id="netbox-sync") in created[0].related
    receipt = api.store.lookup_mutation(api.identifier("root"), hashlib.sha256(key.encode()).hexdigest()).value
    assert receipt is not None
    assert (receipt.resource_id, receipt.operation) == ("netbox-sync", "credential-create")


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/credentials/Not_A_Name"),
        ("PUT", "/credentials/Not_A_Name/value"),
        ("PATCH", "/credentials/Not_A_Name"),
    ],
)
def test_a_path_name_outside_the_pattern_is_refused_as_an_invalid_request(
    api: AccountApi, method: str, path: str
) -> None:
    body = None if method == "GET" else ({"token": TOKEN_VALUE} if method == "PUT" else {"description": "d"})

    response = _send(api, "root", method, path, json=body)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "request-invalid"
    assert "Not_A_Name" not in repr(api.events())
