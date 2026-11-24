"""The health settings accept only whole numbers in their range and refuse others by name, never by value."""

from __future__ import annotations

import pytest

from infrahub_sync.service.settings import (
    HEALTH_CHECK_TIMEOUT_ENV,
    HEALTH_MIN_WORKERS_ENV,
    HealthSettingsError,
    health_check_timeout_seconds,
    health_min_workers,
    validate_settings,
)


def test_the_check_timeout_defaults_to_three_seconds() -> None:
    assert health_check_timeout_seconds({}) == 3


@pytest.mark.parametrize(("value", "expected"), [("1", 1), ("60", 60)])
def test_the_check_timeout_accepts_whole_seconds_from_1_to_60(value: str, expected: int) -> None:
    assert health_check_timeout_seconds({HEALTH_CHECK_TIMEOUT_ENV: value}) == expected


@pytest.mark.parametrize("value", ["0", "61", "1.5", "abc", ""])
def test_the_check_timeout_refuses_a_value_outside_its_range(value: str) -> None:
    with pytest.raises(HealthSettingsError) as caught:
        health_check_timeout_seconds({HEALTH_CHECK_TIMEOUT_ENV: value})

    message = str(caught.value)
    assert HEALTH_CHECK_TIMEOUT_ENV in message
    if value:
        assert value not in message.replace(HEALTH_CHECK_TIMEOUT_ENV, "").replace("1 to 60", "")


def test_the_minimum_of_workers_defaults_to_one() -> None:
    assert health_min_workers({}) == 1


@pytest.mark.parametrize(("value", "expected"), [("1", 1), ("5", 5), ("999999999", 999_999_999)])
def test_the_minimum_of_workers_accepts_a_whole_number_from_one_to_the_maximum(value: str, expected: int) -> None:
    assert health_min_workers({HEALTH_MIN_WORKERS_ENV: value}) == expected


@pytest.mark.parametrize("value", ["0", "-1", "x", "1000000000", "0001000000000"])
def test_the_minimum_of_workers_refuses_a_value_out_of_range_or_not_a_number(value: str) -> None:
    with pytest.raises(HealthSettingsError) as caught:
        health_min_workers({HEALTH_MIN_WORKERS_ENV: value})

    message = str(caught.value)
    assert HEALTH_MIN_WORKERS_ENV in message
    assert "from 1 to 999999999" in message
    assert value not in message.replace(HEALTH_MIN_WORKERS_ENV, "").replace("from 1 to 999999999", "")


def test_a_health_setting_error_is_a_value_error() -> None:
    assert issubclass(HealthSettingsError, ValueError)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        (HEALTH_CHECK_TIMEOUT_ENV, "0"),
        (HEALTH_CHECK_TIMEOUT_ENV, "61"),
        (HEALTH_CHECK_TIMEOUT_ENV, "1.5"),
        (HEALTH_CHECK_TIMEOUT_ENV, "abc"),
        (HEALTH_CHECK_TIMEOUT_ENV, ""),
        (HEALTH_MIN_WORKERS_ENV, "0"),
        (HEALTH_MIN_WORKERS_ENV, "-1"),
        (HEALTH_MIN_WORKERS_ENV, "x"),
    ],
)
def test_validate_settings_refuses_each_refused_health_value(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    monkeypatch.setenv(name, value)

    with pytest.raises(HealthSettingsError, match=name):
        validate_settings()


def test_validate_settings_accepts_the_health_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(HEALTH_CHECK_TIMEOUT_ENV, raising=False)
    monkeypatch.delenv(HEALTH_MIN_WORKERS_ENV, raising=False)

    validate_settings()
