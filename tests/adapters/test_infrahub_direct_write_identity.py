"""Destination identity checks on DiffSync's direct create path."""

from __future__ import annotations

import pytest
from diffsync import Adapter

from infrahub_sync.adapters.infrahub import InfrahubModel
from infrahub_sync.plan.errors import UnkeyedCreateRefusedError
from tests.adapters.test_infrahub_planned_write import (
    DEVICE_KIND,
    SCHEMAS,
    SITE_KIND,
    RecordingClient,
    make_adapter,
    make_node,
)


class TestDevice(InfrahubModel):
    """A DiffSync device whose peer identifier is a unique-id string."""

    __test__ = False

    _modelname = DEVICE_KIND
    _identifiers = ("name", "site")
    _attributes = ()

    name: str
    site: str


def _adapter() -> tuple[RecordingClient, Adapter]:
    client = RecordingClient()
    adapter = make_adapter(client)
    Adapter.__init__(adapter)  # noqa: PLC2801 - The adapter constructor would open a network client.
    adapter.schema = dict(SCHEMAS)
    client.store.set(key="site-a", node=make_node(client, SITE_KIND, "site-id-1"))
    return client, adapter


def test_direct_create_accepts_a_resolved_relationship_crossing_key() -> None:
    client, adapter = _adapter()

    TestDevice.create(adapter=adapter, ids={"name": "device-a", "site": "site-a"}, attrs={})

    assert client.mutation_names == [f"{DEVICE_KIND}Upsert"]


def test_direct_create_refuses_an_empty_resolved_key_before_mutation() -> None:
    client, adapter = _adapter()

    with pytest.raises(UnkeyedCreateRefusedError, match="name__value"):
        TestDevice.create(adapter=adapter, ids={"name": " ", "site": "site-a"}, attrs={})

    assert client.mutation_names == []
