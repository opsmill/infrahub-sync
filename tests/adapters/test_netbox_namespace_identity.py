"""What the NetBox VRF-to-namespace mappings really do at the source-store boundary.

The shipped example names each IP namespace after a NetBox VRF. Two VRFs sharing a name
therefore produce one namespace record twice, and the second is refused while NetBox is
being read — the run fails before a plan exists. That is a refusal, not a merge, and the
documentation says so; these tests are what holds it to that.

Everything here runs the real `NetboxAdapter.model_loader` against the real DiffSync
store, with production model classes built by `build_runtime_models` from the shipped
destination-schema snapshot. Only the pynetbox client is stubbed, so the assertions cover
actual loading rather than identifier arithmetic on stand-in objects.

The positive case executes the ID-derived recipe published in
`docs/docs/reference/schema-mapping.mdx`, extracted from that page rather than restated
here, so a recipe that stops working cannot keep passing as prose.
"""

from __future__ import annotations

import collections
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest
import yaml

if TYPE_CHECKING:
    from infrahub_sync import SyncConfig
    from infrahub_sync.adapters.netbox import NetboxAdapter

# The netbox adapter hard-imports `pynetbox`, an optional dependency that is not part of
# the `dev` extra. Skip this module when it is unavailable instead of erroring during
# collection.
pytest.importorskip("pynetbox")

REPO_ROOT = Path(__file__).resolve().parents[2]
EXAMPLE_CONFIG = REPO_ROOT / "examples" / "netbox_to_infrahub" / "config.yml"
SCHEMA_SNAPSHOT = REPO_ROOT / "tests" / "data" / "generator_schema_snapshots" / "netbox_example_schema.json"
RECIPE_DOC = REPO_ROOT / "docs" / "docs" / "reference" / "schema-mapping.mdx"
RECIPE_HEADING = "### Key the namespace on the VRF ID instead of its name"

# The two expressions the reference publishes. The configuration under test is extracted
# from that page, so these constants are the byte-equality guard: changing the documented
# expression without changing these — or the reverse — fails here instead of quietly
# leaving the tests proving a recipe nobody is told to use.
DOCUMENTED_NAMESPACE_EXPRESSION = '{{ "%r" % ("netbox-vrf-%s" % id) }}'
DOCUMENTED_MEMBER_EXPRESSION = '{{ "%r" % ("netbox-vrf-%s" % vrf.id if vrf is defined and vrf else "default") }}'

# Two separate NetBox VRFs that happen to carry one name, with distinct IDs, distinct
# route distinguishers and — so nothing but the identity can be doing the work — the same
# description. The third is a VRF literally named `default`.
VRF_MGMT_A: dict[str, Any] = {
    "id": 3,
    "name": "MGMT",
    "rd": "65000:3",
    "description": "Management",
    "enforce_unique": True,
    "import_targets": [],
    "export_targets": [],
}
VRF_MGMT_B: dict[str, Any] = {**VRF_MGMT_A, "id": 4, "rd": "65000:4"}
VRF_NAMED_DEFAULT: dict[str, Any] = {**VRF_MGMT_A, "id": 5, "name": "default", "rd": "65000:5"}
SAME_NAME_VRFS = [VRF_MGMT_A, VRF_MGMT_B]
ALL_VRFS = [VRF_MGMT_A, VRF_MGMT_B, VRF_NAMED_DEFAULT]

# 10.0.0.0/24 and 10.0.0.1/32 appear in all three VRFs, so only the namespace can keep
# them apart. The two records without a VRF carry different values, because they share the
# built-in `default` namespace under every recipe.
PREFIXES: list[dict[str, Any]] = [
    {"id": 11, "prefix": "10.0.0.0/24", "status": {"value": "active"}, "description": "a", "vrf": VRF_MGMT_A},
    {"id": 12, "prefix": "10.0.0.0/24", "status": {"value": "active"}, "description": "b", "vrf": VRF_MGMT_B},
    {"id": 13, "prefix": "10.0.0.0/24", "status": {"value": "active"}, "description": "c", "vrf": VRF_NAMED_DEFAULT},
    {"id": 14, "prefix": "192.168.0.0/24", "status": {"value": "active"}, "description": "d", "vrf": None},
    {"id": 15, "prefix": "192.168.1.0/24", "status": {"value": "active"}, "description": "e"},
]
ADDRESSES: list[dict[str, Any]] = [
    {"id": 21, "address": "10.0.0.1/32", "status": {"value": "active"}, "description": "a", "vrf": VRF_MGMT_A},
    {"id": 22, "address": "10.0.0.1/32", "status": {"value": "active"}, "description": "b", "vrf": VRF_MGMT_B},
    {"id": 23, "address": "10.0.0.1/32", "status": {"value": "active"}, "description": "c", "vrf": VRF_NAMED_DEFAULT},
    {"id": 24, "address": "192.168.0.1/32", "status": {"value": "active"}, "description": "d", "vrf": None},
    {"id": 25, "address": "192.168.1.1/32", "status": {"value": "active"}, "description": "e"},
]

EXPECTED_PREFIX_NAMESPACES = {
    "10.0.0.0/24__netbox-vrf-3",
    "10.0.0.0/24__netbox-vrf-4",
    "10.0.0.0/24__netbox-vrf-5",
    "192.168.0.0/24__default",
    "192.168.1.0/24__default",
}
EXPECTED_ADDRESS_NAMESPACES = {
    "10.0.0.1/32__netbox-vrf-3",
    "10.0.0.1/32__netbox-vrf-4",
    "10.0.0.1/32__netbox-vrf-5",
    "192.168.0.1/32__default",
    "192.168.1.1/32__default",
}


class _Record(collections.UserDict):
    """Minimal pynetbox record stub: a UserDict, so `dict(record)` matches the adapter."""


@pytest.fixture(autouse=True)
def _stubbed_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub the adapter client for each test and restore its factory afterward."""
    monkeypatch.setattr(
        "infrahub_sync.adapters.netbox.NetboxAdapter._create_netbox_client",
        lambda _self, _adapter: MagicMock(),
    )


def _documented_recipe() -> list[dict[str, Any]]:
    """Return the schema-mapping entries the reference page publishes."""
    body = RECIPE_DOC.read_text(encoding="utf-8").split(RECIPE_HEADING, 1)[1]
    block = body.split("```yaml\n", 1)[1].split("```", 1)[0]
    return yaml.safe_load(block)


def _example_entries(*names: str) -> list[dict[str, Any]]:
    """Return the shipped example's entries for `names`, as written."""
    entries = yaml.safe_load(EXAMPLE_CONFIG.read_text(encoding="utf-8"))["schema_mapping"]
    return [entry for name in names for entry in entries if entry["name"] == name]


def _destination_snapshot(kinds: set[str]) -> dict[str, Any]:
    """Project the shipped destination-schema snapshot into the normalized wire shape."""
    entries = json.loads(SCHEMA_SNAPSHOT.read_text(encoding="utf-8"))
    return {
        kind: {
            "human_friendly_id": entries[kind]["data"]["human_friendly_id"] or [],
            "uniqueness_constraints": entries[kind]["data"]["uniqueness_constraints"] or [],
            "attributes": {
                attribute["name"]: {
                    "kind": attribute["kind"],
                    "optional": attribute["optional"],
                    "default_value": attribute["default_value"],
                    "unique": attribute["unique"],
                }
                for attribute in entries[kind]["data"]["attributes"]
            },
            "relationships": {
                relationship["name"]: {
                    "peer": relationship["peer"],
                    "cardinality": relationship["cardinality"],
                    "optional": relationship["optional"],
                    "kind": relationship["kind"],
                }
                for relationship in entries[kind]["data"]["relationships"]
            },
        }
        for kind in kinds
    }


def _configuration(schema_mapping: list[dict[str, Any]]) -> SyncConfig:
    from infrahub_sync import SchemaMappingModel, SyncAdapter, SyncConfig

    return SyncConfig(
        name="netbox-namespace-identity",
        source=SyncAdapter(name="netbox"),
        destination=SyncAdapter(name="infrahub"),
        schema_mapping=[SchemaMappingModel(**entry) for entry in schema_mapping],
    )


def _install_endpoint(adapter: NetboxAdapter, mapping: str, rows: list[dict[str, Any]]) -> None:
    """Point the stubbed client's `mapping` endpoint at `rows`."""
    *parents, leaf = mapping.split(".")
    node: Any = adapter.client
    for part in parents:
        node = getattr(node, part)
    endpoint = MagicMock()
    endpoint.all.return_value = [_Record(row) for row in rows]
    setattr(node, leaf, endpoint)


def _adapter(schema_mapping: list[dict[str, Any]], sources: dict[str, list[dict[str, Any]]]) -> NetboxAdapter:
    """Build a NetBox adapter with production models and a stubbed API behind it."""
    from infrahub_sync import SyncAdapter
    from infrahub_sync.adapters.netbox import NetboxAdapter, NetboxModel
    from infrahub_sync.runtime_schema import (
        bind_runtime_models,
        build_runtime_models,
        normalize_destination_schema,
    )

    configuration = _configuration(schema_mapping)
    adapter = NetboxAdapter(
        target="source",
        adapter=SyncAdapter(name="netbox", settings={"url": "https://example.invalid", "token": "x"}),
        config=configuration,
    )
    bind_runtime_models(
        adapter,
        build_runtime_models(
            snapshot=normalize_destination_schema(
                _destination_snapshot({entry.name for entry in configuration.schema_mapping})
            ),
            configuration=configuration,
            model_base=NetboxModel,
        ),
    )
    for mapping, rows in sources.items():
        _install_endpoint(adapter, mapping, rows)
    return adapter


def _load(adapter: NetboxAdapter, *kinds: str) -> None:
    """Load `kinds` in order through the real loader and the real store."""
    for kind in kinds:
        adapter.model_loader(model_name=kind, model=getattr(adapter, kind))


def _loaded(adapter: NetboxAdapter, kind: str) -> list[dict[str, Any]]:
    """Return what the store holds for `kind`, as each model dumps itself.

    The model classes are built for this run from the destination schema, so their
    fields are known at runtime rather than statically.
    """
    return [item.model_dump() for item in adapter.get_all(kind)]


# ---------------------------------------------------------------------------
# The shipped name-based mapping refuses, it does not merge
# ---------------------------------------------------------------------------


def test_same_name_vrfs_are_refused_while_the_namespace_records_load() -> None:
    """Two VRFs sharing a name collide at `IpamNamespace`, before any plan exists."""
    from diffsync import DiffSyncModel
    from diffsync.exceptions import ObjectAlreadyExists

    adapter = _adapter(_example_entries("IpamNamespace"), {"ipam.vrfs": SAME_NAME_VRFS})

    with pytest.raises(ObjectAlreadyExists) as refusal:
        _load(adapter, "IpamNamespace")

    assert "Object MGMT already present" in str(refusal.value)
    # It is the second VRF that is rejected, and loading stops there. Nothing folded the
    # two records together: the store holds the first one, unchanged.
    rejected = refusal.value.existing_object
    assert isinstance(rejected, DiffSyncModel)
    assert rejected.model_dump()["local_id"] == "4"
    assert [namespace["name"] for namespace in _loaded(adapter, "IpamNamespace")] == ["MGMT"]
    assert [namespace["local_id"] for namespace in _loaded(adapter, "IpamNamespace")] == ["3"]


def test_the_refused_source_records_are_left_unchanged() -> None:
    """The adapter transforms copies, so a refusal does not rewrite the source rows."""
    from diffsync.exceptions import ObjectAlreadyExists

    adapter = _adapter(_example_entries("IpamNamespace"), {"ipam.vrfs": SAME_NAME_VRFS})

    with pytest.raises(ObjectAlreadyExists):
        _load(adapter, "IpamNamespace")

    assert VRF_MGMT_A == {
        "id": 3,
        "name": "MGMT",
        "rd": "65000:3",
        "description": "Management",
        "enforce_unique": True,
        "import_targets": [],
        "export_targets": [],
    }
    assert VRF_MGMT_B["id"] == 4
    assert VRF_MGMT_B["name"] == "MGMT"


def test_the_full_example_also_refuses_same_name_vrfs_at_its_vrf_entry() -> None:
    """Why the recipe cannot be copied into the full example on its own.

    `IpamVRF` is keyed on the VRF name too, so a source the namespace transform fixes
    still refuses one entry later.
    """
    from diffsync.exceptions import ObjectAlreadyExists

    adapter = _adapter(_example_entries("IpamVRF"), {"ipam.vrfs": SAME_NAME_VRFS})

    with pytest.raises(ObjectAlreadyExists) as refusal:
        _load(adapter, "IpamVRF")

    assert "Object MGMT already present" in str(refusal.value)


def test_the_shipped_example_still_maps_the_namespace_from_the_vrf_name() -> None:
    """The recipe is an operator-selected alternative; the shipped default is unchanged."""
    (entry,) = _example_entries("IpamNamespace")

    assert entry["mapping"] == "ipam.vrfs"
    assert entry["identifiers"] == ["name"]
    assert "transforms" not in entry
    assert [field["mapping"] for field in entry["fields"] if field["name"] == "name"] == ["name"]


# ---------------------------------------------------------------------------
# The documented ID-derived recipe keeps them apart
# ---------------------------------------------------------------------------


def test_the_documented_recipe_is_the_one_these_tests_execute() -> None:
    """Byte-equality guard between the published expressions and the tested ones."""
    recipe = {entry["name"]: entry for entry in _documented_recipe()}

    assert sorted(recipe) == ["IpamIPAddress", "IpamNamespace", "IpamPrefix"]
    assert [transform["expression"] for transform in recipe["IpamNamespace"]["transforms"]] == [
        DOCUMENTED_NAMESPACE_EXPRESSION
    ]
    for kind in ("IpamPrefix", "IpamIPAddress"):
        namespace_transforms = [
            transform["expression"]
            for transform in recipe[kind]["transforms"]
            if transform["field"] == "namespace_name"
        ]
        assert namespace_transforms == [DOCUMENTED_MEMBER_EXPRESSION]


def test_the_documented_recipe_claims_no_vrf_records_or_references() -> None:
    """The scope the reference states: namespaces, prefixes and addresses only."""
    recipe = _documented_recipe()

    assert not [entry for entry in recipe if entry["name"] == "IpamVRF"]
    assert not [field for entry in recipe for field in entry["fields"] if field["name"] == "vrf"]
    assert not [
        field for entry in recipe for field in entry["fields"] if field.get("reference") not in {None, "IpamNamespace"}
    ]


def test_the_documented_recipe_loads_same_name_vrfs_as_separate_namespaces() -> None:
    """Both VRFs load, keyed on their NetBox IDs, with their provider IDs preserved."""
    adapter = _adapter(_documented_recipe(), {"ipam.vrfs": ALL_VRFS})

    _load(adapter, "IpamNamespace")

    namespaces = _loaded(adapter, "IpamNamespace")
    assert sorted(namespace["name"] for namespace in namespaces) == [
        "netbox-vrf-3",
        "netbox-vrf-4",
        "netbox-vrf-5",
    ]
    assert sorted(namespace["local_id"] for namespace in namespaces) == ["3", "4", "5"]


def test_the_documented_recipe_keeps_overlapping_prefixes_and_addresses_apart() -> None:
    """The whole point: same values in same-name VRFs, loaded as distinct objects."""
    adapter = _adapter(
        _documented_recipe(),
        {"ipam.vrfs": ALL_VRFS, "ipam.prefixes": PREFIXES, "ipam.ip-addresses": ADDRESSES},
    )

    _load(adapter, "IpamNamespace", "IpamPrefix", "IpamIPAddress")

    assert {prefix.get_unique_id() for prefix in adapter.get_all("IpamPrefix")} == EXPECTED_PREFIX_NAMESPACES
    assert {address.get_unique_id() for address in adapter.get_all("IpamIPAddress")} == EXPECTED_ADDRESS_NAMESPACES
    # Nothing was dropped or folded together on the way in.
    assert len(adapter.get_all("IpamPrefix")) == len(PREFIXES)
    assert len(adapter.get_all("IpamIPAddress")) == len(ADDRESSES)


@pytest.mark.parametrize(
    ("source_id", "expected"),
    [(11, "netbox-vrf-3"), (12, "netbox-vrf-4"), (13, "netbox-vrf-5"), (14, "default"), (15, "default")],
)
def test_the_documented_recipe_resolves_each_prefix_to_its_intended_namespace(source_id: int, expected: str) -> None:
    """Including a null `vrf` (14), an absent `vrf` key (15) and a VRF named `default` (13)."""
    adapter = _adapter(
        _documented_recipe(),
        {"ipam.vrfs": ALL_VRFS, "ipam.prefixes": PREFIXES, "ipam.ip-addresses": ADDRESSES},
    )

    _load(adapter, "IpamNamespace", "IpamPrefix", "IpamIPAddress")

    (prefix,) = [item for item in _loaded(adapter, "IpamPrefix") if item["local_id"] == str(source_id)]
    (address,) = [item for item in _loaded(adapter, "IpamIPAddress") if item["local_id"] == str(source_id + 10)]
    assert prefix["ip_namespace"] == expected
    assert address["ip_namespace"] == expected
    assert isinstance(prefix["ip_namespace"], str)


def test_the_documented_recipe_gives_a_vrf_named_default_its_own_namespace() -> None:
    """The stated difference from the shipped mapping, where it shares the built-in one."""
    adapter = _adapter(
        _documented_recipe(),
        {"ipam.vrfs": ALL_VRFS, "ipam.prefixes": PREFIXES, "ipam.ip-addresses": ADDRESSES},
    )

    _load(adapter, "IpamNamespace", "IpamPrefix", "IpamIPAddress")

    loaded = _loaded(adapter, "IpamPrefix")
    in_default_vrf = [item["local_id"] for item in loaded if item["ip_namespace"] == "netbox-vrf-5"]
    in_builtin_default = [item["local_id"] for item in loaded if item["ip_namespace"] == "default"]

    assert in_default_vrf == ["13"]
    assert sorted(in_builtin_default) == ["14", "15"]
