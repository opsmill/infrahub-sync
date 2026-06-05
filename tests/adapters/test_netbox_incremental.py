import collections
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

from infrahub_sync.cache.cursors import CursorState, CursorTier

if TYPE_CHECKING:
    from infrahub_sync.adapters.netbox import NetboxAdapter


def _make_adapter(mappings: list[dict]) -> "NetboxAdapter":
    """Build a NetboxAdapter with stubbed schema_mapping.

    The adapter ctor calls pynetbox.api() which would fail without a
    live URL/token. Patch the client creation to a MagicMock instead.
    """
    from infrahub_sync import SchemaMappingModel, SyncAdapter, SyncConfig
    from infrahub_sync.adapters.netbox import NetboxAdapter

    schema_mapping = [SchemaMappingModel(**m) for m in mappings]
    config = SyncConfig(
        name="t",
        source=SyncAdapter(name="netbox"),
        destination=SyncAdapter(name="infrahub"),
        schema_mapping=schema_mapping,
    )
    target = "test"
    settings = {"url": "https://example.invalid", "token": "x"}

    adapter_settings = SyncAdapter(name="netbox", settings=settings)
    NetboxAdapter._create_netbox_client = lambda _self, _adapter: MagicMock()  # ty: ignore[invalid-assignment]
    return NetboxAdapter(target=target, adapter=adapter_settings, config=config)


def test_cursor_tier_is_timestamp_for_mapped_kinds() -> None:
    adapter = _make_adapter(
        [
            {"name": "InfraDevice", "mapping": "dcim.devices"},
        ]
    )
    assert adapter.cursor_tier_for("InfraDevice") is CursorTier.TIMESTAMP


def test_cursor_tier_is_none_for_unmapped_kinds() -> None:
    adapter = _make_adapter(
        [
            {"name": "InfraDevice", "mapping": "dcim.devices"},
        ]
    )
    assert adapter.cursor_tier_for("WeirdModelMissingFromMapping") is CursorTier.NONE


def test_cursor_tier_is_none_for_mapping_without_resource_path() -> None:
    adapter = _make_adapter(
        [
            {"name": "InfraDevice", "mapping": ""},
        ]
    )
    assert adapter.cursor_tier_for("InfraDevice") is CursorTier.NONE


class _FakeRecord(collections.UserDict):
    """Minimal pynetbox record stub: UserDict so dict(record) works."""


def test_list_changed_since_uses_last_updated_filter() -> None:
    adapter = _make_adapter(
        [
            {
                "name": "InfraDevice",
                "mapping": "dcim.devices",
                "identifiers": ["name"],
            },
        ]
    )
    # Build a fake pynetbox endpoint that returns one record.
    # Use a dict subclass so dict(record) produces the expected shape.
    fake_record = _FakeRecord({"id": 1, "name": "leaf1"})
    fake_endpoint = MagicMock()
    fake_endpoint.filter.return_value = [fake_record]
    adapter.client.dcim.devices = fake_endpoint

    # Register a minimal model stub so getattr(adapter, "InfraDevice") works.
    # filter_records and transform_records pass records through unchanged.
    fake_model = MagicMock()
    fake_model.filter_records.side_effect = lambda **kw: kw["records"]
    fake_model.transform_records.side_effect = lambda **kw: kw["records"]
    fake_model.is_list.return_value = False
    fake_model.fields = None
    adapter.InfraDevice = fake_model  # ty: ignore[unresolved-attribute]

    cursor = CursorState(tier=CursorTier.TIMESTAMP, value="2026-05-17T10:00:00Z")
    rows = list(adapter.list_changed_since("InfraDevice", cursor))

    fake_endpoint.filter.assert_called_once_with(last_updated__gte="2026-05-17T10:00:00Z")
    # Result has at least the id we set on the fake record.
    assert rows[0]["local_id"] == "1"


def test_list_changed_since_raises_for_unknown_model() -> None:
    adapter = _make_adapter(
        [
            {"name": "InfraDevice", "mapping": "dcim.devices"},
        ]
    )
    with pytest.raises(NotImplementedError):
        list(
            adapter.list_changed_since(
                "UnknownKind", CursorState(tier=CursorTier.TIMESTAMP, value="2026-01-01T00:00:00Z")
            )
        )


def test_list_existing_ids_returns_unique_ids() -> None:
    adapter = _make_adapter(
        [
            {"name": "InfraDevice", "mapping": "dcim.devices", "identifiers": ["name"]},
        ]
    )

    rec_a = _FakeRecord({"id": 1, "name": "leaf1"})
    rec_b = _FakeRecord({"id": 2, "name": "leaf2"})
    fake_endpoint = MagicMock()
    fake_endpoint.all.return_value = [rec_a, rec_b]
    adapter.client.dcim.devices = fake_endpoint

    # Stub the model so get_unique_id returns something predictable.
    fake_model = MagicMock()
    fake_model.filter_records.side_effect = lambda records, **_kw: records
    fake_model.transform_records.side_effect = lambda records, **_kw: records

    # When the adapter instantiates `model(**payload)`, it calls the
    # MagicMock — make the returned instance expose `get_unique_id`
    # tied to the `local_id` we know netbox_obj_to_diffsync sets.
    def _make_instance(**payload: object) -> MagicMock:
        instance = MagicMock()
        instance.get_unique_id.return_value = payload["local_id"]
        return instance

    fake_model.side_effect = _make_instance
    adapter.InfraDevice = fake_model  # ty: ignore[unresolved-attribute]

    ids = list(adapter.list_existing_ids("InfraDevice"))

    fake_endpoint.all.assert_called_once_with()
    assert ids == ["1", "2"]


def test_list_existing_ids_raises_for_unknown_model() -> None:
    adapter = _make_adapter(
        [
            {"name": "InfraDevice", "mapping": "dcim.devices"},
        ]
    )
    with pytest.raises(NotImplementedError):
        list(adapter.list_existing_ids("UnknownKind"))
