"""The client methods of the stored credentials: each calls its route and prints no value (spec 027).

The client runs on the base install, so these cases use a mock transport; the routes themselves
are tested in ``tests/service/test_credential_routes.py``.
"""

from __future__ import annotations

import json
import traceback
from datetime import datetime, timezone
from typing import Any

import httpx
import pytest

from infrahub_sync.client import (
    APIError,
    ClientInputError,
    CredentialPage,
    CredentialResource,
    ProtocolError,
    SyncClient,
)

URL = "https://sync.example.test"
BEARER = "bearer-value-1"
TOKEN_VALUE = "  credential-token-canary  "  # noqa: S105 - a canary; its spaces must be kept.
PASSWORD_VALUE = "credential-password-canary"  # noqa: S105 - a canary that must never be printed.
NOW = datetime(2026, 11, 20, 12, tzinfo=timezone.utc)
VERSION = {"server_version": "3.0.0", "api_versions": ["v3-unstable"], "stability": "unstable"}


def _credential(name: str = "netbox-lab", **extra: object) -> dict[str, object]:
    return {
        "name": name,
        "description": "",
        "fields": ["token"],
        "revision": 1,
        "created_by": "root",
        "created_at": NOW.isoformat(),
        "updated_by": "root",
        "updated_at": NOW.isoformat(),
        **extra,
    }


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

    def body(self) -> Any:  # noqa: ANN401
        return json.loads(self.request.content)


def _client(recorder: _Recorder) -> SyncClient:
    return SyncClient(URL, BEARER, transport=httpx.MockTransport(recorder))


def test_list_credentials_reads_one_page_with_offset_and_limit() -> None:
    recorder = _Recorder(httpx.Response(200, json={"items": [_credential()], "next_offset": 1}))

    page = _client(recorder).list_credentials(offset=0, limit=1)

    assert isinstance(page, CredentialPage)
    assert [item.name for item in page.items] == ["netbox-lab"]
    assert page.next_offset == 1
    assert recorder.request.method == "GET"
    assert recorder.request.url.path == "/api/credentials"
    assert dict(recorder.request.url.params) == {"offset": "0", "limit": "1"}


def test_iter_credentials_follows_next_offset() -> None:
    pages = iter(
        [
            httpx.Response(200, json={"items": [_credential("a-lab")], "next_offset": 1}),
            httpx.Response(200, json={"items": [_credential("b-lab")], "next_offset": None}),
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/version":
            return httpx.Response(200, json=VERSION)
        return next(pages)

    client = SyncClient(URL, BEARER, transport=httpx.MockTransport(handler))

    assert [item.name for item in client.iter_credentials()] == ["a-lab", "b-lab"]


def test_get_credential_reads_one_credential() -> None:
    recorder = _Recorder(httpx.Response(200, json=_credential()))

    credential = _client(recorder).get_credential("netbox-lab")

    assert isinstance(credential, CredentialResource)
    assert credential.fields == ("token",)
    assert recorder.request.url.path == "/api/credentials/netbox-lab"


def test_create_credential_sends_the_values_as_given_with_an_idempotency_key() -> None:
    recorder = _Recorder(httpx.Response(201, json=_credential(description="lab")))

    credential = _client(recorder).create_credential(
        "netbox-lab", token=TOKEN_VALUE, description="lab", idempotency_key="key-1"
    )

    assert credential.revision == 1
    assert recorder.request.method == "POST"
    assert recorder.request.url.path == "/api/credentials"
    assert recorder.request.headers["Idempotency-Key"] == "key-1"
    sent_as_given = recorder.body() == {"name": "netbox-lab", "description": "lab", "token": TOKEN_VALUE}
    assert sent_as_given


def test_a_change_without_a_key_gets_a_new_key() -> None:
    recorder = _Recorder(httpx.Response(201, json=_credential()))

    _client(recorder).create_credential("netbox-lab", token=TOKEN_VALUE)

    assert recorder.request.headers["Idempotency-Key"]


def test_set_credential_value_sends_the_whole_new_set() -> None:
    recorder = _Recorder(httpx.Response(200, json=_credential(fields=["password", "username"], revision=2)))

    credential = _client(recorder).set_credential_value(
        "netbox-lab", username="sync", password=PASSWORD_VALUE, idempotency_key="key-2"
    )

    assert credential.revision == 2
    assert recorder.request.method == "PUT"
    assert recorder.request.url.path == "/api/credentials/netbox-lab/value"
    sent_as_given = recorder.body() == {"username": "sync", "password": PASSWORD_VALUE}
    assert sent_as_given


def test_set_credential_description_sends_the_description_only() -> None:
    recorder = _Recorder(httpx.Response(200, json=_credential(description="the lab")))

    credential = _client(recorder).set_credential_description("netbox-lab", "the lab", idempotency_key="key-3")

    assert credential.description == "the lab"
    assert recorder.request.method == "PATCH"
    assert recorder.request.url.path == "/api/credentials/netbox-lab"
    assert recorder.body() == {"description": "the lab"}


@pytest.mark.parametrize("name", ["NetBox", "", "a/b", "netbox lab", 5])
def test_a_name_outside_the_pattern_is_refused_before_any_request(name: object) -> None:
    recorder = _Recorder(httpx.Response(200, json=_credential()))

    with pytest.raises(ClientInputError):
        _client(recorder).get_credential(name)  # ty: ignore[invalid-argument-type]

    assert recorder.requests == []


def test_a_value_that_is_not_text_is_refused_and_named_by_its_argument_only() -> None:
    recorder = _Recorder(httpx.Response(201, json=_credential()))

    with pytest.raises(ClientInputError) as raised:
        _client(recorder).create_credential("netbox-lab", token=5)  # ty: ignore[invalid-argument-type]

    assert raised.value.argument == "token"
    assert recorder.requests == []


def test_a_refusal_prints_no_value() -> None:
    message = "the fields are not accepted"
    refusal = {"error": {"code": "credential-fields-invalid", "message": message, "status": 422}}
    recorder = _Recorder(httpx.Response(422, json=refusal))

    with pytest.raises(APIError) as raised:
        _client(recorder).create_credential("netbox-lab", token=TOKEN_VALUE, password=PASSWORD_VALUE)

    printed = "".join(traceback.format_exception(raised.value)) + repr(raised.value)
    leaked = TOKEN_VALUE.strip() in printed or PASSWORD_VALUE in printed
    assert not leaked
    assert raised.value.code == "credential-fields-invalid"


def _in_use(details: object) -> httpx.Response:
    error = {"code": "credential-in-use", "message": "the credential is in use", "status": 409, "details": details}
    return httpx.Response(409, json={"error": error})


def test_delete_credential_sends_the_deletion_with_an_idempotency_key() -> None:
    recorder = _Recorder(httpx.Response(204))

    deleted = _client(recorder).delete_credential("netbox-lab", idempotency_key="key-4")

    assert deleted is None
    assert recorder.request.method == "DELETE"
    assert recorder.request.url.path == "/api/credentials/netbox-lab"
    assert recorder.request.headers["Idempotency-Key"] == "key-4"
    assert recorder.request.content == b""


def test_delete_credential_without_a_key_gets_a_new_key() -> None:
    recorder = _Recorder(httpx.Response(204))

    _client(recorder).delete_credential("netbox-lab")

    assert recorder.request.headers["Idempotency-Key"]


def test_a_deletion_refused_because_the_credential_is_in_use_names_the_configurations_and_schedules() -> None:
    recorder = _Recorder(_in_use({"configurations": ["c-2", "c-1"], "schedules": ["from-netbox-nightly"]}))

    with pytest.raises(APIError) as raised:
        _client(recorder).delete_credential("netbox-lab")

    assert (raised.value.status, raised.value.code) == (409, "credential-in-use")
    assert raised.value.configurations == ("c-2", "c-1")
    assert raised.value.schedules == ("from-netbox-nightly",)
    # The text of the answer is not kept.
    assert "the credential is in use" not in repr(raised.value) + str(raised.value)


@pytest.mark.parametrize(
    "details",
    [
        None,
        {},
        {"configurations": ["c-1"]},
        {"configurations": "c-1", "schedules": []},
        {"configurations": [1], "schedules": []},
        {"configurations": [], "schedules": [None]},
    ],
)
def test_an_in_use_refusal_without_its_two_lists_breaks_the_protocol(details: object) -> None:
    recorder = _Recorder(_in_use(details))

    with pytest.raises(ProtocolError):
        _client(recorder).delete_credential("netbox-lab")


def test_a_deletion_of_a_credential_that_does_not_exist_is_an_api_error() -> None:
    refusal = {"error": {"code": "credential-not-found", "message": "no such credential", "status": 404}}
    recorder = _Recorder(httpx.Response(404, json=refusal))

    with pytest.raises(APIError) as raised:
        _client(recorder).delete_credential("netbox-lab")

    assert (raised.value.status, raised.value.code) == (404, "credential-not-found")
    assert (raised.value.configurations, raised.value.schedules) == (None, None)


def test_a_deletion_of_a_name_outside_the_pattern_is_refused_before_any_request() -> None:
    recorder = _Recorder(httpx.Response(204))

    with pytest.raises(ClientInputError):
        _client(recorder).delete_credential("NetBox")

    assert recorder.requests == []
