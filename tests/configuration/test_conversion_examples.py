"""The v2 ``2.0.2`` examples through ``convert_v2_configuration`` (spec SC-002, research R3).

The 12 examples that read from a bundled adapter and write to Infrahub convert, and each
package parses and has no error finding, so it registers with no edit. ``custom_adapter`` and
``infrahub_to_peering-manager`` are refused with their reasons.

Two inputs need help from the person converting them, as a customer would give it:

- The ACI example holds ``url: ${CISCO_APIC_URL}`` and ``verify: ${CISCO_APIC_VERIFY}``. v2 read
  both variables before the file, so the test sets them in the visible variables (FR-029).
- The Observium and Slurpit examples hold placeholder text instead of an address
  (``url: "OBSERVIUM_URL"`` and ``url: "<URL>"``). The package check refuses that text as
  ``endpoint-not-absolute``, which is correct for that input. The test converts them with
  ``source_url``, as a customer would pass ``--source-url``, and a separate test asserts the
  refusal without it.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
import yaml

from infrahub_sync.configuration.conversion import ConversionResult, convert_v2_configuration
from infrahub_sync.configuration.models import parse_configuration_package
from infrahub_sync.configuration.validation import collect_findings

_EXAMPLES = Path(__file__).resolve().parents[1] / "data" / "v2_examples"

_ACI_VARIABLES = {"CISCO_APIC_URL": "https://apic.example.com", "CISCO_APIC_VERIFY": "true"}

_PLACEHOLDER_ADDRESS = {
    "observium_to_infrahub": "https://observium.example.com",
    "slurpit_to_infrahub": "https://slurpit.example.com",
}

CONVERTED = [
    "aci_to_infrahub",
    "device42_to_infrahub",
    "ipfabric_to_infrahub",
    "librenms_to_infrahub",
    "nautobot-v1_to_infrahub",
    "nautobot-v2_to_infrahub",
    "netbox_to_infrahub",
    "observium_to_infrahub",
    "peering-manager_to_infrahub",
    "peeringdb_to_infrahub",
    "prometheus_to_infrahub (node_exporter)",
    "slurpit_to_infrahub",
]

REFUSED: dict[str, list[tuple[str, str]]] = {
    "custom_adapter": [
        ("source.name", "mockdb is not a bundled adapter; the bundled adapters are "),
        ("source.adapter", "custom adapters are not available in this release (INFP-654, planned for 3.0)"),
    ],
    "infrahub_to_peering-manager": [
        ("destination.name", "V3 does not write to peeringmanager yet; this release writes only to Infrahub"),
    ],
}


def _load(example: str) -> dict[str, Any]:
    return yaml.safe_load((_EXAMPLES / example / "config.yml").read_text(encoding="utf-8"))


def _convert(example: str) -> ConversionResult:
    visible = _ACI_VARIABLES if example == "aci_to_infrahub" else {}
    return convert_v2_configuration(_load(example), visible, source_url=_PLACEHOLDER_ADDRESS.get(example))


def test_the_lists_cover_the_14_v2_examples() -> None:
    examples = {path.name for path in _EXAMPLES.iterdir() if path.is_dir() and not path.name.startswith("v1_")}

    assert len(CONVERTED) == 12
    assert examples == {*CONVERTED, *REFUSED}


@pytest.mark.parametrize("example", CONVERTED)
def test_example_converts_and_its_package_has_no_error_finding(example: str) -> None:
    data = _load(example)

    result = _convert(example)

    assert result.refusals == ()
    assert result.package is not None
    assert result.package["configuration"]["name"] == data["name"]
    assert result.package["configuration"]["destination"]["name"] == "infrahub"
    errors = [
        finding
        for finding in collect_findings(parse_configuration_package(copy.deepcopy(dict(result.package))))
        if finding.severity == "error"
    ]
    assert errors == []


# The stored credential each v2 example's source block names, written out by hand: the fields v2
# ``2.0.2`` read for that example decide the name. ``None`` is a source that used no authentication.
_SOURCE_CREDENTIAL: dict[str, tuple[str, tuple[str, ...]] | None] = {
    "aci_to_infrahub": ("aci-account", ("username", "password")),
    "device42_to_infrahub": ("genericrestapi-account", ("username", "password")),
    "ipfabric_to_infrahub": ("ipfabricsync-auth", ("token",)),
    "librenms_to_infrahub": ("genericrestapi-token", ("token",)),
    "nautobot-v1_to_infrahub": ("nautobot-token", ("token",)),
    "nautobot-v2_to_infrahub": ("nautobot-token", ("token",)),
    "netbox_to_infrahub": ("netbox-token", ("token",)),
    "observium_to_infrahub": ("genericrestapi-account", ("username", "password")),
    "peering-manager_to_infrahub": ("peeringmanager-token", ("token",)),
    "peeringdb_to_infrahub": ("genericrestapi-token", ("token",)),
    "prometheus_to_infrahub (node_exporter)": None,
    "slurpit_to_infrahub": ("slurpitsync-api-key", ("token",)),
}
_CREDENTIAL_SETTINGS = ("token", "username", "password", "api_key", "auth")


def test_the_source_credentials_cover_the_converted_examples() -> None:
    assert set(_SOURCE_CREDENTIAL) == set(CONVERTED)


@pytest.mark.parametrize("example", CONVERTED)
def test_example_package_names_a_stored_credential_in_each_authenticated_block(example: str) -> None:
    """Each block that uses authentication names one stored credential (spec 027 FR-009, SC-005)."""
    result = _convert(example)
    assert result.package is not None
    configuration = result.package["configuration"]
    expected = _SOURCE_CREDENTIAL[example]

    assert configuration["source"].get("credential") == (None if expected is None else expected[0])
    assert configuration["destination"]["credential"] == "infrahub-token"
    listed = {entry.location: (entry.name, entry.fields) for entry in result.credentials}
    assert listed.get("source.credential") == expected
    assert listed["destination.credential"] == ("infrahub-token", ("token",))


@pytest.mark.parametrize("example", CONVERTED)
def test_example_package_has_no_credentials_block_no_env_reference_and_no_value(example: str) -> None:
    """The old format is not produced, and no credential setting stays in a block (SC-005)."""
    result = _convert(example)
    assert result.package is not None
    configuration = result.package["configuration"]
    written = yaml.safe_dump(dict(result.package))

    assert "credentials" not in result.package
    assert "$credential" not in written
    assert "provider: env" not in written
    assert "INFRAHUB_SYNC_CREDENTIAL_" not in written
    for role in ("source", "destination"):
        assert set(_CREDENTIAL_SETTINGS) & set(configuration[role]["settings"]) == set()


@pytest.mark.parametrize(("example", "reasons"), list(REFUSED.items()), ids=list(REFUSED))
def test_example_is_refused_with_its_reasons(example: str, reasons: list[tuple[str, str]]) -> None:
    result = convert_v2_configuration(_load(example), {})

    assert result.package is None
    assert [refusal.location for refusal in result.refusals] == [location for location, _ in reasons]
    for refusal, (_, reason) in zip(result.refusals, reasons, strict=True):
        assert refusal.reason.startswith(reason)


@pytest.mark.parametrize("example", sorted(_PLACEHOLDER_ADDRESS))
def test_placeholder_address_without_the_flag_is_refused_as_not_absolute(example: str) -> None:
    result = convert_v2_configuration(_load(example), {})

    assert result.package is None
    assert [refusal.location for refusal in result.refusals] == ["/configuration/source/settings/url"]
    assert result.refusals[0].reason.startswith("endpoint-not-absolute: ")
