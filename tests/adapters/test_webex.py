"""Configurable Webex endpoint loading and shared mapping behavior."""

from __future__ import annotations

import json
from typing import Any, cast
from unittest.mock import Mock
from urllib.parse import urlsplit

import pytest
import requests
from pydantic import Field
from structlog.testing import capture_logs

from infrahub_sync import (
    SchemaMappingField,
    SchemaMappingFilter,
    SchemaMappingModel,
    SchemaMappingTransform,
    SyncAdapter,
    SyncConfig,
)
from infrahub_sync.adapters.webex import WebexAdapter, WebexModel
from infrahub_sync.adapters.webex_client import WebexHTTPError, WebexResource


class Record(WebexModel):
    _modelname = "Record"
    _identifiers = ("name",)
    _attributes = ("description", "parent", "enabled")
    name: str
    description: str | None = None
    parent: str | None = None
    enabled: bool = True
    local_id: str | None = None


class LocationSite(Record):
    _modelname = "LocationSite"


class RoomWithPeers(Record):
    _attributes = (*Record._attributes, "peers")
    # The shared mapper detects list fields from their default value.
    peers: list[str] = Field(default=[])


def _load_locations(adapter: WebexAdapter) -> None:
    adapter.config.schema_mapping.insert(
        0,
        SchemaMappingModel(
            name="LocationSite",
            mapping="/v1/locations",
            fields=[SchemaMappingField(name="name", mapping="code")],
            filters=[SchemaMappingFilter(field="notes", operation="regex", value="^ABC[0-9]+$")],
        ),
    )
    adapter.client.options.resources["LocationSite"] = WebexResource(read_only=True)
    adapter.client.pages: Any = Mock(
        return_value=[
            {
                "items": [
                    {"id": "location-1", "code": "site-key", "notes": "ABC0000001"},
                    {"id": "location-2", "code": "filtered-key", "notes": "other"},
                ]
            }
        ]
    )
    adapter.model_loader("LocationSite", LocationSite)


@pytest.mark.parametrize("enabled", [True, False])
def test_skip_filtered_location_references(adapter: WebexAdapter, *, enabled: bool) -> None:
    _load_locations(adapter)
    adapter.config.schema_mapping[-1].fields.append(
        SchemaMappingField(name="parent", mapping="locationId", reference="LocationSite")
    )
    adapter.client.options.resources["Record"] = WebexResource(skip_unresolved_references=enabled)
    pages = Mock(
        return_value=[
            {
                "items": [
                    {"id": "room-1", "displayName": "ROOM-1", "locationId": "site-key"},
                    {"id": "room-2", "displayName": "ROOM-2", "locationId": "filtered-key"},
                    {"id": "room-3", "displayName": "ROOM-3", "locationId": "filtered-key"},
                ]
            }
        ]
    )
    adapter.client.pages: Any = pages
    with capture_logs() as logs:
        adapter.model_loader("Record", Record)
    assert len(adapter.get_all("Record")) == (1 if enabled else 3)
    assert cast("Record", adapter.get("Record", "ROOM-1")).parent == "site-key"
    pages.assert_called_once()
    warnings = [event for event in logs if event["log_level"] == "warning"]
    assert len(warnings) == int(enabled)
    if enabled:
        assert warnings[0]["model"] == "Record"
        assert warnings[0]["dropped"] == 2
        assert warnings[0]["list_items_removed"] == 0
        assert warnings[0]["unresolved_values"] == ["filtered-key"]
        assert adapter._records["Record"] == [pages.return_value[0]["items"][0]]


@pytest.mark.parametrize("value", [None, "", [], {}, "site-key", {"id": "location-1"}])
def test_resolved_or_empty_reference_kept(value: object, adapter: WebexAdapter) -> None:
    _load_locations(adapter)
    adapter.config.schema_mapping[-1].fields.append(
        SchemaMappingField(name="parent", mapping="peer", reference="LocationSite")
    )
    adapter.client.options.resources["Record"] = WebexResource(skip_unresolved_references=True)
    adapter.client.pages: Any = Mock(
        return_value=[{"items": [{"id": "room-1", "displayName": "ROOM-1", "peer": value}]}]
    )
    with capture_logs() as logs:
        adapter.model_loader("Record", Record)
    assert len(adapter.get_all("Record")) == 1
    assert not [event for event in logs if event["log_level"] == "warning"]


@pytest.mark.parametrize("value", ["location-1", {"id": "site-key"}, {"id": "location-2"}])
def test_reference_identifier_forms_are_distinct(value: object, adapter: WebexAdapter) -> None:
    _load_locations(adapter)
    adapter.config.schema_mapping[-1].fields.append(
        SchemaMappingField(name="parent", mapping="peer", reference="LocationSite")
    )
    adapter.client.options.resources["Record"] = WebexResource(skip_unresolved_references=True)
    adapter.client.pages: Any = Mock(
        return_value=[{"items": [{"id": "room-1", "displayName": "ROOM-1", "peer": value}]}]
    )
    adapter.model_loader("Record", Record)
    assert not adapter.get_all("Record")


@pytest.mark.parametrize(
    ("items", "expected", "removed"),
    [
        ([{"id": "location-1"}], ["site-key"], 0),
        (["site-key"], ["site-key"], 0),
        ([{"id": "location-1"}, {"id": "location-2"}], ["site-key"], 1),
        (["site-key", "filtered-key"], ["site-key"], 1),
        ([{"id": "location-2"}], [], 1),
        (["filtered-key", "filtered-key"], [], 2),
        ([], [], 0),
        ([None, "", {}], [], 0),
        ([None, "site-key", "", {}, "filtered-key"], ["site-key"], 1),
    ],
)
def test_list_references_keep_resolved_items(
    items: list[Any], expected: list[str], removed: int, adapter: WebexAdapter
) -> None:
    _load_locations(adapter)
    mapping = adapter.config.schema_mapping[-1]
    mapping.fields.append(SchemaMappingField(name="peers", mapping="nested.peers", reference="LocationSite"))
    mapping.transforms = [SchemaMappingTransform(field="nested", expression="{{ {'peers': sourcePeers} }}")]
    adapter.client.options.resources["Record"] = WebexResource(skip_unresolved_references=True)
    adapter.client.pages: Any = Mock(
        return_value=[{"items": [{"id": "room-1", "displayName": "ROOM-1", "sourcePeers": items}]}]
    )
    with capture_logs() as logs:
        adapter.model_loader("Record", RoomWithPeers)
    assert len(adapter.get_all("Record")) == 1
    assert cast("RoomWithPeers", adapter.get("Record", "ROOM-1")).peers == expected
    assert adapter._records["Record"][0]["nested"]["peers"] == [
        {"id": "location-1"} if item else item
        for item in items
        if not item or item in ("site-key", {"id": "location-1"})
    ]
    warnings = [event for event in logs if event["log_level"] == "warning"]
    assert len(warnings) == int(removed > 0)
    if removed:
        assert warnings[0]["dropped"] == 0
        assert warnings[0]["list_items_removed"] == removed
        assert warnings[0]["unresolved_values"] == sorted(
            {
                str(item["id"]) if isinstance(item, dict) else str(item)
                for item in items
                if item and item not in ("site-key", {"id": "location-1"})
            }
        )


def test_unloaded_reference_requires_order_even_with_no_records(adapter: WebexAdapter) -> None:
    adapter.config.schema_mapping[0].fields.append(
        SchemaMappingField(name="parent", mapping="peer", reference="LocationSite")
    )
    adapter.client.options.resources["Record"] = WebexResource(skip_unresolved_references=True)
    adapter.client.pages: Any = Mock(return_value=[{"items": []}])
    with pytest.raises(ValueError, match=r"Record.*LocationSite must load before.*list LocationSite earlier"):
        adapter.model_loader("Record", Record)


def test_loaded_empty_reference_mapping_drops_nonempty_peers(adapter: WebexAdapter) -> None:
    _load_locations(adapter)
    adapter.remove(adapter.get("LocationSite", "site-key"))
    adapter._records["LocationSite"] = []
    adapter.config.schema_mapping[-1].fields.append(
        SchemaMappingField(name="parent", mapping="peer", reference="LocationSite")
    )
    adapter.client.options.resources["Record"] = WebexResource(skip_unresolved_references=True)
    adapter.client.pages: Any = Mock(
        return_value=[{"items": [{"id": "room-1", "displayName": "ROOM-1", "peer": "site-key"}]}]
    )
    adapter.model_loader("Record", Record)
    assert not adapter.get_all("Record")


def test_self_reference_is_not_checked(adapter: WebexAdapter) -> None:
    adapter.config.schema_mapping[0].fields.append(
        SchemaMappingField(name="parent", mapping="parentId", reference="Record")
    )
    adapter.client.options.resources["Record"] = WebexResource(skip_unresolved_references=True)
    adapter.client.pages: Any = Mock(
        return_value=[{"items": [{"id": "room-1", "displayName": "ROOM-1", "parentId": "ROOM-0"}]}]
    )
    with capture_logs() as logs:
        adapter.model_loader("Record", Record)
    assert cast("Record", adapter.get("Record", "ROOM-1")).parent == "ROOM-0"
    assert not [event for event in logs if event["log_level"] == "warning"]


def test_dict_reference_without_id_logs_the_dict(adapter: WebexAdapter) -> None:
    _load_locations(adapter)
    adapter.config.schema_mapping[-1].fields.append(
        SchemaMappingField(name="parent", mapping="peer", reference="LocationSite")
    )
    adapter.client.options.resources["Record"] = WebexResource(skip_unresolved_references=True)
    adapter.client.pages: Any = Mock(
        return_value=[{"items": [{"id": "room-1", "displayName": "ROOM-1", "peer": {"name": "x"}}]}]
    )
    with capture_logs() as logs:
        adapter.model_loader("Record", Record)
    assert not adapter.get_all("Record")
    warning = next(event for event in logs if event["log_level"] == "warning")
    assert warning["unresolved_values"] == ["{'name': 'x'}"]


def test_list_rewrite_leaves_source_record_unchanged(adapter: WebexAdapter) -> None:
    _load_locations(adapter)
    mapping = adapter.config.schema_mapping[-1]
    mapping.fields.append(SchemaMappingField(name="peers", mapping="nested.peers", reference="LocationSite"))
    adapter.client.options.resources["Record"] = WebexResource(skip_unresolved_references=True)
    source = {"id": "room-1", "displayName": "ROOM-1", "nested": {"peers": ["site-key", "filtered-key"]}}
    adapter.client.pages: Any = Mock(return_value=[{"items": [source]}])
    adapter.model_loader("Record", RoomWithPeers)
    assert source["nested"]["peers"] == ["site-key", "filtered-key"]
    assert adapter._records["Record"][0]["nested"]["peers"] == [{"id": "location-1"}]


def test_warning_truncates_distinct_values(adapter: WebexAdapter) -> None:
    _load_locations(adapter)
    adapter.config.schema_mapping[-1].fields.append(
        SchemaMappingField(name="parent", mapping="peer", reference="LocationSite")
    )
    adapter.client.options.resources["Record"] = WebexResource(skip_unresolved_references=True)
    adapter.client.pages: Any = Mock(
        return_value=[
            {"items": [{"id": f"room-{i}", "displayName": f"ROOM-{i}", "peer": f"absent-{i}"} for i in range(25)]}
        ]
    )
    with capture_logs() as logs:
        adapter.model_loader("Record", Record)
    warning = next(event for event in logs if event["log_level"] == "warning")
    assert warning["dropped"] == 25
    assert warning["list_items_removed"] == 0
    assert len(warning["unresolved_values"]) == 20
    assert warning["unresolved_values_truncated"] is True


@pytest.fixture(name="adapter")
def _adapter_fixture(monkeypatch: pytest.MonkeyPatch) -> WebexAdapter:
    monkeypatch.setenv("WEBEX_TOKEN", "invented-access-value")
    config = SyncConfig.model_validate(
        {
            "name": "configurable-resource",
            "source": {"name": "webex"},
            "destination": {"name": "infrahub"},
            "schema_mapping": [
                {
                    "name": "Record",
                    "mapping": "/v1/example",
                    "fields": [
                        {"name": "name", "mapping": "displayName"},
                        {"name": "description", "mapping": "description"},
                    ],
                }
            ],
        }
    )
    return WebexAdapter("source", config.source, config)


@pytest.mark.parametrize(
    ("payload", "key"),
    [
        ({"records": [{"id": "1", "displayName": "example"}]}, "records"),
        ({"id": "1", "displayName": "example"}, None),
        ([{"id": "1", "displayName": "example"}], None),
    ],
)
def test_alternate_response_shapes(payload: object, key: str | None, adapter: WebexAdapter) -> None:

    adapter.client.options.resources["Record"] = WebexResource(response_key=key)
    adapter.client.pages: Any = Mock(return_value=[payload])
    adapter.model_loader("Record", Record)
    assert cast("Record", adapter.get("Record", "example")).local_id == "1"


@pytest.mark.parametrize("payload", [{"wrong": []}, {"items": ["invalid"]}, {"items": None}])
def test_malformed_response_fails(payload: object, adapter: WebexAdapter) -> None:
    adapter.client.pages: Any = Mock(return_value=[payload])
    with pytest.raises(ValueError, match=r"Webex.*Record"):
        adapter.model_loader("Record", Record)


def test_parent_endpoint_and_query_with_empty_and_404_skips(adapter: WebexAdapter) -> None:

    adapter.config.schema_mapping[0].mapping = "/v1/locations/{id}/example"
    adapter._records["Sites"] = [{"id": "a/b", "code": "example code"}, {"id": "empty"}, {"id": "absent", "code": "x"}]
    adapter.client.options.resources["Record"] = WebexResource(parent="Sites", params={"location": {"parent": "code"}})
    pages = Mock(
        side_effect=[
            [{"items": [{"id": "room-1", "displayName": "example"}]}],
            WebexHTTPError(404, "/v1/locations/absent/example", "absent"),
        ]
    )
    adapter.client.pages: Any = pages
    adapter.model_loader("Record", Record)
    assert pages.call_count == 2
    assert pages.call_args_list[0].args == ("/v1/locations/a%2Fb/example", {"location": "example code", "max": "100"})
    assert len(adapter.get_all("Record")) == 1


def test_parent_requires_explicit_load_order(adapter: WebexAdapter) -> None:

    adapter.client.options.resources["Record"] = WebexResource(parent="Sites")
    with pytest.raises(ValueError, match="loaded first"):
        adapter.model_loader("Record", Record)


def test_unknown_model_name_raises(adapter: WebexAdapter) -> None:
    with pytest.raises(ValueError, match="no schema mapping"):
        adapter.model_loader("Unmapped", Record)


def test_detail_merge_precedes_shared_filter_and_transform(adapter: WebexAdapter) -> None:

    mapping = adapter.config.schema_mapping[0]
    mapping.filters = [SchemaMappingFilter(field="description", operation="==", value="from detail")]
    mapping.transforms = [SchemaMappingTransform(field="displayName", expression="{{ displayName.lower() }}")]
    adapter.client.options.resources["Record"] = WebexResource(details="/v1/example/{id}")
    adapter.client.pages: Any = Mock(return_value=[{"items": [{"id": "one", "displayName": "EXAMPLE"}]}])
    get = Mock(return_value={"description": "from detail"})
    adapter.client.get: Any = get
    adapter.model_loader("Record", Record)
    assert cast("Record", adapter.get("Record", "example")).description == "from detail"
    get.assert_called_once_with("/v1/example/one", params={})


def test_query_values_and_overrides(adapter: WebexAdapter) -> None:

    adapter.client.options.params = {"custom": "default", "enabled": True, "types": ["a", "b"], "removed": "value"}
    adapter.client.options.org_id = "example-org"
    options = WebexResource(page_size=1000, params={"custom": "A & B", "removed": None})
    assert adapter._params(options, {}) == {
        "custom": "A & B",
        "enabled": "true",
        "types": "a,b",
        "orgId": "example-org",
        "max": "1000",
        "removed": None,
    }


def test_native_dict_reference_uses_source_local_id(adapter: WebexAdapter) -> None:

    class Peer(Record):
        _modelname = "Peer"

    adapter.add(Peer(name="site-key", local_id="location-1"))
    mapping = adapter.config.schema_mapping[0]
    mapping.fields.append(SchemaMappingField(name="parent", mapping="peer", reference="Peer"))
    mapping.transforms = [SchemaMappingTransform(field="peer", expression="{{ {'id': locationId} }}")]
    adapter.client.pages: Any = Mock(
        return_value=[{"items": [{"id": "room-1", "displayName": "example", "locationId": "location-1"}]}]
    )
    adapter.model_loader("Record", Record)
    assert cast("Record", adapter.get("Record", "example")).parent == "site-key"


def test_missing_dict_reference_raises(adapter: WebexAdapter) -> None:

    adapter.config.schema_mapping[0].fields.append(SchemaMappingField(name="parent", mapping="peer", reference="Peer"))
    with pytest.raises(IndexError, match="Unable to locate"):
        adapter.obj_to_diffsync(
            {"id": "room-1", "displayName": "example", "peer": {"id": "absent"}},
            adapter.config.schema_mapping[0],
            Record,
        )


def test_destination_rejected_before_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WEBEX_TOKEN", raising=False)
    with pytest.raises(ValueError, match=r"only.*source"):
        WebexAdapter(
            "destination",
            SyncAdapter(name="webex"),
            SyncConfig(name="x", source=SyncAdapter(name="infrahub"), destination=SyncAdapter(name="webex")),
        )


def test_all_1001_fixture_records_loaded(adapter: WebexAdapter) -> None:
    adapter.client.pages: Any = Mock(
        return_value=[
            {"items": [{"id": str(i), "displayName": f"record-{i}"} for i in range(1000)]},
            {"items": [{"id": "1000", "displayName": "record-1000"}]},
        ]
    )
    adapter.model_loader("Record", Record)
    assert len(adapter.get_all("Record")) == 1001


def test_singleton_without_id_is_supported(adapter: WebexAdapter) -> None:

    adapter.client.options.resources["Record"] = WebexResource(response_key=None)
    adapter.client.pages: Any = Mock(return_value=[{"displayName": "settings"}])
    adapter.model_loader("Record", Record)
    assert adapter.get("Record", "settings").get_unique_id() == "settings"


def _http_response(payload: object, status: int = 200, **headers: str) -> requests.Response:
    result = requests.Response()
    result.status_code = status
    result._content = json.dumps(payload).encode()
    result.headers.update(headers)
    return result


@pytest.mark.parametrize("key", ["records", "nested.devices"])
def test_missing_response_key_names_endpoint_and_key(key: str, adapter: WebexAdapter) -> None:
    adapter.client.options.resources["Record"] = WebexResource(response_key=key)
    adapter.client.pages: Any = Mock(return_value=[{"wrong": []}])
    with pytest.raises(ValueError) as exc:
        adapter.model_loader("Record", Record)
    assert "Webex" in str(exc.value)
    assert "/v1/example" in str(exc.value)
    assert key in str(exc.value)


def test_many_parents_count_retries_and_failed_requests(adapter: WebexAdapter, monkeypatch: pytest.MonkeyPatch) -> None:
    adapter.config.schema_mapping[0].mapping = "/v1/locations/{id}/example"
    adapter._records["Sites"] = [{"id": str(i)} for i in range(500)]
    adapter.client.options.resources["Record"] = WebexResource(parent="Sites")
    limited = False

    def read(_method: str, url: str, **_kwargs: object) -> requests.Response:
        nonlocal limited
        parent_id = urlsplit(url).path.split("/")[3]
        if parent_id == "12" and not limited:
            limited = True
            return _http_response({}, 429, **{"Retry-After": "3"})
        if int(parent_id) % 5 == 0:
            return _http_response({}, 404)
        return _http_response({"items": [{"id": parent_id, "displayName": f"record-{parent_id}"}]})

    http = Mock(side_effect=read)
    sleep = Mock()
    monkeypatch.setattr(requests, "request", http)
    monkeypatch.setattr("infrahub_sync.adapters.webex_client.time.sleep", sleep)
    with capture_logs() as logs:
        adapter.model_loader("Record", Record)
    assert len(adapter.get_all("Record")) == 400
    assert http.call_count == adapter.client.request_count == 501
    sleep.assert_called_once_with(3.0)
    requests_logged = [event["requests"] for event in logs if event["event"] == "Webex request"]
    assert requests_logged == list(range(1, 502))
    summaries = [event for event in logs if event["event"] == "Webex mapping requests"]
    assert summaries[-1]["requests"] == summaries[-1]["total_requests"] == 501
    assert "invented-access-value" not in str(logs)


def test_multi_page_rate_limit_loads_every_record(adapter: WebexAdapter, monkeypatch: pytest.MonkeyPatch) -> None:
    http = Mock(
        side_effect=[
            _http_response(
                {"items": [{"id": str(i), "displayName": f"record-{i}"} for i in range(500)]},
                Link='</v1/example?after=500&orgId=invented-org-a>; rel="next"',
            ),
            _http_response({}, 429, **{"Retry-After": "7"}),
            _http_response(
                {"items": [{"id": str(i), "displayName": f"record-{i}"} for i in range(500, 1000)]},
                Link='</v1/example?after=1000&orgId=invented-org-a>; rel="next"',
            ),
            _http_response({"items": [{"id": "1000", "displayName": "record-1000"}]}),
        ]
    )
    sleep = Mock()
    monkeypatch.setattr(requests, "request", http)
    monkeypatch.setattr("infrahub_sync.adapters.webex_client.time.sleep", sleep)
    adapter.model_loader("Record", Record)
    assert {record.get_unique_id() for record in adapter.get_all("Record")} == {f"record-{i}" for i in range(1001)}
    assert adapter.client.request_count == http.call_count == 4
    sleep.assert_called_once_with(7.0)
    assert http.call_args_list[1].args == http.call_args_list[2].args
    assert http.call_args_list[1].kwargs["params"] == http.call_args_list[2].kwargs["params"]


def test_calling_workspace_fixed_query_uses_returned_set(
    adapter: WebexAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The server applies the fixed type filter on each Calling workspace page."""
    adapter.config.schema_mapping[0].mapping = "/v1/workspaces"
    adapter.client.options.resources["Record"] = WebexResource(params={"type": "meetingRoom"})
    records = [
        {"id": "room-1", "displayName": "Example room one", "type": "meetingRoom"},
        {"id": "desk-1", "displayName": "Example desk", "type": "desk"},
        {"id": "room-2", "displayName": "Example room two", "type": "meetingRoom"},
    ]

    def read(_method: str, url: str, *, params: dict[str, str], **_kwargs: object) -> requests.Response:
        assert urlsplit(url).path == "/v1/workspaces"
        assert params["type"] == "meetingRoom"
        matching = [record for record in records if record["type"] == params["type"]]
        if not urlsplit(url).query:
            return _http_response(
                {"items": matching[:1]}, Link='</v1/workspaces?after=one&orgId=invented-org-a>; rel="next"'
            )
        return _http_response({"items": matching[1:]})

    http = Mock(side_effect=read)
    monkeypatch.setattr(requests, "request", http)
    adapter.model_loader("Record", Record)
    assert http.call_count == 2
    assert {record.get_unique_id() for record in adapter.get_all("Record")} == {"Example room one", "Example room two"}


@pytest.mark.parametrize("scope", ["source", "resource"])
def test_null_query_overrides_remove_path_values_on_every_page(
    scope: str, adapter: WebexAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter.config.schema_mapping[0].mapping = "/v1/example?foo=bar&max=10"
    overrides = {"foo": None, "max": None}
    if scope == "source":
        adapter.client.options.params = overrides
    else:
        adapter.client.options.resources["Record"] = WebexResource(params=overrides)
    http = Mock(
        side_effect=[
            _http_response({"items": []}, Link='</v1/example?after=one&foo=bar&max=10>; rel="next"'),
            _http_response({"items": []}),
        ]
    )
    monkeypatch.setattr(requests, "request", http)
    adapter.model_loader("Record", Record)
    queries = [
        urlsplit(requests.Request("GET", call.args[1], params=call.kwargs["params"]).prepare().url).query
        for call in http.call_args_list
    ]
    assert queries == ["", "after=one"]
