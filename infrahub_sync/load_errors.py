"""Typed errors for a failed load, read by the failure cause (spec 009 research R13).

Both are `ValueError` subclasses, so every handler of ADR 0005 that matches `ValueError` still
matches them. Their message text is kept for the worker log; `infrahub_sync.failure_cause`
reads only their named attributes.
"""

from __future__ import annotations

from typing import Any, Literal

SideRole = Literal["source", "destination"]


class TransformError(ValueError):
    """A configured transform failed for one source record.

    `field` is the field the transform writes, `source_id` the record's source record ID, and
    `kind` the mapping entry's kind once `transform_records` knows it.
    """

    def __init__(self, message: str, *, field: str, source_id: str | None, kind: str | None = None) -> None:
        super().__init__(message)
        self.field = field
        self.source_id = source_id
        self.kind = kind


class SideLoadError(ValueError):
    """Loading one side failed; names the side and the adapter, keeps the error as `__cause__`."""

    def __init__(self, message: str, *, side: SideRole, adapter: str) -> None:
        super().__init__(message)
        self.side = side
        self.adapter = adapter


def record_source_id(item: Any) -> str | None:
    """The source record ID of a raw record: its `local_id`, else its `id`."""
    if not isinstance(item, dict):
        return None
    value = item.get("local_id", item.get("id"))
    return None if value is None else str(value)


def side_load_error(message: str, *, side: str, adapter: Any, config: Any) -> SideLoadError:
    """Build the `SideLoadError` of `Potenda._load_side`: side `A` is the source, `B` the destination.

    The adapter name is the one the sync configuration declares, else the adapter's class type.
    """
    role: SideRole = "source" if side == "A" else "destination"
    declared = getattr(config, role, None)
    name = getattr(declared, "name", None)
    if not isinstance(name, str) or not name:
        name = getattr(type(adapter), "type", None) or type(adapter).__name__
    return SideLoadError(message, side=role, adapter=str(name))
