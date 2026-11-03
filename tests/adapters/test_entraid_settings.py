"""Tests of the Entra ID credentials: where the tenant ID, client ID and secret come from, and which credential is built."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import pytest

pytest.importorskip("msgraph")

from azure.identity.aio import ClientSecretCredential, DefaultAzureCredential

from infrahub_sync.adapters import entraid
from infrahub_sync.adapters.entraid import (
    ENV_CLIENT_ID,
    ENV_CLIENT_SECRET,
    ENV_TENANT_ID,
    EntraidConfigError,
    EntraidGraphClient,
    EntraidSettings,
    build_credential,
)
from tests.adapters.entraid_fakes import FakeCredential, GraphRouter

if TYPE_CHECKING:
    from collections.abc import Mapping

ENV_TENANT = "00000000-0000-0000-0000-000000000001"
ENV_CLIENT = "00000000-0000-0000-0000-000000000002"
SETTINGS_TENANT = "00000000-0000-0000-0000-000000000003"
SETTINGS_CLIENT = "00000000-0000-0000-0000-000000000004"
SECRET = "not-a-real-secret"  # noqa: S105 - fake value, tests check that it never appears in repr or messages
FULL_ENV = {ENV_TENANT_ID: ENV_TENANT, ENV_CLIENT_ID: ENV_CLIENT, ENV_CLIENT_SECRET: SECRET}
SETTINGS_IDS = {"tenant_id": SETTINGS_TENANT, "client_id": SETTINGS_CLIENT}


class RecordedCredential(FakeCredential):
    """A fake credential that keeps the arguments it was constructed with."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:  # noqa: ANN401 - records any constructor call
        super().__init__()
        self.args = args
        self.kwargs = kwargs


class CredentialRecorder:
    """Replacement for a credential class: each call builds a `RecordedCredential` and keeps it in `built`."""

    def __init__(self) -> None:
        self.built: list[RecordedCredential] = []

    def __call__(self, *args: Any, **kwargs: Any) -> RecordedCredential:  # noqa: ANN401 - same signature as the class
        credential = RecordedCredential(*args, **kwargs)
        self.built.append(credential)
        return credential


@pytest.fixture
def recorders(monkeypatch: pytest.MonkeyPatch) -> dict[str, CredentialRecorder]:
    """Replace both Azure credential classes in `entraid` with recorders, keyed by `auth` mode."""
    recorded = {entraid.AUTH_CLIENT_SECRET: CredentialRecorder(), entraid.AUTH_DEFAULT: CredentialRecorder()}
    monkeypatch.setattr(entraid, "ClientSecretCredential", recorded[entraid.AUTH_CLIENT_SECRET])
    monkeypatch.setattr(entraid, "DefaultAzureCredential", recorded[entraid.AUTH_DEFAULT])
    return recorded


def make_settings(raw: Mapping[str, Any] | None, environ: Mapping[str, str]) -> EntraidSettings:
    return EntraidSettings.from_settings(raw, environ=environ)


def test_client_secret_auth_builds_client_secret_credential_from_variables(
    recorders: dict[str, CredentialRecorder],
) -> None:
    credential = build_credential(make_settings({"auth": "client_secret"}, FULL_ENV))

    assert recorders["client_secret"].built == [credential]
    assert credential.args == (ENV_TENANT, ENV_CLIENT, SECRET)
    assert credential.kwargs == {}
    assert recorders["default"].built == []


def test_client_secret_is_the_default_auth(recorders: dict[str, CredentialRecorder]) -> None:
    credential = build_credential(make_settings(None, FULL_ENV))

    assert recorders["client_secret"].built == [credential]
    assert recorders["default"].built == []


@pytest.mark.parametrize(
    ("environ", "raw", "expected_tenant", "expected_client"),
    [
        pytest.param({ENV_CLIENT_SECRET: SECRET}, SETTINGS_IDS, SETTINGS_TENANT, SETTINGS_CLIENT, id="settings-only"),
        pytest.param(FULL_ENV, SETTINGS_IDS, ENV_TENANT, ENV_CLIENT, id="variables-win"),
        pytest.param(FULL_ENV, {}, ENV_TENANT, ENV_CLIENT, id="variables-only"),
        pytest.param(
            {ENV_TENANT_ID: ENV_TENANT, ENV_CLIENT_SECRET: SECRET},
            SETTINGS_IDS,
            ENV_TENANT,
            SETTINGS_CLIENT,
            id="tenant-variable-client-setting",
        ),
        pytest.param(
            {ENV_TENANT_ID: "", ENV_CLIENT_ID: "", ENV_CLIENT_SECRET: SECRET},
            SETTINGS_IDS,
            SETTINGS_TENANT,
            SETTINGS_CLIENT,
            id="empty-variables-fall-back",
        ),
    ],
)
def test_tenant_and_client_ids_come_from_variables_then_settings(
    recorders: dict[str, CredentialRecorder],
    environ: Mapping[str, str],
    raw: Mapping[str, Any],
    expected_tenant: str,
    expected_client: str,
) -> None:
    settings = make_settings(raw, environ)
    credential = build_credential(settings)

    assert (settings.tenant_id, settings.client_id) == (expected_tenant, expected_client)
    assert credential.args == (expected_tenant, expected_client, SECRET)
    assert recorders["client_secret"].built == [credential]


@pytest.mark.parametrize("auth", ["client_secret", "default"])
def test_client_secret_setting_is_refused_naming_the_variable(auth: str) -> None:
    with pytest.raises(EntraidConfigError, match=ENV_CLIENT_SECRET) as excinfo:
        make_settings({"auth": auth, "client_secret": SECRET}, FULL_ENV)

    assert SECRET not in str(excinfo.value)


@pytest.mark.parametrize(
    ("environ", "raw"),
    [
        pytest.param({ENV_TENANT_ID: ENV_TENANT, ENV_CLIENT_ID: ENV_CLIENT}, {}, id="ids-from-variables"),
        pytest.param({}, SETTINGS_IDS, id="ids-from-settings"),
        pytest.param({**FULL_ENV, ENV_CLIENT_SECRET: ""}, {}, id="empty-secret-variable"),
    ],
)
def test_missing_secret_raises_naming_only_the_secret_variable(
    environ: Mapping[str, str], raw: Mapping[str, Any]
) -> None:
    with pytest.raises(EntraidConfigError, match=ENV_CLIENT_SECRET) as excinfo:
        make_settings(raw, environ)

    message = str(excinfo.value)
    assert ENV_TENANT_ID not in message
    assert ENV_CLIENT_ID not in message


def test_missing_everything_raises_naming_the_three_variables() -> None:
    with pytest.raises(EntraidConfigError) as excinfo:
        make_settings({}, {})

    message = str(excinfo.value)
    for name in (ENV_TENANT_ID, ENV_CLIENT_ID, ENV_CLIENT_SECRET):
        assert name in message


def test_default_auth_without_variables_needs_no_tenant_client_or_secret(
    recorders: dict[str, CredentialRecorder],
) -> None:
    settings = make_settings({"auth": "default"}, {})
    credential = build_credential(settings)

    assert (settings.tenant_id, settings.client_id, settings.client_secret) == (None, None, None)
    assert recorders["default"].built == [credential]
    assert credential.args == ()
    assert credential.kwargs == {}
    assert recorders["client_secret"].built == []


def test_default_auth_ignores_client_secret_variables(recorders: dict[str, CredentialRecorder]) -> None:
    credential = build_credential(make_settings({"auth": "default"}, FULL_ENV))

    assert recorders["default"].built == [credential]
    assert credential.args == ()
    assert credential.kwargs == {}
    assert recorders["client_secret"].built == []


@pytest.mark.parametrize(
    ("auth", "expected_type"),
    [("client_secret", ClientSecretCredential), ("default", DefaultAzureCredential)],
)
def test_build_credential_returns_the_real_azure_credential_without_contacting_azure(
    auth: str, expected_type: type
) -> None:
    credential = build_credential(make_settings({"auth": auth}, FULL_ENV))
    try:
        assert isinstance(credential, expected_type)
    finally:
        asyncio.run(credential.close())


def test_repr_of_settings_does_not_contain_the_secret() -> None:
    settings = make_settings({}, FULL_ENV)

    assert settings.client_secret == SECRET
    assert SECRET not in repr(settings)
    assert SECRET not in str(settings)
    assert ENV_TENANT in repr(settings)


@pytest.mark.parametrize("auth", ["client_secret", "default"])
def test_built_credential_is_closed_when_graph_client_exits(
    recorders: dict[str, CredentialRecorder], auth: str
) -> None:
    async def open_and_exit() -> None:
        async with EntraidGraphClient(make_settings({"auth": auth}, FULL_ENV), transport=GraphRouter().transport):
            pass

    asyncio.run(open_and_exit())

    assert len(recorders[auth].built) == 1
    assert recorders[auth].built[0].closed is True


@pytest.mark.parametrize(
    ("raw", "setting"),
    [
        pytest.param({"page_size": 0}, "'page_size'", id="page-size-zero"),
        pytest.param({"page_size": 1000}, "'page_size'", id="page-size-above-maximum"),
        pytest.param({"page_size": "abc"}, "'page_size'", id="page-size-not-integer"),
        pytest.param({"membership": "nested"}, "'membership'", id="membership-nested"),
        pytest.param({"auth": "token"}, "'auth'", id="auth-token"),
        pytest.param({"graph_url": "http://graph.example.com/v1.0"}, "'graph_url'", id="graph-url-http"),
        pytest.param({"timeout": 0}, "'timeout'", id="timeout-zero"),
        pytest.param({"max_retries": 11}, "'max_retries'", id="max-retries-above-maximum"),
        pytest.param({"scope": "users"}, "setting(s): scope", id="unknown-top-level-key"),
        pytest.param(
            {"collections": {"accounts": {"filter": "accountEnabled eq true"}}},
            "'collections.accounts.resource'",
            id="collection-without-resource",
        ),
        pytest.param(
            {"collections": {"accounts": {"resource": "devices"}}},
            "'collections.accounts.resource'",
            id="collection-resource-devices",
        ),
        pytest.param(
            {"collections": {"accounts": {"resource": "users", "top": 10}}},
            "'collections.accounts' has unknown key(s): top",
            id="collection-unknown-key",
        ),
        pytest.param(
            {"collections": {"accounts": {"resource": "users", "advanced_query": "yes"}}},
            "'collections.accounts.advanced_query'",
            id="advanced-query-not-boolean",
        ),
        pytest.param({"select": {"devices": ["id"]}}, "'select' has key 'devices'", id="select-key-devices"),
    ],
)
def test_invalid_setting_raises_config_error_naming_the_setting(raw: Mapping[str, Any], setting: str) -> None:
    with pytest.raises(EntraidConfigError) as excinfo:
        make_settings(raw, FULL_ENV)

    assert setting in str(excinfo.value)


def test_given_credential_is_closed_when_graph_client_exits_on_error() -> None:
    credential = FakeCredential()

    async def open_and_fail() -> None:
        async with EntraidGraphClient(
            make_settings({}, FULL_ENV), transport=GraphRouter().transport, credential=credential
        ):
            msg = "fake failure inside the client block"
            raise RuntimeError(msg)

    with pytest.raises(RuntimeError, match="fake failure"):
        asyncio.run(open_and_fail())

    assert credential.closed is True
