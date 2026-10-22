"""Parsing of an Infrahub destination's `source`, `owner` and `protect` settings."""

from __future__ import annotations

from typing import Any

import pytest

from infrahub_sync.configuration.capabilities import (
    LINEAGE_NODE_KINDS,
    InfrahubLineageSettings,
    NamedLineageNode,
    parse_infrahub_lineage_settings,
)


@pytest.mark.parametrize("setting", ["source", "owner"])
@pytest.mark.parametrize("kind", LINEAGE_NODE_KINDS)
def test_each_allowed_kind_is_accepted(setting: str, kind: str) -> None:
    """A `{kind, name}` with an allowed kind parses to a named node."""
    result = parse_infrahub_lineage_settings({setting: {"kind": kind, "name": "network-team"}})

    assert result.findings == ()
    assert result.settings is not None
    assert getattr(result.settings, setting) == NamedLineageNode(kind=kind, name="network-team")  # ty: ignore[invalid-argument-type]


@pytest.mark.parametrize(
    ("settings", "setting", "message"),
    [
        pytest.param({"owner": "network-team"}, "owner", "`owner` must be `{kind, name}`", id="bare-string"),
        pytest.param(
            {"source": {"kind": "BuiltinTag", "name": "x"}},
            "source",
            "`source` kind `BuiltinTag` is not one of the allowed kinds",
            id="unknown-kind",
        ),
        pytest.param({"owner": {"kind": "CoreAccountGroup"}}, "owner", "`owner` has no `name`", id="no-name"),
        pytest.param(
            {"owner": {"kind": "CoreAccountGroup", "name": ""}},
            "owner",
            "`owner` name must be a non-empty string",
            id="empty-name",
        ),
        pytest.param(
            {"owner": {"kind": "CoreAccountGroup", "name": "   "}},
            "owner",
            "`owner` name must be a non-empty string",
            id="blank-name",
        ),
        pytest.param({"source": {"name": "x"}}, "source", "`source` has no `kind`", id="no-kind"),
        pytest.param(
            {"owner": {"kind": "CoreAccountGroup", "name": "x", "id": "y"}},
            "owner",
            "`owner` has an unknown key `id`",
            id="extra-key",
        ),
        pytest.param({"protect": "yes"}, "protect", "`protect` must be `true` or `false`", id="string-protect"),
        pytest.param({"protect": 1}, "protect", "`protect` must be `true` or `false`", id="integer-protect"),
    ],
)
def test_refused_input_gives_one_finding_at_the_setting(settings: dict[str, Any], setting: str, message: str) -> None:
    """Each refused input gives exactly one error finding at the setting's pointer, and no settings."""
    result = parse_infrahub_lineage_settings(settings)

    assert result.settings is None
    assert len(result.findings) == 1
    finding = result.findings[0]
    assert finding.severity == "error"
    assert finding.location == f"/configuration/destination/settings/{setting}"
    assert finding.message.startswith(message)


def test_each_refused_setting_gives_its_own_finding() -> None:
    """Refusals in several settings are all reported, one finding per setting."""
    result = parse_infrahub_lineage_settings({"source": "a", "owner": "b", "protect": "no"})

    assert result.settings is None
    assert [finding.location for finding in result.findings] == [
        "/configuration/destination/settings/source",
        "/configuration/destination/settings/owner",
        "/configuration/destination/settings/protect",
    ]


def test_a_long_value_is_not_repeated_in_the_finding() -> None:
    """A long configured kind is not copied into the finding message."""
    kind = "X" * 200
    result = parse_infrahub_lineage_settings({"source": {"kind": kind, "name": "x"}})

    assert len(result.findings) == 1
    assert kind not in result.findings[0].message


@pytest.mark.parametrize("settings", [None, {}, {"url": "http://infrahub", "token": "t", "branch": "main"}])
def test_absent_settings_give_no_source_no_owner_and_no_protection(settings: dict[str, Any] | None) -> None:
    """Without lineage settings the source is the import account, there is no owner and `protect` is false."""
    result = parse_infrahub_lineage_settings(settings)

    assert result.findings == ()
    assert result.settings is not None
    assert result.settings == InfrahubLineageSettings()
    assert result.settings.source is None
    assert result.settings.owner is None
    assert result.settings.protect is False


@pytest.mark.parametrize("protect", [True, False])
def test_boolean_protect_is_accepted(*, protect: bool) -> None:
    """`protect` accepts `true` and `false`."""
    result = parse_infrahub_lineage_settings({"protect": protect})

    assert result.settings is not None
    assert result.settings.protect is protect
