"""Tests for InfrahubAdapter.infrahub_node_to_diffsync: attribute values and peer relationships.

Most tests exercise the value-transformation logic in
``infrahub_sync/adapters/infrahub.py``: which attribute kinds round-trip
unchanged, which get string-coerced (e.g. ipaddress objects), and which
must NOT be coerced (e.g. ``kind: List``). Their nodes have no
relationships, so the method's relationship branch is a no-op.

Two tests cover relationships. Their three nodes form a loop: a device whose
primary IP address points to an interface whose device is that same device.
The tests run the real method and the real peer resolution on the device.
Each peer is converted only for its identifiers, so the conversion stops at
the IP address. Without that filter, the conversion follows the loop until
Python raises ``RecursionError``. In the second test the IP address's
identifier is not mapped: the error must list the IP address's other mapped
fields without looking up the interface.

The tests bypass network setup by constructing the adapter via
``__new__`` and supplying only the bits the method touches: ``config``
and ``schema``, the adapter's own loaded kind-to-schema mapping, plus a
node-like object exposing ``id``, ``get_kind()`` and one Python attribute
for each Infrahub attribute or relationship. The relationship tests also
supply ``client``, whose store holds the three nodes and records each
lookup, and whose ``get`` fails the test, and one DiffSync model class per
kind.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass, field
from typing import Any, NoReturn

import pytest

from infrahub_sync import (
    SchemaMappingField,
    SchemaMappingModel,
    SyncAdapter,
    SyncConfig,
)
from infrahub_sync.adapters.infrahub import InfrahubAdapter, PeerIdentifierError

# Test constant — extracted to avoid PLR2004 (magic-value) warnings on the
# repeated literal. 443 is just a representative HTTPS port for the Number
# kind; no significance beyond exercising int round-trip.
HTTPS_PORT = 443


# ---------------------------------------------------------------------------
# Lightweight stand-ins for ``InfrahubNodeSync`` and its schema. We only
# need the bits ``infrahub_node_to_diffsync`` reads — no SDK plumbing.
# ---------------------------------------------------------------------------


@dataclass
class FakeAttr:
    """Stand-in for ``InfrahubNodeSync``'s attribute manager — only ``.value`` is read."""

    value: Any


@dataclass
class FakeAttrSchema:
    """Stand-in for an attribute schema — the completeness check in ``resolve_peer_node`` reads both fields."""

    name: str
    optional: bool = False


@dataclass
class FakeRelSchema:
    """Stand-in for a relationship schema — ``name``, ``peer`` kind and ``cardinality`` are read."""

    name: str
    peer: str
    cardinality: str = "one"


@dataclass
class FakeRelatedNode:
    """Stand-in for ``RelatedNodeSync`` — only ``.id``, the peer's id, is read."""

    id: str


@dataclass
class FakeSchema:
    """Stand-in for the kind schema the adapter holds in its own mapping."""

    kind: str
    attribute_names: list[str] = field(default_factory=list)
    attributes: list[FakeAttrSchema] = field(default_factory=list)
    relationships: list[FakeRelSchema] = field(default_factory=list)
    relationship_names: list[str] = field(default_factory=list)


class FakeNode:
    """Stand-in for ``InfrahubNodeSync`` exposing the attributes and relationships by name.

    ``peers`` maps a relationship name to the peer's kind and id. Every
    attribute is declared required, so a peer whose attributes all hold a
    value counts as complete and ``resolve_peer_node`` does not fetch it again.
    """

    def __init__(
        self,
        node_id: str,
        kind: str,
        attrs: dict[str, Any],
        peers: dict[str, tuple[str, str]] | None = None,
    ) -> None:
        peers = peers or {}
        self.id = node_id
        self.schema = FakeSchema(
            kind=kind,
            attribute_names=list(attrs.keys()),
            attributes=[FakeAttrSchema(name=name) for name in attrs],
            relationships=[FakeRelSchema(name=name, peer=peer_kind) for name, (peer_kind, _) in peers.items()],
            relationship_names=list(peers.keys()),
        )
        for name, value in attrs.items():
            setattr(self, name, FakeAttr(value=value))
        for name, (_, peer_id) in peers.items():
            setattr(self, name, FakeRelatedNode(id=peer_id))

    def get_kind(self) -> str:
        return self.schema.kind


class FakeNodeStore:
    """Stand-in for the SDK node store, keyed by kind and node id. Records each lookup as (kind, id)."""

    def __init__(self, nodes: list[FakeNode]) -> None:
        self._nodes = {(node.get_kind(), node.id): node for node in nodes}
        self.lookups: list[tuple[str, str]] = []

    def get(self, *, key: str, kind: str, raise_when_missing: bool = True) -> FakeNode | None:
        assert raise_when_missing is False
        self.lookups.append((kind, key))
        return self._nodes.get((kind, key))


class FakeClient:
    """Stand-in for ``InfrahubClientSync``: holds the store and fails the test on any fetch."""

    def __init__(self, nodes: list[FakeNode]) -> None:
        self.store = FakeNodeStore(nodes)

    @staticmethod
    def get(**kwargs: object) -> NoReturn:
        pytest.fail(f"Unexpected fetch from Infrahub: {kwargs}")


class FakeAdapter(InfrahubAdapter):
    """``InfrahubAdapter`` with its schema mapping and client narrowed to the fakes these tests hold."""

    schema: dict[str, FakeSchema]
    client: FakeClient


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_config(kind: str, field_names: list[str]) -> SyncConfig:
    """SyncConfig with one schema_mapping entry covering ``field_names``."""

    return SyncConfig(
        name="test",
        source=SyncAdapter(name="src", adapter="x:x"),
        destination=SyncAdapter(name="dst", adapter="x:x"),
        order=[kind],
        schema_mapping=[
            SchemaMappingModel(
                name=kind,
                mapping=kind,
                identifiers=["name"],
                fields=[SchemaMappingField(name=n, mapping=n) for n in field_names],
            ),
        ],
    )


def _make_adapter(kind: str, field_names: list[str]) -> FakeAdapter:
    """Build an InfrahubAdapter that bypasses network setup.

    ``__init__`` requires live Infrahub plumbing (client, schema fetch,
    source/owner lookup). We don't need any of that for value-transform
    tests — only ``self.config`` and ``self.schema`` are touched. Using
    ``__new__`` skips ``__init__`` entirely.
    """
    adapter = FakeAdapter.__new__(FakeAdapter)
    adapter.config = _make_config(kind, field_names)
    adapter.schema = {}
    return adapter


def _serialise(adapter: FakeAdapter, node: FakeNode) -> dict[str, Any]:
    """Call ``adapter.infrahub_node_to_diffsync`` on a duck-typed fake node.

    The method's parameter annotation is ``InfrahubNodeSync``, but the
    function body only reads ``node.id``, ``node.get_kind()`` and per-name
    attribute and relationship managers — all of which ``FakeNode`` provides. The node's
    schema goes into the adapter's own mapping first, because that mapping
    is where the adapter reads a kind's schema. The type suppression below
    is a single, scoped location rather than once per test.
    """
    adapter.schema[node.get_kind()] = node.schema
    return adapter.infrahub_node_to_diffsync(node=node)  # ty: ignore[invalid-argument-type]


# ---------------------------------------------------------------------------
# Tests — one per attribute kind. The pass-through cases assert both the
# value AND the resulting Python type. Pydantic's downstream validation
# rejects a stringified list (``"[]"``) against a ``list[str]`` field, so
# locking the type at the dict boundary is the contract that matters;
# value equality alone (``[] == "[]"`` is False but ``str([]) == "[]"``)
# is a weaker invariant.
# ---------------------------------------------------------------------------


def test_always_sets_local_id_from_node_id() -> None:
    """``local_id`` must be the stringified node id, regardless of attrs."""
    adapter = _make_adapter("Thing", [])
    node = FakeNode(node_id="abc-123", kind="Thing", attrs={})
    data = _serialise(adapter, node)
    assert data == {"local_id": "abc-123"}


def test_text_value_passes_through_unchanged() -> None:
    """``kind: Text`` is already str — no transformation needed."""
    adapter = _make_adapter("Thing", ["label"])
    node = FakeNode(node_id="1", kind="Thing", attrs={"label": "hello"})
    data = _serialise(adapter, node)
    assert data["label"] == "hello"
    assert isinstance(data["label"], str)


def test_list_value_passes_through_as_list() -> None:
    """``kind: List`` must arrive as a real list.

    Stringifying turns a list into its ``repr`` (e.g. ``"[]"`` / ``"['a']"``),
    which then fails Pydantic validation against ``list[str]``-typed DiffSync
    fields.
    """
    adapter = _make_adapter("Thing", ["tags"])
    node = FakeNode(node_id="1", kind="Thing", attrs={"tags": ["foo", "bar"]})
    data = _serialise(adapter, node)
    assert data["tags"] == ["foo", "bar"]
    assert isinstance(data["tags"], list)


def test_empty_list_value_passes_through_as_empty_list() -> None:
    """Empty list is a separate code path under ``str()`` — exercise it explicitly."""
    adapter = _make_adapter("Thing", ["tags"])
    node = FakeNode(node_id="1", kind="Thing", attrs={"tags": []})
    data = _serialise(adapter, node)
    assert data["tags"] == []
    assert isinstance(data["tags"], list)


def test_number_value_passes_through_as_int() -> None:
    """``kind: Number`` must stay numeric, not become a stringified int."""
    adapter = _make_adapter("Thing", ["port"])
    node = FakeNode(node_id="1", kind="Thing", attrs={"port": HTTPS_PORT})
    data = _serialise(adapter, node)
    assert data["port"] == HTTPS_PORT
    assert isinstance(data["port"], int)


def test_boolean_value_passes_through_as_bool() -> None:
    """``kind: Boolean`` must stay bool, not become ``"True"`` / ``"False"``."""
    adapter = _make_adapter("Thing", ["enabled"])
    node = FakeNode(node_id="1", kind="Thing", attrs={"enabled": True})
    data = _serialise(adapter, node)
    assert data["enabled"] is True
    assert isinstance(data["enabled"], bool)


def test_dict_value_passes_through_as_dict() -> None:
    """``kind: JSON`` arrives from the SDK as a dict — must not be stringified."""
    adapter = _make_adapter("Thing", ["payload"])
    node = FakeNode(node_id="1", kind="Thing", attrs={"payload": {"a": 1, "b": [2, 3]}})
    data = _serialise(adapter, node)
    assert data["payload"] == {"a": 1, "b": [2, 3]}
    assert isinstance(data["payload"], dict)


def test_datetime_string_value_passes_through() -> None:
    """``kind: DateTime`` already arrives from the SDK as an ISO-8601 string."""
    adapter = _make_adapter("Thing", ["expires_at"])
    node = FakeNode(node_id="1", kind="Thing", attrs={"expires_at": "2027-08-21T08:21:16Z"})
    data = _serialise(adapter, node)
    assert data["expires_at"] == "2027-08-21T08:21:16Z"
    assert isinstance(data["expires_at"], str)


def test_none_value_passes_through_as_none() -> None:
    """A null attribute must stay None, not become the string ``"None"``."""
    adapter = _make_adapter("Thing", ["nickname"])
    node = FakeNode(node_id="1", kind="Thing", attrs={"nickname": None})
    data = _serialise(adapter, node)
    assert data["nickname"] is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (ipaddress.IPv4Interface("10.0.0.1/24"), "10.0.0.1/24"),
        (ipaddress.IPv6Interface("2001:db8::1/64"), "2001:db8::1/64"),
        (ipaddress.IPv4Network("10.0.0.0/24"), "10.0.0.0/24"),
        (ipaddress.IPv6Network("2001:db8::/64"), "2001:db8::/64"),
    ],
)
def test_ip_types_are_stringified(raw: Any, expected: str) -> None:  # noqa: ANN401 — heterogeneous ipaddress types
    """IP types: stringified intentionally — DiffSync models store them as str."""
    adapter = _make_adapter("Thing", ["address"])
    node = FakeNode(node_id="1", kind="Thing", attrs={"address": raw})
    data = _serialise(adapter, node)
    assert data["address"] == expected
    assert isinstance(data["address"], str)


def test_field_not_in_schema_mapping_is_skipped() -> None:
    """``has_field`` filters out attributes not in the schema_mapping config."""
    adapter = _make_adapter("Thing", ["wanted"])  # only "wanted" is in the mapping
    node = FakeNode(node_id="1", kind="Thing", attrs={"wanted": "yes", "ignored": "no"})
    data = _serialise(adapter, node)
    assert "wanted" in data
    assert "ignored" not in data


def test_mixed_kinds_round_trip_together() -> None:
    """End-to-end sanity: a node with one attribute of each kind survives intact.

    This is what the real ``CertificateCertificate`` shape looks like:
    serial (str), expiration (datetime as str), alternative_name (list),
    key_size (str via Dropdown), and a None optional field.
    """
    field_names = ["serial", "expiration", "sans", "key_size", "validation", "is_revoked"]
    adapter = _make_adapter("Cert", field_names)
    node = FakeNode(
        node_id="cert-1",
        kind="Cert",
        attrs={
            "serial": "abc123",
            "expiration": "2027-08-21T08:21:16Z",
            "sans": ["a.com", "b.com"],
            "key_size": "2048",
            "validation": None,
            "is_revoked": False,
        },
    )
    data = _serialise(adapter, node)
    assert data == {
        "local_id": "cert-1",
        "serial": "abc123",
        "expiration": "2027-08-21T08:21:16Z",
        "sans": ["a.com", "b.com"],
        "key_size": "2048",
        "validation": None,
        "is_revoked": False,
    }
    # Explicit type checks on the kinds where Pydantic won't coerce a
    # stringified value back (list, bool) — locks in correct typing at
    # the dict boundary.
    assert isinstance(data["sans"], list)
    assert isinstance(data["is_revoked"], bool)


# ---------------------------------------------------------------------------
# End-to-end: the real consumer of this method's output is
# ``DiffSyncModel(**data)`` inside ``InfrahubAdapter.load``. Validating
# the dict shape alone misses the layer that matters in practice —
# Pydantic typed-field validation on the DiffSync model. These tests
# feed the adapter's output straight into a model and prove construction
# succeeds with values of the right Python type.
# ---------------------------------------------------------------------------


from diffsync import DiffSyncModel  # noqa: E402

from infrahub_sync import DiffSyncModelMixin  # noqa: E402


class TypedCertModel(DiffSyncModelMixin, DiffSyncModel):
    """A Pydantic-typed DiffSync model representative of real downstream usage.

    Every attribute is typed *exactly* (``list[str]`` not ``list``;
    ``int`` not ``int | str``). The adapter's output must construct this
    model cleanly without Pydantic coercing values across types.
    """

    _modelname = "TypedCert"
    _identifiers = ("serial",)
    _attributes = (
        "subject_dn",
        "expiration",
        "port",
        "enabled",
        "sans",
        "metadata",
        "validation",
    )

    serial: str
    subject_dn: str
    expiration: str
    port: int
    enabled: bool
    sans: list[str]
    metadata: dict[str, Any]
    validation: str | None = None
    local_id: str | None = None
    local_data: Any | None = None


def test_adapter_output_constructs_pydantic_diffsync_model() -> None:
    """The output of ``infrahub_node_to_diffsync`` must be directly
    consumable by ``DiffSyncModel(**data)``.

    This is what ``InfrahubAdapter.load`` does at runtime. The other
    tests in this module check dict shape; this one checks the contract
    that matters downstream: Pydantic-typed model construction.
    """
    field_names = ["serial", "subject_dn", "expiration", "port", "enabled", "sans", "metadata", "validation"]
    adapter = _make_adapter("TypedCert", field_names)
    node = FakeNode(
        node_id="cert-real",
        kind="TypedCert",
        attrs={
            "serial": "abc123",
            "subject_dn": "CN=app.example.com",
            "expiration": "2027-08-21T08:21:16Z",
            "port": HTTPS_PORT,
            "enabled": True,
            "sans": ["app.example.com", "alt.example.com"],
            "metadata": {"issuer": "demo-ca", "chain_depth": 2},
            "validation": None,
        },
    )

    data = _serialise(adapter, node)

    # The adapter's dict must satisfy strict Pydantic field types — any
    # cross-type coercion here would surface as ``ValidationError``.
    instance = TypedCertModel(**data)

    # Values survive intact.
    assert instance.serial == "abc123"
    assert instance.subject_dn == "CN=app.example.com"
    assert instance.port == HTTPS_PORT
    assert instance.enabled is True
    assert instance.sans == ["app.example.com", "alt.example.com"]
    assert instance.metadata == {"issuer": "demo-ca", "chain_depth": 2}
    assert instance.validation is None

    # Each non-string kind must arrive with the exact Python type the
    # model annotates — Pydantic strict-mode would reject a coerced str.
    assert isinstance(instance.sans, list)
    assert isinstance(instance.port, int)
    assert isinstance(instance.enabled, bool)
    assert isinstance(instance.metadata, dict)


def test_adapter_output_constructs_model_with_empty_list() -> None:
    """Empty-list and empty-dict edge cases must construct the model.

    ``str([])`` and ``str({})`` produce ``"[]"`` / ``"{}"`` — distinct
    Python literals that look list/dict-ish but fail Pydantic validation
    against ``list[str]`` / ``dict[str, Any]`` fields.
    """
    field_names = ["serial", "subject_dn", "expiration", "port", "enabled", "sans", "metadata"]
    adapter = _make_adapter("TypedCert", field_names)
    node = FakeNode(
        node_id="cert-empty",
        kind="TypedCert",
        attrs={
            "serial": "empty-sans",
            "subject_dn": "CN=no-sans.example.com",
            "expiration": "2027-08-21T08:21:16Z",
            "port": HTTPS_PORT,
            "enabled": True,
            "sans": [],
            "metadata": {},
        },
    )

    data = _serialise(adapter, node)
    instance = TypedCertModel(**data)

    assert instance.sans == []
    assert isinstance(instance.sans, list)
    assert instance.metadata == {}
    assert isinstance(instance.metadata, dict)


# ---------------------------------------------------------------------------
# Relationship loop: the device's primary IP address points to an interface
# whose device is the same device. This test runs the real
# ``infrahub_node_to_diffsync`` and ``_resolve_peer_unique_id``; nothing on
# the adapter is replaced. As in the adapter that ``infrahub-sync generate``
# writes, each DiffSync model is a class attribute named after its kind.
# ---------------------------------------------------------------------------


class DeviceModel(DiffSyncModelMixin, DiffSyncModel):
    """DiffSync model for the device kind of the loop."""

    _modelname = "InfraDevice"
    _identifiers = ("name",)
    _attributes = ("primary_ip",)

    name: str
    primary_ip: str | None = None


class IPAddressModel(DiffSyncModelMixin, DiffSyncModel):
    """DiffSync model for the IP address kind of the loop."""

    _modelname = "IpamIPAddress"
    _identifiers = ("address",)
    _attributes = ("interface",)

    address: str
    interface: str | None = None


class InterfaceModel(DiffSyncModelMixin, DiffSyncModel):
    """DiffSync model for the interface kind of the loop, identified by its device and name."""

    _modelname = "InfraInterface"
    _identifiers = ("device", "name")
    _attributes = ()

    device: str
    name: str


class LoopAdapter(FakeAdapter):
    """Adapter that holds one DiffSync model per kind, read by ``getattr(self, peer_kind)``."""

    InfraDevice = DeviceModel
    IpamIPAddress = IPAddressModel
    InfraInterface = InterfaceModel


def _make_loop_config(ip_address_fields: list[SchemaMappingField] | None = None) -> SyncConfig:
    """SyncConfig that maps each kind of the loop with its relationship to the next kind.

    ``ip_address_fields`` replaces the fields mapped for the IP address kind.
    """
    if ip_address_fields is None:
        ip_address_fields = [
            SchemaMappingField(name="address", mapping="address"),
            SchemaMappingField(name="interface", mapping="interface", reference="InfraInterface"),
        ]
    return SyncConfig(
        name="test",
        source=SyncAdapter(name="src", adapter="x:x"),
        destination=SyncAdapter(name="dst", adapter="x:x"),
        order=["InfraDevice", "IpamIPAddress", "InfraInterface"],
        schema_mapping=[
            SchemaMappingModel(
                name="InfraDevice",
                mapping="InfraDevice",
                identifiers=["name"],
                fields=[
                    SchemaMappingField(name="name", mapping="name"),
                    SchemaMappingField(name="primary_ip", mapping="primary_ip", reference="IpamIPAddress"),
                ],
            ),
            SchemaMappingModel(
                name="IpamIPAddress",
                mapping="IpamIPAddress",
                identifiers=["address"],
                fields=ip_address_fields,
            ),
            SchemaMappingModel(
                name="InfraInterface",
                mapping="InfraInterface",
                identifiers=["device", "name"],
                fields=[
                    SchemaMappingField(name="name", mapping="name"),
                    SchemaMappingField(name="device", mapping="device", reference="InfraDevice"),
                ],
            ),
        ],
    )


def test_relationship_loop_converts_peers_by_identifiers_only() -> None:
    """A device whose primary IP address sits on one of its own interfaces converts without recursion.

    The IP address is converted only for its identifier, ``address``, so its
    ``interface`` relationship is not followed. If every relationship of a peer
    were converted, the conversion would follow device > IP address >
    interface > device until Python raised ``RecursionError``.
    """
    device = FakeNode(
        node_id="d1",
        kind="InfraDevice",
        attrs={"name": "sw1"},
        peers={"primary_ip": ("IpamIPAddress", "ip1")},
    )
    ip_address = FakeNode(
        node_id="ip1",
        kind="IpamIPAddress",
        attrs={"address": ipaddress.IPv4Interface("10.0.0.1/32")},
        peers={"interface": ("InfraInterface", "if1")},
    )
    interface = FakeNode(
        node_id="if1",
        kind="InfraInterface",
        attrs={"name": "eth0"},
        peers={"device": ("InfraDevice", "d1")},
    )
    nodes = [device, ip_address, interface]
    adapter = LoopAdapter.__new__(LoopAdapter)
    adapter.config = _make_loop_config()
    adapter.schema = {node.get_kind(): node.schema for node in nodes}
    adapter.client = FakeClient(nodes)

    data = _serialise(adapter, device)

    assert data == {"local_id": "d1", "name": "sw1", "primary_ip": "10.0.0.1/32"}


def test_peer_missing_identifier_lists_its_mapped_fields_without_resolving_its_peers() -> None:
    """When an IP address cannot be identified, the error lists its other mapped fields.

    The schema_mapping maps the IP address's ``description`` and ``interface``
    but not its identifier, ``address``. ``present_keys`` lists ``description``
    and ``interface``, read from the IP address's schema. The interface is not
    looked up: resolving it would follow the loop back to the device.
    """
    device = FakeNode(
        node_id="d1",
        kind="InfraDevice",
        attrs={"name": "sw1"},
        peers={"primary_ip": ("IpamIPAddress", "ip1")},
    )
    ip_address = FakeNode(
        node_id="ip1",
        kind="IpamIPAddress",
        attrs={"address": ipaddress.IPv4Interface("10.0.0.1/32"), "description": "loopback"},
        peers={"interface": ("InfraInterface", "if1")},
    )
    interface = FakeNode(
        node_id="if1",
        kind="InfraInterface",
        attrs={"name": "eth0"},
        peers={"device": ("InfraDevice", "d1")},
    )
    nodes = [device, ip_address, interface]
    adapter = LoopAdapter.__new__(LoopAdapter)
    adapter.config = _make_loop_config(
        ip_address_fields=[
            SchemaMappingField(name="description", mapping="description"),
            SchemaMappingField(name="interface", mapping="interface", reference="InfraInterface"),
        ]
    )
    adapter.schema = {node.get_kind(): node.schema for node in nodes}
    adapter.client = FakeClient(nodes)

    with pytest.raises(PeerIdentifierError) as excinfo:
        _serialise(adapter, device)

    err = excinfo.value
    assert err.peer_kind == "IpamIPAddress"
    assert err.missing_keys == ("address",)
    assert err.present_keys == ("local_id", "description", "interface")
    assert adapter.client.store.lookups == [("IpamIPAddress", "ip1")]
