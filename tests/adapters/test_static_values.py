"""Static values in the NetBox and Nautobot source adapters.

A schema-mapping field declaring `static` assigns that literal value and ignores the
source object. `None` means "no static value"; every other value, falsy included, is a
value. The models below copy the shapes `examples/netbox_to_infrahub/netbox/sync_models.py`
generates, so the fields under test have real declared types and defaults.
"""

from __future__ import annotations

import importlib
from types import SimpleNamespace
from typing import Any

import pytest

from infrahub_sync import SchemaMappingField, SchemaMappingModel

pynetbox = pytest.importorskip("pynetbox")
pynautobot = pytest.importorskip("pynautobot")

from pynautobot.core.response import Record as NautobotRecord  # noqa: E402
from pynetbox.core.response import Record as NetboxRecord  # noqa: E402

from infrahub_sync.adapters.nautobot import NautobotAdapter, NautobotModel  # noqa: E402
from infrahub_sync.adapters.netbox import NetboxAdapter, NetboxModel  # noqa: E402

ADAPTERS = ("netbox", "nautobot")


class _NetboxGeneratedBase(NetboxModel):
    # The generated base adds only `local_data`; `local_id` is already a field on the mixin.
    local_data: Any | None = None


class _NautobotGeneratedBase(NautobotModel):
    local_data: Any | None = None


class NetboxDcimDeviceType(_NetboxGeneratedBase):
    _modelname = "DcimDeviceType"
    _identifiers = ("name", "manufacturer")
    _attributes = ("full_depth", "height", "part_number", "weight")
    full_depth: bool | None = True
    height: int | None = 1
    name: str
    part_number: str | None = None
    weight: int | None = None
    manufacturer: str


class NetboxOrganizationRIR(_NetboxGeneratedBase):
    _modelname = "OrganizationRIR"
    _identifiers = ("name",)
    _attributes = ("description", "is_private", "tags")
    description: str | None = None
    is_private: bool | None = False
    name: str
    tags: list[str] | None = []  # noqa: RUF012 - `is_list` reads this default


class NautobotDcimDeviceType(_NautobotGeneratedBase):
    _modelname = "DcimDeviceType"
    _identifiers = ("name", "manufacturer")
    _attributes = ("full_depth", "height", "part_number", "weight")
    full_depth: bool | None = True
    height: int | None = 1
    name: str
    part_number: str | None = None
    weight: int | None = None
    manufacturer: str


class NautobotOrganizationRIR(_NautobotGeneratedBase):
    _modelname = "OrganizationRIR"
    _identifiers = ("name",)
    _attributes = ("description", "is_private", "tags")
    description: str | None = None
    is_private: bool | None = False
    name: str
    tags: list[str] | None = []  # noqa: RUF012 - `is_list` reads this default


DEVICE_TYPE_MODELS = {"netbox": NetboxDcimDeviceType, "nautobot": NautobotDcimDeviceType}
RIR_MODELS = {"netbox": NetboxOrganizationRIR, "nautobot": NautobotOrganizationRIR}
MODELS_BY_KEY = {"device_type": DEVICE_TYPE_MODELS, "rir": RIR_MODELS}

DEVICE_TYPE_PAYLOADS = {
    "netbox": {"id": 1, "name": "MX204", "manufacturer": "juniper", "is_full_depth": True},
    "nautobot": {
        "id": "aaaaaaaa-0000-0000-0000-000000000001",
        "name": "MX204",
        "manufacturer": "juniper",
        "is_full_depth": True,
    },
}
RIR_PAYLOADS = {
    "netbox": {
        "id": 2,
        "name": "RIPE",
        "description": "from source",
        "tags": [{"id": 20, "name": "edge"}, {"id": 21, "name": "prod"}],
    },
    "nautobot": {
        "id": "aaaaaaaa-0000-0000-0000-000000000002",
        "name": "RIPE",
        "description": "from source",
        "tags": [
            {"id": "cccccccc-0000-0000-0000-000000000020", "name": "edge"},
            {"id": "cccccccc-0000-0000-0000-000000000021", "name": "prod"},
        ],
    },
}
PAYLOADS_BY_KEY = {"device_type": DEVICE_TYPE_PAYLOADS, "rir": RIR_PAYLOADS}


class _Peer:
    """A stored peer, exposing the two members the reference branch reads."""

    def __init__(self, local_id: str) -> None:
        self.local_id = local_id

    def get_unique_id(self) -> str:
        return f"peer-{self.local_id}"


class _Store:
    """The minimal store surface the reference branch calls."""

    def __init__(self, peers: list[_Peer]) -> None:
        self._peers = peers

    def get_all(self, model: str) -> list[_Peer]:
        assert model == "BuiltinTag"
        return list(self._peers)

    @staticmethod
    def get_all_model_names() -> list[str]:
        return ["BuiltinTag"]


def _source_object(adapter: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Build a real SDK `Record` and hand back the dict the loaders pass to the converter.

    `netbox.py` and `nautobot.py` both load with `dict(node)`, and `Record` has no
    `.get`, so the converter only ever sees the dict form.
    """
    record = NetboxRecord(payload, None, None) if adapter == "netbox" else NautobotRecord(payload, None, None)
    return dict(record)


def _convert(
    adapter: str,
    obj: dict[str, Any],
    fields: list[SchemaMappingField],
    model: type[Any],
    store: _Store | None = None,
) -> dict[str, Any]:
    """Run one adapter's `*_obj_to_diffsync` offline, with no provider client."""
    mapping = SchemaMappingModel(name=model._modelname, fields=fields)
    holder = SimpleNamespace(store=store)
    if adapter == "netbox":
        return NetboxAdapter.netbox_obj_to_diffsync(holder, obj, mapping, model)
    return NautobotAdapter.nautobot_obj_to_diffsync(holder, obj, mapping, model)


def _assert_exact(data: dict[str, Any], name: str, expected: object) -> None:
    """Assert `name` is a key of `data` carrying exactly `expected`, value and type.

    The type check is what separates `False` from `0` and `True` from `1`, which compare
    equal in Python.
    """
    assert name in data
    assert type(data[name]) is type(expected)
    assert data[name] == expected


FALSY_STATICS = (
    pytest.param("device_type", "full_depth", False, id="bool-false"),
    pytest.param("device_type", "height", 0, id="int-zero"),
    pytest.param("rir", "description", "", id="str-empty"),
    pytest.param("rir", "tags", [], id="list-empty"),
)

TRUTHY_STATICS = (
    pytest.param("device_type", "full_depth", True, id="bool-true"),
    pytest.param("device_type", "height", 1, id="int-one"),
    pytest.param("rir", "description", "keep", id="str-keep"),
)


def test_netbox_adapter_module_binds_the_real_sdk() -> None:
    """The cached adapter module still holds the real SDK, not an earlier test's stub.

    Resolved through `sys.modules` at run time, which is how `PluginLoader` loads
    adapters in production, so a module left bound to a stub is visible here.
    """
    module = importlib.import_module("infrahub_sync.adapters.netbox")

    assert module.pynetbox is pynetbox
    assert hasattr(module.pynetbox, "__file__")


@pytest.mark.parametrize("adapter", ADAPTERS)
@pytest.mark.parametrize(("model_key", "field_name", "value"), FALSY_STATICS)
def test_falsy_static_is_emitted(adapter: str, model_key: str, field_name: str, value: object) -> None:
    """A declared falsy static emits its field, with the declared value and type."""
    model = MODELS_BY_KEY[model_key][adapter]
    obj = _source_object(adapter, PAYLOADS_BY_KEY[model_key][adapter])

    data = _convert(adapter, obj, [SchemaMappingField(name=field_name, static=value)], model)

    _assert_exact(data, field_name, value)


@pytest.mark.parametrize("adapter", ADAPTERS)
def test_falsy_bool_static_survives_into_the_model(adapter: str) -> None:
    """A `false` static reaches the plan: the constructed model carries `False`."""
    model = DEVICE_TYPE_MODELS[adapter]
    obj = _source_object(adapter, DEVICE_TYPE_PAYLOADS[adapter])
    fields = [
        SchemaMappingField(name="name", mapping="name"),
        SchemaMappingField(name="manufacturer", mapping="manufacturer"),
        SchemaMappingField(name="full_depth", static=False),
    ]

    data = _convert(adapter, obj, fields, model)
    item = model(**data)

    assert item.full_depth is False
    assert item.get_attrs()["full_depth"] is False


@pytest.mark.parametrize("adapter", ADAPTERS)
def test_empty_list_static_survives_into_the_model(adapter: str) -> None:
    """An `[]` static reaches the plan as a value the operator set, not as the field default."""
    model = RIR_MODELS[adapter]
    obj = _source_object(adapter, RIR_PAYLOADS[adapter])
    fields = [
        SchemaMappingField(name="name", mapping="name"),
        SchemaMappingField(name="tags", static=[]),
    ]

    data = _convert(adapter, obj, fields, model)
    item = model(**data)

    assert item.tags == []
    # `tags` already defaults to `[]`, so only an explicitly set field discriminates.
    assert "tags" in item.model_fields_set
    assert item.get_attrs()["tags"] == []


@pytest.mark.parametrize("adapter", ADAPTERS)
@pytest.mark.parametrize(("model_key", "field_name", "value"), TRUTHY_STATICS)
def test_truthy_static_is_unchanged(adapter: str, model_key: str, field_name: str, value: object) -> None:
    """Truthy statics keep emitting exactly what they did before."""
    model = MODELS_BY_KEY[model_key][adapter]
    obj = _source_object(adapter, PAYLOADS_BY_KEY[model_key][adapter])

    data = _convert(adapter, obj, [SchemaMappingField(name=field_name, static=value)], model)

    _assert_exact(data, field_name, value)


@pytest.mark.parametrize("adapter", ADAPTERS)
def test_explicit_null_static_falls_through_to_the_mapping(adapter: str) -> None:
    """`static: null` behaves exactly like an absent `static`: the mapping resolves."""
    model = RIR_MODELS[adapter]
    obj = _source_object(adapter, RIR_PAYLOADS[adapter])

    declared = _convert(
        adapter, obj, [SchemaMappingField(name="description", mapping="description", static=None)], model
    )
    absent = _convert(adapter, obj, [SchemaMappingField(name="description", mapping="description")], model)

    assert declared["description"] == "from source"
    assert declared == absent


@pytest.mark.parametrize("adapter", ADAPTERS)
def test_static_takes_precedence_over_mapping(adapter: str) -> None:
    """A falsy static wins over a mapping, as the static-first chain already does for truthy statics."""
    model = DEVICE_TYPE_MODELS[adapter]
    obj = _source_object(adapter, DEVICE_TYPE_PAYLOADS[adapter])
    assert obj["is_full_depth"] is True

    data = _convert(
        adapter,
        obj,
        [SchemaMappingField(name="full_depth", static=False, mapping="is_full_depth")],
        model,
    )

    assert data["full_depth"] is False


@pytest.mark.parametrize("adapter", ADAPTERS)
def test_scalar_mapping_is_unchanged(adapter: str) -> None:
    """A mapping-only scalar field still resolves from the source object."""
    model = RIR_MODELS[adapter]
    obj = _source_object(adapter, RIR_PAYLOADS[adapter])

    data = _convert(adapter, obj, [SchemaMappingField(name="description", mapping="description")], model)

    assert data["description"] == "from source"


@pytest.mark.parametrize("adapter", ADAPTERS)
def test_list_reference_is_unchanged(adapter: str) -> None:
    """A list `reference` field still resolves its peers from the store."""
    model = RIR_MODELS[adapter]
    obj = _source_object(adapter, RIR_PAYLOADS[adapter])
    peers = [_Peer(str(tag["id"])) for tag in RIR_PAYLOADS[adapter]["tags"]]

    data = _convert(
        adapter,
        obj,
        [SchemaMappingField(name="tags", mapping="tags", reference="BuiltinTag")],
        model,
        store=_Store(peers),
    )

    assert data["tags"] == sorted(peer.get_unique_id() for peer in peers)
