"""Webex HTTP contract tests, with no real credentials or network traffic."""

from __future__ import annotations

import json
import traceback
from unittest.mock import Mock
from urllib.parse import parse_qs, quote, quote_plus, urlsplit

import pytest
import requests
from rich.console import Console
from rich.traceback import Traceback
from structlog.testing import capture_logs

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


@pytest.mark.parametrize("org_id", [None, "example-org"])
def test_get_and_pagination_through_empty_page(org_id: str | None, http: Mock) -> None:
    http.side_effect = [
        response(
            {"items": [{"id": "1"}]},
            Link='<https://webexapis.com/v1/locations?max=100&after=one&orgId=example-org>; rel="next"',
        ),
        response({"items": []}, Link='</v1/locations?max=100&after=two&orgId=example-org>; rel="next"'),
        response({"items": [{"id": "2"}]}),
    ]
    params: dict[str, object] = {"max": 100}
    if org_id is not None:
        params["orgId"] = org_id
    client = WebexClient({"org_id": org_id})
    pages = client.pages("/v1/locations", params)
    assert [obj["id"] for page in pages for obj in page["items"]] == ["1", "2"]
    assert http.call_count == 3
    assert http.call_args_list[0].kwargs["params"] == params
    assert http.call_args_list[1].kwargs["params"] is None
    assert client.options.org_id == org_id
    assert all(call.args[0] == "GET" and call.kwargs["allow_redirects"] is False for call in http.call_args_list)
    assert http.call_args_list[0].kwargs["headers"]["Authorization"] == "Bearer invented-access-value"


@pytest.mark.parametrize("explicit_filter", [None, "explicit-room"])
def test_endpoint_query_is_preserved_without_duplicate_parameters(explicit_filter: str | None, http: Mock) -> None:
    http.side_effect = [
        response(Link='</v1/devices?after=one>; rel="next"'),
        response(Link='</v1/devices?after=two&max=50>; rel="next"'),
        response(),
    ]
    params: dict[str, object] = {"max": 100, "before": "ignored", "after": "ignored"}
    if explicit_filter is not None:
        params["workspaceId"] = explicit_filter
    WebexClient({}).pages(
        "/v1/devices?workspaceId=first-room&workspaceId=url-room&max=10&before=ignored&after=ignored", params
    )
    queries = []
    for call in http.call_args_list:
        prepared = requests.Request("GET", call.args[1], params=call.kwargs["params"]).prepare()
        queries.append(parse_qs(urlsplit(prepared.url).query))
    fixed = {"workspaceId": [explicit_filter] if explicit_filter else ["first-room", "url-room"], "max": ["100"]}
    assert queries == [fixed, {**fixed, "after": ["one"]}, {**fixed, "after": ["two"], "max": ["50"]}]


def test_endpoint_organization_is_preserved_when_next_link_omits_it(http: Mock) -> None:
    http.side_effect = [response(Link='</v1/devices?after=one>; rel="next"'), response()]
    client = WebexClient({})
    client.pages("/v1/devices?orgId=example-org", {})
    queries = []
    for call in http.call_args_list:
        prepared = requests.Request("GET", call.args[1], params=call.kwargs["params"]).prepare()
        queries.append(parse_qs(urlsplit(prepared.url).query))
    assert queries == [{"orgId": ["example-org"]}, {"orgId": ["example-org"], "after": ["one"]}]
    assert client.options.org_id is None


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


@pytest.mark.parametrize(("settings", "header"), [({}, "301"), ({"max_retry_delay": 5}, "6")])
def test_retry_after_above_limit_fails_without_sleeping(settings: dict[str, int], header: str, http: Mock) -> None:
    http.return_value = response(status=429, **{"Retry-After": header})
    with pytest.raises(WebexHTTPError, match="exceeds max_retry_delay"):
        WebexClient(settings).get("/v1/locations")
    webex_client.time.sleep.assert_not_called()  # ty: ignore[unresolved-attribute]
    assert http.call_count == 1


@pytest.mark.usefixtures("http")
def test_request_events_are_debug_level() -> None:
    with capture_logs() as logs:
        WebexClient({}).get("/v1/locations")
    assert [event["log_level"] for event in logs if event["event"] == "Webex request"] == ["debug"]


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


def _render_settings_failure(settings: dict) -> tuple[str, str]:
    rendered: Traceback | None = None
    message = ""
    try:
        WebexClient(settings)
    except ValueError as exc:
        message = str(exc)
        rendered = Traceback.from_exception(type(exc), exc, exc.__traceback__, show_locals=True)
    else:
        pytest.fail("expected invalid Webex settings")
    assert rendered is not None
    console = Console(width=200)
    with console.capture() as captured:
        console.print(rendered)
    return message, captured.get()


@pytest.mark.parametrize("field", ["token_env", "client_id_env", "client_secret_env", "refresh_token_env"])
def test_credential_environment_names_are_not_resolved(field: str, http: Mock, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WEBEX_REVIEW_CREDENTIAL", "invented-review-secret")
    message, traceback_output = _render_settings_failure({field: {"env": "WEBEX_REVIEW_CREDENTIAL"}})
    assert "invalid source settings" in message
    assert "invented-review-secret" not in message
    assert "invented-review-secret" not in traceback_output
    http.assert_not_called()


def test_environment_references_resolve_only_in_supported_settings(http: Mock, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WEBEX_REVIEW_ORG", "example-org")
    monkeypatch.setenv("WEBEX_REVIEW_FILTER", "meetingRoom")
    client = WebexClient(
        {
            "org_id": {"env": "WEBEX_REVIEW_ORG"},
            "params": {"type": {"env": "WEBEX_REVIEW_FILTER"}},
            "resources": {"Workspace": {"params": {"type": {"env": "WEBEX_REVIEW_FILTER"}}}},
        }
    )
    assert client.options.org_id == "example-org"
    assert client.options.params == {"type": "meetingRoom"}
    assert client.options.resources["Workspace"].params == {"type": "meetingRoom"}
    http.assert_not_called()


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


@pytest.mark.parametrize("cursor", ["before", "after"])
@pytest.mark.parametrize("placement", ["params", "endpoint"])
def test_start_cursor_still_loads_all_pages(cursor: str, placement: str, http: Mock) -> None:
    http.side_effect = [
        response({"items": [{"id": "first"}]}, Link='</v1/devices?after=next&orgId=invented-org-a>; rel="next"'),
        response({"items": [{"id": "last"}]}),
    ]
    endpoint = f"/v1/devices?{cursor}=invented-start&max=1" if placement == "endpoint" else "/v1/devices"
    params = {cursor: "invented-start", "max": 1} if placement == "params" else {}
    pages = WebexClient({"params": {cursor: "invented-start"}}).pages(endpoint, params)
    assert [record["id"] for page in pages for record in page["items"]] == ["first", "last"]
    assert "invented-start" not in str(http.call_args_list)
    assert "after=next" in http.call_args_list[1].args[1]


@pytest.mark.parametrize(
    "settings",
    [
        {"org_id": "invented-org-a", "params": {"orgId": "invented-org-b"}},
        {"org_id": "invented-org-a", "resources": {"Device": {"params": {"orgId": "invented-org-b"}}}},
        {"params": {"orgId": "invented-org-a"}, "resources": {"Device": {"params": {"orgId": "invented-org-b"}}}},
        {"resources": {"Device": {"params": {"orgId": {"parent": "org"}}}}},
        {"resources": {"Device": {"params": {"orgId": ""}}}},
    ],
)
def test_conflicting_organizations_fail_before_http(settings: dict, http: Mock) -> None:
    with pytest.raises(ValueError, match="organization"):
        WebexClient(settings)
    http.assert_not_called()


@pytest.mark.parametrize("endpoint", ["/v1/devices?orgId=invented-org-b", "/v1/devices?orgId="])
def test_endpoint_organization_cannot_change_project_scope(endpoint: str, http: Mock) -> None:
    with pytest.raises(ValueError, match="organization"):
        WebexClient({"org_id": "invented-org-a"}).pages(endpoint, {})
    http.assert_not_called()


@pytest.mark.parametrize("org_id", [None, "invented-org-a"])
def test_pagination_cannot_switch_organizations(org_id: str | None, http: Mock) -> None:
    http.side_effect = [
        response(Link='</v1/devices?after=one&orgId=invented-org-a>; rel="next"'),
        response(Link='</v1/devices?after=two&orgId=invented-org-b>; rel="next"'),
        response(),
    ]
    with pytest.raises(ValueError, match="organization"):
        WebexClient({"org_id": org_id}).pages("/v1/devices", {})
    assert http.call_count == 2


@pytest.mark.parametrize("settings", [{"org_id": "invented-org-a"}, {"params": {"orgId": "invented-org-a"}}])
def test_first_link_cannot_change_configured_organization(settings: dict, http: Mock) -> None:
    http.return_value = response(Link='</v1/devices?after=one&orgId=invented-org-b>; rel="next"')
    with pytest.raises(ValueError, match="organization"):
        WebexClient(settings).pages("/v1/devices", {})
    assert http.call_count == 1


def test_pinned_organization_applies_across_resources(http: Mock) -> None:
    http.side_effect = [
        response(Link='</v1/locations?after=one&orgId=invented-org-a>; rel="next"'),
        response(),
        response(Link='</v1/devices?after=one&orgId=invented-org-b>; rel="next"'),
    ]
    client = WebexClient({})
    client.pages("/v1/locations", {})
    client._validate_organization()
    with pytest.raises(ValueError, match="organization"):
        client.pages("/v1/devices", {})
    assert http.call_count == 3


@pytest.mark.parametrize("query", ["orgId=", "orgId=%20", "orgId=invented-org-a&orgId=invented-org-b"])
def test_first_link_rejects_invalid_organization(query: str, http: Mock) -> None:
    http.return_value = response(Link=f'</v1/devices?after=one&{query}>; rel="next"')
    with pytest.raises(ValueError, match="organization"):
        WebexClient({}).pages("/v1/devices", {})
    assert http.call_count == 1


def test_pinned_organization_allows_links_without_org_id(http: Mock) -> None:
    http.side_effect = [
        response(Link='</v1/locations?after=one&orgId=invented-org-a>; rel="next"'),
        response(Link='</v1/locations?after=two>; rel="next"'),
        response(),
    ]
    assert len(WebexClient({}).pages("/v1/locations", {})) == 3
    assert http.call_count == 3


def test_pinned_organization_is_isolated_per_client(http: Mock) -> None:
    http.side_effect = [
        response(Link='</v1/devices?after=one&orgId=invented-org-a>; rel="next"'),
        response(),
        response(Link='</v1/devices?after=one&orgId=invented-org-b>; rel="next"'),
        response(),
    ]
    assert len(WebexClient({}).pages("/v1/devices", {})) == 2
    assert len(WebexClient({}).pages("/v1/devices", {})) == 2
    assert http.call_count == 4


@pytest.mark.parametrize(
    "failure",
    [
        "authentication",
        "permission",
        "timeout",
        "connection",
        "rate_limit",
        "server",
        "refresh_denied",
        "refresh_invalid",
        "refresh_timeout",
        "refresh_rate_limit",
    ],
)
def test_failure_matrix_names_source_resource_and_hides_credentials(
    failure: str, http: Mock, service: dict[str, str]
) -> None:
    failures = {
        "authentication": response({"message": "invented-access-value"}, 401),
        "permission": response({"message": "invented-secret-value"}, 403),
        "timeout": requests.Timeout("invented-access-value"),
        "connection": requests.ConnectionError("invented-access-value"),
        "rate_limit": response({"message": "invented-refresh-value"}, 429),
        "server": response({"message": "invented-secret-value"}, 503),
        "refresh_denied": response({"message": "invented-refresh-value"}, 400),
        "refresh_invalid": response({"access_token": "invented-new-access", "expires_in": "invented-secret-value"}),
        "refresh_timeout": requests.Timeout("invented-refresh-value"),
        "refresh_rate_limit": response({"message": "invented-secret-value"}, 429),
    }
    settings: dict[str, int | str] = {"retries": 0}
    if failure.startswith("refresh_"):
        settings.update(service)
        http.side_effect = [
            response({"access_token": "invented-new-access", "expires_in": 3600}),
            response(status=401),
            failures[failure],
        ]
    else:
        http.side_effect = [failures[failure]]
    with capture_logs() as logs:
        output = _render_failure(WebexClient(settings))
    assert "Webex" in output
    assert "/v1/locations" in output
    if failure.startswith("refresh_"):
        assert "token refresh failed" in output
        assert http.call_count == 3
    for credential in [
        "invented-access-value",
        "invented-new-access",
        "invented-client-value",
        "invented-secret-value",
        "invented-refresh-value",
    ]:
        assert credential not in output
        assert credential not in str(logs)


@pytest.mark.parametrize(
    "message",
    [
        "Invalid workspaceType: expected room or desk",
        "Invalid workspaceType; token=invented-access-value",
        "Invalid workspaceType; Authorization: Bearer unknown-bearer-value",
        "Invalid workspaceType; Authorization: Basic unknown-basic-value",
        "Invalid workspaceType; refresh_token=unknown-refresh-value; client_secret=unknown-secret-value",
        "Invalid workspaceType; https://user:unknown-password@host.example/path?token=unknown-url-value",
        "Invalid workspaceType; encoded=" + quote("invented-access-value", safe=""),
    ],
)
def test_query_400_repeats_safe_webex_message(message: str, http: Mock) -> None:
    http.return_value = response({"message": message, "other": "invented-access-value"}, 400)
    with capture_logs() as logs:
        output = _render_failure(WebexClient({}))
    assert "Webex" in output
    assert "/v1/locations" in output
    assert "HTTP 400" in output
    assert "Invalid workspaceType" in output
    for secret in [
        "invented-access-value",
        "unknown-bearer-value",
        "unknown-basic-value",
        "unknown-refresh-value",
        "unknown-secret-value",
        "unknown-password",
        "unknown-url-value",
    ]:
        assert secret not in output
        assert secret not in str(logs)


def test_query_400_redacts_prior_and_rotated_service_credentials(http: Mock, service: dict[str, str]) -> None:
    http.side_effect = [
        response({"access_token": "invented-old-access", "expires_in": 3600, "refresh_token": "invented-rotated"}),
        response(status=401),
        response({"access_token": "invented-new-access", "expires_in": 3600}),
        response(
            {
                "message": (
                    "Invalid query: invented-old-access invented-new-access invented-refresh-value "
                    "invented-rotated invented-client-value invented-secret-value"
                )
            },
            400,
        ),
    ]
    output = _render_failure(WebexClient(service))
    assert "Invalid query" in output
    assert "invented-" not in output


@pytest.mark.parametrize("encoding", ["plain", "percent", "form"])
def test_query_400_redacts_credentials_with_special_characters(
    encoding: str, http: Mock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WEBEX_TOKEN", "invented-token/with space+value")
    encodings = {
        "plain": "invented-token/with space+value",
        "percent": quote("invented-token/with space+value", safe=""),
        "form": quote_plus("invented-token/with space+value"),
    }
    http.return_value = response({"message": f"Invalid query; echoed value {encodings[encoding]}"}, 400)
    with capture_logs() as logs:
        output = _render_failure(WebexClient({}))
    assert "Invalid query" in output
    assert "invented-token" not in output
    assert "invented-token" not in str(logs)


@pytest.mark.parametrize("status", [429, 503])
@pytest.mark.parametrize("retry_after", [None, "invalid", "nan", "inf"])
def test_default_backoff_is_capped_until_retry_exhaustion(status: int, retry_after: str | None, http: Mock) -> None:
    headers = {} if retry_after is None else {"Retry-After": retry_after}
    http.return_value = response(status=status, **headers)
    with pytest.raises(WebexHTTPError, match=r"retries exhausted|retry budget exhausted"):
        WebexClient({"retries": 10}).get("/v1/locations")
    assert http.call_count == 11
    assert [call.args[0] for call in webex_client.time.sleep.call_args_list] == [  # ty: ignore[unresolved-attribute]
        1,
        2,
        4,
        8,
        16,
        32,
        64,
        128,
        256,
        300,
    ]


@pytest.mark.parametrize("failure", [requests.Timeout, requests.ConnectionError])
def test_transport_backoff_is_capped(failure: type[requests.RequestException], http: Mock) -> None:
    http.side_effect = failure("invented failure")
    with pytest.raises(ValueError, match="bounded retries"):
        WebexClient({"retries": 4, "max_retry_delay": 3}).get("/v1/locations")
    assert http.call_count == 5
    assert [call.args[0] for call in webex_client.time.sleep.call_args_list] == [1, 2, 3, 3]  # ty: ignore[unresolved-attribute]


def test_repeated_endpoint_query_values_survive_pagination(http: Mock) -> None:
    http.side_effect = [response(Link='</v1/example?after=one>; rel="next"'), response()]
    WebexClient({}).pages("/v1/example?type=a&type=b", {})
    queries = [
        parse_qs(urlsplit(requests.Request("GET", call.args[1], params=call.kwargs["params"]).prepare().url).query)
        for call in http.call_args_list
    ]
    assert queries == [{"type": ["a", "b"]}, {"type": ["a", "b"], "after": ["one"]}]


@pytest.mark.parametrize("org_query", ["orgId=example-org", "orgId=example-org&orgId=example-org"])
def test_endpoint_organization_with_repeated_values(org_query: str, http: Mock) -> None:
    WebexClient({}).pages(f"/v1/example?{org_query}", {})
    prepared = requests.Request("GET", http.call_args.args[1], params=http.call_args.kwargs["params"]).prepare()
    assert parse_qs(urlsplit(prepared.url).query)["orgId"] == ["example-org"] * org_query.count("orgId=")


def test_null_initial_cursors_allow_server_continuation(http: Mock) -> None:
    http.side_effect = [response(Link='</v1/example?after=one>; rel="next"'), response()]
    WebexClient({}).pages("/v1/example?before=ignored", {"before": None, "after": None})
    assert urlsplit(http.call_args_list[1].args[1]).query == "after=one"
