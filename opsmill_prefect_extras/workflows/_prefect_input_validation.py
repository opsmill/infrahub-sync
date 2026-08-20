"""Prefect's and pydantic's error shapes, understood in exactly one place.

This module is the boundary that understands third-party internals: the shape of
a pydantic validation error's entries (``loc`` / ``msg`` / ``input``), the way
Prefect's schedule union labels the branch each complaint came from, and the
``DeploymentCreate`` construction that asks Prefect for its verdict at all.
Those are the feature's only real version-sensitive knowledge, so they are
quarantined here rather than spread through the stable domain model: nothing in
``validation.py`` -- which owns the public report types -- reads a third-party
error entry, and nothing outside this module names a
``prefect.client.schemas`` type.

Asking Prefect at all is a deliberate decision: a value is invalid exactly when
Prefect's own client-side deployment-input schema rejects it, and this library
invents no validity rules on top of that verdict. What lives here is only the
*reading* of it, which is where the version risk actually sits: at Prefect 3.8.1
one unparseable cron arrives as six error entries, five of them branch noise.

Private to the feature: nothing here is exported from
``opsmill_prefect_extras.workflows``, and ``validation.py`` is the public facade
over it. Depends *downward* on ``definitions.py`` -- this feature's common
internal module -- and never upward on the facade, so the two cannot cycle.

Nothing here contacts a Prefect server or the network: the schema construction
is a local pydantic parse, and the sentinel ``flow_id`` never leaves this
module.
"""

from __future__ import annotations

from collections.abc import Mapping

# Any: values read off a third-party schema rejection, whose shapes come from
# Prefect's own dependency; the readers below narrow each one at runtime.
from typing import Any, TypeAlias, cast
from uuid import UUID

from prefect.client.schemas.actions import DeploymentCreate

from opsmill_prefect_extras.workflows.definitions import WorkflowDefinition

# TypeAlias, not a PEP 695 ``type`` statement: this package supports Python
# 3.11 (``requires-python = ">=3.11"``), where the ``type`` statement does not
# parse.
ErrorEntry: TypeAlias = Mapping[str, Any]
"""One structured entry off a schema rejection -- an untyped third-party mapping
from field name to path, complaint or rejected input."""

ErrorLocation: TypeAlias = tuple[Any, ...]
"""The path to a rejected value, mixing field names with sequence indices."""

_SENTINEL_FLOW_ID: UUID = UUID("00000000-0000-0000-0000-000000000000")
"""Stand-in for the server-assigned ``flow_id``, which ``DeploymentCreate``
requires but no offline check can know. Internal to validation: it is never
rendered, returned, or sent anywhere."""

_SCHEDULES_FIELD: str = "schedules"
_SCHEDULE_FIELD: str = "schedule"
_SCHEDULE_TYPE_MARKER: str = "Schedule"
_CRON_BRANCH: str = "CronSchedule"


def rejected_input_messages(definition: WorkflowDefinition) -> tuple[str, ...]:
    """Ask Prefect whether it accepts this definition's deployment input.

    The question is put the way Prefect's own client puts it -- by constructing
    ``DeploymentCreate`` -- with a sentinel ``flow_id`` standing in for the
    server-assigned one. Purely local; nothing is sent anywhere.

    Args:
        definition: The definition to render and submit to the schema.

    Returns:
        One message per rejected value, empty when Prefect accepts the payload.
        Reporting those messages against their definition is the facade's job,
        not this module's.
    """
    try:
        DeploymentCreate(flow_id=_SENTINEL_FLOW_ID, **definition.to_deployment_input())
    # Any failure here is *treated as* invalid input -- a deliberate fallback
    # policy, not a certainty about what Prefect meant. It keeps one bad
    # definition from destroying the aggregate report, at the price of also
    # classifying an unexpected Prefect internal error, or a bug in the payload
    # construction above, as invalid input.
    except Exception as exc:
        return _rejection_messages(exc)
    return ()


def exception_text(exc: BaseException) -> str:
    """Describe an exception as ``TypeName: message``, degrading if it cannot.

    Shared with the facade, whose resolution checks read exceptions inside the
    same kind of broad handler.

    Args:
        exc: The exception to describe.

    Returns:
        ``"TypeName: message"``, or the type name alone when the exception's own
        ``__str__`` raises. Rendering runs inside the broad handlers this
        feature relies on, so a rendering failure has to degrade to a
        still-useful defect rather than escape
        :func:`~opsmill_prefect_extras.workflows.validation.validate_definitions`.
    """
    try:
        return f"{type(exc).__name__}: {exc}"
    except Exception:
        return type(exc).__name__


def _rejection_messages(exc: Exception) -> tuple[str, ...]:
    """Turn a schema rejection into one message per rejected value.

    The read assumes pydantic's documented ``ValidationError.errors()``
    contract, duck-called rather than imported: pydantic reaches this library
    only through Prefect, so naming it directly would make an undeclared
    package part of this feature's dependencies. Any deviation from that
    contract -- a rejection with no ``errors()``, an entry that cannot be
    read -- degrades the whole rejection to the exception's own text rather
    than escaping, preserving
    :func:`~opsmill_prefect_extras.workflows.validation.validate_definitions`'s
    promise never to raise.

    Returns:
        One message per rejected value, or the exception's own text when the
        entries could not be read -- never empty, so a rejection is always at
        least one defect.
    """
    try:
        entries: Any = cast("Any", exc).errors()
        messages = [
            _rejection_message(entry)
            for entry in entries
            if not _is_other_schedule_branch(_entry_loc(entry))
        ]
    except Exception:
        messages = []
    if not messages:
        messages.append(exception_text(exc))
    return tuple(messages)


def _entry_loc(entry: ErrorEntry) -> ErrorLocation:
    """Read an entry's location path."""
    return tuple(entry.get("loc", ()))


def _schedule_branch(location: ErrorLocation) -> tuple[int, str] | None:
    """Locate the schedule-union branch in an error path, if one is present."""
    if len(location) < 5:
        return None
    for schedules_index, part in enumerate(location[:-4]):
        if (
            part != _SCHEDULES_FIELD
            or not isinstance(location[schedules_index + 1], int)
            or location[schedules_index + 2] != _SCHEDULE_FIELD
        ):
            continue
        for branch_index in range(schedules_index + 3, len(location) - 1):
            branch = location[branch_index]
            if isinstance(branch, str) and _SCHEDULE_TYPE_MARKER in branch:
                return branch_index, branch
    return None


def _is_other_schedule_branch(location: ErrorLocation) -> bool:
    """Whether an error is noise from a schedule branch we never render.

    A rendered cron schedule is checked against every branch of Prefect's
    schedule union, and each branch that is not the cron one complains about the
    shape it expected instead: at Prefect 3.8.1 a single unparseable cron
    produces six errors, of which one is the cron branch's real verdict and five
    are branch noise -- three rejecting ``cron`` as an extra input (interval,
    rrule, no-schedule) and two demanding the ``interval`` and ``rrule`` fields
    the payload never carried. Only the cron branch's verdict is about the value
    the definition actually holds; reporting the rest would bury it.
    """
    branch = _schedule_branch(location)
    return branch is not None and _CRON_BRANCH not in branch[1]


def _rejection_message(entry: ErrorEntry) -> str:
    """Render a rejected value as its path, Prefect's complaint, and the value."""
    location = _entry_loc(entry)
    path = _rendered_path(location)
    complaint = str(entry.get("msg", "rejected by Prefect"))
    if "input" in entry:
        return f"{path}: {complaint} (got {entry['input']!r})"
    return f"{path}: {complaint}"


def _rendered_path(location: ErrorLocation) -> str:
    """Render an error location as a dotted path, e.g. ``schedules.0.schedule.cron``.

    The schedule-union branch tag is dropped: it names an internal schema
    branch, not a field of the payload the definition rendered.
    """
    branch = _schedule_branch(location)
    if branch is None:
        parts = location
    else:
        index, _ = branch
        parts = (*location[:index], *location[index + 1 :])
    return ".".join(str(part) for part in parts) or "<deployment input>"
