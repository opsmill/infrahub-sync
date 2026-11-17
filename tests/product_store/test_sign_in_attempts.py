"""The failed sign-ins that the product store counts for one name (research R6).

Each case runs on the three profiles of ``test_accounts.py``. The case with two threads runs on
the SQLite store and on a real PostgreSQL server only: the PostgreSQL store class over SQLite
discards the lock that serializes two recordings, so that profile proves nothing about them.
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, cast

import pytest

from infrahub_sync.product_store import ProductProjection, SignInAttempt
from infrahub_sync.product_store.accounts import (
    SIGN_IN_ATTEMPTS_BOUND,
    SIGN_IN_FAILURE_LIMIT,
    SIGN_IN_FAILURE_WINDOW,
    SIGN_IN_NAME_MAX_LENGTH,
    SIGN_IN_REFUSAL_PERIOD,
)
from tests.product_store.test_contract import _PROFILES_WITH_POSTGRESQL, _projection_profile

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

_T0 = datetime(2026, 11, 17, 9, 0, tzinfo=timezone.utc)
_SECOND = timedelta(seconds=1)


@pytest.fixture(params=_PROFILES_WITH_POSTGRESQL)
def projection(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[ProductProjection]:
    yield from _projection_profile(request.param, tmp_path, "the failed sign-in store's PostgreSQL parameter")


@pytest.fixture(params=("local", pytest.param("postgresql", marks=pytest.mark.integration)))
def serialized_projection(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[ProductProjection]:
    yield from _projection_profile(request.param, tmp_path, "the failed sign-in store's PostgreSQL parameter")


def _rows(projection: ProductProjection) -> list[tuple[Any, ...]]:
    """Return every row of the table, by name."""
    records = cast("Any", projection._records)
    with records._reading() as cursor:
        cursor.execute(
            "SELECT name_key, failed_count, window_started_at, refused_until FROM sign_in_attempts ORDER BY name_key"
        )
        return [tuple(row) for row in cursor.fetchall()]


def _fail(projection: ProductProjection, name: str, times: int, *, start: datetime = _T0) -> SignInAttempt:
    """Record failures one second apart, and return what the store holds after the last one."""
    attempt = None
    for index in range(times):
        attempt = projection.record_sign_in_failure(name, now=start + index * _SECOND)
    assert attempt is not None
    return attempt


def test_the_rule_is_five_failures_in_fifteen_minutes_then_fifteen_minutes_and_ten_thousand_rows() -> None:
    assert SIGN_IN_FAILURE_LIMIT == 5
    assert timedelta(minutes=15) == SIGN_IN_FAILURE_WINDOW
    assert timedelta(minutes=15) == SIGN_IN_REFUSAL_PERIOD
    assert SIGN_IN_ATTEMPTS_BOUND == 10_000
    assert SIGN_IN_NAME_MAX_LENGTH == 64


def test_a_name_with_no_failure_reads_nothing(projection: ProductProjection) -> None:
    assert projection.read_sign_in_attempt("alice") is None


def test_the_first_failure_starts_a_window_with_a_count_of_one(projection: ProductProjection) -> None:
    recorded = projection.record_sign_in_failure("alice", now=_T0)

    assert recorded == SignInAttempt(name_key="alice", failed_count=1, window_started_at=_T0, refused_until=None)
    assert projection.read_sign_in_attempt("alice") == recorded


def test_failures_are_counted_for_the_lower_case_name(projection: ProductProjection) -> None:
    projection.record_sign_in_failure("Alice", now=_T0)
    recorded = projection.record_sign_in_failure("ALICE", now=_T0 + _SECOND)

    assert recorded.name_key == "alice"
    assert recorded.failed_count == 2
    assert projection.read_sign_in_attempt("aLiCe") == recorded
    assert len(_rows(projection)) == 1


def test_a_failure_in_the_window_adds_one_and_keeps_the_start_of_the_window(projection: ProductProjection) -> None:
    recorded = _fail(projection, "alice", SIGN_IN_FAILURE_LIMIT - 1)

    assert recorded.failed_count == SIGN_IN_FAILURE_LIMIT - 1
    assert recorded.window_started_at == _T0
    assert recorded.refused_until is None


def test_the_fifth_failure_refuses_the_name_for_fifteen_minutes(projection: ProductProjection) -> None:
    recorded = _fail(projection, "alice", SIGN_IN_FAILURE_LIMIT)

    fifth = _T0 + (SIGN_IN_FAILURE_LIMIT - 1) * _SECOND
    assert recorded.failed_count == SIGN_IN_FAILURE_LIMIT
    assert recorded.refused_until == fifth + SIGN_IN_REFUSAL_PERIOD
    assert projection.read_sign_in_attempt("alice") == recorded


def test_a_failure_during_the_refusal_changes_nothing(projection: ProductProjection) -> None:
    refused = _fail(projection, "alice", SIGN_IN_FAILURE_LIMIT)
    stored = _rows(projection)
    assert refused.refused_until is not None

    during = projection.record_sign_in_failure("alice", now=refused.refused_until - _SECOND)

    assert during == refused
    assert _rows(projection) == stored


def test_the_first_failure_after_the_refusal_starts_the_count_again(projection: ProductProjection) -> None:
    refused = _fail(projection, "alice", SIGN_IN_FAILURE_LIMIT)
    assert refused.refused_until is not None

    after = projection.record_sign_in_failure("alice", now=refused.refused_until)

    assert after == SignInAttempt(
        name_key="alice", failed_count=1, window_started_at=refused.refused_until, refused_until=None
    )


def test_a_failure_after_the_window_starts_a_new_window(projection: ProductProjection) -> None:
    _fail(projection, "alice", SIGN_IN_FAILURE_LIMIT - 1)
    later = _T0 + SIGN_IN_FAILURE_WINDOW + _SECOND

    recorded = projection.record_sign_in_failure("alice", now=later)

    assert recorded == SignInAttempt(name_key="alice", failed_count=1, window_started_at=later, refused_until=None)


def test_a_failure_at_the_end_of_the_window_is_still_counted_in_it(projection: ProductProjection) -> None:
    projection.record_sign_in_failure("alice", now=_T0)

    recorded = projection.record_sign_in_failure("alice", now=_T0 + SIGN_IN_FAILURE_WINDOW)

    assert recorded.failed_count == 2
    assert recorded.window_started_at == _T0


def test_clearing_a_name_deletes_its_row_only(projection: ProductProjection) -> None:
    _fail(projection, "alice", SIGN_IN_FAILURE_LIMIT)
    projection.record_sign_in_failure("bob", now=_T0)

    assert projection.clear_sign_in_attempts("Alice") is True

    assert projection.read_sign_in_attempt("alice") is None
    assert projection.read_sign_in_attempt("bob") is not None
    assert projection.clear_sign_in_attempts("alice") is False


def test_a_cleared_name_starts_again_from_one(projection: ProductProjection) -> None:
    _fail(projection, "alice", SIGN_IN_FAILURE_LIMIT)
    projection.clear_sign_in_attempts("alice")

    assert projection.record_sign_in_failure("alice", now=_T0 + timedelta(minutes=1)).failed_count == 1


@pytest.mark.parametrize("name", ["", "a" * (SIGN_IN_NAME_MAX_LENGTH + 1), "al\x00ice"], ids=["empty", "65", "nul"])
def test_a_name_that_is_empty_too_long_or_holds_a_nul_character_writes_nothing(
    projection: ProductProjection, name: str
) -> None:
    with pytest.raises(ValueError, match="1 to 64 characters") as refused:
        projection.record_sign_in_failure(name, now=_T0)

    assert name not in str(refused.value) or not name
    assert _rows(projection) == []


def test_a_name_of_64_characters_is_counted(projection: ProductProjection) -> None:
    name = "a" * SIGN_IN_NAME_MAX_LENGTH

    assert projection.record_sign_in_failure(name, now=_T0).failed_count == 1


def test_a_bound_below_one_row_is_refused(projection: ProductProjection) -> None:
    with pytest.raises(ValueError, match="at least one row"):
        projection.record_sign_in_failure("alice", now=_T0, bound=0)

    assert _rows(projection) == []


def test_a_new_name_at_the_bound_replaces_the_row_with_the_oldest_window(projection: ProductProjection) -> None:
    for index, name in enumerate(("oldest", "middle", "newest")):
        projection.record_sign_in_failure(name, now=_T0 + index * _SECOND, bound=3)

    projection.record_sign_in_failure("added", now=_T0 + timedelta(minutes=1), bound=3)

    assert [row[0] for row in _rows(projection)] == ["added", "middle", "newest"]


def test_a_failure_for_a_name_that_has_a_row_deletes_no_row_at_the_bound(projection: ProductProjection) -> None:
    for index, name in enumerate(("oldest", "middle", "newest")):
        projection.record_sign_in_failure(name, now=_T0 + index * _SECOND, bound=3)

    recorded = projection.record_sign_in_failure("oldest", now=_T0 + timedelta(minutes=1), bound=3)

    assert recorded.failed_count == 2
    assert [row[0] for row in _rows(projection)] == ["middle", "newest", "oldest"]


def test_the_table_never_holds_more_rows_than_its_bound(projection: ProductProjection) -> None:
    bound = 4
    for index in range(bound * 3):
        projection.record_sign_in_failure(f"name-{index:02d}", now=_T0 + index * _SECOND, bound=bound)
        assert len(_rows(projection)) <= bound

    assert [row[0] for row in _rows(projection)] == ["name-08", "name-09", "name-10", "name-11"]


def test_a_table_over_a_lowered_bound_is_brought_back_under_it(projection: ProductProjection) -> None:
    for index in range(6):
        projection.record_sign_in_failure(f"name-{index}", now=_T0 + index * _SECOND)

    projection.record_sign_in_failure("added", now=_T0 + timedelta(minutes=1), bound=2)

    assert [row[0] for row in _rows(projection)] == ["added", "name-5"]


def test_two_failures_at_the_same_moment_for_one_name_leave_one_row_with_a_count_of_two(
    serialized_projection: ProductProjection,
) -> None:
    start = threading.Barrier(2)
    errors: list[BaseException] = []

    def fail() -> None:
        try:
            start.wait(timeout=10)
            serialized_projection.record_sign_in_failure("alice", now=_T0)
        except BaseException as error:  # noqa: BLE001 - reported by the assertion below.
            errors.append(error)

    threads = [threading.Thread(target=fail) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert [type(error).__name__ for error in errors] == []
    rows = _rows(serialized_projection)
    assert [(row[0], row[1]) for row in rows] == [("alice", 2)]


def test_many_failures_at_the_same_moment_for_one_name_refuse_it_once(
    serialized_projection: ProductProjection,
) -> None:
    callers = SIGN_IN_FAILURE_LIMIT + 3
    start = threading.Barrier(callers)
    errors: list[BaseException] = []

    def fail() -> None:
        try:
            start.wait(timeout=10)
            serialized_projection.record_sign_in_failure("alice", now=_T0)
        except BaseException as error:  # noqa: BLE001 - reported by the assertion below.
            errors.append(error)

    threads = [threading.Thread(target=fail) for _ in range(callers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert [type(error).__name__ for error in errors] == []
    stored = serialized_projection.read_sign_in_attempt("alice")
    assert stored is not None
    # The failures after the fifth found the name refused, and changed nothing.
    assert stored.failed_count == SIGN_IN_FAILURE_LIMIT
    assert stored.refused_until == _T0 + SIGN_IN_REFUSAL_PERIOD
