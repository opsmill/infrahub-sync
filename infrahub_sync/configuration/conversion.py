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
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from collections.abc import Mapping

    from .models import ValidationFinding

CredentialOrigin = Literal["literal", "placeholder", "variable", "none"]
"""Where a v2 credential value came from: the file, a ``${NAME}`` placeholder, a variable, or nowhere."""

SettingOrigin = Literal["config.yml", "variable", "--source-url", "--destination-url"]
"""Where the value written for an environment-first setting came from."""


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
