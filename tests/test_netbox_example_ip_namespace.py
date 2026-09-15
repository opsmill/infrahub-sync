"""Tests for the VRF-to-IP-namespace mapping in the shipped NetBox example.

Infrahub identifies a prefix by namespace + prefix and an IP address by
namespace + address, so ``examples/netbox_to_infrahub/config.yml`` maps every
NetBox VRF to an ``IpamNamespace`` and derives a ``namespace_name`` transform
for the prefix and address entries. These tests pin the two things that make
that work: the transform resolves a namespace for every record shape the NetBox
adapter can produce, and the resulting identifiers keep overlapping prefixes
apart.

The records here are plain dicts, matching what ``NetboxAdapter`` builds with
``dict(node)`` — pynetbox keeps every key returned by the API (including the
null ones) and turns a nested record into a plain dict.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest
import yaml
from diffsync import DiffSyncModel

from infrahub_sync import DiffSyncModelMixin, SyncConfig
from infrahub_sync.adapters.utils import get_value

if TYPE_CHECKING:
    from infrahub_sync import SchemaMappingModel

CONFIG_PATH = Path(__file__).resolve().parent.parent / "examples" / "netbox_to_infrahub" / "config.yml"

# The two kinds Infrahub identifies by namespace, with the NetBox field each
# one is keyed on.
NAMESPACED_KINDS = [("IpamPrefix", "prefix", "10.0.0.0/24"), ("IpamIPAddress", "address", "10.0.0.1/32")]
KIND_NAMES = [kind for kind, _, _ in NAMESPACED_KINDS]
KIND_KEY_FIELDS = [(kind, key_field) for kind, key_field, _ in NAMESPACED_KINDS]


def _mapping(name: str) -> SchemaMappingModel:
    """Return the example's schema_mapping entry for `name`."""
    config = SyncConfig(**yaml.safe_load(CONFIG_PATH.read_text()))
    return next(mapping for mapping in config.schema_mapping if mapping.name == name)


def _namespace_value(mapping: SchemaMappingModel, record: dict) -> object:
    """Run the example's transforms over `record` and read the namespace field."""
    namespace_field = next(field for field in mapping.fields if field.name == "ip_namespace")
    assert namespace_field.mapping is not None
    transformed = DiffSyncModelMixin.transform_records(records=[record], schema_mapping=mapping)[0]
    return get_value(transformed, namespace_field.mapping)


class PrefixIdentity(DiffSyncModel):
    """Stand-in for the generated `IpamPrefix`, limited to its identity."""

    _modelname = "IpamPrefix"
    _identifiers = ("prefix", "ip_namespace")
    _attributes = ()
    prefix: str
    ip_namespace: str


class AddressIdentity(DiffSyncModel):
    """Stand-in for the generated `IpamIPAddress`, limited to its identity."""

    _modelname = "IpamIPAddress"
    _identifiers = ("address", "ip_namespace")
    _attributes = ()
    address: str
    ip_namespace: str


# ---------------------------------------------------------------------------
# The namespace transform
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("kind", "key_field", "key_value"), NAMESPACED_KINDS)
def test_netbox_example_uses_the_vrf_name_as_the_namespace(kind: str, key_field: str, key_value: str) -> None:
    mapping = _mapping(kind)
    record = {key_field: key_value, "status": {"value": "active"}, "vrf": {"id": 3, "name": "MGMT"}}

    assert _namespace_value(mapping, record) == "MGMT"


@pytest.mark.parametrize(("kind", "key_field", "key_value"), NAMESPACED_KINDS)
def test_netbox_example_falls_back_to_the_default_namespace_without_a_vrf(
    kind: str, key_field: str, key_value: str
) -> None:
    mapping = _mapping(kind)
    record = {key_field: key_value, "status": {"value": "active"}, "vrf": None}

    assert _namespace_value(mapping, record) == "default"


@pytest.mark.parametrize(("kind", "key_field", "key_value"), NAMESPACED_KINDS)
def test_netbox_example_resolves_the_namespace_when_the_vrf_key_is_absent(
    kind: str, key_field: str, key_value: str
) -> None:
    """A payload without a `vrf` key at all must not trip StrictUndefined."""
    mapping = _mapping(kind)
    record = {key_field: key_value, "status": {"value": "active"}}

    assert _namespace_value(mapping, record) == "default"


# ---------------------------------------------------------------------------
# The namespace is the VRF name, whatever the VRF is called
# ---------------------------------------------------------------------------

# NetBox puts no constraint on a VRF name beyond being non-empty text, so the
# mapping has to carry any of these through unchanged.
LITERAL_LIKE_NAMES = ["None", "True", "False", "123", "0", "1.5", "[1, 2]", "{'a': 1}", "()"]
QUOTED_AND_ESCAPED_NAMES = ['quote"inside', "apos'inside", "back\\slash", "line\nbreak", "tab\there"]
NON_BMP_NAMES = ["emoji \U0001f600", "\U00020bb7", "mix \U0001f600 \u65e5 end"]


@pytest.mark.parametrize(("kind", "key_field", "key_value"), NAMESPACED_KINDS)
@pytest.mark.parametrize("vrf_name", LITERAL_LIKE_NAMES)
def test_netbox_example_keeps_vrf_names_that_look_like_python_literals(
    kind: str, key_field: str, key_value: str, vrf_name: str
) -> None:
    """A VRF called `123` or `None` names a namespace, it is not a number or a null."""
    mapping = _mapping(kind)
    record = {key_field: key_value, "status": {"value": "active"}, "vrf": {"id": 1, "name": vrf_name}}

    namespace = _namespace_value(mapping, record)

    assert namespace == vrf_name
    assert isinstance(namespace, str)


@pytest.mark.parametrize(("kind", "key_field", "key_value"), NAMESPACED_KINDS)
@pytest.mark.parametrize("vrf_name", QUOTED_AND_ESCAPED_NAMES + NON_BMP_NAMES)
def test_netbox_example_keeps_quoted_escaped_and_non_bmp_vrf_names(
    kind: str, key_field: str, key_value: str, vrf_name: str
) -> None:
    mapping = _mapping(kind)
    record = {key_field: key_value, "status": {"value": "active"}, "vrf": {"id": 1, "name": vrf_name}}

    namespace = _namespace_value(mapping, record)

    assert namespace == vrf_name
    assert isinstance(namespace, str)


# ---------------------------------------------------------------------------
# Namespace-aware identity
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("kind", "key_field"), KIND_KEY_FIELDS)
def test_netbox_example_identifies_by_namespace(kind: str, key_field: str) -> None:
    mapping = _mapping(kind)

    assert mapping.identifiers == [key_field, "ip_namespace"]


@pytest.mark.parametrize(
    ("kind", "identity_model"), [("IpamPrefix", PrefixIdentity), ("IpamIPAddress", AddressIdentity)]
)
def test_identity_stand_ins_match_the_example(kind: str, identity_model: type[DiffSyncModel]) -> None:
    """Guard: the stand-ins above cannot drift from the shipped mapping."""
    mapping = _mapping(kind)

    assert tuple(mapping.identifiers or ()) == identity_model._identifiers


def test_netbox_example_keeps_overlapping_prefixes_in_different_vrfs_apart() -> None:
    """The point of the mapping: same prefix, different VRF, different object."""
    mapping = _mapping("IpamPrefix")
    record = {"prefix": "10.0.0.0/24", "status": {"value": "active"}}

    in_mgmt = PrefixIdentity(
        prefix="10.0.0.0/24",
        ip_namespace=str(_namespace_value(mapping, {**record, "vrf": {"id": 1, "name": "MGMT"}})),
    )
    in_prod = PrefixIdentity(
        prefix="10.0.0.0/24",
        ip_namespace=str(_namespace_value(mapping, {**record, "vrf": {"id": 2, "name": "PROD"}})),
    )

    assert in_mgmt.get_unique_id() != in_prod.get_unique_id()


def test_netbox_example_keeps_overlapping_addresses_in_different_vrfs_apart() -> None:
    mapping = _mapping("IpamIPAddress")
    record = {"address": "10.0.0.1/32", "status": {"value": "active"}}

    in_mgmt = AddressIdentity(
        address="10.0.0.1/32",
        ip_namespace=str(_namespace_value(mapping, {**record, "vrf": {"id": 1, "name": "MGMT"}})),
    )
    in_prod = AddressIdentity(
        address="10.0.0.1/32",
        ip_namespace=str(_namespace_value(mapping, {**record, "vrf": {"id": 2, "name": "PROD"}})),
    )

    assert in_mgmt.get_unique_id() != in_prod.get_unique_id()


def test_netbox_example_merges_prefixes_that_resolve_to_the_same_namespace() -> None:
    """Accepted consequence: two rows with no VRF share the `default` namespace."""
    mapping = _mapping("IpamPrefix")
    namespace = str(_namespace_value(mapping, {"prefix": "10.0.0.0/24", "status": {"value": "active"}, "vrf": None}))

    first = PrefixIdentity(prefix="10.0.0.0/24", ip_namespace=namespace)
    second = PrefixIdentity(prefix="10.0.0.0/24", ip_namespace=namespace)

    assert namespace == "default"
    assert first.get_unique_id() == second.get_unique_id()


@pytest.mark.parametrize("kind", KIND_NAMES)
def test_netbox_example_keeps_the_vrf_relationship(kind: str) -> None:
    """The namespace is the identity; the VRF relationship itself is still synced."""
    mapping = _mapping(kind)
    vrf_field = next(field for field in mapping.fields if field.name == "vrf")

    assert vrf_field.reference == "IpamVRF"


@pytest.mark.parametrize("kind", KIND_NAMES)
def test_netbox_example_points_the_namespace_field_at_the_namespace_kind(kind: str) -> None:
    mapping = _mapping(kind)
    namespace_field = next(field for field in mapping.fields if field.name == "ip_namespace")

    assert namespace_field.reference == "IpamNamespace"


def test_netbox_example_maps_vrfs_to_namespaces() -> None:
    mapping = _mapping("IpamNamespace")

    assert mapping.mapping == "ipam.vrfs"
    assert mapping.identifiers == ["name"]
    assert [field.name for field in mapping.fields] == ["name", "description"]
