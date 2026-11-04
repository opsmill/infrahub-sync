"""The destination read of a plan carries `updated_by` and `updated_at` from Infrahub 1.7.0.

On 1.7.0 or later, the existing destination reads ask for metadata with `include_metadata=True`, and
no read is added: cardinality-many edges come back in the same `client.all` read.
Before 1.7.0, or when the server version cannot be read or parsed, no read asks for metadata.

The metadata goes into the adapter's own map, keyed by destination node id and field name, and not
into the DiffSync models. So two plans over a value a person changed give the same operations.

The nodes here are real `InfrahubNodeSync` objects parsed from response data shaped like the SDK's
query with `include_metadata=True`; only the client's transport is replaced.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any, ClassVar

import httpx
import pytest
from diffsync import Adapter
from infrahub_sdk import Config, InfrahubClientSync
from infrahub_sdk.exceptions import AuthenticationError, GraphQLError
from infrahub_sdk.node import InfrahubNodeSync
from infrahub_sdk.schema.main import NodeSchemaAPI
from pydantic import ValidationError

from infrahub_sync import SchemaMappingField, SchemaMappingModel, SyncAdapter, SyncConfig
from infrahub_sync.adapters.infrahub import (
    IMPORT_ACCOUNT_QUERY,
    InfrahubAdapter,
    InfrahubModel,
    _latest_change,  # noqa: PLC2701 - the edge ordering under test is private.
    metadata_read_supported,
)
from infrahub_sync.cache.cursors import CursorState, CursorTier, capture_safe_cursors
from infrahub_sync.plan.last_changes import LastChange
from infrahub_sync.plan.models import LineageAccountRecord, RecordedAccountRecord

DEVICE_KIND = "LabDevice"
SITE_KIND = "LabSite"
TAG_KIND = "LabTag"
LOAD_ORDER = (SITE_KIND, TAG_KIND, DEVICE_KIND)

ALICE = {"id": "account-alice", "display_label": "Alice", "__typename": "CoreAccount"}
BOB = {"id": "account-bob", "display_label": "Bob", "__typename": "CoreAccount"}


# ---------------------------------------------------------------------------
# DiffSync models and the destination schema
# ---------------------------------------------------------------------------


class LabSite(InfrahubModel):
    _modelname = SITE_KIND
    _identifiers = ("name",)
    _attributes = ()
    name: str


class LabTag(InfrahubModel):
    _modelname = TAG_KIND
    _identifiers = ("name",)
    _attributes = ()
    name: str


class LabDevice(InfrahubModel):
    _modelname = DEVICE_KIND
    _identifiers = ("name",)
    _attributes = ("description", "site", "tags")
    name: str
    description: str | None = None
    site: str | None = None
    tags: list[str] = []  # noqa: RUF012 — a pydantic field default, copied for each model


def _attribute(name: str, *, optional: bool = True) -> dict[str, Any]:
    return {"name": name, "kind": "Text", "optional": optional}


def _relationship(name: str, peer: str, cardinality: str) -> dict[str, Any]:
    return {
        "name": name,
        "peer": peer,
        "cardinality": cardinality,
        "optional": True,
        "kind": "Generic",
        "identifier": f"device__{name}",
    }


def _schema(kind: str, attributes: list[dict[str, Any]], relationships: list[dict[str, Any]]) -> NodeSchemaAPI:
    return NodeSchemaAPI(
        id=f"{kind}-schema",
        name=kind.removeprefix("Lab"),
        namespace="Lab",
        label=kind,
        attributes=attributes,  # ty: ignore[invalid-argument-type]  # pydantic validates the dicts
        relationships=relationships,  # ty: ignore[invalid-argument-type]
    )


SCHEMAS = {
    SITE_KIND: _schema(SITE_KIND, [_attribute("name", optional=False)], []),
    TAG_KIND: _schema(TAG_KIND, [_attribute("name", optional=False)], []),
    DEVICE_KIND: _schema(
        DEVICE_KIND,
        [_attribute("name", optional=False), _attribute("description")],
        [_relationship("site", SITE_KIND, "one"), _relationship("tags", TAG_KIND, "many")],
    ),
}


def _config() -> SyncConfig:
    def model(kind: str, fields: list[str]) -> SchemaMappingModel:
        return SchemaMappingModel(
            name=kind,
            mapping=kind,
            identifiers=["name"],
            fields=[SchemaMappingField(name=name, mapping=name) for name in fields],
        )

    return SyncConfig(
        name="metadata-read",
        source=SyncAdapter(name="src", adapter="x:x"),
        destination=SyncAdapter(name="dst", adapter="x:x"),
        order=list(LOAD_ORDER),
        schema_mapping=[
            model(SITE_KIND, ["name"]),
            model(TAG_KIND, ["name"]),
            model(DEVICE_KIND, ["name", "description", "site", "tags"]),
        ],
    )


# ---------------------------------------------------------------------------
# Destination data: the device's description was last changed by a person
# ---------------------------------------------------------------------------


def _attribute_data(value: object, *, updated_by: dict[str, str] | None, updated_at: str | None) -> dict[str, Any]:
    return {"value": value, "updated_at": updated_at, "updated_by": updated_by}


def _edge(peer_id: str, peer_kind: str, *, updated_by: dict[str, str] | None, updated_at: str | None) -> dict:
    return {
        "node": {"id": peer_id, "__typename": peer_kind, "display_label": peer_id},
        "relationship_metadata": {"updated_at": updated_at, "updated_by": updated_by},
    }


def _rows(*, description_by: dict[str, str], description_at: str) -> dict[str, list[dict[str, Any]]]:
    """The destination's rows as Infrahub returns them with `include_metadata=True`."""
    name_at = "2026-10-01T08:00:00+00:00"
    return {
        SITE_KIND: [{"id": "site-1", "name": _attribute_data("paris", updated_by=ALICE, updated_at=name_at)}],
        TAG_KIND: [
            {"id": f"tag-{index}", "name": _attribute_data(f"tag-{index}", updated_by=ALICE, updated_at=name_at)}
            for index in (1, 2)
        ],
        DEVICE_KIND: [
            {
                "id": "device-1",
                "name": _attribute_data("edge-01", updated_by=ALICE, updated_at=name_at),
                "description": _attribute_data("set by hand", updated_by=description_by, updated_at=description_at),
                "site": _edge("site-1", SITE_KIND, updated_by=ALICE, updated_at="2026-10-02T08:00:00+00:00"),
                "tags": {
                    "count": 2,
                    "edges": [
                        _edge("tag-1", TAG_KIND, updated_by=ALICE, updated_at="2026-10-03T08:00:00+00:00"),
                        _edge("tag-2", TAG_KIND, updated_by=BOB, updated_at="2026-10-05T08:00:00+00:00"),
                    ],
                },
            },
            {
                # A value Infrahub holds no `updated_by` for, and a relationship with no peer.
                "id": "device-2",
                "name": _attribute_data("edge-02", updated_by=None, updated_at=None),
                "description": _attribute_data(None, updated_by=None, updated_at=None),
                "site": {"node": None},
                "tags": {"count": 0, "edges": []},
            },
        ],
    }


def _strip_metadata(value: object) -> object:
    """The same row as a read without `include_metadata` returns it."""
    if isinstance(value, dict):
        return {
            key: _strip_metadata(item)
            for key, item in value.items()
            if key not in {"updated_at", "updated_by", "relationship_metadata", "node_metadata"}
        }
    if isinstance(value, list):
        return [_strip_metadata(item) for item in value]
    return value


class MetadataClient(InfrahubClientSync):
    """A real SDK client whose server answers are fixed, with every destination call recorded."""

    def __init__(self, *, version: str | Exception = "1.11.9", rows: dict[str, list[dict[str, Any]]]) -> None:
        super().__init__(config=Config(address="http://localhost:8000", api_token="token"))  # noqa: S106
        self.version = version
        self.rows = rows
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def execute_graphql(self, *args: Any, **kwargs: Any) -> dict[str, Any]:  # noqa: ANN401, ARG002
        query = kwargs["query"]
        if query == IMPORT_ACCOUNT_QUERY:
            self.calls.append(("account_profile", {}))
            return {"AccountProfile": {"id": "account-import", "name": {"value": "import"}}}
        if "InfrahubInfo" in query:
            self.calls.append(("version", {}))
            if isinstance(self.version, Exception):
                raise self.version
            return {"InfrahubInfo": {"version": self.version}}
        msg = f"unexpected query {query!r}"
        raise AssertionError(msg)

    def _nodes(self, kind: str, *, include_metadata: bool, populate_store: bool) -> list[InfrahubNodeSync]:
        nodes = []
        for row in self.rows[kind]:
            data = row if include_metadata else _strip_metadata(row)
            node = InfrahubNodeSync(client=self, schema=SCHEMAS[kind], data={"__typename": kind, **data})  # ty: ignore[invalid-argument-type]
            if populate_store:
                self.store.set(key=str(node.id), node=node)
            nodes.append(node)
        return nodes

    def all(self, **kwargs: Any) -> list[InfrahubNodeSync]:  # ty: ignore[invalid-method-override]  # noqa: ANN401
        self.calls.append(("all", kwargs))
        return self._nodes(
            kwargs["kind"],
            include_metadata=kwargs.get("include_metadata", False),
            populate_store=kwargs.get("populate_store", True),
        )

    def filters(self, **kwargs: Any) -> list[InfrahubNodeSync]:  # ty: ignore[invalid-method-override]  # noqa: ANN401
        self.calls.append(("filters", kwargs))
        return self._nodes(
            kwargs["kind"],
            include_metadata=kwargs.get("include_metadata", False),
            populate_store=kwargs.get("populate_store", True),
        )

    def get(self, **kwargs: Any) -> Any:  # ty: ignore[invalid-method-override]  # noqa: ANN401, PLR6301
        msg = f"no peer is fetched again in these tests: {kwargs}"
        raise AssertionError(msg)


class Destination(InfrahubAdapter):
    """The Infrahub adapter with a real DiffSync store and no network setup."""

    LabSite = LabSite
    LabTag = LabTag
    LabDevice = LabDevice
    top_level: ClassVar[list[str]] = list(LOAD_ORDER)

    def __init__(self, client: MetadataClient) -> None:
        Adapter.__init__(self)
        self.target = "destination"
        self.config = _config()
        self.client = client
        self.schema = dict(SCHEMAS)
        self.continue_on_error = False
        self._peer_unique_ids = {}
        self.metadata_read = False
        self.last_changes = {}


class Source(Adapter):
    """A source that proposes a new description and the same peers for both devices."""

    LabSite = LabSite
    LabTag = LabTag
    LabDevice = LabDevice
    top_level: ClassVar[list[str]] = list(LOAD_ORDER)

    def load(self) -> None:
        self.add(LabSite(name="paris"))
        for index in (1, 2):
            self.add(LabTag(name=f"tag-{index}"))
        self.add(LabDevice(name="edge-01", description="from the source", site="paris", tags=["tag-1", "tag-2"]))
        self.add(LabDevice(name="edge-02", description="from the source"))


def _plan(client: MetadataClient) -> Destination:
    """Resolve lineage and load the destination, as the start of a plan does."""
    destination = Destination(client)
    destination.resolve_lineage()
    destination.resolve_metadata_read()
    for kind in LOAD_ORDER:
        destination.model_loader(model_name=kind, model=getattr(destination, kind))
    return destination


def _person_changed_rows() -> dict[str, list[dict[str, Any]]]:
    return _rows(description_by=BOB, description_at="2026-10-04T08:00:00+00:00")


# ---------------------------------------------------------------------------
# Which reads ask for metadata
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("version", "supported"),
    [
        ("1.7.0", True),
        ("1.7", True),
        ("1.11.9", True),
        ("2.0.0", True),
        ("1.6.9", False),
        ("1.6", False),
        ("", False),
        ("not-a-version", False),
    ],
)
def test_metadata_is_read_from_infrahub_1_7_0(version: str, *, supported: bool) -> None:
    assert metadata_read_supported(version) is supported


def test_on_1_7_0_or_later_the_existing_reads_ask_for_metadata_and_no_read_is_added() -> None:
    """One version read per plan; the same `all` reads, each with `include_metadata=True`."""
    with_metadata = MetadataClient(version="1.11.9", rows=_person_changed_rows())
    without_metadata = MetadataClient(version="1.6.0", rows=_person_changed_rows())

    assert _plan(with_metadata).metadata_read is True
    assert _plan(without_metadata).metadata_read is False

    reads = [call for call in with_metadata.calls if call[0] == "all"]
    assert [kwargs["kind"] for _, kwargs in reads] == list(LOAD_ORDER)
    assert all(kwargs["include_metadata"] is True for _, kwargs in reads)
    # The cardinality-many relationship comes back in the same read: it is in `include`.
    (device_read,) = [kwargs for _, kwargs in reads if kwargs["kind"] == DEVICE_KIND]
    assert device_read["include"] == ["name", "description", "site", "tags"]

    assert [name for name, _ in with_metadata.calls] == [name for name, _ in without_metadata.calls], (
        "metadata adds no request"
    )
    assert [name for name, _ in with_metadata.calls].count("version") == 1
    assert [{key: value for key, value in kwargs.items() if key != "include_metadata"} for _, kwargs in reads] == [
        {key: value for key, value in kwargs.items() if key != "include_metadata"}
        for name, kwargs in without_metadata.calls
        if name == "all"
    ], "the reads differ only by the metadata flag"


@pytest.mark.parametrize(
    "version",
    ["1.6.9", "", "not-a-version", GraphQLError(errors=[{"message": "Cannot query field 'InfrahubInfo'"}])],
    ids=["older", "empty", "unparsable", "unreadable"],
)
def test_before_1_7_0_or_with_an_unreadable_version_no_read_asks_for_metadata(version: str | Exception) -> None:
    """The plan continues without metadata; nothing is recorded in the map."""
    client = MetadataClient(version=version, rows=_person_changed_rows())

    destination = _plan(client)

    assert destination.metadata_read is False
    assert all(kwargs["include_metadata"] is False for name, kwargs in client.calls if name == "all")
    assert destination.last_changes == {}


def _authentication_error(status: int, message: str) -> AuthenticationError:
    """The `AuthenticationError` the SDK raises for `status`, chained from the response as the SDK chains it."""
    request = httpx.Request("POST", "http://infrahub.invalid/graphql", headers={"X-INFRAHUB-KEY": VERSION_TOKEN})
    response = httpx.Response(status, json={"errors": [{"message": message}]}, request=request)
    error = AuthenticationError(message)
    error.__cause__ = httpx.HTTPStatusError(message, request=request, response=response)
    return error


VERSION_TOKEN = "version-token-canary-71c2"  # noqa: S105 -- a canary, not a credential


def test_a_version_query_refused_with_http_403_continues_without_metadata(caplog: pytest.LogCaptureFixture) -> None:
    """A missing permission on the version query is "cannot read the version", so the plan continues."""
    refusal = _authentication_error(403, "You do not have the following permission: global:view_info:allow_all")
    client = MetadataClient(version=refusal, rows=_person_changed_rows())

    with caplog.at_level(logging.WARNING, logger="infrahub_sync.adapters.infrahub"):
        destination = _plan(client)

    assert destination.metadata_read is False
    assert destination.last_changes == {}
    (record,) = [record for record in caplog.records if "server version query" in record.getMessage()]
    assert "the permission `global:view_info:allow_all`" in record.getMessage()
    assert VERSION_TOKEN not in caplog.text


def test_a_version_query_refused_with_http_401_stops_the_plan() -> None:
    """A refused token refuses every later read too, so the plan stops at the version query."""
    client = MetadataClient(version=_authentication_error(401, "Invalid token"), rows=_person_changed_rows())

    with pytest.raises(AuthenticationError):
        _plan(client)


def test_a_refused_version_query_is_logged_without_the_error_text(caplog: pytest.LogCaptureFixture) -> None:
    """The SDK's `GraphQLError` text can hold the whole query and response, so only its class is logged."""
    body = f"query {{ InfrahubInfo {{ version }} }} {VERSION_TOKEN}"
    client = MetadataClient(version=GraphQLError(errors=[{"message": body}], query=body), rows=_person_changed_rows())

    with caplog.at_level(logging.WARNING, logger="infrahub_sync.adapters.infrahub"):
        destination = _plan(client)

    assert destination.metadata_read is False
    (record,) = [record for record in caplog.records if "server version query" in record.getMessage()]
    assert record.getMessage().endswith(": GraphQLError")
    assert VERSION_TOKEN not in caplog.text


def test_an_apply_does_not_read_the_server_version() -> None:
    """Apply loads nothing, so it needs no metadata and makes no version read."""
    client = MetadataClient(rows=_person_changed_rows())
    destination = Destination(client)
    destination.metadata_read = True
    saved_plan = SimpleNamespace(
        run_id="20261023T0900-0a1b2c3d",
        lineage=SimpleNamespace(import_account=LineageAccountRecord(name="import", id="account-import")),
    )

    destination.resolve_lineage(saved_plan=saved_plan)  # ty: ignore[invalid-argument-type]
    destination.resolve_metadata_read()

    assert client.calls == [("account_profile", {})]
    assert destination.metadata_read is False


# ---------------------------------------------------------------------------
# What the map holds
# ---------------------------------------------------------------------------


def test_a_value_infrahub_returns_with_updated_by_is_recorded_for_it() -> None:
    """Attributes, a cardinality-one edge and cardinality-many edges, from the one read."""
    destination = _plan(MetadataClient(rows=_person_changed_rows()))

    alice = RecordedAccountRecord(id="account-alice", display_label="Alice")
    bob = RecordedAccountRecord(id="account-bob", display_label="Bob")
    changes = destination.last_changes
    assert changes["device-1", "name"] == LastChange(updated_by=alice, updated_at="2026-10-01T08:00:00+00:00")
    assert changes["device-1", "description"] == LastChange(updated_by=bob, updated_at="2026-10-04T08:00:00+00:00")
    assert changes["device-1", "site"] == LastChange(updated_by=alice, updated_at="2026-10-02T08:00:00+00:00")
    # The relationship's last change is its most recently changed edge.
    assert changes["device-1", "tags"] == LastChange(updated_by=bob, updated_at="2026-10-05T08:00:00+00:00")
    assert all(
        change.updated_by is not None and change.updated_at is not None
        for (node_id, _), change in changes.items()
        if node_id != "device-2"
    ), "no value Infrahub returned with `updated_by` is left without it"


def test_each_account_is_validated_once_per_load_and_its_record_shared() -> None:
    """Values one account changed share one frozen record, so a load does not validate per field."""
    destination = _plan(MetadataClient(rows=_person_changed_rows()))

    changes = destination.last_changes
    alice_name = changes["device-1", "name"].updated_by
    alice_site = changes["device-1", "site"].updated_by
    assert alice_name is not None
    assert alice_name is alice_site
    with pytest.raises(ValidationError):
        alice_name.display_label = "Mallory"


@pytest.mark.parametrize(
    "latest_at",
    [
        pytest.param("2026-10-05T08:00:00.123456789Z", id="nanoseconds"),
        pytest.param("2026-10-05T08:00:00.5Z", id="one-digit"),
        pytest.param("2026-10-05T08:00:00.1234+00:00", id="four-digits"),
    ],
)
def test_the_latest_edge_is_found_whatever_the_precision_of_its_time(latest_at: str) -> None:
    """Every supported Python, 3.10 included, orders edge times whose fraction is not 3 or 6 digits."""
    alice = RecordedAccountRecord(id="account-alice", display_label="Alice")
    bob = RecordedAccountRecord(id="account-bob", display_label="Bob")
    earlier = LastChange(updated_by=alice, updated_at="2026-10-01T08:00:00.000001+00:00")
    latest = LastChange(updated_by=bob, updated_at=latest_at)

    assert _latest_change([earlier, latest]) == latest
    assert _latest_change([latest, earlier]) == latest


def test_two_edges_changed_within_one_microsecond_are_ordered_by_their_nanoseconds() -> None:
    """Cutting the time to microseconds would make these two edges equal and keep the first."""
    alice = RecordedAccountRecord(id="account-alice", display_label="Alice")
    bob = RecordedAccountRecord(id="account-bob", display_label="Bob")
    earlier = LastChange(updated_by=alice, updated_at="2026-10-05T08:00:00.123456001Z")
    latest = LastChange(updated_by=bob, updated_at="2026-10-05T08:00:00.123456002Z")

    assert _latest_change([earlier, latest]) == latest
    assert _latest_change([latest, earlier]) == latest


def test_a_value_infrahub_holds_no_metadata_for_is_recorded_as_empty() -> None:
    """A field with no `updated_by` in the answer is kept as `None`, which the plan shows as not recorded."""
    destination = _plan(MetadataClient(rows=_person_changed_rows()))

    empty = LastChange(updated_by=None, updated_at=None)
    for field in ("name", "description", "site", "tags"):
        assert destination.last_changes["device-2", field] == empty


def test_only_mapped_fields_of_the_loaded_nodes_are_recorded() -> None:
    destination = _plan(MetadataClient(rows=_person_changed_rows()))

    assert sorted(destination.last_changes) == sorted(
        [
            ("site-1", "name"),
            ("tag-1", "name"),
            ("tag-2", "name"),
            *(
                (device, field)
                for device in ("device-1", "device-2")
                for field in ("name", "description", "site", "tags")
            ),
        ]
    )


# ---------------------------------------------------------------------------
# Metadata stays out of the DiffSync models
# ---------------------------------------------------------------------------


def _operations(destination: Destination) -> dict[str, Any]:
    source = Source()
    source.load()
    return destination.diff_from(source).dict()


def test_metadata_does_not_enter_the_models_so_two_plans_give_the_same_operations() -> None:
    """A value a person changed is compared by its value only, so two plans give the same operations."""
    first = _plan(MetadataClient(rows=_person_changed_rows()))
    # Between the two plans another person touched the description again, leaving the same value.
    second = _plan(MetadataClient(rows=_rows(description_by=ALICE, description_at="2026-10-06T08:00:00+00:00")))
    without = _plan(MetadataClient(version="1.6.0", rows=_person_changed_rows()))

    assert first.last_changes["device-1", "description"] != second.last_changes["device-1", "description"]
    assert first.dict() == second.dict() == without.dict()
    assert _operations(first) == _operations(second) == _operations(without)
    assert _operations(first), "the source proposes changes, so the comparison is not empty"
    model_fields = set(LabDevice.model_fields)
    assert not model_fields & {"updated_by", "updated_at", "last_changes"}


# ---------------------------------------------------------------------------
# Incremental loading
# ---------------------------------------------------------------------------


def test_with_metadata_the_destination_is_loaded_in_full() -> None:
    """No incremental load while metadata is read: unchanged nodes would come from a snapshot without it.

    So when metadata is read, every kind reports no cursor tier, no cursor is captured, and the
    engine loads each kind with `model_loader` (the `client.all` read that carries metadata).
    """
    destination = _plan(MetadataClient(rows=_person_changed_rows()))

    assert {destination.cursor_tier_for(kind) for kind in LOAD_ORDER} == {CursorTier.NONE}
    assert capture_safe_cursors(destination, LOAD_ORDER) == {}

    older = _plan(MetadataClient(version="1.6.0", rows=_person_changed_rows()))
    assert {older.cursor_tier_for(kind) for kind in LOAD_ORDER} == {CursorTier.TIMESTAMP}


def test_the_incremental_read_asks_for_metadata_when_metadata_is_read() -> None:
    destination = _plan(MetadataClient(rows=_person_changed_rows()))
    client = destination.client
    assert isinstance(client, MetadataClient)
    client.calls.clear()
    destination.last_changes.clear()

    cursor = CursorState(tier=CursorTier.TIMESTAMP, value="2026-10-01T00:00:00+00:00", safe=True)
    list(destination.list_changed_since(SITE_KIND, cursor))

    ((name, kwargs),) = client.calls
    assert name == "filters"
    assert kwargs["include_metadata"] is True
    assert ("site-1", "name") in destination.last_changes
