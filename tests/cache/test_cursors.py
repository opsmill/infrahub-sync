"""CursorTier + CursorState tests."""

from __future__ import annotations

import pytest

from infrahub_sync.cache.cursors import CursorState, CursorTier, capture_safe_cursor


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


@pytest.mark.parametrize(
    "cursor",
    [
        CursorState(CursorTier.TIMESTAMP, "unqualified"),
        CursorState(CursorTier.PAGE_TOKEN, "wrong-tier", safe=True),
        "not-a-cursor",
    ],
)
def test_safe_cursor_hook_rejects_unqualified_values(cursor: object) -> None:
    from types import SimpleNamespace

    source = SimpleNamespace(safe_cursor_before_load=lambda _resource: cursor)
    with pytest.raises(ValueError, match="must return a safe cursor"):
        capture_safe_cursor(source, "Device", CursorTier.TIMESTAMP)


def test_no_source_guarantee_means_no_safe_cursor() -> None:
    from types import SimpleNamespace

    assert capture_safe_cursor(object(), "Device", CursorTier.TIMESTAMP) is None
    assert (
        capture_safe_cursor(
            SimpleNamespace(safe_cursor_before_load=lambda _resource: None), "Device", CursorTier.TIMESTAMP
        )
        is None
    )
