"""Tests for InfrahubAdapter.infrahub_node_to_diffsync attribute serialisation.

These tests exercise the value-transformation logic in
``infrahub_sync/adapters/infrahub.py``: which attribute kinds round-trip
unchanged, which get string-coerced (e.g. ipaddress objects), and which
must NOT be coerced (e.g. ``kind: List``).

The tests bypass network setup by constructing the adapter via
``__new__`` and supplying only the bits the method touches: ``config``,
plus a node-like object exposing ``id``, ``_schema``, and one attribute
per kind. Relationship handling is out of scope here — the fixtures use
empty ``relationships`` so the method's relationship branch is a no-op.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass, field
from typing import Any

import pytest

from infrahub_sync import (
    SchemaMappingField,
    SchemaMappingModel,
    SyncAdapter,
    SyncConfig,
)
from infrahub_sync.adapters.infrahub import InfrahubAdapter


# ---------------------------------------------------------------------------
# Lightweight stand-ins for ``InfrahubNodeSync`` and its schema. We only
# need the bits ``infrahub_node_to_diffsync`` reads — no SDK plumbing.
# ---------------------------------------------------------------------------


@dataclass
class FakeAttr:
    """Stand-in for ``InfrahubNodeSync``'s attribute manager — only ``.value`` is read."""

    value: Any


@dataclass
class FakeSchema:
    """Stand-in for ``node._schema``."""

    kind: str
    attribute_names: list[str] = field(default_factory=list)
    relationships: list = field(default_factory=list)
    relationship_names: list[str] = field(default_factory=list)


class FakeNode:
    """Stand-in for ``InfrahubNodeSync`` exposing the attributes by name."""

    def __init__(self, node_id: str, kind: str, attrs: dict[str, Any]) -> None:
        self.id = node_id
        self._schema = FakeSchema(kind=kind, attribute_names=list(attrs.keys()))
        for name, value in attrs.items():
            setattr(self, name, FakeAttr(value=value))


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


def _make_adapter(kind: str, field_names: list[str]) -> InfrahubAdapter:
    """Build an InfrahubAdapter that bypasses network setup.

    ``__init__`` requires live Infrahub plumbing (client, schema fetch,
    source/owner lookup). We don't need any of that for value-transform
    tests — only ``self.config`` is touched. Using ``__new__`` skips
    ``__init__`` entirely.
    """
    adapter = InfrahubAdapter.__new__(InfrahubAdapter)
    adapter.config = _make_config(kind, field_names)
    return adapter


# ---------------------------------------------------------------------------
# Tests — one per attribute kind. The pass-through cases all assert both
# the value AND the resulting Python type, because the original bug was a
# type change (list → str) that Pydantic later rejected — value equality
# alone (``[] == "[]"`` is False but ``str([]) == "[]"``) wouldn't catch
# every regression cleanly.
# ---------------------------------------------------------------------------


def test_always_sets_local_id_from_node_id() -> None:
    """``local_id`` must be the stringified node id, regardless of attrs."""
    adapter = _make_adapter("Thing", [])
    node = FakeNode(node_id="abc-123", kind="Thing", attrs={})
    data = adapter.infrahub_node_to_diffsync(node)
    assert data == {"local_id": "abc-123"}


def test_text_value_passes_through_unchanged() -> None:
    """``kind: Text`` is already str — no transformation needed."""
    adapter = _make_adapter("Thing", ["label"])
    node = FakeNode(node_id="1", kind="Thing", attrs={"label": "hello"})
    data = adapter.infrahub_node_to_diffsync(node)
    assert data["label"] == "hello"
    assert isinstance(data["label"], str)


def test_list_value_passes_through_as_list() -> None:
    """``kind: List`` must arrive as a real list.

    Regression guard for the original bug: prior to this fix, lists were
    coerced via ``str()`` to ``"[]"`` / ``"['a']"``, which then failed
    Pydantic validation against ``list[str]``-typed DiffSync fields.
    """
    adapter = _make_adapter("Thing", ["tags"])
    node = FakeNode(node_id="1", kind="Thing", attrs={"tags": ["foo", "bar"]})
    data = adapter.infrahub_node_to_diffsync(node)
    assert data["tags"] == ["foo", "bar"]
    assert isinstance(data["tags"], list)


def test_empty_list_value_passes_through_as_empty_list() -> None:
    """Empty list is the most common failure mode in the wild — exercise it explicitly."""
    adapter = _make_adapter("Thing", ["tags"])
    node = FakeNode(node_id="1", kind="Thing", attrs={"tags": []})
    data = adapter.infrahub_node_to_diffsync(node)
    assert data["tags"] == []
    assert isinstance(data["tags"], list)


def test_number_value_passes_through_as_int() -> None:
    """``kind: Number`` must stay numeric, not become a stringified int."""
    adapter = _make_adapter("Thing", ["port"])
    node = FakeNode(node_id="1", kind="Thing", attrs={"port": 443})
    data = adapter.infrahub_node_to_diffsync(node)
    assert data["port"] == 443
    assert isinstance(data["port"], int)


def test_boolean_value_passes_through_as_bool() -> None:
    """``kind: Boolean`` must stay bool, not become ``"True"`` / ``"False"``."""
    adapter = _make_adapter("Thing", ["enabled"])
    node = FakeNode(node_id="1", kind="Thing", attrs={"enabled": True})
    data = adapter.infrahub_node_to_diffsync(node)
    assert data["enabled"] is True
    assert isinstance(data["enabled"], bool)


def test_dict_value_passes_through_as_dict() -> None:
    """``kind: JSON`` arrives from the SDK as a dict — must not be stringified."""
    adapter = _make_adapter("Thing", ["payload"])
    node = FakeNode(node_id="1", kind="Thing", attrs={"payload": {"a": 1, "b": [2, 3]}})
    data = adapter.infrahub_node_to_diffsync(node)
    assert data["payload"] == {"a": 1, "b": [2, 3]}
    assert isinstance(data["payload"], dict)


def test_datetime_string_value_passes_through() -> None:
    """``kind: DateTime`` already arrives from the SDK as an ISO-8601 string."""
    adapter = _make_adapter("Thing", ["expires_at"])
    node = FakeNode(node_id="1", kind="Thing", attrs={"expires_at": "2027-08-21T08:21:16Z"})
    data = adapter.infrahub_node_to_diffsync(node)
    assert data["expires_at"] == "2027-08-21T08:21:16Z"
    assert isinstance(data["expires_at"], str)


def test_none_value_passes_through_as_none() -> None:
    """A null attribute must stay None, not become the string ``"None"``."""
    adapter = _make_adapter("Thing", ["nickname"])
    node = FakeNode(node_id="1", kind="Thing", attrs={"nickname": None})
    data = adapter.infrahub_node_to_diffsync(node)
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
def test_ip_types_are_stringified(raw: Any, expected: str) -> None:
    """IP types: stringified intentionally — DiffSync models store them as str."""
    adapter = _make_adapter("Thing", ["address"])
    node = FakeNode(node_id="1", kind="Thing", attrs={"address": raw})
    data = adapter.infrahub_node_to_diffsync(node)
    assert data["address"] == expected
    assert isinstance(data["address"], str)


def test_field_not_in_schema_mapping_is_skipped() -> None:
    """``has_field`` filters out attributes not in the schema_mapping config."""
    adapter = _make_adapter("Thing", ["wanted"])  # only "wanted" is in the mapping
    node = FakeNode(node_id="1", kind="Thing", attrs={"wanted": "yes", "ignored": "no"})
    data = adapter.infrahub_node_to_diffsync(node)
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
    data = adapter.infrahub_node_to_diffsync(node)
    assert data == {
        "local_id": "cert-1",
        "serial": "abc123",
        "expiration": "2027-08-21T08:21:16Z",
        "sans": ["a.com", "b.com"],
        "key_size": "2048",
        "validation": None,
        "is_revoked": False,
    }
    # Explicit type checks on the ones that the original bug mangled.
    assert isinstance(data["sans"], list)
    assert isinstance(data["is_revoked"], bool)


# ---------------------------------------------------------------------------
# End-to-end: the real consumer of this method's output is
# ``DiffSyncModel(**data)`` inside ``InfrahubAdapter.load``. Validating the
# dict shape alone misses the layer that actually breaks in practice —
# Pydantic typed-field validation on the DiffSync model. These tests feed
# the adapter's output straight into a model and prove the model
# constructs without error, with values of the right Python type.
# ---------------------------------------------------------------------------


from diffsync import DiffSyncModel  # noqa: E402

from infrahub_sync import DiffSyncModelMixin  # noqa: E402


class TypedCertModel(DiffSyncModelMixin, DiffSyncModel):
    """A Pydantic-typed DiffSync model shaped like the real F5
    ``CertificateCertificate`` that triggered the original bug.

    The point: every attribute is typed *exactly* (``list[str]`` not
    ``list``; ``int`` not ``int | str``). Before the fix, the adapter's
    output failed Pydantic validation on the ``sans`` field. After the
    fix it constructs cleanly.
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

    This is what ``InfrahubAdapter.load`` does at runtime and the layer
    that the original bug actually broke. The other tests in this module
    check dict shape; this one checks the contract that matters:
    Pydantic-typed model construction.
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
            "port": 443,
            "enabled": True,
            "sans": ["app.example.com", "alt.example.com"],
            "metadata": {"issuer": "demo-ca", "chain_depth": 2},
            "validation": None,
        },
    )

    data = adapter.infrahub_node_to_diffsync(node)

    # This is the line that raises pydantic.ValidationError before the
    # fix and succeeds after.
    instance = TypedCertModel(**data)

    # Values survive intact.
    assert instance.serial == "abc123"
    assert instance.subject_dn == "CN=app.example.com"
    assert instance.port == 443
    assert instance.enabled is True
    assert instance.sans == ["app.example.com", "alt.example.com"]
    assert instance.metadata == {"issuer": "demo-ca", "chain_depth": 2}
    assert instance.validation is None

    # Types survive intact on the previously-mangled fields.
    assert isinstance(instance.sans, list)
    assert isinstance(instance.port, int)
    assert isinstance(instance.enabled, bool)
    assert isinstance(instance.metadata, dict)


def test_adapter_output_constructs_model_with_empty_list() -> None:
    """The original reported failure: empty list arriving as ``"[]"``.

    With the buggy adapter, ``TypedCertModel(**data)`` raises:
    ``ValidationError: 1 validation error for TypedCertModel
    sans: Input should be a valid list [type=list_type,
    input_value='[]', input_type=str]``
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
            "port": 443,
            "enabled": True,
            "sans": [],
            "metadata": {},
        },
    )

    data = adapter.infrahub_node_to_diffsync(node)
    instance = TypedCertModel(**data)

    assert instance.sans == []
    assert isinstance(instance.sans, list)
    assert instance.metadata == {}
    assert isinstance(instance.metadata, dict)
