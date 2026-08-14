"""Aggregate validation of workflow definitions, plus the shipped pytest helper.

One call over a whole catalogue returns one typed report of *every* defect in
*every* definition, so a rename that breaks a declared reference is caught in
CI with a single assertion rather than one failure at a time.

Depends *downward* on ``definitions.py`` -- this feature's common internal
module -- and on the private ``_prefect_input_validation`` adapter beside it;
never sideways on a sibling feature.

Two things about validity are deliberate:

* **The library invents no validity rules.** A schedule, concurrency or
  tag value is invalid exactly when Prefect's own client-side
  deployment-input schema rejects it. At Prefect 3.8.1 that makes an
  unparseable cron and an unknown collision strategy failures, while a
  non-positive concurrency limit and coercible non-string tags are *not* --
  Prefect accepts them.
* **This module is the facade, not the adapter.** Asking Prefect and reading
  its verdict -- every ``prefect.client.schemas`` name, every assumption about
  pydantic's error entries -- lives in the private
  ``_prefect_input_validation`` module, so the feature's version-sensitive
  knowledge sits in one file and the report types every consumer touches stay
  clear of it.

Nothing here contacts a Prefect server or the network: the schema construction
is a local pydantic parse in that adapter.

A consumer's whole CI check is one import of :func:`assert_valid_definitions`
and one call on their catalogue; the ``opsmill_prefect_extras.workflows``
package docstring shows it in place, as part of an example the test suite
executes.
"""

from __future__ import annotations

import importlib
from collections.abc import Iterable
from dataclasses import dataclass

from prefect.flows import Flow

from opsmill_prefect_extras.workflows._prefect_input_validation import (
    exception_text,
    rejected_input_messages,
)
from opsmill_prefect_extras.workflows.definitions import (
    WorkflowDefinition,
    _not_a_flow_message,
)


@dataclass(frozen=True, kw_only=True, slots=True)
class DefinitionFailure:
    """One invalid definition, with every problem found in it.

    A definition broken several ways appears once, carrying all its messages --
    the report never splits one definition across entries.

    Attributes:
        definition: The failing definition itself, so a caller can inspect or
            re-render it without a second lookup.
        messages: Every problem found, each a human-readable description naming
            the missing attribute, the rejected value, or whatever else
            identifies it: at most one from the resolution axis (the deepest
            one reachable) plus one per value Prefect rejected. Never empty.
    """

    definition: WorkflowDefinition
    messages: tuple[str, ...]

    @property
    def key(self) -> str:
        """The failing definition's ``flow_name/deployment_name`` key.

        Derived from :attr:`definition` rather than stored, so the identity
        :meth:`ValidationReport.summary` prints -- the text
        :func:`assert_valid_definitions` raises with -- cannot disagree with the
        definition it reports.

        Returns:
            ``definition.key``.
        """
        return self.definition.key

    def __post_init__(self) -> None:
        """Reject a failure with nothing wrong in it.

        Raises:
            ValueError: If ``messages`` is empty. A definition with no problem
                is not a failure, and an empty entry would make the report lie
                about which definitions are valid.
        """
        if not self.messages:
            raise ValueError(
                f"{self.key}: a DefinitionFailure must carry at least one "
                f"message -- a definition with no problem is not a failure"
            )


@dataclass(frozen=True, kw_only=True, slots=True)
class ValidationReport:
    """The typed result of validating a group of definitions.

    Attributes:
        failures: One entry per invalid definition, in the order the
            definitions were supplied. Empty means everything is valid.
    """

    failures: tuple[DefinitionFailure, ...]

    @property
    def is_valid(self) -> bool:
        """Whether every validated definition was sound.

        Returns:
            ``True`` when there are no failures -- including the empty-input
            case, which is valid by definition.
        """
        return not self.failures

    def summary(self) -> str:
        """Render the whole report as human-readable text.

        This is the exact text :func:`assert_valid_definitions` raises with: a
        header line, then every failing definition's key, then every problem
        under it.

        Returns:
            A multi-line string naming every failing definition and each of its
            problems; a single reassuring line when the report is valid.
        """
        if not self.failures:
            return "all workflow definitions are valid"

        lines = [f"{len(self.failures)} invalid workflow definition(s):"]
        for failure in self.failures:
            lines.append(f"  {failure.key}")
            lines.extend(f"    {message}" for message in failure.messages)
        return "\n".join(lines)


def validate_definitions(
    definitions: Iterable[WorkflowDefinition],
) -> ValidationReport:
    """Validate every definition and report every defect in one pass.

    Each definition is checked on two independent axes, and both contribute:

    1. **Resolution** -- import the module, read the attribute off it, confirm
       it is a Prefect flow, confirm its name matches the declared one. The
       deepest reachable problem is reported (a missing module says nothing
       about the attribute, so only the import failure appears).
    2. **Deployment input** -- render the definition and let Prefect's own
       schema judge it, one message per value it rejects.

    Resolution happens fresh on every call: nothing is cached, so a rename or
    reload inside the process is seen by the next call.

    Args:
        definitions: Any iterable of definitions -- a
            :class:`~opsmill_prefect_extras.workflows.catalogue.WorkflowCatalogue`
            (which iterates definitions), a definition group, a chain of both.
            Consumed once. An empty iterable is valid input.

    Returns:
        One :class:`ValidationReport`, with failures in input order. Valid
        definitions are absent from it.

    Raises:
        Nothing for an invalid definition -- not even the exception a target
        module raises while being imported. Raising is
        :func:`assert_valid_definitions`'s job.

    Example:
        ::

            report = validate_definitions(CATALOGUE)
            if not report.is_valid:
                print(report.summary())
    """
    failures: list[DefinitionFailure] = []
    for definition in definitions:
        messages: list[str] = []
        resolution_message = _resolution_message(definition)
        if resolution_message is not None:
            messages.append(resolution_message)
        messages.extend(rejected_input_messages(definition))
        if messages:
            failures.append(
                DefinitionFailure(definition=definition, messages=tuple(messages))
            )
    return ValidationReport(failures=tuple(failures))


def assert_valid_definitions(definitions: Iterable[WorkflowDefinition]) -> None:
    """Fail a test run when any definition is invalid -- the shipped CI check.

    Wraps :func:`validate_definitions` so a consumer gets the whole check as
    one import. The failure is raised explicitly rather than through an
    ``assert`` statement, so it survives ``python -O`` in a non-pytest CI
    runner, where ``assert`` statements are stripped out.

    Args:
        definitions: Any iterable of definitions -- typically the consumer's
            whole catalogue.

    Returns:
        ``None`` when every definition is valid.

    Raises:
        AssertionError: If any definition is invalid. The message is
            :meth:`ValidationReport.summary` -- every failing definition with
            every problem, not just the first.

    Example:
        A consumer's whole test body is one import of this function and one
        call on their catalogue. The ``opsmill_prefect_extras.workflows``
        package docstring shows that test module in full, and the suite
        executes it.
    """
    report = validate_definitions(definitions)
    if not report.is_valid:
        raise AssertionError(report.summary())


def _resolution_message(definition: WorkflowDefinition) -> str | None:
    """Follow the import reference and describe the deepest problem found.

    Args:
        definition: The definition whose reference is being resolved.

    Returns:
        The deepest reachable resolution problem, or ``None`` when the
        reference resolves to a correctly named Prefect flow.
    """
    try:
        module = importlib.import_module(definition.module)
    # A module body propagates its own exception type unwrapped, so catching
    # ImportError alone would let an exploding module crash validation.
    except Exception as exc:
        return (
            f"module {definition.module!r} could not be imported: {exception_text(exc)}"
        )

    try:
        resolved = getattr(module, definition.function)
    except AttributeError as exc:
        return (
            f"module {definition.module!r} has no attribute "
            f"{definition.function!r} -- was the flow function renamed or "
            f"moved?{_attribute_error_detail(definition, exc)}"
        )
    # A module that exports names lazily (a module-level ``__getattr__``,
    # PEP 562) imports the backing module *here*, so reading an attribute off
    # it can raise anything at all -- the same failure the import step above
    # catches, arriving one step later. Letting it through would crash the
    # whole aggregate report instead of naming this one offender.
    except Exception as exc:
        return (
            f"module {definition.module!r} raised while attribute "
            f"{definition.function!r} was being read off it: "
            f"{exception_text(exc)} -- a lazily exported name imports its "
            f"backing module at this point"
        )

    if not isinstance(resolved, Flow):
        # The wording is shared with ``WorkflowDefinition.load``, which
        # reports the same fact directly; only the key prefix differs.
        return _not_a_flow_message(
            module=definition.module,
            function=definition.function,
            resolved=resolved,
        )

    if resolved.name != definition.flow_name:
        return (
            f"declared flow name {definition.flow_name!r} does not match "
            f"the resolved flow's name {resolved.name!r} "
            f"({definition.module}:{definition.function})"
        )

    return None


def _attribute_error_detail(definition: WorkflowDefinition, exc: AttributeError) -> str:
    """Render an attribute error's own text, unless it says nothing new.

    Reading a name off a plain module raises with exactly the fact the defect
    message already states, so repeating it would double-report it. A module that
    exports names lazily (a module-level ``__getattr__``, PEP 562) raises its own
    :exc:`AttributeError` instead -- naming the backing module, the export map,
    or whatever else it knows -- and that text is the only place the real cause
    appears.

    Args:
        definition: The definition whose attribute was being read.
        exc: The attribute error raised while reading it.

    Returns:
        The exception's text, parenthesized and ready to append to the defect
        message, or an empty string when it is the interpreter's stock phrasing
        for a missing module attribute.
    """
    text = exception_text(exc)
    stock = (
        f"AttributeError: module {definition.module!r} has no attribute "
        f"{definition.function!r}"
    )
    return "" if text == stock else f" ({text})"
