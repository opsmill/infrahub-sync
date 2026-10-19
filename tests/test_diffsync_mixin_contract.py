"""Tests for DiffSyncMixin adapter contract methods."""

import pytest

from infrahub_sync import DiffSyncMixin
from infrahub_sync.cache.cursors import CursorState, CursorTier


class _Stub(DiffSyncMixin):
    pass


def test_cursor_tier_for_defaults_to_none() -> None:
    assert _Stub().cursor_tier_for("Anything") is CursorTier.NONE


def test_list_changed_since_raises_when_unimplemented() -> None:
    stub = _Stub()
    with pytest.raises(NotImplementedError):
        list(stub.list_changed_since("Anything", CursorState(tier=CursorTier.NONE)))


def test_list_existing_ids_raises_when_unimplemented() -> None:
    stub = _Stub()
    with pytest.raises(NotImplementedError):
        list(stub.list_existing_ids("Anything"))
