"""Typed load errors that the failure cause reads (spec 009 T070, research R13).

`apply_transform` raises `TransformError` with the field and the record's source ID, and
`Potenda._load_side` raises `SideLoadError` with the side and the adapter name. Both are
`ValueError` subclasses, so the handlers of ADR 0005 that catch `ValueError` still match them.
"""

from __future__ import annotations

from typing import Any, ClassVar

import pytest
from diffsync import Adapter, DiffSyncModel

from infrahub_sync import (
    DiffSyncMixin,
    DiffSyncModelMixin,
    RecordLoadError,
    SchemaMappingField,
    SchemaMappingModel,
    SchemaMappingTransform,
    SideLoadError,
    SyncAdapter,
    SyncConfig,
    TransformError,
)
from infrahub_sync.potenda import Potenda


class Device(DiffSyncModelMixin, DiffSyncModel):
    _modelname = "Device"
    _identifiers = ("name",)
    _attributes = ("position",)

    name: str
    position: int | None = None


DEVICE_ELEMENT = SchemaMappingModel(
    name="Device",
    mapping="dcim.devices",
    identifiers=["name"],
    fields=[SchemaMappingField(name="name", mapping="name"), SchemaMappingField(name="position", mapping="position")],
)


class _Adapter(DiffSyncMixin, Adapter):
    """Loads its records as model data, or raises the exception a record names."""

    top_level: ClassVar[list[str]] = ["Device"]
    Device = Device

    def __init__(self, target: str, records: list[dict[str, Any]]) -> None:
        super().__init__(name=target)
        self.target = target
        self.config = SyncConfig(
            name="transform-error",
            source=SyncAdapter(name="netbox"),
            destination=SyncAdapter(name="infrahub"),
            schema_mapping=[DEVICE_ELEMENT],
        )
        self._records = records

    def list_records(self, model_name: str, element: SchemaMappingModel) -> list[dict[str, Any]]:  # noqa: ARG002
        return list(self._records)

    def record_to_data(self, record: Any, element: SchemaMappingModel, model: type[DiffSyncModel]) -> dict[str, Any]:  # noqa: ANN401, ARG002, PLR6301
        if "raise" in record:
            raise record["raise"]
        return dict(record)


def _potenda(source: _Adapter, destination: _Adapter, *, config: SyncConfig | None = None) -> Potenda:
    return Potenda(
        source=source,
        destination=destination,
        config=config,  # ty: ignore[invalid-argument-type]
        top_level=["Device"],
        show_progress=False,
    )


def test_apply_transform_raises_a_transform_error_with_the_field_and_the_source_id() -> None:
    item = {"id": 42, "name": "leaf-1"}

    with pytest.raises(TransformError) as caught:
        DiffSyncModelMixin.apply_transform(item=item, transform_expr="{{ name.missing.deeper }}", field="name")

    assert isinstance(caught.value, ValueError)
    assert caught.value.field == "name"
    assert caught.value.source_id == "42"
    assert caught.value.kind is None


def test_apply_transform_prefers_the_local_id_of_the_record() -> None:
    item = {"local_id": "7", "id": 42, "name": "leaf-1"}

    with pytest.raises(TransformError) as caught:
        DiffSyncModelMixin.apply_transform(item=item, transform_expr="{{ name.missing.deeper }}", field="name")

    assert caught.value.source_id == "7"


def test_a_sandbox_refusal_is_a_transform_error_too() -> None:
    item = {"name": "leaf-1"}

    with pytest.raises(TransformError) as caught:
        DiffSyncModelMixin.apply_transform(item=item, transform_expr="{{ name.__class__ }}", field="name")

    assert caught.value.field == "name"
    assert caught.value.source_id is None


def test_transform_records_names_the_kind_of_the_mapping_entry() -> None:
    element = DEVICE_ELEMENT.model_copy(
        update={"transforms": [SchemaMappingTransform(field="name", expression="{{ name.missing.deeper }}")]}
    )

    with pytest.raises(TransformError) as caught:
        DiffSyncModelMixin.transform_records(records=[{"id": 3, "name": "leaf-1"}], schema_mapping=element)

    assert caught.value.kind == "Device"
    assert caught.value.field == "name"
    assert caught.value.source_id == "3"


@pytest.mark.parametrize(
    ("side", "load", "role", "adapter"),
    [
        pytest.param("source", "source_load", "source", "netbox", id="source"),
        pytest.param("destination", "destination_load", "destination", "infrahub", id="destination"),
    ],
)
def test_load_side_raises_a_side_load_error_with_the_side_and_the_adapter(
    side: str, load: str, role: str, adapter: str
) -> None:
    failing = _Adapter(side, [{"name": "leaf-1", "raise": RuntimeError("unexpected")}])
    other = _Adapter("other", [])
    source, destination = (failing, other) if side == "source" else (other, failing)
    ptd = _potenda(source, destination, config=failing.config)

    with pytest.raises(SideLoadError) as caught:
        getattr(ptd, load)()

    assert isinstance(caught.value, ValueError)
    assert caught.value.side == role
    assert caught.value.adapter == adapter
    # The original exception stays the cause: a source record fails as a `RecordLoadError`.
    assert isinstance(caught.value.__cause__, RecordLoadError if side == "source" else RuntimeError)


def test_load_side_without_a_configuration_names_the_adapter_class() -> None:
    ptd = _potenda(_Adapter("source", [{"name": "leaf-1", "raise": RuntimeError("unexpected")}]), _Adapter("d", []))

    with pytest.raises(SideLoadError) as caught:
        ptd.source_load()

    assert caught.value.side == "source"
    assert caught.value.adapter == "_Adapter"


@pytest.mark.parametrize(
    "error",
    [
        pytest.param(TransformError("text", field="name", source_id="1"), id="transform"),
        pytest.param(SideLoadError("text", side="source", adapter="netbox"), id="side-load"),
    ],
)
def test_a_value_error_handler_still_matches_both(error: ValueError) -> None:
    def _raise() -> None:
        raise error

    with pytest.raises(ValueError, match="text"):
        _raise()
