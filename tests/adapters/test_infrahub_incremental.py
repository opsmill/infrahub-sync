from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest

from infrahub_sync.cache.cursors import CursorTier

if TYPE_CHECKING:
    from infrahub_sync.adapters.infrahub import InfrahubAdapter


def _make_adapter(schema_kinds: list[str]) -> "InfrahubAdapter":
    """Build an InfrahubAdapter with stubbed `schema` and `client`.

    The real ctor pulls live schema + accounts from Infrahub. Patch
    those out so the constructor can complete with an in-memory schema.
    """
    from infrahub_sync import SchemaMappingModel, SyncAdapter, SyncConfig
    from infrahub_sync.adapters.infrahub import InfrahubAdapter

    config = SyncConfig(
        name="t",
        source=SyncAdapter(name="netbox"),
        destination=SyncAdapter(name="infrahub"),
        schema_mapping=[SchemaMappingModel(name=k, mapping="anything", identifiers=["name"]) for k in schema_kinds],
    )

    fake_client = MagicMock()
    fake_client.schema.all.return_value = {k: MagicMock() for k in schema_kinds}
    fake_client.get.return_value = None
    # Build an adapter, then overwrite client/schema rather than running
    # the constructor's I/O. Skip __init__ entirely.
    adapter = InfrahubAdapter.__new__(InfrahubAdapter)
    adapter.target = "test"
    adapter.config = config
    adapter.client = fake_client
    adapter.schema = {k: MagicMock() for k in schema_kinds}
    adapter.source_node = None
    adapter.owner_node = None
    adapter.continue_on_error = False
    return adapter


def test_cursor_tier_is_timestamp_for_known_kinds() -> None:
    adapter = _make_adapter(["InfraDevice", "InfraInterfaceL2L3"])
    assert adapter.cursor_tier_for("InfraDevice") is CursorTier.TIMESTAMP


def test_cursor_tier_is_none_for_unknown_kinds() -> None:
    adapter = _make_adapter(["InfraDevice"])
    assert adapter.cursor_tier_for("MissingFromSchema") is CursorTier.NONE


@pytest.mark.parametrize("change_offset", [0, 1])
def test_list_changed_since_uses_updated_at_filter(tmp_path: Path, change_offset: int) -> None:
    from infrahub_sync.cache.cursors import CursorState
    from infrahub_sync.cache.incremental import load_cursors, persist_cursors

    change_time = datetime.fromisoformat("2026-05-17T10:00:00Z") + timedelta(seconds=change_offset)
    adapter = _make_adapter(["InfraDevice"])
    fake_node = MagicMock()
    adapter.client.filters.side_effect = (  # ty: ignore[unresolved-attribute]
        lambda **kwargs: (
            [fake_node] if change_time > datetime.fromisoformat(kwargs["node_metadata__updated_at__after"]) else []
        )
    )

    # Stub infrahub_node_to_diffsync to bypass complex node→dict logic.
    adapter.infrahub_node_to_diffsync = MagicMock(return_value={"local_id": "1", "name": "leaf1"})  # ty: ignore[invalid-assignment]

    # A source guarantee accounts for the strict Infrahub boundary as well as
    # the inclusive NetBox/Nautobot boundary. Persist and query the exact bound.
    path = tmp_path / "cursors.json"
    persist_cursors(
        path,
        side="A",
        cursors={"InfraDevice": CursorState(tier=CursorTier.TIMESTAMP, value="2026-05-17T09:59:59Z", safe=True)},
    )
    cursor = load_cursors(path, side="A")["InfraDevice"]
    assert cursor.safe
    rows = list(adapter.list_changed_since("InfraDevice", cursor))

    adapter.client.filters.assert_called_once_with(  # ty: ignore[unresolved-attribute]
        kind="InfraDevice",
        populate_store=True,
        prefetch_relationships=True,
        node_metadata__updated_at__after="2026-05-17T09:59:59Z",
    )
    assert rows == [{"local_id": "1", "name": "leaf1"}]


def test_list_changed_since_raises_for_unknown_model() -> None:
    import pytest

    from infrahub_sync.cache.cursors import CursorState

    adapter = _make_adapter(["InfraDevice"])
    with pytest.raises(NotImplementedError):
        list(
            adapter.list_changed_since(
                "MissingKind", CursorState(tier=CursorTier.TIMESTAMP, value="2026-01-01T00:00:00Z")
            )
        )


def test_list_existing_ids_yields_unique_ids() -> None:
    adapter = _make_adapter(["InfraDevice"])

    # Stub two fake nodes; the adapter calls client.all → 2 nodes.
    fake_node_a = MagicMock()
    fake_node_b = MagicMock()
    adapter.client.all.return_value = [fake_node_a, fake_node_b]  # ty: ignore[unresolved-attribute]

    # Stub infrahub_node_to_diffsync to return predictable payloads
    payloads = [{"local_id": "1", "name": "leaf1"}, {"local_id": "2", "name": "leaf2"}]
    adapter.infrahub_node_to_diffsync = MagicMock(side_effect=payloads)  # ty: ignore[invalid-assignment]

    # Stub the model class so `model_cls(**payload).get_unique_id()` returns
    # the local_id (or any predictable value tied to the payload).
    fake_model = MagicMock()
    fake_model._identifiers = ("name",)

    def _make_instance(**payload: Any) -> MagicMock:  # noqa: ANN401
        instance = MagicMock()
        instance.get_unique_id.return_value = payload["local_id"]
        return instance

    fake_model.side_effect = _make_instance
    adapter.InfraDevice = fake_model  # ty: ignore[unresolved-attribute]

    ids = list(adapter.list_existing_ids("InfraDevice"))

    adapter.client.all.assert_called_once_with(  # ty: ignore[unresolved-attribute]
        kind="InfraDevice",
        include=["name"],
        populate_store=False,
    )
    assert ids == ["1", "2"]


def test_list_existing_ids_raises_for_unknown_model() -> None:
    import pytest

    adapter = _make_adapter(["InfraDevice"])
    with pytest.raises(NotImplementedError):
        list(adapter.list_existing_ids("MissingKind"))


def test_model_loader_requests_identifiers_and_mapped_attributes() -> None:
    adapter = _make_adapter(["InfraDevice"])
    fake_node = MagicMock()
    adapter.client.all.return_value = [fake_node]  # ty: ignore[unresolved-attribute]
    adapter.infrahub_node_to_diffsync = MagicMock(  # ty: ignore[invalid-assignment]
        return_value={"local_id": "device-id", "device": "leaf1", "name": "eth0", "description": "test"}
    )
    adapter.update_or_add_model_instance = MagicMock()  # ty: ignore[invalid-assignment]

    fake_model = MagicMock()
    fake_model._identifiers = ("device", "name")
    fake_model._attributes = ("description", "name")
    fake_item = MagicMock()
    fake_item.get_unique_id.return_value = "leaf1"
    fake_model.return_value = fake_item

    adapter.model_loader("InfraDevice", fake_model)

    adapter.client.all.assert_called_once_with(  # ty: ignore[unresolved-attribute]
        kind="InfraDevice",
        include=["device", "name", "description"],
        populate_store=True,
    )


def test_timestamp_source_has_no_established_safe_bound() -> None:
    from infrahub_sync.cache.cursors import capture_safe_cursor

    adapter = _make_adapter(["InfraDevice"])
    assert capture_safe_cursor(adapter, "InfraDevice", CursorTier.TIMESTAMP) is None
