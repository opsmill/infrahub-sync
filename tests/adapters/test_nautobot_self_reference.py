"""Covers model_loader()'s reconciliation pass for self-referencing scalar fields.

Nautobot's list endpoints return records in whatever order the source API uses (commonly
alphabetical by name), not parent-before-child. A record whose self-referenced peer (e.g.
Location.parent -> Location) sorts *later* in that list finds no match on the first
conversion pass, and without a follow-up the field is left unset permanently -- see
model_loader()'s docstring for why one retry pass is sufficient.
"""

from collections import UserDict
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

if TYPE_CHECKING:
    from infrahub_sync.adapters.nautobot import NautobotAdapter, NautobotModel

# The nautobot adapter hard-imports `pynautobot`, an optional dependency that is
# not part of the `dev` extra. Skip this module when it is unavailable instead
# of erroring during collection.
pytest.importorskip("pynautobot")


class _FakeRecord(UserDict):
    """`dict(MagicMock())` returns {}, so use UserDict to make `dict(node)` work."""


def _make_adapter(mappings: list[dict]) -> "NautobotAdapter":
    """Build a NautobotAdapter with stubbed pynautobot client."""
    from infrahub_sync import SchemaMappingModel, SyncAdapter, SyncConfig
    from infrahub_sync.adapters.nautobot import NautobotAdapter

    schema_mapping = [SchemaMappingModel(**m) for m in mappings]
    config = SyncConfig(
        name="t",
        source=SyncAdapter(name="nautobot"),
        destination=SyncAdapter(name="infrahub"),
        schema_mapping=schema_mapping,
    )
    adapter_settings = SyncAdapter(name="nautobot", settings={"url": "https://example.invalid", "token": "x"})
    NautobotAdapter._create_nautobot_client = lambda _self, _adapter: MagicMock()  # ty: ignore[invalid-assignment]
    return NautobotAdapter(target="test", adapter=adapter_settings, config=config)


def _location_model() -> type["NautobotModel"]:
    from infrahub_sync.adapters.nautobot import NautobotModel

    class Location(NautobotModel):
        _modelname = "Location"
        _identifiers = ("name",)
        _attributes = ("parent",)
        name: str
        parent: str | None = None

    return Location


def test_model_loader_resolves_self_reference_out_of_order() -> None:
    """Child-before-parent source order must not leave `parent` unset."""
    mapping = {
        "name": "Location",
        "mapping": "dcim.locations",
        "identifiers": ["name"],
        "fields": [
            {"name": "name", "mapping": "name"},
            {"name": "parent", "mapping": "parent", "reference": "Location"},
        ],
    }
    adapter = _make_adapter([mapping])
    location_cls = _location_model()

    # Building-02 (child) is returned before Campus-01 (its parent) -- the exact
    # ordering Nautobot's default alphabetical sort produces for this real dataset.
    child = _FakeRecord({"id": 2, "name": "Building-02", "parent": {"id": 1}})
    parent = _FakeRecord({"id": 1, "name": "Campus-01", "parent": None})
    fake_endpoint = MagicMock()
    fake_endpoint.all.return_value = [child, parent]
    adapter.client.dcim.locations = fake_endpoint  # ty: ignore[unresolved-attribute]

    adapter.model_loader("Location", location_cls)

    nodes = {node.name: node for node in adapter.store.get_all(model="Location")}  # ty: ignore[unresolved-attribute]
    assert nodes["Campus-01"].parent is None
    assert nodes["Building-02"].parent == "Campus-01"


def test_model_loader_reconciliation_warns_on_genuinely_missing_peer() -> None:
    """A parent id that never appears in the source data stays unresolved, loudly."""
    mapping = {
        "name": "Location",
        "mapping": "dcim.locations",
        "identifiers": ["name"],
        "fields": [
            {"name": "name", "mapping": "name"},
            {"name": "parent", "mapping": "parent", "reference": "Location"},
        ],
    }
    adapter = _make_adapter([mapping])
    location_cls = _location_model()

    orphan = _FakeRecord({"id": 2, "name": "Orphan-01", "parent": {"id": 999}})
    fake_endpoint = MagicMock()
    fake_endpoint.all.return_value = [orphan]
    adapter.client.dcim.locations = fake_endpoint  # ty: ignore[unresolved-attribute]

    adapter.model_loader("Location", location_cls)

    nodes = {node.name: node for node in adapter.store.get_all(model="Location")}  # ty: ignore[unresolved-attribute]
    assert nodes["Orphan-01"].parent is None


def test_model_loader_leaves_non_self_referencing_fields_alone() -> None:
    """The reconciliation pass only engages when a self-referencing field is configured."""
    mapping = {
        "name": "Location",
        "mapping": "dcim.locations",
        "identifiers": ["name"],
        "fields": [
            {"name": "name", "mapping": "name"},
        ],
    }
    adapter = _make_adapter([mapping])

    from infrahub_sync.adapters.nautobot import NautobotModel

    class LocationNoParent(NautobotModel):
        _modelname = "Location"
        _identifiers = ("name",)
        _attributes = ()
        name: str

    fake_endpoint = MagicMock()
    fake_endpoint.all.return_value = [_FakeRecord({"id": 1, "name": "Campus-01"})]
    adapter.client.dcim.locations = fake_endpoint  # ty: ignore[unresolved-attribute]

    # No self-referencing field is mapped, so there is nothing to reconcile --
    # this must not raise even though `self_ref_fields` is empty.
    adapter.model_loader("Location", LocationNoParent)

    nodes = list(adapter.store.get_all(model="Location"))  # ty: ignore[unresolved-attribute]
    assert nodes[0].name == "Campus-01"
