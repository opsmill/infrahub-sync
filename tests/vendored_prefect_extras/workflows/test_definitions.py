"""Unit tests for ``WorkflowDefinition``.

Three groups, mirroring the public contract: the value object itself
(construction, normalization, immutability, key derivation), ``load()``
resolution, and ``to_deployment_input()`` rendering. Everything runs offline
against the fixture modules in this package.
"""

from __future__ import annotations

import importlib
import sys
import types
from collections.abc import Iterable
from dataclasses import FrozenInstanceError
from typing import Any, cast

import pytest
from prefect import flow
from prefect.flows import Flow

from opsmill_prefect_extras.workflows.definitions import WorkflowDefinition

FLOWS_MODULE = "tests.workflows.flows"
SENTINEL_MODULE = "tests.workflows.sentinel"
MISSING_MODULE = "tests.workflows.no_such_module"


# ---------------------------------------------------------------------------
# Value object: construction, normalization, immutability, key
# ---------------------------------------------------------------------------


def _construct_freely(*args: Any, **kwargs: Any) -> WorkflowDefinition:
    """Construct a definition from arbitrary arguments.

    Any: a deliberately malformed call must reach the constructor's runtime
    signature, so its argument shape stays opaque to the type gate here.
    """
    return WorkflowDefinition(*args, **kwargs)


def test_construction_is_keyword_only() -> None:
    """Positional arguments are refused: the two-name model invites mixups."""
    with pytest.raises(TypeError):
        _construct_freely("my-sync-flow", FLOWS_MODULE, "my_sync_flow")


def test_instances_are_immutable() -> None:
    definition = WorkflowDefinition(
        flow_name="my-sync-flow",
        module=FLOWS_MODULE,
        function="my_sync_flow",
    )
    # Any: the assignment must dodge the static gate to reach the frozen guard.
    frozen: Any = definition

    with pytest.raises(FrozenInstanceError):
        frozen.flow_name = "renamed"

    assert definition.flow_name == "my-sync-flow"


def test_omitted_deployment_name_defaults_to_flow_name() -> None:
    definition = WorkflowDefinition(
        flow_name="my-sync-flow",
        module=FLOWS_MODULE,
        function="my_sync_flow",
    )

    assert definition.deployment_name == "my-sync-flow"
    assert definition.key == "my-sync-flow/my-sync-flow"


def test_supplied_deployment_name_stays_distinct_from_flow_name() -> None:
    definition = WorkflowDefinition(
        flow_name="inventory-refresh",
        deployment_name="scheduled",
        module=FLOWS_MODULE,
        function="my_sync_flow",
    )

    assert definition.flow_name == "inventory-refresh"
    assert definition.deployment_name == "scheduled"
    assert definition.key == "inventory-refresh/scheduled"


@pytest.mark.parametrize(
    "tags",
    [
        pytest.param(["sync", "nightly"], id="list"),
        pytest.param((tag for tag in ("sync", "nightly")), id="generator"),
    ],
)
def test_tags_accept_any_iterable_and_normalize_to_a_tuple(
    tags: Iterable[str],
) -> None:
    definition = WorkflowDefinition(
        flow_name="my-sync-flow",
        module=FLOWS_MODULE,
        function="my_sync_flow",
        tags=tags,
    )

    assert definition.tags == ("sync", "nightly")
    assert isinstance(definition.tags, tuple)


def test_tags_default_to_an_empty_tuple() -> None:
    definition = WorkflowDefinition(
        flow_name="my-sync-flow",
        module=FLOWS_MODULE,
        function="my_sync_flow",
    )

    assert definition.tags == ()
    assert isinstance(definition.tags, tuple)


@pytest.mark.parametrize(
    "tags",
    [
        pytest.param("sync", id="str"),
        pytest.param(b"sync", id="bytes"),
    ],
)
def test_bare_string_tags_raise_type_error_naming_the_field(
    tags: str | bytes,
) -> None:
    """A bare ``str`` or ``bytes`` would char-split silently, so it is refused."""
    with pytest.raises(TypeError) as excinfo:
        WorkflowDefinition(
            flow_name="my-sync-flow",
            module=FLOWS_MODULE,
            function="my_sync_flow",
            # cast: the wrong element type is exactly what is under test.
            tags=cast("Iterable[str]", tags),
        )

    message = str(excinfo.value)
    assert "tags" in message
    assert "did you mean tags=" in message


@pytest.mark.parametrize(
    ("flow_name", "deployment_name", "offending_field"),
    [
        pytest.param("flows/sync", None, "flow_name", id="flow-name"),
        pytest.param(
            "infrahub-sync", "run/now", "deployment_name", id="deployment-name"
        ),
    ],
)
def test_slash_in_either_name_raises_value_error(
    flow_name: str, deployment_name: str | None, offending_field: str
) -> None:
    """``/`` is the key separator, so it is banned in both halves."""
    with pytest.raises(ValueError) as excinfo:
        WorkflowDefinition(
            flow_name=flow_name,
            deployment_name=deployment_name,
            module=FLOWS_MODULE,
            function="my_sync_flow",
        )

    assert offending_field in str(excinfo.value)


def test_a_defaulted_deployment_name_inherits_the_slash_ban() -> None:
    with pytest.raises(ValueError) as excinfo:
        WorkflowDefinition(
            flow_name="flows/sync",
            module=FLOWS_MODULE,
            function="my_sync_flow",
        )

    assert "/" in str(excinfo.value)


def test_optional_settings_default_to_none() -> None:
    definition = WorkflowDefinition(
        flow_name="my-sync-flow",
        module=FLOWS_MODULE,
        function="my_sync_flow",
    )

    assert definition.cron is None
    assert definition.concurrency_limit is None
    assert definition.collision_strategy is None
    assert definition.entrypoint is None


def test_construction_does_not_import_the_target_module() -> None:
    assert SENTINEL_MODULE not in sys.modules

    definition = WorkflowDefinition(
        flow_name="explodes-on-import",
        module=SENTINEL_MODULE,
        function="anything",
    )

    assert definition.key == "explodes-on-import/explodes-on-import"
    assert definition.module == SENTINEL_MODULE
    assert SENTINEL_MODULE not in sys.modules


def test_construction_applies_no_value_validation() -> None:
    """Validity is Prefect's call, not the library's.

    An unparseable cron, a non-positive concurrency limit, an unknown collision
    strategy and non-string tags all construct without complaint. Prefect 3.8.1
    then *accepts* the non-positive concurrency limit and the non-string tags
    outright, so treating either as a defect would be a validity rule of the
    library's own; the unparseable cron and the unknown collision strategy are
    the two that surface through validation -- never through construction.
    """
    definition = WorkflowDefinition(
        flow_name="my-sync-flow",
        module=FLOWS_MODULE,
        function="my_sync_flow",
        # cast: a non-string tag element is the point of this assertion.
        tags=cast("Iterable[str]", (123,)),
        cron="not a cron",
        concurrency_limit=0,
        collision_strategy="NOT_A_STRATEGY",
    )

    assert definition.tags == (123,)
    assert definition.cron == "not a cron"
    assert definition.concurrency_limit == 0
    assert definition.collision_strategy == "NOT_A_STRATEGY"


def test_construction_accepts_a_negative_concurrency_limit() -> None:
    """No positivity rule of our own -- Prefect 3.8.1 accepts it."""
    definition = WorkflowDefinition(
        flow_name="my-sync-flow",
        module=FLOWS_MODULE,
        function="my_sync_flow",
        concurrency_limit=-1,
    )

    assert definition.concurrency_limit == -1


# ---------------------------------------------------------------------------
# Resolution: load()
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("function", "expected_flow_name"),
    [
        pytest.param("my_sync_flow", "my-sync-flow", id="sync"),
        pytest.param("declared_name_flow", "declared-name", id="declared-name"),
        pytest.param("my_async_flow", "my-async-flow", id="async"),
    ],
)
def test_load_returns_the_real_prefect_flow(
    function: str, expected_flow_name: str
) -> None:
    """The definition carries an ``entrypoint`` on purpose: it is deployment-input
    data only, and resolution must go on following ``module``/``function``. An
    implementation that substituted the entrypoint would fail to import here.
    """
    definition = WorkflowDefinition(
        flow_name=expected_flow_name,
        module=FLOWS_MODULE,
        function=function,
        entrypoint="whatever/format.py:my_sync_flow",
    )

    resolved = definition.load()

    assert isinstance(resolved, Flow)
    assert resolved.name == expected_flow_name
    assert resolved is getattr(importlib.import_module(FLOWS_MODULE), function)


def test_load_missing_module_raises_module_not_found_error() -> None:
    definition = WorkflowDefinition(
        flow_name="my-sync-flow",
        module=MISSING_MODULE,
        function="my_sync_flow",
    )

    with pytest.raises(ModuleNotFoundError):
        definition.load()


def test_load_propagates_an_import_time_exception_unwrapped() -> None:
    """The module's own exception escapes, never wrapped in ``ImportError``.

    Matching the sentinel's message (not merely "some exception") is what stops
    this passing vacuously on a ``ModuleNotFoundError`` from a broken fixture
    path -- and ``ImportError`` is asserted against explicitly, since wrapping
    would hide the target module's real failure from the caller.
    """
    definition = WorkflowDefinition(
        flow_name="explodes-on-import",
        module=SENTINEL_MODULE,
        function="anything",
    )

    with pytest.raises(RuntimeError) as excinfo:
        definition.load()

    assert "sentinel module must never be imported by the catalogue" in str(
        excinfo.value
    )
    assert not isinstance(excinfo.value, ImportError)


def test_load_missing_attribute_raises_attribute_error() -> None:
    definition = WorkflowDefinition(
        flow_name="my-sync-flow",
        module=FLOWS_MODULE,
        function="renamed_away",
    )

    with pytest.raises(AttributeError) as excinfo:
        definition.load()

    assert "renamed_away" in str(excinfo.value)


def test_load_non_flow_target_raises_type_error_naming_key_and_type() -> None:
    definition = WorkflowDefinition(
        flow_name="not-a-flow",
        deployment_name="run",
        module=FLOWS_MODULE,
        function="plain_function",
    )

    with pytest.raises(TypeError) as excinfo:
        definition.load()

    message = str(excinfo.value)
    assert "not-a-flow/run" in message
    # The rendered form in full: the bare word "function" is already a substring
    # of "plain_function", so matching it would say nothing about the *type* of
    # the object that resolved -- which is the whole claim of the message.
    plain_function = importlib.import_module(FLOWS_MODULE).plain_function
    assert f"resolved a {type(plain_function).__name__} instead" in message


def test_load_resolves_fresh_on_every_call() -> None:
    module_name = "opsmill_prefect_extras_dynamic_fixture"

    @flow(name="dynamic-flow")
    def dynamic_flow() -> str:
        return "dynamic_flow"

    module = types.ModuleType(module_name)
    module.__dict__["dynamic_flow"] = dynamic_flow
    sys.modules[module_name] = module
    try:
        definition = WorkflowDefinition(
            flow_name="dynamic-flow",
            module=module_name,
            function="dynamic_flow",
        )

        assert definition.load() is dynamic_flow

        del module.__dict__["dynamic_flow"]

        with pytest.raises(AttributeError):
            definition.load()
    finally:
        del sys.modules[module_name]


# ---------------------------------------------------------------------------
# Rendering: to_deployment_input()
# ---------------------------------------------------------------------------


def test_rendering_omits_optional_settings_that_were_not_supplied() -> None:
    """``name`` and ``tags`` are always present -- ``name`` is the *deployment*
    name -- and everything unsupplied is an absent key, not an invented default.
    """
    definition = WorkflowDefinition(
        flow_name="my-sync-flow",
        module=FLOWS_MODULE,
        function="my_sync_flow",
    )

    payload = definition.to_deployment_input()

    assert payload == {"name": "my-sync-flow", "tags": []}
    for absent in (
        "entrypoint",
        "schedules",
        "concurrency_limit",
        "concurrency_options",
    ):
        assert absent not in payload


def test_rendering_carries_supplied_settings_verbatim() -> None:
    definition = WorkflowDefinition(
        flow_name="infrahub-sync",
        deployment_name="run",
        module=FLOWS_MODULE,
        function="my_sync_flow",
        tags=("sync",),
        cron="0 2 * * *",
        concurrency_limit=4,
        collision_strategy="CANCEL_NEW",
        entrypoint="whatever/format.py:my_sync_flow",
    )

    payload = definition.to_deployment_input()

    assert payload == {
        "name": "run",
        "tags": ["sync"],
        "entrypoint": "whatever/format.py:my_sync_flow",
        "schedules": [{"schedule": {"cron": "0 2 * * *"}}],
        "concurrency_limit": 4,
        "concurrency_options": {"collision_strategy": "CANCEL_NEW"},
    }


def test_rendering_is_plain_data_with_no_prefect_model_instances() -> None:
    """Only builtin containers and scalars, so the payload stays splattable."""
    definition = WorkflowDefinition(
        flow_name="infrahub-sync",
        deployment_name="run",
        module=FLOWS_MODULE,
        function="my_sync_flow",
        tags=("sync",),
        cron="0 2 * * *",
        concurrency_limit=4,
        collision_strategy="CANCEL_NEW",
        entrypoint="whatever/format.py:my_sync_flow",
    )

    _assert_plain_data(definition.to_deployment_input())


def test_rendering_omits_flow_name_but_the_definition_keeps_it() -> None:
    """``create_deployment`` has no flow-name parameter."""
    definition = WorkflowDefinition(
        flow_name="infrahub-sync",
        deployment_name="run",
        module=FLOWS_MODULE,
        function="my_sync_flow",
    )

    payload = definition.to_deployment_input()

    assert "flow_name" not in payload
    assert "infrahub-sync" not in payload.values()
    assert definition.flow_name == "infrahub-sync"
    assert definition.key == "infrahub-sync/run"


def test_rendering_returns_a_fresh_dict_per_call() -> None:
    """Callers may mutate the payload without corrupting the definition."""
    definition = WorkflowDefinition(
        flow_name="my-sync-flow",
        module=FLOWS_MODULE,
        function="my_sync_flow",
        tags=("sync",),
    )

    first = definition.to_deployment_input()
    second = definition.to_deployment_input()

    assert first == second
    assert first is not second
    first["name"] = "mutated"
    first["tags"].append("added")

    assert definition.to_deployment_input() == second
    assert definition.tags == ("sync",)


def test_rendering_never_raises_and_never_imports() -> None:
    assert SENTINEL_MODULE not in sys.modules
    definition = WorkflowDefinition(
        flow_name="explodes-on-import",
        module=SENTINEL_MODULE,
        function="anything",
        cron="not a cron",
        concurrency_limit=0,
        collision_strategy="NOT_A_STRATEGY",
    )

    payload = definition.to_deployment_input()

    assert payload["schedules"] == [{"schedule": {"cron": "not a cron"}}]
    assert payload["concurrency_limit"] == 0
    assert payload["concurrency_options"] == {"collision_strategy": "NOT_A_STRATEGY"}
    assert SENTINEL_MODULE not in sys.modules


def _assert_plain_data(value: object) -> None:
    """Recursively assert ``value`` is built from builtins only."""
    assert type(value) in (str, int, bool, list, dict), value
    if isinstance(value, dict):
        for key, item in value.items():
            assert type(key) is str, key
            _assert_plain_data(item)
    elif isinstance(value, list):
        for item in value:
            _assert_plain_data(item)
