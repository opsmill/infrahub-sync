"""Canonical JSON encoding for the plan artifact (FR-005, AD001, PD-002).

One encoding serves both artifact files: `json.dumps` with `sort_keys=True`,
`separators=(",", ":")` and `ensure_ascii=False`, UTF-8 encoded, LF only. Values that
are not JSON-native are normalized **beforehand** by `canonical_value`, per the PD-002
table, rather than by a `json.dumps(default=...)` hook: a `default=str` fallback would
make the artifact's determinism depend on some type's `__str__`, which is exactly what
SC-006 fails on months later. A type outside the table raises.

Canonical ordering applies to mappings only. A payload's list-valued attribute keeps
its source order and is never re-sorted, because sorting it would make the applied
value differ from the reviewed source value (FR-005).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from infrahub_sync.plan.errors import UnserializablePayloadValueError


def _describe(kind: str | None, field: str | None) -> str:
    """Name the kind and field a value was met under, for an error message."""
    return f"kind {kind or '<unknown>'!r}, field {field or '<unknown>'!r}"


def _child_field(field: str | None, key: str) -> str:
    """Field path of a mapping member, so a nested failure names where it is."""
    return key if field is None else f"{field}.{key}"


def _child_index(field: str | None, index: int) -> str:
    """Field path of a sequence member."""
    return f"[{index}]" if field is None else f"{field}[{index}]"


def canonical_value(value: Any, *, kind: str | None = None, field: str | None = None) -> Any:
    """Normalize `value` to a JSON-native value, per the PD-002 table.

    `str | int | float | bool | None` pass through; `datetime` and `date` become their
    ISO-8601 string; `Decimal` becomes its `str`; `list` and `tuple` recurse **in source
    order**; mappings recurse with their keys sorted. Anything else raises
    `UnserializablePayloadValueError` naming `kind`, `field` and the Python type.

    `kind` and `field` are carried only to make that message actionable; they do not
    affect the value produced.
    """
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (list, tuple)):
        return [canonical_value(item, kind=kind, field=_child_index(field, index)) for index, item in enumerate(value)]
    if isinstance(value, Mapping):
        normalized: dict[str, Any] = {}
        for key in value:
            if not isinstance(key, str):
                msg = (
                    f"Cannot canonically encode a mapping key of Python type {type(key).__name__!r} "
                    f"({_describe(kind, field)}): canonical JSON object keys must be strings, so a "
                    "non-string key has no deterministic encoding."
                )
                raise UnserializablePayloadValueError(msg)
            normalized[key] = canonical_value(value[key], kind=kind, field=_child_field(field, key))
        return {key: normalized[key] for key in sorted(normalized)}
    msg = (
        f"Cannot canonically encode a value of Python type {type(value).__name__!r} "
        f"({_describe(kind, field)}): it is outside the canonical-value table."
    )
    raise UnserializablePayloadValueError(msg)


def canonical_json_bytes(value: Any, *, kind: str | None = None, field: str | None = None) -> bytes:
    """Encode `value` as the artifact's canonical JSON bytes.

    The value is normalized by `canonical_value` first — normalization is idempotent, so
    passing an already-normalized value is safe — and there is deliberately no `default=`
    hook, so an unencodable type raises `UnserializablePayloadValueError` rather than
    being stringified behind the caller's back.

    `allow_nan=False` for the same reason: `NaN` and `Infinity` pass the type
    table as floats, but the default `allow_nan=True` would emit their JavaScript literals
    — invalid JSON — into a file the contract calls canonical.
    """
    try:
        text = json.dumps(
            canonical_value(value, kind=kind, field=field),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except ValueError as exc:
        msg = (
            f"Cannot canonically encode a non-finite float ({_describe(kind, field)}): NaN and "
            "Infinity have no JSON encoding."
        )
        raise UnserializablePayloadValueError(msg) from exc
    return text.encode("utf-8")
