from collections import UserDict
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

from infrahub_sync.cache.cursors import CursorState, CursorTier

if TYPE_CHECKING:
    from infrahub_sync.adapters.nautobot import NautobotAdapter


class _FakeRecord(UserDict):
    """`dict(MagicMock())` returns {}, so use UserDict to make `dict(node)` work."""


def _make_adapter(mappings: list[dict]) -> "NautobotAdapter":
    """Build a NautobotAdapter with stubbed pynautobot client."""
    from infrahub_sync import SchemaMappingModel, SyncAdapter, SyncConfig
    from infrahub_sync.adapters.nautobot import NautobotAdapter

    schema_mapping = [SchemaMappingModel(**m) for m in mappings]
    config = SyncConfig(
        name="t",
        source=SyncAdapter(name="nautobot"),
        destination=SyncAdapter(name="infrahub"),
        schema_mapping=schema_mapping,
    )
    adapter_settings = SyncAdapter(name="nautobot", settings={"url": "https://example.invalid", "token": "x"})
    NautobotAdapter._create_nautobot_client = lambda _self, _adapter: MagicMock()  # ty: ignore[invalid-assignment]
    return NautobotAdapter(target="test", adapter=adapter_settings, config=config)


def test_cursor_tier_is_timestamp_for_mapped_kinds() -> None:
    adapter = _make_adapter([{"name": "InfraDevice", "mapping": "dcim.devices"}])
    assert adapter.cursor_tier_for("InfraDevice") is CursorTier.TIMESTAMP


def test_cursor_tier_is_none_for_unmapped_kinds() -> None:
    adapter = _make_adapter([{"name": "InfraDevice", "mapping": "dcim.devices"}])
    assert adapter.cursor_tier_for("Unknown") is CursorTier.NONE


def test_cursor_tier_is_none_for_empty_mapping() -> None:
    adapter = _make_adapter([{"name": "InfraDevice", "mapping": ""}])
    assert adapter.cursor_tier_for("InfraDevice") is CursorTier.NONE


def test_list_changed_since_uses_last_updated_filter() -> None:
    adapter = _make_adapter([{"name": "InfraDevice", "mapping": "dcim.devices", "identifiers": ["name"]}])
    fake_record = _FakeRecord({"id": 1, "name": "leaf1"})
    fake_endpoint = MagicMock()
    fake_endpoint.filter.return_value = [fake_record]
    adapter.client.dcim.devices = fake_endpoint  # ty: ignore[unresolved-attribute]

    fake_model = MagicMock()
    fake_model.filter_records.side_effect = lambda records, **_kw: records
    fake_model.transform_records.side_effect = lambda records, **_kw: records
    adapter.InfraDevice = fake_model  # ty: ignore[unresolved-attribute]

    cursor = CursorState(tier=CursorTier.TIMESTAMP, value="2026-05-17T10:00:00Z")
    rows = list(adapter.list_changed_since("InfraDevice", cursor))

    fake_endpoint.filter.assert_called_once_with(last_updated__gte="2026-05-17T10:00:00Z")
    assert rows[0]["local_id"] == "1"


def test_list_changed_since_raises_for_unknown_model() -> None:
    adapter = _make_adapter([{"name": "InfraDevice", "mapping": "dcim.devices"}])
    with pytest.raises(NotImplementedError):
        list(
            adapter.list_changed_since("Unknown", CursorState(tier=CursorTier.TIMESTAMP, value="2026-01-01T00:00:00Z"))
        )


def test_list_changed_since_falls_back_when_endpoint_rejects_filter() -> None:
    """Some Nautobot endpoints (front-ports, rear-ports, ...) return 400 'Unknown filter field'
    on `last_updated__gte`. The adapter must catch that and fall back to `endpoint.all()`.
    """
    import pynautobot

    adapter = _make_adapter([{"name": "InfraDevice", "mapping": "dcim.devices", "identifiers": ["name"]}])
    fake_record = _FakeRecord({"id": 7, "name": "edge1"})

    fake_endpoint = MagicMock()
    # Build a real RequestError mirroring what pynautobot raises on 400.
    fake_resp = MagicMock()
    fake_resp.status_code = 400
    fake_resp.reason = "Bad Request"
    fake_resp.json.return_value = {"last_updated__gte": ["Unknown filter field"]}
    fake_resp.url = "https://demo.nautobot.com/api/dcim/devices/?last_updated__gte=…"
    fake_resp.text = ""
    fake_resp.request.body = None
    fake_endpoint.filter.side_effect = pynautobot.core.query.RequestError(fake_resp)
    fake_endpoint.all.return_value = [fake_record]
    adapter.client.dcim.devices = fake_endpoint  # ty: ignore[unresolved-attribute]

    fake_model = MagicMock()
    fake_model.filter_records.side_effect = lambda records, **_kw: records
    fake_model.transform_records.side_effect = lambda records, **_kw: records
    adapter.InfraDevice = fake_model  # ty: ignore[unresolved-attribute]

    cursor = CursorState(tier=CursorTier.TIMESTAMP, value="2026-05-17T10:00:00Z")
    rows = list(adapter.list_changed_since("InfraDevice", cursor))

    fake_endpoint.filter.assert_called_once_with(last_updated__gte="2026-05-17T10:00:00Z")
    fake_endpoint.all.assert_called_once_with()
    assert rows[0]["local_id"] == "7"


def test_list_existing_ids_returns_unique_ids() -> None:
    adapter = _make_adapter([{"name": "InfraDevice", "mapping": "dcim.devices", "identifiers": ["name"]}])
    rec_a = _FakeRecord({"id": 1, "name": "leaf1"})
    rec_b = _FakeRecord({"id": 2, "name": "leaf2"})
    fake_endpoint = MagicMock()
    fake_endpoint.all.return_value = [rec_a, rec_b]
    adapter.client.dcim.devices = fake_endpoint  # ty: ignore[unresolved-attribute]

    fake_model = MagicMock()
    fake_model.filter_records.side_effect = lambda records, **_kw: records
    fake_model.transform_records.side_effect = lambda records, **_kw: records

    def _make_instance(**payload: object) -> MagicMock:
        instance = MagicMock()
        instance.get_unique_id.return_value = payload["local_id"]
        return instance

    fake_model.side_effect = _make_instance
    adapter.InfraDevice = fake_model  # ty: ignore[unresolved-attribute]

    ids = list(adapter.list_existing_ids("InfraDevice"))
    fake_endpoint.all.assert_called_once_with()
    assert ids == ["1", "2"]
