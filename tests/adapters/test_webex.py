"""Configurable Webex endpoint loading and shared mapping behavior."""

from __future__ import annotations

from typing import Any, cast
from unittest.mock import Mock

import pytest

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
