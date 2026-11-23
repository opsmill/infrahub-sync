"""Cisco SD-WAN Manager HTTP contract tests, with no real credentials or network traffic."""

from __future__ import annotations

import json
from unittest.mock import Mock

import pytest
import requests
from rich.console import Console
from rich.traceback import Traceback
from structlog.testing import capture_logs

from infrahub_sync.adapters.ciscosdwan_client import CiscosdwanClient, CiscosdwanError, CiscosdwanHTTPError

LOGIN_VALUE = "invented-login-value"
URL = "https://manager.example.com"


def response(
    payload: object = None, status: int = 200, *, text: str | None = None, content_type: str = "application/json"
) -> requests.Response:
    result = requests.Response()
    result.status_code = status
    body = text if text is not None else json.dumps(payload if payload is not None else {"data": []})
    result._content = body.encode()
    result.encoding = "utf-8"
    result.headers["Content-Type"] = content_type
    return result


def login_ok() -> list[requests.Response]:
    return [response(text=""), response(text="invented-xsrf-value", content_type="text/plain")]


def login_page() -> requests.Response:
    return response(text="<html><head><title>Cisco Catalyst SD-WAN</title></head></html>", content_type="text/html")


@pytest.fixture(name="http")
def _http_fixture(monkeypatch: pytest.MonkeyPatch) -> Mock:
    monkeypatch.delenv("CISCO_SDWAN_URL", raising=False)
    monkeypatch.setenv("CISCO_SDWAN_USERNAME", "invented-user")
    monkeypatch.setenv("CISCO_SDWAN_PASSWORD", LOGIN_VALUE)
    mock = Mock()
    monkeypatch.setattr(requests.Session, "request", mock)
    monkeypatch.setattr("infrahub_sync.adapters.ciscosdwan_client.time.sleep", Mock())
    return mock


def client(**settings: object) -> CiscosdwanClient:
    return CiscosdwanClient({"url": URL, **settings})


def test_login_then_get_uses_form_login_and_xsrf_token(http: Mock) -> None:
    http.side_effect = [*login_ok(), response({"data": [{"uuid": "a"}]})]
    sdwan = client()
    assert sdwan.records("device") == [{"uuid": "a"}]
    login, token, data = http.call_args_list
    assert login.args == ("POST", f"{URL}/j_security_check")
    assert login.kwargs["data"] == {"j_username": "invented-user", "j_password": LOGIN_VALUE}
    assert token.args == ("GET", f"{URL}/dataservice/client/token")
    assert data.args == ("GET", f"{URL}/dataservice/device")
    assert all(call.kwargs["allow_redirects"] is False for call in http.call_args_list)
    assert sdwan._session is not None
    assert sdwan._session.headers["X-XSRF-TOKEN"] == "invented-xsrf-value"
    assert sdwan.request_count == 3


@pytest.mark.parametrize(
    "rejection",
    [
        response({"error": {"message": "Login Error", "details": " invented-user"}}),
        login_page(),
        response(text="", status=401),
    ],
)
def test_rejected_login_names_variables_not_values(rejection: requests.Response, http: Mock) -> None:
    http.side_effect = [rejection] * 4
    with pytest.raises(CiscosdwanError, match="CISCO_SDWAN_USERNAME and CISCO_SDWAN_PASSWORD") as exc:
        client(retries=0).records("device")
    assert LOGIN_VALUE not in str(exc.value)
    assert "invented-user" not in str(exc.value)


def test_login_without_token_endpoint_continues(http: Mock) -> None:
    http.side_effect = [response(text=""), response(text="", status=404), response({"data": []})]
    sdwan = client()
    assert sdwan.records("device") == []
    assert sdwan._session is not None
    assert "X-XSRF-TOKEN" not in sdwan._session.headers


def test_login_page_as_token_rejects_session(http: Mock) -> None:
    http.side_effect = [response(text=""), login_page()]
    with pytest.raises(CiscosdwanError, match="rejected the new session"):
        client().records("device")


@pytest.mark.parametrize("expired", [login_page(), response(status=401), response(status=403)])
def test_expired_session_logs_in_once_more(expired: requests.Response, http: Mock) -> None:
    http.side_effect = [*login_ok(), expired, *login_ok(), response({"data": [{"id": "1"}]})]
    assert client().records("device") == [{"id": "1"}]
    assert [call.args[1].rsplit("/", 1)[-1] for call in http.call_args_list] == [
        "j_security_check",
        "token",
        "device",
        "j_security_check",
        "token",
        "device",
    ]


@pytest.mark.parametrize(
    ("final", "status", "reason"),
    [
        (login_page(), 401, "rejected the session"),
        (response(status=403), 403, "lacks permission"),
        (response(status=404), 404, "endpoint not found"),
        (
            response({"error": {"message": "Invalid  query", "details": "invented-user"}}, status=400),
            400,
            "Invalid query",
        ),
        (response(text="not json", status=400), 400, "check the mapping path"),
        (response(status=302), 302, "redirect refused"),
    ],
)
def test_http_failures_report_status_and_short_reason(
    final: requests.Response, status: int, reason: str, http: Mock
) -> None:
    expired = [login_page(), *login_ok()] if status in {401, 403} else []
    http.side_effect = [*login_ok(), *expired, final]
    with pytest.raises(CiscosdwanHTTPError, match=reason) as exc:
        client().records("device")
    assert exc.value.status == status
    assert "/dataservice/device" in str(exc.value)
    assert "invented-user" not in str(exc.value)


def test_page_info_pagination_follows_end_id(http: Mock) -> None:
    http.side_effect = [
        *login_ok(),
        response({"data": [{"id": 1}], "pageInfo": {"startId": "1", "endId": "10", "moreEntries": True}}),
        response({"data": [{"id": 2}], "pageInfo": {"startId": "11", "endId": "20", "moreEntries": True}}),
        response({"data": [], "pageInfo": {"startId": "21", "endId": "21", "moreEntries": False}}),
    ]
    records = client().records("data/device/state/CEdgeInterface?count=10")
    assert records == [{"id": 1}, {"id": 2}]
    params = [call.kwargs["params"] for call in http.call_args_list[2:]]
    assert params == [{"count": "10"}, {"count": "10", "startId": "10"}, {"count": "10", "startId": "20"}]


@pytest.mark.parametrize(
    ("page_info", "message"),
    [
        ({"endId": "10", "moreEntries": True}, "no new endId"),
        ({"moreEntries": True}, "no new endId"),
        ({"scrollId": "abc", "hasMoreData": True}, "scroll pagination is not supported"),
    ],
)
def test_pagination_stops_on_cycles_and_unsupported_scrolling(page_info: dict, message: str, http: Mock) -> None:
    http.side_effect = [*login_ok()] + [response({"data": [{"id": 1}], "pageInfo": page_info})] * 3
    with pytest.raises(CiscosdwanError, match=message):
        client().records("data/device/state/Interface")


@pytest.mark.parametrize(
    ("payload", "records"),
    [
        ({"data": [{"id": "1"}]}, [{"id": "1"}]),
        ({"header": {}, "data": {"version": "20.18.2.1"}}, [{"version": "20.18.2.1"}]),
        ([{"id": "1"}, {"id": "2"}], [{"id": "1"}, {"id": "2"}]),
        ({"version": "20.18.2.1"}, [{"version": "20.18.2.1"}]),
    ],
)
def test_response_shapes(payload: object, records: list, http: Mock) -> None:
    http.side_effect = [*login_ok(), response(payload)]
    assert client().records("client/about") == records


@pytest.mark.parametrize("payload", [{"data": ["text"]}, {"data": "text"}, ["text"]])
def test_non_object_records_are_rejected(payload: object, http: Mock) -> None:
    http.side_effect = [*login_ok(), response(payload)]
    with pytest.raises(CiscosdwanError, match="must contain JSON objects"):
        client().records("device")


def test_invalid_json_is_rejected(http: Mock) -> None:
    http.side_effect = [*login_ok(), response(text="{not json")]
    with pytest.raises(CiscosdwanError, match="not valid JSON"):
        client().records("device")


def test_server_errors_and_rate_limits_are_retried(http: Mock) -> None:
    rate_limited = response(status=429)
    rate_limited.headers["Retry-After"] = "2"
    http.side_effect = [*login_ok(), response(status=503), rate_limited, response({"data": [{"id": "1"}]})]
    assert client().records("device") == [{"id": "1"}]
    assert http.call_count == 5


def test_retry_after_above_limit_stops(http: Mock) -> None:
    rate_limited = response(status=429)
    rate_limited.headers["Retry-After"] = "600"
    http.side_effect = [*login_ok(), rate_limited]
    with pytest.raises(CiscosdwanHTTPError, match="exceeds max_retry_delay") as exc:
        client().records("device")
    assert exc.value.status == 429


def test_retries_are_bounded(http: Mock) -> None:
    http.side_effect = [*login_ok(), response(status=500), response(status=500)]
    with pytest.raises(CiscosdwanHTTPError, match="server retries exhausted"):
        client(retries=1).records("device")


def test_timeouts_are_retried_then_reported(http: Mock) -> None:
    http.side_effect = requests.Timeout("invented detail")
    with pytest.raises(CiscosdwanError, match="timeout or connection failure after 3 attempts") as exc:
        client(retries=2).records("device")
    assert "invented detail" not in str(exc.value)


def test_certificate_failure_explains_verify_ssl(http: Mock) -> None:
    http.side_effect = requests.exceptions.SSLError("invented detail")
    with pytest.raises(CiscosdwanError, match="verify_ssl: false"):
        client().records("device")


def test_verify_ssl_applies_to_the_session(http: Mock) -> None:
    http.side_effect = [*login_ok(), *login_ok()]
    assert client().login().verify is True
    assert client(verify_ssl=False).login().verify is False


def test_close_posts_logout_once(http: Mock) -> None:
    http.side_effect = [*login_ok(), response(text="", status=302)]
    sdwan = client()
    sdwan.login()
    sdwan.close()
    sdwan.close()
    assert http.call_args_list[-1].args == ("POST", f"{URL}/logout")
    assert http.call_count == 3
    assert sdwan._session is None


def test_close_falls_back_to_get_logout_on_older_managers(http: Mock) -> None:
    http.side_effect = [*login_ok(), response(text="", status=405), response(text="", status=200)]
    sdwan = client()
    sdwan.login()
    sdwan.close()
    assert http.call_args_list[-1].args == ("GET", f"{URL}/logout")
    assert "nocache" in http.call_args_list[-1].kwargs["params"]


def test_close_failure_is_logged_not_raised(http: Mock) -> None:
    http.side_effect = [*login_ok(), requests.ConnectionError("invented detail")]
    sdwan = client()
    sdwan.login()
    with capture_logs() as logs:
        sdwan.close()
    assert any("logout failed" in entry["event"] for entry in logs)
    assert sdwan._session is None


@pytest.mark.parametrize("method", ["post", "patch", "put"])
def test_mutation_helpers_are_rejected(method: str, http: Mock) -> None:
    with pytest.raises(CiscosdwanError, match="only GET"):
        getattr(client(), method)("device", data={"name": "x"})
    http.assert_not_called()


def test_delete_is_rejected(http: Mock) -> None:
    with pytest.raises(CiscosdwanError, match="only GET"):
        client().delete("device")
    http.assert_not_called()


def test_get_returns_one_payload(http: Mock) -> None:
    http.side_effect = [*login_ok(), response({"data": {"version": "20.18.2.1"}})]
    assert client().get("client/about", params={"x": "1"}) == {"data": {"version": "20.18.2.1"}}
    assert http.call_args_list[-1].kwargs["params"] == {"x": "1"}


@pytest.mark.parametrize(
    ("endpoint", "path", "params"),
    [
        ("device", "/dataservice/device", {}),
        ("/dataservice/device", "/dataservice/device", {}),
        ("dataservice/v1/config-group", "/dataservice/v1/config-group", {}),
        (" data/device/state/Interface?count=500 ", "/dataservice/data/device/state/Interface", {"count": "500"}),
    ],
)
def test_resource_path_accepts_dataservice_paths(endpoint: str, path: str, params: dict) -> None:
    assert CiscosdwanClient.resource_path(endpoint) == (path, params)


@pytest.mark.parametrize(
    "endpoint", ["", "/", "/dataservice/", "https://other.example.com/dataservice/device", "../logout", "device#x"]
)
def test_resource_path_rejects_other_locations(endpoint: str) -> None:
    with pytest.raises(CiscosdwanError, match=r"must be a path under /dataservice/"):
        CiscosdwanClient.resource_path(endpoint)


@pytest.mark.parametrize(
    ("settings", "message"),
    [
        ({"url": None}, "set the url setting or the CISCO_SDWAN_URL"),
        ({"url": "http://manager.example.com"}, "HTTPS origin"),
        ({"url": "https://manager.example.com/dataservice"}, "HTTPS origin"),
        ({"url": "https://user:pass@manager.example.com"}, "HTTPS origin"),
        ({"timeout": 0}, "invalid source settings: timeout"),
        ({"unknown": True}, "invalid source settings: unknown"),
    ],
)
def test_invalid_settings_fail_before_requests(settings: dict, message: str, http: Mock) -> None:
    with pytest.raises(CiscosdwanError, match=message):
        CiscosdwanClient({"url": URL, **settings})
    http.assert_not_called()


def test_url_environment_variable_replaces_setting(monkeypatch: pytest.MonkeyPatch, http: Mock) -> None:
    monkeypatch.setenv("CISCO_SDWAN_URL", "https://env-manager.example.com:8443/")
    assert client().base_url == "https://env-manager.example.com:8443"
    http.assert_not_called()


def test_missing_credentials_name_the_variable(monkeypatch: pytest.MonkeyPatch, http: Mock) -> None:
    monkeypatch.delenv("CISCO_SDWAN_PASSWORD")
    with pytest.raises(CiscosdwanError, match="CISCO_SDWAN_PASSWORD is not set"):
        client()
    with pytest.raises(CiscosdwanError, match="OTHER_PASSWORD is not set"):
        client(password_env="OTHER_PASSWORD")  # noqa: S106 - a variable name, not a value
    http.assert_not_called()


def test_logs_exclude_credentials_and_tokens(http: Mock) -> None:
    http.side_effect = [*login_ok(), response({"data": [{"id": "1"}]}), response(text="", status=302)]
    sdwan = client()
    with capture_logs() as logs:
        sdwan.records("device")
        sdwan.close()
    text = json.dumps(logs, default=str)
    assert LOGIN_VALUE not in text
    assert "invented-xsrf-value" not in text
    assert any(entry["event"] == "Cisco SD-WAN resource fetched" for entry in logs)


def render_failure(sdwan: CiscosdwanClient) -> str:
    """Render the failure the way the CLI would with local-variable display turned on."""
    with pytest.raises(CiscosdwanError) as exc:
        sdwan.records("device")
    rendered = Traceback.from_exception(exc.type, exc.value, exc.tb, show_locals=True)
    console = Console(record=True, width=200)
    with console.capture() as captured:
        console.print(rendered)
    return captured.get()


@pytest.mark.parametrize(
    "failure",
    [
        requests.Timeout("invented detail"),
        requests.exceptions.SSLError("invented detail"),
        [response({"error": {"message": "Login Error"}})],
        [*login_ok(), response(status=500)],
    ],
    ids=["timeout", "certificate", "rejected-login", "server-error"],
)
def test_tracebacks_with_locals_exclude_credentials(failure: object, http: Mock) -> None:
    http.side_effect = failure
    output = render_failure(client(retries=0))
    assert "Cisco SD-WAN" in output
    assert LOGIN_VALUE not in output
    assert "invented-xsrf-value" not in output
