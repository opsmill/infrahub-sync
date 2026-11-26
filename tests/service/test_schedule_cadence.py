"""Cadence of a schedule: validation and start times in UTC, computed by Prefect's ``CronSchedule``."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("prefect")

from prefect.server.schemas.schedules import CronSchedule

from infrahub_sync.service.schedule_cadence import (
    MAX_STARTS,
    InvalidCadenceError,
    next_start,
    starts_between,
    validate,
)

UTC = timezone.utc


def _utc(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


def test_valid_cadence_is_accepted() -> None:
    """A five-field expression and a known time zone raise nothing."""
    validate("0 2 * * *", "Europe/Paris")


def test_next_start_is_given_in_utc() -> None:
    """02:00 in Paris in winter is 01:00 UTC, returned as a standard-library datetime in UTC."""
    start = next_start("0 2 * * *", "Europe/Paris", _utc(2026, 1, 15, 12))

    assert start == _utc(2026, 1, 16, 1)
    assert type(start) is datetime
    assert start.utcoffset() == timedelta(0)


def test_next_start_is_strictly_after_the_given_time() -> None:
    """A time that is itself a start of the cadence gives the following start, not the same one."""
    assert next_start("0 2 * * *", "UTC", _utc(2026, 1, 1, 2)) == _utc(2026, 1, 2, 2)


def test_next_start_accepts_a_time_in_another_time_zone() -> None:
    """The offset of ``after`` does not change the answer."""
    paris_noon = datetime(2026, 1, 15, 13, tzinfo=timezone(timedelta(hours=1)))

    assert next_start("0 2 * * *", "Europe/Paris", paris_noon) == _utc(2026, 1, 16, 1)


async def test_functions_run_inside_a_running_event_loop() -> None:
    """The route handlers are coroutines; the same functions answer there."""
    await asyncio.sleep(0)

    assert next_start("0 2 * * *", "UTC", _utc(2026, 1, 1, 12)) == _utc(2026, 1, 2, 2)
    assert starts_between("0 2 * * *", "UTC", _utc(2026, 1, 1, 12), _utc(2026, 1, 3, 12), 10) == (
        _utc(2026, 1, 2, 2),
        _utc(2026, 1, 3, 2),
    )


async def test_starts_are_the_dates_prefect_computes() -> None:
    """The cadence an operator views is the one Prefect's scheduler runs."""
    after, until = _utc(2026, 3, 27, 12), _utc(2026, 4, 2, 12)
    expected = await CronSchedule(cron="30 2 * * *", timezone="Europe/Paris").get_dates(n=10, start=after, end=until)

    assert starts_between("30 2 * * *", "Europe/Paris", after, until, 10) == tuple(
        date.astimezone(UTC) for date in expected
    )


def test_hour_that_a_daylight_saving_change_removes_gives_one_start() -> None:
    """On 29 March 2026 Paris has no 02:30. The night gives one start.

    Prefect places it at 01:30 local time on Python 3.11 and 3.12, and at 03:30 local time on Python 3.13.
    """
    starts = starts_between("30 2 * * *", "Europe/Paris", _utc(2026, 3, 28, 12), _utc(2026, 3, 29, 12), 10)

    assert len(starts) == 1
    assert starts[0] in {_utc(2026, 3, 29, 0, 30), _utc(2026, 3, 29, 1, 30)}


def test_hour_that_a_daylight_saving_change_repeats_gives_one_start() -> None:
    """On 25 October 2026 Paris has 02:30 twice. The night gives one start, the first."""
    starts = starts_between("30 2 * * *", "Europe/Paris", _utc(2026, 10, 24, 12), _utc(2026, 10, 25, 12), 10)

    assert starts == (_utc(2026, 10, 25, 0, 30),)


@pytest.mark.parametrize(
    ("after", "until"),
    [
        pytest.param(_utc(2026, 3, 28, 12), _utc(2026, 3, 30, 12), id="hour-removed"),
        pytest.param(_utc(2026, 10, 24, 12), _utc(2026, 10, 26, 12), id="hour-repeated"),
    ],
)
def test_no_start_is_doubled_around_a_daylight_saving_change(after: datetime, until: datetime) -> None:
    """Two nights give two different starts, in increasing order."""
    starts = starts_between("30 2 * * *", "Europe/Paris", after, until, 10)

    assert len(starts) == 2
    assert starts[0] < starts[1]


@pytest.mark.parametrize(
    "cron",
    [
        pytest.param("bad", id="not-cron"),
        pytest.param("61 2 * * *", id="minute-out-of-range"),
        pytest.param("", id="empty"),
        pytest.param("0 2 * *", id="four-fields"),
        pytest.param("0 0 2 * * *", id="six-fields-with-seconds"),
        pytest.param("@daily", id="alias"),
        pytest.param("0 2 30 2 *", id="date-that-never-comes"),
    ],
)
def test_invalid_expression_raises_the_module_error(cron: str) -> None:
    """An expression Prefect refuses, or one that does not have five fields, names the ``cron`` field."""
    with pytest.raises(InvalidCadenceError) as raised:
        validate(cron, "UTC")

    assert raised.value.field == "cron"
    assert str(raised.value)


@pytest.mark.parametrize(
    "timezone_name",
    [
        pytest.param("Mars/Phobos", id="unknown"),
        pytest.param("europe/paris", id="wrong-case"),
        pytest.param("", id="empty"),
        pytest.param("  ", id="blank"),
    ],
)
def test_unknown_time_zone_raises_the_module_error(timezone_name: str) -> None:
    """A time zone name Prefect does not know names the ``timezone`` field."""
    with pytest.raises(InvalidCadenceError) as raised:
        validate("0 2 * * *", timezone_name)

    assert raised.value.field == "timezone"
    assert str(raised.value)


def test_module_error_is_a_value_error() -> None:
    """Callers that catch ``ValueError`` also catch an invalid cadence."""
    assert issubclass(InvalidCadenceError, ValueError)


def test_start_functions_validate_the_cadence() -> None:
    """The start functions raise the same error as ``validate``."""
    with pytest.raises(InvalidCadenceError):
        next_start("0 0 2 * * *", "UTC", _utc(2026, 1, 1))
    with pytest.raises(InvalidCadenceError):
        starts_between("0 2 * * *", "Mars/Phobos", _utc(2026, 1, 1), _utc(2026, 1, 2), 5)


def test_starts_between_excludes_after_and_includes_until() -> None:
    """The window is open at ``after`` and closed at ``until``."""
    starts = starts_between("0 2 * * *", "UTC", _utc(2026, 1, 1, 2), _utc(2026, 1, 3, 2), 10)

    assert starts == (_utc(2026, 1, 2, 2), _utc(2026, 1, 3, 2))
    assert all(type(start) is datetime and start.utcoffset() == timedelta(0) for start in starts)


def test_starts_between_is_bounded_by_limit() -> None:
    """A cadence of one minute over a year returns the first ``limit`` starts only."""
    starts = starts_between("* * * * *", "UTC", _utc(2026, 1, 1), _utc(2027, 1, 1), 5)

    assert starts == tuple(_utc(2026, 1, 1, 0, minute) for minute in range(1, 6))


def test_starts_between_accepts_the_largest_limit() -> None:
    """The largest limit returns that many starts."""
    starts = starts_between("* * * * *", "UTC", _utc(2026, 1, 1), _utc(2027, 1, 1), MAX_STARTS)

    assert len(starts) == MAX_STARTS
    assert starts[-1] == _utc(2026, 1, 1) + timedelta(minutes=MAX_STARTS)


def test_starts_between_is_empty_when_the_window_holds_no_start() -> None:
    """A window with no start, or one that ends before it begins, gives no start."""
    assert starts_between("0 2 * * *", "UTC", _utc(2026, 1, 1, 3), _utc(2026, 1, 1, 23), 5) == ()
    assert starts_between("0 2 * * *", "UTC", _utc(2026, 1, 5), _utc(2026, 1, 1), 5) == ()


@pytest.mark.parametrize("limit", [0, -1, MAX_STARTS + 1])
def test_starts_between_refuses_a_limit_out_of_range(limit: int) -> None:
    """The limit is between 1 and ``MAX_STARTS``."""
    with pytest.raises(ValueError, match="limit"):
        starts_between("0 2 * * *", "UTC", _utc(2026, 1, 1), _utc(2026, 1, 2), limit)


def test_time_without_a_time_zone_is_refused() -> None:
    """A naive time is not guessed to be in UTC or in the time zone of the schedule."""
    naive = datetime(2026, 1, 1, 12)  # noqa: DTZ001  # the naive value is the case under test

    with pytest.raises(ValueError, match="time zone"):
        next_start("0 2 * * *", "UTC", naive)
    with pytest.raises(ValueError, match="time zone"):
        starts_between("0 2 * * *", "UTC", naive, _utc(2026, 1, 2), 5)
    with pytest.raises(ValueError, match="time zone"):
        starts_between("0 2 * * *", "UTC", _utc(2026, 1, 1), naive, 5)
