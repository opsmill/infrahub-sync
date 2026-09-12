"""The accumulating validation core: one declared defect, one finding.

``validate_package_credentials`` reports the first defect and stops. The core behind it keeps
going, so a package carrying N independent defects yields N findings. These tests fix the two
orderings that separates — execution order, which the wrapper's message comes from, and the
stable cross-interface order ``collect_findings`` returns.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

import infrahub_sync
from infrahub_sync.configuration import (
    BUILTIN_ADAPTER_CAPABILITIES,
    AdapterConfigurationCapabilities,
    AdapterRole,
    ConfigurationPackage,
    CredentialConfigurationError,
    ValidationFinding,
    sort_findings,
    validate_package_credentials,
    validation,
)
from infrahub_sync.configuration.validation import collect_findings
from tests.configuration.test_validation_compatibility import SHIPPED_REFUSALS
from tests.configuration.validation_packages import package, package_data

_NARROW_TYPE = "UnknownAdapterCapabilitiesError"
_FINDING_TEXT_BOUND = 256


def _triples(package_content: dict[str, Any]) -> list[tuple[str, str]]:
    return [(finding.code, finding.location) for finding in collect_findings(package(package_content))]


def test_a_valid_package_produces_no_findings() -> None:
    assert collect_findings(package()) == ()


def test_independent_defects_each_become_one_finding() -> None:
    data = package_data()
    data["credentials"]["netbox-token"]["provider"] = "vault"
    data["configuration"]["source"]["settings"]["url"] = "demo.netbox.dev"
    data["configuration"]["destination"]["settings"]["bogus_dest"] = 1
    data["configuration"]["store"] = {"type": "redis", "settings": {"password": {"$credential": "nope"}}}

    assert _triples(data) == [
        ("undeclared-setting", "/configuration/destination/settings/bogus_dest"),
        ("endpoint-not-absolute", "/configuration/source/settings/url"),
        ("unknown-credential-reference", "/configuration/store/settings/password"),
        ("unknown-credential-provider", "/credentials/netbox-token"),
    ]


def test_findings_are_returned_in_the_stable_cross_interface_order() -> None:
    data = package_data()
    data["credentials"]["netbox-token"]["provider"] = "vault"
    data["configuration"]["destination"]["settings"]["bogus_dest"] = 1
    findings = collect_findings(package(data))

    assert findings == sort_findings(findings)


def test_execution_first_error_and_sort_first_finding_are_different_findings() -> None:
    # OES-16. The pinned pair: the credential declaration executes first and sorts last, while
    # the destination setting executes fourth and sorts first. A fixture whose two orders agree
    # would prove nothing.
    data = package_data()
    data["credentials"]["netbox-token"]["provider"] = "vault"
    data["configuration"]["destination"]["settings"]["bogus_dest"] = 1

    with pytest.raises(CredentialConfigurationError) as caught:
        validate_package_credentials(package(data))

    findings = collect_findings(package(data))
    assert str(caught.value) == "credential reference 'netbox-token' uses provider 'vault', which is not installed"
    assert findings[0].location == "/configuration/destination/settings/bogus_dest"
    assert findings[0].code == "undeclared-setting"
    assert findings[-1].location == "/credentials/netbox-token"


@pytest.mark.parametrize(
    ("node", "expected_code"),
    [
        pytest.param({"$credential": "nope"}, "unknown-credential-reference", id="unknown-reference"),
        pytest.param(
            {"$credential": "netbox-token", "fallback": "declared-in-line"},
            "malformed-credential-reference",
            id="malformed-reference",
        ),
    ],
)
def test_the_owning_check_is_authoritative_at_a_declared_credential_path(
    node: dict[str, Any],
    expected_code: str,
) -> None:
    # The whole-package walk revisits the same node the adapter check already judged. Reporting
    # both would give one defect two byte-identical findings.
    data = package_data()
    data["configuration"]["source"]["settings"]["token"] = node

    assert _triples(data) == [(expected_code, "/configuration/source/settings/token")]


def test_a_reference_at_an_undeclared_setting_name_yields_one_finding() -> None:
    # One typo. The surface check owns the location and gives the precise reason; the walk
    # would add "not a credential-bearing setting" at the same pointer, which is the vaguer
    # reason the check order already exists to make unreachable.
    data = package_data()
    data["configuration"]["source"]["settings"]["api_key"] = {"$credential": "netbox-token"}

    assert _triples(data) == [("undeclared-setting", "/configuration/source/settings/api_key")]


def _nested_under_an_undeclared_setting(data: dict[str, Any]) -> None:
    data["configuration"]["source"]["settings"]["api_key"] = {"deep": {"$credential": "netbox-token"}}


def _nested_under_a_refused_url(data: dict[str, Any]) -> None:
    data["configuration"]["source"]["settings"]["url"] = {"deep": {"$credential": "netbox-token"}}


def _nested_under_a_refused_store_setting(data: dict[str, Any]) -> None:
    node = {"deep": {"$credential": "netbox-token"}}
    data["configuration"]["store"] = {"type": "redis", "settings": {"password": node}}


@pytest.mark.parametrize(
    ("mutate", "expected"),
    [
        pytest.param(
            _nested_under_an_undeclared_setting,
            ("undeclared-setting", "/configuration/source/settings/api_key"),
            id="undeclared-setting",
        ),
        pytest.param(
            _nested_under_a_refused_url,
            ("setting-not-a-string", "/configuration/source/settings/url"),
            id="setting-not-a-string",
        ),
        pytest.param(
            _nested_under_a_refused_store_setting,
            ("inline-credential-value", "/configuration/store/settings/password"),
            id="inline-credential-value",
        ),
    ],
)
def test_a_surface_check_owns_the_setting_it_judged_and_everything_beneath_it(
    mutate: Callable[[dict[str, Any]], None],
    expected: tuple[str, str],
) -> None:
    # The flat form of each of these is one finding. Nesting the reference one level deeper
    # must not turn one defect into two, and the second would be the vaguer reason at the
    # deeper pointer — exactly what the surface checks run first to make unreachable.
    data = package_data()
    mutate(data)

    assert _triples(data) == [expected]


def test_a_role_mismatch_and_a_misplaced_reference_stay_two_findings() -> None:
    # Not the same defect twice. netbox declares verify_ssl under a role-independent settings
    # surface, so the reference is misplaced on its own merits whether or not the adapter can
    # serve the destination role. Suppressing either would under-report.
    data = package_data()
    data["configuration"]["destination"] = {
        "name": "netbox",
        "settings": {"verify_ssl": {"$credential": "netbox-token"}},
    }

    assert _triples(data) == [
        ("adapter-role-mismatch", "/configuration/destination"),
        ("credential-path-not-declared", "/configuration/destination/settings/verify_ssl"),
    ]


def test_the_walk_still_reports_where_no_surface_check_has_authority() -> None:
    # The counter-test to the ownership rule: suppressing the walk wholesale would pass every
    # test above and silence its real job. "order" is declared as strings and cannot carry a
    # node, so the reachable uncovered surfaces are a declared setting that is not
    # credential-bearing, the package-level schema mapping, and structures nested inside it.
    data = package_data()
    data["configuration"]["source"]["settings"]["verify_ssl"] = {"$credential": "netbox-token"}
    data["configuration"]["schema_mapping"] = [
        {
            "name": "Device",
            "fields": [
                {"name": "token", "static": {"$credential": "netbox-token"}},
                {"name": "nested", "static": {"outer": [{"inner": {"$credential": "netbox-token"}}]}},
            ],
        }
    ]

    assert _triples(data) == [
        ("credential-path-not-declared", "/configuration/schema_mapping/0/fields/0/static"),
        ("credential-path-not-declared", "/configuration/schema_mapping/0/fields/1/static/outer/0/inner"),
        ("credential-path-not-declared", "/configuration/source/settings/verify_ssl"),
    ]


def _shared_validator_package_data() -> dict[str, Any]:
    """One package-level mapping defect judged by two adapters sharing one validator."""
    data = package_data()
    data["configuration"]["source"] = {"name": "genericrestapi", "settings": {"url": "https://api.example"}}
    data["configuration"]["destination"] = {"name": "peeringmanager", "settings": {"url": "https://pm.example"}}
    data["configuration"]["schema_mapping"] = [{"name": "Device", "mapping": "https://evil.example/devices"}]
    return data


def test_one_mapping_defect_judged_by_two_adapters_yields_one_finding() -> None:
    # _validate_relative_rest_mapping_endpoints is one function object serving both adapters and
    # it interpolates the adapter name, so the two findings differ only in message. The defect is
    # a package-level schema_mapping entry and is role-independent; first in execution order wins.
    findings = collect_findings(package(_shared_validator_package_data()))

    assert [(finding.code, finding.location) for finding in findings] == [
        ("unsafe-rest-request-endpoint", "/configuration/schema_mapping/0/mapping"),
    ]
    assert findings[0].message.startswith("genericrestapi ")


def test_two_defects_in_one_declaration_still_yield_two_findings() -> None:
    # The precedence rule is per check, not per finding: one check reporting two different
    # problems at one pointer is two defects, and collapsing them would under-report.
    data = package_data()
    data["credentials"]["both-bad"] = {"provider": "vault", "identifier": "NOT-VALID"}

    assert _triples(data) == [
        ("malformed-credential-reference", "/credentials/both-bad"),
        ("unknown-credential-provider", "/credentials/both-bad"),
    ]


def test_a_reference_outside_the_declared_paths_is_still_reported_by_the_walk() -> None:
    data = package_data()
    data["configuration"]["schema_mapping"] = [
        {"name": "Device", "fields": [{"name": "token", "static": {"$credential": "netbox-token"}}]}
    ]

    assert _triples(data) == [
        ("credential-path-not-declared", "/configuration/schema_mapping/0/fields/0/static"),
    ]


def test_a_missing_adapter_is_expressible_as_a_finding() -> None:
    data = package_data()
    data["configuration"]["source"]["name"] = "NetBox"

    assert _triples(data) == [("missing-adapter", "/configuration/source")]


def test_a_missing_adapter_suppresses_only_its_own_role() -> None:
    # Its settings cannot be judged against a surface that does not exist, so claiming a finding
    # about them would be inventing one. Everything else still runs.
    data = package_data()
    data["configuration"]["source"]["name"] = "NetBox"
    data["configuration"]["source"]["settings"]["bogus_source"] = 1
    data["configuration"]["destination"]["settings"]["bogus_dest"] = 1
    data["configuration"]["store"] = {"type": "redis", "settings": {"password": "declared-in-line"}}

    assert _triples(data) == [
        ("undeclared-setting", "/configuration/destination/settings/bogus_dest"),
        ("missing-adapter", "/configuration/source"),
        ("inline-credential-value", "/configuration/store/settings/password"),
    ]


def test_an_undeclared_store_type_suppresses_only_its_own_settings() -> None:
    # The same rule the missing adapter above obeys, at the other unevaluable surface. Whether
    # a store setting is credential-bearing is exactly what an undeclared store type makes
    # unknowable, so the walk has no surface to judge "url" against and must not claim one.
    data = package_data()
    data["configuration"]["store"] = {"type": "mystery", "settings": {"url": {"$credential": "netbox-token"}}}
    data["configuration"]["destination"]["settings"]["bogus_dest"] = 1
    data["configuration"]["source"]["settings"]["verify_ssl"] = {"$credential": "netbox-token"}

    assert _triples(data) == [
        ("undeclared-setting", "/configuration/destination/settings/bogus_dest"),
        ("credential-path-not-declared", "/configuration/source/settings/verify_ssl"),
        ("missing-store-capabilities", "/configuration/store"),
    ]


def test_an_undeclared_store_type_carrying_nothing_is_still_silent() -> None:
    # Measured shipped behaviour: an undeclared store type declaring no settings declares
    # nothing unsafe, so it is not a defect and suppresses nothing.
    data = package_data()
    data["configuration"]["store"] = {"type": "mystery", "settings": {}}

    assert _triples(data) == []


def _narrow_type_uses(tree: ast.AST) -> list[str]:
    uses: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Raise):
            uses.extend("raise" for name in ast.walk(node) if isinstance(name, ast.Name) and name.id == _NARROW_TYPE)
        if isinstance(node, ast.ExceptHandler) and node.type is not None:
            uses.extend(
                "except" for name in ast.walk(node.type) if isinstance(name, ast.Name) and name.id == _NARROW_TYPE
            )
    return uses


def test_the_narrow_unknown_adapter_type_is_raised_and_caught_in_one_module_only() -> None:
    # OES-15 narrowed the blast radius of the type; this keeps that a fact rather than a claim.
    root = Path(infrahub_sync.__file__).parent
    owner = root / "configuration" / "capabilities.py"
    offenders = {
        str(path.relative_to(root)): _narrow_type_uses(ast.parse(path.read_text(encoding="utf-8")))
        for path in sorted(root.rglob("*.py"))
        if path != owner
    }

    assert _narrow_type_uses(ast.parse(owner.read_text(encoding="utf-8"))) == ["raise"]
    assert {path: uses for path, uses in offenders.items() if uses} == {}


def _capabilities_with(
    validator: Callable[..., object],
    adapter_name: str = "peeringmanager",
) -> dict[str, AdapterConfigurationCapabilities]:
    declared = dict(BUILTIN_ADAPTER_CAPABILITIES)
    declared[adapter_name] = replace(declared[adapter_name], validator=validator)
    return declared


def _both_roles_package_data(mapping: str = "https://evil.example/devices") -> dict[str, Any]:
    data = package_data()
    settings = {"url": "https://pm.example", "token": {"$credential": "netbox-token"}}
    data["configuration"]["source"] = {"name": "peeringmanager", "settings": settings}
    data["configuration"]["destination"] = {"name": "peeringmanager", "settings": dict(settings)}
    data["configuration"]["schema_mapping"] = [{"name": "Device", "mapping": mapping}]
    return data


def test_one_adapter_serving_both_roles_reports_its_validator_finding_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # OES-17. The same validator serves both roles and cannot tell them apart here, so it
    # returns the same finding twice; two identical findings would break the sort's totality.
    roles: list[str] = []

    def _validator(package_argument: ConfigurationPackage, role: AdapterRole) -> tuple[ValidationFinding, ...]:
        del package_argument
        roles.append(role)
        return (
            ValidationFinding(
                code="unsafe-rest-request-endpoint",
                severity="error",
                location="/configuration/schema_mapping/0/mapping",
                message="peeringmanager schema mapping endpoints must be a relative request path",
            ),
        )

    monkeypatch.setattr(validation, "BUILTIN_ADAPTER_CAPABILITIES", _capabilities_with(_validator))
    findings = collect_findings(package(_both_roles_package_data()))

    assert roles == ["source", "destination"]
    assert [(finding.code, finding.location) for finding in findings] == [
        ("unsafe-rest-request-endpoint", "/configuration/schema_mapping/0/mapping"),
    ]


def test_an_adapter_validator_keeps_its_own_code_and_location() -> None:
    data = package_data()
    data["configuration"]["source"] = {"name": "genericrestapi", "settings": {"url": "https://api.example"}}
    data["configuration"]["schema_mapping"] = [{"name": "Device", "mapping": "https://evil.example/devices"}]

    assert _triples(data) == [("unsafe-rest-request-endpoint", "/configuration/schema_mapping/0/mapping")]


def _validator_reporting_at(location: str) -> Callable[..., tuple[ValidationFinding, ...]]:
    """Return a validator that reports one finding at `location`, whatever role it is given."""

    def _validator(package_argument: ConfigurationPackage, role: AdapterRole) -> tuple[ValidationFinding, ...]:
        del package_argument, role
        return (ValidationFinding(code="adapter-note", severity="error", location=location, message="Zq7"),)

    return _validator


def test_a_source_validator_cannot_silence_a_finding_in_the_destination_subtree(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Location precedence is the acceptance rule and stays intact; what an adapter may not do
    # is place a finding where it has no standing. A source validator reporting at a
    # destination pointer used to win precedence there and delete the core's credential-safety
    # finding for a real inline value, silently.
    monkeypatch.setattr(
        validation,
        "BUILTIN_ADAPTER_CAPABILITIES",
        _capabilities_with(_validator_reporting_at("/configuration/destination/settings/token"), "netbox"),
    )
    data = package_data()
    inline_value = "declared-in-line"
    data["configuration"]["destination"]["settings"]["token"] = inline_value

    findings = collect_findings(package(data))

    assert [(finding.code, finding.location) for finding in findings] == [
        ("inline-credential-value", "/configuration/destination/settings/token"),
        ("adapter-validator-finding", "/configuration/source"),
    ]
    assert all("Zq7" not in finding.message for finding in findings)


@pytest.mark.parametrize(
    "location",
    [
        pytest.param("/configuration/destination/settings/token", id="the-other-role"),
        pytest.param("/configuration/destination", id="the-other-role-root"),
        pytest.param("/configuration/store/settings/password", id="the-store"),
        pytest.param("/credentials/netbox-token", id="the-credential-declarations"),
        pytest.param("", id="the-whole-package"),
        pytest.param("/configuration/schema_mapping_other", id="a-prefix-lookalike"),
    ],
)
def test_a_finding_outside_the_validator_role_subtree_is_contained(
    monkeypatch: pytest.MonkeyPatch,
    location: str,
) -> None:
    monkeypatch.setattr(
        validation,
        "BUILTIN_ADAPTER_CAPABILITIES",
        _capabilities_with(_validator_reporting_at(location), "netbox"),
    )

    assert _triples(package_data()) == [("adapter-validator-finding", "/configuration/source")]


@pytest.mark.parametrize(
    "location",
    [
        pytest.param("/configuration/source", id="its-own-role-root"),
        pytest.param("/configuration/source/settings/url", id="its-own-role-subtree"),
        pytest.param("/configuration/schema_mapping", id="package-level-mapping-root"),
        pytest.param("/configuration/schema_mapping/0/mapping", id="package-level-mapping-entry"),
    ],
)
def test_a_finding_inside_the_validator_role_subtree_is_kept(
    monkeypatch: pytest.MonkeyPatch,
    location: str,
) -> None:
    # The permitted set is the validator's own role subtree plus the role-independent package
    # content the shipped genericrestapi validator legitimately reports at.
    monkeypatch.setattr(
        validation,
        "BUILTIN_ADAPTER_CAPABILITIES",
        _capabilities_with(_validator_reporting_at(location), "netbox"),
    )

    assert _triples(package_data()) == [("adapter-note", location)]


def test_an_adapter_validator_cannot_mint_a_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    # The emission rule is closed: only the two contract section 4 families in the warnings
    # module report warnings. A warning is advice the wrapper never raises on, so a validator
    # allowed to return one could write a defect into the non-blocking channel. It is contained
    # to the existing contained-validator-failure error, exactly like any other unusable result.
    def _validator(package_argument: ConfigurationPackage, role: AdapterRole) -> tuple[ValidationFinding, ...]:
        del package_argument, role
        return (
            ValidationFinding(
                code="adapter-note",
                severity="warning",
                location="/configuration/source",
                message="Zq7",
            ),
        )

    monkeypatch.setattr(validation, "BUILTIN_ADAPTER_CAPABILITIES", _capabilities_with(_validator, "netbox"))

    findings = collect_findings(package(package_data()))

    assert [(finding.code, finding.severity, finding.location) for finding in findings] == [
        ("adapter-validator-finding", "error", "/configuration/source"),
    ]
    assert all("Zq7" not in finding.message for finding in findings)


_ROLE_INDEPENDENT_POINTER = "/configuration/schema_mapping/0/mapping"


def _validator_reporting(code: str) -> Callable[..., tuple[ValidationFinding, ...]]:
    """Return a validator reporting one distinctly coded finding at the shared mapping pointer."""

    def _validator(package_argument: ConfigurationPackage, role: AdapterRole) -> tuple[ValidationFinding, ...]:
        del package_argument, role
        return (
            ValidationFinding(
                code=code,
                severity="error",
                location=_ROLE_INDEPENDENT_POINTER,
                message=f"{code} reports here",
            ),
        )

    return _validator


def test_the_first_check_to_report_at_a_pointer_silences_a_different_check_there(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The precedence rule has two halves and only one of them is reachable with the shipped
    # adapters. The (code, location) half drops a repeat of the same code; this half drops a
    # *different* check's finding at a pointer some earlier check already owns, and it needs
    # two adapters that both have standing at one pointer. A schema mapping is declared once
    # for the package, so each role's own validator may report at it, and the two roles are
    # two different checks. Source runs first, so the destination adapter's distinct finding
    # is dropped - not deduplicated, dropped, and nothing tells the operator it existed.
    declared = dict(BUILTIN_ADAPTER_CAPABILITIES)
    declared["netbox"] = replace(declared["netbox"], validator=_validator_reporting("source-note"))
    declared["infrahub"] = replace(declared["infrahub"], validator=_validator_reporting("destination-note"))
    monkeypatch.setattr(validation, "BUILTIN_ADAPTER_CAPABILITIES", declared)
    data = package_data()
    data["configuration"]["schema_mapping"] = [{"name": "Device", "mapping": "/devices"}]

    assert _triples(data) == [("source-note", _ROLE_INDEPENDENT_POINTER)]


def _raising_validator(package_argument: ConfigurationPackage, role: AdapterRole) -> tuple[ValidationFinding, ...]:
    del package_argument, role
    msg = "Zq7"
    raise RuntimeError(msg)


def _string_returning_validator(package_argument: ConfigurationPackage, role: AdapterRole) -> str:
    del package_argument, role
    return "Zq7"


def _non_finding_returning_validator(
    package_argument: ConfigurationPackage,
    role: AdapterRole,
) -> list[dict[str, str]]:
    del package_argument, role
    return [{"code": "Zq7", "severity": "error", "location": "/a", "message": "Zq7"}]


@pytest.mark.parametrize(
    "validator",
    [
        pytest.param(_raising_validator, id="raises"),
        pytest.param(_string_returning_validator, id="returns-a-string"),
        pytest.param(_non_finding_returning_validator, id="returns-a-non-finding"),
    ],
)
def test_a_failing_adapter_validator_is_contained_as_one_finding(
    monkeypatch: pytest.MonkeyPatch,
    validator: Callable[..., object],
) -> None:
    # A str is a Sequence, so "returned a sequence" is not the test; "returned findings" is.
    monkeypatch.setattr(validation, "BUILTIN_ADAPTER_CAPABILITIES", _capabilities_with(validator))
    data = _both_roles_package_data(mapping="/devices")

    with pytest.raises(CredentialConfigurationError) as caught:
        validate_package_credentials(package(data))

    assert _triples(data) == [
        ("adapter-validator-finding", "/configuration/destination"),
        ("adapter-validator-finding", "/configuration/source"),
    ]
    # The contained failure is reported, not re-raised, and it carries none of its own text.
    assert caught.value.__context__ is None
    assert "Zq7" not in str(caught.value)


def _constructed_finding_validator(fields: dict[str, Any]) -> Callable[..., list[ValidationFinding]]:
    """Return a validator handing back a finding built with ``model_construct``.

    ``model_construct`` runs no field validator, so an adapter can return an object that
    passes ``isinstance(item, ValidationFinding)`` while carrying fields the contract refuses.
    """

    def _validator(package_argument: ConfigurationPackage, role: AdapterRole) -> list[ValidationFinding]:
        del package_argument, role
        return [ValidationFinding.model_construct(**fields)]

    return _validator


@pytest.mark.parametrize(
    "fields",
    [
        pytest.param(
            {"code": "x", "severity": "warning", "location": "/a", "message": "Zq7"},
            id="undeclared-severity",
        ),
        pytest.param(
            {"code": "x", "severity": "error", "location": 7, "message": "Zq7"},
            id="location-not-a-string",
        ),
        pytest.param(
            {"code": "x", "severity": "error", "location": "/a", "message": "Zq7" + "m" * 5000},
            id="over-long-message",
        ),
        pytest.param(
            {"code": "x", "severity": "error", "location": "/Zq7\nb\x00", "message": "m"},
            id="unprintable-location",
        ),
    ],
)
@pytest.mark.filterwarnings("ignore:Pydantic serializer warnings:UserWarning")
def test_a_returned_finding_the_contract_refuses_is_contained(
    monkeypatch: pytest.MonkeyPatch,
    fields: dict[str, Any],
) -> None:
    # Sorting the returned findings and joining their text sat outside the contained block, so
    # the first two of these escaped collect_findings as a KeyError and a TypeError. The last two
    # reached the public tuple unbounded and unescaped, because an adapter finding was used as
    # given rather than put through the bounds the core applies to its own.
    monkeypatch.setattr(
        validation,
        "BUILTIN_ADAPTER_CAPABILITIES",
        _capabilities_with(_constructed_finding_validator(fields)),
    )

    findings = collect_findings(package(_both_roles_package_data(mapping="/devices")))

    assert [(finding.code, finding.location) for finding in findings] == [
        ("adapter-validator-finding", "/configuration/destination"),
        ("adapter-validator-finding", "/configuration/source"),
    ]
    # Nothing the validator supplied is carried, and what is published is displayable and bounded.
    assert all("Zq7" not in finding.location + finding.message for finding in findings)
    assert all(character.isprintable() for finding in findings for character in finding.location + finding.message)
    assert all(len(finding.message) <= _FINDING_TEXT_BOUND for finding in findings)


@pytest.mark.parametrize(
    "fields",
    [
        pytest.param(
            {
                "code": "adapter-note",
                "severity": "error",
                "location": "/configuration/schema_mapping",
                "message": "Zq7" + "m" * 5000,
            },
            id="over-long-message",
        ),
        pytest.param(
            {
                "code": "adapter-note",
                "severity": "error",
                "location": "/configuration/schema_mapping",
                "message": "Zq7\x07",
            },
            id="unprintable-message",
        ),
    ],
)
def test_a_returned_finding_the_contract_refuses_is_contained_inside_the_role_subtree(
    monkeypatch: pytest.MonkeyPatch,
    fields: dict[str, Any],
) -> None:
    # The confinement rule refuses a finding placed outside the validator's own subtree before
    # anything is read off it, so a fixture reporting out of subtree proves confinement and
    # says nothing about revalidation. These two report where the validator does have standing,
    # which is the only place the revalidation and printability guards decide anything: without
    # them the adapter's own text is published, bounded to 256 characters or carrying a control
    # character an operator's terminal interprets.
    monkeypatch.setattr(
        validation,
        "BUILTIN_ADAPTER_CAPABILITIES",
        _capabilities_with(_constructed_finding_validator(fields), "netbox"),
    )

    findings = collect_findings(package(package_data()))

    assert [(finding.code, finding.location) for finding in findings] == [
        ("adapter-validator-finding", "/configuration/source"),
    ]
    assert all("Zq7" not in finding.location + finding.message for finding in findings)
    assert all(character.isprintable() for finding in findings for character in finding.location + finding.message)


def _pointer_cut_on_an_escape() -> dict[str, Any]:
    """A declared package whose pointer cut lands on the "~" of a "~1" escape.

    The 256-character cut is taken over the escaped pointer, so it can separate a "~" from the
    "0" or "1" that completes it, and the finding grammar refuses a lone "~". The offsets are
    chosen so the cut falls exactly there: the escape comes from a "/" in a declared key, and
    the key below it pushes the pointer past the bound without moving the escape.
    """
    data = package_data()
    node: Any = {"$credential": "netbox-token"}
    node = {"z" * 20: node}
    node = {("x" * 16) + "/y": node}
    for _ in range(3):
        node = {"n" * 60: node}
    data["configuration"]["source"]["settings"]["verify_ssl"] = node
    return data


def test_a_pointer_cut_on_an_escape_still_yields_a_well_formed_finding() -> None:
    # Without the trailing-"~" drop this raises pydantic.ValidationError out of collect_findings,
    # uncontained: a declared package makes the validator itself crash rather than report.
    findings = collect_findings(package(_pointer_cut_on_an_escape()))

    assert [finding.code for finding in findings] == ["credential-path-not-declared"]
    assert not findings[0].location.rstrip("~").endswith("~")
    assert _TRUNCATED_POINTER.search(findings[0].location) is not None
    assert len(findings[0].location) <= _FINDING_TEXT_BOUND


_OVER_CAP_DEFECTS = 300
_FINDING_CAP = 256
# The empty pointer, which every real pointer sorts after because they all begin with "/".
_LIMIT_POINTER = ""


def _over_cap_package_data(*, reversed_declaration: bool) -> dict[str, Any]:
    # Credential declarations are walked in insertion order, so reversing them reverses the
    # execution order too. Undeclared settings would not work here: that check already emits
    # sorted, so a truncation taken in execution order would survive the reordering unnoticed.
    data = package_data()
    names = [f"ref{index:03}" for index in range(_OVER_CAP_DEFECTS)]
    if reversed_declaration:
        names.reverse()
    for name in names:
        data["credentials"][name] = {"provider": "vault", "identifier": "DECLARED_TOKEN"}
    return data


def test_the_finding_count_is_bounded_and_the_bound_reports_itself_first() -> None:
    findings = collect_findings(package(_over_cap_package_data(reversed_declaration=False)))

    assert len(findings) == _FINDING_CAP + 1
    assert findings[0].code == "finding-limit-reached"
    assert findings[0].location == _LIMIT_POINTER
    assert {finding.code for finding in findings[1:]} == {"unknown-credential-provider"}
    assert findings[1].location == "/credentials/ref000"


def test_a_reordered_equivalent_package_yields_an_identical_finding_set() -> None:
    # Truncating in execution order would make the surviving 256 depend on the order the
    # defects were declared in, which is why the cut happens after the sort and not before it.
    declared = package(_over_cap_package_data(reversed_declaration=False))
    reordered = package(_over_cap_package_data(reversed_declaration=True))

    assert collect_findings(declared) == collect_findings(reordered)


@pytest.mark.parametrize("declaration", ["declared", "reordered"])
def test_no_two_findings_share_a_location_severity_and_code_over_the_cap(declaration: str) -> None:
    data = _over_cap_package_data(reversed_declaration=declaration == "reordered")
    findings = collect_findings(package(data))

    keys = [(finding.location, finding.severity, finding.code) for finding in findings]
    assert len(set(keys)) == len(keys)


def _many_omissions_package_data(*, contradiction_at: int | None = None) -> dict[str, Any]:
    data = package_data()
    if contradiction_at is not None:
        data["configuration"]["schema_mapping"] = [{"name": f"Kind{contradiction_at:03}", "mapping": "x"}]
    data["omissions"] = [{"kind": f"Kind{index:03}"} for index in range(_FINDING_CAP + 1)]
    return data


def test_a_warnings_only_suppression_yields_a_warning_sentinel_and_no_refusal() -> None:
    # The sentinel's severity is the maximum severity among the *suppressed* findings.
    # 257 warnings: the one cut is a warning, so the report stays error-free and the
    # package still validates with no wrapper raise.
    data = _many_omissions_package_data()

    findings = collect_findings(package(data))

    assert len(findings) == _FINDING_CAP + 1
    assert findings[0].code == "finding-limit-reached"
    assert findings[0].location == _LIMIT_POINTER
    assert {finding.severity for finding in findings} == {"warning"}
    validate_package_credentials(package(data))


def test_one_suppressed_error_yields_an_error_sentinel() -> None:
    # The contradiction error's pointer "/omissions/99" sorts lexicographically last among
    # the 257 omission pointers, so warnings fill the cap and the one suppressed finding
    # is the error: the sentinel says error even though every presented real finding is a
    # warning — an error-free presentation would misreport a package that cannot register.
    data = _many_omissions_package_data(contradiction_at=99)

    findings = collect_findings(package(data))

    assert len(findings) == _FINDING_CAP + 1
    assert findings[0].code == "finding-limit-reached"
    assert findings[0].severity == "error"
    assert {finding.severity for finding in findings[1:]} == {"warning"}


# Frozen by CF-004a's envelope. Two contract families are deliberately absent — destination
# schema mismatch and unsupported destination write behaviour — so neither can be claimed
# vacuously by a set comparison that only grows.
FROZEN_CODES = frozenset(
    {
        "adapter-role-mismatch",
        "adapter-validator-finding",
        "credential-path-not-declared",
        "endpoint-not-absolute",
        "endpoint-not-relative",
        "finding-limit-reached",
        "inline-credential-value",
        "malformed-credential-reference",
        "missing-adapter",
        "missing-store-capabilities",
        "setting-contains-credential-material",
        "setting-not-a-string",
        "undeclared-setting",
        "unknown-credential-provider",
        "unknown-credential-reference",
    }
)


def _declared_codes(tree: ast.AST) -> set[str]:
    declared: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        names = [target.id for target in node.targets if isinstance(target, ast.Name)]
        if any(name.startswith("_CODE_") for name in names) and isinstance(node.value, ast.Constant):
            declared.add(str(node.value.value))
    return declared


_KEBAB_LITERAL = re.compile(r"^[a-z][a-z0-9]*(-[a-z0-9]+)+$")


def _kebab_literals(tree: ast.AST) -> set[str]:
    return {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and _KEBAB_LITERAL.fullmatch(node.value) is not None
    }


def test_the_core_can_emit_exactly_the_frozen_code_enumeration() -> None:
    # Collected from the implementation, not restated: a new check introduces a code here and
    # this test is what asks whether the envelope agreed to it.
    tree = ast.parse(Path(validation.__file__).read_text(encoding="utf-8"))

    assert _declared_codes(tree) == FROZEN_CODES
    # A code written as a bare literal rather than as a _CODE_ constant would slip past the
    # scan above; every finding code is kebab-case and nothing else in the module is.
    assert _kebab_literals(tree) == FROZEN_CODES


def _totality_package_data() -> dict[str, Any]:
    # A missing adapter makes its role unevaluable, so it goes on one role and every other
    # family is spread across the other role, the store, and the credential declarations.
    data = package_data()
    data["credentials"]["bad-provider"] = {"provider": "vault", "identifier": "DECLARED_TOKEN"}
    data["credentials"]["bad-identifier"] = {"provider": "env", "identifier": "NOT-VALID"}
    data["configuration"]["source"] = {
        "name": "genericrestapi",
        "settings": {
            "url": "api.example",
            "api_endpoint": "https://evil.example/api",
            "token": {"$credential": "nope"},
            "username": "declared-in-line",
            "password": {"$credential": "netbox-token", "fallback": "declared-in-line"},
            "response_key_pattern": {"$credential": "netbox-token"},
            "bogus_source": 1,
        },
    }
    data["configuration"]["destination"] = {"name": "NetBox", "settings": {"url": "http://localhost:8000"}}
    data["configuration"]["store"] = {
        "type": "redis",
        "settings": {
            "url": {"$credential": "nope"},
            "password": "declared-in-line",
            "host": {"$credential": "netbox-token"},
            "bogus_store": 1,
        },
    }
    data["configuration"]["schema_mapping"] = [{"name": "Device", "mapping": "https://evil.example/devices"}]
    return data


def test_one_package_carrying_every_reachable_family_keeps_the_sort_total() -> None:
    # Nine of these codes appear more than once and are separated only by location, which is
    # the collision a per-family test cannot see.
    findings = collect_findings(package(_totality_package_data()))

    keys = [(finding.location, finding.severity, finding.code) for finding in findings]
    assert len(set(keys)) == len(keys)
    assert len(keys) == 15
    assert {finding.code for finding in findings} == {
        "credential-path-not-declared",
        "endpoint-not-absolute",
        "endpoint-not-relative",
        "inline-credential-value",
        "malformed-credential-reference",
        "missing-adapter",
        "undeclared-setting",
        "unknown-credential-provider",
        "unknown-credential-reference",
        # The adapter's own code, passed through rather than replaced.
        "unsafe-rest-request-endpoint",
    }


def _collision_prone_package_data() -> dict[str, Any]:
    """The two collisions a per-family fixture cannot see, in one package.

    A shared adapter validator judging one package-level mapping under both roles, and a
    credential reference at a setting name the adapter does not declare.
    """
    data = _shared_validator_package_data()
    data["configuration"]["source"]["settings"]["api_key"] = {"$credential": "netbox-token"}
    return data


def test_the_collision_prone_package_reports_each_defect_once() -> None:
    assert _triples(_collision_prone_package_data()) == [
        ("unsafe-rest-request-endpoint", "/configuration/schema_mapping/0/mapping"),
        ("undeclared-setting", "/configuration/source/settings/api_key"),
    ]


def _reachable_codes(monkeypatch: pytest.MonkeyPatch) -> set[str]:
    reached: set[str] = set()
    for _name, mutate, _message in SHIPPED_REFUSALS:
        data = package_data()
        mutate(data)
        reached.update(finding.code for finding in collect_findings(package(data)))
    reached.update(
        finding.code
        for finding in collect_findings(
            package(
                _over_cap_package_data(
                    reversed_declaration=False,
                )
            )
        )
    )
    monkeypatch.setattr(validation, "BUILTIN_ADAPTER_CAPABILITIES", _capabilities_with(_raising_validator))
    reached.update(finding.code for finding in collect_findings(package(_both_roles_package_data(mapping="/x"))))
    return reached


def test_every_frozen_code_is_reachable_and_nothing_else_is_emitted(monkeypatch: pytest.MonkeyPatch) -> None:
    reached = _reachable_codes(monkeypatch)

    # The adapter-owned code is emitted by an adapter, not by the core, so it is not frozen here.
    assert reached - {"unsafe-rest-request-endpoint"} == FROZEN_CODES


# Resolution, network access, and destination schema reads all belong to later slices. The core
# judges declared content only, so none of these may appear in it.
FORBIDDEN_IN_THE_CORE = (
    "EnvironmentCredentialProvider",
    "environ",
    "httpx",
    "provider_for",
    "requests",
    "resolve",
    "resolve_reference",
)


def test_the_core_reads_declared_content_only() -> None:
    tree = ast.parse(Path(validation.__file__).read_text(encoding="utf-8"))
    called = {
        node.func.id if isinstance(node.func, ast.Name) else node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, (ast.Name, ast.Attribute))
    }
    severities = {
        keyword.value.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        for keyword in node.keywords
        if keyword.arg == "severity" and isinstance(keyword.value, ast.Constant)
    }

    assert severities == {"error"}
    assert called & set(FORBIDDEN_IN_THE_CORE) == set()


_FINDING_TEXT_BOUND = 256


# A truncated pointer ends with the marker and the digest that keeps it distinguishable.
_TRUNCATED_POINTER = re.compile(r"\\u2026[0-9a-f]{8}$")


def test_a_hostile_declaration_still_yields_a_well_formed_finding() -> None:
    # A finding pointer is bounded and refuses a lone "~", while the diagnostic truncation
    # marker is "~2". So the marker changes form here, to one escaping can never produce.
    over_length_key = package_data()
    over_length_key["configuration"]["source"]["settings"]["k" * 5000] = 1
    deep_node: Any = {"$credential": "netbox-token"}
    for _ in range(8):
        deep_node = {"n" * 60: deep_node}
    deep_nesting = package_data()
    deep_nesting["configuration"]["source"]["settings"]["verify_ssl"] = deep_node

    truncated_component = collect_findings(package(over_length_key))
    truncated_pointer = collect_findings(package(deep_nesting))

    assert [finding.code for finding in truncated_component] == ["undeclared-setting"]
    assert _TRUNCATED_POINTER.search(truncated_component[0].location) is not None
    assert [finding.code for finding in truncated_pointer] == ["credential-path-not-declared"]
    assert _TRUNCATED_POINTER.search(truncated_pointer[0].location) is not None
    assert len(truncated_pointer[0].location) == _FINDING_TEXT_BOUND
    assert len(truncated_pointer[0].message) == _FINDING_TEXT_BOUND


def _pointer_bound_collision() -> dict[str, Any]:
    """Two distinct ``$credential`` nodes whose pointers share a >256-character prefix."""
    data = package_data()
    node: Any = {"a": {"$credential": "netbox-token"}, "b": {"$credential": "netbox-token"}}
    for _ in range(8):
        node = {"n" * 60: node}
    data["configuration"]["source"]["settings"]["verify_ssl"] = node
    return data


def _component_bound_collision() -> dict[str, Any]:
    """Two undeclared settings whose names differ only past the component bound."""
    data = package_data()
    shared = "k" * 70
    data["configuration"]["source"]["settings"][f"{shared}a"] = 1
    data["configuration"]["source"]["settings"][f"{shared}b"] = 1
    return data


@pytest.mark.parametrize(
    ("build", "expected_code"),
    [
        pytest.param(_pointer_bound_collision, "credential-path-not-declared", id="pointer-bound"),
        pytest.param(_component_bound_collision, "undeclared-setting", id="component-bound"),
    ],
)
def test_two_defects_under_one_truncated_pointer_stay_distinguishable(
    build: Callable[[], dict[str, Any]],
    expected_code: str,
) -> None:
    # Truncation is lossy at both bounds, so without a digest of the untruncated pointer two
    # separate defects arrive as one byte-identical finding twice and the operator cannot tell
    # them apart. The module's global "no two findings share (location, severity, code)" claim
    # is what that falsifies.
    findings = collect_findings(package(build()))

    assert [finding.code for finding in findings] == [expected_code, expected_code]
    assert findings[0].location != findings[1].location
    assert findings[0] != findings[1]
    assert all(len(finding.location) <= _FINDING_TEXT_BOUND for finding in findings)
    assert all(_TRUNCATED_POINTER.search(finding.location) is not None for finding in findings)


def _normalized_walk_collision() -> dict[str, Any]:
    """Two undeclared ``$credential`` nodes whose keys differ only by a trailing space."""
    data = package_data()
    data["configuration"]["source"]["settings"]["verify_ssl"] = {
        "leaked": {"$credential": "netbox-token"},
        "leaked ": {"$credential": "netbox-token"},
    }
    return data


def _normalized_setting_collision() -> dict[str, Any]:
    """Two undeclared adapter settings whose names differ only by a trailing space."""
    data = package_data()
    data["configuration"]["source"]["settings"]["zz"] = 1
    data["configuration"]["source"]["settings"]["zz "] = 1
    return data


def _normalized_store_collision() -> dict[str, Any]:
    """Two undeclared store settings whose names differ only by a trailing space."""
    data = package_data()
    data["configuration"]["store"] = {"type": "redis", "settings": {"zz": 1, "zz ": 1}}
    return data


def _normalized_empty_component_collision() -> dict[str, Any]:
    """An empty declared key and a whitespace-only one, which normalize onto each other."""
    data = package_data()
    data["configuration"]["source"]["settings"]["verify_ssl"] = {
        "": {"$credential": "netbox-token"},
        " ": {"$credential": "netbox-token"},
    }
    return data


@pytest.mark.parametrize(
    ("build", "expected_code"),
    [
        pytest.param(_normalized_walk_collision, "credential-path-not-declared", id="walk"),
        pytest.param(_normalized_setting_collision, "undeclared-setting", id="adapter-setting"),
        pytest.param(_normalized_store_collision, "undeclared-setting", id="store-setting"),
        pytest.param(_normalized_empty_component_collision, "credential-path-not-declared", id="empty-component"),
    ],
)
def test_two_defects_the_finding_model_normalizes_together_stay_distinguishable(
    build: Callable[[], dict[str, Any]],
    expected_code: str,
) -> None:
    # ValidationFinding normalizes what it stores, so the pointer the core builds is not always
    # the pointer the finding carries. That is a third lossy transform beside the two length
    # bounds and it breaks the same way: two declared keys differing only in what the model
    # strips arrive at one pointer, and (code, location) deduplication then deletes one of the
    # two defects outright. Under-reporting a credential-safety defect is the failure this pins.
    findings = collect_findings(package(build()))

    assert [finding.code for finding in findings] == [expected_code, expected_code]
    assert findings[0].location != findings[1].location
    assert all(len(finding.location) <= _FINDING_TEXT_BOUND for finding in findings)
    # Exactly one of the pair was rewritten by the model, so exactly one carries the marker.
    marked = [finding for finding in findings if _TRUNCATED_POINTER.search(finding.location) is not None]
    assert len(marked) == 1


def test_two_defects_at_one_normalized_pointer_keep_their_own_codes() -> None:
    # Distinguishability is not only about deduplication deleting a finding. Two independent
    # defects whose keys differ only by what the model strips must not be rendered at one
    # pointer either, or the operator is told to look in one place for two different keys.
    data = package_data()
    data["configuration"]["source"]["settings"]["url "] = "demo.netbox.dev"
    data["configuration"]["source"]["settings"]["url"] = "demo.netbox.dev"

    findings = collect_findings(package(data))
    locations = [finding.location for finding in findings]

    assert len(findings) == 2
    assert len(set(locations)) == 2


@pytest.mark.parametrize(
    "build",
    [
        pytest.param(_totality_package_data, id="every-family"),
        pytest.param(_collision_prone_package_data, id="collision-prone"),
        pytest.param(_pointer_bound_collision, id="pointer-bound"),
        pytest.param(_component_bound_collision, id="component-bound"),
        pytest.param(_normalized_walk_collision, id="normalized-walk"),
        pytest.param(_normalized_setting_collision, id="normalized-adapter-setting"),
        pytest.param(_normalized_store_collision, id="normalized-store-setting"),
        pytest.param(_normalized_empty_component_collision, id="normalized-empty-component"),
    ],
)
def test_no_two_findings_share_a_location_severity_and_code(build: Callable[[], dict[str, Any]]) -> None:
    # The global invariant, not a property of one fixture. Every family that has been shown to
    # break it gets a fixture here.
    findings = collect_findings(package(build()))

    keys = [(finding.location, finding.severity, finding.code) for finding in findings]
    assert len(keys) >= 2
    assert len(set(keys)) == len(keys)


def test_every_frozen_code_is_documented_for_an_operator() -> None:
    # The codes are the part of the contract AD030 says is stable, so an undocumented one is a
    # stable identifier nobody can look up. Anchored at the repository root, not the working
    # directory, so it cannot pass by reading nothing.
    reference = Path(infrahub_sync.__file__).parents[1] / "docs" / "docs" / "reference" / "durable-product-records.mdx"
    documented = reference.read_text(encoding="utf-8")

    assert "### Finding codes" in documented
    assert {code for code in FROZEN_CODES if f"`{code}`" not in documented} == set()
