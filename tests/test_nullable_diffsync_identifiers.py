"""Regression coverage for nullable DiffSync identifiers."""

from __future__ import annotations

import logging
from typing import ClassVar

import pytest
from diffsync import Adapter, DiffSyncModel
from infrahub_sdk.schema import AttributeSchema, NodeSchema
from infrahub_sdk.schema.main import AttributeKind

from infrahub_sync import (
    DiffSyncModelMixin,
    SchemaMappingField,
    SchemaMappingModel,
    SyncAdapter,
    SyncConfig,
)
from infrahub_sync.generator import get_identifiers


class _AutoIdentifierModel(DiffSyncModelMixin, DiffSyncModel):
    """Shape emitted when the generator auto-selects optional ``serial``."""

    _modelname = "InfraDevice"
    _identifiers = ("serial",)
    _attributes = ("role",)

    serial: str | None = None
    role: str = "leaf"


class _ExplicitIdentifierModel(DiffSyncModelMixin, DiffSyncModel):
    """Shape emitted when configuration explicitly selects ``serial``."""

    _modelname = "InfraAsset"
    _identifiers = ("serial",)
    _attributes = ()

    serial: str | None = None


class _DeviceAdapter(Adapter):
    InfraDevice = _AutoIdentifierModel
    top_level: ClassVar[list[str]] = ["InfraDevice"]


def _generator_inputs(
    *, optional: bool, configured_identifiers: list[str] | None = None
) -> tuple[NodeSchema, SyncConfig]:
    node = NodeSchema(
        name="Device",
        namespace="Infra",
        attributes=[
            AttributeSchema(name="serial", kind=AttributeKind.TEXT, unique=True, optional=optional),
        ],
    )
    config = SyncConfig(
        name="nullable-identifier-test",
        source=SyncAdapter(name="netbox"),
        destination=SyncAdapter(name="infrahub"),
        schema_mapping=[
            SchemaMappingModel(
                name=node.kind,
                identifiers=configured_identifiers,
                fields=[SchemaMappingField(name="serial")],
            )
        ],
    )
    return node, config


def test_two_missing_auto_selected_identifiers_raise_instead_of_colliding() -> None:
    adapter = _DeviceAdapter(name="source")

    for record in (_AutoIdentifierModel(), _AutoIdentifierModel()):
        with pytest.raises(ValueError, match="InfraDevice identifier field 'serial' cannot be None"):
            adapter.add(record)

    assert not list(adapter.get_all("InfraDevice"))


def test_present_identifier_loads_and_diffs_normally() -> None:
    source = _DeviceAdapter(name="source")
    destination = _DeviceAdapter(name="destination")
    source.add(_AutoIdentifierModel(serial="SER-001"))
    destination.add(_AutoIdentifierModel(serial="SER-001"))

    assert not source.diff_to(destination).has_diffs()


def test_missing_explicitly_configured_identifier_raises() -> None:
    node, config = _generator_inputs(optional=True, configured_identifiers=["serial"])
    record = _ExplicitIdentifierModel()

    assert get_identifiers(node=node, config=config) == list(record._identifiers)
    with pytest.raises(ValueError, match="InfraAsset identifier field 'serial' cannot be None"):
        record.get_unique_id()


@pytest.mark.parametrize(
    ("optional", "configured_identifiers", "expects_warning"),
    [
        pytest.param(True, None, True, id="optional-auto-selected"),
        pytest.param(False, None, False, id="required-auto-selected"),
        pytest.param(True, ["serial"], False, id="optional-explicitly-configured"),
    ],
)
def test_generator_warns_only_for_optional_auto_selected_identifiers(
    caplog: pytest.LogCaptureFixture,
    optional: bool,
    configured_identifiers: list[str] | None,
    expects_warning: bool,
) -> None:
    node, config = _generator_inputs(optional=optional, configured_identifiers=configured_identifiers)

    with caplog.at_level(logging.WARNING, logger="infrahub_sync.generator"):
        assert get_identifiers(node=node, config=config) == ["serial"]

    warnings = [record.getMessage() for record in caplog.records]
    assert bool(warnings) is expects_warning
    if expects_warning:
        assert warnings == [
            "Auto-selected optional attribute(s) ['serial'] as identifiers for InfraDevice; "
            "configure identifiers explicitly for this node"
        ]
