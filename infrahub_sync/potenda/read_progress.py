"""What Potenda reports about each side's read while a `plan` or `sync` stage runs.

`Potenda._load_side` calls an optional `SideReadObserver` when a side's read starts and when it
ends. The engine imports nothing from the service: the Sync service's worker supplies the one
observer that stores the reports (spec 018, research R2).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal, Protocol

if TYPE_CHECKING:
    from collections.abc import Mapping

Side = Literal["source", "destination"]
ReadMode = Literal["full", "incremental"]


@dataclass(frozen=True)
class SideReadReport:
    """How one side's read ended.

    A read that succeeded carries its mode and the number of nodes of every top-level kind
    and of every other kind the side holds, sorted by kind name. A read that failed carries only the class name of the exception,
    never its message, so the report holds no address or secret.
    """

    succeeded: bool
    read_mode: ReadMode | None = None
    nodes_by_kind: Mapping[str, int] = field(default_factory=dict)
    error_class: str | None = None


class SideReadObserver(Protocol):
    """Receives the start and the end of each side's read.

    The two sides can be read on two threads at once, so an implementation must accept calls
    for both sides concurrently. An exception raised here never changes the read's result.
    """

    def read_started(self, side: Side) -> None:
        """The side's read started."""

    def read_ended(self, side: Side, report: SideReadReport) -> None:
        """The side's read ended, as `report` describes."""
