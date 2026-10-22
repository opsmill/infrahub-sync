"""The v1 and v2 configuration conversion in ``configuration/conversion.py``."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

import pytest

from infrahub_sync.configuration.capabilities import BUILTIN_ADAPTER_CAPABILITIES
from infrahub_sync.configuration.conversion import (
    V2_ENVIRONMENT_FIRST,
    CredentialReference,
    EnvironmentSetting,
    credential_reference,
    visible_v2_variables,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

# --- V2_ENVIRONMENT_FIRST: one row per line of research R2 ------------------------------

_R2_ROWS: list[tuple[str, str, tuple[str, ...], str, str]] = [
    ("infrahub", "url", ("INFRAHUB_ADDRESS", "INFRAHUB_URL"), "address", "variable"),
    ("infrahub", "token", ("INFRAHUB_API_TOKEN",), "credential", "variable"),
    ("netbox", "url", ("NETBOX_ADDRESS", "NETBOX_URL"), "address", "variable"),
    ("netbox", "token", ("NETBOX_TOKEN",), "credential", "variable"),
    ("nautobot", "url", ("NAUTOBOT_ADDRESS", "NAUTOBOT_URL"), "address", "variable"),
    ("nautobot", "token", ("NAUTOBOT_TOKEN",), "credential", "variable"),
    ("aci", "url", ("CISCO_APIC_URL",), "address", "variable"),
    ("aci", "username", ("CISCO_APIC_USERNAME",), "credential", "variable"),
    ("aci", "password", ("CISCO_APIC_PASSWORD",), "credential", "variable"),
    ("aci", "verify", ("CISCO_APIC_VERIFY",), "other", "variable"),
    ("ipfabricsync", "base_url", ("IPF_URL",), "address", "file"),
    ("ipfabricsync", "auth", ("IPF_TOKEN",), "credential", "file"),
    ("prometheus", "url", ("PROM_URL", "PROM_ADDRESS"), "address", "variable"),
    ("prometheus", "username", ("PROM_USERNAME",), "credential", "variable"),
    ("prometheus", "password", ("PROM_PASSWORD",), "credential", "variable"),
    ("prometheus", "token", ("PROM_TOKEN",), "credential", "variable"),
    ("genericrestapi", "url", ("URL", "ADDRESS"), "address", "variable"),
    ("genericrestapi", "token", ("TOKEN",), "credential", "variable"),
    ("genericrestapi", "username", ("USERNAME",), "credential", "variable"),
    ("genericrestapi", "password", ("PASSWORD",), "credential", "variable"),
    ("peeringmanager", "url", ("PEERING_MANAGER_ADDRESS", "PEERING_MANAGER_URL"), "address", "variable"),
    ("peeringmanager", "token", ("PEERING_MANAGER_TOKEN",), "credential", "variable"),
    ("peeringmanager", "username", ("USERNAME",), "credential", "variable"),
    ("peeringmanager", "password", ("PASSWORD",), "credential", "variable"),
]


def _row(adapter: str, setting: str) -> EnvironmentSetting:
    matches = [entry for entry in V2_ENVIRONMENT_FIRST[adapter] if entry.setting == setting]
    assert len(matches) == 1, f"{adapter}.{setting} appears {len(matches)} times"
    return matches[0]


@pytest.mark.parametrize(
    ("adapter", "setting", "variables", "kind", "precedence"),
    _R2_ROWS,
    ids=[f"{row[0]}-{row[1]}" for row in _R2_ROWS],
)
def test_environment_first_row(
    adapter: str, setting: str, variables: tuple[str, ...], kind: str, precedence: str
) -> None:
    """Each row names v2's variables in v2's order, the kind, and which value v2 preferred."""
    assert _row(adapter, setting) == EnvironmentSetting(setting, variables, kind, precedence)  # ty: ignore[invalid-argument-type]


def test_environment_first_has_no_other_rows() -> None:
    """The table holds the R2 rows and nothing else; ``slurpitsync`` read no variable."""
    actual = {(adapter, entry.setting) for adapter, entries in V2_ENVIRONMENT_FIRST.items() for entry in entries}
    assert actual == {(row[0], row[1]) for row in _R2_ROWS}
    assert V2_ENVIRONMENT_FIRST["slurpitsync"] == ()


def test_environment_first_covers_every_bundled_adapter() -> None:
    assert set(V2_ENVIRONMENT_FIRST) == set(BUILTIN_ADAPTER_CAPABILITIES)


@pytest.mark.parametrize(
    ("adapter", "setting"),
    [(row[0], row[1]) for row in _R2_ROWS if row[3] == "credential"],
)
def test_credential_row_is_a_declared_credential_setting(adapter: str, setting: str) -> None:
    """Every ``credential`` row is a ``credential_setting_paths`` entry of the adapter."""
    assert setting in BUILTIN_ADAPTER_CAPABILITIES[adapter].credential_setting_paths


@pytest.mark.parametrize(
    ("adapter", "setting"),
    [(row[0], row[1]) for row in _R2_ROWS if row[3] != "credential"],
)
def test_non_credential_row_is_not_a_credential_setting(adapter: str, setting: str) -> None:
    assert setting not in BUILTIN_ADAPTER_CAPABILITIES[adapter].credential_setting_paths


# --- credential_reference ---------------------------------------------------------------

_CREDENTIAL_SETTINGS = [
    (adapter, setting)
    for adapter, capabilities in sorted(BUILTIN_ADAPTER_CAPABILITIES.items())
    for setting in capabilities.credential_setting_paths
]

_EXPECTED_REFERENCES = {
    ("aci", "username"): ("aci-username", "INFRAHUB_SYNC_CREDENTIAL_ACI_USERNAME"),
    ("aci", "password"): ("aci-password", "INFRAHUB_SYNC_CREDENTIAL_ACI_PASSWORD"),
    ("genericrestapi", "token"): ("genericrestapi-token", "INFRAHUB_SYNC_CREDENTIAL_GENERICRESTAPI_TOKEN"),
    ("genericrestapi", "username"): ("genericrestapi-username", "INFRAHUB_SYNC_CREDENTIAL_GENERICRESTAPI_USERNAME"),
    ("genericrestapi", "password"): ("genericrestapi-password", "INFRAHUB_SYNC_CREDENTIAL_GENERICRESTAPI_PASSWORD"),
    ("infrahub", "token"): ("infrahub-token", "INFRAHUB_SYNC_CREDENTIAL_INFRAHUB_API_TOKEN"),
    ("ipfabricsync", "auth"): ("ipfabricsync-auth", "INFRAHUB_SYNC_CREDENTIAL_IPFABRICSYNC_AUTH"),
    ("nautobot", "token"): ("nautobot-token", "INFRAHUB_SYNC_CREDENTIAL_NAUTOBOT_TOKEN"),
    ("netbox", "token"): ("netbox-token", "INFRAHUB_SYNC_CREDENTIAL_NETBOX_TOKEN"),
    ("peeringmanager", "token"): ("peeringmanager-token", "INFRAHUB_SYNC_CREDENTIAL_PEERINGMANAGER_TOKEN"),
    ("peeringmanager", "username"): ("peeringmanager-username", "INFRAHUB_SYNC_CREDENTIAL_PEERINGMANAGER_USERNAME"),
    ("peeringmanager", "password"): ("peeringmanager-password", "INFRAHUB_SYNC_CREDENTIAL_PEERINGMANAGER_PASSWORD"),
    ("prometheus", "token"): ("prometheus-token", "INFRAHUB_SYNC_CREDENTIAL_PROMETHEUS_TOKEN"),
    ("prometheus", "username"): ("prometheus-username", "INFRAHUB_SYNC_CREDENTIAL_PROMETHEUS_USERNAME"),
    ("prometheus", "password"): ("prometheus-password", "INFRAHUB_SYNC_CREDENTIAL_PROMETHEUS_PASSWORD"),
    ("slurpitsync", "api_key"): ("slurpitsync-api-key", "INFRAHUB_SYNC_CREDENTIAL_SLURPITSYNC_API_KEY"),
    ("slurpitsync", "token"): ("slurpitsync-token", "INFRAHUB_SYNC_CREDENTIAL_SLURPITSYNC_TOKEN"),
}


def test_expected_references_cover_every_credential_setting() -> None:
    assert set(_EXPECTED_REFERENCES) == set(_CREDENTIAL_SETTINGS)


@pytest.mark.parametrize(
    ("adapter", "setting"), _CREDENTIAL_SETTINGS, ids=[f"{a}-{s}" for a, s in _CREDENTIAL_SETTINGS]
)
def test_credential_reference(adapter: str, setting: str) -> None:
    """The reference and identifier follow spec FR-006 for every bundled credential setting."""
    assert credential_reference(adapter, setting) == CredentialReference(*_EXPECTED_REFERENCES[adapter, setting])


@pytest.mark.parametrize(("adapter", "setting"), [("netbox", "url"), ("aci", "verify"), ("infrahub", "branch")])
def test_credential_reference_refuses_a_setting_that_is_not_a_credential(adapter: str, setting: str) -> None:
    with pytest.raises(ValueError, match="is not a credential setting"):
        credential_reference(adapter, setting)


# --- visible_v2_variables ---------------------------------------------------------------

_CREDENTIAL_VARIABLES = sorted(
    {
        name
        for entries in V2_ENVIRONMENT_FIRST.values()
        for entry in entries
        if entry.kind == "credential"
        for name in entry.variables
    }
)


def test_visible_variables_returns_set_address_and_other_names_in_table_order() -> None:
    environ = {
        "NETBOX_URL": "https://netbox.example",
        "INFRAHUB_ADDRESS": "http://infrahub:8000",
        "CISCO_APIC_VERIFY": "false",
    }
    visible = visible_v2_variables(environ)
    assert dict(visible) == environ
    assert tuple(visible) == ("INFRAHUB_ADDRESS", "NETBOX_URL", "CISCO_APIC_VERIFY")


def test_visible_variables_ignores_an_empty_variable() -> None:
    assert dict(visible_v2_variables({"NETBOX_URL": ""})) == {}


@pytest.mark.parametrize("name", _CREDENTIAL_VARIABLES)
def test_visible_variables_never_returns_a_credential_variable(name: str) -> None:
    """A credential variable set where the command runs is neither returned nor read."""

    class _RecordingEnviron(Mapping[str, str]):
        def __init__(self, data: dict[str, str]) -> None:
            self._data = data
            self.read: list[str] = []

        def __getitem__(self, key: str) -> str:
            self.read.append(key)
            return self._data[key]

        def __iter__(self) -> Iterator[str]:
            return iter(self._data)

        def __len__(self) -> int:
            return len(self._data)

    environ = _RecordingEnviron({name: "canary-credential-value"})
    assert dict(visible_v2_variables(environ)) == {}
    assert name not in environ.read


@pytest.mark.parametrize("name", ["HOME", "PATH", "INFRAHUB_SYNC_CREDENTIAL_NETBOX_TOKEN", "NETBOX_SOMETHING", "url"])
def test_visible_variables_never_returns_an_unrelated_name(name: str) -> None:
    assert dict(visible_v2_variables({name: "value"})) == {}
