"""Contract section 4 warning families: the emission surface in ``configuration/warnings.py``.

The core's own code enumeration (``test_validation_core.FROZEN_CODES``) and the schema
module's stay byte-untouched; this module carries the warnings module's mirror of the same
exact-set and reachability guards, plus the behavior of the two warning families.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any

import pytest

from infrahub_sync.configuration import (
    CredentialConfigurationError,
    collect_findings,
    validate_package_credentials,
)
from infrahub_sync.configuration import warnings as configuration_warnings
from tests.configuration.validation_packages import package, package_data

_MAX_FINDING_TEXT_LENGTH = 256


def _triples(data: dict[str, Any]) -> list[tuple[str, str, str]]:
    return [(finding.code, finding.severity, finding.location) for finding in collect_findings(package(data))]


# --- The intentional-omission family ----------------------------------------------------


def test_each_declared_omission_yields_one_warning_at_its_declaration() -> None:
    data = package_data()
    data["omissions"] = [
        {"kind": "InfraDevice", "fields": ["serial_number"]},
        {"kind": "InfraCircuit"},
    ]

    assert _triples(data) == [
        ("intentional-omission", "warning", "/omissions/0"),
        ("intentional-omission", "warning", "/omissions/1"),
    ]


def test_an_omission_reason_reaches_the_warning_message_verbatim() -> None:
    reason = "serials tracked in the CMDB"
    data = package_data()
    data["omissions"] = [{"kind": "InfraDevice", "reason": reason}]

    findings = collect_findings(package(data))

    assert [finding.code for finding in findings] == ["intentional-omission"]
    assert findings[0].message.endswith(f": {reason}")


def test_a_reason_at_the_model_bound_yields_a_well_formed_finding() -> None:
    # AR10 at the bound: the 160-character model bound plus the fixed template stays under
    # the 256-character finding-text limit, verbatim and untruncated.
    reason = "r" * 160
    data = package_data()
    data["omissions"] = [{"kind": "InfraDevice", "reason": reason}]

    findings = collect_findings(package(data))

    assert [finding.code for finding in findings] == ["intentional-omission"]
    assert findings[0].message.endswith(f": {reason}")
    assert len(findings[0].message) <= _MAX_FINDING_TEXT_LENGTH


def test_an_omission_naming_a_mapped_field_is_an_error_replacing_the_warning() -> None:
    # A contradictory declaration is a package defect, not a preference: exactly one
    # finding at the declaration, and it is the error.
    data = package_data()
    data["configuration"]["schema_mapping"] = [
        {"name": "InfraDevice", "mapping": "dcim.devices", "fields": [{"name": "serial_number", "mapping": "serial"}]}
    ]
    data["omissions"] = [{"kind": "InfraDevice", "fields": ["serial_number"]}]

    assert _triples(data) == [("omission-contradicts-mapping", "error", "/omissions/0")]


def test_a_whole_kind_omission_contradicts_any_mapping_of_that_kind() -> None:
    data = package_data()
    data["configuration"]["schema_mapping"] = [{"name": "InfraDevice", "mapping": "dcim.devices"}]
    data["omissions"] = [{"kind": "InfraDevice"}]

    assert _triples(data) == [("omission-contradicts-mapping", "error", "/omissions/0")]


def test_omitting_an_unmapped_field_of_a_mapped_kind_stays_a_warning() -> None:
    # The mapping maps other fields; omitting a field it does not map is consistent intent.
    data = package_data()
    data["configuration"]["schema_mapping"] = [
        {"name": "InfraDevice", "mapping": "dcim.devices", "fields": [{"name": "hostname", "mapping": "name"}]}
    ]
    data["omissions"] = [{"kind": "InfraDevice", "fields": ["serial_number"]}]

    assert _triples(data) == [("intentional-omission", "warning", "/omissions/0")]


def test_omitting_an_unmapped_kind_stays_a_warning() -> None:
    data = package_data()
    data["configuration"]["schema_mapping"] = [{"name": "InfraCircuit", "mapping": "circuits.circuits"}]
    data["omissions"] = [{"kind": "InfraDevice"}]

    assert _triples(data) == [("intentional-omission", "warning", "/omissions/0")]


# --- The unqualified-optional-feature family ---------------------------------------------


def test_declaring_incremental_against_an_unqualified_source_yields_one_warning() -> None:
    # `incremental:` is an optional feature only some sources implement; a source whose
    # capability declaration does not qualify it silently runs full extraction today.
    data = package_data()
    data["configuration"]["source"] = {"name": "prometheus", "settings": {"url": "https://prom.example"}}
    data["configuration"]["incremental"] = {"full_resync_every": 5}

    assert _triples(data) == [("optional-feature-unqualified", "warning", "/configuration/incremental")]


def test_declaring_incremental_against_a_qualified_source_yields_no_warning() -> None:
    data = package_data()
    data["configuration"]["incremental"] = {"full_resync_every": 5}

    assert _triples(data) == []


def test_an_undeclared_incremental_feature_yields_no_warning() -> None:
    data = package_data()
    data["configuration"]["source"] = {"name": "prometheus", "settings": {"url": "https://prom.example"}}

    assert _triples(data) == []


def test_an_unknown_source_adapter_reports_no_feature_warning() -> None:
    # A missing capability needed to determine safety is an error, not a warning: the
    # core's missing-adapter finding owns the role and its subtree is unevaluable.
    data = package_data()
    data["configuration"]["source"]["name"] = "mystery"
    data["configuration"]["incremental"] = {"full_resync_every": 5}

    assert _triples(data) == [("missing-adapter", "error", "/configuration/source")]


# --- Determinism across invocations and presentations -----------------------------------


def test_mixed_severity_findings_are_deterministic_across_invocations_and_presentations() -> None:
    # Same package bytes, same installed adapter set: byte-identical ordered findings,
    # warnings included, on every invocation — and the wrapper still raises the shipped
    # message of the first error in execution order, which the warning families follow.
    data = package_data()
    data["configuration"]["source"] = {"name": "prometheus", "settings": {"url": "https://prom.example", "bogus": 1}}
    data["configuration"]["incremental"] = {"full_resync_every": 5}
    data["configuration"]["schema_mapping"] = [{"name": "InfraDevice", "mapping": "dcim.devices"}]
    data["omissions"] = [{"kind": "InfraCircuit"}, {"kind": "InfraDevice"}]

    first = collect_findings(package(data))

    assert first == collect_findings(package(data))
    assert [(finding.code, finding.severity, finding.location) for finding in first] == [
        ("optional-feature-unqualified", "warning", "/configuration/incremental"),
        ("undeclared-setting", "error", "/configuration/source/settings/bogus"),
        ("intentional-omission", "warning", "/omissions/0"),
        ("omission-contradicts-mapping", "error", "/omissions/1"),
    ]
    with pytest.raises(CredentialConfigurationError) as caught:
        validate_package_credentials(package(data))
    assert str(caught.value) == (
        "adapter 'prometheus' contains unsupported declared settings for the source role: [\"bogus\"]"
    )


# --- The warnings module's own exact-set and reachability guards ------------------------


FROZEN_WARNING_MODULE_CODES = frozenset(
    {
        "intentional-omission",
        "omission-contradicts-mapping",
        "optional-feature-unqualified",
    }
)
# The closed emission rule: only the two contract section 4 families carry a warning.
FROZEN_WARNING_SEVERITY_CODES = frozenset({"intentional-omission", "optional-feature-unqualified"})


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


def test_the_warnings_module_can_emit_exactly_the_frozen_code_enumeration() -> None:
    # The warnings-module mirror of the core's exact-set test: collected from the
    # implementation, not restated, and every code is a kebab-case literal nothing else is.
    tree = ast.parse(Path(configuration_warnings.__file__).read_text(encoding="utf-8"))

    assert _declared_codes(tree) == FROZEN_WARNING_MODULE_CODES
    assert _kebab_literals(tree) == FROZEN_WARNING_MODULE_CODES


def test_every_frozen_warning_module_code_is_reachable_and_nothing_else_is_emitted() -> None:
    data = package_data()
    data["configuration"]["source"] = {"name": "prometheus", "settings": {"url": "https://prom.example"}}
    data["configuration"]["incremental"] = {"full_resync_every": 5}
    data["configuration"]["schema_mapping"] = [{"name": "InfraDevice", "mapping": "dcim.devices"}]
    data["omissions"] = [
        {"kind": "InfraCircuit", "fields": ["provider"]},
        {"kind": "InfraDevice"},
    ]

    findings = collect_findings(package(data))

    assert {finding.code for finding in findings} == FROZEN_WARNING_MODULE_CODES
    assert {finding.code for finding in findings if finding.severity == "warning"} == FROZEN_WARNING_SEVERITY_CODES


def test_warning_severity_is_written_only_in_the_warnings_module() -> None:
    # The closed emission rule, structurally: across the configuration package, only the
    # warnings module hands "warning" to a finding constructor. The core's own AST test
    # already proves validation.py writes severity="error" and nothing else.
    package_directory = Path(configuration_warnings.__file__).parent
    for module_path in sorted(package_directory.glob("*.py")):
        tree = ast.parse(module_path.read_text(encoding="utf-8"))
        severities = {
            keyword.value.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            for keyword in node.keywords
            if keyword.arg == "severity" and isinstance(keyword.value, ast.Constant)
        }
        if module_path.name != "warnings.py":
            assert "warning" not in severities, f"{module_path.name} writes a warning severity"
