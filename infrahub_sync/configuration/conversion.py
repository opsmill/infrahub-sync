"""Conversion of a v1 or v2 ``config.yml`` into a V3 configuration package.

The conversion is pure: it takes the parsed v1 or v2 mapping, the v2 environment variables
visible where the command runs, and the address flags, and returns a ``ConversionResult``. It
reads no file, opens no network connection and prints nothing; the ``configs convert`` command
owns the input file, the report layout, the confirmation and the write.

A result holds either a package mapping or one or more refusals, never both. Every refusal in
the input is gathered before the result is returned, so one run names every reason the file
cannot be converted.

The report entries never carry a credential value. A ``CredentialEntry`` names where the v2
value came from and which V3 variable replaces it; a ``RefusalEntry`` for a credential setting
holds no value.

``ConversionInputError`` is raised for input that is not a v1 or v2 configuration at all (for
example a package that already declares ``format_version``); the command maps it to exit
status 2. A configuration that is a v1 or v2 configuration but cannot be converted is a
refusal, not an exception.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Literal, NamedTuple
from urllib.parse import urlsplit

from .capabilities import BUILTIN_ADAPTER_CAPABILITIES, get_adapter_capabilities
from .credentials import ENV_CREDENTIAL_PREFIX
from .models import ConfigurationPackageParseError, parse_configuration_package
from .validation import collect_findings

if TYPE_CHECKING:
    from collections.abc import Mapping

    from .capabilities import AdapterConfigurationCapabilities
    from .models import ValidationFinding

CredentialOrigin = Literal["literal", "placeholder", "variable", "none"]
"""Where a v2 credential value came from: the file, a ``${NAME}`` placeholder, a variable, or nowhere."""

SettingOrigin = Literal["config.yml", "variable", "--source-url", "--destination-url"]
"""Where the value written for an environment-first setting came from."""

EnvironmentSettingKind = Literal["address", "credential", "other"]
"""What a v2 setting read from the environment holds: an address, a credential, or another value."""

EnvironmentPrecedence = Literal["variable", "file"]
"""Which value v2 used when the file and a variable both held one."""


@dataclass(frozen=True, slots=True)
class EnvironmentSetting:
    """One v2 setting that v2 read from environment variables, frozen at v2 ``2.0.2``.

    ``variables`` are the names v2 read, in the order v2 tried them; v2 used the first one
    that held a non-empty value. ``precedence`` is ``variable`` when v2 read the variables
    before the file setting and ``file`` when v2 read them only when the file setting was
    empty (``ipfabricsync``).
    """

    setting: str
    variables: tuple[str, ...]
    kind: EnvironmentSettingKind
    precedence: EnvironmentPrecedence = "variable"


V2_ENVIRONMENT_FIRST: Mapping[str, tuple[EnvironmentSetting, ...]] = MappingProxyType(
    {
        "infrahub": (
            EnvironmentSetting("url", ("INFRAHUB_ADDRESS", "INFRAHUB_URL"), "address"),
            EnvironmentSetting("token", ("INFRAHUB_API_TOKEN",), "credential"),
        ),
        "netbox": (
            EnvironmentSetting("url", ("NETBOX_ADDRESS", "NETBOX_URL"), "address"),
            EnvironmentSetting("token", ("NETBOX_TOKEN",), "credential"),
        ),
        "nautobot": (
            EnvironmentSetting("url", ("NAUTOBOT_ADDRESS", "NAUTOBOT_URL"), "address"),
            EnvironmentSetting("token", ("NAUTOBOT_TOKEN",), "credential"),
        ),
        "aci": (
            EnvironmentSetting("url", ("CISCO_APIC_URL",), "address"),
            EnvironmentSetting("username", ("CISCO_APIC_USERNAME",), "credential"),
            EnvironmentSetting("password", ("CISCO_APIC_PASSWORD",), "credential"),
            EnvironmentSetting("verify", ("CISCO_APIC_VERIFY",), "other"),
        ),
        "ipfabricsync": (
            EnvironmentSetting("base_url", ("IPF_URL",), "address", "file"),
            EnvironmentSetting("auth", ("IPF_TOKEN",), "credential", "file"),
        ),
        "prometheus": (
            EnvironmentSetting("url", ("PROM_URL", "PROM_ADDRESS"), "address"),
            EnvironmentSetting("username", ("PROM_USERNAME",), "credential"),
            EnvironmentSetting("password", ("PROM_PASSWORD",), "credential"),
            EnvironmentSetting("token", ("PROM_TOKEN",), "credential"),
        ),
        "genericrestapi": (
            EnvironmentSetting("url", ("URL", "ADDRESS"), "address"),
            EnvironmentSetting("token", ("TOKEN",), "credential"),
            EnvironmentSetting("username", ("USERNAME",), "credential"),
            EnvironmentSetting("password", ("PASSWORD",), "credential"),
        ),
        "peeringmanager": (
            EnvironmentSetting("url", ("PEERING_MANAGER_ADDRESS", "PEERING_MANAGER_URL"), "address"),
            EnvironmentSetting("token", ("PEERING_MANAGER_TOKEN",), "credential"),
            EnvironmentSetting("username", ("USERNAME",), "credential"),
            EnvironmentSetting("password", ("PASSWORD",), "credential"),
        ),
        "slurpitsync": (),
    }
)
"""The v2 settings each bundled adapter read from the environment (research R2).

For ``genericrestapi`` and ``peeringmanager`` the names are v2's defaults; a file's
``*_env_vars`` settings never extend this table (spec FR-030).
"""

# Built from the prefix so that only ``credentials.py`` spells the namespace out
# (``test_no_service_setting_uses_the_credential_namespace``); this module never reads them.
COMPOSE_WORKER_CREDENTIAL_VARIABLES: tuple[str, ...] = tuple(
    f"{ENV_CREDENTIAL_PREFIX}{suffix}" for suffix in ("INFRAHUB_API_TOKEN", "NETBOX_TOKEN", "NAUTOBOT_TOKEN")
)
"""The credential variables the ``sync-worker`` service of ``docker-compose.yml`` passes through."""

_INFRAHUB_TOKEN_IDENTIFIER = f"{ENV_CREDENTIAL_PREFIX}INFRAHUB_API_TOKEN"

_GENERIC_VARIABLES = frozenset({"URL", "ADDRESS"})
"""``genericrestapi``'s default address variables, which other software may also set (research R2)."""


class CredentialReference(NamedTuple):
    """The credential reference name and the V3 environment variable it reads."""

    reference: str
    identifier: str


def credential_reference(adapter: str, setting: str) -> CredentialReference:
    """Return the reference name and identifier for one credential setting (spec FR-006).

    The reference is ``<adapter>-<setting>`` in lower case with ``_`` changed to ``-``; the
    identifier is the credential prefix followed by ``<ADAPTER>_<SETTING>`` in upper case. The
    Infrahub token uses the prefix followed by ``INFRAHUB_API_TOKEN``.

    Raises:
        UnknownAdapterCapabilitiesError: ``adapter`` is not a bundled adapter.
        ValueError: ``setting`` is not a credential setting of ``adapter``.
    """
    if setting not in get_adapter_capabilities(adapter).credential_setting_paths:
        msg = f"{setting!r} is not a credential setting of adapter {adapter!r}"
        raise ValueError(msg)
    reference = f"{adapter}-{setting}".lower().replace("_", "-")
    if adapter == "infrahub" and setting == "token":
        return CredentialReference(reference, _INFRAHUB_TOKEN_IDENTIFIER)
    return CredentialReference(reference, f"{ENV_CREDENTIAL_PREFIX}{adapter}_{setting}".upper())


def _non_credential_variables() -> tuple[str, ...]:
    names: dict[str, None] = {}
    for settings in V2_ENVIRONMENT_FIRST.values():
        for entry in settings:
            if entry.kind != "credential":
                names.update(dict.fromkeys(entry.variables))
    return tuple(names)


V2_NON_CREDENTIAL_VARIABLES: tuple[str, ...] = _non_credential_variables()
"""Every ``address`` and ``other`` variable name of ``V2_ENVIRONMENT_FIRST``, once each."""


def visible_v2_variables(environ: Mapping[str, str]) -> Mapping[str, str]:
    """Return the v2 address and setting variables set in ``environ``, with their values.

    Only the ``address`` and ``other`` names of ``V2_ENVIRONMENT_FIRST`` are looked up, so a
    credential variable or an unrelated name is never read (spec FR-030). A variable that is
    empty counts as not set, as it did for v2. The keys keep the order of the table, so they
    can be reported as the variables visible where the command ran.
    """
    visible: dict[str, str] = {}
    for name in V2_NON_CREDENTIAL_VARIABLES:
        value = environ.get(name)
        if value:
            visible[name] = value
    return MappingProxyType(visible)


class ConversionInputError(Exception):
    """The input is not a v1 or v2 configuration that the conversion can read.

    Raised for a mapping that already declares ``format_version`` (a V3 package) or that does
    not have the shape of a v1 or v2 configuration. The message never holds a setting value.
    """


@dataclass(frozen=True, slots=True)
class CredentialEntry:
    """One credential setting replaced by a credential reference.

    ``location`` is the setting path, for example ``source.settings.token``. ``origin`` is where
    the v2 value came from; ``origin_variable`` names the variable for the ``placeholder`` and
    ``variable`` origins. ``reference`` is the credential reference name written to the package
    and ``identifier`` the V3 environment variable the reference reads. ``worker_receives`` is
    true when the Sync worker service in ``docker-compose.yml`` passes ``identifier`` through.
    The entry never holds the credential value.
    """

    location: str
    origin: CredentialOrigin
    reference: str
    identifier: str
    worker_receives: bool
    origin_variable: str | None = None

    @property
    def warnings(self) -> tuple[str, ...]:
        """The report warnings for this credential (spec FR-008, FR-009), never a value."""
        warnings: list[str] = []
        if self.origin == "literal":
            warnings.append(f"config.yml still holds a literal value for {self.location}; remove it once V3 works")
        if not self.worker_receives:
            warnings.append(
                f"the Compose file shipped with this version does not pass {self.identifier} to the Sync worker; "
                "add it to the worker's environment"
            )
        return tuple(warnings)


@dataclass(frozen=True, slots=True)
class SettingOriginEntry:
    """One setting whose value v2 read from the environment before the file.

    ``value`` is the value written to the package and ``origin`` where it came from;
    ``origin_variable`` names the variable for the ``variable`` origin. ``other_value`` is the
    value that lost when the file and the variable held different values. ``is_loopback`` is
    true when the value names ``localhost`` or ``127.0.0.1``.
    """

    location: str
    value: str
    origin: SettingOrigin
    origin_variable: str | None = None
    other_value: str | None = None
    is_loopback: bool = False

    @property
    def warnings(self) -> tuple[str, ...]:
        """The report warnings for this setting (spec FR-014, research R2)."""
        warnings: list[str] = []
        if self.is_loopback:
            warnings.append(
                f"{self.location} is a loopback address; the Sync worker runs in a container and may not reach it"
            )
        if self.origin == "variable" and self.origin_variable in _GENERIC_VARIABLES:
            warnings.append(
                f"{self.location} came from {self.origin_variable}, a generic variable name that other software "
                "may set; check that it holds the address v2 used"
            )
        return tuple(warnings)


@dataclass(frozen=True, slots=True)
class VisibleVariablesEntry:
    """The v2 address and setting variables that were set where the command ran, names only."""

    names: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DroppedEntry:
    """One key left out of the package, with the reason it has no V3 equivalent."""

    location: str
    reason: str


@dataclass(frozen=True, slots=True)
class NoteEntry:
    """One free note for the engineer, for example that V3 has no generation step."""

    text: str


@dataclass(frozen=True, slots=True)
class RefusalEntry:
    """One reason the configuration cannot be converted.

    ``value`` is the refused value, or ``None`` for a credential setting, whose value is never
    reported. For a name that is not bundled, ``reason`` lists the bundled names.
    """

    location: str
    reason: str
    value: str | None = None


@dataclass(frozen=True, slots=True)
class ConversionResult:
    """What the conversion returns to the command.

    ``package`` is the V3 package mapping, or ``None`` when ``refusals`` is not empty. The
    report sections are kept in the order the command prints them: credentials, setting
    origins, visible variables, dropped keys, notes, then the warning findings from
    ``collect_findings``.
    """

    package: Mapping[str, Any] | None
    refusals: tuple[RefusalEntry, ...] = ()
    credentials: tuple[CredentialEntry, ...] = ()
    setting_origins: tuple[SettingOriginEntry, ...] = ()
    visible_variables: VisibleVariablesEntry | None = None
    dropped: tuple[DroppedEntry, ...] = ()
    notes: tuple[NoteEntry, ...] = ()
    findings: tuple[ValidationFinding, ...] = ()

    def __post_init__(self) -> None:
        """Reject a result that holds both a package and refusals, or neither."""
        if self.refusals and self.package is not None:
            msg = "a refused conversion has no package"
            raise ValueError(msg)
        if not self.refusals and self.package is None:
            msg = "a conversion without refusals has a package"
            raise ValueError(msg)


# --- The conversion --------------------------------------------------------------------

AdapterRole = Literal["source", "destination"]

_PLACEHOLDER = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1"})
_BUNDLED_MODULE_PREFIX = "infrahub_sync.adapters."
_COPIED_KEYS = frozenset({"name", "order", "diffsync_flags", "schema_mapping"})
_DEFAULT_ADDRESS = EnvironmentSetting("url", (), "address")
_ENV_VARS_SUFFIX = "_env_vars"
_ENV_VARS_ADAPTERS = frozenset({"genericrestapi"})

_DROP_REASONS: Mapping[str, str] = MappingProxyType(
    {
        "store": "V3 keeps sync data in memory during a run",
        "adapters_path": "V3 runs only its bundled adapters; custom adapters are planned for 3.0 (INFP-654)",
    }
)
_ENV_VARS_REASON = "V3 reads credentials from credential references and does not read the variables this setting names"
_UNREAD_KEY_REASON = "v2 did not read this key"
_GENERATION_NOTE = "V3 has no generation step; the generated adapter and model files are not needed"
_FLAGS: Mapping[AdapterRole, Literal["--source-url", "--destination-url"]] = MappingProxyType(
    {"source": "--source-url", "destination": "--destination-url"}
)


@dataclass(slots=True)
class _Report:
    """The report entries and the credentials block gathered while the input is converted."""

    refusals: list[RefusalEntry] = field(default_factory=list)
    credentials: list[CredentialEntry] = field(default_factory=list)
    setting_origins: list[SettingOriginEntry] = field(default_factory=list)
    dropped: list[DroppedEntry] = field(default_factory=list)
    notes: list[NoteEntry] = field(default_factory=list)
    findings: list[ValidationFinding] = field(default_factory=list)
    credentials_block: dict[str, dict[str, str]] = field(default_factory=dict)

    def result(self, package: dict[str, Any], visible_names: tuple[str, ...]) -> ConversionResult:
        """Return the result: the package only when nothing was refused."""
        return ConversionResult(
            package=None if self.refusals else package,
            refusals=tuple(self.refusals),
            credentials=tuple(self.credentials),
            setting_origins=tuple(self.setting_origins),
            visible_variables=VisibleVariablesEntry(visible_names),
            dropped=tuple(self.dropped),
            notes=tuple(self.notes),
            findings=tuple(self.findings),
        )


@dataclass(frozen=True, slots=True)
class _Flags:
    """The address flags given on the command line, by role."""

    source: str | None
    destination: str | None

    def for_role(self, role: AdapterRole) -> str | None:
        return self.source if role == "source" else self.destination


def convert_v2_configuration(
    data: Mapping[str, Any],
    visible: Mapping[str, str],
    *,
    source_url: str | None = None,
    destination_url: str | None = None,
) -> ConversionResult:
    """Convert a parsed v1 or v2 ``config.yml`` into a V3 configuration package.

    ``visible`` holds the v2 address and setting variables set where the command runs, as
    returned by ``visible_v2_variables``; only the names of ``V2_ENVIRONMENT_FIRST`` are looked
    up in it. ``source_url`` and ``destination_url`` are the address flags, which override the
    file and the variables. The conversion reads no file and no environment and prints nothing.

    Raises:
        ConversionInputError: ``data`` is a V3 package or not a v1 or v2 configuration.
    """
    _check_shape(data)
    report = _Report()
    flags = _Flags(source_url, destination_url)
    configuration: dict[str, Any] = {}
    for key, value in data.items():
        if key == "source":
            configuration[key] = _convert_adapter("source", value, visible, flags, report)
        elif key == "destination":
            configuration[key] = _convert_adapter("destination", value, visible, flags, report)
        else:
            _convert_top_level(key, value, data, configuration, report)
    report.notes.append(NoteEntry(_GENERATION_NOTE))
    package: dict[str, Any] = {
        "format_version": 1,
        "configuration": configuration,
        "credentials": report.credentials_block,
    }
    if not report.refusals:
        _check_package(package, report)
    names = tuple(name for name in V2_NON_CREDENTIAL_VARIABLES if visible.get(name))
    return report.result(package, names)


def _check_shape(data: Mapping[str, Any]) -> None:
    """Refuse input that is not a v1 or v2 configuration, naming the first location that fails."""
    if not isinstance(data, dict):
        msg = "the file is not a mapping of configuration keys"
        raise ConversionInputError(msg)
    if "format_version" in data:
        msg = "format_version: the file is already a V3 configuration package"
        raise ConversionInputError(msg)
    if not isinstance(data.get("name"), str):
        msg = "name: a v1 or v2 configuration has a name"
        raise ConversionInputError(msg)
    for role in ("source", "destination"):
        block = data.get(role)
        if not isinstance(block, dict) or not isinstance(block.get("name"), str):
            msg = f"{role}: a v1 or v2 configuration has a {role} with a name"
            raise ConversionInputError(msg)
        if not isinstance(block.get("settings") or {}, dict):
            msg = f"{role}.settings: the settings are not a mapping"
            raise ConversionInputError(msg)


def _convert_top_level(
    key: str, value: Any, data: Mapping[str, Any], configuration: dict[str, Any], report: _Report
) -> None:
    """Copy or drop one top-level key (spec FR-017, FR-019)."""
    if key in _COPIED_KEYS:
        configuration[key] = copy.deepcopy(value)
    elif key in _DROP_REASONS:
        report.dropped.append(DroppedEntry(key, _DROP_REASONS[key]))
    elif key == "incremental":
        source_name = data["source"]["name"]
        capabilities = BUILTIN_ADAPTER_CAPABILITIES.get(source_name)
        if capabilities is not None and capabilities.incremental_extraction:
            configuration[key] = copy.deepcopy(value)
        else:
            reason = f"the {source_name} source does not support incremental extraction in V3"
            report.dropped.append(DroppedEntry(key, reason))
    else:
        report.dropped.append(DroppedEntry(key, _UNREAD_KEY_REASON))


def _convert_adapter(
    role: AdapterRole, block: Mapping[str, Any], visible: Mapping[str, str], flags: _Flags, report: _Report
) -> dict[str, Any]:
    """Convert one ``source`` or ``destination`` block."""
    name: str = block["name"]
    converted: dict[str, Any] = {"name": name}
    adapter = block.get("adapter")
    if adapter is not None:
        if _is_bundled_module(adapter, name):
            report.notes.append(
                NoteEntry(f"{role}.adapter names the module of the bundled {name} adapter; V3 uses its bundled adapter")
            )
        else:
            # Refused by the pre-write check until the adapter refusals name it (FR-015).
            converted["adapter"] = adapter
    capabilities = BUILTIN_ADAPTER_CAPABILITIES.get(name)
    if capabilities is None:
        # No settings are carried for an adapter V3 does not bundle: they could hold a
        # credential, and the pre-write check refuses the name.
        converted["settings"] = {}
        return converted
    settings: Mapping[str, Any] = block.get("settings") or {}
    converted["settings"] = _convert_settings(role, name, capabilities, settings, visible, flags, report)
    return converted


def _is_bundled_module(adapter: object, name: str) -> bool:
    """Return whether ``adapter`` names the bundled module of adapter ``name`` (research R7)."""
    if not isinstance(adapter, str):
        return False
    module = adapter.split(":", 1)[0]
    return module == f"{_BUNDLED_MODULE_PREFIX}{name}"


def _environment_settings(name: str) -> dict[str, EnvironmentSetting]:
    """Return the environment-first settings of an adapter, with ``url`` as an address by default."""
    rows = {row.setting: row for row in V2_ENVIRONMENT_FIRST.get(name, ()) if row.kind != "credential"}
    if not any(row.kind == "address" for row in rows.values()):
        rows[_DEFAULT_ADDRESS.setting] = _DEFAULT_ADDRESS
    return rows


def _convert_settings(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    role: AdapterRole,
    name: str,
    capabilities: AdapterConfigurationCapabilities,
    settings: Mapping[str, Any],
    visible: Mapping[str, str],
    flags: _Flags,
    report: _Report,
) -> dict[str, Any]:
    """Convert the settings of one bundled adapter, keeping the order of the file."""
    credential_settings = capabilities.credential_setting_paths
    environment = _environment_settings(name)
    converted: dict[str, Any] = {}
    for key, value in settings.items():
        location = f"{role}.settings.{key}"
        if key in credential_settings:
            converted[key] = _convert_credential(role, name, key, value, report)
        elif key in environment:
            _set_resolved(
                converted, key, _resolve(location, environment[key], value, visible, flags.for_role(role), report)
            )
        elif name in _ENV_VARS_ADAPTERS and key.endswith(_ENV_VARS_SUFFIX):
            report.dropped.append(DroppedEntry(location, _ENV_VARS_REASON))
        else:
            converted[key] = copy.deepcopy(value)
    for key in credential_settings:
        if key not in settings:
            converted[key] = _convert_credential(role, name, key, None, report)
    for key, row in environment.items():
        if key not in settings:
            location = f"{role}.settings.{key}"
            _set_resolved(converted, key, _resolve(location, row, None, visible, flags.for_role(role), report))
    return converted


def _set_resolved(settings: dict[str, Any], key: str, value: object) -> None:
    if value is not None:
        settings[key] = value


# --- Credentials (FR-006 to FR-009) -----------------------------------------------------


def _convert_credential(role: AdapterRole, name: str, setting: str, value: object, report: _Report) -> dict[str, str]:
    """Replace one credential setting by a reference; the value is read only for its kind."""
    reference = credential_reference(name, setting)
    origin, origin_variable = _credential_origin(name, setting, value)
    report.credentials.append(
        CredentialEntry(
            location=f"{role}.settings.{setting}",
            origin=origin,
            reference=reference.reference,
            identifier=reference.identifier,
            worker_receives=reference.identifier in COMPOSE_WORKER_CREDENTIAL_VARIABLES,
            origin_variable=origin_variable,
        )
    )
    report.credentials_block[reference.reference] = {"provider": "env", "identifier": reference.identifier}
    return {"$credential": reference.reference}


def _credential_origin(name: str, setting: str, value: object) -> tuple[CredentialOrigin, str | None]:
    """Return where v2 took a credential from, without keeping the value."""
    placeholder = _placeholder_name(value)
    if placeholder is not None:
        return "placeholder", placeholder
    if not _is_empty(value):
        return "literal", None
    rows = [row for row in V2_ENVIRONMENT_FIRST.get(name, ()) if row.setting == setting]
    if rows:
        return "variable", ", ".join(rows[0].variables)
    return "none", None


def _is_empty(value: object) -> bool:
    """Return whether a file setting holds nothing: absent, null or an empty string, as v2 read it."""
    return value is None or (isinstance(value, str) and not value)


def _placeholder_name(value: object) -> str | None:
    """Return the variable a whole-value ``${NAME}`` placeholder names, or ``None``."""
    if not isinstance(value, str):
        return None
    match = _PLACEHOLDER.fullmatch(value.strip())
    return match.group(1) if match else None


# --- Environment-first settings (FR-010 to FR-014, FR-029, FR-030) ----------------------


@dataclass(frozen=True, slots=True)
class _Candidates:
    """The values one environment-first setting can take, before v2's precedence is applied."""

    file_value: object
    variable: str | None
    variable_value: str | None
    flag: str | None
    placeholder: str | None


def _resolve(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    location: str,
    row: EnvironmentSetting,
    value: object,
    visible: Mapping[str, str],
    flag: str | None,
    report: _Report,
) -> object:
    """Return the value v2 used for one setting, recording its origin, or ``None``."""
    candidates = _candidates(location, row, value, visible, flag, report)
    if row.kind == "address" and _any_user_information(candidates):
        report.refusals.append(
            RefusalEntry(
                location,
                "the address holds a user name or password; remove it from the address and pass it as a credential",
            )
        )
        return None
    chosen = _choose(location, row, candidates)
    if chosen is None:
        _refuse_missing(location, row, candidates, report)
        return None
    report.setting_origins.append(chosen)
    return _chosen_value(chosen, candidates)


def _candidates(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    location: str,
    row: EnvironmentSetting,
    value: object,
    visible: Mapping[str, str],
    flag: str | None,
    report: _Report,
) -> _Candidates:
    """Collect the file value, the first visible v2 variable and the flag for one setting."""
    placeholder = _placeholder_name(value)
    lookup = row.variables
    file_value: object = None if _is_empty(value) else value
    if placeholder is not None:
        file_value = None
        if placeholder in row.variables:
            lookup = (placeholder, *(name for name in row.variables if name != placeholder))
        else:
            report.notes.append(
                NoteEntry(
                    f"{location} holds ${{{placeholder}}}; {placeholder} is not a variable v2 read for this "
                    "setting, so it was not read"
                )
            )
    variable, variable_value = None, None
    for name in lookup:
        found = visible.get(name)
        if found:
            variable, variable_value = name, found
            break
    return _Candidates(file_value, variable, variable_value, flag if row.kind == "address" else None, placeholder)


def _choose(location: str, row: EnvironmentSetting, candidates: _Candidates) -> SettingOriginEntry | None:
    """Apply flag, then v2's precedence between the variable and the file."""
    is_address = row.kind == "address"
    if candidates.flag:
        origin = _FLAGS["source" if location.startswith("source.") else "destination"]
        return SettingOriginEntry(
            location, candidates.flag, origin, is_loopback=_is_loopback(candidates.flag, is_address)
        )
    file_value = candidates.file_value
    variable_value = candidates.variable_value
    use_variable = variable_value is not None and (row.precedence == "variable" or file_value is None)
    if use_variable:
        other = None if file_value is None or str(file_value) == variable_value else str(file_value)
        return SettingOriginEntry(
            location,
            str(variable_value),
            "variable",
            origin_variable=candidates.variable,
            other_value=other,
            is_loopback=_is_loopback(str(variable_value), is_address),
        )
    if file_value is None:
        return None
    other = None if variable_value is None or variable_value == str(file_value) else variable_value
    return SettingOriginEntry(
        location,
        str(file_value),
        "config.yml",
        other_value=other,
        is_loopback=_is_loopback(str(file_value), is_address),
    )


def _chosen_value(chosen: SettingOriginEntry, candidates: _Candidates) -> object:
    """Return the value to write: the file's own value keeps its YAML type."""
    return candidates.file_value if chosen.origin == "config.yml" else chosen.value


def _refuse_missing(location: str, row: EnvironmentSetting, candidates: _Candidates, report: _Report) -> None:
    """Refuse a setting that got no value (FR-012, FR-029); an absent optional setting is left out."""
    names = ", ".join(row.variables) or "none"
    if row.kind == "address":
        flag = _FLAGS["source" if location.startswith("source.") else "destination"]
        reason = f"no address in config.yml or a v2 variable ({names}); pass {flag}"
    elif candidates.placeholder is not None:
        reason = f"no value in config.yml or a v2 variable ({names}); set one of these variables"
    else:
        return
    report.refusals.append(RefusalEntry(location, reason))


def _any_user_information(candidates: _Candidates) -> bool:
    values = (candidates.file_value, candidates.variable_value, candidates.flag)
    return any(isinstance(value, str) and _has_user_information(value) for value in values)


def _has_user_information(address: str) -> bool:
    """Return whether an address holds a user name or password."""
    try:
        parts = urlsplit(address)
    except ValueError:
        return "@" in address
    return parts.username is not None or parts.password is not None


def _is_loopback(address: str, is_address: bool) -> bool:
    if not is_address:
        return False
    try:
        host = urlsplit(address).hostname
    except ValueError:
        return False
    return host in _LOOPBACK_HOSTS


# --- The pre-write check (FR-020) -------------------------------------------------------


def _check_package(package: dict[str, Any], report: _Report) -> None:
    """Run the registration checks that need no connection; errors become refusals (research R6)."""
    try:
        parsed = parse_configuration_package(copy.deepcopy(package))
    except ConfigurationPackageParseError as exc:
        report.refusals.append(RefusalEntry("package", f"the converted package does not register: {exc}"))
        return
    for finding in collect_findings(parsed):
        if finding.severity == "error":
            report.refusals.append(RefusalEntry(finding.location or "/", f"{finding.code}: {finding.message}"))
        else:
            report.findings.append(finding)
