"""Infrahub clients for Sync's own records."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import httpx
import pytest

from infrahub_sync.platform import client as platform_client
from infrahub_sync.platform.client import (
    DefaultBranchUnavailableError,
    PlatformSettings,
    PlatformSettingsError,
    caller_client,
    default_branch,
    default_branch_sync,
    service_client,
    service_client_sync,
)

SERVICE_TOKEN = "service-token-0123456789"  # noqa: S105 - test value


def _settings(**values: str) -> PlatformSettings:
    return PlatformSettings.from_environment(
        {
            "INFRAHUB_SYNC_INFRAHUB_ADDRESS": "http://infrahub-server:8000/",
            "INFRAHUB_SYNC_INFRAHUB_TOKEN": SERVICE_TOKEN,
            **values,
        }
    )


def test_settings_strip_a_trailing_slash_and_never_render_the_token() -> None:
    settings = _settings()

    assert settings.address == "http://infrahub-server:8000"
    assert SERVICE_TOKEN not in repr(settings)


@pytest.mark.parametrize("missing", ["INFRAHUB_SYNC_INFRAHUB_ADDRESS", "INFRAHUB_SYNC_INFRAHUB_TOKEN"])
def test_a_missing_setting_is_named(missing: str) -> None:
    values = {"INFRAHUB_SYNC_INFRAHUB_ADDRESS": "http://x:8000", "INFRAHUB_SYNC_INFRAHUB_TOKEN": SERVICE_TOKEN}
    del values[missing]

    with pytest.raises(PlatformSettingsError, match=missing):
        PlatformSettings.from_environment(values)


def test_a_malformed_tls_flag_is_refused() -> None:
    with pytest.raises(PlatformSettingsError, match="true or false"):
        _settings(INFRAHUB_SYNC_INFRAHUB_TLS_INSECURE="maybe")


def test_the_sdk_never_fills_a_field_from_the_ambient_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INFRAHUB_USERNAME", "ambient-user")
    monkeypatch.setenv("INFRAHUB_PASSWORD", "ambient-password")
    monkeypatch.setenv("INFRAHUB_TLS_INSECURE", "true")

    config = _settings().sdk_config()

    assert config.api_token == SERVICE_TOKEN
    assert config.username is None
    assert config.password is None
    assert config.tls_insecure is False


def test_a_caller_client_presents_the_callers_token_not_the_service_token() -> None:
    caller_token = "caller-token-0123456789"  # noqa: S105 - test value
    client = caller_client(_settings(), caller_token)

    assert client.config.api_token == caller_token


def _branches(*named: tuple[str, bool]) -> dict[str, SimpleNamespace]:
    return {name: SimpleNamespace(is_default=is_default) for name, is_default in named}


class _Branches:
    def __init__(self, branches: dict[str, SimpleNamespace]) -> None:
        self._branches = branches

    async def all(self) -> dict[str, SimpleNamespace]:
        return self._branches


async def test_the_default_branch_is_read_from_the_server_not_assumed() -> None:
    client = SimpleNamespace(branch=_Branches(_branches(("feature", False), ("trunk", True))))

    assert await default_branch(cast("Any", client)) == "trunk"


def test_the_default_branch_is_read_with_the_synchronous_client_too() -> None:
    client = SimpleNamespace(branch=SimpleNamespace(all=lambda: _branches(("main", True))))

    assert default_branch_sync(cast("Any", client)) == "main"


async def test_a_server_without_a_default_branch_is_refused() -> None:
    client = SimpleNamespace(branch=_Branches(_branches(("feature", False))))

    with pytest.raises(DefaultBranchUnavailableError):
        await default_branch(cast("Any", client))


_BRANCH = {"sync_with_git": False, "has_schema_changes": False, "branched_from": "2026-10-04T00:00:00Z"}


class RenamedDefaultBranchServer:
    """An Infrahub whose default branch is `master`: `/graphql/main` is an unknown branch."""

    def __init__(self) -> None:
        self.urls: list[str] = []

    def answer(self, url: str, payload: dict | None) -> httpx.Response:
        """Answer one GraphQL request as Infrahub routes it."""
        self.urls.append(url)
        request = httpx.Request("POST", url)
        if url.endswith("/graphql/main"):
            return httpx.Response(404, json={"errors": ["Branch: main not found."]}, request=request)
        if (payload or {}).get("operationName") == "GET_PROFILE_DETAILS":
            profile = {"id": "account-id", "name": {"value": "alice"}, "status": {"value": "active"}}
            return httpx.Response(200, json={"data": {"AccountProfile": profile}}, request=request)
        branches = [
            {**_BRANCH, "id": "1", "name": "master", "is_default": True},
            {**_BRANCH, "id": "2", "name": "feature", "is_default": False},
        ]
        return httpx.Response(200, json={"data": {"Branch": branches}}, request=request)

    async def request(self, *, url: str, payload: dict | None = None, **_ignored: Any) -> httpx.Response:  # noqa: ANN401
        """The asynchronous client's request method."""
        return self.answer(url, payload)

    def request_sync(self, *, url: str, payload: dict | None = None, **_ignored: Any) -> httpx.Response:  # noqa: ANN401
        """The synchronous client's request method."""
        return self.answer(url, payload)


async def test_a_renamed_default_branch_is_read_without_a_branch_suffix() -> None:
    server = RenamedDefaultBranchServer()
    client = service_client(_settings())
    client._request_method = server.request  # ty: ignore[invalid-assignment]

    assert await default_branch(client) == "master"
    assert server.urls == ["http://infrahub-server:8000/graphql"]
    assert client.default_branch == "master"


def test_a_renamed_default_branch_is_read_by_the_synchronous_client_too() -> None:
    server = RenamedDefaultBranchServer()
    client = service_client_sync(_settings())
    client._request_method = server.request_sync  # ty: ignore[invalid-assignment]

    assert default_branch_sync(client) == "master"
    assert server.urls == ["http://infrahub-server:8000/graphql"]


def test_the_module_reads_no_prefect() -> None:
    assert "prefect" not in platform_client.__dict__


@pytest.mark.parametrize(("flag", "expected"), [("", False), ("false", False), ("true", True), (" TRUE ", True)])
def test_the_tls_flag_reaches_the_sdk(flag: str, expected: bool) -> None:  # noqa: FBT001
    settings = _settings(INFRAHUB_SYNC_INFRAHUB_TLS_INSECURE=flag)

    assert settings.tls_insecure is expected
    assert settings.sdk_config().tls_insecure is expected
