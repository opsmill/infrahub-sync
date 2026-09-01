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
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import pytest

from infrahub_sync.configuration import schema_validation
from infrahub_sync.configuration import validation as validation_module
from infrahub_sync.configuration.capabilities import BUILTIN_ADAPTER_CAPABILITIES
from infrahub_sync.configuration.schema_validation import DestinationSchemaOptions
from infrahub_sync.configuration.validation import collect_findings
from infrahub_sync.product_store import configs as configs_service
from infrahub_sync.product_store import local_product_projection
from infrahub_sync.runtime_schema import compute_consumed_schema_fingerprint, normalize_destination_schema
from tests.configuration.validation_packages import package, package_data

if TYPE_CHECKING:
    from collections.abc import ItemsView, Iterator

    from infrahub_sync.configuration import ConfigurationPackage
    from infrahub_sync.product_store import ProductProjection

_SNAPSHOT: dict[str, Any] = {
    "InfraDevice": {
        "human_friendly_id": ["name__value"],
        "uniqueness_constraints": [["name__value"]],
        "attributes": {"name": {"kind": "Text", "optional": False, "default_value": None, "unique": True}},
        "relationships": {
            "site": {"peer": "LocationSite", "cardinality": "one", "optional": True, "kind": "Attribute"}
        },
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


def _registered(tmp_path: Path, data: dict[str, Any] | None = None) -> tuple[str, ProductProjection]:
    root = tmp_path / "product-cache"
    root.mkdir()
    projection = local_product_projection(root)
    registered = configs_service.register(
        package=package_data() if data is None else data,
        projection=projection,
    )
    return registered.version.config_id, projection


def _validate(
    config_id: str,
    projection: ProductProjection,
    destination_schema: DestinationSchemaOptions | None = None,
) -> configs_service.ValidationReport:
    return configs_service.validate(
        config_id=config_id,
        registry_version=1,
        projection=projection,
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
    config_id, projection = _registered(tmp_path)

    report = _validate(config_id, projection)

    assert accessor.calls == []
    assert report.destination_schema_fingerprint is None
    assert report.findings == ()


def test_a_default_validate_performs_no_network_io(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_id, projection = _registered(tmp_path)

    def _refuse(*args: object, **kwargs: object) -> object:
        del args, kwargs
        msg = "the default validate path opened a network socket"
        raise AssertionError(msg)

    monkeypatch.setattr(socket, "socket", _refuse)

    report = _validate(config_id, projection)

    assert report.findings == ()


def test_the_opt_in_performs_no_network_io_when_the_snapshot_is_injected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    accessor = _inject_accessor(monkeypatch)
    config_id, projection = _registered(tmp_path)

    def _refuse(*args: object, **kwargs: object) -> object:
        del args, kwargs
        msg = "an injected schema read opened a network socket"
        raise AssertionError(msg)

    monkeypatch.setattr(socket, "socket", _refuse)

    report = _validate(config_id, projection, destination_schema=DestinationSchemaOptions())

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
    config_id, projection = _registered(tmp_path)

    report = _validate(config_id, projection, destination_schema=DestinationSchemaOptions())

    assert report.destination_schema_fingerprint == compute_consumed_schema_fingerprint(
        configuration=package(package_data()).configuration, snapshot=normalize_destination_schema(_SNAPSHOT)
    )


def test_the_opt_in_report_is_identical_across_invocations(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _inject_accessor(monkeypatch)
    config_id, projection = _registered(tmp_path, _mapping_package_data("NopeKind"))

    first = _validate(config_id, projection, destination_schema=DestinationSchemaOptions())
    second = _validate(config_id, projection, destination_schema=DestinationSchemaOptions())

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
    config_id, projection = _registered(tmp_path, data)
    monkeypatch.setattr(schema_validation, "BUILTIN_ADAPTER_CAPABILITIES", table)
    monkeypatch.setattr(validation_module, "BUILTIN_ADAPTER_CAPABILITIES", table)

    report = _validate(config_id, projection, destination_schema=DestinationSchemaOptions())

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
    config_id, projection = _registered(tmp_path)

    report = _validate(config_id, projection, destination_schema=DestinationSchemaOptions())

    assert [(finding.code, finding.location) for finding in report.findings] == [
        ("destination-schema-read-failed", "/configuration/destination")
    ]
    assert report.destination_schema_fingerprint is None


# --- The opt-in argument itself ---------------------------------------------------------


def test_a_wrong_typed_opt_in_is_a_request_refusal(tmp_path: Path) -> None:
    config_id, projection = _registered(tmp_path)

    wrong_typed = cast("DestinationSchemaOptions", True)  # noqa: FBT003 - the wrong type is the fixture
    with pytest.raises(configs_service.ConfigsRequestError) as raised:
        _validate(config_id, projection, destination_schema=wrong_typed)

    assert raised.value.family == "request"


def test_the_opt_in_against_a_non_declaring_destination_reports_both_gate_findings(
    tmp_path: Path,
) -> None:
    data = package_data()
    data["configuration"]["destination"]["name"] = "peeringmanager"
    config_id, projection = _registered(tmp_path, data)

    report = _validate(config_id, projection, destination_schema=DestinationSchemaOptions())

    assert [(finding.code, finding.location) for finding in report.findings] == [
        ("destination-schema-validation-unsupported", "/configuration/destination"),
        ("unsupported-destination-write", "/configuration/destination"),
    ]
    assert report.destination_schema_fingerprint is None


# --- Reduction round: normalization reads nothing from a hostile schema response --------


class _ReturningSchemaEndpoint:
    def __init__(self, response: object) -> None:
        self._response = response

    def all(self, branch: str) -> object:
        del branch
        return self._response


class _ReturningClient:
    def __init__(self, response: object) -> None:
        self.schema = _ReturningSchemaEndpoint(response)


def _mock_live_schema_read(monkeypatch: pytest.MonkeyPatch, response: object) -> None:
    """Route the real bundled accessor's client call to a canned hostile response.

    No table injection: the built-in ``infrahub`` declaration already carries the real
    accessor, so the whole normalization boundary runs under public ``validate()``.
    """
    monkeypatch.setenv("INFRAHUB_API_TOKEN", "test-token")

    def _fake_client(address: str, config: object) -> _ReturningClient:
        del address, config
        return _ReturningClient(response)

    monkeypatch.setattr("infrahub_sdk.InfrahubClientSync", _fake_client)


def test_a_metaclass_raising_response_exception_lands_as_a_rejected_finding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Inspection is invocation, one level up: the normalization handler formatted
    # type(exc).__name__, and a metaclass executes on that read — the probe escaped
    # public validate() as ConfigsInternalError with the read recorded. The boundary
    # now reads nothing from the exception: one fixed message, reason "rejected".
    reads: list[str] = []

    class _ExecutingMeta(type):
        @property
        def __name__(cls) -> str:  # noqa: PLW3201 - shadowing type's own descriptor is the fixture
            reads.append("__name__")
            msg = "metaclass executed on __name__ read"
            raise RuntimeError(msg)

    class _HostileError(Exception, metaclass=_ExecutingMeta):
        """An ordinary exception whose class name read is executable behavior."""

    class _RaisingItems(Mapping):
        def __getitem__(self, key: str) -> object:
            raise KeyError(key)

        def __iter__(self) -> Iterator[str]:
            return iter(())

        def __len__(self) -> int:
            return 0

        def items(self) -> ItemsView[str, object]:  # noqa: PLR6301 - protocol hook, self unused by design
            msg = "items() exploded: third-party secret text"
            raise _HostileError(msg)

    _mock_live_schema_read(monkeypatch, _RaisingItems())
    config_id, projection = _registered(tmp_path)

    report = _validate(config_id, projection, destination_schema=DestinationSchemaOptions())

    assert [(finding.code, finding.location) for finding in report.findings] == [
        ("destination-schema-read-failed", "/configuration/destination")
    ]
    assert "rejected" in report.findings[0].message
    assert "third-party secret text" not in report.findings[0].message
    assert reads == []


def test_a_response_cannot_forge_its_own_schema_read_reason(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # A DestinationSchemaReadError raised by the response's own property used to pass
    # through normalization untouched, so a hostile response forged its own reason
    # ("unauthorized") into the finding an operator reads. Normalization now rewraps
    # every exception into one new fixed error with reason "rejected".
    from infrahub_sync.configuration.capabilities import DestinationSchemaReadError

    class _ForgingNode:
        relationships: tuple[object, ...] = ()

        @property
        def attributes(self) -> tuple[object, ...]:
            msg = "destination refused the schema read credentials: forged"
            raise DestinationSchemaReadError(msg, reason="unauthorized")

    _mock_live_schema_read(monkeypatch, {"InfraDevice": _ForgingNode()})
    config_id, projection = _registered(tmp_path)

    report = _validate(config_id, projection, destination_schema=DestinationSchemaOptions())

    assert [(finding.code, finding.location) for finding in report.findings] == [
        ("destination-schema-read-failed", "/configuration/destination")
    ]
    message = report.findings[0].message
    assert "rejected" in message
    assert "unauthorized" not in message
    assert "forged" not in message
