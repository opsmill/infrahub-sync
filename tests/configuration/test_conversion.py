"""The v1 and v2 configuration conversion in ``configuration/conversion.py``."""

from __future__ import annotations

import copy
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
import yaml

from infrahub_sync.configuration import conversion
from infrahub_sync.configuration.capabilities import BUILTIN_ADAPTER_CAPABILITIES
from infrahub_sync.configuration.conversion import (
    COMPOSE_WORKER_CREDENTIAL_VARIABLES,
    V2_ENVIRONMENT_FIRST,
    ConversionResult,
    CredentialEntry,
    CredentialReference,
    EnvironmentSetting,
    SettingOriginEntry,
    convert_v2_configuration,
    credential_reference,
    visible_v2_variables,
)
from infrahub_sync.configuration.models import ValidationFinding, parse_configuration_package
from infrahub_sync.configuration.validation import collect_findings

if TYPE_CHECKING:
    from collections.abc import Iterator

_EXAMPLES = Path(__file__).resolve().parents[1] / "data" / "v2_examples"

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


# --- convert_v2_configuration: shared builders ------------------------------------------

_CANARY = "canary-literal-credential-value"


def _config(
    source_settings: dict[str, Any] | None = None,
    destination_settings: dict[str, Any] | None = None,
    *,
    source_name: str = "netbox",
    **top: object,
) -> dict[str, Any]:
    """Return a v2 configuration mapping; settings default to two remote addresses."""
    data: dict[str, Any] = {
        "name": "from-netbox",
        "source": {
            "name": source_name,
            "settings": {"url": "https://netbox.example.com"} if source_settings is None else source_settings,
        },
        "destination": {
            "name": "infrahub",
            "settings": {"url": "http://infrahub.example.com:8000"}
            if destination_settings is None
            else destination_settings,
        },
    }
    data.update(top)
    return data


def _convert(
    data: dict[str, Any],
    visible: Mapping[str, str] | None = None,
    *,
    source_url: str | None = None,
    destination_url: str | None = None,
) -> ConversionResult:
    return convert_v2_configuration(
        data, {} if visible is None else visible, source_url=source_url, destination_url=destination_url
    )


def _configuration(result: ConversionResult) -> Mapping[str, Any]:
    assert result.refusals == (), result.refusals
    assert result.package is not None
    return result.package["configuration"]


def _credential(result: ConversionResult, location: str) -> CredentialEntry:
    matches = [entry for entry in result.credentials if entry.location == location]
    assert len(matches) == 1, result.credentials
    return matches[0]


def _origin(result: ConversionResult, location: str) -> SettingOriginEntry:
    matches = [entry for entry in result.setting_origins if entry.location == location]
    assert len(matches) == 1, result.setting_origins
    return matches[0]


def _example(directory: str) -> dict[str, Any]:
    loaded = yaml.safe_load((_EXAMPLES / directory / "config.yml").read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


# --- T009: credentials (FR-006 to FR-009) -----------------------------------------------


@pytest.mark.parametrize(
    ("token", "origin", "origin_variable"),
    [
        (_CANARY, "literal", None),
        ("${MY_NETBOX_TOKEN}", "placeholder", "MY_NETBOX_TOKEN"),
        (None, "variable", "NETBOX_TOKEN"),
    ],
    ids=["literal", "placeholder", "no-value"],
)
def test_source_token_becomes_a_reference(token: str | None, origin: str, origin_variable: str | None) -> None:
    settings: dict[str, Any] = {"url": "https://netbox.example.com"}
    if token is not None:
        settings["token"] = token
    result = _convert(_config(settings))
    configuration = _configuration(result)
    assert configuration["source"]["settings"]["token"] == {"$credential": "netbox-token"}
    assert result.package is not None
    assert result.package["credentials"]["netbox-token"] == {
        "provider": "env",
        "identifier": "INFRAHUB_SYNC_CREDENTIAL_NETBOX_TOKEN",
    }
    entry = _credential(result, "source.settings.token")
    assert entry.origin == origin
    assert entry.origin_variable == origin_variable
    assert entry.reference == "netbox-token"
    assert entry.identifier == "INFRAHUB_SYNC_CREDENTIAL_NETBOX_TOKEN"
    assert _CANARY not in repr(result)


def test_literal_adds_the_still_holds_warning() -> None:
    result = _convert(_config({"url": "https://netbox.example.com", "token": _CANARY}))
    entry = _credential(result, "source.settings.token")
    assert any("config.yml still holds a literal value for source.settings.token" in w for w in entry.warnings)


@pytest.mark.parametrize("token", ["${NETBOX_TOKEN}", None])
def test_placeholder_or_no_value_adds_no_literal_warning(token: str | None) -> None:
    settings: dict[str, Any] = {"url": "https://netbox.example.com"}
    if token is not None:
        settings["token"] = token
    entry = _credential(_convert(_config(settings)), "source.settings.token")
    assert not any("literal" in warning for warning in entry.warnings)


def test_destination_infrahub_token_uses_the_infrahub_reference() -> None:
    result = _convert(_config())
    configuration = _configuration(result)
    assert configuration["destination"]["settings"]["token"] == {"$credential": "infrahub-token"}
    entry = _credential(result, "destination.settings.token")
    assert (entry.origin, entry.origin_variable) == ("variable", "INFRAHUB_API_TOKEN")
    assert entry.identifier == "INFRAHUB_SYNC_CREDENTIAL_INFRAHUB_API_TOKEN"


def test_every_declared_credential_setting_gets_a_reference() -> None:
    result = _convert(_config({"url": "https://prometheus.example.com"}, source_name="prometheus"))
    locations = {entry.location for entry in result.credentials}
    assert locations == {
        "source.settings.token",
        "source.settings.username",
        "source.settings.password",
        "destination.settings.token",
    }


def test_setting_without_v2_variable_and_value_has_origin_none() -> None:
    result = _convert(_config({"url": "https://slurpit.example.com"}, source_name="slurpitsync"))
    entry = _credential(result, "source.settings.api_key")
    assert (entry.origin, entry.origin_variable) == ("none", None)


@pytest.mark.parametrize(
    ("source_name", "location"),
    [
        ("netbox", "source.settings.token"),
        ("aci", "source.settings.password"),
        ("infrahub", "destination.settings.token"),
    ],
)
def test_worker_receives_follows_the_compose_variables(source_name: str, location: str) -> None:
    settings = {"url": "https://source.example.com"}
    entry = _credential(_convert(_config(settings, source_name=source_name)), location)
    receives = entry.identifier in COMPOSE_WORKER_CREDENTIAL_VARIABLES
    assert entry.worker_receives is receives
    worker_warnings = [w for w in entry.warnings if "does not pass" in w]
    assert bool(worker_warnings) is not receives
    if not receives:
        assert entry.identifier in worker_warnings[0]


# --- T010: environment-first settings (FR-010 to FR-014, FR-029, FR-030) ----------------

_FILE_URL = "https://netbox-file.example.com"
_VARIABLE_URL = "https://netbox-variable.example.com"


@pytest.mark.parametrize(
    ("file_url", "visible", "expected"),
    [
        (_FILE_URL, {}, (_FILE_URL, "config.yml", None, None)),
        (None, {"NETBOX_URL": _VARIABLE_URL}, (_VARIABLE_URL, "variable", "NETBOX_URL", None)),
        (_FILE_URL, {"NETBOX_URL": _FILE_URL}, (_FILE_URL, "variable", "NETBOX_URL", None)),
        (_FILE_URL, {"NETBOX_ADDRESS": _VARIABLE_URL}, (_VARIABLE_URL, "variable", "NETBOX_ADDRESS", _FILE_URL)),
    ],
    ids=["file-only", "variable-only", "both-equal", "both-differ"],
)
def test_grilling_table(
    file_url: str | None, visible: dict[str, str], expected: tuple[str, str, str | None, str | None]
) -> None:
    """``expected`` is the value written, its origin, the origin variable and the value that lost."""
    settings = {} if file_url is None else {"url": file_url}
    result = _convert(_config(settings), visible)
    assert _configuration(result)["source"]["settings"]["url"] == expected[0]
    entry = _origin(result, "source.settings.url")
    assert (entry.value, entry.origin, entry.origin_variable, entry.other_value) == expected


def test_neither_file_nor_variable_is_refused_naming_flag_and_variables() -> None:
    result = _convert(_config({}))
    assert result.package is None
    refusal = next(item for item in result.refusals if item.location == "source.settings.url")
    assert "--source-url" in refusal.reason
    assert "NETBOX_ADDRESS" in refusal.reason
    assert "NETBOX_URL" in refusal.reason


def test_destination_without_address_names_the_destination_flag() -> None:
    result = _convert(_config(destination_settings={}))
    refusal = next(item for item in result.refusals if item.location == "destination.settings.url")
    assert "--destination-url" in refusal.reason
    assert "INFRAHUB_ADDRESS" in refusal.reason


def test_variables_are_tried_in_v2_order() -> None:
    visible = {"NETBOX_URL": "https://second.example.com", "NETBOX_ADDRESS": "https://first.example.com"}
    entry = _origin(_convert(_config({}), visible), "source.settings.url")
    assert (entry.value, entry.origin_variable) == ("https://first.example.com", "NETBOX_ADDRESS")


def test_flags_override_file_and_variables() -> None:
    visible = {"NETBOX_URL": _VARIABLE_URL, "INFRAHUB_ADDRESS": "http://infrahub-variable.example.com"}
    result = _convert(
        _config({"url": _FILE_URL}),
        visible,
        source_url="https://flag.example.com",
        destination_url="http://infrahub-flag.example.com",
    )
    settings = _configuration(result)
    assert settings["source"]["settings"]["url"] == "https://flag.example.com"
    assert settings["destination"]["settings"]["url"] == "http://infrahub-flag.example.com"
    assert _origin(result, "source.settings.url").origin == "--source-url"
    assert _origin(result, "destination.settings.url").origin == "--destination-url"


@pytest.mark.parametrize(
    ("file_url", "visible", "value", "origin"),
    [
        (
            "https://ipf-file.example.com",
            {"IPF_URL": "https://ipf-variable.example.com"},
            "https://ipf-file.example.com",
            "config.yml",
        ),
        (None, {"IPF_URL": "https://ipf-variable.example.com"}, "https://ipf-variable.example.com", "variable"),
    ],
    ids=["file-wins", "variable-when-file-empty"],
)
def test_ipfabric_reads_the_file_first(file_url: str | None, visible: dict[str, str], value: str, origin: str) -> None:
    settings = {} if file_url is None else {"base_url": file_url}
    entry = _origin(_convert(_config(settings, source_name="ipfabricsync"), visible), "source.settings.base_url")
    assert (entry.value, entry.origin) == (value, origin)


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1"])
def test_loopback_address_warns(host: str) -> None:
    result = _convert(_config(destination_settings={"url": f"http://{host}:8000"}))
    entry = _origin(result, "destination.settings.url")
    assert entry.is_loopback
    assert any("container" in warning for warning in entry.warnings)


def test_remote_address_does_not_warn() -> None:
    entry = _origin(_convert(_config()), "destination.settings.url")
    assert not entry.is_loopback
    assert entry.warnings == ()


@pytest.mark.parametrize("variable", ["URL", "ADDRESS"])
def test_generic_rest_variable_warns(variable: str) -> None:
    settings = {"api_endpoint": "/api", "url": "https://rest-file.example.com"}
    result = _convert(_config(settings, source_name="genericrestapi"), {variable: "https://rest.example.com"})
    entry = _origin(result, "source.settings.url")
    assert entry.origin_variable == variable
    assert any(variable in warning and "generic" in warning for warning in entry.warnings)


def test_aci_verify_placeholder_reads_its_own_variable() -> None:
    settings = {"url": "${CISCO_APIC_URL}", "verify": "${CISCO_APIC_VERIFY}", "api_endpoint": "api"}
    visible = {"CISCO_APIC_URL": "https://apic.example.com", "CISCO_APIC_VERIFY": "false"}
    result = _convert(_config(settings, source_name="aci"), visible)
    source = _configuration(result)["source"]["settings"]
    assert (source["url"], source["verify"]) == ("https://apic.example.com", "false")
    entry = _origin(result, "source.settings.verify")
    assert (entry.origin, entry.origin_variable) == ("variable", "CISCO_APIC_VERIFY")


def test_aci_verify_without_value_is_refused_naming_setting_and_variable() -> None:
    settings = {"url": "https://apic.example.com", "verify": "${CISCO_APIC_VERIFY}"}
    result = _convert(_config(settings, source_name="aci"))
    refusal = next(item for item in result.refusals if item.location == "source.settings.verify")
    assert "CISCO_APIC_VERIFY" in refusal.reason


def test_aci_verify_absent_everywhere_is_left_out() -> None:
    result = _convert(_config({"url": "https://apic.example.com"}, source_name="aci"))
    assert "verify" not in _configuration(result)["source"]["settings"]
    assert all(entry.location != "source.settings.verify" for entry in result.setting_origins)


def test_placeholder_naming_another_variable_is_not_read() -> None:
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

    visible = _RecordingEnviron({"NETBOX_TOKEN": _CANARY})
    result = _convert(_config({"url": "${NETBOX_TOKEN}"}), visible)
    assert "NETBOX_TOKEN" not in visible.read
    assert any("NETBOX_TOKEN" in note.text and "not read" in note.text for note in result.notes)
    assert any(item.location == "source.settings.url" for item in result.refusals)
    assert _CANARY not in repr(result)


@pytest.mark.parametrize(
    ("settings", "visible"),
    [
        ({"url": f"https://user:{_CANARY}@netbox.example.com"}, {}),
        ({"url": "https://netbox.example.com"}, {"NETBOX_URL": f"https://{_CANARY}@netbox.example.com"}),
    ],
    ids=["file", "variable"],
)
def test_address_with_user_information_is_refused_without_printing_it(
    settings: dict[str, str], visible: dict[str, str]
) -> None:
    result = _convert(_config(settings), visible)
    assert result.package is None
    refusal = next(item for item in result.refusals if item.location == "source.settings.url")
    assert refusal.value is None
    assert "user name or password" in refusal.reason
    assert _CANARY not in repr(result)
    assert all(entry.location != "source.settings.url" for entry in result.setting_origins)


def test_visible_variables_entry_lists_names_only() -> None:
    visible = {"NETBOX_URL": _VARIABLE_URL, "CISCO_APIC_VERIFY": "false"}
    result = _convert(_config(), visible)
    assert result.visible_variables is not None
    assert result.visible_variables.names == ("NETBOX_URL", "CISCO_APIC_VERIFY")


def test_visible_variables_entry_is_empty_when_none_is_set() -> None:
    result = _convert(_config())
    assert result.visible_variables is not None
    assert result.visible_variables.names == ()


# --- T011: copied and dropped keys (FR-016 to FR-019) -----------------------------------

_SCHEMA_MAPPING = [
    {
        "name": "BuiltinTag",
        "mapping": "extras.tags",
        "identifiers": ["name"],
        "fields": [{"name": "name", "mapping": "name"}],
    }
]


def test_top_level_and_destination_settings_are_copied_unchanged() -> None:
    destination = {
        "url": "http://infrahub.example.com:8000",
        "branch": "v2-import",
        "source": "netbox-import",
        "owner": "network-team",
        "verify_ssl": False,
    }
    data = _config(
        destination_settings=destination,
        schema_mapping=copy.deepcopy(_SCHEMA_MAPPING),
        order=["BuiltinTag"],
        diffsync_flags=["SKIP_UNMATCHED_DST"],
    )
    configuration = _configuration(_convert(data))
    assert configuration["name"] == "from-netbox"
    assert configuration["schema_mapping"] == _SCHEMA_MAPPING
    assert configuration["order"] == ["BuiltinTag"]
    assert configuration["diffsync_flags"] == ["SKIP_UNMATCHED_DST"]
    settings = configuration["destination"]["settings"]
    for key in ("branch", "source", "owner", "verify_ssl"):
        assert settings[key] == destination[key]


@pytest.mark.parametrize("source_name", ["infrahub", "nautobot", "netbox"])
def test_incremental_is_copied_for_sources_that_support_it(source_name: str) -> None:
    data = _config({"url": "https://source.example.com"}, source_name=source_name, incremental={"full_resync_every": 5})
    result = _convert(data)
    assert result.package is not None, result.refusals
    assert result.package["configuration"]["incremental"] == {"full_resync_every": 5}
    assert all(entry.location != "incremental" for entry in result.dropped)


@pytest.mark.parametrize("source_name", ["aci", "prometheus", "slurpitsync"])
def test_incremental_is_dropped_for_other_sources(source_name: str) -> None:
    data = _config({"url": "https://source.example.com"}, source_name=source_name, incremental={"full_resync_every": 5})
    result = _convert(data)
    dropped = next(entry for entry in result.dropped if entry.location == "incremental")
    assert source_name in dropped.reason
    if result.package is not None:
        assert "incremental" not in result.package["configuration"]


def test_store_and_adapters_path_are_dropped_with_reasons() -> None:
    data = _config(store={"type": "redis", "settings": {"host": "localhost"}}, adapters_path=["./adapters"])
    result = _convert(data)
    configuration = _configuration(result)
    assert "store" not in configuration
    assert "adapters_path" not in configuration
    reasons = {entry.location: entry.reason for entry in result.dropped}
    assert "memory" in reasons["store"]
    assert "INFP-654" in reasons["adapters_path"]


def test_generic_rest_env_vars_settings_are_dropped() -> None:
    settings = {
        "url": "https://rest.example.com",
        "api_endpoint": "/api",
        "url_env_vars": ["CUSTOM_URL"],
        "token_env_vars": ["CUSTOM_TOKEN"],
    }
    result = _convert(_config(settings, source_name="genericrestapi"), {})
    locations = {entry.location for entry in result.dropped}
    assert {"source.settings.url_env_vars", "source.settings.token_env_vars"} <= locations
    if result.package is not None:
        assert "url_env_vars" not in result.package["configuration"]["source"]["settings"]


def test_generation_note_is_present() -> None:
    result = _convert(_config())
    assert any("no generation step" in note.text for note in result.notes)


@pytest.mark.parametrize("adapter", ["infrahub_sync.adapters.netbox", "infrahub_sync.adapters.netbox:NetboxAdapter"])
def test_bundled_module_adapter_is_noted_and_left_out(adapter: str) -> None:
    data = _config()
    data["source"]["adapter"] = adapter
    result = _convert(data)
    assert "adapter" not in _configuration(result)["source"]
    assert any("source.adapter" in note.text and "bundled adapter" in note.text for note in result.notes)


# --- T012: the pre-write check (FR-020) -------------------------------------------------


def test_netbox_example_package_parses_without_error_findings() -> None:
    result = _convert(_example("netbox_to_infrahub"))
    assert result.refusals == ()
    assert result.package is not None
    package = parse_configuration_package(copy.deepcopy(dict(result.package)))
    assert [item for item in collect_findings(package) if item.severity == "error"] == []


def test_error_finding_becomes_a_refusal_naming_its_code(monkeypatch: pytest.MonkeyPatch) -> None:
    forced = ValidationFinding(
        code="forced-error", severity="error", location="/configuration/name", message="forced for the test"
    )
    monkeypatch.setattr(conversion, "collect_findings", lambda _package: (forced,))
    result = _convert(_config())
    assert result.package is None
    assert any("forced-error" in item.reason for item in result.refusals)


def test_warning_finding_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    warning = ValidationFinding(
        code="forced-warning", severity="warning", location="/configuration/name", message="forced for the test"
    )
    monkeypatch.setattr(conversion, "collect_findings", lambda _package: (warning,))
    result = _convert(_config())
    assert result.package is not None
    assert result.findings == (warning,)


# --- T019: a v1 configuration converts the same way -------------------------------------


def test_v1_netbox_example_converts_like_the_v2_example() -> None:
    """v1 has the v2 keys without ``incremental``; neither NetBox example sets it."""
    v1_input = _example("v1_netbox_to_infrahub")
    v2_result = _convert(_example("netbox_to_infrahub"))
    v1_result = _convert(copy.deepcopy(v1_input))
    v1_configuration = _configuration(v1_result)
    v2_configuration = _configuration(v2_result)
    assert v1_result.package is not None
    assert v2_result.package is not None
    assert v1_result.package["credentials"] == v2_result.package["credentials"]
    assert v1_configuration["source"] == v2_configuration["source"]
    assert v1_configuration["destination"] == v2_configuration["destination"]
    expected = copy.deepcopy(v1_input)
    expected["source"]["settings"]["token"] = {"$credential": "netbox-token"}
    expected["destination"]["settings"]["token"] = {"$credential": "infrahub-token"}
    assert dict(v1_configuration) == expected
    assert "incremental" not in v1_configuration
    assert _credential(v1_result, "source.settings.token").origin == "literal"
    assert _credential(v2_result, "source.settings.token").origin == "variable"
