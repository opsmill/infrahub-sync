"""Destination schema validation — the explicit opt-in checks outside the declared-content core.

Everything here exercises :mod:`infrahub_sync.configuration.schema_validation` and the
accessor seam on :mod:`infrahub_sync.configuration.capabilities`. Schema snapshots are
injected through the seam; no test contacts a live server (the live-read exercise is the
opt-in integration test). The declared-content core and its frozen tests stay untouched —
this module carries the schema-path mirror of the core's exact-set and reachability tests.
"""

from __future__ import annotations

import ast
import re
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from infrahub_sync.cache import compute_schema_subhash
from infrahub_sync.configuration import capabilities as capabilities_module
from infrahub_sync.configuration import schema_validation
from infrahub_sync.configuration import validation as validation_module
from infrahub_sync.configuration.capabilities import (
    BUILTIN_ADAPTER_CAPABILITIES,
    AdapterConfigurationCapabilities,
    DestinationSchemaReadError,
)
from infrahub_sync.configuration.credentials import CredentialConfigurationError
from infrahub_sync.configuration.schema_validation import (
    collect_destination_schema_findings,
    resolve_declared_destination_branch,
)
from infrahub_sync.configuration.validation import collect_findings, validate_package_credentials
from tests.configuration.validation_packages import package, package_data

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from infrahub_sync.configuration import ConfigurationPackage


# A real destination schema snapshot shape: kind -> attributes (name -> kind) and
# relationships (name -> peer + cardinality), exactly what the accessor contract returns.
_SNAPSHOT: dict[str, Any] = {
    "InfraDevice": {
        "attributes": {"name": "Text", "description": "Text"},
        "relationships": {
            "site": {"peer": "LocationSite", "cardinality": "one"},
            "tags": {"peer": "BuiltinTag", "cardinality": "many"},
        },
    },
    "LocationSite": {"attributes": {"name": "Text"}, "relationships": {}},
}


class _SpiedAccessor:
    """An injected accessor that records every read and returns a fixed snapshot."""

    def __init__(self, snapshot: Mapping[str, Any] | None = None) -> None:
        self.snapshot = _SNAPSHOT if snapshot is None else snapshot
        self.calls: list[str] = []

    def __call__(self, package_: ConfigurationPackage, branch: str) -> Mapping[str, Any]:
        del package_
        self.calls.append(branch)
        return self.snapshot


def _raising_accessor(reason: str) -> Callable[[ConfigurationPackage, str], Mapping[str, Any]]:
    def read(package_: ConfigurationPackage, branch: str) -> Mapping[str, Any]:
        del package_
        msg = f"schema read failed for branch {branch!r}"
        raise DestinationSchemaReadError(msg, reason=reason)

    return read


def _table_with_accessor(
    accessor: Callable[[ConfigurationPackage, str], Mapping[str, Any]],
) -> dict[str, AdapterConfigurationCapabilities]:
    table = dict(BUILTIN_ADAPTER_CAPABILITIES)
    table["infrahub"] = replace(table["infrahub"], destination_schema_accessor=accessor)
    return table


def _inject(
    monkeypatch: pytest.MonkeyPatch,
    table: dict[str, AdapterConfigurationCapabilities],
) -> None:
    monkeypatch.setattr(schema_validation, "BUILTIN_ADAPTER_CAPABILITIES", table)


def _mapping_package_data(
    fields: list[dict[str, Any]] | None = None,
    *,
    kind: str = "InfraDevice",
) -> dict[str, Any]:
    data = package_data()
    data["configuration"]["schema_mapping"] = [
        {"name": kind, "mapping": "dcim.devices", "fields": [] if fields is None else fields}
    ]
    return data


def _codes_and_locations(findings: tuple[Any, ...]) -> list[tuple[str, str]]:
    return [(finding.code, finding.location) for finding in findings]


# --- The accessor seam (registration-time invariant) ---------------------------------


def test_declaring_schema_validation_without_an_accessor_is_a_registration_time_error() -> None:
    with pytest.raises(ValueError, match="schema"):
        AdapterConfigurationCapabilities(
            adapter_name="declared-without-accessor",
            roles=frozenset({"destination"}),
            destination_schema_validation=True,
        )


def test_an_accessor_without_the_declaration_is_a_registration_time_error() -> None:
    with pytest.raises(ValueError, match="schema"):
        AdapterConfigurationCapabilities(
            adapter_name="accessor-without-declaration",
            roles=frozenset({"destination"}),
            destination_schema_accessor=_SpiedAccessor(),
        )


def test_the_bundled_infrahub_declaration_binds_the_live_accessor() -> None:
    capabilities = BUILTIN_ADAPTER_CAPABILITIES["infrahub"]
    assert capabilities.destination_schema_validation is True
    assert capabilities.destination_schema_accessor is capabilities_module._read_infrahub_destination_schema


def test_a_read_error_requires_a_short_lowercase_reason() -> None:
    error = DestinationSchemaReadError("boom", reason="timeout")
    assert error.reason == "timeout"
    with pytest.raises(ValueError, match="reason"):
        DestinationSchemaReadError("boom", reason="Not A Word!")


# --- The interim branch-resolution helper (SYNC-79 direction) -------------------------


def test_the_declared_branch_setting_is_resolved() -> None:
    data = package_data()
    data["configuration"]["destination"]["settings"]["branch"] = "staging"
    assert resolve_declared_destination_branch(package(data)) == "staging"


def test_an_absent_branch_setting_resolves_to_main() -> None:
    assert resolve_declared_destination_branch(package(package_data())) == "main"


def test_a_non_string_branch_setting_resolves_to_main() -> None:
    data = package_data()
    data["configuration"]["destination"]["settings"]["branch"] = 5
    assert resolve_declared_destination_branch(package(data)) == "main"


def test_the_ambient_environment_is_never_read_for_the_branch(monkeypatch: pytest.MonkeyPatch) -> None:
    # The helper reads the declared setting only — never the environment (SYNC-79).
    monkeypatch.setenv("INFRAHUB_BRANCH", "ambient-branch")
    monkeypatch.setenv("INFRAHUB_DEFAULT_BRANCH", "ambient-branch")
    assert resolve_declared_destination_branch(package(package_data())) == "main"


def test_the_schema_read_consumes_the_resolved_branch(monkeypatch: pytest.MonkeyPatch) -> None:
    accessor = _SpiedAccessor()
    _inject(monkeypatch, _table_with_accessor(accessor))
    data = package_data()
    data["configuration"]["destination"]["settings"]["branch"] = "staging"

    collect_destination_schema_findings(package(data))

    assert accessor.calls == ["staging"]


# --- AR3: the four error fixtures against an injected snapshot ------------------------


def test_an_unknown_kind_is_an_error_finding(monkeypatch: pytest.MonkeyPatch) -> None:
    _inject(monkeypatch, _table_with_accessor(_SpiedAccessor()))
    # The bogus field under the unknown kind is judged nowhere deeper: the kind made the
    # subtree unevaluable, so exactly one finding names the defect (the OES-15 rule).
    data = _mapping_package_data([{"name": "bogus"}], kind="NopeKind")

    result = collect_destination_schema_findings(package(data))

    assert _codes_and_locations(result.findings) == [
        ("destination-schema-mismatch", "/configuration/schema_mapping/0/name")
    ]


def test_an_unknown_field_is_an_error_finding(monkeypatch: pytest.MonkeyPatch) -> None:
    _inject(monkeypatch, _table_with_accessor(_SpiedAccessor()))
    data = _mapping_package_data([{"name": "serial_number", "mapping": "serial"}])

    result = collect_destination_schema_findings(package(data))

    assert _codes_and_locations(result.findings) == [
        ("destination-schema-mismatch", "/configuration/schema_mapping/0/fields/0/name")
    ]


def test_a_reference_on_an_attribute_is_a_wrong_type_error_finding(monkeypatch: pytest.MonkeyPatch) -> None:
    _inject(monkeypatch, _table_with_accessor(_SpiedAccessor()))
    # `name` is an attribute of InfraDevice; a reference can only bind a relationship.
    data = _mapping_package_data([{"name": "name", "mapping": "name", "reference": "LocationSite"}])

    result = collect_destination_schema_findings(package(data))

    assert _codes_and_locations(result.findings) == [
        ("destination-schema-mismatch", "/configuration/schema_mapping/0/fields/0/reference")
    ]


@pytest.mark.parametrize(
    ("field", "location"),
    [
        pytest.param(
            {"name": "site", "static": ["a", "b"]},
            "/configuration/schema_mapping/0/fields/0/static",
            id="list-static-on-cardinality-one",
        ),
        pytest.param(
            {"name": "tags", "static": "a"},
            "/configuration/schema_mapping/0/fields/0/static",
            id="scalar-static-on-cardinality-many",
        ),
    ],
)
def test_a_wrong_relationship_cardinality_is_an_error_finding(
    monkeypatch: pytest.MonkeyPatch, field: dict[str, Any], location: str
) -> None:
    _inject(monkeypatch, _table_with_accessor(_SpiedAccessor()))

    result = collect_destination_schema_findings(package(_mapping_package_data([field])))

    assert _codes_and_locations(result.findings) == [("destination-schema-mismatch", location)]


def test_a_conforming_mapping_yields_no_findings_and_a_fingerprint(monkeypatch: pytest.MonkeyPatch) -> None:
    _inject(monkeypatch, _table_with_accessor(_SpiedAccessor()))
    data = _mapping_package_data(
        [
            {"name": "name", "mapping": "name"},
            {"name": "site", "mapping": "site.name", "reference": "LocationSite"},
            {"name": "tags", "static": ["managed"]},
        ]
    )
    parsed = package(data)

    result = collect_destination_schema_findings(parsed)

    assert result.findings == ()
    assert result.schema_fingerprint == compute_schema_subhash(parsed.configuration, _SNAPSHOT)


# --- AR2: capability gating -----------------------------------------------------------


def test_an_explicit_request_against_a_non_declaring_destination_is_an_error_finding() -> None:
    # peeringmanager is a real destination that does not declare schema validation, and it
    # declares update as its only write operation, so the shipped infrahub_to_peering-manager
    # shape — creates requested, update-only declared — reports both defects (AR5 fixture).
    data = package_data()
    data["configuration"]["destination"]["name"] = "peeringmanager"

    result = collect_destination_schema_findings(package(data))

    assert _codes_and_locations(result.findings) == [
        ("destination-schema-validation-unsupported", "/configuration/destination"),
        ("unsupported-destination-write", "/configuration/destination"),
    ]
    assert result.schema_fingerprint is None


def test_an_update_only_request_against_an_update_only_destination_is_clean() -> None:
    # SKIP_UNMATCHED_SRC removes create, and delete is never requested under the supported
    # live-sync profile even though SKIP_UNMATCHED_DST is not configured (SYNC-78).
    data = package_data()
    data["configuration"]["destination"]["name"] = "peeringmanager"
    data["configuration"]["diffsync_flags"] = ["SKIP_UNMATCHED_SRC"]

    result = collect_destination_schema_findings(package(data))

    assert [finding.code for finding in result.findings] == ["destination-schema-validation-unsupported"]


def test_an_unknown_destination_adapter_adds_no_schema_findings() -> None:
    # The core already reports missing-adapter for the destination and keeps judging
    # everything independent of it; the schema checks add nothing on the unevaluable subtree.
    data = package_data()
    data["configuration"]["destination"]["name"] = "doesnotexist"
    data["configuration"]["source"]["settings"]["bogus_source"] = "x"
    parsed = package(data)

    result = collect_destination_schema_findings(parsed)
    core = collect_findings(parsed)

    assert result.findings == ()
    assert result.schema_fingerprint is None
    assert ("missing-adapter", "/configuration/destination") in _codes_and_locations(core)
    assert ("undeclared-setting", "/configuration/source/settings/bogus_source") in _codes_and_locations(core)


# --- AR9: a failed schema read is a typed error finding -------------------------------


@pytest.mark.parametrize("reason", ["timeout", "unauthorized", "unreachable"])
def test_a_failed_schema_read_is_an_error_finding_not_a_raise(monkeypatch: pytest.MonkeyPatch, reason: str) -> None:
    _inject(monkeypatch, _table_with_accessor(_raising_accessor(reason)))

    result = collect_destination_schema_findings(package(package_data()))

    assert _codes_and_locations(result.findings) == [("destination-schema-read-failed", "/configuration/destination")]
    assert reason in result.findings[0].message
    assert result.schema_fingerprint is None


def test_a_failed_schema_read_does_not_suppress_the_write_operations_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # An update-only destination that also declares schema validation, whose read fails:
    # the two checks are independent, so both report (the accumulating contract).
    table = dict(BUILTIN_ADAPTER_CAPABILITIES)
    table["peeringmanager"] = replace(
        table["peeringmanager"],
        destination_schema_validation=True,
        destination_schema_accessor=_raising_accessor("timeout"),
    )
    _inject(monkeypatch, table)
    data = package_data()
    data["configuration"]["destination"]["name"] = "peeringmanager"

    result = collect_destination_schema_findings(package(data))

    assert _codes_and_locations(result.findings) == [
        ("destination-schema-read-failed", "/configuration/destination"),
        ("unsupported-destination-write", "/configuration/destination"),
    ]


# --- AR6: determinism and ordering -----------------------------------------------------


def test_the_same_package_and_snapshot_produce_byte_identical_findings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _inject(monkeypatch, _table_with_accessor(_SpiedAccessor()))
    data = _mapping_package_data([{"name": "bogus"}, {"name": "also_bogus"}])

    first = collect_destination_schema_findings(package(data))
    second = collect_destination_schema_findings(package(data))

    assert first.findings == second.findings
    assert first.schema_fingerprint == second.schema_fingerprint


def test_schema_findings_arrive_in_the_stable_sort_order(monkeypatch: pytest.MonkeyPatch) -> None:
    _inject(monkeypatch, _table_with_accessor(_SpiedAccessor()))
    data = package_data()
    data["configuration"]["schema_mapping"] = [
        {"name": "InfraDevice", "mapping": "dcim.devices", "fields": [{"name": "bogus"}]},
        {"name": "NopeKind", "mapping": "dcim.nope"},
    ]

    result = collect_destination_schema_findings(package(data))

    assert _codes_and_locations(result.findings) == [
        ("destination-schema-mismatch", "/configuration/schema_mapping/0/fields/0/name"),
        ("destination-schema-mismatch", "/configuration/schema_mapping/1/name"),
    ]


# --- AR4 (retained): the wrapper path never performs a schema read ---------------------


def test_the_wrapper_and_the_core_never_call_a_schema_accessor(monkeypatch: pytest.MonkeyPatch) -> None:
    accessor = _SpiedAccessor()
    table = _table_with_accessor(accessor)
    monkeypatch.setattr(validation_module, "BUILTIN_ADAPTER_CAPABILITIES", table)
    _inject(monkeypatch, table)
    defective = package_data()
    defective["configuration"]["destination"]["settings"]["bogus_dest"] = "x"

    assert validate_package_credentials(package(package_data())) is None
    with pytest.raises(CredentialConfigurationError, match="bogus_dest"):
        validate_package_credentials(package(defective))
    collect_findings(package(defective))

    assert accessor.calls == []


# --- AR8: the schema module's own exact-set and reachability tests ---------------------


FROZEN_SCHEMA_CODES = frozenset(
    {
        # The destination-schema-mismatch family: declared mappings judged against the
        # snapshot, and the capability gate for a destination that cannot be judged at all.
        "destination-schema-mismatch",
        "destination-schema-validation-unsupported",
        # The unsupported-destination-write family.
        "unsupported-destination-write",
        # AR9's schema-read-failure family.
        "destination-schema-read-failed",
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


def test_the_schema_module_can_emit_exactly_the_frozen_schema_code_enumeration() -> None:
    # The schema-path mirror of the core's exact-set test: collected from the implementation,
    # not restated, and extended only by new enumerated surface — never by loosening this.
    tree = ast.parse(Path(schema_validation.__file__).read_text(encoding="utf-8"))

    assert _declared_codes(tree) == FROZEN_SCHEMA_CODES
    assert _kebab_literals(tree) == FROZEN_SCHEMA_CODES


def test_every_frozen_schema_code_is_reachable_and_nothing_else_is_emitted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reached: set[str] = set()
    severities: set[str] = set()

    _inject(monkeypatch, _table_with_accessor(_SpiedAccessor()))
    for finding in collect_destination_schema_findings(package(_mapping_package_data(kind="NopeKind"))).findings:
        reached.add(finding.code)
        severities.add(finding.severity)
    _inject(monkeypatch, _table_with_accessor(_raising_accessor("timeout")))
    for finding in collect_destination_schema_findings(package(package_data())).findings:
        reached.add(finding.code)
        severities.add(finding.severity)
    _inject(monkeypatch, dict(BUILTIN_ADAPTER_CAPABILITIES))
    non_declaring = package_data()
    non_declaring["configuration"]["destination"]["name"] = "peeringmanager"
    for finding in collect_destination_schema_findings(package(non_declaring)).findings:
        reached.add(finding.code)
        severities.add(finding.severity)

    assert reached == FROZEN_SCHEMA_CODES
    assert severities == {"error"}
