"""Ambient `CISCO_APIC_VERIFY` precedence against a declared `verify` setting."""

from __future__ import annotations

import warnings
from unittest.mock import patch

import pytest
from requests import Response

from infrahub_sync import SyncAdapter
from infrahub_sync.adapters.aci import AciAdapter
from infrahub_sync.configuration import ConfigurationPackage
from infrahub_sync.configuration.runtime import resolve_runtime_instance

BASE_SETTINGS: dict[str, object] = {
    "url": "https://apic.example.test",
    "username": "admin",
    "password": "secret",
}


def _create_client(monkeypatch: pytest.MonkeyPatch, *, env_value: str | None, declared_verify: object) -> bool:
    """Build a client through `_create_aci_client` and return the resolved `verify` flag."""
    if env_value is None:
        monkeypatch.delenv("CISCO_APIC_VERIFY", raising=False)
    else:
        monkeypatch.setenv("CISCO_APIC_VERIFY", env_value)
    adapter = object.__new__(AciAdapter)
    settings = dict(BASE_SETTINGS)
    settings["verify"] = declared_verify
    # AciApiClient disables warnings when verification is false; restore the
    # process-wide warning filters before another test can observe the change.
    with warnings.catch_warnings():
        client = adapter._create_aci_client(SyncAdapter(name="aci", settings=settings))
    return client.verify


@pytest.mark.parametrize(
    ("env_value", "declared_verify", "expected"),
    [
        # Unset: the declared setting is authoritative.
        (None, False, False),
        (None, True, True),
        # Empty: falls through to the declared setting rather than enabling verification.
        ("", False, False),
        ("", True, True),
        # A real falsy value in the environment overrides a declared True.
        ("false", True, False),
        ("0", True, False),
        ("no", True, False),
        # A real truthy value in the environment overrides a declared False.
        ("true", False, True),
        # A non-string declared setting (not just str/bool) still normalizes via bool().
        (None, 0, False),
        (None, 1, True),
        # A declared `verify: null` is "unset", not "disabled": keep the secure default,
        # both when the env var is absent and when it is present but empty.
        (None, None, True),
        ("", None, True),
    ],
)
def test_verify_precedence(
    monkeypatch: pytest.MonkeyPatch,
    env_value: str | None,
    declared_verify: object,
    expected: bool,  # noqa: FBT001 - one parametrized dimension of the resolved flag.
) -> None:
    """Resolve the `verify` flag for each ambient env / declared setting combination."""
    assert _create_client(monkeypatch, env_value=env_value, declared_verify=declared_verify) is expected


def test_empty_env_does_not_override_declared_false(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression test: an empty `CISCO_APIC_VERIFY` must not silently enable TLS verification."""
    assert _create_client(monkeypatch, env_value="", declared_verify=False) is False


@pytest.mark.parametrize("env_value", [None, "", "false", "true"])
@pytest.mark.parametrize(
    ("verification_settings", "expected"),
    [
        ({"verify": True}, True),
        ({"verify": False}, False),
        ({"verify": "false"}, False),
        ({"verify": "0"}, False),
        ({"verify": "no"}, False),
        ({"verify": "FaLsE"}, False),
        ({"verify": "true"}, True),
        ({"verify": " false "}, True),
        ({"verify": ""}, True),
        ({"verify": 0}, False),
        ({"verify": 1}, True),
        ({"verify": None}, True),
        ({}, True),
    ],
)
def test_registered_verify_reaches_http_request(
    monkeypatch: pytest.MonkeyPatch,
    env_value: str | None,
    verification_settings: dict[str, object],
    expected: bool,  # noqa: FBT001 - expected verification flag.
) -> None:
    """Registered settings govern the real client and its final HTTP verification flag."""
    if env_value is None:
        monkeypatch.delenv("CISCO_APIC_VERIFY", raising=False)
    else:
        monkeypatch.setenv("CISCO_APIC_VERIFY", env_value)
    monkeypatch.setenv("INFRAHUB_SYNC_CREDENTIAL_ACI_TEST_USERNAME", "registered-user")
    monkeypatch.setenv("INFRAHUB_SYNC_CREDENTIAL_ACI_TEST_PASSWORD", "registered-password")
    monkeypatch.setenv("CISCO_APIC_URL", "https://ambient.example.test")
    monkeypatch.setenv("CISCO_APIC_USERNAME", "ambient-user")
    monkeypatch.setenv("CISCO_APIC_PASSWORD", "ambient-password")
    package = ConfigurationPackage.model_validate(
        {
            "format_version": 1,
            "configuration": {
                "name": "registered-aci",
                "source": {
                    "name": "aci",
                    "settings": {
                        "url": BASE_SETTINGS["url"],
                        "username": {"$credential": "username"},
                        "password": {"$credential": "password"},
                        **verification_settings,
                    },
                },
                "destination": {"name": "infrahub", "settings": {}},
                "schema_mapping": [],
            },
            "credentials": {
                "username": {"provider": "env", "identifier": "INFRAHUB_SYNC_CREDENTIAL_ACI_TEST_USERNAME"},
                "password": {"provider": "env", "identifier": "INFRAHUB_SYNC_CREDENTIAL_ACI_TEST_PASSWORD"},
            },
        }
    )
    instance = resolve_runtime_instance(package, directory="/registered")
    with warnings.catch_warnings():
        client = object.__new__(AciAdapter)._create_aci_client(instance.source)
    try:
        assert client.verify is expected
        assert client.base_url == f"{BASE_SETTINGS['url']}/api/"
        assert client.username == "registered-user"
        assert client.password == "registered-password"  # noqa: S105 - synthetic test credential.
        response = Response()
        response.status_code = 200
        with patch.object(client.session, "request", return_value=response) as request:
            assert client._handle_request(client.base_url + "class/fabricNode.json") is response
        assert request.call_args.kwargs["verify"] is expected
    finally:
        client.session.close()


@pytest.mark.parametrize(("env_value", "expected"), [(None, True), ("", True), ("false", False), ("true", True)])
def test_direct_omitted_verify(
    monkeypatch: pytest.MonkeyPatch,
    env_value: str | None,
    expected: bool,  # noqa: FBT001 - expected verification flag.
) -> None:
    """Direct use keeps the secure default and non-empty environment override."""
    if env_value is None:
        monkeypatch.delenv("CISCO_APIC_VERIFY", raising=False)
    else:
        monkeypatch.setenv("CISCO_APIC_VERIFY", env_value)
    with warnings.catch_warnings():
        client = object.__new__(AciAdapter)._create_aci_client(SyncAdapter(name="aci", settings=dict(BASE_SETTINGS)))
    try:
        assert client.verify is expected
    finally:
        client.session.close()
