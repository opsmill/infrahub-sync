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
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Literal, NamedTuple
from urllib.parse import urlsplit

from .capabilities import BUILTIN_ADAPTER_CAPABILITIES, get_adapter_capabilities
from .conversion_authentication import UnknownAuthenticationMethodError, UsedCredentials, used_credentials
from .credentials import ENV_CREDENTIAL_PREFIX
from .models import ConfigurationPackageParseError, parse_configuration_package
from .validation import collect_findings

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

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


def _non_credential_variables(adapters: Iterable[str] | None = None) -> tuple[str, ...]:
    """Return the ``address`` and ``other`` variable names of ``adapters`` (default: all), once each."""
    names: dict[str, None] = {}
    for adapter in V2_ENVIRONMENT_FIRST if adapters is None else adapters:
        for entry in V2_ENVIRONMENT_FIRST.get(adapter, ()):
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

    def __post_init__(self) -> None:
        """Reject a ``placeholder`` or ``variable`` origin that does not name its variable."""
        _check_origin_variable(self.origin, self.origin_variable)

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
    """One setting that v2 could read from the environment, with the value written and its origin.

    ``value`` is the value written to the package and ``origin`` where it came from;
    ``origin_variable`` names the variable for the ``variable`` origin. ``other_value`` is the
    value that lost when the file and the variable held different values.
    """

    location: str
    value: str
    origin: SettingOrigin
    origin_variable: str | None = None
    other_value: str | None = None

    def __post_init__(self) -> None:
        """Reject a ``variable`` origin that does not name its variable."""
        _check_origin_variable(self.origin, self.origin_variable)

    @property
    def is_loopback(self) -> bool:
        """Whether the value is an address on ``localhost`` or ``127.0.0.1``."""
        return _is_loopback(self.value)

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


def _check_origin_variable(origin: str, origin_variable: str | None) -> None:
    if origin in _ORIGINS_WITH_A_VARIABLE and origin_variable is None:
        msg = f"the {origin} origin needs origin_variable"
        raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class VisibleVariablesEntry:
    """The v2 address and setting variables that were set where the command ran, names only.

    ``looked_up`` names the variables v2 read for the configuration's source and destination
    adapters, so the report can name them when none was set (spec FR-013).
    """

    names: tuple[str, ...]
    looked_up: tuple[str, ...] = ()


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

    ``value`` is the refused value, or ``None`` when it is not reported: for a credential
    setting, a setting the bundled adapter does not accept (its value could be a credential),
    an address with a user name or password, and a value that is a mapping or a list. For a
    name that is not bundled, ``reason`` lists the bundled names.
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
# The v2 adapters that read ``<setting>_env_vars`` from the file: the generic REST adapter
# and Peering Manager, which is built on it and only changes the default names.
_ENV_VARS_ADAPTERS = frozenset({"genericrestapi", "peeringmanager"})
_ADAPTER_BLOCK_KEYS = frozenset({"name", "adapter", "settings"})
_ONLY_DESTINATION = "infrahub"
_CUSTOM_ADAPTER_REASON = "custom adapters are not available in this release (INFP-654, planned for 3.0)"
_PLACEHOLDER_REASON = (
    "v2 did not read this setting from the environment and used the placeholder text as its value; "
    "write the value in config.yml"
)
_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")
_ORIGINS_WITH_A_VARIABLE = frozenset({"placeholder", "variable"})
_ROLE_ORDER: Mapping[str, int] = MappingProxyType({"source": 0, "destination": 1})

_DROP_REASONS: Mapping[str, str] = MappingProxyType(
    {
        "store": "V3 keeps sync data in memory during a run",
        "adapters_path": "V3 runs only its bundled adapters; custom adapters are planned for 3.0 (INFP-654)",
    }
)
_ENV_VARS_REASON = (
    "V3 does not read the variables this setting names; credentials come from credential references "
    "and other settings from the package"
)
_UNREAD_KEY_REASON = "v2 did not read this key"
_GENERATION_NOTE = "V3 has no generation step; the generated adapter and model files are not needed"
_FlagName = Literal["--source-url", "--destination-url"]
_FLAGS: Mapping[AdapterRole, _FlagName] = MappingProxyType(
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

    def result(self, package: dict[str, Any], visible_variables: VisibleVariablesEntry) -> ConversionResult:
        """Return the result: the package only when nothing was refused.

        The refusals are ordered by location: the source first, then the destination, then
        the rest; within each, in the order they were found, which follows the file.
        """
        refusals = sorted(
            self.refusals, key=lambda item: _ROLE_ORDER.get(item.location.split(".", 1)[0], len(_ROLE_ORDER))
        )
        return ConversionResult(
            package=None if refusals else package,
            refusals=tuple(refusals),
            credentials=tuple(self.credentials),
            setting_origins=tuple(self.setting_origins),
            visible_variables=visible_variables,
            dropped=tuple(self.dropped),
            notes=tuple(self.notes),
            findings=tuple(self.findings),
        )


class _Flag(NamedTuple):
    """One address flag: its name, and the value given on the command line or ``None``."""

    name: _FlagName
    value: str | None


@dataclass(frozen=True, slots=True)
class _Flags:
    """The address flags given on the command line, by role."""

    source: str | None
    destination: str | None

    def for_role(self, role: AdapterRole) -> _Flag:
        return _Flag(_FLAGS[role], self.source if role == "source" else self.destination)


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
    looked_up = _non_credential_variables((data["source"]["name"], data["destination"]["name"]))
    return report.result(package, VisibleVariablesEntry(names, looked_up))


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
        settings = block.get("settings")
        if settings is not None and not isinstance(settings, dict):
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
    capabilities = BUILTIN_ADAPTER_CAPABILITIES.get(name)
    if capabilities is None:
        report.refusals.append(RefusalEntry(f"{role}.name", _not_bundled_reason(name), name))
    elif role == "destination" and name != _ONLY_DESTINATION:
        report.refusals.append(RefusalEntry(f"{role}.name", _destination_reason(name), name))
    adapter = block.get("adapter")
    if adapter is not None:
        if _is_bundled_module(adapter, name):
            report.notes.append(
                NoteEntry(f"{role}.adapter names the module of the bundled {name} adapter; V3 uses its bundled adapter")
            )
        else:
            report.refusals.append(RefusalEntry(f"{role}.adapter", _CUSTOM_ADAPTER_REASON, _printable(adapter)))
    # v2 ignored any other key of the block, so it is dropped and reported like a top-level key.
    report.dropped.extend(
        DroppedEntry(f"{role}.{key}", _UNREAD_KEY_REASON) for key in block if key not in _ADAPTER_BLOCK_KEYS
    )
    if capabilities is None:
        # The settings of an adapter V3 does not bundle are not read: any of them could hold
        # a credential, and the name is already refused.
        converted["settings"] = {}
        return converted
    settings: Mapping[str, Any] = block.get("settings") or {}
    converted["settings"] = _convert_settings(role, name, capabilities, settings, visible, flags, report)
    return converted


def _is_bundled_module(adapter: object, name: str) -> bool:
    """Return whether ``adapter`` names the bundled module of adapter ``name`` (research R7)."""
    if not isinstance(adapter, str) or name not in BUILTIN_ADAPTER_CAPABILITIES:
        return False
    module = adapter.split(":", 1)[0]
    return module == f"{_BUNDLED_MODULE_PREFIX}{name}"


def _not_bundled_reason(name: str) -> str:
    return (
        f"{name} is not a bundled adapter; the bundled adapters are {', '.join(sorted(BUILTIN_ADAPTER_CAPABILITIES))}"
    )


def _destination_reason(name: str) -> str:
    return f"V3 does not write to {name} yet; this release writes only to Infrahub"


def _printable(value: object) -> str | None:
    """Return a scalar value as text for a refusal, or ``None`` for a mapping or a list."""
    if isinstance(value, (str, int, float, bool)):
        return str(value)
    return None


def _holds_placeholder(value: object) -> bool:
    """Return whether a ``${NAME}`` placeholder appears anywhere in a setting value."""
    if isinstance(value, str):
        return _PLACEHOLDER.search(value) is not None
    if isinstance(value, dict):
        return any(_holds_placeholder(item) for item in value.values())
    if isinstance(value, list):
        return any(_holds_placeholder(item) for item in value)
    return False


def _refuse_other_setting(name: str, key: str, value: object, location: str, report: _Report) -> bool:
    """Refuse a setting the bundled adapter does not accept, or that holds a placeholder v2 did not read.

    The value of a setting not accepted is never reported: such a setting is not declared as a
    credential, and its value could still be one whatever its name.
    """
    capabilities = BUILTIN_ADAPTER_CAPABILITIES[name]
    if key not in capabilities.allowed_settings:
        report.refusals.append(RefusalEntry(location, f"the bundled {name} adapter does not accept this setting"))
        return True
    if _holds_placeholder(value):
        report.refusals.append(RefusalEntry(location, _PLACEHOLDER_REASON, _printable(value)))
        return True
    return False


def _replaced_variables(name: str, settings: Mapping[str, Any]) -> frozenset[str]:
    """Return the settings whose v2 variables the file replaced with a ``<setting>_env_vars`` list.

    v2 read only the variables such a list names, and not its default names. The command
    reads neither (spec FR-030), so the value of these settings comes from the file or a flag.
    A list equal to v2's default names replaces nothing.
    """
    if name not in _ENV_VARS_ADAPTERS:
        return frozenset()
    defaults = {row.setting: list(row.variables) for row in V2_ENVIRONMENT_FIRST.get(name, ())}
    replaced: set[str] = set()
    for key, value in settings.items():
        if isinstance(key, str) and key.endswith(_ENV_VARS_SUFFIX):
            setting = key.removesuffix(_ENV_VARS_SUFFIX)
            if value != defaults.get(setting):
                replaced.add(setting)
    return frozenset(replaced)


def _environment_settings(name: str, replaced: frozenset[str]) -> dict[str, EnvironmentSetting]:
    """Return the environment-first settings of an adapter, with ``url`` as an address by default.

    A setting in ``replaced`` keeps its row with no variable, so only the file and a flag are read.
    """
    rows = {
        row.setting: replace(row, variables=()) if row.setting in replaced else row
        for row in V2_ENVIRONMENT_FIRST.get(name, ())
        if row.kind != "credential"
    }
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
    used = _used_credentials(role, name, credential_settings, settings, report)
    replaced = _replaced_variables(name, settings)
    environment = _environment_settings(name, replaced)
    flag = flags.for_role(role)
    converted: dict[str, Any] = {}
    for key, value in settings.items():
        location = f"{role}.settings.{key}"
        if key in credential_settings:
            _set_resolved(converted, key, _reference_credential(role, name, key, value, used, report, replaced))
        elif key in environment:
            _set_resolved(converted, key, _resolve(location, environment[key], value, visible, flag, report))
        elif name in _ENV_VARS_ADAPTERS and isinstance(key, str) and key.endswith(_ENV_VARS_SUFFIX):
            report.dropped.append(DroppedEntry(location, _ENV_VARS_REASON))
        elif not _refuse_other_setting(name, key, value, location, report):
            converted[key] = copy.deepcopy(value)
    for key in credential_settings:
        if key not in settings:
            _set_resolved(converted, key, _reference_credential(role, name, key, None, used, report, replaced))
    for key, row in environment.items():
        if key not in settings:
            location = f"{role}.settings.{key}"
            _set_resolved(converted, key, _resolve(location, row, None, visible, flag, report))
    return converted


def _set_resolved(settings: dict[str, Any], key: str, value: object) -> None:
    if value is not None:
        settings[key] = value


# --- Credentials (FR-006 to FR-009) -----------------------------------------------------


def _used_credentials(
    role: AdapterRole, name: str, declared: tuple[str, ...], settings: Mapping[str, Any], report: _Report
) -> UsedCredentials:
    """Return the credential settings the v2 authentication method of this block used (spec FR-006).

    An ``auth_method`` that v2 did not accept is refused; an ``auth_method`` with a placeholder
    is already refused as a setting v2 did not read from the environment. Either way every
    declared setting is kept, so the refusal is the only thing reported.
    """
    method = settings.get("auth_method")
    if _holds_placeholder(method):
        return UsedCredentials(frozenset(declared), "")
    try:
        return used_credentials(name, declared, settings)
    except UnknownAuthenticationMethodError as exc:
        report.refusals.append(RefusalEntry(f"{role}.settings.auth_method", str(exc), _printable(method)))
        return UsedCredentials(frozenset(declared), "")


def _reference_credential(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    role: AdapterRole,
    name: str,
    setting: str,
    value: object,
    used: UsedCredentials,
    report: _Report,
    replaced: frozenset[str],
) -> dict[str, str] | None:
    """Return the reference for a credential setting v2 used or the file fills, or note that it has none."""
    if setting in used.settings or not _is_empty(value):
        return _convert_credential(role, name, setting, value, report, replaced=replaced)
    report.notes.append(NoteEntry(f"not referenced: {role}.settings.{setting} ({used.reason})"))
    return None


def _convert_credential(  # pylint: disable=too-many-arguments
    role: AdapterRole,
    name: str,
    setting: str,
    value: object,
    report: _Report,
    *,
    replaced: frozenset[str] = frozenset(),
) -> dict[str, str]:
    """Replace one credential setting by a reference; the value is read only for its kind."""
    reference = credential_reference(name, setting)
    origin, origin_variable = _credential_origin(name, setting, value, replaced)
    location = f"{role}.settings.{setting}"
    shared = next((entry.location for entry in report.credentials if entry.reference == reference.reference), None)
    if shared is not None:
        # The source and the destination use the same adapter, so one reference serves both.
        report.notes.append(
            NoteEntry(
                f"{shared} and {location} share the credential reference {reference.reference} and both read "
                f"{reference.identifier}; the package cannot give them different values"
            )
        )
    report.credentials.append(
        CredentialEntry(
            location=location,
            origin=origin,
            reference=reference.reference,
            identifier=reference.identifier,
            worker_receives=reference.identifier in COMPOSE_WORKER_CREDENTIAL_VARIABLES,
            origin_variable=origin_variable,
        )
    )
    report.credentials_block[reference.reference] = {"provider": "env", "identifier": reference.identifier}
    return {"$credential": reference.reference}


def _credential_origin(
    name: str, setting: str, value: object, replaced: frozenset[str] = frozenset()
) -> tuple[CredentialOrigin, str | None]:
    """Return where v2 took a credential from, without keeping the value.

    For a setting in ``replaced``, v2 read the variables that ``<setting>_env_vars`` names
    instead of its default names; the list is named, its entries are not.
    """
    placeholder = _placeholder_name(value)
    if placeholder is not None:
        return "placeholder", placeholder
    if not _is_empty(value):
        return "literal", None
    if setting in replaced:
        return "variable", f"a variable named in {setting}{_ENV_VARS_SUFFIX}"
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
    flag_name: _FlagName
    placeholder: str | None
    visible_variables: tuple[tuple[str, str], ...] = ()


def _resolve(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    location: str,
    row: EnvironmentSetting,
    value: object,
    visible: Mapping[str, str],
    flag: _Flag,
    report: _Report,
) -> object:
    """Return the value v2 used for one setting, recording its origin, or ``None``."""
    candidates = _candidates(location, row, value, visible, flag, report)
    if row.kind == "address":
        holders = _user_information_holders(candidates)
        if holders:
            report.refusals.append(
                RefusalEntry(
                    location,
                    f"the address from {', '.join(holders)} holds a user name or password; "
                    "remove it from the address and pass it as a credential",
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
    flag: _Flag,
    report: _Report,
) -> _Candidates:
    """Collect the file value, the first visible v2 variable and the flag for one setting.

    A placeholder counts as no value in the file. When it names one of the setting's v2
    variables, the variables are still read in v2's order (spec FR-011, FR-029): v2 never
    expanded the placeholder, so the name in it did not change which variable v2 used.
    """
    placeholder = _placeholder_name(value)
    file_value: object = None if _is_empty(value) else value
    if placeholder is not None:
        file_value = None
        if placeholder not in row.variables:
            report.notes.append(
                NoteEntry(
                    f"{location} holds ${{{placeholder}}}; {placeholder} is not a variable v2 read for this "
                    "setting, so it was not read"
                )
            )
    found = tuple((name, visible[name]) for name in row.variables if visible.get(name))
    variable, variable_value = found[0] if found else (None, None)
    return _Candidates(
        file_value=file_value,
        variable=variable,
        variable_value=variable_value,
        flag=flag.value if row.kind == "address" else None,
        flag_name=flag.name,
        placeholder=placeholder,
        visible_variables=found,
    )


def _as_text(value: object) -> str:
    """Return a file value as YAML writes it, so ``false`` in the file equals ``false`` in a variable."""
    return str(value).lower() if isinstance(value, bool) else str(value)


def _choose(location: str, row: EnvironmentSetting, candidates: _Candidates) -> SettingOriginEntry | None:
    """Apply flag, then v2's precedence between the variable and the file."""
    if candidates.flag:
        return SettingOriginEntry(location, candidates.flag, candidates.flag_name)
    file_text = None if candidates.file_value is None else _as_text(candidates.file_value)
    variable_value = candidates.variable_value
    use_variable = variable_value is not None and (row.precedence == "variable" or file_text is None)
    if use_variable:
        other = None if file_text is None or file_text == variable_value else file_text
        return SettingOriginEntry(
            location,
            str(variable_value),
            "variable",
            origin_variable=candidates.variable,
            other_value=other,
        )
    if file_text is None:
        return None
    other = None if variable_value is None or variable_value == file_text else variable_value
    return SettingOriginEntry(
        location,
        file_text,
        "config.yml",
        other_value=other,
    )


def _chosen_value(chosen: SettingOriginEntry, candidates: _Candidates) -> object:
    """Return the value to write: the file's own value keeps its YAML type."""
    return candidates.file_value if chosen.origin == "config.yml" else chosen.value


def _refuse_missing(location: str, row: EnvironmentSetting, candidates: _Candidates, report: _Report) -> None:
    """Refuse a setting that got no value (FR-012, FR-029); an absent optional setting is left out."""
    names = ", ".join(row.variables) or "none"
    if row.kind == "address":
        reason = f"no address in config.yml or a v2 variable ({names}); pass {candidates.flag_name}"
    elif candidates.placeholder is not None:
        reason = f"no value in config.yml or a v2 variable ({names}); set one of these variables"
    else:
        return
    report.refusals.append(RefusalEntry(location, reason))


def _user_information_holders(candidates: _Candidates) -> list[str]:
    """Name the origin of every candidate address that holds a user name or password.

    Every candidate is checked, not only the one v2 used: a value that loses is still printed
    in the report next to the value that wins.
    """
    holders: list[str] = []
    if isinstance(candidates.file_value, str) and _has_user_information(candidates.file_value):
        holders.append("config.yml")
    holders.extend(name for name, value in candidates.visible_variables if _has_user_information(value))
    if candidates.flag is not None and _has_user_information(candidates.flag):
        holders.append(candidates.flag_name)
    return holders


def _has_user_information(address: str) -> bool:
    """Return whether an address holds a user name or password.

    The authority is the text after an optional ``scheme://`` and before the first ``/``, ``?``
    or ``#``; any ``@`` in it marks user information. Read this way, a value without a scheme,
    such as ``user:secret@netbox.example.com``, is caught too: ``urlsplit`` reads ``user`` in
    it as the scheme.
    """
    authority = _SCHEME.sub("", address.strip(), count=1)
    for separator in ("/", "?", "#"):
        authority = authority.split(separator, 1)[0]
    return "@" in authority


def _is_loopback(address: str) -> bool:
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
