"""Check the stored credentials that a package names (spec 027, FR-008, research R7).

A source or destination block names one stored credential with ``credential: <name>``. The
credential must exist and hold the fields that the block's adapter and ``auth_method`` need.
Three places ask, with the same function:

- registration, inside the transaction that stores the version;
- the routes that start a stage of a run, because a credential can be deleted or changed
  after registration;
- validation of a registered version.

This module reads no store and no value. The caller passes ``stored_fields``, which answers the
names of the fields a credential holds, or ``None`` when no credential has the name.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Literal

from .capabilities import BUILTIN_ADAPTER_CAPABILITIES, UnknownCredentialMethodError, credential_field_map
from .conversion_credentials import create_command, ordered_fields
from .credentials import CredentialConfigurationError
from .models import ValidationFinding

if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Iterable

    from .models import ConfigurationPackage

CredentialBlock = Literal["source", "destination"]

CREDENTIAL_NOT_FOUND: Final = "credential-not-found"
CREDENTIAL_FIELD_MISSING: Final = "credential-field-missing"
CREDENTIAL_METHOD_UNKNOWN: Final = "credential-method-unknown"
STORED_CREDENTIAL_CODES: Final = frozenset({CREDENTIAL_NOT_FOUND, CREDENTIAL_FIELD_MISSING, CREDENTIAL_METHOD_UNKNOWN})
"""The codes of the findings of this module. Each one refuses a registration and a run start."""

CREDENTIAL_BLOCKS: Final[tuple[CredentialBlock, ...]] = ("source", "destination")
# The blocks each stage connects to (research R5). An apply, a resume and a verify build the
# destination only. An approval starts no stage; it precedes an apply, so it checks what the
# apply needs.
STAGE_CREDENTIAL_BLOCKS: Final[dict[str, tuple[CredentialBlock, ...]]] = {
    "plan": CREDENTIAL_BLOCKS,
    "sync": CREDENTIAL_BLOCKS,
    "verify": ("destination",),
    "approve": ("destination",),
    "apply": ("destination",),
    "resume": ("destination",),
}


@dataclass(frozen=True, slots=True)
class StoredCredentialFinding:
    """What is wrong with the stored credential that one block names: no value is in it.

    ``finding`` is the validation finding. ``details`` answers the same facts as the details of
    a refusal: the block, the credential name and the missing field names. ``needed_fields``
    are the fields that the block's adapter and ``auth_method`` need; they are empty when the
    method is not known.
    """

    code: str
    block: CredentialBlock
    name: str
    message: str
    missing_fields: tuple[str, ...] = ()
    needed_fields: tuple[str, ...] = ()

    @property
    def finding(self) -> ValidationFinding:
        """Return the finding, located at the ``credential`` key of the block."""
        return ValidationFinding(
            code=self.code,
            severity="error",
            location=f"/configuration/{self.block}/credential",
            message=self.message,
        )

    def details(self) -> dict[str, object]:
        """Return the block, the credential name and the missing field names."""
        details: dict[str, object] = {"block": self.block, "name": self.name}
        if self.missing_fields:
            details["fields"] = list(self.missing_fields)
        return details

    def entry(self) -> dict[str, object]:
        """Return this credential as one entry of the details of a refused registration.

        ``fields`` are the fields to supply, sorted: every field the block needs when the
        credential does not exist, and the fields it does not hold otherwise. No value is in it.
        """
        fields = self.missing_fields if self.code == CREDENTIAL_FIELD_MISSING else self.needed_fields
        return {"block": self.block, "name": self.name, "code": self.code, "fields": sorted(fields)}


def named_credentials(
    package: ConfigurationPackage, blocks: Iterable[CredentialBlock] = CREDENTIAL_BLOCKS
) -> dict[CredentialBlock, str]:
    """Return the stored credential name of each of ``blocks`` that names one."""
    named: dict[CredentialBlock, str] = {}
    for block in blocks:
        name = getattr(package.configuration, block).credential
        if name is not None:
            named[block] = name
    return named


def stored_credential_findings(
    package: ConfigurationPackage,
    stored_fields: Callable[[str], Collection[str] | None],
    *,
    blocks: Iterable[CredentialBlock] = CREDENTIAL_BLOCKS,
) -> tuple[StoredCredentialFinding, ...]:
    """Return one finding for each of ``blocks`` whose named stored credential cannot serve it.

    - ``credential-not-found``: no credential has the name. The message names the fields the
      credential needs and the command that creates it, and says that an administrator runs it.
    - ``credential-field-missing``: the credential does not hold a field that the block's
      adapter and ``auth_method`` need. The message names the fields.
    - ``credential-method-unknown``: the block's ``auth_method`` is not one its adapter
      accepts, so the fields it needs are not known.

    A block that names no credential, a block whose adapter has no capability declaration,
    and a block whose method uses no credential give no finding here: configuration validation
    reports the last two (``missing-adapter``, ``credential-not-used``).

    ``stored_fields`` is called once for each block that needs a credential, in the order of
    ``blocks``.
    """
    findings: list[StoredCredentialFinding] = []
    for block, name in named_credentials(package, blocks).items():
        adapter = getattr(package.configuration, block)
        if adapter.name not in BUILTIN_ADAPTER_CAPABILITIES:
            continue
        try:
            needed = credential_field_map(adapter.name, adapter.settings or {}).fields
        except UnknownCredentialMethodError:
            message = (
                f"{block} names the stored credential {name!r}, and its auth_method is not one that "
                f"adapter {adapter.name!r} accepts"
            )
            findings.append(StoredCredentialFinding(CREDENTIAL_METHOD_UNKNOWN, block, name, message))
            continue
        if not needed:
            continue
        held = stored_fields(name)
        needs = tuple(sorted(needed))
        if held is None:
            listed = ordered_fields(needed)
            message = (
                f"{block} names the stored credential {name!r}, which does not exist; it needs the "
                f"field{'' if len(listed) == 1 else 's'} {', '.join(listed)}; an administrator "
                f"can create it with `{create_command(name, needed)}`"
            )
            findings.append(StoredCredentialFinding(CREDENTIAL_NOT_FOUND, block, name, message, needed_fields=needs))
            continue
        missing = tuple(sorted(set(needed) - set(held)))
        if missing:
            message = (
                f"the stored credential {name!r} that {block} names does not hold the fields "
                f"{', '.join(missing)}, which adapter {adapter.name!r} needs; an administrator can set "
                f"them with `infrahub-sync credentials set {name}`"
            )
            findings.append(
                StoredCredentialFinding(CREDENTIAL_FIELD_MISSING, block, name, message, missing, needed_fields=needs)
            )
    return tuple(findings)


class StoredCredentialCheckError(CredentialConfigurationError):
    """A registration names a stored credential that cannot serve its block.

    ``findings`` holds every block that failed. The message is the message of the first one.
    It is a :class:`CredentialConfigurationError`, so registration refuses it as it refuses
    every other credential defect of a package.
    """

    def __init__(self, findings: tuple[StoredCredentialFinding, ...]) -> None:
        super().__init__(findings[0].message)
        self.findings = findings
