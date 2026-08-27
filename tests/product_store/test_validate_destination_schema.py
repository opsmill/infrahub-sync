"""The ``validate`` operation's explicit destination-schema opt-in.

The service contract this file pins: the default ``validate`` call is byte-identical to
the pre-opt-in behavior — zero schema reads, zero network I/O, no adapter construction —
and only an explicit :class:`DestinationSchemaOptions` request adds the schema checks and
records the judged snapshot's fingerprint. Snapshots are injected through the capability
seam; nothing here contacts a server.
"""

from __future__ import annotations

import ast
import socket
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import pytest

from infrahub_sync.cache import compute_schema_subhash
from infrahub_sync.configuration import schema_validation
from infrahub_sync.configuration import validation as validation_module
from infrahub_sync.configuration.capabilities import BUILTIN_ADAPTER_CAPABILITIES
from infrahub_sync.configuration.schema_validation import DestinationSchemaOptions
from infrahub_sync.configuration.validation import collect_findings
from infrahub_sync.product_store import configs as configs_service
from tests.configuration.validation_packages import package, package_data

if TYPE_CHECKING:
    from collections.abc import Mapping

    from infrahub_sync.configuration import ConfigurationPackage

_SNAPSHOT: dict[str, Any] = {
    "InfraDevice": {
        "attributes": {"name": "Text"},
        "relationships": {"site": {"peer": "LocationSite", "cardinality": "one"}},
    },
}


class _SpiedAccessor:
    """An injected accessor that records every read and returns a fixed snapshot."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def __call__(self, package_: ConfigurationPackage, branch: str) -> Mapping[str, Any]:
        del package_
        self.calls.append(branch)
        return _SNAPSHOT


def _inject_accessor(monkeypatch: pytest.MonkeyPatch) -> _SpiedAccessor:
    accessor = _SpiedAccessor()
    table = dict(BUILTIN_ADAPTER_CAPABILITIES)
    table["infrahub"] = replace(table["infrahub"], destination_schema_accessor=accessor)
    monkeypatch.setattr(schema_validation, "BUILTIN_ADAPTER_CAPABILITIES", table)
    return accessor


def _registered(tmp_path: Path, data: dict[str, Any] | None = None) -> tuple[str, str]:
    location = str(tmp_path / "product-cache")
    Path(location).mkdir()
    registered = configs_service.register(
        package=package_data() if data is None else data,
        product_cache_location=location,
    )
    return registered.version.config_id, location


def _validate(
    config_id: str,
    location: str,
    destination_schema: DestinationSchemaOptions | None = None,
) -> configs_service.ValidationReport:
    return configs_service.validate(
        config_id=config_id,
        registry_version=1,
        product_cache_location=location,
        destination_schema=destination_schema,
    )


def _mapping_package_data(kind: str) -> dict[str, Any]:
    data = package_data()
    data["configuration"]["schema_mapping"] = [{"name": kind, "mapping": "dcim.devices"}]
    return data


# --- AR7: the default path is byte-identical — no schema read, no network, no adapter ---


def test_a_default_validate_never_calls_the_accessor_and_carries_no_fingerprint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    accessor = _inject_accessor(monkeypatch)
    config_id, location = _registered(tmp_path)

    report = _validate(config_id, location)

    assert accessor.calls == []
    assert report.destination_schema_fingerprint is None
    assert report.findings == ()


def test_a_default_validate_performs_no_network_io(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_id, location = _registered(tmp_path)

    def _refuse(*args: object, **kwargs: object) -> object:
        del args, kwargs
        msg = "the default validate path opened a network socket"
        raise AssertionError(msg)

    monkeypatch.setattr(socket, "socket", _refuse)

    report = _validate(config_id, location)

    assert report.findings == ()


def test_the_opt_in_performs_no_network_io_when_the_snapshot_is_injected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    accessor = _inject_accessor(monkeypatch)
    config_id, location = _registered(tmp_path)

    def _refuse(*args: object, **kwargs: object) -> object:
        del args, kwargs
        msg = "an injected schema read opened a network socket"
        raise AssertionError(msg)

    monkeypatch.setattr(socket, "socket", _refuse)

    report = _validate(config_id, location, destination_schema=DestinationSchemaOptions())

    assert accessor.calls == ["main"]
    assert report.findings == ()


_SERVICE_MODULES = (configs_service.__file__, schema_validation.__file__)


def test_the_service_and_schema_modules_construct_no_adapters() -> None:
    # The structural half of AR7: no adapter module is imported and no dynamic import
    # mechanism is reachable from the validate path, so no source adapter can be constructed.
    for module_path in _SERVICE_MODULES:
        tree = ast.parse(Path(module_path).read_text(encoding="utf-8"))
        imported = {name.name for node in ast.walk(tree) if isinstance(node, ast.Import) for name in node.names} | {
            node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module
        }
        called = {
            node.func.id if isinstance(node.func, ast.Name) else node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, (ast.Name, ast.Attribute))
        }

        assert not {name for name in imported if name.startswith("infrahub_sync.adapters")}, module_path
        assert not called & {"import_module", "__import__", "import_adapter"}, module_path


# --- AR6: the fingerprint and determinism ----------------------------------------------


def test_the_opt_in_records_the_snapshot_fingerprint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _inject_accessor(monkeypatch)
    config_id, location = _registered(tmp_path)

    report = _validate(config_id, location, destination_schema=DestinationSchemaOptions())

    assert report.destination_schema_fingerprint == compute_schema_subhash(
        package(package_data()).configuration, _SNAPSHOT
    )


def test_the_opt_in_report_is_identical_across_invocations(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _inject_accessor(monkeypatch)
    config_id, location = _registered(tmp_path, _mapping_package_data("NopeKind"))

    first = _validate(config_id, location, destination_schema=DestinationSchemaOptions())
    second = _validate(config_id, location, destination_schema=DestinationSchemaOptions())

    assert first.findings == second.findings
    assert first.findings != ()
    assert first.destination_schema_fingerprint == second.destination_schema_fingerprint


# --- The merged report: core and schema findings under one sort contract ----------------


def test_core_and_schema_findings_merge_in_the_stable_sort_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Capability drift after registration: the source adapter disappears from the table,
    # so the core reports missing-adapter while the schema checks judge the snapshot.
    accessor = _SpiedAccessor()
    table = dict(BUILTIN_ADAPTER_CAPABILITIES)
    table["infrahub"] = replace(table["infrahub"], destination_schema_accessor=accessor)
    del table["netbox"]
    data = _mapping_package_data("NopeKind")
    config_id, location = _registered(tmp_path, data)
    monkeypatch.setattr(schema_validation, "BUILTIN_ADAPTER_CAPABILITIES", table)
    monkeypatch.setattr(validation_module, "BUILTIN_ADAPTER_CAPABILITIES", table)

    report = _validate(config_id, location, destination_schema=DestinationSchemaOptions())

    assert [(finding.code, finding.location) for finding in report.findings] == [
        ("destination-schema-mismatch", "/configuration/schema_mapping/0/name"),
        ("missing-adapter", "/configuration/source"),
    ]
    # The non-schema portion is exactly what the core reports for the same stored bytes.
    core = [finding for finding in report.findings if finding.code == "missing-adapter"]
    assert tuple(core) == collect_findings(package(data))


# --- AR9 at the boundary: a failed read is a finding, never a service refusal -----------


def test_a_failed_schema_read_returns_a_report_rather_than_a_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from infrahub_sync.configuration.capabilities import DestinationSchemaReadError

    def _raising(package_: ConfigurationPackage, branch: str) -> Mapping[str, Any]:
        del package_
        msg = f"schema read failed for branch {branch!r}"
        raise DestinationSchemaReadError(msg, reason="unreachable")

    table = dict(BUILTIN_ADAPTER_CAPABILITIES)
    table["infrahub"] = replace(table["infrahub"], destination_schema_accessor=_raising)
    monkeypatch.setattr(schema_validation, "BUILTIN_ADAPTER_CAPABILITIES", table)
    config_id, location = _registered(tmp_path)

    report = _validate(config_id, location, destination_schema=DestinationSchemaOptions())

    assert [(finding.code, finding.location) for finding in report.findings] == [
        ("destination-schema-read-failed", "/configuration/destination")
    ]
    assert report.destination_schema_fingerprint is None


# --- The opt-in argument itself ---------------------------------------------------------


def test_a_wrong_typed_opt_in_is_a_request_refusal(tmp_path: Path) -> None:
    config_id, location = _registered(tmp_path)

    wrong_typed = cast("DestinationSchemaOptions", True)  # noqa: FBT003 - the wrong type is the fixture
    with pytest.raises(configs_service.ConfigsRequestError) as raised:
        _validate(config_id, location, destination_schema=wrong_typed)

    assert raised.value.family == "request"


def test_the_opt_in_against_a_non_declaring_destination_reports_both_gate_findings(
    tmp_path: Path,
) -> None:
    data = package_data()
    data["configuration"]["destination"]["name"] = "peeringmanager"
    config_id, location = _registered(tmp_path, data)

    report = _validate(config_id, location, destination_schema=DestinationSchemaOptions())

    assert [(finding.code, finding.location) for finding in report.findings] == [
        ("destination-schema-validation-unsupported", "/configuration/destination"),
        ("unsupported-destination-write", "/configuration/destination"),
    ]
    assert report.destination_schema_fingerprint is None
