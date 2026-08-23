"""Contract tests for the reusable hostile-input test harness."""

from __future__ import annotations

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
    cases = (*hostile_builtin_cases(), *protocol_object_cases())

    assert len({case.id for case in cases}) == len(cases)
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

    outcomes = {case.id: case.outcome for case in cases}
    assert outcomes["exact-dict"] is BoundaryOutcome.ACCEPT
    assert outcomes["valid-model"] is BoundaryOutcome.REJECT
    assert outcomes["constructed-invalid-model"] is BoundaryOutcome.REJECT
    assert outcomes["model-subclass"] is BoundaryOutcome.REJECT
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


def test_invalid_json_cases_cover_every_required_graph_shape() -> None:
    cases = invalid_json_cases()

    assert {case.reason for case in cases} == {
        "maximum declared-content depth exceeded",
        "recursive list",
        "recursive mapping",
        "non-string mapping key",
        "non-finite float",
        "non-JSON value",
    }
    assert len({case.id for case in cases}) == len(cases)


def test_forged_diagnostic_cases_cover_trusted_shapes_without_private_markers() -> None:
    cases = forged_diagnostic_cases()

    assert {case.error_type for case in cases} == {
        "invalid_json_value",
        "invalid_unicode_surrogate",
        "unsupported_declared_fields",
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

    assert {case.group for case in controls} >= {
        "c0",
        "del",
        "c1",
        "bidi",
        "isolate",
        "zero-width",
        "separator",
        "astral",
    }
    assert {case.id for case in collisions} >= {
        "raw-lf-vs-literal-escape",
        "raw-esc-vs-literal-escape",
        "backslash",
        "slash",
        "tilde",
        "raw-astral-vs-literal-escape",
    }
    assert {ord(case.value) for case in valid_scalars if len(case.value) == 1} >= {
        0xD7FF,
        0xE000,
        0x1F600,
        0x10FFFF,
    }
    assert any(case.value == "café-東京-😀" for case in valid_scalars)


def test_every_lone_surrogate_is_available_without_expanding_the_default_corpus() -> None:
    cases = tuple(iter_lone_surrogates())

    assert len(cases) == 0x800
    assert ord(cases[0].value) == 0xD800
    assert ord(cases[-1].value) == 0xDFFF
    assert len(diagnostic_unicode_cases()) < 32


def test_endpoint_cases_include_unsafe_forms_and_accepted_controls() -> None:
    cases = endpoint_cases()

    assert {case.form for case in cases} >= {
        "absolute",
        "authority",
        "userinfo",
        "query",
        "fragment",
        "malformed-authority",
        "relative",
    }
    assert any(case.outcome is BoundaryOutcome.ACCEPT and case.form == "absolute" for case in cases)
    assert any(case.outcome is BoundaryOutcome.ACCEPT and case.form == "relative" for case in cases)
    assert all(case.canary is None or case.canary in case.value for case in cases)
    assert all(case.expected_error is None for case in cases if case.outcome is BoundaryOutcome.ACCEPT)
    assert all(case.expected_error is not None for case in cases if case.outcome is BoundaryOutcome.REJECT)
