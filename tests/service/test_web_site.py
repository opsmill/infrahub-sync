"""The service answers the HTTP routing contract of spec 017: the API under /api, the web interface elsewhere."""

from __future__ import annotations

import base64
import hashlib
import json
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

import pytest
import structlog.testing

pytest.importorskip("prefect")
pytest.importorskip("fastapi")

from fastapi import FastAPI
from fastapi.testclient import TestClient

from infrahub_sync.product_store import local_product_projection
from infrahub_sync.service.app import create_app
from infrahub_sync.service.auth import PRINCIPALS_ENV, EnvironmentPrincipalResolver
from infrahub_sync.service.service import RunService
from infrahub_sync.service.web import (
    ASSET_CACHE_CONTROL,
    INDEX_CACHE_CONTROL,
    NO_WEB_INTERFACE_MESSAGE,
    NOT_FOUND_MESSAGE,
    create_site,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

    import httpx

INLINE_SCRIPT = '\n      document.documentElement.dataset.theme = localStorage.getItem("theme") ?? "light";\n    '
INDEX_HTML = (
    "<!doctype html>\n<html>\n  <head>\n"
    f"    <script>{INLINE_SCRIPT}</script>\n"
    '    <script type="module" crossorigin src="/assets/app-abc.js"></script>\n'
    '  </head>\n  <body><div id="root"></div></body>\n</html>\n'
)
ASSET_JS = "console.log('app');\n"
FAVICON = b"\x00\x00\x01\x00icon"
OUTSIDE_CONTENT = "outside-the-web-directory"
HTML = {"Accept": "text/html,application/xhtml+xml,*/*;q=0.8"}
JSON = {"Accept": "application/json"}


@pytest.fixture
def web_directory(tmp_path: Path) -> Path:
    directory = tmp_path / "web"
    (directory / "assets").mkdir(parents=True)
    (directory / "index.html").write_text(INDEX_HTML, encoding="utf-8")
    (directory / "assets" / "app-abc.js").write_text(ASSET_JS, encoding="utf-8")
    (directory / "favicon.ico").write_bytes(FAVICON)
    (tmp_path / "secret.txt").write_text(OUTSIDE_CONTENT, encoding="utf-8")
    return directory


def _small_api(events: list[str] | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_application: FastAPI) -> AsyncIterator[None]:  # noqa: RUF029
        if events is not None:
            events.append("start")
        yield
        if events is not None:
            events.append("stop")

    api = FastAPI(lifespan=lifespan)

    @api.get("/version")
    def version() -> dict[str, str]:
        return {"server_version": "test"}

    @api.post("/runs")
    def create_run() -> dict[str, str]:
        return {"run_id": "r-1"}

    return api


@pytest.fixture
def site(web_directory: Path) -> TestClient:
    return TestClient(create_site(_small_api(), web_directory))


@pytest.fixture
def bare_site() -> TestClient:
    return TestClient(create_site(_small_api(), None))


def _assert_envelope(response: httpx.Response) -> None:
    assert response.status_code == 404
    assert response.headers["content-type"] == "application/json"
    error = response.json()["error"]
    assert (error["code"], error["message"], error["status"]) == ("not-found", NOT_FOUND_MESSAGE, 404)


def _assert_index(response: httpx.Response) -> None:
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["cache-control"] == INDEX_CACHE_CONTROL
    assert response.text == INDEX_HTML


def _assert_security_headers(response: httpx.Response) -> None:
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]


# Row 1: a method other than GET or HEAD.
@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
def test_a_write_method_outside_api_answers_with_the_envelope(site: TestClient, method: str) -> None:
    response = site.request(method, "/runs", headers=HTML)

    _assert_envelope(response)
    _assert_security_headers(response)


@pytest.mark.parametrize("method", ["TRACE", "PROPFIND", "MADEUP"])
@pytest.mark.parametrize("headers", [HTML, JSON])
def test_any_other_method_outside_api_answers_with_the_envelope_and_the_security_headers(
    site: TestClient, method: str, headers: dict[str, str]
) -> None:
    response = site.request(method, "/runs", headers=headers)

    _assert_envelope(response)
    _assert_security_headers(response)


def test_another_method_under_api_still_reaches_the_api(site: TestClient) -> None:
    response = site.request("TRACE", "/api/version")

    assert response.status_code == 405
    assert "content-security-policy" not in response.headers


def test_post_runs_outside_api_does_not_reach_the_api(site: TestClient) -> None:
    _assert_envelope(site.post("/runs", headers=JSON))
    assert site.post("/api/runs").json() == {"run_id": "r-1"}


# Row 2: no built web interface.
def test_without_a_web_interface_a_browser_gets_plain_text(bare_site: TestClient) -> None:
    response = bare_site.get("/runs/abc", headers=HTML)

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("text/plain")
    assert response.text == NO_WEB_INTERFACE_MESSAGE
    _assert_security_headers(response)


@pytest.mark.parametrize("path", ["/", "/version", "/index.html", "/assets/app-abc.js"])
def test_without_a_web_interface_a_program_gets_the_envelope(bare_site: TestClient, path: str) -> None:
    response = bare_site.get(path, headers=JSON)

    _assert_envelope(response)
    _assert_security_headers(response)


def test_without_a_web_interface_the_api_still_answers(bare_site: TestClient) -> None:
    assert bare_site.get("/api/version").json() == {"server_version": "test"}
    _assert_envelope(bare_site.post("/runs", headers=HTML))


# Row 3: a regular file inside the built directory.
def test_index_html_is_sent_with_no_cache(site: TestClient) -> None:
    response = site.get("/index.html", headers=JSON)

    _assert_index(response)
    _assert_security_headers(response)


def test_an_asset_is_sent_with_an_immutable_cache(site: TestClient) -> None:
    response = site.get("/assets/app-abc.js", headers=JSON)

    assert response.status_code == 200
    assert response.text == ASSET_JS
    assert response.headers["cache-control"] == ASSET_CACHE_CONTROL
    _assert_security_headers(response)


def test_another_top_level_file_is_sent_whatever_accept_says(site: TestClient) -> None:
    response = site.get("/favicon.ico", headers={"Accept": "image/*"})

    assert response.status_code == 200
    assert response.content == FAVICON
    assert "cache-control" not in response.headers
    _assert_security_headers(response)


def test_head_answers_a_file_without_a_body(site: TestClient) -> None:
    response = site.head("/assets/app-abc.js")

    assert response.status_code == 200
    assert response.content == b""
    assert response.headers["cache-control"] == ASSET_CACHE_CONTROL
    _assert_security_headers(response)


def test_a_matching_etag_answers_304(site: TestClient) -> None:
    etag = site.get("/assets/app-abc.js").headers["etag"]

    response = site.get("/assets/app-abc.js", headers={"If-None-Match": etag})

    assert response.status_code == 304
    _assert_security_headers(response)


def test_a_matching_modification_date_answers_304(site: TestClient) -> None:
    last_modified = site.get("/index.html").headers["last-modified"]

    response = site.get("/index.html", headers={"If-Modified-Since": last_modified})

    assert response.status_code == 304
    _assert_security_headers(response)


# Row 4: a path under assets/ that names no file.
def test_a_missing_asset_answers_with_the_envelope_even_for_a_browser(site: TestClient) -> None:
    response = site.get("/assets/missing.js", headers=HTML)

    _assert_envelope(response)
    _assert_security_headers(response)


@pytest.mark.parametrize(
    "path",
    ["/assets/", "//assets/", "//assets/missing.js", "/./assets/missing.js", "/runs/../assets/missing.js"],
)
def test_a_missing_asset_behind_extra_path_segments_answers_with_the_envelope(site: TestClient, path: str) -> None:
    # The absolute form keeps httpx from reading `//assets` as a host name.
    response = site.get(f"http://testserver{path}", headers=HTML)

    _assert_envelope(response)
    _assert_security_headers(response)


# Row 5: a browser asking for a page path.
@pytest.mark.parametrize("path", ["/", "/runs", "/runs/abc", "/configs/c-1/versions", "/api"])
def test_a_browser_gets_the_entry_page_for_a_page_path(site: TestClient, path: str) -> None:
    response = site.get(path, headers=HTML)

    _assert_index(response)
    _assert_security_headers(response)


def test_head_of_a_page_path_answers_the_entry_page_headers(site: TestClient) -> None:
    response = site.head("/runs/abc", headers=HTML)

    assert response.status_code == 200
    assert response.headers["cache-control"] == INDEX_CACHE_CONTROL
    _assert_security_headers(response)


# Row 6: anything else.
@pytest.mark.parametrize("path", ["/version", "/status", "/runs", "/api", "/favicon.png"])
def test_a_program_outside_api_gets_the_envelope(site: TestClient, path: str) -> None:
    response = site.get(path, headers=JSON)

    _assert_envelope(response)
    _assert_security_headers(response)


# Never a file outside the built directory.
@pytest.mark.parametrize(
    "path",
    [
        "/%2e%2e/secret.txt",
        "/assets/%2e%2e/%2e%2e/secret.txt",
        "/assets/..%2f..%2fsecret.txt",
        "/%2e%2e%2fsecret.txt",
    ],
)
@pytest.mark.parametrize("headers", [HTML, JSON])
def test_a_traversal_path_never_returns_the_outside_file(site: TestClient, path: str, headers: dict[str, str]) -> None:
    response = site.get(path, headers=headers)

    assert OUTSIDE_CONTENT not in response.text
    assert response.status_code in {200, 404}
    if response.status_code == 200:
        assert response.text == INDEX_HTML


@pytest.mark.parametrize("path", ["/linked.txt", "/assets/linked.js"])
@pytest.mark.parametrize("headers", [HTML, JSON])
def test_a_symbolic_link_to_a_file_outside_the_directory_is_never_served(
    web_directory: Path, path: str, headers: dict[str, str]
) -> None:
    outside = web_directory.parent / "secret.txt"
    (web_directory / "linked.txt").symlink_to(outside)
    (web_directory / "assets" / "linked.js").symlink_to(outside)
    client = TestClient(create_site(_small_api(), web_directory))

    response = client.get(path, headers=headers)

    assert OUTSIDE_CONTENT not in response.text
    assert response.status_code in {200, 404}
    if response.status_code == 200:
        assert response.text == INDEX_HTML


@pytest.mark.parametrize("path", ["/a%00b", "/assets/a%00b", "/" + "a" * 5000, "/assets/" + "b" * 5000])
@pytest.mark.parametrize("headers", [HTML, JSON])
def test_a_null_byte_or_a_very_long_path_never_answers_500(
    site: TestClient, path: str, headers: dict[str, str]
) -> None:
    response = site.get(path, headers=headers)

    assert response.status_code in {200, 404}
    _assert_security_headers(response)


def test_an_entry_page_that_is_not_utf_8_is_treated_as_no_web_interface(tmp_path: Path) -> None:
    directory = tmp_path / "web"
    directory.mkdir()
    (directory / "index.html").write_bytes(b"<!doctype html><html>\xff\xfe\x80</html>")

    with structlog.testing.capture_logs() as logs:
        client = TestClient(create_site(_small_api(), directory))

    assert [(log["log_level"], log["error_type"], log["path"]) for log in logs] == [
        ("warning", "UnicodeDecodeError", str((directory / "index.html").resolve()))
    ]
    assert "0xff" not in repr(logs)

    response = client.get("/", headers=HTML)
    assert response.status_code == 404
    assert response.text == NO_WEB_INTERFACE_MESSAGE
    _assert_envelope(client.get("/", headers=JSON))
    assert client.get("/api/version").json() == {"server_version": "test"}


def test_an_encoded_traversal_under_assets_reaches_the_route_undecoded(site: TestClient) -> None:
    # A browser request to this address gets the envelope (row 4), not the entry page, which shows
    # the client sent the encoded dots and the route saw a path under assets/.
    _assert_envelope(site.get("/assets/%2e%2e/%2e%2e/secret.txt", headers=HTML))


# Security headers.
def test_the_csp_holds_the_hash_of_the_inline_script(site: TestClient) -> None:
    digest = base64.b64encode(hashlib.sha256(INLINE_SCRIPT.encode("utf-8")).digest()).decode("ascii")

    policy = site.get("/", headers=HTML).headers["content-security-policy"]

    assert f"script-src 'self' 'sha256-{digest}';" in policy
    assert "default-src 'self'" in policy
    assert "font-src 'self' https://rsms.me" in policy
    assert "connect-src 'self'" in policy


def test_the_csp_allows_inline_styles_but_never_inline_scripts(site: TestClient) -> None:
    # React Aria inserts a `<style>` element at run time, so styles need 'unsafe-inline'
    # (the fallback of research R-3a, seen in a browser); scripts are allowed by hash only.
    directives = dict(
        directive.strip().split(" ", 1)
        for directive in site.get("/", headers=HTML).headers["content-security-policy"].split(";")
    )

    assert directives["style-src"] == "'self' 'unsafe-inline'"
    assert "unsafe-inline" not in directives["script-src"]


def test_the_api_answers_are_not_given_the_web_headers(site: TestClient) -> None:
    response = site.get("/api/version")

    assert response.json() == {"server_version": "test"}
    assert "content-security-policy" not in response.headers


def test_no_answer_sets_a_cookie(site: TestClient) -> None:
    for response in (site.get("/", headers=HTML), site.get("/api/version"), site.post("/runs")):
        assert "set-cookie" not in response.headers


# Lifespan.
def test_the_service_lifespan_runs_the_api_lifespan(web_directory: Path) -> None:
    events: list[str] = []

    with TestClient(create_site(_small_api(events), web_directory)) as client:
        assert events == ["start"]
        assert client.get("/api/version").status_code == 200

    assert events == ["start", "stop"]


# The real Sync API under the mount.
@pytest.fixture
def real_site(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, web_directory: Path) -> TestClient:
    monkeypatch.setenv(PRINCIPALS_ENV, json.dumps({"owner": {"token": "web-site-owner-token"}}))
    monkeypatch.delenv("INFRAHUB_SYNC_REQUIRE_APPROVAL", raising=False)
    resolver = EnvironmentPrincipalResolver.from_environment()
    projection = local_product_projection((tmp_path / "store").resolve())
    # No route these tests call reaches the orchestration.
    service = RunService(projection, object(), secrets=resolver.secret_values)  # ty: ignore[invalid-argument-type]
    return TestClient(create_site(create_app(service, resolver), web_directory))


def test_the_openapi_document_keeps_unprefixed_paths_and_declares_the_api_server(real_site: TestClient) -> None:
    document = real_site.get("/api/openapi.json").json()

    assert document["servers"] == [{"url": "/api"}]
    assert "/version" in document["paths"]
    assert "/runs" in document["paths"]
    assert not any(path.startswith("/api/") for path in document["paths"])


def test_the_api_documentation_page_reads_the_prefixed_openapi_document(real_site: TestClient) -> None:
    response = real_site.get("/api/docs")

    assert response.status_code == 200
    assert "/api/openapi.json" in response.text


def test_an_unknown_api_path_answers_with_the_api_envelope(real_site: TestClient) -> None:
    response = real_site.get("/api/nope")

    assert response.status_code == 404
    error = response.json()["error"]
    assert (error["code"], error["message"]) == ("not-found", "Not Found")


def test_the_real_api_answers_only_under_api(real_site: TestClient) -> None:
    assert real_site.get("/api/version").status_code == 200
    _assert_envelope(real_site.get("/version", headers=JSON))
    _assert_envelope(real_site.get("/me", headers={**JSON, "Authorization": "Bearer web-site-owner-token"}))
