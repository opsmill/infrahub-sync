"""Save an apply's progress marker on its execution while the worker writes the saved plan.

The worker gives Potenda a `StoreApplyProgressObserver`, which saves the position of the last
committed operation to the execution's row (spec 022, research R2 and R3). It saves after 500
commits, after 30 seconds, at each kind boundary, when the apply stops and at its end. A saved
position never runs ahead of a commit, because the engine reports an operation only after its
destination call returned and the apply recorded it.

When saves keep failing, `should_stop()` asks the engine to stop before its next dispatch once
the first failed attempt since the last successful save is 5 minutes old (research R4,
critique E4), so a quiet period between scheduled saves is never counted as a failure.

A log line never holds an exception message: a failed save is logged by error class only
(docs/docs/development/guidelines/secret-redaction.md).
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from infrahub_sync.product_store.models import ApplyProgress

if TYPE_CHECKING:
    from collections.abc import Callable

    from infrahub_sync.plan.models import PlannedOperation
    from infrahub_sync.product_store import ProductProjection

logger = logging.getLogger(__name__)

# Save after this many commits since the last save attempt (research R3).
SAVE_EVERY_COMMITS = 500
# Save at the next commit once this many seconds passed since the last save attempt.
SAVE_EVERY_SECONDS = 30.0
# Stop the apply once the first failed save since the last success is this old (research R4).
STOP_AFTER_FAILING_SECONDS = 300.0


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class StoreApplyProgressObserver:
    """Save the last committed position of one write execution, as Potenda reports commits.

    `total` is the number of operations in the saved plan, and `start_after` the position a
    resume started after, or `None` for the first apply. `clock` measures the save interval
    and the stop delay and must not go backwards; `now` gives the time saved with the marker.
    Both are injectable for tests.

    The engine calls the observer on the thread that dispatches the operations, one call at a
    time, so the observer holds no lock.
    """

    def __init__(
        self,
        projection: ProductProjection,
        run_id: str,
        flow_run_id: str,
        *,
        total: int,
        start_after: int | None = None,
        clock: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._projection = projection
        self._run_id = run_id
        self._flow_run_id = flow_run_id
        self._total = total
        self._start_after = start_after
        self._clock = clock
        self._now = now
        # The last committed operation, as (position, operation_id); never a delete.
        self._last_committed: tuple[int, str] | None = None
        self._saved_position: int | None = None
        self._commits_since_attempt = 0
        self._last_attempt_at = clock()
        self._first_failure_at: float | None = None

    def committed(self, index: int, operation: PlannedOperation) -> None:
        """Remember the committed operation, and save when 500 commits or 30 seconds passed."""
        self._last_committed = (index, operation.operation_id)
        self._commits_since_attempt += 1
        if (
            self._commits_since_attempt >= SAVE_EVERY_COMMITS
            or self._clock() - self._last_attempt_at >= SAVE_EVERY_SECONDS
        ):
            self._save()

    def kind_finished(self, index: int) -> None:  # noqa: ARG002  # pylint: disable=unused-argument  # saves the last commit
        """Save at a kind boundary and after the plan's last operation."""
        self._save()

    def stopped(self, index: int) -> None:  # noqa: ARG002  # pylint: disable=unused-argument  # saves the last commit
        """Save when the apply stops, so the marker names the last operation known to be committed."""
        self._save()

    def should_stop(self) -> bool:
        """Whether the first failed save since the last successful one is 5 minutes old."""
        return (
            self._first_failure_at is not None and self._clock() - self._first_failure_at >= STOP_AFTER_FAILING_SECONDS
        )

    def _save(self) -> None:
        """Save the last committed position unless nothing was committed since the last save.

        Every attempt restarts the commit count and the timer, so a store that keeps failing
        is tried again at the next scheduled save rather than after every commit.

        The catch is broad on purpose. The store raises the errors of two unrelated database
        drivers, `sqlite3` and `psycopg`, which share no base class, and a failed save must
        never end the apply it reports on. The warning names the error class, so a
        programming error still shows in the worker log.
        """
        if self._last_committed is None or self._last_committed[0] == self._saved_position:
            return
        position, operation_id = self._last_committed
        self._commits_since_attempt = 0
        self._last_attempt_at = self._clock()
        try:
            marker = ApplyProgress(
                position=position,
                operation_id=operation_id,
                total=self._total,
                start_after=self._start_after,
                saved_at=self._now(),
            )
            stored = self._projection.record_apply_progress(self._run_id, self._flow_run_id, marker)
        except Exception as exc:  # noqa: BLE001  # pylint: disable=broad-exception-caught
            logger.warning("Apply progress of run %s was not saved (%s)", self._run_id, type(exc).__name__)
            self._save_failed()
            return
        if not stored:
            # The position is not saved, so the attempt counts as failed: an execution that
            # ended or is unknown has nothing a resume could start after.
            logger.warning(
                "Apply progress of run %s was not saved: execution %s is ended or unknown",
                self._run_id,
                self._flow_run_id,
            )
            self._save_failed()
            return
        self._saved_position = position
        self._first_failure_at = None

    def _save_failed(self) -> None:
        if self._first_failure_at is None:
            self._first_failure_at = self._last_attempt_at
