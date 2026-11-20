"""The worker's call to the worker route (spec 027, research R6, critique E3 and E6).

``credential_client.release_run_credentials`` sends one request with the worker token, waits at
most 10 seconds, tries again three times after 1, 2 and 4 seconds when the Sync API cannot be
reached, and adds every released value to the redaction registry of the run before it returns.
A failure is a ``CredentialUnavailableError`` with a reason and no value.

Every value is a marker text. A check compares digests or a boolean, so a failed check prints no
value. No case waits: the function that waits is replaced by one that records the delays.
"""

from __future__ import annotations

import hashlib
import json
import ssl
from collections.abc import Callable
from typing import Any, cast

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("prefect")

import certifi
import httpx

from infrahub_sync.execution import NoRedactionScopeError, redaction_scope
from infrahub_sync.service import credential_client
from infrahub_sync.service.credential_client import CredentialUnavailableError, release_run_credentials
from infrahub_sync.service.settings import WORKER_API_URL_ENV, WORKER_TOKEN_ENV, WorkerSettings
from infrahub_sync.service.web import API_PREFIX

WORKER_TOKEN = "worker-token-of-the-environment-1"  # noqa: S105 - a test value, not a secret.
SOURCE_TOKEN = "client-marker-source-token-6a1f"  # noqa: S105 - a test value, not a secret.
DESTINATION_TOKEN = "client-marker-destination-token-3c9e"  # noqa: S105 - a test value, not a secret.
MARKERS = (SOURCE_TOKEN, DESTINATION_TOKEN)
RUN_ID = "run_0123456789abcdef"
FLOW_RUN_ID = "0d0b7c1e-5c0f-4c58-9d5e-6d0c1f0a7b11"
SETTINGS = WorkerSettings(api_url="http://sync-api:8000", token=WORKER_TOKEN)
URL = f"http://sync-api:8000/api/worker/runs/{RUN_ID}/credentials"
ANSWER = {
    "run_id": RUN_ID,
    "blocks": {
        "destination": {"name": "infrahub-main", "revision": 4, "fields": {"token": DESTINATION_TOKEN}},
        "source": {"name": "netbox-lab", "revision": 3, "fields": {"token": SOURCE_TOKEN}},
    },
}

Step = Callable[[httpx.Request], httpx.Response]


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _holds_a_value(text: str) -> bool:
    """Return whether the text holds a marker value; the caller asserts the boolean."""
    return any(marker in text for marker in MARKERS)


def _answer(status: int, body: object) -> Step:
    return lambda _request: httpx.Response(status, json=body)


def _refusal(status: int, code: str, details: dict[str, Any] | None = None) -> Step:
    error: dict[str, Any] = {"code": code, "message": f"refused, and a careless message holds {SOURCE_TOKEN}"}
    if details is not None:
        error["details"] = details
    return _answer(status, {"error": error})


def _raise(error: type[httpx.TransportError]) -> Step:
    def step(request: httpx.Request) -> httpx.Response:
        raise error("the transport failed", request=request)  # noqa: EM101, TRY003 - a test double.

    return step


class Api:
    """A Sync API that answers each request with the next step, and records what the client did."""

    def __init__(self, *steps: Step) -> None:
        self._steps = list(steps)
        self.requests: list[httpx.Request] = []
        self.sleeps: list[float] = []

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        step = self._steps[min(len(self.requests), len(self._steps)) - 1]
        return step(request)

    def release(self, settings: WorkerSettings | None = SETTINGS) -> dict[str, credential_client.ReleasedBlock]:
        return release_run_credentials(
            RUN_ID,
            FLOW_RUN_ID,
            settings=settings,
            sleep=self.sleeps.append,
            transport=httpx.MockTransport(self._handle),
        )

    def refused(self) -> CredentialUnavailableError:
        """Call inside a redaction scope and return the error, after checking that it holds no value."""
        with redaction_scope(), pytest.raises(CredentialUnavailableError) as raised:
            self.release()
        error = raised.value
        leaked = _holds_a_value(f"{error} {error!r} {error.args} {error.__cause__!r} {error.__context__!r}")
        assert not leaked
        return error


_UNREACHABLE: dict[str, Step] = {
    "connection-error": _raise(httpx.ConnectError),
    "connect-timeout": _raise(httpx.ConnectTimeout),
    "read-timeout": _raise(httpx.ReadTimeout),
    "502": _refusal(502, "bad-gateway"),
    "503": _refusal(503, "credential-key-not-configured"),
    "504": _refusal(504, "gateway-timeout"),
}


def test_the_request_names_the_route_the_execution_and_the_worker_token() -> None:
    api = Api(_answer(200, ANSWER))

    with redaction_scope():
        api.release()

    (request,) = api.requests
    assert request.method == "POST"
    assert str(request.url) == URL
    assert request.headers["Authorization"] == f"Bearer {WORKER_TOKEN}"
    assert "Idempotency-Key" not in request.headers
    assert json.loads(request.content) == {"flow_run_id": FLOW_RUN_ID}


def test_the_route_is_under_the_prefix_of_the_served_api() -> None:
    assert URL.startswith(f"http://sync-api:8000{API_PREFIX}/worker/")


def test_an_address_with_a_final_slash_gives_the_same_route() -> None:
    api = Api(_answer(200, ANSWER))

    with redaction_scope():
        api.release(WorkerSettings(api_url="http://sync-api:8000/", token=WORKER_TOKEN))

    assert str(api.requests[0].url) == URL


def test_the_request_waits_at_most_10_seconds() -> None:
    api = Api(_answer(200, ANSWER))

    with redaction_scope():
        api.release()

    assert api.requests[0].extensions["timeout"] == {"connect": 10.0, "read": 10.0, "write": 10.0, "pool": 10.0}


def test_the_released_blocks_are_returned() -> None:
    api = Api(_answer(200, ANSWER))

    with redaction_scope():
        blocks = api.release()

    assert sorted(blocks) == ["destination", "source"]
    assert (blocks["source"].name, blocks["source"].revision) == ("netbox-lab", 3)
    assert (blocks["destination"].name, blocks["destination"].revision) == ("infrahub-main", 4)
    assert _digest(blocks["source"].fields["token"]) == _digest(SOURCE_TOKEN)
    assert _digest(blocks["destination"].fields["token"]) == _digest(DESTINATION_TOKEN)
    shown = _holds_a_value(repr(blocks))
    assert not shown
    assert api.sleeps == []


def test_an_answer_without_a_block_releases_nothing() -> None:
    api = Api(_answer(200, {"run_id": RUN_ID, "blocks": {}}))

    with redaction_scope():
        assert api.release() == {}


def test_every_released_value_is_in_the_redaction_registry_before_the_client_returns() -> None:
    api = Api(_answer(200, ANSWER))

    with redaction_scope() as registry:
        api.release()
        registered = {_digest(value) for value in registry}

    assert {_digest(SOURCE_TOKEN), _digest(DESTINATION_TOKEN)} <= registered
    names_registered = {"netbox-lab", "infrahub-main", RUN_ID} & set(registry)
    assert not names_registered


def test_without_a_redaction_registry_no_request_is_sent() -> None:
    api = Api(_answer(200, ANSWER))

    with pytest.raises(NoRedactionScopeError):
        api.release()

    assert api.requests == []


@pytest.mark.parametrize("failure", sorted(_UNREACHABLE))
def test_an_unreachable_api_is_tried_again_after_1_2_and_4_seconds(failure: str) -> None:
    step = _UNREACHABLE[failure]
    api = Api(step, step, step, _answer(200, ANSWER))

    with redaction_scope():
        blocks = api.release()

    assert len(api.requests) == 4
    assert api.sleeps == [1.0, 2.0, 4.0]
    assert sorted(blocks) == ["destination", "source"]


@pytest.mark.parametrize("failure", sorted(_UNREACHABLE))
def test_after_the_third_retry_the_cause_is_api_unreachable(failure: str) -> None:
    api = Api(_UNREACHABLE[failure])

    error = api.refused()

    assert error.reason == "api-unreachable"
    assert len(api.requests) == 4
    assert api.sleeps == [1.0, 2.0, 4.0]


def test_a_retry_that_succeeds_stops_the_retries() -> None:
    api = Api(_raise(httpx.ConnectError), _answer(200, ANSWER))

    with redaction_scope():
        api.release()

    assert len(api.requests) == 2
    assert api.sleeps == [1.0]


@pytest.mark.parametrize(
    ("status", "code", "reason"),
    [
        (401, "unauthenticated", "worker-token-refused"),
        (401, "token-expired", "worker-token-refused"),
        (403, "forbidden", "worker-token-refused"),
        (404, "run-not-found", "release-refused"),
        (409, "execution-not-in-progress", "release-refused"),
        (422, "credential-not-found", "credential-missing"),
        (422, "credential-field-missing", "credential-field-missing"),
        (422, "credential-method-unknown", "release-refused"),
        (422, "request-invalid", "release-refused"),
        (500, "credential-undecryptable", "credential-undecryptable"),
        (500, "internal-error", "release-refused"),
    ],
)
def test_a_refusal_is_final_at_once_and_has_its_reason(status: int, code: str, reason: str) -> None:
    api = Api(_refusal(status, code))

    error = api.refused()

    assert error.reason == reason
    assert error.code == code
    assert len(api.requests) == 1
    assert api.sleeps == []


def test_a_refusal_names_the_block_the_credential_and_the_missing_fields() -> None:
    details = {"block": "source", "name": "netbox-lab", "fields": ["password", "username"]}
    api = Api(_refusal(422, "credential-field-missing", details))

    error = api.refused()

    assert (error.block, error.name, error.fields) == ("source", "netbox-lab", ("password", "username"))
    assert "netbox-lab" in str(error)
    assert "source" in str(error)


def test_details_that_are_not_a_block_a_name_and_field_names_are_dropped() -> None:
    # A credential name has lower-case letters, digits and hyphens only; this text has a space.
    not_a_name = f"Bearer {SOURCE_TOKEN}"
    details = {"block": not_a_name, "name": not_a_name, "fields": [not_a_name, "token"]}
    api = Api(_refusal(422, "credential-field-missing", details))

    error = api.refused()

    assert (error.block, error.name, error.fields) == (None, None, ("token",))


def test_a_refusal_that_is_not_an_error_envelope_has_no_code() -> None:
    api = Api(lambda _request: httpx.Response(403, text=f"<html>{SOURCE_TOKEN}</html>"))

    error = api.refused()

    assert error.reason == "worker-token-refused"
    assert error.code is None


def test_a_redirect_is_not_followed() -> None:
    api = Api(lambda _request: httpx.Response(307, headers={"Location": "http://elsewhere.example/"}))

    error = api.refused()

    assert error.reason == "release-refused"
    assert len(api.requests) == 1


@pytest.mark.parametrize(
    "body",
    [
        {"run_id": RUN_ID, "blocks": {"source": {"name": "netbox-lab", "fields": {"token": SOURCE_TOKEN}}}},
        {
            "run_id": RUN_ID,
            "blocks": {"source": {"name": "netbox-lab", "revision": "3", "fields": {"token": SOURCE_TOKEN}}},
        },
        {
            "run_id": RUN_ID,
            "blocks": {"store": {"name": "netbox-lab", "revision": 3, "fields": {"token": SOURCE_TOKEN}}},
        },
        {
            "run_id": RUN_ID,
            "blocks": {"source": {"name": "netbox-lab", "revision": 3, "fields": {"token": 7, "x": SOURCE_TOKEN}}},
        },
        {"run_id": RUN_ID, "blocks": [{"fields": {"token": SOURCE_TOKEN}}]},
        {"run_id": "run_another", "blocks": {}},
        ["not", "an", "object"],
    ],
)
def test_an_answer_that_is_not_the_contract_is_refused_and_its_values_are_registered(body: object) -> None:
    api = Api(_answer(200, body))

    with redaction_scope() as registry, pytest.raises(CredentialUnavailableError) as raised:
        api.release()
    registered = {_digest(value) for value in registry}

    assert raised.value.reason == "release-refused"
    assert raised.value.code == "answer-invalid"
    leaked = _holds_a_value(f"{raised.value} {raised.value!r} {raised.value.__cause__!r}")
    assert not leaked
    document = cast("dict[str, Any]", body) if isinstance(body, dict) else {}
    if isinstance(document.get("blocks"), dict) and SOURCE_TOKEN in json.dumps(body):
        assert _digest(SOURCE_TOKEN) in registered


def test_an_answer_that_is_not_json_is_refused_without_its_text() -> None:
    api = Api(lambda _request: httpx.Response(200, text=f'{{"blocks": {SOURCE_TOKEN}'))

    error = api.refused()

    assert (error.reason, error.code) == ("release-refused", "answer-invalid")
    assert error.__cause__ is None


def test_the_settings_are_read_from_the_environment_of_the_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(WORKER_API_URL_ENV, "http://sync-api:8000")
    monkeypatch.setenv(WORKER_TOKEN_ENV, WORKER_TOKEN)
    api = Api(_answer(200, ANSWER))

    with redaction_scope():
        api.release(None)

    assert str(api.requests[0].url) == URL
    assert api.requests[0].headers["Authorization"] == f"Bearer {WORKER_TOKEN}"


def test_the_ca_bundle_that_the_worker_installed_is_trusted(monkeypatch: pytest.MonkeyPatch) -> None:
    """``install_custom_ca_bundle`` names the bundle of ``INFRAHUB_SYNC_CA_DIRECTORY`` in ``SSL_CERT_FILE``."""
    monkeypatch.setenv("SSL_CERT_FILE", certifi.where())

    assert isinstance(credential_client.certificate_trust(), ssl.SSLContext)


def test_without_a_ca_bundle_the_public_roots_are_trusted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)

    assert credential_client.certificate_trust() is True


def test_a_ca_bundle_that_cannot_be_read_is_api_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SSL_CERT_FILE", "/nonexistent/infrahub-sync-ca-bundle.pem")
    api = Api(_answer(200, ANSWER))

    with redaction_scope(), pytest.raises(CredentialUnavailableError) as raised:
        release_run_credentials(RUN_ID, FLOW_RUN_ID, settings=SETTINGS, sleep=api.sleeps.append)

    assert raised.value.reason == "api-unreachable"
    assert api.sleeps == []
