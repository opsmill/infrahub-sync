"""The stored credentials a converted package names, as ``configs convert`` reports them.

A V3 package names one stored credential in each block that uses authentication
(``credential: <name>``, spec 027 FR-009). ``credential_name`` builds that name, and a
``CredentialEntry`` tells the engineer which stored credential to create, with which fields and
with which command. No entry holds a credential value.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from .capabilities import get_adapter_capabilities

if TYPE_CHECKING:
    from collections.abc import Iterable

    from .capabilities import CredentialField

CredentialOrigin = Literal["literal", "placeholder", "variable", "none"]
"""Where a v2 credential value came from: the file, a ``${NAME}`` placeholder, a variable, or nowhere."""

_ORIGINS_WITH_A_VARIABLE = frozenset({"placeholder", "variable"})

_ACCOUNT_SUFFIX = "account"
"""The name suffix of a stored credential that fills more than one setting: a username and a password."""

FIELD_ORDER: tuple[CredentialField, ...] = ("username", "password", "token")
_CREATE_COMMAND = "infrahub-sync credentials create"


def credential_name(adapter: str, settings: Iterable[str]) -> str:
    """Return the name of the stored credential that fills ``settings`` of one adapter block.

    A block names one stored credential (spec 027 FR-020). When the credential fills one
    setting, the name is ``<adapter>-<setting>`` in lower case with ``_`` changed to ``-``,
    for example ``netbox-token``. When it fills several (a username and a password), no single
    setting names it, and the name is ``<adapter>-account``.

    Raises:
        UnknownAdapterCapabilitiesError: ``adapter`` is not a bundled adapter.
        ValueError: ``settings`` is empty, or one is not a credential setting of ``adapter``.
    """
    filled = tuple(settings)
    declared = get_adapter_capabilities(adapter).credential_setting_paths
    for setting in filled:
        if setting not in declared:
            msg = f"{setting!r} is not a credential setting of adapter {adapter!r}"
            raise ValueError(msg)
    if not filled:
        msg = f"a block of adapter {adapter!r} that fills no credential setting needs no credential"
        raise ValueError(msg)
    suffix = filled[0] if len(filled) == 1 else _ACCOUNT_SUFFIX
    return f"{adapter}-{suffix}".lower().replace("_", "-")


def check_origin_variable(origin: str, origin_variable: str | None) -> None:
    """Reject a ``placeholder`` or ``variable`` origin that does not name its variable."""
    if origin in _ORIGINS_WITH_A_VARIABLE and origin_variable is None:
        msg = f"the {origin} origin needs origin_variable"
        raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class CredentialSettingEntry:
    """One v2 credential setting that the block's stored credential fills.

    ``location`` is the setting path, for example ``source.settings.token``. ``field`` is the
    field of the stored credential that fills it. ``origin`` is where the v2 value came from;
    ``origin_variable`` names the variable for the ``placeholder`` and ``variable`` origins.
    The entry never holds the credential value.
    """

    location: str
    field: CredentialField
    origin: CredentialOrigin
    origin_variable: str | None = None

    def __post_init__(self) -> None:
        """Reject a ``placeholder`` or ``variable`` origin that does not name its variable."""
        check_origin_variable(self.origin, self.origin_variable)


@dataclass(frozen=True, slots=True)
class CredentialEntry:
    """One stored credential that a block of the package names, to create before registration.

    ``location`` is the block's key, for example ``source.credential``. ``name`` is the name
    written to the package. ``fields`` are the fields the stored credential must hold, in the
    order username, password, token. ``settings`` are the v2 settings it fills, with the origin
    of each v2 value. The entry never holds a credential value.
    """

    location: str
    name: str
    fields: tuple[CredentialField, ...]
    settings: tuple[CredentialSettingEntry, ...] = ()

    @property
    def command(self) -> str:
        """The command that creates the stored credential; it asks for the secret value without echo."""
        username = " --username <username>" if "username" in self.fields else ""
        return f"{_CREATE_COMMAND} {self.name}{username}"

    @property
    def warnings(self) -> tuple[str, ...]:
        """The report warnings for this credential (spec FR-008), never a value."""
        return tuple(
            f"config.yml still holds a literal value for {setting.location}; remove it once V3 works"
            for setting in self.settings
            if setting.origin == "literal"
        )
