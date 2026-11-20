"""``resolve_runtime_instance`` fills block settings from released values (spec 027, research R6).

A block that names a stored credential declares no credential setting. The worker passes the
fields that the Sync API released for each block, and the field map of the block's adapter says
which field fills which setting.

Every value is a marker text; a check compares digests, so a failed check prints no value.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Any

import pytest

from infrahub_sync.configuration import CredentialConfigurationError, parse_configuration_package
from infrahub_sync.configuration.credentials import is_registered_context
from infrahub_sync.configuration.runtime import resolve_runtime_instance
from tests.configuration.validation_packages import package_data

if TYPE_CHECKING:
    from pathlib import Path

TOKEN = "runtime-marker-token-4a7c"  # noqa: S105 - a test value, not a secret.
USERNAME = "runtime-marker-user"
PASSWORD = "runtime-marker-password-e21d"  # noqa: S105 - a test value, not a secret.
OLD_PATH_VALUE = "runtime-marker-old-path-93fb"


def _digest(value: object) -> str:
    return hashlib.sha256(str(value).encode()).hexdigest()


def _content(source: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return a package whose source names a stored credential; the destination keeps ``$credential``."""
    data = package_data()
    data["credentials"].pop("netbox-token")
    data["configuration"]["source"]["settings"].pop("token")
    data["configuration"]["source"]["credential"] = "source-credential"
    if source is not None:
        data["configuration"]["source"] = {**source, "credential": "source-credential"}
    return data


@pytest.fixture(autouse=True)
def _old_path_destination(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INFRAHUB_SYNC_CREDENTIAL_INFRAHUB_API_TOKEN", OLD_PATH_VALUE)


def test_a_released_token_fills_the_token_setting(tmp_path: Path) -> None:
    package = parse_configuration_package(_content())

    instance = resolve_runtime_instance(package, directory=str(tmp_path), released={"source": {"token": TOKEN}})

    settings = instance.source.settings
    assert settings is not None
    assert _digest(settings["token"]) == _digest(TOKEN)
    assert is_registered_context(settings)
    # The runtime model has no `credential` key: the name belongs to the package.
    assert getattr(instance.source, "credential", None) is None


def test_a_block_with_references_still_resolves_beside_a_block_with_a_stored_credential(tmp_path: Path) -> None:
    package = parse_configuration_package(_content())

    instance = resolve_runtime_instance(package, directory=str(tmp_path), released={"source": {"token": TOKEN}})

    settings = instance.destination.settings
    assert settings is not None
    assert _digest(settings["token"]) == _digest(OLD_PATH_VALUE)


@pytest.mark.parametrize(
    ("source", "released", "filled"),
    [
        (
            {"name": "aci", "settings": {"url": "https://apic.example.com"}},
            {"username": USERNAME, "password": PASSWORD},
            {"username": USERNAME, "password": PASSWORD},
        ),
        (
            {"name": "ipfabricsync", "settings": {"base_url": "https://ipf.example.com"}},
            {"token": TOKEN},
            {"auth": TOKEN},
        ),
        (
            {"name": "slurpitsync", "settings": {"url": "https://slurpit.example.com"}},
            {"token": TOKEN},
            {"api_key": TOKEN},
        ),
    ],
)
def test_the_field_map_of_the_adapter_decides_which_setting_a_field_fills(
    tmp_path: Path, source: dict[str, Any], released: dict[str, str], filled: dict[str, str]
) -> None:
    package = parse_configuration_package(_content(source))

    instance = resolve_runtime_instance(package, directory=str(tmp_path), released={"source": released})

    settings = instance.source.settings
    assert settings is not None
    assert {name: _digest(settings[name]) for name in filled} == {
        name: _digest(value) for name, value in filled.items()
    }


def test_only_the_settings_of_the_field_map_are_written(tmp_path: Path) -> None:
    package = parse_configuration_package(_content())

    instance = resolve_runtime_instance(
        package, directory=str(tmp_path), released={"source": {"token": TOKEN, "username": USERNAME}}
    )

    settings = instance.source.settings
    assert settings is not None
    assert "username" not in settings


def test_a_block_whose_values_were_not_released_is_refused(tmp_path: Path) -> None:
    package = parse_configuration_package(_content())

    with pytest.raises(CredentialConfigurationError, match="source block names a stored credential"):
        resolve_runtime_instance(package, directory=str(tmp_path))


def test_released_values_without_a_needed_field_are_refused_and_show_no_value(tmp_path: Path) -> None:
    package = parse_configuration_package(_content())

    with pytest.raises(CredentialConfigurationError, match="do not hold the fields token") as refused:
        resolve_runtime_instance(package, directory=str(tmp_path), released={"source": {"password": PASSWORD}})

    shown = PASSWORD in str(refused.value)
    assert not shown


def test_a_source_that_is_not_resolved_takes_no_released_value(tmp_path: Path) -> None:
    package = parse_configuration_package(_content())

    instance = resolve_runtime_instance(
        package, directory=str(tmp_path), resolve_source_credentials=False, released={"source": {"token": TOKEN}}
    )

    settings = instance.source.settings
    assert settings is not None
    assert "token" not in settings
