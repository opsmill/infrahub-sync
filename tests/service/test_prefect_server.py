"""Prefect server version and access-mode checks run at API and worker startup."""

from __future__ import annotations

import json
import logging
from types import SimpleNamespace
from typing import Any, cast

import httpx
import prefect
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from infrahub_sync.service import prefect_server, serve, worker
from infrahub_sync.service.prefect_server import (
    PrefectServerCheckError,
    PrefectServerNotConfiguredError,
    PrefectServerRefusedCredentialError,
    PrefectServerRejectedCheckError,
    PrefectServerUnavailableError,
    PrefectStartupRefusedError,
    PrefectVersionMismatchError,
    api_startup_check,
    check_prefect_server,
    prefect_auth_mode,
    require_prefect_server_version,
)

CREDENTIAL_URL = "http://admin:hunter2-secret@task-manager:4200/api"


class _VersionClient:
    def __init__(self, answer: object) -> None:
        self._answer = answer

    async def api_version(self) -> str:
        if isinstance(self._answer, BaseException):
            raise self._answer
        return cast("str", self._answer)


class _ClientContext:
    def __init__(self, client: _VersionClient) -> None:
        self._client = client

    async def __aenter__(self) -> _VersionClient:
        return self._client

    async def __aexit__(self, *_exc: object) -> None:
        return None


def _serve_client(monkeypatch: pytest.MonkeyPatch, answer: object, *, url: str = CREDENTIAL_URL) -> None:
    settings = SimpleNamespace(api=SimpleNamespace(url=url or None, auth_string=None))
    monkeypatch.setattr(prefect_server, "get_current_settings", lambda: settings)
    monkeypatch.setattr(prefect_server, "get_client", lambda: _ClientContext(_VersionClient(answer)))


async def test_matching_server_version_is_returned() -> None:
    assert await require_prefect_server_version(_VersionClient(prefect.__version__)) == prefect.__version__


async def test_different_server_version_names_both_versions() -> None:
    with pytest.raises(PrefectVersionMismatchError) as caught:
        await require_prefect_server_version(_VersionClient("3.7.5"))

    message = str(caught.value)
    assert "3.7.5" in message
    assert prefect.__version__ in message


@pytest.mark.parametrize(
    "failure",
    [
        httpx.ConnectError(f"refused {CREDENTIAL_URL}"),
        httpx.ReadTimeout("slow"),
        json.JSONDecodeError("Expecting value", "<html>login</html>", 0),
        httpx.InvalidURL(f"bad {CREDENTIAL_URL}"),
    ],
    ids=["connect", "timeout", "non-json-body", "invalid-url"],
)
async def test_unreachable_or_unreadable_server_is_refused_without_its_cause(failure: Exception) -> None:
    with pytest.raises(PrefectServerUnavailableError) as caught:
        await require_prefect_server_version(_VersionClient(failure))

    assert caught.value.__cause__ is None
    assert caught.value.__suppress_context__


@pytest.mark.parametrize("answer", ["", None, 3])
async def test_malformed_server_version_is_refused(answer: object) -> None:
    with pytest.raises(PrefectServerUnavailableError):
        await require_prefect_server_version(_VersionClient(answer))


@pytest.mark.parametrize(
    ("environ", "expected"),
    [
        ({"PREFECT_API_AUTH_STRING": "admin:pass"}, "credential"),
        ({"PREFECT_API_AUTH_STRING": ""}, "network-isolation"),
        ({"PREFECT_API_AUTH_STRING": "   "}, "network-isolation"),
        ({}, "network-isolation"),
    ],
)
def test_prefect_auth_mode(environ: dict[str, str], expected: str) -> None:
    assert prefect_auth_mode(environ) == expected


@pytest.mark.parametrize(
    ("auth_string", "expected"),
    [(SecretStr("admin:pass"), "credential"), (SecretStr(""), "network-isolation"), (None, "network-isolation")],
)
def test_the_default_auth_mode_reads_the_credential_prefect_presents(
    monkeypatch: pytest.MonkeyPatch, auth_string: SecretStr | None, expected: str
) -> None:
    """A credential from a Prefect profile or settings file counts, not only the variable."""
    monkeypatch.delenv("PREFECT_API_AUTH_STRING", raising=False)
    settings = SimpleNamespace(api=SimpleNamespace(url=CREDENTIAL_URL, auth_string=auth_string))
    monkeypatch.setattr(prefect_server, "get_current_settings", lambda: settings)

    assert prefect_auth_mode() == expected


async def test_the_credential_mode_is_logged_visibly_and_never_the_credential(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING, logger=prefect_server.__name__)
    _serve_client(monkeypatch, prefect.__version__)

    assert await check_prefect_server({"PREFECT_API_AUTH_STRING": "admin:hunter2-secret"}) == prefect.__version__

    assert [record.getMessage() for record in caplog.records] == ["prefect_auth=credential"]
    assert "hunter2" not in caplog.text


async def test_the_network_isolation_mode_is_logged_with_its_warning(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING, logger=prefect_server.__name__)
    _serve_client(monkeypatch, prefect.__version__)

    await check_prefect_server({})

    messages = [record.getMessage() for record in caplog.records]
    assert messages[0] == "prefect_auth=network-isolation"
    assert "network isolation only" in messages[1]


async def test_an_unset_api_url_is_refused_instead_of_starting_a_local_server(monkeypatch: pytest.MonkeyPatch) -> None:
    _serve_client(monkeypatch, prefect.__version__, url="")
    monkeypatch.setattr(prefect_server, "get_client", lambda: pytest.fail("a client was created"))

    with pytest.raises(PrefectServerUnavailableError, match="PREFECT_API_URL is not set"):
        await check_prefect_server({})


async def test_a_client_that_cannot_be_built_is_refused_without_its_detail(monkeypatch: pytest.MonkeyPatch) -> None:
    _serve_client(monkeypatch, prefect.__version__)

    def broken() -> Any:  # noqa: ANN401 - the client factory's return.
        msg = f"cannot build a client for {CREDENTIAL_URL}"
        raise ValueError(msg)

    monkeypatch.setattr(prefect_server, "get_client", broken)

    with pytest.raises(PrefectServerUnavailableError) as caught:
        await check_prefect_server({})

    assert "hunter2" not in str(caught.value)


@pytest.mark.parametrize(
    ("answer", "reason_type", "detail"),
    [
        ("3.7.5", PrefectVersionMismatchError, "3.7.5"),
        (httpx.ConnectError(f"refused {CREDENTIAL_URL}"), PrefectServerUnavailableError, "could not be read"),
    ],
    ids=["mismatch", "unreachable"],
)
def test_the_worker_refuses_to_start_naming_itself_and_no_credential(
    monkeypatch: pytest.MonkeyPatch,
    answer: object,
    reason_type: type[PrefectServerCheckError],
    detail: str,
) -> None:
    _serve_client(monkeypatch, answer)
    monkeypatch.setattr(worker, "ServiceProcessWorker", lambda **_kwargs: pytest.fail("the worker started"))

    with pytest.raises(PrefectStartupRefusedError) as caught:
        worker.main(["--pool", "infrahub-sync"])

    message = str(caught.value)
    assert message.startswith("infrahub-sync worker refused to start: ")
    assert detail in message
    assert "hunter2" not in message
    assert type(caught.value.reason) is reason_type
    assert caught.value.__cause__ is None


def _app(startup_check: Any) -> Any:  # noqa: ANN401 - the FastAPI app under test
    return serve.build_app(
        projection_factory=object,
        resolver_factory=lambda: SimpleNamespace(secret_values=()),
        run_service_factory=lambda *_args, **_kwargs: object(),
        configuration_routes_factory=lambda **_kwargs: None,
        startup_check=startup_check,
    )


def test_the_api_refuses_to_start_on_a_version_mismatch_through_any_serving_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The check runs in the lifespan, so `uvicorn --factory ...build_app` runs it too."""
    _serve_client(monkeypatch, "3.7.5")

    with pytest.raises(PrefectVersionMismatchError, match=r"3\.7\.5"), TestClient(_app(api_startup_check)):
        pytest.fail("the API started")


def test_the_api_refuses_to_start_without_a_prefect_url(monkeypatch: pytest.MonkeyPatch) -> None:
    _serve_client(monkeypatch, prefect.__version__, url="")

    with pytest.raises(PrefectServerNotConfiguredError), TestClient(_app(api_startup_check)):
        pytest.fail("the API started")


def test_the_api_serves_when_the_task_manager_is_unreachable(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A briefly absent task manager is reported, not fatal: the API still answers."""
    caplog.set_level(logging.WARNING, logger=prefect_server.__name__)
    _serve_client(monkeypatch, httpx.ConnectError(f"refused {CREDENTIAL_URL}"))

    with TestClient(_app(api_startup_check)) as client:
        assert client.get("/version").status_code == 200

    assert "could not be checked at startup" in caplog.text
    assert "hunter2" not in caplog.text


@pytest.mark.parametrize("status", [401, 403])
async def test_a_refused_credential_stops_the_api_rather_than_being_tolerated(
    monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    request = httpx.Request("GET", CREDENTIAL_URL)
    refused = httpx.HTTPStatusError("refused", request=request, response=httpx.Response(status, request=request))
    _serve_client(monkeypatch, refused)

    with pytest.raises(PrefectServerRefusedCredentialError) as caught:
        await api_startup_check()

    assert "hunter2" not in str(caught.value)


@pytest.mark.parametrize("status", [400, 404, 405, 422])
async def test_a_client_error_from_the_version_route_stops_the_api(
    monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    """A 4xx is an answer, so the URL points at something that is not the task manager API."""
    request = httpx.Request("GET", CREDENTIAL_URL)
    answer = httpx.HTTPStatusError("rejected", request=request, response=httpx.Response(status, request=request))
    _serve_client(monkeypatch, answer)

    with pytest.raises(PrefectServerRejectedCheckError) as caught:
        await api_startup_check()

    assert str(status) in str(caught.value)
    assert "hunter2" not in str(caught.value)


@pytest.mark.parametrize("status", [408, 429, 500, 502, 503, 504])
async def test_a_retryable_or_server_error_is_still_unavailable(status: int) -> None:
    request = httpx.Request("GET", CREDENTIAL_URL)
    answer = httpx.HTTPStatusError("away", request=request, response=httpx.Response(status, request=request))

    with pytest.raises(PrefectServerUnavailableError):
        await require_prefect_server_version(_VersionClient(answer))
