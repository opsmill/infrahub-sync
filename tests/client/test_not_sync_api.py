"""The client reports an address that does not answer as a Sync API (spec 017 FR-023)."""

from __future__ import annotations

import json
from hashlib import sha256
from typing import TYPE_CHECKING

import httpx
import pytest

from infrahub_sync.client import CompatibilityError, NotSyncAPIError, SyncClient

if TYPE_CHECKING:
    from collections.abc import Callable

TOKEN = "token-secret"  # noqa: S105 - a fixed test credential.
BODY_MARKER = "body-marker-never-shown"
HTML = f"<!doctype html><html><body>{BODY_MARKER}</body></html>"
HTML_HEADERS = {"Content-Type": "text/html; charset=utf-8"}


def _version() -> dict[str, object]:
    return {"server_version": "3.0.0", "api_versions": ["v3-unstable"], "stability": "unstable"}


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> SyncClient:
    return SyncClient("http://127.0.0.1:8001", TOKEN, transport=httpx.MockTransport(handler))


def _assert_body_absent(error: NotSyncAPIError) -> None:
    for rendered in (str(error), repr(error), repr(vars(error))):
        assert BODY_MARKER not in rendered
        assert TOKEN not in rendered


def test_an_html_page_answering_version_is_not_a_sync_api() -> None:
    with pytest.raises(NotSyncAPIError) as raised:
        _client(lambda _request: httpx.Response(200, text=HTML, headers=HTML_HEADERS)).get_version()

    assert (raised.value.operation, raised.value.url, raised.value.status) == (
        "get_version",
        "http://127.0.0.1:8001/api/version",
        200,
    )
    _assert_body_absent(raised.value)


@pytest.mark.parametrize("status", [200, 502])
def test_an_html_answer_to_get_status_is_not_a_sync_api(status: int) -> None:
    with pytest.raises(NotSyncAPIError) as raised:
        _client(lambda _request: httpx.Response(status, text=HTML, headers=HTML_HEADERS)).get_status()

    assert (raised.value.operation, raised.value.url, raised.value.status) == (
        "get_status",
        "http://127.0.0.1:8001/api/status",
        status,
    )
    _assert_body_absent(raised.value)


@pytest.mark.parametrize("content_type", ["application/json; charset=utf-8", "application/problem+json"])
def test_a_json_media_type_with_parameters_or_a_json_suffix_is_accepted(content_type: str) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=json.dumps(_version()).encode(), headers={"Content-Type": content_type})

    version = _client(handler).get_version()

    assert version.api_versions == ("v3-unstable",)


def test_an_html_404_is_not_a_sync_api() -> None:
    with pytest.raises(NotSyncAPIError) as raised:
        _client(lambda _request: httpx.Response(404, text=HTML, headers=HTML_HEADERS)).list_configs()

    assert (raised.value.operation, raised.value.status) == ("get_version", 404)
    _assert_body_absent(raised.value)


def test_a_json_404_without_an_error_envelope_on_version_is_not_a_sync_api() -> None:
    with pytest.raises(NotSyncAPIError) as raised:
        _client(lambda _request: httpx.Response(404, json={"detail": "Not Found"})).get_version()

    assert (raised.value.operation, raised.value.url, raised.value.status) == (
        "get_version",
        "http://127.0.0.1:8001/api/version",
        404,
    )


def test_a_404_error_envelope_on_version_stays_a_compatibility_error() -> None:
    envelope = {"error": {"code": "not-found", "message": "the Sync API is served under /api", "status": 404}}

    with pytest.raises(CompatibilityError):
        _client(lambda _request: httpx.Response(404, json=envelope)).get_version()


def test_an_html_502_to_list_runs_names_the_operation_and_drops_the_query() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/version":
            return httpx.Response(200, json=_version())
        return httpx.Response(502, text=HTML, headers=HTML_HEADERS)

    with pytest.raises(NotSyncAPIError) as raised:
        _client(handler).list_runs(limit=5)

    assert (raised.value.operation, raised.value.url, raised.value.status) == (
        "list_runs",
        "http://127.0.0.1:8001/api/runs",
        502,
    )
    _assert_body_absent(raised.value)


def test_a_plain_text_artifact_with_a_valid_digest_still_succeeds() -> None:
    data = b"plain text artifact"
    headers = {"Content-Type": "text/plain", "Digest": f"sha-256={sha256(data).hexdigest()}"}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/version":
            return httpx.Response(200, json=_version())
        return httpx.Response(200, content=data, headers=headers)

    artifact = _client(handler).get_artifact("run-1", "artifact-1")

    assert (artifact.data, artifact.media_type) == (data, "text/plain")


def test_an_html_error_answer_to_get_artifact_is_not_a_sync_api() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/version":
            return httpx.Response(200, json=_version())
        return httpx.Response(404, text=HTML, headers=HTML_HEADERS)

    with pytest.raises(NotSyncAPIError) as raised:
        _client(handler).get_artifact("run-1", "artifact-1")

    assert (raised.value.operation, raised.value.status) == ("get_artifact", 404)
    _assert_body_absent(raised.value)
