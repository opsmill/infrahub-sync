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

    def __post_init__(self) -> None:
        if self.tier is not CursorTier.NONE and self.value is None:
            msg = f"CursorState(tier={self.tier.name}) requires a non-None value."
            raise ValueError(msg)
