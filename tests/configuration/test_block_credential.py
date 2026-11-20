"""The optional ``credential`` key of the source and destination blocks (spec 027, T014)."""

from __future__ import annotations

import re
from typing import Any

import pytest

from infrahub_sync.configuration import (
    ConfigurationPackageParseError,
    parse_configuration_package,
)
from infrahub_sync.configuration.capabilities import BUILTIN_ADAPTER_CAPABILITIES
from infrahub_sync.configuration.models import CREDENTIAL_NAME_PATTERN, CREDENTIAL_SETTING_NAMES
from infrahub_sync.configuration.validation import collect_findings
from infrahub_sync.product_store.models import STORED_CREDENTIAL_NAME_PATTERN
from tests.configuration.validation_packages import package_data

_ROLES = ("source", "destination")
# The checksum of the shared valid package before the ``credential`` key existed: a package
# that does not use the key keeps the identity it was registered with.
_CHECKSUM_WITHOUT_CREDENTIAL_KEY = "bd4a3240c671745a379e195f988aa25c2b8242759cdb1f6985fd6dffeec75e1b"


def _named_credential_data(**names: str) -> dict[str, Any]:
    """Return the shared package with ``credential`` set on the given roles and no token setting."""
    data = package_data()
    del data["credentials"]
    for role in _ROLES:
        data["configuration"][role]["settings"].pop("token")
    for role, name in names.items():
        data["configuration"][role]["credential"] = name
    return data


@pytest.mark.parametrize("role", _ROLES)
def test_a_block_accepts_a_credential_name(role: str) -> None:
    parsed = parse_configuration_package(_named_credential_data(**{role: "netbox-lab"}))

    block = getattr(parsed.configuration, role)
    assert block.credential == "netbox-lab"
    assert parsed.declared_content()["configuration"][role]["credential"] == "netbox-lab"
    assert collect_findings(parsed) == ()


@pytest.mark.parametrize("role", _ROLES)
def test_a_block_without_a_credential_name_keeps_its_declared_content(role: str) -> None:
    parsed = parse_configuration_package(_named_credential_data())

    assert getattr(parsed.configuration, role).credential is None
    assert "credential" not in parsed.declared_content()["configuration"][role]


def test_a_package_without_the_credential_key_keeps_its_checksum() -> None:
    assert parse_configuration_package(package_data()).checksum() == _CHECKSUM_WITHOUT_CREDENTIAL_KEY


@pytest.mark.parametrize(
    "name",
    [
        pytest.param("a", id="one-letter"),
        pytest.param("netbox-lab", id="hyphen"),
        pytest.param("infrahub2", id="digit"),
        pytest.param("a" + "b" * 62, id="63-characters"),
    ],
)
@pytest.mark.parametrize("role", _ROLES)
def test_the_credential_name_pattern_accepts(role: str, name: str) -> None:
    assert (
        getattr(parse_configuration_package(_named_credential_data(**{role: name})).configuration, role).credential
        == name
    )


@pytest.mark.parametrize(
    "name",
    [
        pytest.param("", id="empty"),
        pytest.param("Netbox", id="uppercase"),
        pytest.param("1netbox", id="leading-digit"),
        pytest.param("-netbox", id="leading-hyphen"),
        pytest.param("netbox_lab", id="underscore"),
        pytest.param("netbox lab", id="space"),
        pytest.param("a" + "b" * 63, id="64-characters"),
    ],
)
@pytest.mark.parametrize("role", _ROLES)
def test_the_credential_name_pattern_refuses(role: str, name: str) -> None:
    with pytest.raises(ConfigurationPackageParseError) as caught:
        parse_configuration_package(_named_credential_data(**{role: name}))

    assert str(caught.value) == (
        f"configuration package is invalid at /configuration/{role}/credential: does not match required pattern"
    )


@pytest.mark.parametrize("role", _ROLES)
def test_a_credential_name_that_is_not_a_string_is_refused(role: str) -> None:
    data = _named_credential_data()
    data["configuration"][role]["credential"] = 7

    with pytest.raises(ConfigurationPackageParseError, match=f"/configuration/{role}/credential: wrong type"):
        parse_configuration_package(data)


def test_the_stored_credential_name_pattern_is_the_block_pattern() -> None:
    assert STORED_CREDENTIAL_NAME_PATTERN == CREDENTIAL_NAME_PATTERN == r"^[a-z][a-z0-9-]{0,62}$"


@pytest.mark.parametrize(
    "value",
    [
        pytest.param("credential-value-canary", id="inline-value"),
        pytest.param({"$credential": "netbox-token"}, id="reference"),
    ],
)
@pytest.mark.parametrize("role", _ROLES)
def test_a_block_that_names_a_credential_refuses_its_credential_settings(role: str, value: object) -> None:
    data = _named_credential_data(**{role: "netbox-lab"})
    data["configuration"][role]["settings"]["token"] = value

    with pytest.raises(ConfigurationPackageParseError) as caught:
        parse_configuration_package(data)

    message = str(caught.value)
    assert message == (
        f"configuration package is invalid at /configuration/{role}/settings/token: "
        "credential setting refused because the block names a credential"
    )
    assert "canary" not in message


@pytest.mark.parametrize(
    ("adapter", "setting"),
    [
        pytest.param(adapter, setting, id=f"{adapter}-{setting}")
        for adapter, capabilities in sorted(BUILTIN_ADAPTER_CAPABILITIES.items())
        for setting in capabilities.credential_setting_paths
    ],
)
def test_every_credential_setting_of_a_source_adapter_is_refused(adapter: str, setting: str) -> None:
    data = _named_credential_data(source="lab")
    data["configuration"]["source"] = {
        "name": adapter,
        "credential": "lab",
        "settings": {setting: "credential-value-canary"},
    }

    with pytest.raises(ConfigurationPackageParseError, match=re.escape(f"/configuration/source/settings/{setting}:")):
        parse_configuration_package(data)


def test_the_refused_setting_names_are_the_declared_credential_settings() -> None:
    declared = {path for item in BUILTIN_ADAPTER_CAPABILITIES.values() for path in item.credential_setting_paths}

    assert declared == CREDENTIAL_SETTING_NAMES


@pytest.mark.parametrize(("role", "reference"), [("source", "netbox-token"), ("destination", "infrahub-token")])
def test_a_block_without_a_credential_name_keeps_its_credential_settings(role: str, reference: str) -> None:
    parsed = parse_configuration_package(package_data())

    assert getattr(parsed.configuration, role).settings["token"] == {"$credential": reference}


@pytest.mark.parametrize(
    ("first", "second"),
    [
        pytest.param({"source": "netbox-lab"}, {"source": "netbox-prod"}, id="source-renamed"),
        pytest.param({"destination": "infrahub-a"}, {"destination": "infrahub-b"}, id="destination-renamed"),
        pytest.param({}, {"source": "netbox-lab"}, id="source-added"),
        pytest.param({"source": "shared"}, {"destination": "shared"}, id="moved-to-the-other-block"),
    ],
)
def test_the_checksum_changes_when_the_credential_name_changes(first: dict[str, str], second: dict[str, str]) -> None:
    first_checksum = parse_configuration_package(_named_credential_data(**first)).checksum()
    second_checksum = parse_configuration_package(_named_credential_data(**second)).checksum()

    assert first_checksum != second_checksum


def test_a_package_with_the_old_credentials_block_still_parses() -> None:
    parsed = parse_configuration_package(package_data())

    assert set(parsed.credentials) == {"netbox-token", "infrahub-token"}
    assert collect_findings(parsed) == ()
