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

from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Literal, NamedTuple

from .capabilities import get_adapter_capabilities
from .credentials import ENV_CREDENTIAL_PREFIX

if TYPE_CHECKING:
    from collections.abc import Mapping

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

COMPOSE_WORKER_CREDENTIAL_VARIABLES: tuple[str, ...] = (
    "INFRAHUB_SYNC_CREDENTIAL_INFRAHUB_API_TOKEN",
    "INFRAHUB_SYNC_CREDENTIAL_NETBOX_TOKEN",
    "INFRAHUB_SYNC_CREDENTIAL_NAUTOBOT_TOKEN",
)
"""The credential variables the ``sync-worker`` service of ``docker-compose.yml`` passes through."""

_INFRAHUB_TOKEN_IDENTIFIER = f"{ENV_CREDENTIAL_PREFIX}INFRAHUB_API_TOKEN"


class CredentialReference(NamedTuple):
    """The credential reference name and the V3 environment variable it reads."""

    reference: str
    identifier: str


def credential_reference(adapter: str, setting: str) -> CredentialReference:
    """Return the reference name and identifier for one credential setting (spec FR-006).

    The reference is ``<adapter>-<setting>`` in lower case with ``_`` changed to ``-``; the
    identifier is ``INFRAHUB_SYNC_CREDENTIAL_<ADAPTER>_<SETTING>``. The Infrahub token uses
    ``INFRAHUB_SYNC_CREDENTIAL_INFRAHUB_API_TOKEN``.

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
