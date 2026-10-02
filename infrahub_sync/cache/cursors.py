"""Cursor tiers for incremental sync.

Each adapter resource declares its tier. The engine uses the strongest tier
the adapter supports for each resource at run time.

| Tier            | Used by                                       | Update rule              |
|-----------------|-----------------------------------------------|--------------------------|
| NONE            | adapters that cannot filter by mtime           | always full extract      |
| PAGE_TOKEN      | adapters with `?next=` pagination only         | resume mid-page on crash |
| TIMESTAMP       | NetBox, Nautobot — `last_updated__gte`         | extract changed-since    |
| INFRAHUB_DIFF   | Infrahub destination read-back                 | diff API returns deltas  |
"""

from __future__ import annotations

from collections.abc import Iterable  # noqa: TC003
from dataclasses import dataclass
from enum import IntEnum


class CursorTier(IntEnum):
    """Capability tier the adapter exposes for incremental cursors (see module docstring)."""

    NONE = 0
    PAGE_TOKEN = 1
    TIMESTAMP = 2
    INFRAHUB_DIFF = 3


@dataclass(frozen=True)
class CursorState:
    """Serialized cursor for one model/resource — `tier` + a tier-specific opaque value."""

    tier: CursorTier
    value: str | None = None
    safe: bool = False

    def __post_init__(self) -> None:
        if self.tier is not CursorTier.NONE and self.value is None:
            msg = f"CursorState(tier={self.tier.name}) requires a non-None value."
            raise ValueError(msg)


def capture_safe_cursor(adapter: object, model_name: str, tier: CursorTier) -> CursorState | None:
    """Ask the source for a safe next-run bound before any resource is queried.

    The optional ``safe_cursor_before_load(model_name)`` hook must account for
    source clock skew, timestamp precision and exclusive query boundaries. No
    hook or no guarantee means full extraction; a host timestamp is not safe.
    """
    if not isinstance(tier, CursorTier) or tier is CursorTier.NONE:
        return None
    hook = getattr(adapter, "safe_cursor_before_load", None)
    if hook is None:
        return None
    cursor = hook(model_name)
    if cursor is None:
        return None
    if not isinstance(cursor, CursorState) or not cursor.safe or cursor.tier is not tier:
        msg = f"{model_name}: safe_cursor_before_load must return a safe cursor of tier {tier.name} or None"
        raise ValueError(msg)
    return cursor


def capture_safe_cursors(adapter: object, model_names: Iterable[str]) -> dict[str, CursorState]:
    """Capture all resource guarantees before extraction starts on one side."""
    tier_for = getattr(adapter, "cursor_tier_for", None)
    if tier_for is None:
        return {}
    cursors = {}
    for model_name in model_names:
        cursor = capture_safe_cursor(adapter, model_name, tier_for(model_name))
        if cursor is not None:
            cursors[model_name] = cursor
    return cursors
