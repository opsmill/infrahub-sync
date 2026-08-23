"""Contract tests for the reusable hostile-input test harness."""

from __future__ import annotations

from typing import cast

import pytest
from pydantic import BaseModel, ConfigDict
from pydantic_core import PydanticCustomError, PydanticSerializationError

from tests.hostile_inputs import (
    BoundaryCase,
    BoundaryOutcome,
    ForgedDiagnosticCase,
    diagnostic_unicode_cases,
    endpoint_cases,
    forged_diagnostic_cases,
    framework_root_cases,
    hostile_builtin_cases,
    invalid_json_cases,
    iter_lone_surrogates,
    protocol_object_cases,
    root_value_cases,
    unicode_collision_cases,
    valid_unicode_scalar_cases,
)


class _ExampleModel(BaseModel):
    model_config = ConfigDict(frozen=True)

    value: int


def test_hostile_case_ids_are_unique_and_repr_is_callback_safe() -> None:
    builtins = hostile_builtin_cases()
    protocols = protocol_object_cases()
    cases = (*builtins, *protocols)

    assert len({case.id for case in cases}) == len(cases)
    assert {case.id: (case.outcome, case.probed_callback) for case in builtins} == {
        "dict-subclass": (BoundaryOutcome.REJECT, "dict.items"),
        "list-subclass": (BoundaryOutcome.REJECT, "list.iter"),
        "str-subclass": (BoundaryOutcome.REJECT, "str.str"),
        "int-subclass": (BoundaryOutcome.REJECT, "int.convert"),
        "float-subclass": (BoundaryOutcome.REJECT, "float.convert"),
    }
    assert {case.id: (case.outcome, case.probed_callback) for case in protocols} == {
        "custom-mapping": (BoundaryOutcome.REJECT, "mapping.items"),
        "custom-iterator": (BoundaryOutcome.REJECT, "iterator.next"),
        "generator": (BoundaryOutcome.REJECT, "generator.next"),
        "repr-str-format-trap": (BoundaryOutcome.REJECT, "object.repr"),
        "attribute-property-trap": (BoundaryOutcome.REJECT, "object.attribute"),
        "spoofed-class": (BoundaryOutcome.REJECT, "object.__class__"),
    }
    assert all(case.id in repr(case) for case in cases)
    assert all(case.tripwire.calls == () for case in cases)


@pytest.mark.parametrize(
    "case",
    [pytest.param(case, id=case.id) for case in (*hostile_builtin_cases(), *protocol_object_cases())],
)
def test_hostile_case_callback_probes_are_live(case: BoundaryCase) -> None:
    with pytest.raises(AssertionError, match="hostile callback executed"):
        case.probe_callback()

    assert case.tripwire.calls == (case.probed_callback,)


def test_root_factories_classify_exact_dict_and_framework_bypasses() -> None:
    valid_mapping = {"value": 1}
    valid_model = _ExampleModel.model_validate(valid_mapping)
    cases = (
        *root_value_cases(valid_mapping),
        *framework_root_cases(_ExampleModel, valid_model),
    )

    by_id = {case.id: case for case in cases}
    assert {case_id: case.outcome for case_id, case in by_id.items()} == {
        "exact-dict": BoundaryOutcome.ACCEPT,
        "none": BoundaryOutcome.REJECT,
        "list": BoundaryOutcome.REJECT,
        "string": BoundaryOutcome.REJECT,
        "int": BoundaryOutcome.REJECT,
        "float": BoundaryOutcome.REJECT,
        "dict-subclass": BoundaryOutcome.REJECT,
        "spoofed-class": BoundaryOutcome.REJECT,
        "valid-model": BoundaryOutcome.REJECT,
        "constructed-invalid-model": BoundaryOutcome.REJECT,
        "model-subclass": BoundaryOutcome.REJECT,
    }
    assert type(by_id["exact-dict"].value) is dict
    assert by_id["exact-dict"].value == valid_mapping
    assert by_id["none"].value is None
    assert type(by_id["list"].value) is list
    assert by_id["list"].value == []
    assert type(by_id["string"].value) is str
    assert by_id["string"].value == "root-string-canary"
    assert type(by_id["int"].value) is int
    assert by_id["int"].value == 7
    assert type(by_id["float"].value) is float
    assert cast("float", by_id["float"].value).as_integer_ratio() == (3, 2)
    assert type(by_id["dict-subclass"].value).__name__ == "_HostileDict"
    assert issubclass(type(by_id["dict-subclass"].value), dict)
    hostile_dict = cast("dict[object, object]", by_id["dict-subclass"].value)
    assert dict.__len__(hostile_dict) == 0  # noqa: PLC2801 - bypass hostile hooks.
    assert type(by_id["spoofed-class"].value).__name__ == "_SpoofedClass"
    assert by_id["valid-model"].value is valid_model
    assert type(by_id["constructed-invalid-model"].value) is _ExampleModel
    assert type(vars(by_id["constructed-invalid-model"].value)["value"]).__name__ == "_ConstructedValue"
    assert type(by_id["model-subclass"].value).__name__ == "_HostileModel"
    assert issubclass(type(by_id["model-subclass"].value), _ExampleModel)
    assert type(vars(by_id["model-subclass"].value)["value"]).__name__ == "_SubclassValue"
    assert all(case.expected_callbacks == () for case in cases)


def _framework_callback_cases() -> tuple[BoundaryCase, ...]:
    valid_model = _ExampleModel.model_validate({"value": 1})
    return tuple(case for case in framework_root_cases(_ExampleModel, valid_model) if case.probed_callback is not None)


@pytest.mark.parametrize(
    "case",
    [pytest.param(case, id=case.id) for case in _framework_callback_cases()],
)
def test_framework_case_callback_probes_are_live(case: BoundaryCase) -> None:
    with pytest.raises((AssertionError, PydanticSerializationError), match="hostile callback executed"):
        case.probe_callback()

    assert case.tripwire.calls == (case.probed_callback,)


def test_model_subclass_nested_values_share_the_case_tripwire() -> None:
    case = next(case for case in _framework_callback_cases() if case.id == "model-subclass")
    nested_value = vars(case.value)["value"]

    with pytest.raises(AssertionError, match="hostile callback executed"):
        _ = nested_value.payload

    assert case.tripwire.calls == ("model-subclass.attribute",)


def test_invalid_json_cases_cover_every_required_graph_shape() -> None:
    cases = invalid_json_cases()

    assert {case.id for case in cases} == {
        "excessive-depth",
        "recursive-list",
        "recursive-mapping",
        "non-string-key",
        "non-finite-float",
        "non-json-value",
    }
    assert {case.reason for case in cases} == {
        "maximum declared-content depth exceeded",
        "recursive list",
        "recursive mapping",
        "non-string mapping key",
        "non-finite float",
        "non-JSON value",
    }


def test_forged_diagnostic_cases_cover_trusted_shapes_without_private_markers() -> None:
    cases = forged_diagnostic_cases()

    assert {case.id: (case.error_type, case.context) for case in cases} == {
        "json-value": (
            "invalid_json_value",
            {
                "pointer": "/forged\npointer-value-canary",
                "reason": "non-JSON value",
                "message": "pydantic-message-canary\nsecond-line-canary",
            },
        ),
        "unicode-surrogate": (
            "invalid_unicode_surrogate",
            {
                "pointer": "/forged\npointer-value-canary",
                "message": "pydantic-message-canary\nsecond-line-canary",
            },
        ),
        "unsupported-fields": (
            "unsupported_declared_fields",
            {
                "pointer": "/forged\npointer-value-canary",
                "field_names": ("forged-field-name-canary",),
                "message": "pydantic-message-canary\nsecond-line-canary",
            },
        ),
    }
    assert all("marker" not in key for case in cases for key in case.context)
    assert all(case.expected_callbacks == () for case in cases)


@pytest.mark.parametrize(
    "case",
    [pytest.param(case, id=case.id) for case in forged_diagnostic_cases()],
)
def test_forged_diagnostic_callbacks_raise_the_declared_error(case: ForgedDiagnosticCase) -> None:
    with pytest.raises(PydanticCustomError) as caught:
        case.probe_forged_error()

    assert caught.value.type == case.error_type
    assert caught.value.context == case.context
    assert case.tripwire.calls == ("forged-mapping.items",)


def test_unicode_corpora_cover_controls_collisions_and_valid_scalars() -> None:
    controls = diagnostic_unicode_cases()
    collisions = unicode_collision_cases()
    valid_scalars = valid_unicode_scalar_cases()

    assert tuple((case.id, case.value, case.visible, case.group) for case in controls) == (
        ("nul", "\x00", r"\u0000", "c0"),
        ("tab", "\t", r"\t", "c0"),
        ("lf", "\n", r"\n", "c0"),
        ("cr", "\r", r"\r", "c0"),
        ("escape", "\x1b", r"\u001b", "c0"),
        ("delete", "\x7f", r"\u007f", "del"),
        ("next-line", "\x85", r"\u0085", "c1"),
        ("application-program-command", "\x9f", r"\u009f", "c1"),
        ("right-to-left-override", "\u202e", r"\u202e", "bidi"),
        ("left-to-right-isolate", "\u2066", r"\u2066", "isolate"),
        ("zero-width-space", "\u200b", r"\u200b", "zero-width"),
        ("zero-width-no-break-space", "\ufeff", r"\ufeff", "zero-width"),
        ("line-separator", "\u2028", r"\u2028", "separator"),
        ("paragraph-separator", "\u2029", r"\u2029", "separator"),
        ("language-tag", "\U000e0001", r"\U000e0001", "astral"),
    )
    assert tuple((case.id, case.raw, case.literal, case.raw_visible, case.literal_visible) for case in collisions) == (
        ("raw-lf-vs-literal-escape", "\n", r"\n", r"\n", r"\\n"),
        ("raw-cr-vs-literal-escape", "\r", r"\r", r"\r", r"\\r"),
        ("raw-tab-vs-literal-escape", "\t", r"\t", r"\t", r"\\t"),
        ("raw-esc-vs-literal-escape", "\x1b", r"\u001b", r"\u001b", r"\\u001b"),
        ("raw-line-separator-vs-literal-escape", "\u2028", r"\u2028", r"\u2028", r"\\u2028"),
        ("raw-paragraph-separator-vs-literal-escape", "\u2029", r"\u2029", r"\u2029", r"\\u2029"),
        ("raw-rtl-override-vs-literal-escape", "\u202e", r"\u202e", r"\u202e", r"\\u202e"),
        ("raw-zero-width-space-vs-literal-escape", "\u200b", r"\u200b", r"\u200b", r"\\u200b"),
        ("raw-left-to-right-isolate-vs-literal-escape", "\u2066", r"\u2066", r"\u2066", r"\\u2066"),
        (
            "raw-zero-width-no-break-space-vs-literal-escape",
            "\ufeff",
            r"\ufeff",
            r"\ufeff",
            r"\\ufeff",
        ),
        ("backslash", "\\", r"\\", r"\\", r"\\\\"),
        ("slash", "/", "~1", "~1", "~01"),
        ("tilde", "~", "~0", "~0", "~00"),
        (
            "raw-astral-vs-literal-escape",
            "\U000e0001",
            r"\U000e0001",
            r"\U000e0001",
            r"\\U000e0001",
        ),
    )
    assert tuple((case.id, case.value, case.visible, case.group) for case in valid_scalars) == (
        ("before-surrogates", "\ud7ff", r"\ud7ff", "valid"),
        ("after-surrogates", "\ue000", r"\ue000", "valid"),
        ("emoji", "😀", "😀", "valid"),
        ("maximum-scalar", "\U0010ffff", r"\U0010ffff", "valid"),
        ("mixed-printable", "café-東京-😀", "café-東京-😀", "valid"),
    )


def test_every_lone_surrogate_is_available_without_expanding_the_default_corpus() -> None:
    cases = tuple(iter_lone_surrogates())

    assert tuple((case.id, case.value, case.visible, case.group) for case in cases) == tuple(
        (f"U+{codepoint:04X}", chr(codepoint), f"\\u{codepoint:04x}", "surrogate")
        for codepoint in range(0xD800, 0xE000)
    )
    assert len(diagnostic_unicode_cases()) < 32


def test_endpoint_cases_include_unsafe_forms_and_accepted_controls() -> None:
    cases = endpoint_cases()

    assert tuple(
        (
            case.id,
            case.value,
            case.form,
            case.setting_name,
            case.outcome,
            case.canary,
            case.expected_error,
        )
        for case in cases
    ) == (
        ("ordinary-absolute", "https://service.example/api", "absolute", "url", BoundaryOutcome.ACCEPT, None, None),
        (
            "ordinary-authority",
            "//service.example/api",
            "authority",
            "api_endpoint",
            BoundaryOutcome.REJECT,
            None,
            "must be a relative request path without a scheme or authority",
        ),
        ("ordinary-relative", "/api/v1/items", "relative", "api_endpoint", BoundaryOutcome.ACCEPT, None, None),
        (
            "userinfo",
            "https://probe:url-userinfo-canary@service.example/api",
            "userinfo",
            "url",
            BoundaryOutcome.REJECT,
            "url-userinfo-canary",
            "cannot contain user information, query parameters, or fragments",
        ),
        (
            "query",
            "https://service.example/api?probe=url-query-canary",
            "query",
            "url",
            BoundaryOutcome.REJECT,
            "url-query-canary",
            "cannot contain user information, query parameters, or fragments",
        ),
        (
            "fragment",
            "https://service.example/api#url-fragment-canary",
            "fragment",
            "url",
            BoundaryOutcome.REJECT,
            "url-fragment-canary",
            "cannot contain user information, query parameters, or fragments",
        ),
        (
            "malformed-authority",
            "https://[url-authority-canary",
            "malformed-authority",
            "url",
            BoundaryOutcome.REJECT,
            "url-authority-canary",
            "cannot contain user information, query parameters, or fragments",
        ),
    )
    assert all(case.canary is None or case.canary in case.value for case in cases)
    assert all(case.expected_error is None for case in cases if case.outcome is BoundaryOutcome.ACCEPT)
    assert all(case.expected_error is not None for case in cases if case.outcome is BoundaryOutcome.REJECT)
