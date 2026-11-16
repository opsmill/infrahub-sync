"""The Infrahub destination keeps the ids of the nodes Infrahub created itself (spec 019 T009).

The mark is the one research R2 chose from the probe of T001: the creator `__system__` in the
node metadata. The nodes are built from the probe output of a new Infrahub 1.11.9 branch,
`tests/data/infrahub_builtin_nodes/infrahub-1.11.9.json`: the `IpamNamespace` named `default`
that Infrahub created, and a control `BuiltinTag` that the API token created.

The client is a real SDK client whose server answers are fixed, as in
`tests/adapters/test_infrahub_metadata_read.py`, because only real SDK nodes parse the
`node_metadata` block. A read without `include_metadata` returns no node metadata, as Infrahub
does.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, ClassVar

from diffsync import Adapter
from infrahub_sdk import Config, InfrahubClientSync
from infrahub_sdk.node import InfrahubNodeSync
from infrahub_sdk.schema.main import NodeSchemaAPI

from infrahub_sync import SchemaMappingField, SchemaMappingModel, SyncAdapter, SyncConfig
from infrahub_sync.adapters.infrahub import INFRAHUB_SYSTEM_ACCOUNT_ID, InfrahubAdapter, InfrahubModel
from infrahub_sync.plan.write_surface import destination_builtin_node_ids

PROBE = json.loads(
    (Path(__file__).resolve().parent.parent / "data" / "infrahub_builtin_nodes" / "infrahub-1.11.9.json").read_text(
        encoding="utf-8"
    )
)

NAMESPACE_KIND = "IpamNamespace"
TAG_KIND = "BuiltinTag"
LOAD_ORDER = (NAMESPACE_KIND, TAG_KIND)

(DEFAULT_NAMESPACE,) = PROBE["nodes_by_kind"][NAMESPACE_KIND]
CONTROL_TAG = PROBE["control_node_created_through_api"]


class IpamNamespace(InfrahubModel):
    _modelname = NAMESPACE_KIND
    _identifiers = ("name",)
    _attributes = ()
    name: str


class BuiltinTag(InfrahubModel):
    _modelname = TAG_KIND
    _identifiers = ("name",)
    _attributes = ()
    name: str


def _schema(kind: str) -> NodeSchemaAPI:
    namespace, name = ("Ipam", "Namespace") if kind == NAMESPACE_KIND else ("Builtin", "Tag")
    return NodeSchemaAPI(
        id=f"{kind}-schema",
        name=name,
        namespace=namespace,
        label=kind,
        attributes=[{"name": "name", "kind": "Text", "optional": False}],  # ty: ignore[invalid-argument-type]
        relationships=[],
    )


SCHEMAS = {kind: _schema(kind) for kind in LOAD_ORDER}


def _account(probe_account: dict[str, str]) -> dict[str, str]:
    """A probe account in the shape of the GraphQL `node_metadata` block."""
    return {"id": probe_account["id"], "display_label": probe_account["display_label"], "__typename": "CoreAccount"}


def _row(probe_node: dict[str, Any]) -> dict[str, Any]:
    """One node of the probe as Infrahub returns it with `include_metadata=True`."""
    return {
        "id": probe_node["id"],
        "name": {"value": probe_node["display_label"]},
        "node_metadata": {
            "created_at": probe_node["created_at"],
            "created_by": _account(probe_node["created_by"]),
            "updated_at": probe_node["updated_at"],
            "updated_by": _account(probe_node["updated_by"]),
        },
    }


def _probe_rows() -> dict[str, list[dict[str, Any]]]:
    return {NAMESPACE_KIND: [_row(DEFAULT_NAMESPACE)], TAG_KIND: [_row(CONTROL_TAG)]}


class ProbeClient(InfrahubClientSync):
    """A real SDK client that answers the version query and returns the probe's nodes."""

    def __init__(self, *, version: str, rows: dict[str, list[dict[str, Any]]]) -> None:
        super().__init__(config=Config(address="http://localhost:8000", api_token="token"))  # noqa: S106
        self.version = version
        self.rows = rows
        self.reads: list[dict[str, Any]] = []

    def execute_graphql(self, *args: Any, **kwargs: Any) -> dict[str, Any]:  # noqa: ANN401, ARG002
        query = kwargs["query"]
        if "InfrahubInfo" in query:
            return {"InfrahubInfo": {"version": self.version}}
        msg = f"unexpected query {query!r}"
        raise AssertionError(msg)

    def all(self, **kwargs: Any) -> list[InfrahubNodeSync]:  # ty: ignore[invalid-method-override]  # noqa: ANN401
        self.reads.append(kwargs)
        kind = kwargs["kind"]
        nodes = []
        for row in self.rows[kind]:
            data = dict(row)
            if not kwargs.get("include_metadata"):
                data.pop("node_metadata", None)
            node = InfrahubNodeSync(client=self, schema=SCHEMAS[kind], data={"__typename": kind, **data})
            self.store.set(key=str(node.id), node=node)
            nodes.append(node)
        return nodes


def _config() -> SyncConfig:
    return SyncConfig(
        name="builtin-nodes",
        source=SyncAdapter(name="src", adapter="x:x"),
        destination=SyncAdapter(name="dst", adapter="x:x"),
        order=list(LOAD_ORDER),
        schema_mapping=[
            SchemaMappingModel(
                name=kind,
                mapping=kind,
                identifiers=["name"],
                fields=[SchemaMappingField(name="name", mapping="name")],
            )
            for kind in LOAD_ORDER
        ],
    )


class Destination(InfrahubAdapter):
    """The Infrahub adapter with a real DiffSync store and no network setup."""

    IpamNamespace = IpamNamespace
    BuiltinTag = BuiltinTag
    top_level: ClassVar[list[str]] = list(LOAD_ORDER)

    def __init__(self, client: ProbeClient, *, target: str = "destination") -> None:
        Adapter.__init__(self)
        self.target = target
        self.config = _config()
        self.client = client
        self.schema = dict(SCHEMAS)
        self._peer_unique_ids = {}
        self.metadata_read = False
        self.last_changes = {}


def _load(client: ProbeClient, *, target: str = "destination") -> Destination:
    """Decide the metadata read and load every kind, as the start of a plan does."""
    adapter = Destination(client, target=target)
    adapter.resolve_metadata_read()
    for kind in LOAD_ORDER:
        adapter.model_loader(model_name=kind, model=getattr(adapter, kind))
    return adapter


def test_the_probe_records_the_chosen_mark() -> None:
    """The fixture holds the mark R2 chose; the control node does not carry it."""
    assert DEFAULT_NAMESPACE["created_by"]["id"] == INFRAHUB_SYSTEM_ACCOUNT_ID
    assert CONTROL_TAG["created_by"]["id"] != INFRAHUB_SYSTEM_ACCOUNT_ID


def test_the_destination_keeps_the_id_of_each_node_infrahub_created() -> None:
    destination = _load(ProbeClient(version="1.11.9", rows=_probe_rows()))

    assert destination.metadata_read is True
    assert destination.builtin_node_ids == frozenset({DEFAULT_NAMESPACE["id"]})
    assert destination_builtin_node_ids(destination) == frozenset({DEFAULT_NAMESPACE["id"]})


def test_the_node_the_api_token_created_is_not_built_in() -> None:
    destination = _load(ProbeClient(version="1.11.9", rows=_probe_rows()))

    assert CONTROL_TAG["id"] not in destination.builtin_node_ids


def test_a_node_without_a_creator_is_not_built_in() -> None:
    rows = _probe_rows()
    del rows[NAMESPACE_KIND][0]["node_metadata"]["created_by"]

    destination = _load(ProbeClient(version="1.11.9", rows=rows))

    assert destination.builtin_node_ids == frozenset()


def test_the_mark_adds_no_read() -> None:
    """The mark comes from the reads that already ask for metadata; no request is added."""
    client = ProbeClient(version="1.11.9", rows=_probe_rows())
    _load(client)

    assert [read["kind"] for read in client.reads] == list(LOAD_ORDER)
    assert all(read["include_metadata"] is True for read in client.reads)


def test_without_metadata_no_node_is_built_in() -> None:
    """Before Infrahub 1.7.0 the reads ask for no metadata, so the set stays empty."""
    destination = _load(ProbeClient(version="1.6.0", rows=_probe_rows()))

    assert destination.metadata_read is False
    assert destination.builtin_node_ids == frozenset()


def test_a_source_keeps_no_builtin_set() -> None:
    source = _load(ProbeClient(version="1.11.9", rows=_probe_rows()), target="source")

    assert source.builtin_node_ids == frozenset()


def test_the_mark_does_not_enter_the_model_data() -> None:
    destination = _load(ProbeClient(version="1.11.9", rows=_probe_rows()))

    (namespace,) = destination.get_all(NAMESPACE_KIND)
    assert namespace.get_attrs() == {}
    assert namespace.get_identifiers() == {"name": "default"}
    assert namespace.model_dump()["local_id"] == DEFAULT_NAMESPACE["id"]
    assert not any("created" in name or "metadata" in name for name in type(namespace).model_fields)
