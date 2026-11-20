"""The variables of stored credentials: the encryption key, the worker token and the worker's API address.

Every refusal names the variable and never contains the value. Values are compared, never printed.
"""

from __future__ import annotations

import base64

import pytest

from infrahub_sync.service.settings import (
    ADMIN_ACCOUNT_TOKEN_ENV,
    ENCRYPTION_KEY_ENV,
    WORKER_API_URL_ENV,
    WORKER_TOKEN_ENV,
    AccountSettingsError,
    CredentialSettingsError,
    credential_settings,
    worker_settings,
)

_KEY_BYTES = bytes(range(32))
_KEY = base64.b64encode(_KEY_BYTES).decode()
_WORKER_TOKEN = "worker-environment-token-0001"  # noqa: S105 - a test value, not a secret.
_ADMIN_TOKEN = "admin-environment-token-0001"  # noqa: S105 - a test value, not a secret.
_API_URL = "http://sync-api:8000"


def _api_environment(**changes: str | None) -> dict[str, str]:
    environment = {ENCRYPTION_KEY_ENV: _KEY, WORKER_TOKEN_ENV: _WORKER_TOKEN, ADMIN_ACCOUNT_TOKEN_ENV: _ADMIN_TOKEN}
    for name, value in changes.items():
        if value is None:
            environment.pop(name, None)
        else:
            environment[name] = value
    return environment


def _worker_environment(**changes: str | None) -> dict[str, str]:
    environment = {WORKER_TOKEN_ENV: _WORKER_TOKEN, WORKER_API_URL_ENV: _API_URL}
    for name, value in changes.items():
        if value is None:
            environment.pop(name, None)
        else:
            environment[name] = value
    return environment


# The Sync API.


def test_the_sync_api_reads_the_key_and_the_worker_token_from_the_environment() -> None:
    settings = credential_settings(_api_environment())

    assert settings.encryption_key == _KEY_BYTES
    assert settings.worker_token == _WORKER_TOKEN
    assert _KEY not in repr(settings)
    assert repr(_KEY_BYTES) not in repr(settings)
    assert _WORKER_TOKEN not in repr(settings)


def test_the_sync_api_accepts_a_worker_token_when_admin_has_no_token() -> None:
    settings = credential_settings(_api_environment(**{ADMIN_ACCOUNT_TOKEN_ENV: None}))

    assert settings.worker_token == _WORKER_TOKEN


_REFUSED_KEYS = [
    pytest.param(None, "must be set", id="unset"),
    pytest.param("", "must be set", id="empty"),
    pytest.param("   ", "must be set", id="spaces"),
    pytest.param(base64.b64encode(bytes(16)).decode(), "32 bytes", id="16-bytes"),
    pytest.param(base64.b64encode(bytes(31)).decode(), "32 bytes", id="31-bytes"),
    pytest.param(base64.b64encode(bytes(33)).decode(), "32 bytes", id="33-bytes"),
    pytest.param(base64.b64encode(bytes(64)).decode(), "32 bytes", id="64-bytes"),
    pytest.param("not base64 at all, but long enough to be a key!!", "base64", id="not-base64"),
    pytest.param(_KEY[:-1], "base64", id="bad-padding"),
    pytest.param("é" * 44, "base64", id="not-ascii"),
    pytest.param(_KEY_BYTES.hex(), "32 bytes", id="hexadecimal"),
]


@pytest.mark.parametrize(("value", "rule"), _REFUSED_KEYS)
def test_a_key_that_is_not_32_bytes_of_base64_is_refused_without_its_value(value: str | None, rule: str) -> None:
    with pytest.raises(CredentialSettingsError) as caught:
        credential_settings(_api_environment(**{ENCRYPTION_KEY_ENV: value}))

    message = str(caught.value)
    assert ENCRYPTION_KEY_ENV in message
    assert rule in message
    if value and value.strip():
        assert value not in message


_REFUSED_TOKENS = [
    pytest.param(None, "must be set", id="unset"),
    pytest.param("", "must be set", id="empty"),
    pytest.param("short-token-15c", "at least 16 characters", id="15-characters"),
    pytest.param("ifs_pat_worker-environment-token", "must not start with", id="api-token-prefix"),
    pytest.param("ifs_ses_worker-environment-token", "must not start with", id="session-token-prefix"),
    pytest.param(" worker-environment-token", "printable ASCII", id="leading-space"),
    pytest.param("worker-environment-token ", "printable ASCII", id="trailing-space"),
    pytest.param("worker-environment-tökén", "printable ASCII", id="not-ascii"),
    pytest.param("worker-environment\ttoken", "printable ASCII", id="control-character"),
    pytest.param(_ADMIN_TOKEN, "must differ from", id="equal-to-admin-token"),
]


@pytest.mark.parametrize(("value", "rule"), _REFUSED_TOKENS)
def test_the_sync_api_refuses_a_worker_token_that_breaks_a_rule_without_its_value(value: str | None, rule: str) -> None:
    with pytest.raises(CredentialSettingsError) as caught:
        credential_settings(_api_environment(**{WORKER_TOKEN_ENV: value}))

    message = str(caught.value)
    assert WORKER_TOKEN_ENV in message
    assert rule in message
    if value:
        assert value not in message
        assert value.strip() not in message


@pytest.mark.parametrize(("value", "rule"), [p for p in _REFUSED_TOKENS if p.id != "equal-to-admin-token"])
def test_a_worker_refuses_a_worker_token_that_breaks_a_rule_without_its_value(value: str | None, rule: str) -> None:
    with pytest.raises(CredentialSettingsError) as caught:
        worker_settings(_worker_environment(**{WORKER_TOKEN_ENV: value}))

    message = str(caught.value)
    assert WORKER_TOKEN_ENV in message
    assert rule in message
    if value:
        assert value not in message


def test_a_refusal_is_an_account_settings_error_so_the_start_reports_it() -> None:
    with pytest.raises(AccountSettingsError):
        credential_settings(_api_environment(**{ENCRYPTION_KEY_ENV: None}))


# The worker.


@pytest.mark.parametrize("address", ["http://sync-api:8000", "https://sync.example.test", "https://10.0.0.5:8443/"])
def test_a_worker_reads_its_token_and_the_address_of_the_sync_api(address: str) -> None:
    settings = worker_settings(_worker_environment(**{WORKER_API_URL_ENV: address}))

    assert settings.api_url == address
    assert settings.token == _WORKER_TOKEN
    assert _WORKER_TOKEN not in repr(settings)


_REFUSED_ADDRESSES = [
    pytest.param(None, "must be set", id="unset"),
    pytest.param("", "must be set", id="empty"),
    pytest.param("sync.example.test:8000", "http:// or https://", id="no-scheme"),
    pytest.param("ftp://sync.example.test", "http:// or https://", id="other-scheme"),
    pytest.param("http://:8000/api", "http:// or https://", id="no-host"),
    pytest.param(" http://sync.example.test", "http:// or https://", id="leading-space"),
    pytest.param("http://operator:a-password-value@sync-api:8000", "user name or password", id="user-information"),
]


@pytest.mark.parametrize(("value", "rule"), _REFUSED_ADDRESSES)
def test_a_worker_refuses_an_address_that_is_not_an_http_address_without_its_value(
    value: str | None, rule: str
) -> None:
    with pytest.raises(CredentialSettingsError) as caught:
        worker_settings(_worker_environment(**{WORKER_API_URL_ENV: value}))

    message = str(caught.value)
    assert WORKER_API_URL_ENV in message
    assert rule in message
    if value:
        assert value not in message
        assert "a-password-value" not in message
