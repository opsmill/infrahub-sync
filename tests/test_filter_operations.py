"""Tests for the schema mapping filter operations."""

from __future__ import annotations

import pytest

from infrahub_sync import DiffSyncModelMixin, SchemaMappingFilter


@pytest.mark.parametrize("field_value", ["", None, "core switch"])
def test_not_contains_keeps_field_without_value(field_value: str | None) -> None:
    assert DiffSyncModelMixin.apply_filter(field_value, "not contains", "decom")


def test_not_contains_drops_field_with_value() -> None:
    assert not DiffSyncModelMixin.apply_filter("decom 2024", "not contains", "decom")


@pytest.mark.parametrize("field_value", ["", None, "core switch", "decom 2024"])
def test_contains_and_not_contains_are_complementary(field_value: str | None) -> None:
    contains = bool(DiffSyncModelMixin.apply_filter(field_value, "contains", "decom"))
    not_contains = bool(DiffSyncModelMixin.apply_filter(field_value, "not contains", "decom"))
    assert contains is not not_contains


@pytest.mark.parametrize("field_value", [None, "junos"])
def test_regex_does_not_match(field_value: str | None) -> None:
    assert DiffSyncModelMixin.apply_filter(field_value, "regex", "^ios") is False


def test_regex_matches() -> None:
    assert DiffSyncModelMixin.apply_filter("ios", "regex", "^ios") is True


@pytest.mark.parametrize("field_value", ["", None])
@pytest.mark.parametrize("pattern", [".*", "^$"])
def test_regex_drops_blank_field_for_pattern_matching_empty_string(field_value: str | None, pattern: str) -> None:
    assert DiffSyncModelMixin.apply_filter(field_value, "regex", pattern) is False


def test_regex_still_rejects_non_string_field() -> None:
    with pytest.raises(TypeError):
        DiffSyncModelMixin.apply_filter(100, "regex", "^1")


BLANK_OR_NULL = [pytest.param(None, id="none"), pytest.param("", id="empty-string")]


@pytest.mark.parametrize("field_value", BLANK_OR_NULL)
@pytest.mark.parametrize(
    ("operation", "value"),
    [
        pytest.param(">", 5, id="gt"),
        pytest.param("<", 5, id="lt"),
        pytest.param(">=", 5, id="ge"),
        pytest.param("<=", 5, id="le"),
        pytest.param("in", "dc1-dc2", id="in-string"),
        pytest.param("is_ip_within", "10.0.0.0/8", id="is-ip-within"),
    ],
)
def test_positive_filter_drops_blank_or_null_field(field_value: str | None, operation: str, value: object) -> None:
    assert not DiffSyncModelMixin.apply_filter(field_value, operation, value)


@pytest.mark.parametrize("field_value", BLANK_OR_NULL)
def test_not_in_string_keeps_blank_or_null_field(field_value: str | None) -> None:
    assert DiffSyncModelMixin.apply_filter(field_value, "not in", "dc1-dc2")


@pytest.mark.parametrize("operation", [">", "<", ">=", "<="])
def test_comparison_still_rejects_non_numeric_field(operation: str) -> None:
    with pytest.raises(ValueError, match="Cannot convert 'abc' to int"):
        DiffSyncModelMixin.apply_filter("abc", operation, 5)


def test_is_ip_within_still_rejects_invalid_address() -> None:
    with pytest.raises(ValueError, match="does not appear to be an IPv4 or IPv6"):
        DiffSyncModelMixin.apply_filter("n/a", "is_ip_within", "10.0.0.0/8")


def test_in_list_still_matches_listed_blank_value() -> None:
    assert DiffSyncModelMixin.apply_filter("", "in", ["", "active"])


def test_filter_records_with_blank_description_and_null_platform() -> None:
    records = [
        {"name": "sw1", "description": "", "platform": None},
        {"name": "sw2", "description": "decom", "platform": {"name": "ios"}},
        {"name": "sw3", "description": "", "platform": {"name": "ios-xe"}},
    ]
    filters = [
        SchemaMappingFilter(field="description", operation="not contains", value="decom"),
        SchemaMappingFilter(field="platform.name", operation="regex", value="^ios"),
    ]
    kept = [record["name"] for record in records if DiffSyncModelMixin.apply_filters(record, filters)]
    assert kept == ["sw3"]
