"""Unit tests for aggregate validation and the shipped pytest helper.

Three groups, mirroring the public contract: the typed report objects
(``DefinitionFailure``, ``ValidationReport``), aggregate
``validate_definitions`` over both failure axes, and the shipped
``assert_valid_definitions`` helper consumers wire into their own CI.

Everything runs offline against the fixture modules in this package and real
``prefect.Flow`` objects -- no Prefect server, no network, no wall-clock sleeps.
The input axis is exercised only with values Prefect 3.8.1 actually rejects: an
unparseable cron and an unknown collision strategy. A non-positive
``concurrency_limit`` and non-string tags are *accepted* by that version, so
asserting them as defects would invent a validity rule of the library's own --
validity is Prefect's judgment, not this library's -- and they are asserted here
as *non*-defects instead.
"""

from __future__ import annotations

import subprocess
import sys
import types
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import FrozenInstanceError, fields
from pathlib import Path
from typing import Any, cast

import pytest

import opsmill_prefect_extras.workflows as workflows_package
from opsmill_prefect_extras.workflows import (
    _prefect_input_validation as prefect_input_validation,
)
from opsmill_prefect_extras.workflows.catalogue import (
    DuplicateWorkflowError,
    WorkflowCatalogue,
)
from opsmill_prefect_extras.workflows.definitions import WorkflowDefinition
from opsmill_prefect_extras.workflows.validation import (
    DefinitionFailure,
    ValidationReport,
    assert_valid_definitions,
    validate_definitions,
)

FLOWS_MODULE = "tests.workflows.flows"
SENTINEL_MODULE = "tests.workflows.sentinel"
MISSING_MODULE = "tests.workflows.no_such_module"
SENTINEL_MESSAGE = "sentinel module must never be imported by the catalogue"


def _any_definition() -> WorkflowDefinition:
    """A definition to hang report objects off; never resolved by these tests."""
    return WorkflowDefinition(
        flow_name="my-sync-flow",
        deployment_name="run",
        module=FLOWS_MODULE,
        function="my_sync_flow",
    )


def _definition_keyed(flow_name: str) -> WorkflowDefinition:
    """A definition whose key is ``flow_name/run``.

    A :class:`DefinitionFailure` must be keyed by the definition it reports, so
    a test needing a particular key builds the definition that renders it.
    """
    return WorkflowDefinition(
        flow_name=flow_name,
        deployment_name="run",
        module=FLOWS_MODULE,
        function="my_sync_flow",
    )


def _failure(definition: WorkflowDefinition, message: str) -> DefinitionFailure:
    """One single-message failure for ``definition``."""
    return DefinitionFailure(definition=definition, messages=(message,))


# ---------------------------------------------------------------------------
# Report types
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "instance_factory",
    [
        pytest.param(
            lambda: DefinitionFailure(definition=_any_definition(), messages=("m",)),
            id="definition-failure",
        ),
        pytest.param(lambda: ValidationReport(failures=()), id="validation-report"),
    ],
)
def test_report_types_are_frozen(
    # Any: the factories build unrelated report types with no supertype.
    instance_factory: Any,
) -> None:
    instance = instance_factory()

    # Frozenness is asserted through the behaviour it exists for -- assignment
    # raising -- rather than by reading ``__dataclass_params__``, an undocumented
    # CPython internal.
    frozen: Any = instance
    first_field = fields(instance)[0].name
    with pytest.raises(FrozenInstanceError):
        setattr(frozen, first_field, None)


def test_definition_failure_carries_key_definition_and_messages() -> None:
    """``key`` is derived from the definition, so the identity ``summary()``
    prints cannot disagree with the definition it reports.
    """
    definition = _any_definition()
    messages = ("gone", "bad cron")

    failure = DefinitionFailure(definition=definition, messages=messages)

    assert failure.key == "my-sync-flow/run"
    assert failure.key == definition.key
    assert failure.definition is definition
    assert failure.messages == messages
    assert isinstance(failure.messages, tuple)
    backfill = WorkflowDefinition(
        flow_name="my-sync-flow",
        deployment_name="backfill",
        module=FLOWS_MODULE,
        function="my_sync_flow",
    )
    assert _failure(backfill, "m").key == "my-sync-flow/backfill"


def test_definition_failure_rejects_an_empty_messages_tuple() -> None:
    with pytest.raises(ValueError):
        DefinitionFailure(definition=_any_definition(), messages=())


def test_validation_report_of_no_failures_is_valid() -> None:
    report = ValidationReport(failures=())

    assert report.failures == ()
    assert report.is_valid is True


def test_validation_report_with_failures_is_invalid() -> None:
    failure = DefinitionFailure(definition=_any_definition(), messages=("m",))

    report = ValidationReport(failures=(failure,))

    assert report.is_valid is False
    assert report.failures == (failure,)


def test_validation_report_keeps_failures_in_the_order_given() -> None:
    failures = tuple(
        _failure(_definition_keyed(f"flow-{index}"), f"m{index}") for index in (2, 0, 1)
    )

    report = ValidationReport(failures=failures)

    assert [failure.key for failure in report.failures] == [
        "flow-2/run",
        "flow-0/run",
        "flow-1/run",
    ]


def test_summary_is_multi_line_and_names_every_failure_and_message() -> None:
    """The exact text the helper raises with: nothing is summarized away."""
    definition = _any_definition()
    gone = _definition_keyed("gone-module")
    first = DefinitionFailure(definition=gone, messages=("no such module",))
    second = DefinitionFailure(
        definition=definition,
        messages=("renamed_away is gone", "bad cron here"),
    )

    summary = ValidationReport(failures=(first, second)).summary()

    assert summary.count("\n") >= 4
    for failure in (first, second):
        assert failure.key in summary
        for message in failure.messages:
            assert message in summary


def test_summary_of_a_valid_report_names_nothing() -> None:
    summary = ValidationReport(failures=()).summary()

    assert isinstance(summary, str)
    assert summary.strip() != ""
    assert "/" not in summary


# ---------------------------------------------------------------------------
# Aggregate validation
# ---------------------------------------------------------------------------
#
# Offender builders. Each returns a definition broken in exactly one way,
# with a key distinct from every other builder's so a mixed catalogue can hold
# them all at once. The "valid" builders resolve against the real ``Flow``
# objects in ``tests/workflows/flows.py``.


def _valid(deployment_name: str = "run") -> WorkflowDefinition:
    """A definition that resolves to the real ``my-sync-flow`` flow."""
    return WorkflowDefinition(
        flow_name="my-sync-flow",
        deployment_name=deployment_name,
        module=FLOWS_MODULE,
        function="my_sync_flow",
        tags=("sync",),
        cron="0 2 * * *",
        concurrency_limit=4,
        collision_strategy="CANCEL_NEW",
    )


def _valid_async() -> WorkflowDefinition:
    """An async flow is a ``Flow`` like any other."""
    return WorkflowDefinition(
        flow_name="my-async-flow",
        deployment_name="run",
        module=FLOWS_MODULE,
        function="my_async_flow",
    )


def _valid_declared_name() -> WorkflowDefinition:
    """Declared ``@flow(name=...)`` matched exactly -- no mismatch."""
    return WorkflowDefinition(
        flow_name="declared-name",
        deployment_name="run",
        module=FLOWS_MODULE,
        function="declared_name_flow",
    )


def _missing_module() -> WorkflowDefinition:
    """The module was moved or deleted."""
    return WorkflowDefinition(
        flow_name="gone-module",
        deployment_name="run",
        module=MISSING_MODULE,
        function="my_sync_flow",
    )


def _exploding_module() -> WorkflowDefinition:
    """The module imports, and raises its own ``RuntimeError`` doing it."""
    return WorkflowDefinition(
        flow_name="explodes-on-import",
        deployment_name="run",
        module=SENTINEL_MODULE,
        function="anything",
    )


def _missing_attribute() -> WorkflowDefinition:
    """The headline case: the implementing function was renamed."""
    return WorkflowDefinition(
        flow_name="my-sync-flow",
        deployment_name="renamed",
        module=FLOWS_MODULE,
        function="renamed_away",
    )


def _not_a_flow() -> WorkflowDefinition:
    """The attribute resolves, but the ``@flow`` decorator is missing."""
    return WorkflowDefinition(
        flow_name="not-a-flow",
        deployment_name="run",
        module=FLOWS_MODULE,
        function="plain_function",
    )


def _name_mismatch() -> WorkflowDefinition:
    """Declared ``declared-name-flow``; the real flow is ``declared-name``."""
    return WorkflowDefinition(
        flow_name="declared-name-flow",
        deployment_name="run",
        module=FLOWS_MODULE,
        function="declared_name_flow",
    )


def _bad_cron() -> WorkflowDefinition:
    """Resolves fine; Prefect 3.8.1 rejects the cron."""
    return WorkflowDefinition(
        flow_name="my-sync-flow",
        deployment_name="bad-cron",
        module=FLOWS_MODULE,
        function="my_sync_flow",
        cron="not a cron",
    )


def _bad_collision_strategy() -> WorkflowDefinition:
    """Resolves fine; the strategy is not a ``ConcurrencyLimitStrategy``."""
    return WorkflowDefinition(
        flow_name="my-sync-flow",
        deployment_name="bad-strategy",
        module=FLOWS_MODULE,
        function="my_sync_flow",
        concurrency_limit=1,
        collision_strategy="NOT_A_STRATEGY",
    )


OFFENDERS = (
    pytest.param(
        _missing_module,
        (MISSING_MODULE, "could not be imported"),
        id="missing-module",
    ),
    pytest.param(
        _exploding_module,
        (SENTINEL_MODULE, SENTINEL_MESSAGE),
        id="module-raises-at-import",
    ),
    pytest.param(
        _missing_attribute,
        ("renamed_away", FLOWS_MODULE, "has no attribute"),
        id="missing-attribute",
    ),
    pytest.param(
        _not_a_flow,
        # "resolved a function instead" in full: the bare word "function" is
        # already a substring of "plain_function", so matching it would prove
        # nothing about the *type* of the object that resolved.
        ("plain_function", "resolved a function instead"),
        id="plain-function-target",
    ),
    pytest.param(
        _name_mismatch,
        ("'declared-name-flow'", "'declared-name'", "does not match"),
        id="flow-name-mismatch",
    ),
    pytest.param(
        _bad_cron,
        ("cron", "not a cron"),
        id="unparseable-cron",
    ),
    pytest.param(
        _bad_collision_strategy,
        ("collision_strategy", "NOT_A_STRATEGY"),
        id="unknown-collision-strategy",
    ),
)
"""The seven offenders, one message each.

The input axis is exercised only via an unparseable cron and an unknown
collision strategy -- deliberately *not* via a non-positive
``concurrency_limit`` or non-string tags, which Prefect 3.8.1 accepts.
"""


@pytest.mark.parametrize(("builder", "fragments"), OFFENDERS)
def test_each_offender_is_reported_with_its_cause(
    builder: Callable[[], WorkflowDefinition],
    fragments: tuple[str, ...],
) -> None:
    definition = builder()

    report = validate_definitions([definition])

    assert report.is_valid is False
    assert len(report.failures) == 1
    failure = report.failures[0]
    assert failure.key == definition.key
    assert failure.definition is definition
    assert len(failure.messages) == 1
    for fragment in fragments:
        assert fragment in failure.messages[0]


def test_a_module_raising_at_import_is_module_not_importable_not_a_crash() -> None:
    """The import step catches ``Exception``, not just ``ImportError``.

    A module body propagates its own exception type unwrapped, so an exploding
    module would crash validation if only ``ImportError`` were caught. Matching
    the sentinel's own message is what stops this passing vacuously on a
    ``ModuleNotFoundError`` from a broken fixture path -- which would be the
    *other* module-import failure and would prove nothing about the broad
    handler.
    """
    assert SENTINEL_MODULE not in sys.modules

    report = validate_definitions([_exploding_module()])

    message = report.failures[0].messages[0]
    assert "could not be imported" in message
    assert SENTINEL_MESSAGE in message
    assert "RuntimeError" in message
    assert "ModuleNotFoundError" not in message


def test_one_call_over_a_mixed_catalogue_reports_every_offender() -> None:
    """The offenders are interleaved with valid definitions and supplied both
    individually and inside a group, so nothing about ordering or composition
    shape can hide a failure.
    """
    valid_keys = ("my-sync-flow/nightly", "my-sync-flow/weekly", "my-async-flow/run")
    catalogue = WorkflowCatalogue(
        _valid("nightly"),
        _missing_module(),
        _valid("weekly"),
        (_missing_attribute(), _not_a_flow()),
        _valid_async(),
        _name_mismatch(),
        (_bad_cron(), _bad_collision_strategy()),
        _exploding_module(),
    )

    report = validate_definitions(catalogue)

    assert [failure.key for failure in report.failures] == [
        "gone-module/run",
        "my-sync-flow/renamed",
        "not-a-flow/run",
        "declared-name-flow/run",
        "my-sync-flow/bad-cron",
        "my-sync-flow/bad-strategy",
        "explodes-on-import/run",
    ]
    assert all(len(failure.messages) == 1 for failure in report.failures)
    for key in valid_keys:
        assert key not in [failure.key for failure in report.failures]


def test_a_definition_broken_on_both_axes_reports_both_in_one_entry() -> None:
    definition = WorkflowDefinition(
        flow_name="my-sync-flow",
        deployment_name="both-axes",
        module=FLOWS_MODULE,
        function="renamed_away",
        cron="not a cron",
    )

    report = validate_definitions([definition])

    assert len(report.failures) == 1
    messages = report.failures[0].messages
    assert len(messages) == 2
    assert "renamed_away" in messages[0]
    assert "not a cron" in messages[1]


def test_every_rejected_value_gets_its_own_input_axis_message() -> None:
    definition = WorkflowDefinition(
        flow_name="my-sync-flow",
        deployment_name="two-bad-values",
        module=FLOWS_MODULE,
        function="my_sync_flow",
        cron="not a cron",
        collision_strategy="NOT_A_STRATEGY",
    )

    report = validate_definitions([definition])

    messages = report.failures[0].messages
    assert len(messages) == 2
    joined = " ".join(messages)
    assert "not a cron" in joined
    assert "NOT_A_STRATEGY" in joined


def test_schedule_union_noise_is_filtered_out_of_the_report() -> None:
    """One unparseable cron, one defect -- the union's branch noise is dropped.

    At Prefect 3.8.1 the schedule union answers a single unparseable cron with
    six errors, of which one is the cron branch's real verdict: three of the
    other five reject ``cron`` as an extra input (the interval, rrule and
    no-schedule branches), and two demand the ``interval`` and ``rrule`` fields
    the payload never carried. Reporting them would bury the real defect.

    The rendered path is asserted in full because the cron branch's own tag is
    the one thing the renderer strips: leaving it in would put an internal schema
    branch name in a message about the consumer's payload.
    """
    report = validate_definitions([_bad_cron()])

    messages = report.failures[0].messages
    assert len(messages) == 1
    message = messages[0]
    assert message.startswith("schedules.0.schedule.cron: ")
    assert "not a cron" in message
    for noise in (
        "CronSchedule",
        "IntervalSchedule",
        "RRuleSchedule",
        "NoSchedule",
        "Extra inputs are not permitted",
        "Field required",
    ):
        assert noise not in message


def test_schedule_branch_is_located_by_shape_not_a_fixed_path_index(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A surrounding schema path must not expose union noise or its branch tag."""
    rejection = _StandInRejection(
        lambda: [
            {
                "loc": (
                    "request",
                    "schedules",
                    0,
                    "schedule",
                    "IntervalSchedule",
                    "cron",
                ),
                "msg": "Extra inputs are not permitted",
                "input": "not a cron",
            },
            {
                "loc": (
                    "request",
                    "schedules",
                    0,
                    "schedule",
                    "CronSchedule",
                    "cron",
                ),
                "msg": "Input should be a valid cron expression",
                "input": "not a cron",
            },
        ]
    )
    _reject_with(monkeypatch, rejection)

    report = validate_definitions([_bad_cron()])

    messages = report.failures[0].messages
    assert messages == (
        "request.schedules.0.schedule.cron: Input should be a valid cron expression "
        "(got 'not a cron')",
    )


def test_an_all_valid_catalogue_yields_an_empty_valid_report() -> None:
    catalogue = WorkflowCatalogue(
        _valid("nightly"), _valid_async(), _valid_declared_name()
    )

    report = validate_definitions(catalogue)

    assert report.failures == ()
    assert report.is_valid is True


@pytest.mark.parametrize(
    "definitions",
    [
        pytest.param(WorkflowCatalogue(), id="empty-catalogue"),
        pytest.param(iter(()), id="empty-iterator"),
    ],
)
def test_an_empty_input_yields_an_empty_valid_report(
    definitions: Iterable[WorkflowDefinition],
) -> None:
    report = validate_definitions(definitions)

    assert report.failures == ()
    assert report.is_valid is True


@pytest.mark.parametrize(
    ("field", "value"),
    [
        pytest.param("concurrency_limit", 0, id="zero-concurrency-limit"),
        pytest.param("concurrency_limit", -1, id="negative-concurrency-limit"),
        pytest.param("cron", "0 2 * * *", id="valid-cron"),
        pytest.param("collision_strategy", "ENQUEUE", id="enqueue"),
        pytest.param("collision_strategy", "CANCEL_NEW", id="cancel-new"),
        pytest.param(
            "entrypoint", "whatever/format.py:my_sync_flow", id="free-form-entrypoint"
        ),
    ],
)
def test_values_prefect_accepts_are_not_defects(
    field: str,
    # Any: the parametrized values span differently typed definition fields.
    value: Any,
) -> None:
    """Validity is Prefect's judgment, and Prefect 3.8.1 accepts these.

    A non-positive concurrency limit is the headline case: it looks like an
    invalid value, but Prefect 3.8.1 stores it as given, so reporting it would
    be a validity rule of the library's own.
    """
    definition = WorkflowDefinition(
        flow_name="my-sync-flow",
        module=FLOWS_MODULE,
        function="my_sync_flow",
        **{field: value},
    )

    report = validate_definitions([definition])

    assert report.failures == ()
    assert report.is_valid is True


def test_non_string_tags_are_not_defects() -> None:
    """Prefect 3.8.1 coerces them rather than rejecting them."""
    definition = WorkflowDefinition(
        flow_name="my-sync-flow",
        module=FLOWS_MODULE,
        function="my_sync_flow",
        # cast: a non-string tag element is exactly the point of this test.
        tags=cast("Iterable[str]", (123, object())),
    )

    report = validate_definitions([definition])

    assert report.failures == ()
    assert report.is_valid is True


def test_validation_resolves_fresh_on_every_call() -> None:
    module_name = "opsmill_prefect_extras_validation_fixture"
    module = types.ModuleType(module_name)
    module.__dict__["dynamic_flow"] = _flows_module().my_sync_flow
    sys.modules[module_name] = module
    try:
        definition = WorkflowDefinition(
            flow_name="my-sync-flow",
            module=module_name,
            function="dynamic_flow",
        )

        assert validate_definitions([definition]).is_valid is True

        del module.__dict__["dynamic_flow"]
        second = validate_definitions([definition])

        assert second.is_valid is False
        assert "has no attribute" in second.failures[0].messages[0]
        assert "dynamic_flow" in second.failures[0].messages[0]
    finally:
        del sys.modules[module_name]


def test_validation_sees_a_renamed_flow_across_distinct_modules() -> None:
    module_name = "opsmill_prefect_extras_rename_fixture"
    flows = _flows_module()
    module = types.ModuleType(module_name)
    module.__dict__["target"] = flows.my_sync_flow
    sys.modules[module_name] = module
    try:
        definition = WorkflowDefinition(
            flow_name="my-sync-flow", module=module_name, function="target"
        )

        assert validate_definitions([definition]).is_valid is True

        module.__dict__["target"] = flows.declared_name_flow
        second = validate_definitions([definition])

        assert "does not match" in second.failures[0].messages[0]
    finally:
        del sys.modules[module_name]


@pytest.mark.parametrize(
    "make_definitions",
    [
        pytest.param(lambda offenders: WorkflowCatalogue(*offenders), id="catalogue"),
        pytest.param(list, id="list-group"),
        pytest.param(
            lambda offenders: (offender for offender in offenders), id="generator"
        ),
    ],
)
def test_validate_definitions_accepts_any_iterable(
    # Any: the parametrized values return deliberately different iterable types.
    make_definitions: Any,
) -> None:
    offenders = (_valid("nightly"), _missing_module(), _missing_attribute())

    report = validate_definitions(make_definitions(offenders))

    assert [failure.key for failure in report.failures] == [
        "gone-module/run",
        "my-sync-flow/renamed",
    ]


# ---------------------------------------------------------------------------
# The two halves of the same guarantee: never raise, never silently pass
# ---------------------------------------------------------------------------
#
# ``validate_definitions`` promises both that it never raises for an invalid
# definition and that it never reports an invalid one as valid, and the two hold
# together: a crash names no offender at all, and a rejection quietly dropped
# turns a consumer's CI green on a payload Prefect will refuse at deploy time.
# The tests below drive the defensive paths that keep both true, with hand-built
# stand-ins standing where a future Prefect or pydantic would.

LAZY_MODULE = "opsmill_prefect_extras_lazy_export_fixture"
UNPRINTABLE_MODULE = "opsmill_prefect_extras_unprintable_import_fixture"

UNPRINTABLE_MODULE_SOURCE = '''
"""A module that fails to import, with an exception that cannot be printed."""


class Unprintable(RuntimeError):
    """An exception whose own ``__str__`` raises."""

    def __str__(self) -> str:
        raise RuntimeError("message rendering failed")


raise Unprintable()
'''
"""Fixture source for the unprintable-import case, written out per test."""


@contextmanager
def _lazy_export_module(exc: BaseException) -> Iterator[str]:
    """Register a module that exports names lazily and fails while doing it.

    The mainstream lazy-export pattern -- a module-level ``__getattr__``
    (PEP 562) importing a backing module on demand -- means reading a name off
    the module runs import machinery, so it can raise anything at all instead of
    the ``AttributeError`` a plain module would.

    Args:
        exc: What the module raises when a name is read off it.

    Yields:
        The registered module's dotted name.
    """
    module = types.ModuleType(LAZY_MODULE)

    def _module_getattr(name: str) -> object:
        raise exc

    module.__dict__["__getattr__"] = _module_getattr
    sys.modules[LAZY_MODULE] = module
    try:
        yield LAZY_MODULE
    finally:
        del sys.modules[LAZY_MODULE]


class _RejectionWithoutErrors(Exception):
    """A schema rejection carrying no ``errors`` attribute at all."""


class _StandInRejection(Exception):
    """A schema rejection whose ``errors`` is whatever a test needs it to be.

    Stands in for a future Prefect or pydantic whose rejection is not shaped the
    way this library reads it by duck typing today.
    """

    def __init__(self, errors: Any) -> None:
        """Carry ``errors`` verbatim.

        Args:
            errors: What the stand-in exposes as its ``errors`` attribute.
                Any: a malformed shape is the whole point, so no narrower
                type describes the values passed here.
        """
        super().__init__("stand-in rejection")
        self.errors = errors


def _reject_with(monkeypatch: pytest.MonkeyPatch, exc: Exception) -> None:
    """Make Prefect's schema construction raise ``exc`` for this test.

    Args:
        monkeypatch: The active pytest patcher, which undoes this afterwards.
        exc: The stand-in rejection to raise instead of Prefect's own.
    """

    # Any: stands in for ``DeploymentCreate``, which refuses every call unread.
    def _refuse(**_kwargs: Any) -> None:
        raise exc

    monkeypatch.setattr(prefect_input_validation, "DeploymentCreate", _refuse)


def test_a_lazy_export_failure_is_reported_rather_than_raised() -> None:
    """Reading a lazily exported name can fail with anything, not just AttributeError.

    Catching only ``AttributeError`` at the attribute step would let one such
    definition in a consumer's catalogue crash the whole aggregate report,
    naming no offender at all -- the opposite of the one-CI-failure-names-every-
    offender guarantee. The exception's type is carried into the message so the
    cause is not lost.
    """
    exc = RuntimeError("impl blew up")
    with _lazy_export_module(exc) as module_name:
        definition = WorkflowDefinition(
            flow_name="lazily-exported",
            module=module_name,
            function="not_exported_yet",
        )

        report = validate_definitions([definition])

    assert report.is_valid is False
    assert len(report.failures) == 1
    messages = report.failures[0].messages
    assert len(messages) == 1
    assert type(exc).__name__ in messages[0]
    assert "not_exported_yet" in messages[0]


def test_a_lazy_export_attribute_error_keeps_its_own_diagnostic() -> None:
    """A lazily exported name's ``AttributeError`` carries the only real cause.

    A module-level ``__getattr__`` (PEP 562) raising :exc:`AttributeError` is the
    one attribute failure whose exception text says more than "the name is not
    there" -- it knows which backing module or export map let the name go
    missing. Discarding that text would leave an operator with nothing but the
    stock "was the flow function renamed or moved?", which is the wrong lead
    entirely here.
    """
    diagnostic = "lazy export map has no entry for it since the 2.0 split"
    with _lazy_export_module(AttributeError(diagnostic)) as module_name:
        definition = WorkflowDefinition(
            flow_name="lazily-exported",
            module=module_name,
            function="not_exported_yet",
        )

        report = validate_definitions([definition])

    assert report.is_valid is False
    messages = report.failures[0].messages
    assert len(messages) == 1
    message = messages[0]
    assert diagnostic in message
    assert "AttributeError" in message
    # The actionable sentence is still there: the name really is unreadable.
    assert "not_exported_yet" in message
    assert "renamed or moved?" in message


def test_an_ordinary_missing_attribute_is_not_reported_twice() -> None:
    """The stock phrasing states what the message already says.

    The interpreter's own text for a missing module attribute is the defect
    message's first clause almost word for word, so appending it would print the
    same fact twice in the report a consumer reads.
    """
    definition = _missing_attribute()

    report = validate_definitions([definition])

    message = report.failures[0].messages[0]
    assert message.count("has no attribute") == 1
    assert "AttributeError" not in message


def test_an_import_failure_that_cannot_be_printed_is_still_a_defect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rendering the defect message runs inside the broad import handler.

    ``str()`` on a target module's own exception is third-party code and can
    itself raise. The message then has to degrade to naming the exception's type
    rather than escaping the report.
    """
    (tmp_path / f"{UNPRINTABLE_MODULE}.py").write_text(
        UNPRINTABLE_MODULE_SOURCE, encoding="utf-8"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    definition = WorkflowDefinition(
        flow_name="unprintable-import",
        module=UNPRINTABLE_MODULE,
        function="anything",
    )

    report = validate_definitions([definition])

    assert len(report.failures) == 1
    messages = report.failures[0].messages
    assert len(messages) == 1
    assert "could not be imported" in messages[0]
    assert "Unprintable" in messages[0]
    assert UNPRINTABLE_MODULE not in sys.modules


@pytest.mark.parametrize(
    "make_rejection",
    [
        pytest.param(_RejectionWithoutErrors, id="no-errors-attribute"),
        pytest.param(
            lambda: _StandInRejection(lambda: [42, "entry", None]),
            id="errors-returns-non-mappings",
        ),
    ],
)
def test_a_rejection_this_library_cannot_read_is_still_one_defect(
    monkeypatch: pytest.MonkeyPatch,
    # Any: each parametrized value builds a differently malformed stand-in.
    make_rejection: Any,
) -> None:
    """A rejection is never dropped, however unreadable -- dropping it says "valid".

    Both shapes defeat the entry read: a rejection with no ``errors()`` at all,
    and one whose entries are not the mappings pydantic documents. In each case
    Prefect *did* refuse the payload, so the report must still show one defect
    naming the refusing exception -- silence here is the false pass this report
    exists to prevent, and an escape is the crash it exists to prevent.
    """
    rejection = make_rejection()
    _reject_with(monkeypatch, rejection)

    report = validate_definitions([_valid_async()])

    assert report.is_valid is False
    assert len(report.failures) == 1
    messages = report.failures[0].messages
    assert len(messages) == 1
    assert type(rejection).__name__ in messages[0]


def _flows_module() -> Any:
    """Import the real-``Flow`` fixture module.

    Any: a module object's attributes are dynamic by nature.
    """
    return __import__(FLOWS_MODULE, fromlist=["my_sync_flow"])


# ---------------------------------------------------------------------------
# The shipped pytest helper
# ---------------------------------------------------------------------------

OPTIMIZED_HELPER_SCRIPT = '''
"""Run the shipped helper against a broken definition under ``python -O``.

Deliberately free of ``assert`` statements: under ``-O`` they would vanish,
which is the very failure mode this script exists to detect.
"""

if __debug__:
    raise SystemExit("python -O did not take effect: __debug__ is True")

from opsmill_prefect_extras.workflows.definitions import WorkflowDefinition
from opsmill_prefect_extras.workflows.validation import assert_valid_definitions

BROKEN = WorkflowDefinition(
    flow_name="gone-module",
    module="tests.workflows.no_such_module",
    function="my_sync_flow",
)

try:
    assert_valid_definitions([BROKEN])
except AssertionError as exc:
    print("RAISED AssertionError")
    print("MENTIONS_MODULE", "tests.workflows.no_such_module" in str(exc))
else:
    raise SystemExit("the helper did not raise under python -O")
'''
"""The optimization-safety check, run in a subprocess with ``-O``."""


def test_helper_returns_none_on_valid_and_empty_catalogues() -> None:
    catalogue = WorkflowCatalogue(
        _valid("nightly"), _valid_async(), _valid_declared_name()
    )

    assert assert_valid_definitions(catalogue) is None
    assert assert_valid_definitions(WorkflowCatalogue()) is None


def test_helper_raises_assertion_error_whose_message_is_the_summary() -> None:
    catalogue = WorkflowCatalogue(_valid("nightly"), _missing_attribute(), _bad_cron())
    expected = validate_definitions(catalogue).summary()

    with pytest.raises(AssertionError) as excinfo:
        assert_valid_definitions(catalogue)

    assert str(excinfo.value) == expected


def test_helper_message_names_every_failing_definition_and_defect() -> None:
    """The renamed-attribute definition is also broken on the input axis, so the
    message has to carry two defects for that one entry as well.
    """
    two_defects = WorkflowDefinition(
        flow_name="my-sync-flow",
        deployment_name="both-axes",
        module=FLOWS_MODULE,
        function="renamed_away",
        cron="not a cron",
    )
    catalogue = WorkflowCatalogue(
        _valid("nightly"),
        _missing_module(),
        _exploding_module(),
        _not_a_flow(),
        _name_mismatch(),
        _bad_collision_strategy(),
        two_defects,
    )

    with pytest.raises(AssertionError) as excinfo:
        assert_valid_definitions(catalogue)

    message = str(excinfo.value)
    for key in (
        "gone-module/run",
        "explodes-on-import/run",
        "not-a-flow/run",
        "declared-name-flow/run",
        "my-sync-flow/bad-strategy",
        "my-sync-flow/both-axes",
    ):
        assert key in message
    for fragment in (
        MISSING_MODULE,
        SENTINEL_MESSAGE,
        "plain_function",
        "'declared-name'",
        "NOT_A_STRATEGY",
        "renamed_away",
        "not a cron",
    ):
        assert fragment in message
    assert "my-sync-flow/nightly" not in message


def test_helper_raises_for_a_rejection_this_library_cannot_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unreadable rejection reaches the helper as the failure it is.

    ``AssertionError`` is what a consumer's CI is written against. An exception
    escaping the read of a third-party error shape would surface as itself
    instead, from a call the consumer made on their own catalogue.
    """
    _reject_with(monkeypatch, _RejectionWithoutErrors("schema said no"))

    with pytest.raises(AssertionError) as excinfo:
        assert_valid_definitions(WorkflowCatalogue(_valid_async()))

    message = str(excinfo.value)
    assert "my-async-flow/run" in message
    assert _RejectionWithoutErrors.__name__ in message


def test_a_consumer_gets_the_ci_check_from_one_import() -> None:
    """This is a consumer's whole test body: import the helper from
    ``opsmill_prefect_extras.workflows`` and hand it the catalogue.
    """
    from opsmill_prefect_extras.workflows import (
        assert_valid_definitions as public_helper,
    )

    catalogue = WorkflowCatalogue(_valid("nightly"), _valid_async())

    assert public_helper(catalogue) is None

    with pytest.raises(AssertionError):
        public_helper(WorkflowCatalogue(_missing_attribute()))


def test_helper_still_raises_under_python_optimization() -> None:
    """``python -O`` strips ``assert`` statements; the helper must survive.

    A consumer's non-pytest CI runner may well use ``-O``. An ``assert``-based
    implementation would silently pass every broken catalogue there, which is
    why the helper raises ``AssertionError`` explicitly.
    """
    result = subprocess.run(
        [sys.executable, "-O", "-c", OPTIMIZED_HELPER_SCRIPT],
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "RAISED AssertionError" in result.stdout
    assert "MENTIONS_MODULE True" in result.stdout


# ---------------------------------------------------------------------------
# The public import surface
# ---------------------------------------------------------------------------

CONTRACT_IMPORT_SURFACE: dict[str, object] = {
    "WorkflowDefinition": WorkflowDefinition,
    "WorkflowCatalogue": WorkflowCatalogue,
    "DuplicateWorkflowError": DuplicateWorkflowError,
    "DefinitionFailure": DefinitionFailure,
    "ValidationReport": ValidationReport,
    "validate_definitions": validate_definitions,
    "assert_valid_definitions": assert_valid_definitions,
}
"""The seven names the contract's import block promises, and what each one is.

These are the names of the single ``from opsmill_prefect_extras.workflows
import (...)`` block the public API promises, whose whole premise is that one
import *is* the surface. Every other test in this suite reaches into the
submodules, so without the two tests below the re-export layer is unguarded: a
name dropped from it would break every consumer while the suite stayed green.
"""


def test_the_package_exports_exactly_the_contract_import_block() -> None:
    exported = workflows_package.__all__

    assert set(exported) == set(CONTRACT_IMPORT_SURFACE)
    assert len(exported) == len(CONTRACT_IMPORT_SURFACE)


@pytest.mark.parametrize(("name", "expected"), sorted(CONTRACT_IMPORT_SURFACE.items()))
def test_each_exported_name_is_the_submodule_object(
    name: str, expected: object
) -> None:
    assert getattr(workflows_package, name) is expected
