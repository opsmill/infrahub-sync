"""Cursor and page-size rules shared by the run, configuration and version lists."""

from __future__ import annotations

import base64
import json
from datetime import datetime, timezone

import pytest

from infrahub_sync.service.paging import (
    DEFAULT_LIMIT,
    InvalidCursorError,
    SortKey,
    decode_cursor,
    encode_cursor,
    filter_digest,
    timestamp_key,
    validate_limit,
    version_key,
)

_RUN_FILTERS = {"config_id": "cfg-netbox", "registry_version": 2}


def _encode_raw(payload: bytes) -> str:
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


@pytest.mark.parametrize(
    ("sort_key", "filters"),
    [
        (("2026-11-04T09:30:12+00:00", "20261104T0930-0a1b2c3d"), _RUN_FILTERS),
        (("2026-11-04T09:30:12+00:00", "cfg-netbox"), {"name": "netbox"}),
        (7, {"config_id": "cfg-netbox"}),
        (("2026-11-04T09:30:12+00:00", "20261104T0930-0a1b2c3d"), {}),
    ],
)
def test_cursor_round_trips_its_sort_key(sort_key: str | int | tuple[str, ...], filters: dict) -> None:
    cursor = encode_cursor(sort_key, filters)

    assert decode_cursor(cursor, filters) == sort_key


def test_cursor_is_url_safe_base64_json_with_the_sort_key_and_filter_digest() -> None:
    cursor = encode_cursor(("2026-11-04T09:30:12+00:00", "run-1"), _RUN_FILTERS)

    assert "=" not in cursor
    payload = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
    assert payload == {"k": ["2026-11-04T09:30:12+00:00", "run-1"], "f": filter_digest(_RUN_FILTERS)}
    assert len(payload["f"]) == 16
    assert all(character in "0123456789abcdef" for character in payload["f"])


def test_filter_digest_ignores_key_order_and_unset_filters() -> None:
    assert filter_digest({"a": "x", "b": 1}) == filter_digest({"b": 1, "a": "x"})
    assert filter_digest({"config_id": "cfg", "registry_version": None}) == filter_digest({"config_id": "cfg"})


@pytest.mark.parametrize(
    "other_filters",
    [
        {"config_id": "cfg-netbox", "registry_version": 3},
        {"config_id": "cfg-nautobot", "registry_version": 2},
        {"config_id": "cfg-netbox"},
        {},
    ],
)
def test_cursor_reused_with_different_filters_is_refused(other_filters: dict) -> None:
    cursor = encode_cursor(("2026-11-04T09:30:12+00:00", "run-1"), _RUN_FILTERS)

    with pytest.raises(InvalidCursorError):
        decode_cursor(cursor, other_filters)


@pytest.mark.parametrize(
    "value",
    [
        pytest.param("", id="empty"),
        pytest.param("not base64!", id="characters-outside-the-alphabet"),
        pytest.param("eyJr+/", id="standard-base64-alphabet"),
        pytest.param("a", id="impossible-length"),
        pytest.param("A" * 4096, id="oversized"),
        pytest.param(_encode_raw(b"\xff\xfe"), id="not-utf-8"),
    ],
)
def test_malformed_base64_is_refused(value: str) -> None:
    with pytest.raises(InvalidCursorError):
        decode_cursor(value, {})


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param(b"{not json", id="not-json"),
        pytest.param(b"[1, 2]", id="not-an-object"),
        pytest.param(json.dumps({"k": 1}).encode(), id="missing-digest"),
        pytest.param(json.dumps({"f": filter_digest({})}).encode(), id="missing-sort-key"),
        pytest.param(json.dumps({"k": 1, "f": filter_digest({}), "x": 1}).encode(), id="extra-key"),
        pytest.param(json.dumps({"k": None, "f": filter_digest({})}).encode(), id="null-sort-key"),
        pytest.param(json.dumps({"k": True, "f": filter_digest({})}).encode(), id="boolean-sort-key"),
        pytest.param(json.dumps({"k": [], "f": filter_digest({})}).encode(), id="empty-sort-key"),
        pytest.param(json.dumps({"k": [{"a": 1}], "f": filter_digest({})}).encode(), id="nested-sort-key"),
    ],
)
def test_malformed_json_is_refused(payload: bytes) -> None:
    with pytest.raises(InvalidCursorError):
        decode_cursor(_encode_raw(payload), {})


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param(b"[" * 1500, id="deeply-nested-list"),
        pytest.param(b'{"k":' + b"[" * 1500, id="deeply-nested-sort-key"),
        pytest.param(b'{"k":[[1]],"f":"0"}', id="list-in-a-list"),
    ],
)
def test_nested_json_is_refused_before_parsing(payload: bytes) -> None:
    with pytest.raises(InvalidCursorError):
        decode_cursor(_encode_raw(payload), {})


def test_cursor_from_one_list_is_refused_by_another_list() -> None:
    cursor = encode_cursor(("2026-11-04T09:30:12+00:00", "cfg-netbox"), {"list": "configs"})

    with pytest.raises(InvalidCursorError):
        decode_cursor(cursor, {"list": "runs"})


@pytest.mark.parametrize("value", [1, 2**63 - 1])
def test_version_key_accepts_the_signed_64_bit_range(value: int) -> None:
    assert version_key(value) == value


@pytest.mark.parametrize("value", [0, -1, 2**63, 2**64, "7", ("7",)])
def test_version_key_outside_the_signed_64_bit_range_is_refused(value: SortKey) -> None:
    with pytest.raises(InvalidCursorError):
        version_key(value)


def test_timestamp_key_is_normalized_to_utc() -> None:
    parsed, identifier = timestamp_key(("2026-11-04T11:30:12+02:00", "run-1"))

    assert parsed == datetime(2026, 11, 4, 9, 30, 12, tzinfo=timezone.utc)
    assert parsed.tzinfo is timezone.utc
    assert identifier == "run-1"


@pytest.mark.parametrize(
    "key",
    [
        pytest.param(("9999-12-31T23:59:59-23:59", "run-1"), id="overflows-in-utc"),
        pytest.param(("0001-01-01T00:00:00+23:59", "run-1"), id="underflows-in-utc"),
        pytest.param(("2026-11-04T09:30:12", "run-1"), id="naive"),
        pytest.param(("not a timestamp", "run-1"), id="not-iso-8601"),
        pytest.param(("2026-11-04T09:30:12+00:00", ""), id="empty-identifier"),
    ],
)
def test_timestamp_key_outside_the_utc_range_or_malformed_is_refused(key: SortKey) -> None:
    with pytest.raises(InvalidCursorError):
        timestamp_key(key)


def test_limit_defaults_to_fifty() -> None:
    assert validate_limit(None) == DEFAULT_LIMIT == 50


@pytest.mark.parametrize("value", [1, 50, 200])
def test_limit_within_bounds_is_accepted(value: int) -> None:
    assert validate_limit(value) == value


@pytest.mark.parametrize("value", [0, -1, 201, 10_000, True])
def test_limit_out_of_bounds_is_refused(value: int) -> None:
    with pytest.raises(ValueError, match="limit must be an integer from 1 to 200"):
        validate_limit(value)
