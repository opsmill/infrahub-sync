"""Registration refuses invalid `source`, `owner` and `protect` settings of an Infrahub destination.

Each refused input gives one finding at
`/configuration/destination/settings/<setting>`, and registration raises on it.
"""

from __future__ import annotations

from typing import Any

import pytest

from infrahub_sync.configuration import CredentialConfigurationError, collect_findings, validate_package_credentials
from tests.configuration.validation_packages import package, package_data

LINEAGE_CODE = "invalid-lineage-setting"


def _with_destination_settings(**lineage: Any) -> dict[str, Any]:  # noqa: ANN401 — a setting's value is any declared value
    data = package_data()
    data["configuration"]["destination"]["settings"].update(lineage)
    return data


REFUSED = [
    pytest.param({"owner": "network-team"}, "owner", "`owner` must be `{kind, name}`", id="bare-name"),
    pytest.param(
        {"source": {"kind": "BuiltinTag", "name": "x"}},
        "source",
        "`source` kind `BuiltinTag` is not one of the allowed kinds",
        id="kind-not-allowed",
    ),
    pytest.param({"owner": {"kind": "CoreAccountGroup"}}, "owner", "`owner` has no `name`", id="no-name"),
    pytest.param(
        {"owner": {"kind": "CoreAccountGroup", "name": "x", "id": "y"}},
        "owner",
        "`owner` has an unknown key `id`",
        id="unknown-key",
    ),
    pytest.param({"protect": "yes"}, "protect", "`protect` must be `true` or `false`", id="protect-not-boolean"),
    pytest.param({"protect": "true"}, "protect", "`protect` must be `true` or `false`", id="protect-quoted-true"),
    pytest.param({"protect": 1}, "protect", "`protect` must be `true` or `false`", id="protect-integer-one"),
    pytest.param({"protect": 0}, "protect", "`protect` must be `true` or `false`", id="protect-integer-zero"),
    pytest.param({"protect": [True]}, "protect", "`protect` must be `true` or `false`", id="protect-list"),
]


@pytest.mark.parametrize(("lineage", "setting", "message"), REFUSED)
def test_each_refused_input_gives_one_finding_at_the_settings_pointer(
    lineage: dict[str, Any], setting: str, message: str
) -> None:
    """The finding names the setting and sits at the setting's pointer."""
    findings = collect_findings(package(_with_destination_settings(**lineage)))

    assert [(finding.code, finding.location) for finding in findings] == [
        (LINEAGE_CODE, f"/configuration/destination/settings/{setting}")
    ]
    assert message in findings[0].message


@pytest.mark.parametrize(("lineage", "setting", "message"), REFUSED)
def test_registration_refuses_each_refused_input(lineage: dict[str, Any], setting: str, message: str) -> None:
    """Registering the package raises, naming the setting's pointer and the refusal."""
    with pytest.raises(CredentialConfigurationError) as raised:
        validate_package_credentials(package(_with_destination_settings(**lineage)))

    assert f"/configuration/destination/settings/{setting}" in str(raised.value)
    assert message in str(raised.value)


def test_each_refused_setting_gives_its_own_finding() -> None:
    """Three refused settings in one package give three findings, one per pointer."""
    findings = collect_findings(
        package(_with_destination_settings(source="netbox", owner={"kind": "CoreAccountGroup"}, protect="yes"))
    )

    assert sorted(finding.location for finding in findings if finding.code == LINEAGE_CODE) == [
        "/configuration/destination/settings/owner",
        "/configuration/destination/settings/protect",
        "/configuration/destination/settings/source",
    ]


def test_the_accepted_settings_register() -> None:
    """The accepted example of the contract gives no finding and registers."""
    accepted = package(
        _with_destination_settings(owner={"kind": "CoreAccountGroup", "name": "network-team"}, protect=False)
    )

    assert collect_findings(accepted) == ()
    validate_package_credentials(accepted)


@pytest.mark.parametrize("protect", [pytest.param(True, id="protect-true"), pytest.param(False, id="protect-false")])
def test_protect_registers_as_true_or_false_without_an_owner(*, protect: bool) -> None:
    """`protect` is accepted as `true` or `false`, and needs no owner: Infrahub accepts the flag alone."""
    accepted = package(_with_destination_settings(protect=protect))

    assert collect_findings(accepted) == ()
    validate_package_credentials(accepted)


def test_lineage_settings_on_an_infrahub_source_are_not_judged_as_destination_settings() -> None:
    """The validator judges the destination role only; an Infrahub source gives no lineage finding."""
    data = package_data()
    data["configuration"]["source"] = {
        "name": "infrahub",
        "settings": {"url": "http://localhost:8001", "token": {"$credential": "netbox-token"}, "protect": "yes"},
    }

    assert [finding for finding in collect_findings(package(data)) if finding.code == LINEAGE_CODE] == []
