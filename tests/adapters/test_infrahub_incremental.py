import logging
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


def test_list_changed_since_uses_updated_at_filter() -> None:
    from infrahub_sync.cache.cursors import CursorState

    adapter = _make_adapter(["InfraDevice"])
    fake_node = MagicMock()
    adapter.client.filters.return_value = [fake_node]  # ty: ignore[unresolved-attribute]

    # Stub infrahub_node_to_diffsync to bypass complex node→dict logic.
    adapter.infrahub_node_to_diffsync = MagicMock(return_value={"local_id": "1", "name": "leaf1"})  # ty: ignore[invalid-assignment]

    cursor = CursorState(tier=CursorTier.TIMESTAMP, value="2026-05-17T10:00:00Z")
    rows = list(adapter.list_changed_since("InfraDevice", cursor))

    adapter.client.filters.assert_called_once_with(  # ty: ignore[unresolved-attribute]
        kind="InfraDevice",
        populate_store=True,
        prefetch_relationships=True,
        node_metadata__updated_at__after="2026-05-17T10:00:00Z",
    )
    assert rows == [{"local_id": "1", "name": "leaf1"}]


def test_list_changed_since_skips_missing_identifier_when_continuing(caplog: pytest.LogCaptureFixture) -> None:
    """Incremental extraction skips a bad record and still yields later rows."""
    from infrahub_sync.adapters.infrahub import PeerIdentifierError
    from infrahub_sync.cache.cursors import CursorState

    adapter = _make_adapter(["InfraDevice"])
    adapter.continue_on_error = True
    bad_node = MagicMock(id="bad-id")
    good_node = MagicMock(id="good-id")
    adapter.client.filters.return_value = [bad_node, good_node]  # ty: ignore[unresolved-attribute]
    error = PeerIdentifierError(
        parent_kind="InfraDevice",
        parent_id="bad-id",
        rel_name="<self>",
        peer_kind="InfraDevice",
        peer_id="bad-id",
        identifiers=("name",),
        missing_keys=("name",),
        present_keys=(),
    )
    adapter.infrahub_node_to_diffsync = MagicMock(  # ty: ignore[invalid-assignment]
        side_effect=[error, {"local_id": "good-id", "name": "leaf1"}]
    )
    cursor = CursorState(tier=CursorTier.TIMESTAMP, value="2026-05-17T10:00:00Z")

    with caplog.at_level(logging.WARNING, logger="infrahub_sync.adapters.infrahub"):
        rows = list(adapter.list_changed_since("InfraDevice", cursor))

    assert rows == [{"local_id": "good-id", "name": "leaf1"}]
    assert any(
        record.name == "infrahub_sync.adapters.infrahub"
        and "Skipping InfraDevice[bad-id]" in record.message
        and "name" in record.message
        for record in caplog.records
    )


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
