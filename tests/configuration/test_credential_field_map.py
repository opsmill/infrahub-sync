"""Which stored credential field fills which adapter setting (spec 027 research R2, T016)."""

from __future__ import annotations

from typing import Any

import pytest

from infrahub_sync.configuration import capabilities, parse_configuration_package
from infrahub_sync.configuration.capabilities import (
    BUILTIN_ADAPTER_CAPABILITIES,
    UnknownAdapterCapabilitiesError,
    UnknownCredentialMethodError,
    credential_field_map,
)
from infrahub_sync.configuration.conversion_authentication import V2_AUTHENTICATION_METHODS
from infrahub_sync.configuration.validation import collect_findings

_TOKEN = {"token": "token"}
_BASIC = {"username": "username", "password": "password"}

# (adapter, settings, setting ← stored field) for every adapter and auth_method of research R2.
_CASES = [
    pytest.param("infrahub", {}, _TOKEN, id="infrahub"),
    pytest.param("netbox", {}, _TOKEN, id="netbox"),
    pytest.param("nautobot", {}, _TOKEN, id="nautobot"),
    pytest.param("aci", {}, _BASIC, id="aci"),
    pytest.param("ipfabricsync", {}, {"auth": "token"}, id="ipfabricsync"),
    pytest.param("slurpitsync", {}, {"api_key": "token"}, id="slurpitsync"),
    *(
        pytest.param(adapter, {"auth_method": method}, expected, id=f"{adapter}-{method}")
        for adapter in ("genericrestapi", "peeringmanager")
        for method, expected in (
            ("token", _TOKEN),
            ("x-auth-token", _TOKEN),
            ("api-key", _TOKEN),
            ("key", _TOKEN),
            ("basic", _BASIC),
            ("none", {}),
        )
    ),
    pytest.param("genericrestapi", {}, _TOKEN, id="genericrestapi-default-token"),
    pytest.param("peeringmanager", {}, _TOKEN, id="peeringmanager-default-token"),
    pytest.param("prometheus", {"auth_method": "bearer"}, _TOKEN, id="prometheus-bearer"),
    pytest.param("prometheus", {"auth_method": "basic"}, _BASIC, id="prometheus-basic"),
    pytest.param("prometheus", {"auth_method": "none"}, {}, id="prometheus-none"),
    pytest.param("prometheus", {}, {}, id="prometheus-default-none"),
]


@pytest.mark.parametrize(("adapter", "settings", "expected"), _CASES)
def test_the_setting_each_stored_field_fills(adapter: str, settings: dict[str, Any], expected: dict[str, str]) -> None:
    field_map = credential_field_map(adapter, settings)

    assert dict(field_map.settings) == expected
    assert field_map.fields == frozenset(expected.values())
    assert field_map.needs_credential is bool(expected)


@pytest.mark.parametrize("adapter", sorted(BUILTIN_ADAPTER_CAPABILITIES))
def test_every_bundled_adapter_has_a_field_map_within_its_credential_settings(adapter: str) -> None:
    declared = set(BUILTIN_ADAPTER_CAPABILITIES[adapter].credential_setting_paths)

    assert set(credential_field_map(adapter, {}).settings) <= declared


@pytest.mark.parametrize(
    "adapter",
    sorted(name for name, item in BUILTIN_ADAPTER_CAPABILITIES.items() if item.credential_setting_paths),
)
def test_every_bundled_adapter_with_credential_settings_has_a_declared_field_map(adapter: str) -> None:
    """A new adapter must be added to the map; falling through would report its credential as not used."""
    assert adapter in capabilities._FIXED_CREDENTIAL_FIELDS or adapter in V2_AUTHENTICATION_METHODS


@pytest.mark.parametrize("adapter", ["genericrestapi", "peeringmanager", "prometheus"])
@pytest.mark.parametrize("method", ["oauth", "", "Token", 3, None])
def test_an_unknown_auth_method_is_refused_rather_than_read_as_needing_nothing(adapter: str, method: object) -> None:
    with pytest.raises(UnknownCredentialMethodError, match="auth_method"):
        credential_field_map(adapter, {"auth_method": method})


def test_an_adapter_without_a_declaration_has_no_field_map() -> None:
    with pytest.raises(UnknownAdapterCapabilitiesError):
        credential_field_map("custom-adapter", {})


def _package(source: dict[str, Any]) -> dict[str, Any]:
    return {
        "format_version": 1,
        "configuration": {
            "name": "credential-field-map",
            "source": source,
            "destination": {
                "name": "infrahub",
                "credential": "infrahub-sync",
                "settings": {"url": "http://localhost:8000"},
            },
        },
    }


@pytest.mark.parametrize(
    "source",
    [
        pytest.param(
            {"name": "prometheus", "settings": {"url": "http://localhost:9090", "auth_method": "none"}},
            id="prometheus-none",
        ),
        pytest.param({"name": "prometheus", "settings": {"url": "http://localhost:9090"}}, id="prometheus-default"),
        pytest.param(
            {"name": "genericrestapi", "settings": {"url": "http://localhost:8080", "auth_method": "none"}},
            id="genericrestapi-none",
        ),
        pytest.param(
            {"name": "peeringmanager", "settings": {"url": "http://localhost:8080", "auth_method": "none"}},
            id="peeringmanager-none",
        ),
    ],
)
def test_a_block_whose_method_needs_no_credential_reports_credential_not_used(source: dict[str, Any]) -> None:
    source = {**source, "credential": "unused"}

    findings = collect_findings(parse_configuration_package(_package(source)))

    assert [(item.code, item.severity, item.location) for item in findings] == [
        ("credential-not-used", "error", "/configuration/source/credential")
    ]
    assert "unused" in findings[0].message


@pytest.mark.parametrize(
    "source",
    [
        pytest.param({"name": "prometheus", "settings": {"auth_method": "bearer"}}, id="prometheus-bearer"),
        pytest.param({"name": "genericrestapi", "settings": {}}, id="genericrestapi-default"),
        pytest.param({"name": "netbox", "settings": {}}, id="netbox"),
    ],
)
def test_a_block_whose_method_needs_a_credential_has_no_credential_not_used(source: dict[str, Any]) -> None:
    source = {**source, "credential": "lab"}
    source["settings"] = {"url": "http://localhost:8080", **source["settings"]}

    findings = collect_findings(parse_configuration_package(_package(source)))

    assert "credential-not-used" not in {item.code for item in findings}


def test_a_block_that_names_no_credential_has_no_credential_not_used() -> None:
    source = {"name": "prometheus", "settings": {"url": "http://localhost:9090", "auth_method": "none"}}

    findings = collect_findings(parse_configuration_package(_package(source)))

    assert "credential-not-used" not in {item.code for item in findings}
