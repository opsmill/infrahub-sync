"""The site: the Sync API under `/api` and the web interface at every other path (spec 023).

Research R1 (mount and lifespan), R3 (`app.frontend()`) and R4 (headers); `contracts/http-paths.md`.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
from typing import TYPE_CHECKING

import pytest

pytest.importorskip("prefect")
pytest.importorskip("fastapi")

from fastapi import APIRouter, Request
from fastapi.testclient import TestClient

from infrahub_sync.service.site import IMAGE_REFERENCE, create_site, inline_script_hashes
from tests.service.web_fixtures import (
    INLINE_SCRIPT,
    OPERATOR_TOKEN,
    RecordingReconciler,
    build_api,
    write_web_build,
)

if TYPE_CHECKING:
    from pathlib import Path

HTML = {"Accept": "text/html,application/xhtml+xml"}
JSON = {"Accept": "application/json"}
WEB_HEADERS = ("content-security-policy", "x-frame-options", "x-content-type-options", "cache-control")


@pytest.fixture
def web_directory(tmp_path: Path) -> Path:
    """A built web interface in a directory of its own."""
    return write_web_build(tmp_path / "web")


@pytest.fixture
def site(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, web_directory: Path) -> TestClient:
    """The site with a built web interface."""
    api, _projection = build_api(monkeypatch, tmp_path / "store")
    return TestClient(create_site(api, web_directory=web_directory))


@pytest.fixture
def site_without_web(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> TestClient:
    """The site of an installation from the Python package, which has no built web interface."""
    api, _projection = build_api(monkeypatch, tmp_path / "store")
    return TestClient(create_site(api, web_directory=None))


# --- The Sync API under /api (FR-007, FR-011) ---------------------------------------------------


def test_the_version_route_moves_under_api(site: TestClient) -> None:
    response = site.get("/api/version", headers=JSON)

    assert response.status_code == 200
    assert "server_version" in response.json()


def test_the_old_version_path_is_not_an_alias(site: TestClient) -> None:
    assert site.get("/version", headers=JSON).status_code == 404


def test_openapi_paths_stay_relative_to_api_and_name_api_as_the_server(site: TestClient) -> None:
    document = site.get("/api/openapi.json").json()

    assert "/runs/{run_id}" in document["paths"]
    assert not any(path.startswith("/api") for path in document["paths"])
    assert document["servers"] == [{"url": "/api"}]


def test_the_site_enters_the_sync_api_lifespan_so_liveness_checks_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, web_directory: Path
) -> None:
    reconciler = RecordingReconciler()
    api, _projection = build_api(monkeypatch, tmp_path / "store", reconciler=reconciler)

    with TestClient(create_site(api, web_directory=web_directory)) as client:
        client.portal.call(asyncio.wait_for, reconciler.started.wait(), 5)

    assert reconciler.iterations >= 1


def test_a_route_inside_the_mount_sees_the_full_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    api, _projection = build_api(monkeypatch, tmp_path / "store")
    router = APIRouter()

    @router.get("/echo-path")
    def echo_path(request: Request) -> dict[str, str]:
        return {"path": request.url.path}

    api.include_router(router)

    assert TestClient(create_site(api, web_directory=None)).get("/api/echo-path").json() == {"path": "/api/echo-path"}


def test_a_bearer_token_reaches_the_sync_api_through_the_mount(site: TestClient) -> None:
    response = site.get("/api/me", headers={"Authorization": f"Bearer {OPERATOR_TOKEN}"})

    assert response.status_code == 200
    assert response.json()["actor"] == "operator"


# --- The web interface (FR-001, FR-003, FR-004) ------------------------------------------------


@pytest.mark.parametrize("path", ["/", "/runs", "/runs/abc", "/configs/inventory"])
def test_a_page_path_that_accepts_html_returns_the_web_interface(site: TestClient, path: str) -> None:
    response = site.get(path, headers=HTML)

    assert response.status_code == 200
    assert INLINE_SCRIPT in response.text


def test_a_page_path_that_does_not_accept_html_returns_404(site: TestClient) -> None:
    assert site.get("/runs/abc", headers=JSON).status_code == 404


def test_a_hashed_asset_is_served(site: TestClient) -> None:
    response = site.get("/assets/app-abc123.js")

    assert response.status_code == 200
    assert "console.log" in response.text


def test_an_unknown_api_path_returns_the_envelope_never_the_web_interface(site: TestClient) -> None:
    response = site.get("/api/no-such-route", headers=HTML)

    assert response.status_code == 404
    assert response.headers["content-type"] == "application/json"
    assert response.json()["error"]["code"] == "not-found"


def test_a_write_to_a_page_path_returns_404(site: TestClient) -> None:
    assert site.post("/runs", headers=HTML).status_code == 404


# --- Headers of the web interface (FR-006, research R4) ----------------------------------------


def test_web_interface_responses_forbid_frames_and_foreign_scripts(site: TestClient) -> None:
    response = site.get("/", headers=HTML)
    digest = base64.b64encode(hashlib.sha256(INLINE_SCRIPT.encode()).digest()).decode()
    policy = response.headers["content-security-policy"]

    assert f"script-src 'self' 'sha256-{digest}'" in policy
    assert "frame-ancestors 'none'" in policy
    assert "default-src 'self'" in policy
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["x-content-type-options"] == "nosniff"


def test_the_page_is_revalidated_and_hashed_assets_are_cached(site: TestClient) -> None:
    assert site.get("/", headers=HTML).headers["cache-control"] == "no-cache"
    assert site.get("/assets/app-abc123.js").headers["cache-control"] == "public, max-age=31536000, immutable"


@pytest.mark.parametrize("headers", [HTML, JSON], ids=["page-request", "script-request"])
def test_a_missing_asset_is_never_cached_as_immutable(site: TestClient, headers: dict[str, str]) -> None:
    assert site.get("/assets/missing-0000.js", headers=headers).headers["cache-control"] == "no-cache"


def test_a_path_that_only_starts_with_api_returns_the_web_interface_with_its_headers(site: TestClient) -> None:
    response = site.get("/apix", headers=HTML)

    assert response.status_code == 200
    assert INLINE_SCRIPT in response.text
    assert "content-security-policy" in response.headers


def test_the_bare_api_path_redirects_into_the_sync_api(site: TestClient) -> None:
    response = site.get("/api", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"].endswith("/api/")


def test_a_method_that_a_route_does_not_accept_returns_the_envelope(site: TestClient) -> None:
    response = site.post("/api/version")

    assert response.status_code == 405
    assert response.json()["error"]["code"] == "method-not-allowed"


@pytest.mark.parametrize("path", ["/api/version", "/api/docs", "/api/no-such-route"])
def test_sync_api_responses_carry_none_of_the_web_interface_headers(site: TestClient, path: str) -> None:
    headers = site.get(path).headers

    assert not any(name in headers for name in WEB_HEADERS)


def test_inline_script_hashes_ignore_scripts_with_a_source() -> None:
    html = '<script>a()</script><script type="module" src="/x.js"></script><SCRIPT defer>b()</SCRIPT>'

    assert len(inline_script_hashes(html)) == 2


# --- Installations without a web interface (FR-005) ---------------------------------------------


def test_a_directory_without_index_html_stops_start_up_naming_the_setting(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    api, _projection = build_api(monkeypatch, tmp_path / "store")
    empty = tmp_path / "empty"
    empty.mkdir()

    with pytest.raises(ValueError, match=r"^INFRAHUB_SYNC_SERVICE_WEB_DIRECTORY must name a readable directory"):
        create_site(api, web_directory=empty)


def test_a_missing_directory_stops_start_up_naming_the_setting(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    api, _projection = build_api(monkeypatch, tmp_path / "store")

    with pytest.raises(ValueError, match=r"^INFRAHUB_SYNC_SERVICE_WEB_DIRECTORY must name a readable directory"):
        create_site(api, web_directory=tmp_path / "missing")


def test_without_a_web_interface_the_root_names_the_released_image(site_without_web: TestClient) -> None:
    response = site_without_web.get("/", headers=HTML)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert IMAGE_REFERENCE in response.text


def test_without_a_web_interface_other_paths_return_404_with_the_same_page(site_without_web: TestClient) -> None:
    response = site_without_web.get("/runs", headers=HTML)

    assert response.status_code == 404
    assert IMAGE_REFERENCE in response.text


@pytest.mark.parametrize("path", ["/", "/runs"])
def test_without_a_web_interface_the_page_carries_the_web_interface_headers(
    site_without_web: TestClient, path: str
) -> None:
    headers = site_without_web.get(path, headers=HTML).headers

    assert "frame-ancestors 'none'" in headers["content-security-policy"]
    assert headers["x-frame-options"] == "DENY"


def test_without_a_web_interface_the_sync_api_still_answers(site_without_web: TestClient) -> None:
    assert site_without_web.get("/api/version").status_code == 200
    assert site_without_web.get("/api/no-such-route").json()["error"]["code"] == "not-found"
