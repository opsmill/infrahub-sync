"""Keyset paging shared by the run, configuration and version lists.

A cursor is an opaque, URL-safe base64 value (without padding) of a JSON object with two
keys:

- ``k``: the last returned row's sort key, for example ``[started_at, run_id]`` for runs or
  ``registry_version`` for versions;
- ``f``: the first 16 hex characters of the SHA-256 of the canonical JSON of the filters the
  cursor was issued for.

A cursor that does not decode, or that is presented with different filters, is refused with
``InvalidCursorError``, which the HTTP layer maps to ``422 invalid-cursor``.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from datetime import datetime
from typing import TYPE_CHECKING, Any, TypeGuard

if TYPE_CHECKING:
    from collections.abc import Mapping

DEFAULT_LIMIT = 50
MIN_LIMIT = 1
MAX_LIMIT = 200

# A filter value is a scalar query parameter, or ``None`` when the filter is not given.
FilterValue = str | int | None
SortKeyPart = str | int
SortKey = SortKeyPart | tuple[SortKeyPart, ...]

_FILTER_DIGEST_LENGTH = 16
# Longest cursor accepted before decoding. A real cursor holds one sort key and one digest, so
# this bound refuses oversized input without limiting any cursor the service issues.
_MAX_CURSOR_LENGTH = 2048
_URL_SAFE_BASE64 = re.compile(r"^[A-Za-z0-9_-]+$")
_CURSOR_KEYS = frozenset({"k", "f"})


class InvalidCursorError(ValueError):
    """A paging cursor that does not decode, or that was issued for other filters."""

    def __init__(self, message: str = "the cursor is not valid for this request") -> None:
        super().__init__(message)
        self.message = message


def filter_digest(filters: Mapping[str, FilterValue]) -> str:
    """Return the first 16 hex characters of the SHA-256 of the canonical filter JSON.

    Filters set to ``None`` are left out, so an absent filter and an unset one give the same
    digest.
    """
    present = {name: value for name, value in filters.items() if value is not None}
    canonical = json.dumps(present, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:_FILTER_DIGEST_LENGTH]


def encode_cursor(sort_key: SortKey, filters: Mapping[str, FilterValue]) -> str:
    """Return the opaque cursor for the row after ``sort_key`` under ``filters``."""
    key: SortKeyPart | list[SortKeyPart] = list(sort_key) if isinstance(sort_key, tuple) else sort_key
    payload = json.dumps({"k": key, "f": filter_digest(filters)}, separators=(",", ":"), ensure_ascii=False)
    return base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii").rstrip("=")


def decode_cursor(value: str, filters: Mapping[str, FilterValue]) -> SortKey:
    """Return the sort key held by ``value`` when it was issued for ``filters``.

    Raises:
        InvalidCursorError: ``value`` is not URL-safe base64 of a JSON object with exactly the
            keys ``k`` and ``f``, its sort key is not a string, an integer or a list of them, or
            its filter digest differs from the digest of ``filters``.
    """
    if not value or len(value) > _MAX_CURSOR_LENGTH or not _URL_SAFE_BASE64.match(value):
        raise InvalidCursorError
    try:
        raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        payload: Any = json.loads(raw.decode("utf-8"))
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError):
        raise InvalidCursorError from None
    if not isinstance(payload, dict) or set(payload) != _CURSOR_KEYS:
        raise InvalidCursorError
    if payload["f"] != filter_digest(filters):
        raise InvalidCursorError
    return _sort_key(payload["k"])


def validate_limit(value: int | None) -> int:
    """Return the page size: ``DEFAULT_LIMIT`` when ``value`` is ``None``.

    Raises:
        ValueError: ``value`` is not an integer from ``MIN_LIMIT`` to ``MAX_LIMIT``.
    """
    if value is None:
        return DEFAULT_LIMIT
    if isinstance(value, bool) or not isinstance(value, int) or not MIN_LIMIT <= value <= MAX_LIMIT:
        msg = f"limit must be an integer from {MIN_LIMIT} to {MAX_LIMIT}"
        raise ValueError(msg)
    return value


def timestamp_key(key: SortKey) -> tuple[datetime, str]:
    """Return the ``(timestamp, identifier)`` key a run or configuration cursor holds.

    Raises:
        InvalidCursorError: the key is not a timezone-aware ISO 8601 timestamp and a
            non-empty identifier.
    """
    if not isinstance(key, tuple) or len(key) != 2 or not all(isinstance(part, str) for part in key):
        raise InvalidCursorError
    timestamp, identifier = key
    try:
        parsed = datetime.fromisoformat(str(timestamp))
    except ValueError:
        raise InvalidCursorError from None
    if parsed.utcoffset() is None or not identifier:
        raise InvalidCursorError
    return parsed, str(identifier)


def version_key(key: SortKey) -> int:
    """Return the ``registry_version`` a version-list cursor holds.

    Raises:
        InvalidCursorError: the key is not a positive integer.
    """
    if not isinstance(key, int) or key < 1:
        raise InvalidCursorError
    return key


def _sort_key(value: object) -> SortKey:
    """Return ``value`` as a sort key, refusing any JSON shape the service never issues."""
    if _is_sort_key_part(value):
        return value
    if isinstance(value, list) and value:
        parts = tuple(part for part in value if _is_sort_key_part(part))
        if len(parts) == len(value):
            return parts
    raise InvalidCursorError


def _is_sort_key_part(value: object) -> TypeGuard[SortKeyPart]:
    return isinstance(value, (str, int)) and not isinstance(value, bool)
