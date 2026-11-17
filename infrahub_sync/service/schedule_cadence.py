"""Cadence of a schedule: validate a cron expression and a time zone, and compute start times in UTC.

Every answer comes from the server-side ``CronSchedule`` of the installed Prefect, the class that
Prefect's scheduler uses. The start times that an operator views are therefore the start times
that Prefect runs, including a start in the hour that a daylight-saving change removes or repeats.

The functions are synchronous and do no input or output, so an API route and the worker flow call
the same ones. Only modules of ``infrahub_sync.service`` import this module.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Literal, TypeVar

from prefect.server.schemas.schedules import CronSchedule
from pydantic import ValidationError

if TYPE_CHECKING:
    from collections.abc import Coroutine

__all__ = ["MAX_STARTS", "CadenceField", "InvalidCadenceError", "next_start", "starts_between", "validate"]

CadenceField = Literal["cron", "timezone"]

CRON_FIELD_COUNT = 5
MAX_STARTS = 1000
"""The largest ``limit`` of :func:`starts_between`. Prefect checks at most this many dates in one call."""

_T = TypeVar("_T")


class InvalidCadenceError(ValueError):
    """The cron expression or the time zone of a cadence is not valid.

    ``field`` names the value that is refused, ``"cron"`` or ``"timezone"``. The message holds the reason.
    """

    def __init__(self, reason: str, *, field: CadenceField) -> None:
        super().__init__(reason)
        self.field: CadenceField = field
        self.reason = reason


def validate(cron: str, timezone: str) -> None:  # pylint: disable=redefined-outer-name
    """Check a cadence.

    Raises:
        InvalidCadenceError: the expression does not have five fields, Prefect refuses the
            expression or the time zone name, or the expression has no start time.
    """
    schedule = _schedule(cron, timezone)
    _dates(schedule, cron, count=1, start=datetime.now(tz=_UTC), end=None)


def next_start(cron: str, timezone: str, after: datetime) -> datetime:  # pylint: disable=redefined-outer-name
    """Return the first start of the cadence that is later than ``after``, in UTC.

    Raises:
        InvalidCadenceError: the cadence is not valid, or it has no start after ``after``.
        ValueError: ``after`` has no time zone.
    """
    _require_time_zone("after", after)
    schedule = _schedule(cron, timezone)
    # Prefect returns a start that is equal to ``start``; ask for two dates and keep the later ones.
    later = [date for date in _dates(schedule, cron, count=2, start=after, end=None) if date > after]
    if not later:
        reason = f'The cron expression "{cron}" has no start time after {after.isoformat()}.'
        raise InvalidCadenceError(reason, field="cron")
    return later[0]


def starts_between(  # pylint: disable=redefined-outer-name
    cron: str, timezone: str, after: datetime, until: datetime, limit: int
) -> tuple[datetime, ...]:
    """Return the starts of the cadence later than ``after`` and not later than ``until``, in UTC.

    The starts are in increasing order. At most ``limit`` starts are returned, the earliest ones.

    Raises:
        InvalidCadenceError: the cadence is not valid.
        ValueError: ``after`` or ``until`` has no time zone, or ``limit`` is not between 1 and
            ``MAX_STARTS``.
    """
    _require_time_zone("after", after)
    _require_time_zone("until", until)
    if not 1 <= limit <= MAX_STARTS:
        message = f"limit must be between 1 and {MAX_STARTS}, not {limit}."
        raise ValueError(message)
    schedule = _schedule(cron, timezone)
    if until <= after:
        return ()
    # Prefect returns a start that is equal to ``start``; ask for one more date and keep the later ones.
    dates = _dates(schedule, cron, count=limit + 1, start=after, end=until)
    return tuple(date for date in dates if date > after)[:limit]


_UTC = timezone.utc


def _schedule(cron: str, timezone_name: str) -> CronSchedule:
    """Build the Prefect schedule of a cadence, or raise the error that names the refused value."""
    field_count = len(cron.split())
    if field_count != CRON_FIELD_COUNT:
        reason = (
            f'The cron expression "{cron}" has {field_count} fields. A cron expression has five fields: '
            "minute, hour, day of the month, month and day of the week."
        )
        raise InvalidCadenceError(reason, field="cron")
    if not timezone_name.strip():
        # Prefect reads an empty name as "no time zone" and applies its own default.
        reason = "The time zone name is empty."
        raise InvalidCadenceError(reason, field="timezone")
    try:
        return CronSchedule(cron=cron, timezone=timezone_name)
    except ValidationError as exc:
        errors = exc.errors()
        reason = "; ".join(str(error["msg"]).removeprefix("Value error, ") for error in errors)
        # Prefect checks the expression in a field validator and the time zone in a model validator,
        # so only an error of the expression carries a field location.
        field: CadenceField = "cron" if any(error["loc"] == ("cron",) for error in errors) else "timezone"
        raise InvalidCadenceError(reason, field=field) from exc


def _dates(schedule: CronSchedule, cron: str, *, count: int, start: datetime, end: datetime | None) -> list[datetime]:
    """Return up to ``count`` dates of the schedule from ``start`` to ``end``, both included, in UTC."""
    try:
        dates = _result(schedule.get_dates(n=count, start=start, end=end))
    except ValueError as exc:
        # The cron library raises a ValueError when it finds no date, for example for 30 February.
        reason = f'The cron expression "{cron}" has no start time: {exc}.'
        raise InvalidCadenceError(reason, field="cron") from exc
    # Prefect returns a pendulum or a zoned value, by Python version; a timestamp gives a plain datetime.
    return [datetime.fromtimestamp(date.timestamp(), tz=_UTC) for date in dates]


def _result(coroutine: Coroutine[object, object, _T]) -> _T:
    """Return the result of a coroutine that finishes without waiting for anything.

    ``CronSchedule.get_dates`` is declared ``async`` and awaits nothing. Running it to its end here
    gives one implementation to a caller inside a running event loop and to a caller with none.
    """
    try:
        coroutine.send(None)
    except StopIteration as finished:
        return finished.value
    coroutine.close()
    message = "Prefect's CronSchedule.get_dates waited for an event loop; schedule_cadence expects it not to."
    raise RuntimeError(message)


def _require_time_zone(name: str, value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        message = f"{name} must carry a time zone."
        raise ValueError(message)
