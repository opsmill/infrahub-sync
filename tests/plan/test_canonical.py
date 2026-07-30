"""T010 — canonical encoding (FR-005, PD-002).

Two normalization rules are deliberately different and both are asserted here: a
mapping recurses **key-sorted**, while a list or tuple recurses **in source order** and is
never re-sorted. Sorting a payload's list-valued attribute would make the applied value
differ from the reviewed source value, which is the whole point of the artifact.

Every row of the PD-002 table is a parametrized case, so a row deleted from the
implementation fails a named case rather than disappearing quietly.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

import pytest

from infrahub_sync.plan.canonical import canonical_json_bytes, canonical_value
from infrahub_sync.plan.errors import UnserializablePayloadValueError

# --------------------------------------------------------------------------------------
# The PD-002 normalization table, one case per row.
# --------------------------------------------------------------------------------------

# `str | int | float | bool | None` pass through unchanged; `datetime`/`date` become their
# ISO-8601 string; `Decimal` becomes its `str`; `list`/`tuple` recurse in source order;
# `dict` recurses key-sorted.
PD002_ROWS: list[tuple[str, Any, Any]] = [
    ("str passes through", "prod", "prod"),
    ("empty str passes through", "", ""),
    ("non-ascii str passes through unescaped", "café", "café"),
    ("int passes through", 42, 42),
    ("negative int passes through", -7, -7),
    ("float passes through", 1.5, 1.5),
    ("bool True passes through", True, True),
    ("bool False passes through", False, False),
    ("None passes through", None, None),
    (
        "datetime becomes its ISO-8601 string",
        datetime(2026, 7, 26, 18, 4, 11, 512034, tzinfo=timezone.utc),
        "2026-07-26T18:04:11.512034+00:00",
    ),
    ("naive datetime becomes its ISO-8601 string", datetime(2026, 7, 26, 18, 4, 11), "2026-07-26T18:04:11"),  # noqa: DTZ001 — a naive value is exactly what the table must handle
    ("date becomes its ISO-8601 string", date(2026, 7, 26), "2026-07-26"),
    ("Decimal becomes its str", Decimal("1.10"), "1.10"),
    ("Decimal keeps its own precision, not a float's", Decimal("0.1000000000000000055"), "0.1000000000000000055"),
    ("list recurses in source order", ["c", "a", "b"], ["c", "a", "b"]),
    ("empty list stays empty", [], []),
    ("tuple recurses in source order and becomes a list", ("c", "a", "b"), ["c", "a", "b"]),
    ("dict recurses key-sorted", {"b": 1, "a": 2}, {"a": 2, "b": 1}),
    ("empty dict stays empty", {}, {}),
    (
        "nested list of dicts: order kept outside, keys sorted inside",
        [{"b": Decimal(2), "a": date(2026, 1, 2)}, {"z": 1}],
        [{"a": "2026-01-02", "b": "2"}, {"z": 1}],
    ),
]


@pytest.mark.parametrize(
    ("value", "expected"),
    [pytest.param(value, expected, id=label) for label, value, expected in PD002_ROWS],
)
def test_pd002_table_row(value: Any, expected: Any) -> None:  # noqa: ANN401 — the table is heterogeneous by construction
    """Each PD-002 row normalizes to exactly the value the table names."""
    assert canonical_value(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [pytest.param(value, expected, id=label) for label, value, expected in PD002_ROWS],
)
def test_pd002_table_row_is_idempotent(value: Any, expected: Any) -> None:  # noqa: ANN401 — see above
    """Normalizing an already-normalized value is a no-op."""
    assert canonical_value(canonical_value(value)) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        pytest.param("prod", b'"prod"', id="str"),
        pytest.param("café", b'"caf\xc3\xa9"', id="non-ascii is UTF-8, not escaped"),
        pytest.param(True, b"true", id="bool"),
        pytest.param(None, b"null", id="None"),
        pytest.param({"b": 1, "a": 2}, b'{"a":2,"b":1}', id="dict is key-sorted, no spaces"),
        pytest.param(["c", "a"], b'["c","a"]', id="list keeps source order, no spaces"),
        pytest.param(Decimal("1.10"), b'"1.10"', id="Decimal is a JSON string"),
        pytest.param(date(2026, 7, 26), b'"2026-07-26"', id="date is a JSON string"),
    ],
)
def test_encoding_is_the_declared_separators_and_encoding(value: Any, expected: bytes) -> None:  # noqa: ANN401 — see above
    """The bytes are `separators=(",", ":")`, `sort_keys=True`, `ensure_ascii=False`, UTF-8."""
    assert canonical_json_bytes(value) == expected


def test_no_lf_or_cr_is_ever_emitted() -> None:
    """The encoding is one line, so `operations.jsonl` stays one record per line."""
    # U+2028 is deliberate: `ensure_ascii=False` emits it raw, and it is the one character
    # a naive reader might treat as a line break.
    encoded = canonical_json_bytes({"b": ["x\u2028y"], "a": {"c": 1}})
    assert b"\n" not in encoded
    assert b"\r" not in encoded


# --------------------------------------------------------------------------------------
# Byte stability under dict key reordering.
# --------------------------------------------------------------------------------------


def test_dict_key_reordering_does_not_change_the_bytes() -> None:
    """Two mappings differing only in insertion order encode to identical bytes."""
    forward = {"alpha": 1, "beta": 2, "gamma": 3}
    reverse = {"gamma": 3, "beta": 2, "alpha": 1}
    assert list(forward) != list(reverse)  # sanity: the insertion orders really differ
    assert canonical_json_bytes(forward) == canonical_json_bytes(reverse)
    assert canonical_json_bytes(forward) == b'{"alpha":1,"beta":2,"gamma":3}'


def test_nested_dict_key_reordering_does_not_change_the_bytes() -> None:
    """Key sorting is recursive, not just top level."""
    forward = {"outer": {"b": {"z": 1, "y": 2}, "a": 3}}
    reverse = {"outer": {"a": 3, "b": {"y": 2, "z": 1}}}
    assert canonical_json_bytes(forward) == canonical_json_bytes(reverse)
    assert canonical_json_bytes(forward) == b'{"outer":{"a":3,"b":{"y":2,"z":1}}}'


# --------------------------------------------------------------------------------------
# A payload's list-valued attribute keeps source order and is never re-sorted.
# --------------------------------------------------------------------------------------


def test_payload_list_attribute_keeps_source_order() -> None:
    """The list is emitted in the source's order, not sorted."""
    payload = {"name": "dev1", "tags": ["zulu", "alpha", "mike"]}
    encoded = canonical_json_bytes(payload)
    assert encoded == b'{"name":"dev1","tags":["zulu","alpha","mike"]}'
    assert b'["alpha","mike","zulu"]' not in encoded


def test_two_list_orders_are_two_different_payloads() -> None:
    """Reordering a list changes the bytes — proof the list is not being normalized away."""
    first = canonical_json_bytes({"tags": ["a", "b"]})
    second = canonical_json_bytes({"tags": ["b", "a"]})
    assert first != second


def test_list_of_mappings_keeps_element_order_while_sorting_each_element() -> None:
    """The two rules apply at once and do not interfere."""
    value = [{"b": 1, "a": 2}, {"d": 3, "c": 4}]
    assert canonical_json_bytes(value) == b'[{"a":2,"b":1},{"c":4,"d":3}]'


# --------------------------------------------------------------------------------------
# Out-of-table types raise, naming kind, field and Python type.
# --------------------------------------------------------------------------------------


class _Opaque:
    """A type outside the PD-002 table, with a deliberately unhelpful `__str__`."""

    def __str__(self) -> str:
        return "opaque-and-unstable"


@pytest.mark.parametrize(
    "value",
    [
        pytest.param(_Opaque(), id="arbitrary object"),
        pytest.param({1, 2}, id="set"),
        pytest.param(frozenset({1}), id="frozenset"),
        pytest.param(b"bytes", id="bytes"),
        pytest.param(object(), id="bare object"),
        pytest.param(complex(1, 2), id="complex"),
    ],
)
def test_out_of_table_type_raises(value: Any) -> None:  # noqa: ANN401 — see above
    """Anything outside the table raises rather than being silently stringified."""
    with pytest.raises(UnserializablePayloadValueError):
        canonical_value(value)
    with pytest.raises(UnserializablePayloadValueError):
        canonical_json_bytes(value)


def test_out_of_table_type_is_never_stringified() -> None:
    """No `default=str` hook: the unstable `__str__` never reaches the bytes."""
    with pytest.raises(UnserializablePayloadValueError):
        canonical_json_bytes({"thing": _Opaque()})


def test_out_of_table_error_names_kind_field_and_python_type() -> None:
    """The message names all three, so the operator can narrow the mapping."""
    with pytest.raises(UnserializablePayloadValueError) as excinfo:
        canonical_json_bytes(_Opaque(), kind="DcimDevice", field="serial")
    message = str(excinfo.value)
    assert "DcimDevice" in message
    assert "serial" in message
    assert "_Opaque" in message


def test_nested_failure_names_the_full_field_path() -> None:
    """A failure inside a mapping names the nested field, not just the root."""
    with pytest.raises(UnserializablePayloadValueError) as excinfo:
        canonical_json_bytes({"outer": {"inner": _Opaque()}}, kind="DcimDevice")
    message = str(excinfo.value)
    assert "DcimDevice" in message
    assert "outer.inner" in message
    assert "_Opaque" in message


def test_failure_inside_a_list_names_the_index() -> None:
    """A failure inside a list names its index, so the operator can find the element."""
    with pytest.raises(UnserializablePayloadValueError) as excinfo:
        canonical_json_bytes({"tags": ["ok", _Opaque()]}, kind="BuiltinTag")
    message = str(excinfo.value)
    assert "BuiltinTag" in message
    assert "tags[1]" in message


@pytest.mark.parametrize(
    "value",
    [
        pytest.param(float("nan"), id="NaN"),
        pytest.param(float("inf"), id="Infinity"),
        pytest.param(float("-inf"), id="-Infinity"),
    ],
)
def test_a_non_finite_float_is_refused_rather_than_encoded(value: float) -> None:
    """MIN-001: `NaN`/`Infinity` pass the type table as floats, but have no JSON encoding."""
    with pytest.raises(UnserializablePayloadValueError):
        canonical_json_bytes({"metric": value})


def test_a_nested_non_finite_float_is_refused_too() -> None:
    """The refusal is the encoder's, so depth does not matter."""
    with pytest.raises(UnserializablePayloadValueError) as excinfo:
        canonical_json_bytes({"metrics": [1.0, float("nan")]}, kind="DcimDevice")
    assert "DcimDevice" in str(excinfo.value)
    assert excinfo.value.next_action


def test_non_string_mapping_key_raises() -> None:
    """A non-string object key has no deterministic canonical encoding."""
    with pytest.raises(UnserializablePayloadValueError) as excinfo:
        canonical_json_bytes({1: "one"}, kind="BuiltinTag", field="tags")
    message = str(excinfo.value)
    assert "int" in message
    assert "BuiltinTag" in message


def test_the_error_carries_a_next_action() -> None:
    """Every taxonomy member states what to do next (AD059)."""
    with pytest.raises(UnserializablePayloadValueError) as excinfo:
        canonical_value(_Opaque())
    assert excinfo.value.next_action
    assert excinfo.value.next_action in str(excinfo.value)
