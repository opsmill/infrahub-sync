"""Requests without a token and the anonymous access settings (spec 023 FR-012 to FR-019).

The resolution table is in `data-model.md`; the order of the checks is research R8.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import pytest

pytest.importorskip("prefect")
pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from infrahub_sync.service import serve
from infrahub_sync.service.auth import AnonymousAccess, Principal, anonymous_refusal
from infrahub_sync.service.settings import ANONYMOUS_ACTOR_ENV, ANONYMOUS_HOSTS_ENV, WEB_DIRECTORY_ENV, anonymous_actor
from infrahub_sync.service.site import create_site
from tests.service.web_fixtures import (
    DEFAULT_HOSTS,
    INLINE_SCRIPT,
    OPERATOR_TOKEN,
    TOKENS,
    build_api,
    set_principals,
    write_web_build,
)

if TYPE_CHECKING:
    from pathlib import Path

    from infrahub_sync.product_store import ProductProjection

LOCAL = "127.0.0.1:8001"


def _site(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, anonymous_actor_name: str | None = "web-ui"
) -> tuple[TestClient, ProductProjection]:
    api, projection = build_api(monkeypatch, tmp_path, anonymous_actor=anonymous_actor_name)
    return TestClient(create_site(api, web_directory=None), base_url=f"http://{LOCAL}"), projection


def _me(client: TestClient, **headers: str) -> tuple[int, dict[str, Any]]:
    response = client.get("/api/me", headers=headers)
    return response.status_code, response.json()


# --- The resolution table ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Origin": f"http://{LOCAL}"},
        {"Host": "LOCALHOST:8001"},
        {"Host": "[::1]:8001"},
        {"Host": "127.0.0.1"},
        {"Host": "[::1]:8001", "Origin": "http://[::1]:8001"},
        {"Host": "localhost", "Origin": "http://localhost"},
        {"Host": "localhost", "Origin": "https://localhost:443"},
    ],
    ids=["no-origin", "same-origin", "host-case", "ipv6", "no-port", "ipv6-origin", "default-port", "https-port"],
)
def test_a_request_without_a_token_from_an_allowed_host_acts_as_the_anonymous_principal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, headers: dict[str, str]
) -> None:
    client, _projection = _site(monkeypatch, tmp_path)

    status, body = _me(client, **headers)

    assert status == 200
    assert body["actor"] == "web-ui"


@pytest.mark.parametrize(
    "headers",
    [
        {"Origin": "http://evil.example"},
        {"Origin": "null"},
        {"Origin": "http://127.0.0.1:9000"},
        {"Host": "[::1]:8001", "Origin": "http://[::1]:9000"},
        {"Origin": "not a url"},
    ],
    ids=["other-site", "null", "other-port", "ipv6-other-port", "unparseable"],
)
def test_a_request_without_a_token_from_another_origin_is_refused_with_403(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, headers: dict[str, str]
) -> None:
    client, _projection = _site(monkeypatch, tmp_path)

    status, body = _me(client, **headers)

    assert status == 403
    assert body["error"]["code"] == "cross-origin-refused"


@pytest.mark.parametrize(
    "headers",
    [
        {"Host": "rebound.evil.example:8001"},
        {"Host": "rebound.evil.example:8001", "Origin": "http://rebound.evil.example:8001"},
    ],
    ids=["rebound-host", "rebound-same-origin"],
)
def test_a_request_without_a_token_through_a_host_that_is_not_allowed_is_refused_with_401(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, headers: dict[str, str]
) -> None:
    client, _projection = _site(monkeypatch, tmp_path)

    status, body = _me(client, **headers)

    assert status == 401
    assert body["error"]["code"] == "unauthenticated"


@pytest.mark.parametrize(
    "headers",
    [{}, {"Host": "rebound.evil.example:8001"}, {"Origin": "http://evil.example"}],
    ids=["local", "rebound-host", "other-origin"],
)
def test_a_bearer_token_that_resolves_acts_as_its_principal_whatever_the_host_and_origin(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, headers: dict[str, str]
) -> None:
    client, _projection = _site(monkeypatch, tmp_path)

    status, body = _me(client, Authorization=f"Bearer {OPERATOR_TOKEN}", **headers)

    assert status == 200
    assert body["actor"] == "operator"


@pytest.mark.parametrize(
    "authorization",
    ["Bearer wrong-token-0000000001", "Basic b3BlcmF0b3I6c2VjcmV0", "Bearer", ""],
    ids=["refused-token", "basic", "empty-bearer", "empty-header"],
)
def test_an_authorization_header_that_does_not_resolve_never_acts_as_the_anonymous_principal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, authorization: str
) -> None:
    client, _projection = _site(monkeypatch, tmp_path)

    status, body = _me(client, Authorization=authorization)

    assert status == 401
    assert body["error"]["code"] == "unauthenticated"


def test_with_anonymous_access_off_a_request_without_a_token_is_refused_with_401(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    client, _projection = _site(monkeypatch, tmp_path, anonymous_actor_name=None)

    status, _body = _me(client)

    assert status == 401


def test_an_open_route_needs_no_token_and_no_allowed_host(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    client, _projection = _site(monkeypatch, tmp_path)

    assert client.get("/api/version", headers={"Host": "rebound.evil.example:8001"}).status_code == 200


@pytest.mark.parametrize(
    ("headers", "reason"),
    [
        ({"Origin": "http://evil.example"}, "cross-origin-refused"),
        ({"Host": "rebound.evil.example:8001"}, "anonymous-host-not-allowed"),
    ],
    ids=["cross-origin", "host"],
)
def test_each_refusal_is_audited_with_its_reason_and_the_api_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, headers: dict[str, str], reason: str
) -> None:
    client, projection = _site(monkeypatch, tmp_path)

    _me(client, **headers)

    events = [event for event in projection.audit_events() if event.outcome == "refused-authentication"]
    assert [(event.operation, event.reason) for event in events] == [("/api/me", reason)]


@pytest.mark.parametrize(
    ("host", "origin", "expected"),
    [
        (None, None, "anonymous-host-not-allowed"),
        ("", None, "anonymous-host-not-allowed"),
        ("bad host:port:x", None, "anonymous-host-not-allowed"),
        (None, "http://127.0.0.1:8001", "cross-origin-refused"),
        ("127.0.0.1:8001", "http://127.0.0.1:8001", None),
    ],
)
def test_anonymous_refusal_handles_missing_and_malformed_headers(
    host: str | None, origin: str | None, expected: str | None
) -> None:
    assert anonymous_refusal(host, origin, DEFAULT_HOSTS) == expected


# --- Settings and the start-up line (FR-017, FR-018, FR-019) ------------------------------------


def _resolve_anonymous_access(monkeypatch: pytest.MonkeyPatch) -> AnonymousAccess | None:
    return serve.resolve_anonymous_access(set_principals(monkeypatch))


def test_an_actor_that_is_not_a_principal_stops_start_up_without_its_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ANONYMOUS_ACTOR_ENV, "nobody-canary")

    with pytest.raises(ValueError, match=r"INFRAHUB_SYNC_SERVICE_ANONYMOUS_ACTOR must name an actor") as caught:
        _resolve_anonymous_access(monkeypatch)

    message = str(caught.value)
    assert "nobody-canary" not in message
    assert not any(token in message for token in TOKENS)


def test_an_empty_host_list_stops_start_up_naming_the_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ANONYMOUS_ACTOR_ENV, "web-ui")
    monkeypatch.setenv(ANONYMOUS_HOSTS_ENV, " , ")

    with pytest.raises(ValueError, match=r"^INFRAHUB_SYNC_SERVICE_ANONYMOUS_HOSTS must name at least one host$"):
        _resolve_anonymous_access(monkeypatch)


@pytest.mark.parametrize("entry", ["sync.example.net:8001", "[::1]", "localhost:80"])
def test_a_host_with_a_port_or_brackets_stops_start_up_naming_the_setting(
    monkeypatch: pytest.MonkeyPatch, entry: str
) -> None:
    monkeypatch.setenv(ANONYMOUS_ACTOR_ENV, "web-ui")
    monkeypatch.setenv(ANONYMOUS_HOSTS_ENV, f"localhost,{entry}")

    with pytest.raises(ValueError, match=r"^INFRAHUB_SYNC_SERVICE_ANONYMOUS_HOSTS must list host names without a port"):
        _resolve_anonymous_access(monkeypatch)


@pytest.mark.parametrize(
    "hosts",
    [frozenset(), frozenset({"LocalHost"}), frozenset({"[::1]"}), frozenset({"localhost:8001"})],
    ids=["empty", "upper-case", "brackets", "port"],
)
def test_anonymous_access_refuses_hosts_that_could_never_match(hosts: frozenset[str]) -> None:
    with pytest.raises(ValueError, match=r"^allowed_hosts must hold"):
        AnonymousAccess(principal=Principal(actor="web-ui"), allowed_hosts=hosts)


def test_build_app_reads_anonymous_access_and_the_web_directory_from_the_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    set_principals(monkeypatch)
    monkeypatch.setenv(ANONYMOUS_ACTOR_ENV, "web-ui")
    monkeypatch.setenv(WEB_DIRECTORY_ENV, str(write_web_build(tmp_path / "web")))
    projection = build_api(monkeypatch, tmp_path / "store")[1]
    client = TestClient(serve.build_app(projection_factory=lambda: projection), base_url=f"http://{LOCAL}")

    assert _me(client) == (200, {"actor": "web-ui", "administrator": False, "approval_required": False})
    assert INLINE_SCRIPT in client.get("/", headers={"Accept": "text/html"}).text


@pytest.mark.parametrize("value", ["", "   "])
def test_an_empty_actor_turns_anonymous_access_off(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv(ANONYMOUS_ACTOR_ENV, value)

    assert anonymous_actor() is None
    assert _resolve_anonymous_access(monkeypatch) is None


def test_allowed_hosts_are_trimmed_and_lowercased(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ANONYMOUS_ACTOR_ENV, " web-ui ")
    monkeypatch.setenv(ANONYMOUS_HOSTS_ENV, " Sync.Example.NET ,localhost")

    access = _resolve_anonymous_access(monkeypatch)

    assert access is not None
    assert access.principal.actor == "web-ui"
    assert access.allowed_hosts == frozenset({"sync.example.net", "localhost"})


def test_a_principal_that_is_not_an_administrator_logs_one_info_line(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv(ANONYMOUS_ACTOR_ENV, "web-ui")
    caplog.set_level(logging.INFO, logger=serve.logger.name)

    _resolve_anonymous_access(monkeypatch)

    assert [(record.levelname, record.getMessage()) for record in caplog.records] == [
        (
            "INFO",
            'anonymous access is on: requests without a token act as "web-ui" from hosts 127.0.0.1, ::1, localhost',
        )
    ]


def test_an_administrator_principal_is_allowed_with_one_warning(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv(ANONYMOUS_ACTOR_ENV, "admin")
    caplog.set_level(logging.INFO, logger=serve.logger.name)

    access = _resolve_anonymous_access(monkeypatch)

    assert access is not None
    assert access.principal.administrator
    assert [(record.levelname, record.getMessage()) for record in caplog.records] == [
        (
            "WARNING",
            'anonymous access is on: requests without a token act as administrator "admin" '
            "from hosts 127.0.0.1, ::1, localhost",
        )
    ]
    assert not any(token in caplog.text for token in TOKENS)


def test_no_line_is_logged_when_anonymous_access_is_off(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.delenv(ANONYMOUS_ACTOR_ENV, raising=False)
    caplog.set_level(logging.INFO, logger=serve.logger.name)

    _resolve_anonymous_access(monkeypatch)

    assert caplog.records == []
