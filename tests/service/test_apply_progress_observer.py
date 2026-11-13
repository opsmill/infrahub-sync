"""The worker's apply progress observer: when it saves, and when it asks the apply to stop (spec 022).

The observer is called directly, with an injected clock: the engine's own call wrapper
swallows observer errors, so it cannot show what the observer logs.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, cast

from infrahub_sync.service.apply_progress import (
    SAVE_EVERY_COMMITS,
    SAVE_EVERY_SECONDS,
    STOP_AFTER_FAILING_SECONDS,
    StoreApplyProgressObserver,
)

if TYPE_CHECKING:
    import pytest

    from infrahub_sync.product_store import ApplyProgress, ApplyProgressSave, ProductProjection

_LOGGER_NAME = "infrahub_sync.service.apply_progress"
_CANARY = "db.internal.example:5432 password=ZZ-PROGRESS-CANARY"
_TOTAL = 2_000
_SAVED_AT = datetime(2026, 11, 12, 9, 30, tzinfo=timezone.utc)


class _Clock:
    def __init__(self) -> None:
        self.value = 1_000.0

    def __call__(self) -> float:
        return self.value


class _Operation:
    def __init__(self, index: int) -> None:
        self.operation_id = f"op_{index:016x}"


class _Store:
    """Stands in for the projection; records every attempt, and can fail or answer that nothing was saved."""

    def __init__(self) -> None:
        self.attempts: list[ApplyProgress] = []
        self.fail = False
        self.answer: ApplyProgressSave = "saved"

    def record_apply_progress(self, run_id: str, flow_run_id: str, document: ApplyProgress) -> ApplyProgressSave:
        assert (run_id, flow_run_id) == ("run-001", "flow-apply-1")
        self.attempts.append(document)
        if self.fail:
            raise ConnectionError(_CANARY)
        return self.answer

    @property
    def positions(self) -> list[int]:
        return [attempt.position for attempt in self.attempts]


def _observer(store: _Store, clock: _Clock, *, start_after: int | None = None) -> StoreApplyProgressObserver:
    return StoreApplyProgressObserver(
        cast("ProductProjection", store),
        "run-001",
        "flow-apply-1",
        total=_TOTAL,
        start_after=start_after,
        clock=clock,
        now=lambda: _SAVED_AT,
    )


def _commit(observer: StoreApplyProgressObserver, first: int, count: int) -> None:
    for index in range(first, first + count):
        observer.committed(index, _Operation(index))  # ty: ignore[invalid-argument-type] - an operation stand-in


def test_saves_after_500_commits_and_not_before() -> None:
    store, clock = _Store(), _Clock()
    observer = _observer(store, clock)

    _commit(observer, 0, SAVE_EVERY_COMMITS - 1)
    assert store.attempts == []

    _commit(observer, SAVE_EVERY_COMMITS - 1, 1)
    assert store.positions == [SAVE_EVERY_COMMITS - 1]
    saved = store.attempts[0]
    assert saved.operation_id == _Operation(SAVE_EVERY_COMMITS - 1).operation_id
    assert (saved.total, saved.start_after, saved.saved_at) == (_TOTAL, None, _SAVED_AT)


def test_saves_at_the_first_commit_after_30_seconds() -> None:
    store, clock = _Store(), _Clock()
    observer = _observer(store, clock)
    _commit(observer, 0, 3)
    clock.value += SAVE_EVERY_SECONDS - 0.1
    _commit(observer, 3, 1)
    assert store.attempts == []

    clock.value += 0.1
    _commit(observer, 4, 1)

    assert store.positions == [4]


def test_a_change_of_kind_before_the_plan_end_saves_nothing() -> None:
    """Kinds of one tier interleave in the plan's order: a save at each change would follow nearly every commit."""
    store, clock = _Store(), _Clock()
    observer = _observer(store, clock)

    for index in range(SAVE_EVERY_COMMITS - 1):
        observer.committed(index, _Operation(index))  # ty: ignore[invalid-argument-type] - an operation stand-in
        observer.kind_finished(index)

    assert store.attempts == []


def test_saves_after_the_plan_last_operation_with_the_last_commit() -> None:
    store, clock = _Store(), _Clock()
    observer = _observer(store, clock, start_after=_TOTAL - 10)
    _commit(observer, _TOTAL - 9, 7)

    observer.kind_finished(_TOTAL - 3)
    # A delete at the plan's last position is never committed; the end saves the last commit.
    observer.kind_finished(_TOTAL - 1)

    assert store.positions == [_TOTAL - 3]


def test_a_stop_before_any_commit_saves_nothing() -> None:
    store, clock = _Store(), _Clock()
    observer = _observer(store, clock)

    observer.kind_finished(_TOTAL - 1)
    observer.stopped(1)

    assert store.attempts == []


def test_a_stop_saves_the_last_committed_position_never_the_stopped_one() -> None:
    store, clock = _Store(), _Clock()
    observer = _observer(store, clock, start_after=9)
    _commit(observer, 10, 4)

    observer.stopped(14)

    assert store.positions == [13]
    assert store.attempts[0].start_after == 9


def test_a_failing_store_keeps_being_tried_and_logs_one_warning_per_failure_without_the_message(
    caplog: pytest.LogCaptureFixture,
) -> None:
    store, clock = _Store(), _Clock()
    store.fail = True
    observer = _observer(store, clock)

    with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
        _commit(observer, 0, 1)
        observer.stopped(1)
        # Every attempt restarts the count, so a failing store is not tried after every commit.
        _commit(observer, 1, SAVE_EVERY_COMMITS - 1)
        _commit(observer, SAVE_EVERY_COMMITS, 1)
        # The stop tries again, because the last committed position is still not saved.
        observer.stopped(SAVE_EVERY_COMMITS + 1)

    assert store.positions == [0, SAVE_EVERY_COMMITS, SAVE_EVERY_COMMITS]
    warnings = [record for record in caplog.records if record.levelno >= logging.WARNING]
    assert len(warnings) == 3
    for record in warnings:
        message = record.getMessage()
        assert "run-001" in message
        assert "ConnectionError" in message
        assert "ZZ-PROGRESS-CANARY" not in message
        assert "db.internal.example" not in message
        assert record.exc_info is None


def test_a_save_to_an_ended_execution_stops_the_apply_before_its_next_dispatch(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Liveness ended the execution while the worker kept writing: the answer is final (review item 1)."""
    store, clock = _Store(), _Clock()
    store.answer = "ended"
    observer = _observer(store, clock)

    with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
        _commit(observer, 0, 1)
        observer.stopped(1)
        # No 5-minute wait: the next dispatch is refused at once.
        assert observer.should_stop() is True
        # The stop the engine reports next tries no further save.
        _commit(observer, 1, SAVE_EVERY_COMMITS)
        observer.stopped(SAVE_EVERY_COMMITS + 1)

    assert store.positions == [0]
    warnings = [record for record in caplog.records if record.levelno >= logging.WARNING]
    assert len(warnings) == 1
    message = warnings[0].getMessage()
    assert "run-001" in message
    assert "ended" in message
    assert warnings[0].exc_info is None


def test_a_save_to_an_unknown_execution_counts_as_a_failed_attempt(caplog: pytest.LogCaptureFixture) -> None:
    store, clock = _Store(), _Clock()
    store.answer = "unknown"
    observer = _observer(store, clock)

    with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
        _commit(observer, 0, 1)
        observer.stopped(1)
    assert observer.should_stop() is False
    clock.value += STOP_AFTER_FAILING_SECONDS

    assert observer.should_stop() is True
    assert len([record for record in caplog.records if record.levelno >= logging.WARNING]) == 1


def test_should_stop_turns_true_5_minutes_after_the_first_failed_attempt() -> None:
    store, clock = _Store(), _Clock()
    observer = _observer(store, clock)
    store.fail = True
    _commit(observer, 0, 1)
    observer.stopped(1)
    first_failure = clock.value

    clock.value = first_failure + STOP_AFTER_FAILING_SECONDS / 2
    _commit(observer, 1, 1)
    observer.stopped(2)
    clock.value = first_failure + STOP_AFTER_FAILING_SECONDS - 0.1
    assert observer.should_stop() is False

    clock.value = first_failure + STOP_AFTER_FAILING_SECONDS
    assert observer.should_stop() is True


def test_a_successful_save_clears_the_failure_and_the_stop() -> None:
    store, clock = _Store(), _Clock()
    observer = _observer(store, clock)
    store.fail = True
    _commit(observer, 0, 1)
    observer.stopped(1)

    clock.value += STOP_AFTER_FAILING_SECONDS - 1
    store.fail = False
    _commit(observer, 1, 1)
    observer.stopped(2)
    clock.value += STOP_AFTER_FAILING_SECONDS

    assert store.positions == [0, 1]
    assert observer.should_stop() is False


def test_should_stop_stays_false_during_a_quiet_period_between_successful_saves() -> None:
    store, clock = _Store(), _Clock()
    observer = _observer(store, clock)
    _commit(observer, 0, 1)
    observer.stopped(1)

    # One slow operation: no commit and no save for an hour.
    clock.value += 3_600

    assert observer.should_stop() is False
    assert store.positions == [0]
