"""IP Fabric and Slurpit reference conversion without their optional SDKs installed.

Both adapters import their provider SDK unconditionally at module level, and neither
`ipfabric` nor `slurpit` is installable in any repo profile. Each import is stubbed
with a bare `types.ModuleType` injected into `sys.modules`, and the cached adapter
module is dropped first, exactly as `tests/runtime_schema/test_registered_execution.py`
does for `pynetbox`.
"""

from __future__ import annotations

import sys
import types
from collections.abc import Iterator
from typing import Any, cast

import pytest
from diffsync.store.local import LocalStore

import infrahub_sync.adapters as adapters_package
from infrahub_sync import SchemaMappingField, SchemaMappingModel


def _reload_submodule(name: str) -> Iterator[types.ModuleType]:
    """Import `infrahub_sync.adapters.<name>` fresh, then restore both prior states.

    A plain `import infrahub_sync.adapters.<name>` sets `<name>` as an attribute on the
    already-imported `infrahub_sync.adapters` package, in addition to the `sys.modules`
    entry. Popping only `sys.modules` at teardown leaves that stub-backed attribute in
    place, so a later `from infrahub_sync.adapters import <name>` returns it without
    re-importing. Both must be recorded and restored.
    """
    full_name = f"infrahub_sync.adapters.{name}"
    had_module = full_name in sys.modules
    prior_module = sys.modules.get(full_name)
    had_attr = hasattr(adapters_package, name)
    prior_attr = getattr(adapters_package, name, None)

    sys.modules.pop(full_name, None)

    yield __import__(full_name, fromlist=["_"])

    sys.modules.pop(full_name, None)
    if had_module and prior_module is not None:
        sys.modules[full_name] = prior_module
    if had_attr:
        setattr(adapters_package, name, prior_attr)
    else:
        adapters_package.__dict__.pop(name, None)


@pytest.fixture
def ipfabricsync_module(monkeypatch: pytest.MonkeyPatch) -> Iterator[types.ModuleType]:
    """Import `infrahub_sync.adapters.ipfabricsync` against a bare `ipfabric` stub."""
    stub = cast("Any", types.ModuleType("ipfabric"))
    stub.IPFClient = object  # only the imported symbol; nothing else is simulated
    monkeypatch.setitem(sys.modules, "ipfabric", stub)
    yield from _reload_submodule("ipfabricsync")


@pytest.fixture
def slurpitsync_module(monkeypatch: pytest.MonkeyPatch) -> Iterator[types.ModuleType]:
    """Import `infrahub_sync.adapters.slurpitsync` against a bare `slurpit` stub."""
    stub = types.ModuleType("slurpit")
    monkeypatch.setitem(sys.modules, "slurpit", stub)
    yield from _reload_submodule("slurpitsync")


def _holder(*, peer_model: type[Any], peers: dict[str, str], identifier_mapping: str):
    """Build a small holder carrying `store` and `config`, following the ACI reference-conversion test style.

    `identifier_mapping` is the source-object key that the peer model's `name` identifier
    resolves to, matching how `build_mapping` reads identifiers directly off the caller's
    top-level source object.
    """
    holder = types.SimpleNamespace()
    setattr(holder, peer_model._modelname, peer_model)
    holder.store = LocalStore(adapter=holder)
    for local_id, name in peers.items():
        peer = peer_model(name=name)
        peer.local_id = local_id
        holder.store.add(obj=peer)
    holder.config = types.SimpleNamespace(
        schema_mapping=[
            SchemaMappingModel(
                name=peer_model._modelname,
                fields=[SchemaMappingField(name="name", mapping=identifier_mapping)],
            ),
        ]
    )
    return holder


# --- IP Fabric ---------------------------------------------------------------------


def _ipf_peer_model(module):
    """Build a minimal IP Fabric peer model referenced by `_ipf_record_model`."""

    class IpfPeer(module.IpfabricsyncModel):
        _modelname = "IpfPeer"
        _identifiers = ("name",)
        name: str

    return IpfPeer


def _ipf_record_model(module):
    """Build a minimal IP Fabric record model with scalar and list peer references."""

    class IpfRecord(module.IpfabricsyncModel):
        _modelname = "IpfRecord"
        _identifiers = ("name",)
        _attributes = ("peer",)
        name: str
        peer: str | None = None
        peers: list[str] | None = []  # noqa: RUF012 - `is_list` reads this default

    return IpfRecord


IPF_SCALAR_FIELD = SchemaMappingField(name="peer", mapping="peer_id", reference="IpfPeer")
IPF_LIST_FIELD = SchemaMappingField(name="peers", mapping="peer_ids", reference="IpfPeer")


def test_ipfabric_peer_found_resolves_to_unique_id(ipfabricsync_module) -> None:
    """A scalar reference resolves to the matching peer's unique id."""
    peer_model = _ipf_peer_model(ipfabricsync_module)
    record_model = _ipf_record_model(ipfabricsync_module)
    holder = _holder(peer_model=peer_model, peers={"peer-1": "zulu"}, identifier_mapping="peer_id")
    mapping = SchemaMappingModel(name=record_model._modelname, fields=[IPF_SCALAR_FIELD])

    data = ipfabricsync_module.IpfabricsyncAdapter.ipfabric_dict_to_diffsync(
        holder, obj={"id": "record-1", "peer_id": "zulu"}, mapping=mapping, model=record_model
    )

    assert data["peer"] == "zulu"


def test_ipfabric_peer_missing_resolves_to_none(ipfabricsync_module) -> None:
    """IP Fabric's policy for an unresolved scalar reference is `None`, not an error."""
    peer_model = _ipf_peer_model(ipfabricsync_module)
    record_model = _ipf_record_model(ipfabricsync_module)
    holder = _holder(peer_model=peer_model, peers={}, identifier_mapping="peer_id")
    mapping = SchemaMappingModel(name=record_model._modelname, fields=[IPF_SCALAR_FIELD])

    data = ipfabricsync_module.IpfabricsyncAdapter.ipfabric_dict_to_diffsync(
        holder, obj={"id": "record-1", "peer_id": "missing"}, mapping=mapping, model=record_model
    )

    assert data["peer"] is None


def test_ipfabric_list_valued_reference_field_is_left_unset(ipfabricsync_module) -> None:
    """A list-valued reference field is not populated; only the scalar branch is implemented."""
    peer_model = _ipf_peer_model(ipfabricsync_module)
    record_model = _ipf_record_model(ipfabricsync_module)
    holder = _holder(peer_model=peer_model, peers={"peer-1": "zulu", "peer-2": "alpha"}, identifier_mapping="peer_ids")
    mapping = SchemaMappingModel(name=record_model._modelname, fields=[IPF_LIST_FIELD])

    data = ipfabricsync_module.IpfabricsyncAdapter.ipfabric_dict_to_diffsync(
        holder, obj={"id": "record-1", "peer_ids": ["zulu", "alpha"]}, mapping=mapping, model=record_model
    )

    assert "peers" not in data


# --- Slurpit -------------------------------------------------------------------------


def _slurpit_holder(*, peer_model: type[Any], peers: dict[str, str], identifier_mapping: str):
    """Build a `_holder` with the `skipped` list Slurpit's reference conversion appends to."""
    holder = _holder(peer_model=peer_model, peers=peers, identifier_mapping=identifier_mapping)
    holder.skipped = []
    return holder


def _slurpit_peer_model(module):
    """Build a minimal Slurpit peer model referenced by `_slurpit_record_model`."""

    class SlurpitPeer(module.SlurpitsyncModel):
        _modelname = "SlurpitPeer"
        _identifiers = ("name",)
        name: str

    return SlurpitPeer


def _slurpit_record_model(module):
    """Build a minimal Slurpit record model with scalar and list peer references."""

    class SlurpitRecord(module.SlurpitsyncModel):
        _modelname = "SlurpitRecord"
        _identifiers = ("name",)
        _attributes = ("peer",)
        name: str
        peer: str | None = None
        peers: list[str] | None = []  # noqa: RUF012 - `is_list` reads this default

    return SlurpitRecord


SLURPIT_SCALAR_FIELD = SchemaMappingField(name="peer", mapping="peer_id", reference="SlurpitPeer")
SLURPIT_LIST_FIELD = SchemaMappingField(name="peers", mapping="peer_ids", reference="SlurpitPeer")


def test_slurpit_peer_found_resolves_to_unique_id(slurpitsync_module) -> None:
    """A scalar reference resolves to the matching peer's unique id."""
    peer_model = _slurpit_peer_model(slurpitsync_module)
    record_model = _slurpit_record_model(slurpitsync_module)
    holder = _slurpit_holder(peer_model=peer_model, peers={"peer-1": "zulu"}, identifier_mapping="peer_id")
    mapping = SchemaMappingModel(name=record_model._modelname, fields=[SLURPIT_SCALAR_FIELD])

    data = slurpitsync_module.SlurpitsyncAdapter.slurpit_obj_to_diffsync(
        holder, obj={"id": "record-1", "peer_id": "zulu"}, mapping=mapping, model=record_model
    )

    assert data["peer"] == "zulu"
    assert holder.skipped == []


def test_slurpit_peer_missing_is_skipped_and_returns_none(slurpitsync_module) -> None:
    """Slurpit's policy for an unresolved scalar reference is to append to `skipped` and return `None`."""
    peer_model = _slurpit_peer_model(slurpitsync_module)
    record_model = _slurpit_record_model(slurpitsync_module)
    holder = _slurpit_holder(peer_model=peer_model, peers={}, identifier_mapping="peer_id")
    mapping = SchemaMappingModel(name=record_model._modelname, fields=[SLURPIT_SCALAR_FIELD])

    data = slurpitsync_module.SlurpitsyncAdapter.slurpit_obj_to_diffsync(
        holder, obj={"id": "record-1", "peer_id": "missing"}, mapping=mapping, model=record_model
    )

    assert data is None
    assert holder.skipped == ["missing"]


def test_slurpit_list_valued_reference_resolves_matching_peer(slurpitsync_module) -> None:
    """List conversion resolves a matching peer via `build_mapping` and appends its unique id."""
    peer_model = _slurpit_peer_model(slurpitsync_module)
    record_model = _slurpit_record_model(slurpitsync_module)
    holder = _slurpit_holder(
        peer_model=peer_model, peers={"peer-1": "zulu", "peer-2": "alpha"}, identifier_mapping="peer_ids"
    )
    mapping = SchemaMappingModel(name=record_model._modelname, fields=[SLURPIT_LIST_FIELD])

    data = slurpitsync_module.SlurpitsyncAdapter.slurpit_obj_to_diffsync(
        holder, obj={"id": "record-1", "peer_ids": "alpha"}, mapping=mapping, model=record_model
    )

    assert data["peers"] == ["alpha"]
    assert holder.skipped == []


def test_slurpit_list_valued_reference_missing_is_skipped(slurpitsync_module) -> None:
    """Slurpit's policy for an unresolved list-valued reference is to append to `skipped` and continue."""
    peer_model = _slurpit_peer_model(slurpitsync_module)
    record_model = _slurpit_record_model(slurpitsync_module)
    holder = _slurpit_holder(peer_model=peer_model, peers={}, identifier_mapping="peer_ids")
    mapping = SchemaMappingModel(name=record_model._modelname, fields=[SLURPIT_LIST_FIELD])

    data = slurpitsync_module.SlurpitsyncAdapter.slurpit_obj_to_diffsync(
        holder, obj={"id": "record-1", "peer_ids": "missing"}, mapping=mapping, model=record_model
    )

    assert data["peers"] == []
    assert holder.skipped == ["missing"]


# --- Fixture teardown hygiene -------------------------------------------------------


@pytest.mark.parametrize(("name", "sdk_name"), [("ipfabricsync", "ipfabric"), ("slurpitsync", "slurpit")])
def test_teardown_leaves_no_stub_backed_module_on_parent_package(
    monkeypatch: pytest.MonkeyPatch, name: str, sdk_name: str
) -> None:
    """After a fixture's teardown runs, the parent package must not still expose the stub-backed module.

    A plain `import infrahub_sync.adapters.<name>` sets `<name>` as an attribute on the
    already-imported `infrahub_sync.adapters` package. If teardown only pops the
    `sys.modules` entry, that attribute survives and `from infrahub_sync.adapters import
    <name>` returns the stale stub-backed module without re-importing. This drives the
    same reload-and-restore helper the fixtures use, then imports through the parent
    package afterward and proves no stub-backed module comes back.
    """
    full_name = f"infrahub_sync.adapters.{name}"
    assert not hasattr(adapters_package, name), "leaked from a previous test"

    stub = cast("Any", types.ModuleType(sdk_name))
    if sdk_name == "ipfabric":
        stub.IPFClient = object
    with monkeypatch.context() as sdk_patch:
        sdk_patch.setitem(sys.modules, sdk_name, stub)
        gen = _reload_submodule(name)
        module = next(gen)
        assert module is sys.modules[full_name]
        assert getattr(adapters_package, name) is module

        with pytest.raises(StopIteration):
            next(gen)

    assert full_name not in sys.modules
    assert not hasattr(adapters_package, name)

    with pytest.raises(ModuleNotFoundError):
        __import__(full_name, fromlist=["_"])
