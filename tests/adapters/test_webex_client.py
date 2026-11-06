"""Webex HTTP contract tests, with no real credentials or network traffic."""

from __future__ import annotations

import json
import traceback
from unittest.mock import Mock

import pytest
import requests
from rich.console import Console
from rich.traceback import Traceback

from infrahub_sync.adapters import webex_client
from infrahub_sync.adapters.webex_client import WebexClient, WebexHTTPError


def response(payload: object = None, status: int = 200, **headers: str) -> requests.Response:
    result = requests.Response()
    result.status_code = status
    result._content = json.dumps(payload if payload is not None else {"items": []}).encode()
    result.headers.update(headers)
    return result


@pytest.fixture(name="http")
def _http_fixture(monkeypatch: pytest.MonkeyPatch) -> Mock:
    monkeypatch.setenv("WEBEX_TOKEN", "invented-access-value")
    mock = Mock(return_value=response())
    monkeypatch.setattr(requests, "request", mock)
    monkeypatch.setattr("infrahub_sync.adapters.webex_client.time.sleep", Mock())
    return mock


@pytest.fixture(name="service")
def _service_fixture(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    monkeypatch.setenv("WEBEX_CLIENT_ID", "invented-client-value")
    monkeypatch.setenv("WEBEX_CLIENT_SECRET", "invented-secret-value")
    monkeypatch.setenv("WEBEX_REFRESH_TOKEN", "invented-refresh-value")
    return {"auth_method": "service_app"}


def test_get_and_pagination_through_empty_page(http: Mock) -> None:
    http.side_effect = [
        response({"items": [{"id": "1"}]}, Link='<https://webexapis.com/v1/locations?max=100&after=one>; rel="next"'),
        response({"items": []}, Link='</v1/locations?max=100&after=two>; rel="next"'),
        response({"items": [{"id": "2"}]}),
    ]
    pages = WebexClient({}).pages("/v1/locations", {"max": 100, "orgId": "example-org"})
    assert [obj["id"] for page in pages for obj in page["items"]] == ["1", "2"]
    assert http.call_count == 3
    assert http.call_args_list[1].kwargs["params"] == {"orgId": "example-org"}
    assert all(call.args[0] == "GET" and call.kwargs["allow_redirects"] is False for call in http.call_args_list)
    assert http.call_args_list[0].kwargs["headers"]["Authorization"] == "Bearer invented-access-value"


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE", "HEAD"])
def test_mutations_rejected(method: str, http: Mock) -> None:
    with pytest.raises(ValueError, match="only GET"):
        WebexClient({}).request(method, "/v1/workspaces")
    http.assert_not_called()


def test_refresh_only_posts_to_oauth_path(http: Mock, service: dict[str, str]) -> None:
    http.side_effect = [response({"access_token": "invented-new-access", "expires_in": 3600}), response()]
    client = WebexClient(service)
    client.get("/v1/workspaces")
    assert [(call.args[0], call.args[1]) for call in http.call_args_list] == [
        ("POST", "https://webexapis.com/v1/access_token"),
        ("GET", "https://webexapis.com/v1/workspaces"),
    ]
    assert http.call_args_list[0].kwargs["data"]["grant_type"] == "refresh_token"
    with pytest.raises(ValueError, match="only GET"):
        client._send("POST", "https://webexapis.com/v1/workspaces")
    with pytest.raises(ValueError, match="only GET"):
        client.post("/v1/access_token")


def test_service_refreshes_once_after_401(http: Mock, service: dict[str, str]) -> None:
    token = {"access_token": "invented-new-access", "expires_in": 3600, "refresh_token": "invented-rotated"}
    http.side_effect = [response(token), response(status=401), response(token), response()]
    WebexClient(service).get("/v1/locations")
    assert [call.args[0] for call in http.call_args_list] == ["POST", "GET", "POST", "GET"]
    assert http.call_args_list[2].kwargs["data"]["refresh_token"] == "invented-rotated"  # noqa: S105 - invented fixture credential


def test_service_refreshes_after_expiry(http: Mock, service: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> None:
    clock = Mock(return_value=0)
    monkeypatch.setattr("infrahub_sync.adapters.webex_client.time.monotonic", clock)
    token = {"access_token": "invented-new-access", "expires_in": 10}
    http.side_effect = [response(token), response(), response(token), response()]
    client = WebexClient(service)
    client.get("/v1/locations")
    clock.return_value = 11
    client.get("/v1/locations")
    assert [call.args[0] for call in http.call_args_list] == ["POST", "GET", "POST", "GET"]


@pytest.mark.parametrize(
    ("status", "reason"), [(401, "authentication"), (403, "permission"), (400, "query"), (302, "rejected")]
)
def test_status_errors_exclude_raw_body(status: int, reason: str, http: Mock) -> None:
    http.return_value = response({"message": "invented-access-value"}, status)
    with pytest.raises(WebexHTTPError, match=reason) as exc:
        WebexClient({}).get("/v1/locations")
    assert "invented-access-value" not in str(exc.value)
    assert "/v1/locations" in str(exc.value)
    assert http.call_count == 1


@pytest.mark.parametrize(
    "failure", [requests.Timeout("invented-access-value"), requests.ConnectionError("invented-access-value")]
)
def test_network_failure_suppresses_unsafe_chain(failure: Exception, http: Mock) -> None:
    http.side_effect = failure
    with pytest.raises(ValueError, match=r"Webex.*locations.*bounded") as exc:
        WebexClient({"retries": 1}).get("/v1/locations")
    assert http.call_count == 2
    assert "invented-access-value" not in "".join(traceback.format_exception(exc.value))


def test_rate_limit_retry_after(http: Mock) -> None:
    http.side_effect = [response(status=429, **{"Retry-After": "7"}), response()]
    WebexClient({}).get("/v1/locations")

    webex_client.time.sleep.assert_called_once_with(7.0)  # ty: ignore[unresolved-attribute]


@pytest.mark.parametrize("status", [429, 503])
def test_retry_exhaustion(status: int, http: Mock) -> None:
    http.return_value = response(status=status)
    with pytest.raises(WebexHTTPError, match="exhausted"):
        WebexClient({"retries": 2}).get("/v1/locations")
    assert http.call_count == 3


@pytest.mark.parametrize(
    "url",
    [
        "https://external.example/v1/locations",
        "http://webexapis.com/v1/locations",
        "https://user:pass@webexapis.com/v1/locations",
    ],
)
def test_unsafe_page_url_rejected(url: str, http: Mock) -> None:
    http.return_value = response(Link=f'<{url}>; rel="next"')
    with pytest.raises(ValueError, match="origin"):
        WebexClient({}).pages("/v1/locations", {})
    assert http.call_count == 1


def test_cycle_rejected(http: Mock) -> None:
    http.return_value = response(Link='</v1/locations>; rel="next"')
    with pytest.raises(ValueError, match="cycle"):
        WebexClient({}).pages("/v1/locations", {})
    assert http.call_count == 1


def test_invalid_json_does_not_echo_body(http: Mock) -> None:
    item = response()
    item._content = b"invented-access-value"
    http.return_value = item
    with pytest.raises(ValueError, match="not valid JSON") as exc:
        WebexClient({}).get("/v1/locations")
    assert "invented-access-value" not in "".join(traceback.format_exception(exc.value))


def test_refresh_failure_has_no_tokens(http: Mock, service: dict[str, str]) -> None:
    http.return_value = response({"error": "invented-refresh-value invented-secret-value"}, 400)
    with pytest.raises(ValueError, match="authorize") as exc:
        WebexClient(service).get("/v1/locations")
    assert "invented-" not in "".join(traceback.format_exception(exc.value))


def test_missing_parameter_environment_fails_before_oauth(
    http: Mock, service: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("WEBEX_UNSET_EXAMPLE", raising=False)
    with pytest.raises(ValueError, match="WEBEX_UNSET_EXAMPLE"):
        WebexClient({**service, "resources": {"Example": {"params": {"orgId": {"env": "WEBEX_UNSET_EXAMPLE"}}}}})
    http.assert_not_called()


@pytest.mark.parametrize(
    "settings",
    [
        {"timeout": 0},
        {"retries": -1},
        {"page_size": 0},
        {"token": "invented-secret-value"},
        {"params": {"after": "cursor"}},
    ],
)
def test_invalid_settings(settings: dict, http: Mock) -> None:
    with pytest.raises(ValueError) as exc:
        WebexClient(settings)
    assert "invented-secret-value" not in str(exc.value)
    http.assert_not_called()


def _render_failure(client: WebexClient) -> str:
    rendered: Traceback | None = None
    try:
        client.get("/v1/locations")
    except ValueError as exc:
        rendered = Traceback.from_exception(type(exc), exc, exc.__traceback__, show_locals=True)
    else:
        pytest.fail("expected a Webex failure")
    assert rendered is not None
    console = Console(record=True, width=200)
    with console.capture() as captured:
        console.print(rendered)
    return captured.get()


@pytest.mark.parametrize("auth_method", ["token", "service_app"])
def test_transport_failure_hides_credentials_with_traceback_locals(
    auth_method: str, http: Mock, service: dict[str, str]
) -> None:
    """Protect credentials with the unchanged CLI's local-variable display."""
    http.side_effect = requests.Timeout("invented-access-value invented-refresh-value invented-secret-value")
    settings = {**service, "retries": 0} if auth_method == "service_app" else {"retries": 0}
    output = _render_failure(WebexClient(settings))
    assert "timeout or connection failure" in output
    assert "invented-access-value" not in output
    assert "invented-refresh-value" not in output
    assert "invented-secret-value" not in output


def test_invalid_refresh_payload_hides_credentials_with_traceback_locals(http: Mock, service: dict[str, str]) -> None:
    http.return_value = response({"access_token": "invented-new-access", "expires_in": "invented-secret-value"})
    output = _render_failure(WebexClient(service))
    assert "refresh response lacks" in output
    assert "invented-new-access" not in output
    assert "invented-secret-value" not in output
