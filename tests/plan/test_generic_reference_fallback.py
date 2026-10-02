"""A filtered generic candidate cannot identify a missing peer's concrete kind."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from infrahub_sdk.schema.main import AttributeKind, AttributeSchemaAPI, GenericSchemaAPI, NodeSchemaAPI

from infrahub_sync.plan.errors import SourcePeerUnresolvedError
from infrahub_sync.plan.reference_candidates import reference_candidates
from infrahub_sync.runtime_schema import RuntimeModelPlan, RuntimeSideModels
from tests.test_potenda_plan_artifact import (
    _FakeAdapter,
    _FakeDiff,
    _FakeElement,
    _FakeRecord,
    build_config,
    build_potenda,
    manifest_path,
    mapping_entry,
    plan_run_dir,
    read_operations_bytes,
)

if TYPE_CHECKING:
    from pathlib import Path

    from infrahub_sync.potenda import Potenda

PEER_NAME = "site-only"
ALL_PEER_KINDS = ("LocationRack", "LocationSite", "LocationHub")


@pytest.fixture(autouse=True)
def _cache_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep artifact writes inside this test's directory."""
    monkeypatch.setenv("INFRAHUB_SYNC_CACHE_DIR", str(tmp_path))


def _engine(  # noqa: PLR0913 — separate inputs for schema, mapping, and loaded stores
    *,
    snapshot: bool,
    reference: str = "LocationAny",
    possible_kinds: tuple[str, ...] = ALL_PEER_KINDS,
    destination_rack: bool = False,
    source_rack: bool = False,
    identity_reference: bool = False,
) -> Potenda:
    """Map only Rack and Device against a real SDK generic and concrete schemas."""
    config = build_config(
        order=("LocationRack", "DcimDevice"),
        schema_mapping=[
            mapping_entry("LocationRack", identifiers=["name"], fields={"name": None}),
            mapping_entry(
                "DcimDevice",
                identifiers=["name", "location"] if identity_reference else ["name"],
                fields={"name": None, "location": reference},
            ),
        ],
    )
    schema = {
        "LocationAny": GenericSchemaAPI(name="Any", namespace="Location", used_by=list(possible_kinds)),
        "LocationRack": NodeSchemaAPI(
            name="Rack",
            namespace="Location",
            human_friendly_id=["name__value"],
            attributes=[AttributeSchemaAPI(name="name", kind=AttributeKind.TEXT)],
        ),
        "DcimDevice": NodeSchemaAPI(
            name="Device",
            namespace="Dcim",
            human_friendly_id=["name__value"],
            attributes=[AttributeSchemaAPI(name="name", kind=AttributeKind.TEXT)],
        ),
    }
    if snapshot:
        config._runtime_models = RuntimeModelPlan(
            branch="dst",
            schema_fingerprint="a" * 64,
            destination=RuntimeSideModels(adapter_class=object, models={}),
            source=None,
            generic_peers={"LocationAny": possible_kinds},
        )
        # The validated snapshot must win over a live schema that would admit a literal.
        schema["LocationAny"] = GenericSchemaAPI(name="Any", namespace="Location", used_by=["LocationRack"])
    return build_potenda(
        config=config,
        source=_FakeAdapter("source", [_FakeRecord("LocationRack", {"name": PEER_NAME})] if source_rack else []),
        destination=_FakeAdapter(
            "destination",
            [_FakeRecord("LocationRack", {"name": PEER_NAME})] if destination_rack else [],
            schema=schema,
        ),
        run_id="20261001T2200-12345678",
        top_level=config.order,
    )


def _device_diff(*, identity_reference: bool = False) -> _FakeDiff:
    keys = {"name": "device-a", **({"location": PEER_NAME} if identity_reference else {})}
    return _FakeDiff(
        {
            "DcimDevice": [
                _FakeElement(
                    kind="DcimDevice",
                    name="device-a",
                    keys=keys,
                    source_attrs={} if identity_reference else {"location": PEER_NAME},
                )
            ]
        }
    )


@pytest.mark.parametrize("snapshot", [False, True], ids=["direct-schema", "validated-snapshot"])
@pytest.mark.parametrize("destination_rack", [False, True], ids=["empty-destination", "same-name-rack"])
@pytest.mark.parametrize("identity_reference", [False, True], ids=["relationship", "identity"])
def test_an_unmapped_generic_peer_cannot_be_recorded_as_the_remaining_mapped_kind(
    *, snapshot: bool, destination_rack: bool, identity_reference: bool
) -> None:
    """An unloaded site must not bind a same-name rack at apply, even with a valid rack key."""
    engine = _engine(snapshot=snapshot, destination_rack=destination_rack, identity_reference=identity_reference)
    assert reference_candidates(engine.config, "DcimDevice") == {"location": ("LocationRack",)}

    with pytest.raises(SourcePeerUnresolvedError, match="Candidate peer kinds tried: LocationRack"):
        engine.write_plan_artifact([_device_diff(identity_reference=identity_reference)])

    assert not manifest_path(engine).exists()
    assert not (plan_run_dir(engine) / "plan" / "operations.jsonl").exists()


@pytest.mark.parametrize("snapshot", [False, True], ids=["direct-schema", "validated-snapshot"])
@pytest.mark.parametrize("source_rack", [False, True], ids=["destination-only", "loaded-peer"])
def test_an_explicit_concrete_reference_keeps_its_destination_only_literal_rule(
    *, snapshot: bool, source_rack: bool
) -> None:
    """Generic metadata does not remove the existing explicit Rack reference exception."""
    engine = _engine(snapshot=snapshot, reference="LocationRack", destination_rack=True, source_rack=source_rack)
    assert engine.write_plan_artifact([_device_diff()]) is not None
    operation = json.loads(read_operations_bytes(plan_run_dir(engine)))
    assert operation["relationships"] == [
        {"field": "location", "peer_kind": "LocationRack", "cardinality": "one", "peers": [{"name": PEER_NAME}]}
    ]


@pytest.mark.parametrize("snapshot", [False, True], ids=["direct-schema", "validated-snapshot"])
def test_a_loaded_concrete_peer_still_resolves_from_an_incomplete_generic_expansion(*, snapshot: bool) -> None:
    """A real source-store match proves the peer's kind even when other kinds are unmapped."""
    engine = _engine(snapshot=snapshot, source_rack=True)
    assert engine.write_plan_artifact([_device_diff()]) is not None
    operation = json.loads(read_operations_bytes(plan_run_dir(engine)))
    assert operation["relationships"][0]["peer_kind"] == "LocationRack"
    assert operation["relationships"][0]["peers"] == [{"name": PEER_NAME}]


@pytest.mark.parametrize("snapshot", [False, True], ids=["direct-schema", "validated-snapshot"])
def test_a_generic_with_only_one_possible_concrete_kind_can_use_a_literal_peer(*, snapshot: bool) -> None:
    """The full schema's singleton generic set proves the same kind as the mapped candidate."""
    engine = _engine(snapshot=snapshot, possible_kinds=("LocationRack",), destination_rack=True)
    assert engine.write_plan_artifact([_device_diff()]) is not None
    operation = json.loads(read_operations_bytes(plan_run_dir(engine)))
    assert operation["relationships"][0]["peer_kind"] == "LocationRack"
    assert operation["relationships"][0]["peers"] == [{"name": PEER_NAME}]


@pytest.mark.parametrize("snapshot", [False, True], ids=["direct-schema", "validated-snapshot"])
@pytest.mark.parametrize("mixed_kinds", [False, True], ids=["one-kind", "mixed-kinds"])
def test_a_generic_many_reference_requires_all_peers_to_share_one_concrete_kind(
    *, snapshot: bool, mixed_kinds: bool
) -> None:
    """The saved artifact supports homogeneous peer sets and refuses mixed kinds."""
    engine = _engine(snapshot=snapshot)
    assert engine.config is not None
    engine.config.schema_mapping.append(mapping_entry("LocationSite", identifiers=["name"], fields={"name": None}))
    engine.top_level.insert(1, "LocationSite")
    assert isinstance(engine.source, _FakeAdapter)
    engine.source.add(_FakeRecord("LocationRack", {"name": "rack-a"}))
    engine.source.add(_FakeRecord("LocationSite" if mixed_kinds else "LocationRack", {"name": "peer-b"}))
    diff = _FakeDiff(
        {
            "DcimDevice": [
                _FakeElement(
                    kind="DcimDevice",
                    name="device-a",
                    keys={"name": "device-a"},
                    source_attrs={"location": ["rack-a", "peer-b"]},
                )
            ]
        }
    )
    if mixed_kinds:
        with pytest.raises(SourcePeerUnresolvedError, match="names peers of more than one kind"):
            engine.write_plan_artifact([diff])
        assert not manifest_path(engine).exists()
    else:
        assert engine.write_plan_artifact([diff]) is not None
        operation = json.loads(read_operations_bytes(plan_run_dir(engine)))
        assert operation["relationships"] == [
            {
                "field": "location",
                "peer_kind": "LocationRack",
                "cardinality": "many",
                "peers": [{"name": "peer-b"}, {"name": "rack-a"}],
            }
        ]
