"""Cursor and page-size rules shared by the run, configuration and version lists."""

from __future__ import annotations

import base64
import json

import pytest

from infrahub_sync.service.paging import (
    DEFAULT_LIMIT,
    InvalidCursorError,
    decode_cursor,
    encode_cursor,
    filter_digest,
    validate_limit,
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


def test_limit_defaults_to_fifty() -> None:
    assert validate_limit(None) == DEFAULT_LIMIT == 50


@pytest.mark.parametrize("value", [1, 50, 200])
def test_limit_within_bounds_is_accepted(value: int) -> None:
    assert validate_limit(value) == value


@pytest.mark.parametrize("value", [0, -1, 201, 10_000, True])
def test_limit_out_of_bounds_is_refused(value: int) -> None:
    with pytest.raises(ValueError, match="limit must be an integer from 1 to 200"):
        validate_limit(value)
