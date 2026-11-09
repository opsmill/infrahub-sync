"""Configurable Webex endpoint loading and shared mapping behavior."""

from __future__ import annotations

import json
from typing import Any, cast
from unittest.mock import Mock
from urllib.parse import urlsplit

import pytest
import requests
from structlog.testing import capture_logs

from infrahub_sync import SchemaMappingField, SchemaMappingFilter, SchemaMappingTransform, SyncAdapter, SyncConfig
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
