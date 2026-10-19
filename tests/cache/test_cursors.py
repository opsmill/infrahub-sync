"""CursorTier + CursorState tests."""

from __future__ import annotations

import pytest

from infrahub_sync.cache.cursors import CursorState, CursorTier


def test_cursor_tier_ordering() -> None:
    """Higher tiers are strictly more capable."""
    assert CursorTier.NONE < CursorTier.PAGE_TOKEN
    assert CursorTier.PAGE_TOKEN < CursorTier.TIMESTAMP
    assert CursorTier.TIMESTAMP < CursorTier.INFRAHUB_DIFF


def test_cursor_state_constructs() -> None:
    cs = CursorState(tier=CursorTier.TIMESTAMP, value="2026-05-12T15:30:00Z")
    assert cs.tier is CursorTier.TIMESTAMP
    assert cs.value == "2026-05-12T15:30:00Z"


def test_cursor_state_none_default() -> None:
    cs = CursorState(tier=CursorTier.NONE)
    assert cs.value is None


def test_cursor_state_value_required_for_non_none() -> None:
    with pytest.raises(ValueError):
        CursorState(tier=CursorTier.TIMESTAMP, value=None)
