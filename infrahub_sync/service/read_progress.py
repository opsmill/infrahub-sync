"""Store each side's read state of a stage execution, and derive what the run response shows.

The worker gives Potenda a `StoreSideReadObserver`, which writes one JSON document per side
to the execution's row (spec 018, research R10). The Sync API derives `StageReads` from the
stored documents and the execution's terminal state, without writing (research R6), so a
worker that was killed leaves a state the response can still explain.

A stored or returned document never holds an exception message: a failed read records the
error's class name only (docs/docs/development/guidelines/secret-redaction.md).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from infrahub_sync.client.models import SideRead, StageReads

if TYPE_CHECKING:
    from collections.abc import Mapping

    from infrahub_sync.potenda.read_progress import Side, SideReadReport
    from infrahub_sync.product_store import PrefectExecutionLink, ProductProjection

logger = logging.getLogger(__name__)

_SIDES: tuple[Side, ...] = ("source", "destination")
# The fields a stored document may carry (spec 018 data-model.md); anything else is dropped.
_DOCUMENT_FIELDS = ("state", "started_at", "ended_at", "read_mode", "nodes_by_kind", "error_class")
# How a side that never ended is returned once its execution ended without completing.
_UNFINISHED_STATES = {"waiting": "not_started", "reading": "interrupted"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _store(
    projection: ProductProjection, run_id: str, flow_run_id: str, side: Side, document: Mapping[str, Any]
) -> None:
    """Write one side's document; a store failure is logged and never ends the read (FR-007)."""
    try:
        projection.record_side_read(run_id, flow_run_id, side, document)
    except Exception as exc:  # noqa: BLE001  # pylint: disable=broad-exception-caught
        logger.warning("Read state of the %s side of run %s was not stored (%s)", side, run_id, type(exc).__name__)


def record_waiting(projection: ProductProjection, run_id: str, flow_run_id: str) -> None:
    """Record both sides as `waiting`, before the stage reads either (research R4)."""
    for side in _SIDES:
        _store(projection, run_id, flow_run_id, side, {"state": "waiting"})


class StoreSideReadObserver:
    """Write each side's read state to one execution's row as Potenda reports it.

    The two sides can be read on two threads at once. Each write opens its own store
    connection and each side has its own column, so the two threads share nothing but
    `_started`, where each thread writes only its own side's key.
    """

    def __init__(self, projection: ProductProjection, run_id: str, flow_run_id: str) -> None:
        self._projection = projection
        self._run_id = run_id
        self._flow_run_id = flow_run_id
        self._started: dict[Side, str] = {}

    def read_started(self, side: Side) -> None:
        """Record the side as `reading`, with its start time."""
        started_at = _now()
        self._started[side] = started_at
        self._write(side, {"state": "reading", "started_at": started_at})

    def read_ended(self, side: Side, report: SideReadReport) -> None:
        """Record the side as `succeeded` or `failed`, repeating its start time."""
        document: dict[str, Any] = {"state": "succeeded" if report.succeeded else "failed"}
        started_at = self._started.get(side)
        if started_at is not None:
            document["started_at"] = started_at
        document["ended_at"] = _now()
        if report.succeeded:
            document["read_mode"] = report.read_mode
            document["nodes_by_kind"] = dict(sorted(report.nodes_by_kind.items()))
        else:
            document["error_class"] = report.error_class
        self._write(side, document)

    def _write(self, side: Side, document: Mapping[str, Any]) -> None:
        _store(self._projection, self._run_id, self._flow_run_id, side, document)


def _side_read(
    document: dict[str, Any] | None, *, side: Side, run_id: str, terminal_state: str | None
) -> SideRead | None:
    if document is None:
        return None
    try:
        read = SideRead.model_validate({key: document[key] for key in _DOCUMENT_FIELDS if key in document})
    except ValidationError:
        logger.warning("Read state of the %s side of run %s does not parse and is not returned", side, run_id)
        return None
    if read.state not in _UNFINISHED_STATES or terminal_state is None:
        return read
    if terminal_state == "completed":
        # A completed plan or sync read both sides, so the write that ended this read was lost.
        logger.warning(
            "Read state of the %s side of run %s is still %s after the execution completed; not returned",
            side,
            run_id,
            read.state,
        )
        return None
    return read.model_copy(update={"state": _UNFINISHED_STATES[read.state], "ended_at": None})


def stage_reads(link: PrefectExecutionLink, *, run_id: str) -> StageReads | None:
    """Derive the read state the run response returns for one execution (research R6).

    `None` when the execution stored no document: one recorded before the read state, a
    `verify` or `apply` execution, or a `plan` or `sync` execution the worker has not brought
    to its planning step (not started yet, or failed before `record_waiting` ran).
    """
    if link.source_read is None and link.destination_read is None:
        return None
    return StageReads(
        source=_side_read(link.source_read, side="source", run_id=run_id, terminal_state=link.terminal_state),
        destination=_side_read(
            link.destination_read, side="destination", run_id=run_id, terminal_state=link.terminal_state
        ),
    )
