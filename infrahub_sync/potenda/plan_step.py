"""What Potenda reports about the parts of the plan step that follow the two reads.

`Potenda` calls an optional `PlanStepObserver` when comparing starts (`Potenda.diff`), around
the problem pass that checks the derived plan, and around the plan artifact write. The engine
imports nothing from the service: the Sync service's worker supplies the one observer that
stores the reports (spec 019, research R10 and R12; the pattern of ADR 0035).

A part that raises is never reported as ended, so the part that was running when the plan step
failed stays the running part (FR-016).
"""

from __future__ import annotations

from typing import Literal, Protocol

PlanStepPartName = Literal["comparing", "checking", "saving"]
PLAN_STEP_PARTS: tuple[PlanStepPartName, ...] = ("comparing", "checking", "saving")


class PlanStepObserver(Protocol):
    """Receives the start and the end of each part of the plan step after the reads.

    The parts run one after the other on one thread. An exception raised here never changes
    the plan.
    """

    def part_started(self, part: PlanStepPartName) -> None:
        """The part started."""

    def part_ended(self, part: PlanStepPartName) -> None:
        """The part ended without an error."""
