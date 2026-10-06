"""Tests for schema_mapping transform rendering in DiffSyncModelMixin."""

from __future__ import annotations

import os
import sys
import threading
from typing import TYPE_CHECKING, Any

import pytest
from jinja2.exceptions import SecurityError
from jinja2.sandbox import unsafe

from infrahub_sync import DiffSyncModelMixin, _materialize_lazy  # noqa: PLC2701

if TYPE_CHECKING:
    from jinja2 import Environment


class _FilteredModel(DiffSyncModelMixin):
    """Model that registers one custom filter, the way adapter models do."""

    @classmethod
    def _add_custom_filters(cls, native_env: Environment, item: dict[str, Any]) -> None:  # noqa: ARG003
        native_env.filters["shout"] = lambda value: f"{value}!".upper()


@pytest.mark.parametrize(
    ("expression", "item", "expected"),
    [
        ("{{ name | upper }}", {"name": "eth0"}, "ETH0"),
        ("{{ count + 1 }}", {"count": 41}, 42),
        ("{{ enabled }}", {"enabled": False}, False),
        ("{{ vlans | map(attribute='vid') | list }}", {"vlans": [{"vid": 10}, {"vid": 20}]}, [10, 20]),
        ("{{ {'key': name} }}", {"name": "eth0"}, {"key": "eth0"}),
        ("{{ [] }}", {}, []),
        ("{{ 'trunk' if mode == 'tagged' else 'access' }}", {"mode": "tagged"}, "trunk"),
        ("{{ name.split('/') | first }}", {"name": "Gi0/1"}, "Gi0"),
    ],
)
def test_transform_returns_native_types(expression: str, item: dict[str, Any], expected: object) -> None:
    DiffSyncModelMixin.apply_transform(item=item, transform_expr=expression, field="result")

    assert item["result"] == expected
    assert type(item["result"]) is type(expected)


def test_transform_returning_none_leaves_field_unset() -> None:
    item: dict[str, Any] = {"mode": None}

    DiffSyncModelMixin.apply_transform(item=item, transform_expr="{{ mode }}", field="result")

    assert "result" not in item


@pytest.mark.parametrize("expression", ["{{ missing }}", "{{ [missing] }}"])
def test_transform_refuses_a_missing_key(expression: str) -> None:
    item: dict[str, Any] = {"name": "eth0"}

    with pytest.raises(ValueError, match="Failed to transform 'result'"):
        DiffSyncModelMixin.apply_transform(item=item, transform_expr=expression, field="result")

    assert "result" not in item


def test_transform_stores_a_lazy_filter_result_as_a_list() -> None:
    item: dict[str, Any] = {"vlans": [{"vid": 10}, {"vid": 20}]}

    DiffSyncModelMixin.apply_transform(item=item, transform_expr="{{ vlans | map(attribute='vid') }}", field="result")

    assert item["result"] == [10, 20]
    assert type(item["result"]) is list


@pytest.mark.parametrize(
    "expression",
    ["{{ items | map(attribute='missing') }}", "{{ items | selectattr('missing') }}"],
)
def test_transform_refuses_a_lazy_filter_result_with_a_missing_key(expression: str) -> None:
    item: dict[str, Any] = {"items": [{}]}

    with pytest.raises(ValueError, match=r"Failed to transform 'result'.*no attribute 'missing'"):
        DiffSyncModelMixin.apply_transform(item=item, transform_expr=expression, field="result")

    assert "result" not in item


@pytest.mark.parametrize(
    "expression",
    ["{{ items | map('map', attribute='missing') }}", "{{ items | map('selectattr', 'missing') }}"],
)
def test_transform_refuses_a_composed_lazy_filter_result_with_a_missing_key(expression: str) -> None:
    item: dict[str, Any] = {"items": [[{}]]}

    with pytest.raises(ValueError, match=r"Failed to transform 'result'.*no attribute 'missing'"):
        DiffSyncModelMixin.apply_transform(item=item, transform_expr=expression, field="result")

    assert "result" not in item


@pytest.mark.parametrize(
    "expression",
    [
        "{{ {(items | map(attribute='missing')): 'value'} }}",
        "{{ {(items | selectattr('missing')): 'value'} }}",
    ],
)
def test_transform_refuses_a_lazy_filter_result_used_as_a_dictionary_key(expression: str) -> None:
    item: dict[str, Any] = {"items": [{}], "result": "kept"}

    with pytest.raises(ValueError, match=r"Failed to transform 'result'.*lazy iterator"):
        DiffSyncModelMixin.apply_transform(item=item, transform_expr=expression, field="result")

    assert item["result"] == "kept"


@pytest.mark.parametrize("container", [set, frozenset])
def test_materialize_lazy_refuses_a_lazy_iterator_inside_a_set(container: type) -> None:
    """A lazy iterator held by a set cannot be consumed into a member, so it is refused."""
    with pytest.raises(TypeError, match="lazy iterator"):
        _materialize_lazy(container({iter([1])}))


def test_transform_stores_a_composed_lazy_filter_result_as_nested_lists() -> None:
    item: dict[str, Any] = {"items": [[{"vid": 10}], [{"vid": 20}]]}

    DiffSyncModelMixin.apply_transform(
        item=item, transform_expr="{{ items | map('map', attribute='vid') }}", field="result"
    )

    assert item["result"] == [[10], [20]]
    assert type(item["result"][0]) is list


def test_transform_keeps_model_custom_filters() -> None:
    item: dict[str, Any] = {"name": "eth0"}

    _FilteredModel.apply_transform(item=item, transform_expr="{{ name | shout }}", field="result")

    assert item["result"] == "ETH0!"


def _called_from_a_template() -> bool:
    """Whether a rendered template is on this thread's stack; Jinja compiles one as `<template>`."""
    frame = sys._getframe(1)
    while frame is not None:
        if frame.f_code.co_filename == "<template>":
            return True
        frame = frame.f_back
    return False


def test_template_call_filter_ignores_plain_threads() -> None:
    """The `os.getpid` filter counts a call made inside a template, not one from a plain thread."""
    from jinja2 import Environment

    seen: list[bool] = []
    worker = threading.Thread(target=lambda: seen.append(_called_from_a_template()))
    worker.start()
    worker.join()
    env = Environment()  # noqa: S701
    env.from_string("{{ probe() }}").render(probe=lambda: seen.append(_called_from_a_template()) or "")

    assert seen == [False, True]


@pytest.mark.parametrize(
    "expression",
    [
        "{{ cycler.__init__.__globals__.os.getpid() }}",
        "{{ ''.__class__.__mro__ }}",
        "{{ name.__class__ }}",
        "{{ lipsum.__globals__ }}",
        "{{ [name.__class__] }}",
        "{{ {'key': name.__class__} }}",
        # The attr-filter escape fixed in Jinja2 3.1.6 (GHSA-cpwx-vrp4-4pq7).
        "{{ ('{0.__init__.__globals__}' | attr('format'))(cycler) }}",
    ],
)
def test_transform_refuses_unsafe_attribute_access(expression: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Unsafe attribute chains raise SecurityError before a template can reach `os.getpid`."""
    called: list[bool] = []
    real_getpid = os.getpid

    def recording_getpid() -> int:
        # Threads left running by other tests call os.getpid too; only a template's call counts.
        if _called_from_a_template():
            called.append(True)
        return real_getpid()

    monkeypatch.setattr(os, "getpid", recording_getpid)
    item: dict[str, Any] = {"name": "eth0"}

    with pytest.raises(ValueError, match="Failed to transform 'result'") as excinfo:
        DiffSyncModelMixin.apply_transform(item=item, transform_expr=expression, field="result")

    assert isinstance(excinfo.value.__cause__, SecurityError)
    assert "not allowed" in str(excinfo.value)
    assert "result" not in item
    assert called == []


@pytest.mark.parametrize(
    "expression",
    [
        "{{ tags.append('injected') }}",
        "{{ settings.update({'injected': True}) }}",
        "{{ settings.pop('token') }}",
    ],
)
def test_transform_refuses_mutating_the_record(expression: str) -> None:
    item: dict[str, Any] = {"tags": ["a"], "settings": {"token": "kept"}}

    with pytest.raises(ValueError, match="Failed to transform 'result'") as excinfo:
        DiffSyncModelMixin.apply_transform(item=item, transform_expr=expression, field="result")

    assert isinstance(excinfo.value.__cause__, SecurityError)
    assert item == {"tags": ["a"], "settings": {"token": "kept"}}


def test_transform_failure_does_not_echo_record_values() -> None:
    item: dict[str, Any] = {"password": "hunter2-canary", "name": "eth0"}

    with pytest.raises(ValueError, match="Failed to transform 'result'") as excinfo:
        DiffSyncModelMixin.apply_transform(
            item=item,
            transform_expr="{{ password.__class__ }}",
            field="result",
        )

    assert "hunter2-canary" not in str(excinfo.value)


class _GuardedRecordValue:
    """A record value whose methods Jinja2 marks as unsafe or as changing data."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    @unsafe
    def reveal(self) -> str:
        self.calls.append("reveal")
        return "revealed"

    def delete(self) -> str:
        self.calls.append("delete")
        return "deleted"

    # Django-style marker that the Jinja2 sandbox checks before a call.
    delete.__dict__["alters_data"] = True


@pytest.mark.parametrize("expression", ["{{ value.reveal() }}", "{{ value.delete() }}"])
def test_transform_refuses_unsafe_callables(expression: str) -> None:
    value = _GuardedRecordValue()
    item: dict[str, Any] = {"value": value}

    with pytest.raises(ValueError, match="not allowed in the transform sandbox") as excinfo:
        DiffSyncModelMixin.apply_transform(item=item, transform_expr=expression, field="result")

    assert isinstance(excinfo.value.__cause__, SecurityError)
    assert not value.calls
    assert "result" not in item
