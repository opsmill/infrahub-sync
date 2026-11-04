"""The lineage settings of an Infrahub destination: `source`, `owner` and `protect`.

`parse_infrahub_lineage_settings` turns the destination's settings into typed
`InfrahubLineageSettings`, or into the findings that refuse them. Registration runs it as
the Infrahub adapter's configuration validator, and the adapter parses the same settings
when it is built.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field

from .models import ConfigurationPackage, ValidationFinding

if TYPE_CHECKING:
    from .capabilities import AdapterRole

# The node kinds an Infrahub destination's `source` and `owner` settings may name. They are
# the kinds Infrahub accepts as the source or owner of a value.
LineageNodeKind = Literal["CoreAccount", "CoreAccountGroup", "CoreRepository", "CoreReadOnlyRepository"]
LINEAGE_NODE_KINDS: tuple[str, ...] = get_args(LineageNodeKind)
LineageSettingName = Literal["source", "owner"]
_LINEAGE_NODE_KEYS = frozenset({"kind", "name"})
_LINEAGE_FINDING_CODE = "invalid-lineage-setting"
# Longest configured value a finding message repeats; longer values are not echoed.
_LINEAGE_ECHO_LIMIT = 64


class NamedLineageNode(BaseModel):
    """One Infrahub node named by kind and name in the `source` or `owner` setting."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    kind: LineageNodeKind
    name: str = Field(min_length=1)


class InfrahubLineageSettings(BaseModel):
    """The lineage settings of an Infrahub destination, after parsing.

    `source` absent means the import account is the source; `owner` absent means the import
    sets no owner; `protect` absent means the import does not protect the values it writes.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    source: NamedLineageNode | None = None
    owner: NamedLineageNode | None = None
    protect: bool = False


@dataclass(frozen=True, slots=True)
class LineageSettingsParse:
    """The result of parsing lineage settings: typed settings, or the findings that refuse them.

    Exactly one of the two is meaningful: `settings` is `None` when `findings` is not empty.
    """

    settings: InfrahubLineageSettings | None
    findings: tuple[ValidationFinding, ...]


def _echo(value: object) -> str:
    """Return a configured value for a finding message, or a placeholder when it is not a short string."""
    if isinstance(value, str) and 0 < len(value) <= _LINEAGE_ECHO_LIMIT and value.isprintable():
        return f"`{value}`"
    return "that is given"


def _lineage_finding(setting: str, message: str) -> ValidationFinding:
    return ValidationFinding(
        code=_LINEAGE_FINDING_CODE,
        severity="error",
        location=f"/configuration/destination/settings/{setting}",
        message=message,
    )


def _parse_named_node(setting: LineageSettingName, value: object) -> NamedLineageNode | str:
    """Return the named node `value` gives, or why it is not a `{kind, name}` mapping with an allowed kind."""
    if not isinstance(value, Mapping):
        return f"`{setting}` must be `{{kind, name}}`"
    unknown = sorted(str(key) for key in value if key not in _LINEAGE_NODE_KEYS)
    if unknown:
        return f"`{setting}` has an unknown key {_echo(unknown[0])}"
    return _parse_named_node_fields(setting, value)


def _parse_named_node_fields(setting: LineageSettingName, value: Mapping[Any, object]) -> NamedLineageNode | str:
    """Return the named node a `{kind, name}` mapping gives, or why its `kind` or `name` is refused."""
    if "kind" not in value:
        return f"`{setting}` has no `kind`"
    kind = value["kind"]
    if kind not in LINEAGE_NODE_KINDS:
        allowed = ", ".join(LINEAGE_NODE_KINDS)
        return f"`{setting}` kind {_echo(kind)} is not one of the allowed kinds: {allowed}"
    if "name" not in value:
        return f"`{setting}` has no `name`"
    name = value["name"]
    if not isinstance(name, str) or not name.strip():
        return f"`{setting}` name must be a non-empty string"
    if name != name.strip():
        # Infrahub matches the name exactly, so a padded name would refuse every plan with a
        # node that looks like the one that exists.
        return f"`{setting}` name {_echo(name)} has leading or trailing whitespace"
    return NamedLineageNode.model_validate({"kind": kind, "name": name})


def parse_infrahub_lineage_settings(settings: Mapping[str, object] | None) -> LineageSettingsParse:
    """Parse the `source`, `owner` and `protect` settings of an Infrahub destination.

    Each refused setting gives one finding at `/configuration/destination/settings/<setting>`.
    Other settings in the mapping are ignored. When there is no finding, the result carries
    the typed settings; absent settings, and settings given as null, take their defaults.
    """
    settings = settings or {}
    findings: list[ValidationFinding] = []
    named: dict[str, NamedLineageNode] = {}
    setting: LineageSettingName
    for setting in ("source", "owner"):
        value = settings.get(setting)
        if value is None:
            continue
        parsed = _parse_named_node(setting, value)
        if isinstance(parsed, str):
            findings.append(_lineage_finding(setting, parsed))
        else:
            named[setting] = parsed
    raw_protect = settings.get("protect")
    protect = False
    if isinstance(raw_protect, bool):
        protect = raw_protect
    elif raw_protect is not None:
        findings.append(_lineage_finding("protect", "`protect` must be `true` or `false`"))
    if findings:
        return LineageSettingsParse(settings=None, findings=tuple(findings))
    return LineageSettingsParse(
        settings=InfrahubLineageSettings(source=named.get("source"), owner=named.get("owner"), protect=protect),
        findings=(),
    )


def validate_infrahub_lineage_settings(
    package: ConfigurationPackage,
    role: AdapterRole,
) -> tuple[ValidationFinding, ...]:
    """Refuse the destination's `source`, `owner` and `protect` settings the lineage parser refuses.

    One finding per refused setting, at `/configuration/destination/settings/<setting>`. The
    settings are lineage only when Infrahub is the destination, so the source role gives none.
    """
    if role != "destination":
        return ()
    return parse_infrahub_lineage_settings(package.configuration.destination.settings).findings
