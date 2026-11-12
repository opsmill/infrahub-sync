"""What Potenda reports about an apply's progress while it writes a saved plan.

`Potenda.apply_plan` calls an optional `ApplyProgressObserver` as it works through the plan's
operations in stored order. The engine imports nothing from the service: the Sync service's
worker supplies the one observer that saves the progress marker (spec 022, research R3). The
CLI-local and direct Prefect paths pass no observer.

Every `index` is the operation's position in the saved plan's stored order, counting from 0
and counting recorded deletes, so a marker saved by one execution names the same operation
for the execution that resumes after it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from infrahub_sync.plan.models import ApplyRecord, PlannedOperation


class ApplyProgressObserver(Protocol):
    """Receives each committed operation, each finished kind, and the stop of an apply.

    The engine calls these methods on the thread that dispatches the operations, one call at a
    time. An implementation catches its own errors: the engine does not guard these calls, and
    an exception raised here ends the apply like any other failure in the loop.
    """

    def committed(self, index: int, operation: PlannedOperation) -> None:
        """The destination call for the operation at `index` returned, and the apply recorded it.

        Never called for an operation whose destination call raised, nor for a recorded delete,
        which is collected and never dispatched (spec 022 FR-002).
        """

    def kind_finished(self, index: int) -> None:
        """The operation at `index` is the last one of its kind in this run of the plan.

        Called after the operation at `index` was handled, when the next operation has another
        kind, and after the plan's last operation.
        """

    def stopped(self, index: int) -> None:
        """The apply stopped at the operation at `index` without recording it as applied.

        Every operation committed before it was already reported through `committed`.
        """

    def should_stop(self) -> bool:
        """Whether the apply must stop before its next dispatch because progress can no longer be saved."""


class ApplyProgressLostError(RuntimeError):
    """The apply stopped because its progress could no longer be saved (spec 022, research R4).

    Raised before a dispatch, so the operation it stopped at was not written. Carries the
    partial `ApplyRecord` as `apply_record`, the attribute every apply failure carries, so the
    failure exit records what was written before the stop: when at least one write was
    dispatched, the run is recorded as interrupted (ADR 0010).
    """

    def __init__(self, message: str, *, apply_record: ApplyRecord) -> None:
        super().__init__(message)
        self.apply_record = apply_record
