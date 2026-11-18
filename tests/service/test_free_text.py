"""The free text of a request: a ``reason`` that holds a token is refused and never recorded.

The process cannot list the tokens it stores as digests, so a ``reason`` that holds the prefix of
an API token or of a session token is refused, whoever the token belongs to. The token of ``admin``
that the environment defines is refused by its value. The password of ``admin`` is in no check.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("prefect")

from infrahub_sync.client.models import (
    REASON_MAX_LENGTH,
    ApplyRunRequest,
    ApproveRunRequest,
    CancelRunRequest,
    ConfigMutationRequest,
    CreateRunRequest,
    ResumeRunRequest,
    VerifyRunRequest,
)
from infrahub_sync.service import credentials
from infrahub_sync.service.service import is_free_text
from tests.configuration.validation_packages import package_data
from tests.service.account_api import (
    ADMIN_PASSWORD,
    ADMIN_TOKEN,
    NEW_PASSWORD,
    PASSWORD,
    AccountApi,
    bearer,
    build_account_api,
)

if TYPE_CHECKING:
    from pathlib import Path

    from httpx import Response
    from pydantic import BaseModel

_CHECKSUM = "a" * 64
_RUN_ID = "20261118T0900-0a1b2c3d"
# Texts that hold a token, by what makes the service refuse them.
_SECRET_TEXTS = [
    pytest.param(f"rotate {credentials.API_TOKEN_PREFIX}Zm9vYmFy", id="api-token-prefix"),
    pytest.param(f"{credentials.SESSION_TOKEN_PREFIX}Zm9vYmFy was shared", id="session-token-prefix"),
    pytest.param(credentials.API_TOKEN_PREFIX, id="the-prefix-alone"),
    pytest.param(f"sent with {ADMIN_TOKEN}", id="the-token-of-admin"),
]
# Every run route that takes a reason, with the rest of a body that its schema accepts.
_RUN_ROUTES: list[tuple[str, dict[str, Any]]] = [
    ("/runs", {"operation": "plan", "config_id": "inventory", "registry_version": 1}),
    (f"/runs/{_RUN_ID}/verify", {}),
    (f"/runs/{_RUN_ID}/apply", {"expected_checksum": _CHECKSUM, "confirm_writes": True}),
    (f"/runs/{_RUN_ID}/resume", {"expected_checksum": _CHECKSUM, "confirm_writes": True}),
    (f"/runs/{_RUN_ID}/approve", {"expected_checksum": _CHECKSUM}),
    (f"/runs/{_RUN_ID}/cancel", {}),
]
_RUN_ROUTE_IDS = ["create", "verify", "apply", "resume", "approve", "cancel"]


@pytest.fixture(name="api")
def _api_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AccountApi:
    return build_account_api(tmp_path, monkeypatch)


def _post(api: AccountApi, path: str, headers: dict[str, str], body: dict[str, Any], *, key: str = "key-1") -> Response:
    return api.client.post(path, headers={**headers, "Idempotency-Key": key}, json=body)


def _assert_refused(
    api: AccountApi, response: Response, text: str, *, event_type: str, reason: str | None = None
) -> None:
    """Check the refusal of a free text, its one event, and that neither the answer nor the store holds the text."""
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "secret-parameter-refused"
    assert text not in response.text
    assert text not in api.stored_text()
    assert ADMIN_TOKEN not in api.stored_text()
    (refusal,) = api.refusals("refused-secret-parameter")
    assert (refusal.event_type, refusal.reason) == (event_type, reason)


@pytest.mark.parametrize("text", _SECRET_TEXTS)
@pytest.mark.parametrize(("path", "body"), _RUN_ROUTES, ids=_RUN_ROUTE_IDS)
def test_a_run_route_refuses_a_reason_that_holds_a_token(
    api: AccountApi, path: str, body: dict[str, Any], text: str
) -> None:
    response = _post(api, path, api.token_headers("alice"), {**body, "reason": text})

    _assert_refused(api, response, text, event_type="infrahub_sync.run.refused")


def test_a_run_route_refuses_a_branch_that_holds_a_token_prefix(api: AccountApi) -> None:
    text = f"{credentials.API_TOKEN_PREFIX}Zm9vYmFy"
    body = {"operation": "plan", "config_id": "inventory", "registry_version": 1, "branch": text, "reason": "plan"}

    response = _post(api, "/runs", api.token_headers("alice"), body)

    _assert_refused(api, response, text, event_type="infrahub_sync.run.refused", reason="plan")


@pytest.mark.parametrize("text", _SECRET_TEXTS)
@pytest.mark.parametrize("path", ["/configs", "/configs/inventory/versions"], ids=["register", "create-version"])
def test_a_configuration_route_refuses_a_reason_that_holds_a_token(api: AccountApi, path: str, text: str) -> None:
    response = _post(api, path, bearer(ADMIN_TOKEN), {"package": package_data(), "reason": text})

    _assert_refused(api, response, text, event_type="infrahub_sync.config.refused")
    assert api.client.get("/configs", headers=bearer(ADMIN_TOKEN)).json()["items"] == []


@pytest.mark.parametrize("path", ["/configs", "/configs/inventory/versions"], ids=["register", "create-version"])
def test_the_refusal_of_a_configuration_route_for_an_account_that_is_not_an_administrator_records_no_token(
    api: AccountApi, path: str
) -> None:
    text = f"asked with {credentials.new_api_token()}"

    response = _post(api, path, api.token_headers("alice"), {"package": package_data(), "reason": text})

    assert response.status_code == 403
    assert text not in api.stored_text()
    (refusal,) = api.refusals("refused-authorization")
    assert refusal.reason is None


def test_the_refusal_of_a_configuration_route_keeps_a_reason_that_holds_no_token(api: AccountApi) -> None:
    body = {"package": package_data(), "reason": "asked by the network team"}

    assert _post(api, "/configs", api.token_headers("alice"), body).status_code == 403

    (refusal,) = api.refusals("refused-authorization")
    assert refusal.reason == "asked by the network team"


@pytest.mark.parametrize("text", _SECRET_TEXTS)
@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("POST", "/accounts", {"name": "carol", "password": PASSWORD}),
        ("PATCH", "/accounts/alice", {"disabled": True}),
        ("PUT", "/accounts/alice/password", {"new_password": NEW_PASSWORD}),
        ("POST", "/accounts/alice/unlock", {}),
        ("PUT", "/me/password", {"current_password": PASSWORD, "new_password": NEW_PASSWORD}),
        ("POST", "/me/tokens", {"name": "ci"}),
    ],
    ids=["create-account", "update-account", "set-password", "unlock", "change-password", "create-token"],
)
def test_an_account_route_refuses_a_reason_that_holds_a_token(
    api: AccountApi, method: str, path: str, body: dict[str, Any], text: str
) -> None:
    root = api.token_headers("root")
    events = len(api.account_events())

    response = api.call(method, path, root, json={**body, "reason": text})

    _assert_refused(api, response, text, event_type="infrahub_sync.account.refused")
    assert len(api.account_events()) == events + 1
    assert api.store.lookup_account_by_name("carol") is None
    assert api.sign_in("alice").status_code == 200
    assert api.sign_in("root").status_code == 200
    assert api.gate.hashes == 0


@pytest.mark.parametrize("text", _SECRET_TEXTS)
def test_the_deletion_of_a_token_refuses_a_reason_that_holds_a_token(api: AccountApi, text: str) -> None:
    alice = api.session_headers("alice")
    token_id = api.client.post("/me/tokens", headers=alice, json={"name": "ci"}).json()["token_id"]

    response = api.call("DELETE", f"/me/tokens/{token_id}", alice, params={"reason": text})

    _assert_refused(api, response, text, event_type="infrahub_sync.account.refused")
    assert len(api.call("GET", "/me/tokens", alice).json()) == 1


def test_a_reason_of_513_characters_is_refused_on_every_route(api: AccountApi) -> None:
    alice, root = api.token_headers("alice"), api.token_headers("root")
    too_long = "r" * (REASON_MAX_LENGTH + 1)
    events = len(api.events())

    answers = [_post(api, path, alice, {**body, "reason": too_long}) for path, body in _RUN_ROUTES]
    answers.extend(
        [
            _post(api, "/configs", bearer(ADMIN_TOKEN), {"package": package_data(), "reason": too_long}),
            api.call("POST", "/accounts/alice/unlock", root, json={"reason": too_long}),
            api.call("POST", "/me/tokens", alice, json={"name": "ci", "reason": too_long}),
            api.call("DELETE", "/me/tokens/any", alice, params={"reason": too_long}),
        ]
    )

    for response in answers:
        assert response.status_code == 422, response.text
        assert response.json()["error"]["code"] == "request-invalid"
    assert len(api.events()) == events


def test_a_reason_of_512_characters_is_accepted_and_recorded(api: AccountApi) -> None:
    longest = "r" * REASON_MAX_LENGTH

    assert api.create_run(api.token_headers("alice"), reason=longest).status_code == 202
    unlock = api.call("POST", "/accounts/alice/unlock", api.token_headers("root"), json={"reason": longest})

    assert unlock.status_code == 204
    assert [event.reason for event in api.events()].count(longest) == 2


@pytest.mark.parametrize(
    "model",
    [CreateRunRequest, VerifyRunRequest, ApplyRunRequest, ApproveRunRequest, CancelRunRequest, ResumeRunRequest],
)
def test_every_run_request_of_the_client_limits_its_reason_to_512_characters(model: type[BaseModel]) -> None:
    reason = model.model_json_schema()["properties"]["reason"]["anyOf"][0]

    assert (reason["minLength"], reason["maxLength"]) == (1, 512)


def test_the_configuration_request_of_the_client_limits_its_reason_to_512_characters() -> None:
    reason = ConfigMutationRequest.model_json_schema()["properties"]["reason"]["anyOf"][0]

    assert (reason["minLength"], reason["maxLength"]) == (1, 512)


def test_a_reason_equal_to_the_password_of_admin_is_accepted_like_any_other_text(api: AccountApi) -> None:
    """The password of ``admin`` is in no check, so a reason cannot be used to test candidate passwords."""
    alice, root = api.token_headers("alice"), api.token_headers("root")

    api.register(reason=ADMIN_PASSWORD)
    run = api.create_run(alice, reason=ADMIN_PASSWORD)
    other_run = api.create_run(alice, reason="any other text")
    unlock = api.call("POST", "/accounts/alice/unlock", root, json={"reason": ADMIN_PASSWORD})

    assert (run.status_code, other_run.status_code) == (202, 202)
    assert unlock.status_code == 204
    assert [event.reason for event in api.events()].count(ADMIN_PASSWORD) >= 3
    assert api.sign_in("admin", ADMIN_PASSWORD).status_code == 200


@pytest.mark.parametrize(
    ("value", "secrets", "expected"),
    [
        ("review the plan", ("admin-token-canary",), True),
        ("uses admin-token-canary", ("admin-token-canary",), False),
        ("ifs_pat_", (), False),
        ("x ifs_ses_abc", (), False),
        ("IFS_PAT_upper", (), True),
        ("ifs pat", (), True),
        ("", (), True),
    ],
)
def test_is_free_text_refuses_a_known_secret_and_a_token_prefix(
    value: str, secrets: tuple[str, ...], *, expected: bool
) -> None:
    assert is_free_text(value, secrets) is expected
