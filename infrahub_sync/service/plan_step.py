"""Store the parts of the plan step of a stage execution, and derive the timings the run shows.

The worker gives Potenda a `StorePlanStepObserver`, which writes one JSON document to the
execution's `plan_step` column (spec 019, research R11). The document holds `comparing`,
`checking` and `saving`, each with its start and end, and the part that runs. Waiting for a
worker and preparing are not stored: `stage_plan_step` derives them from the execution's
`submitted_at` and `claimed_at` and from the read documents. The reads themselves keep their
own columns and are not repeated (FR-015).

When the plan step fails, is cancelled or its worker stops, the part that was running stays the
running part in the stored document. `stage_plan_step` then returns it with the time of the
run's verdict as `stopped_at`, never with an `ended_at` (FR-016, research R13).

A stored or returned document holds part names and times only: no error message and no data
value (FR-021, docs/docs/development/guidelines/secret-redaction.md).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, cast

from infrahub_sync.potenda.plan_step import PLAN_STEP_PARTS

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from infrahub_sync.potenda.plan_step import PlanStepPartName
    from infrahub_sync.product_store import PrefectExecutionLink, ProductProjection

logger = logging.getLogger(__name__)

# The part names the run response returns, in the order the plan step runs them. The two
# reads run between preparing and comparing and are returned with the reads (FR-015).
RESPONSE_PARTS: tuple[str, ...] = ("waiting", "preparing", *PLAN_STEP_PARTS)
# The parts the document may store. `preparing` is derived today; a later release may store it.
_STORED_PARTS: tuple[str, ...] = ("preparing", *PLAN_STEP_PARTS)
# The Prefect states, as the Sync API observes them, of a flow run that ended without completing.
_ENDED_PREFECT_STATES = frozenset({"failed", "crashed", "cancelled"})


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class StorePlanStepObserver:
    """Write the parts of one execution's plan step to its row as Potenda reports them.

    Potenda reports `comparing`, `checking` and `saving`. The worker adds three calls:

    - `begin` writes an empty document when the stage starts, so the run response can show
      waiting and preparing while the sides are read;
    - `close_saving` ends `saving` after the plan is published, because the run shows its plan
      only then (research R10). Potenda's own end of `saving` is not stored;
    - `stopped` writes what is not yet stored before the worker commits a failure, because the
      store refuses a write once the execution has a verdict.

    The end of a part is stored with the start of the next part in one write. A write that fails
    is logged by error class and never ends the plan step.
    """

    def __init__(
        self,
        projection: ProductProjection,
        run_id: str,
        flow_run_id: str,
        *,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._projection = projection
        self._run_id = run_id
        self._flow_run_id = flow_run_id
        self._clock = clock
        self._parts: dict[str, dict[str, str]] = {}
        self._running: str | None = None
        self._unsaved = False

    def begin(self) -> None:
        """Write a document with no part, before the stage reads either side."""
        self._write()

    def part_started(self, part: PlanStepPartName) -> None:
        """Record `part` as running, with its start; a second start of the same part is ignored."""
        if part in self._parts:
            return
        self._parts[part] = {"started_at": self._clock().isoformat()}
        self._running = part
        self._write()

    def part_ended(self, part: PlanStepPartName) -> None:
        """Record the end of `part`; it is stored with the next write.

        An end with no start is ignored, so no part gets a duration it did not measure. The end
        of `saving` waits for `close_saving`.
        """
        entry = self._parts.get(part)
        if part == "saving" or entry is None or "ended_at" in entry:
            return
        entry["ended_at"] = self._clock().isoformat()
        if self._running == part:
            self._running = None
        self._unsaved = True

    def close_saving(self) -> None:
        """End `saving` once the plan is published, and store everything not yet stored."""
        entry = self._parts.get("saving")
        if entry is not None and "ended_at" not in entry:
            entry["ended_at"] = self._clock().isoformat()
            if self._running == "saving":
                self._running = None
            self._unsaved = True
        if self._unsaved:
            self._write()

    def stopped(self) -> None:
        """Store what is not yet stored before the failure commit; the running part stays running."""
        if self._unsaved:
            self._write()

    def document(self) -> dict[str, Any]:
        """The document as the next write stores it."""
        document: dict[str, Any] = {"parts": {name: dict(times) for name, times in self._parts.items()}}
        if self._running is not None:
            document["running"] = self._running
        return document

    def _write(self) -> None:
        """Store the document; a failure is logged and never ends the plan step.

        The catch is broad on purpose. The store raises the errors of two unrelated database
        drivers, `sqlite3` and `psycopg`, which share no base class, and a timing must never end
        the plan it measures. The warning names the error class only.
        """
        self._unsaved = False
        try:
            stored = self._projection.record_plan_step(self._run_id, self._flow_run_id, self.document())
        except Exception as exc:  # noqa: BLE001  # pylint: disable=broad-exception-caught
            self._unsaved = True
            logger.warning("Plan step timings of run %s were not stored (%s)", self._run_id, type(exc).__name__)
            return
        if not stored:
            logger.warning(
                "Plan step timings of run %s were not stored: execution %s is ended or unknown",
                self._run_id,
                self._flow_run_id,
            )


@dataclass(frozen=True)
class PlanStepPartTimes:
    """The times of one part of the plan step as the run response returns them.

    `ended_at` is set for a part that ended. `stopped_at` is set for the part that was running
    when the plan step failed, was cancelled or was interrupted: the time of the run's verdict.
    """

    started_at: datetime
    ended_at: datetime | None = None
    stopped_at: datetime | None = None


@dataclass(frozen=True)
class PlanStepState:
    """The timings of one execution's plan step: the part that runs, and each part that started.

    `parts` follows the order of `RESPONSE_PARTS`. A part that did not start has no entry.
    """

    running: str | None = None
    parts: Mapping[str, PlanStepPartTimes] = field(default_factory=dict)


def _aware(value: object) -> datetime | None:
    """Parse an ISO 8601 time with a time zone; anything else is `None`."""
    if isinstance(value, datetime):
        return value if value.utcoffset() is not None else None
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.utcoffset() is not None else None


def _stored_part(name: str, value: object, *, run_id: str) -> PlanStepPartTimes | None:
    """One stored part, or `None` with a warning when its times do not parse or are out of order."""
    if not isinstance(value, dict):
        logger.warning("Plan step part %s of run %s does not parse and is not returned", name, run_id)
        return None
    entry = cast("dict[str, object]", value)
    started_at = _aware(entry.get("started_at"))
    ended_at = _aware(entry.get("ended_at")) if "ended_at" in entry else None
    if started_at is None or ("ended_at" in entry and (ended_at is None or ended_at < started_at)):
        logger.warning("Plan step part %s of run %s does not parse and is not returned", name, run_id)
        return None
    return PlanStepPartTimes(started_at=started_at, ended_at=ended_at)


def _first_read_start(link: PrefectExecutionLink) -> datetime | None:
    """The earlier start of the two reads, or `None` when neither read started."""
    starts = [
        started_at
        for document in (link.source_read, link.destination_read)
        if document is not None and (started_at := _aware(document.get("started_at"))) is not None
    ]
    return min(starts, default=None)


def _verdict(link: PrefectExecutionLink, prefect_state: str | None) -> str | None:
    """`completed`, `stopped` (a verdict other than completed), `ended` (no verdict yet), or `None`."""
    if link.terminal_state == "completed":
        return "completed"
    if link.terminal_state is not None:
        return "stopped"
    if prefect_state is not None and prefect_state.lower() in _ENDED_PREFECT_STATES:
        return "ended"
    return None


def _unfinished(
    name: str, times: PlanStepPartTimes, *, verdict: str | None, terminal_at: datetime | None, run_id: str
) -> PlanStepPartTimes | None:
    """How a part that never ended is returned, given the execution's verdict (research R13)."""
    if verdict == "completed":
        # A completed plan step ended every part, so the write that ended this one was lost.
        logger.warning(
            "Plan step part %s of run %s still runs after the execution completed; not returned", name, run_id
        )
        return None
    if verdict == "stopped" and terminal_at is not None and terminal_at >= times.started_at:
        return PlanStepPartTimes(started_at=times.started_at, stopped_at=terminal_at)
    return times


def _derived_parts(link: PrefectExecutionLink, *, derive_preparing: bool) -> dict[str, PlanStepPartTimes]:
    """`waiting` and `preparing`, derived from the execution's times and the read documents (research R11).

    Waiting needs `claimed_at` not before `submitted_at`. Preparing runs from `claimed_at` to
    the earlier read start, and has no end while neither read started.
    """
    parts: dict[str, PlanStepPartTimes] = {}
    claimed_at = _aware(link.claimed_at)
    if claimed_at is None:
        return parts
    submitted_at = _aware(link.submitted_at)
    if submitted_at is not None and claimed_at >= submitted_at:
        parts["waiting"] = PlanStepPartTimes(started_at=submitted_at, ended_at=claimed_at)
    if derive_preparing:
        first_read = _first_read_start(link)
        if first_read is None:
            parts["preparing"] = PlanStepPartTimes(started_at=claimed_at)
        elif first_read >= claimed_at:
            parts["preparing"] = PlanStepPartTimes(started_at=claimed_at, ended_at=first_read)
    return parts


def stage_plan_step(
    link: PrefectExecutionLink, *, run_id: str, prefect_state: str | None = None
) -> PlanStepState | None:
    """Derive the plan step timings the run response returns for one execution (research R11, R13).

    `None` when the execution stored no document: one recorded before this feature, a `verify`
    or `apply` execution, or a `plan` or `sync` execution the worker has not started.

    - `waiting` runs from `submitted_at` to `claimed_at`, and is returned only when `claimed_at`
      is not before `submitted_at`.
    - `preparing` runs from `claimed_at` to the earlier read start, unless the document stores it.
    - A part that has not ended is the running part while the execution has no verdict. Once the
      execution failed, was cancelled or was interrupted, it is returned with `stopped_at` set to
      the verdict time and `running` is `None`. When `prefect_state` says the flow run ended and
      the store holds no verdict yet, it is returned with its start only. A completed execution
      returns no entry for it.
    """
    document = link.plan_step
    if document is None:
        return None
    verdict = _verdict(link, prefect_state)
    terminal_at = _aware(link.terminal_at)
    stored = document.get("parts")
    stored_parts: dict[str, Any] = stored if isinstance(stored, dict) else {}
    parts = _derived_parts(link, derive_preparing="preparing" not in stored_parts)
    for name in _STORED_PARTS:
        if name in stored_parts:
            times = _stored_part(name, stored_parts[name], run_id=run_id)
            if times is not None:
                parts[name] = times
    running: str | None = None
    for name in RESPONSE_PARTS:
        times = parts.get(name)
        if times is None or times.ended_at is not None:
            continue
        if verdict is None:
            running = name
            continue
        unfinished = _unfinished(name, times, verdict=verdict, terminal_at=terminal_at, run_id=run_id)
        if unfinished is None:
            del parts[name]
        else:
            parts[name] = unfinished
    return PlanStepState(running=running, parts={name: parts[name] for name in RESPONSE_PARTS if name in parts})
