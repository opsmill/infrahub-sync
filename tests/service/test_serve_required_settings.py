"""The Sync API process refuses to start without the encryption key or the worker token (spec 027).

The refusal belongs to the start of the process: ``serve.main`` and the factory that
``uvicorn --factory`` calls. ``serve.build_app`` stays as it is for an application built in a test:
without a key, its credential routes answer ``503 credential-key-not-configured``.
"""

from __future__ import annotations

import base64
from typing import TYPE_CHECKING, NoReturn

import pytest

pytest.importorskip("prefect")
pytest.importorskip("fastapi")

from infrahub_sync.service import serve
from infrahub_sync.service.settings import (
    ADMIN_ACCOUNT_TOKEN_ENV,
    ENCRYPTION_KEY_ENV,
    WORKER_TOKEN_ENV,
    CredentialSettingsError,
)

if TYPE_CHECKING:
    from collections.abc import Callable

# Test values only; they reach no system.
KEY = base64.b64encode(bytes(range(32))).decode()
WORKER_TOKEN = "worker-token-of-the-start-test-01"  # noqa: S105 - a test value, not a secret.
NOT_BASE64_KEY = "not*base64*text*of*the*start*test"
SHORT_KEY = base64.b64encode(bytes(range(16))).decode()
LONG_KEY = base64.b64encode(bytes(range(48))).decode()
SHORT_WORKER_TOKEN = "short-token"  # noqa: S105 - a test value, not a secret.


@pytest.fixture
def started(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record whether the application was built or served; a refused start does neither."""
    events: list[str] = []
    monkeypatch.setenv(ENCRYPTION_KEY_ENV, KEY)
    monkeypatch.setenv(WORKER_TOKEN_ENV, WORKER_TOKEN)
    monkeypatch.delenv(ADMIN_ACCOUNT_TOKEN_ENV, raising=False)
    monkeypatch.setattr(serve, "build_service", lambda: events.append("built") or "application")
    monkeypatch.setattr(serve.uvicorn, "run", lambda *_args, **_kwargs: events.append("served"))
    return events


def _unset(variable: str) -> Callable[[pytest.MonkeyPatch], None]:
    return lambda monkeypatch: monkeypatch.delenv(variable)


def _set(variable: str, value: str) -> Callable[[pytest.MonkeyPatch], None]:
    return lambda monkeypatch: monkeypatch.setenv(variable, value)


REFUSED_STARTS = [
    pytest.param(_unset(ENCRYPTION_KEY_ENV), ENCRYPTION_KEY_ENV, "", id="key-unset"),
    pytest.param(_set(ENCRYPTION_KEY_ENV, ""), ENCRYPTION_KEY_ENV, "", id="key-empty"),
    pytest.param(_set(ENCRYPTION_KEY_ENV, NOT_BASE64_KEY), ENCRYPTION_KEY_ENV, NOT_BASE64_KEY, id="key-not-base64"),
    pytest.param(_set(ENCRYPTION_KEY_ENV, SHORT_KEY), ENCRYPTION_KEY_ENV, SHORT_KEY, id="key-of-16-bytes"),
    pytest.param(_set(ENCRYPTION_KEY_ENV, LONG_KEY), ENCRYPTION_KEY_ENV, LONG_KEY, id="key-of-48-bytes"),
    pytest.param(_unset(WORKER_TOKEN_ENV), WORKER_TOKEN_ENV, "", id="worker-token-unset"),
    pytest.param(_set(WORKER_TOKEN_ENV, ""), WORKER_TOKEN_ENV, "", id="worker-token-empty"),
    pytest.param(
        _set(WORKER_TOKEN_ENV, SHORT_WORKER_TOKEN), WORKER_TOKEN_ENV, SHORT_WORKER_TOKEN, id="worker-token-too-short"
    ),
]


@pytest.mark.parametrize(("change", "variable", "value"), REFUSED_STARTS)
def test_main_refuses_to_start_and_names_the_variable(
    started: list[str],
    monkeypatch: pytest.MonkeyPatch,
    change: Callable[[pytest.MonkeyPatch], None],
    variable: str,
    value: str,
) -> None:
    change(monkeypatch)

    with pytest.raises(SystemExit) as stopped:
        serve.main()

    message = str(stopped.value)
    assert variable in message
    # The message never holds the value; the comparison result is kept, not the texts.
    holds_the_value = bool(value) and value in message
    assert not holds_the_value
    assert started == []


@pytest.mark.parametrize(("change", "variable", "value"), REFUSED_STARTS)
def test_the_factory_of_the_process_refuses_before_it_builds_the_application(
    started: list[str],
    monkeypatch: pytest.MonkeyPatch,
    change: Callable[[pytest.MonkeyPatch], None],
    variable: str,
    value: str,
) -> None:
    change(monkeypatch)

    with pytest.raises(CredentialSettingsError) as refused:
        serve.build_process_service()

    message = str(refused.value)
    assert variable in message
    holds_the_value = bool(value) and value in message
    assert not holds_the_value
    assert started == []


def test_main_refuses_a_worker_token_equal_to_the_admin_token(
    started: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ADMIN_ACCOUNT_TOKEN_ENV, WORKER_TOKEN)

    with pytest.raises(SystemExit) as stopped:
        serve.main()

    assert WORKER_TOKEN_ENV in str(stopped.value)
    holds_the_value = WORKER_TOKEN in str(stopped.value)
    assert not holds_the_value
    assert started == []


def test_main_serves_with_both_settings(started: list[str]) -> None:
    serve.main()

    assert started == ["built", "served"]


def test_the_factory_of_the_process_builds_the_served_application(started: list[str]) -> None:
    assert serve.build_process_service() == "application"
    assert started == ["built"]


def test_a_refused_start_opens_no_database(monkeypatch: pytest.MonkeyPatch) -> None:
    """The settings are checked before the real application is built, so nothing is written."""

    def no_application(**_factories: object) -> NoReturn:
        pytest.fail("the application was built")

    monkeypatch.delenv(ENCRYPTION_KEY_ENV, raising=False)
    monkeypatch.setenv(WORKER_TOKEN_ENV, WORKER_TOKEN)
    monkeypatch.setattr(serve, "build_app", no_application)

    with pytest.raises(CredentialSettingsError):
        serve.build_process_service()
