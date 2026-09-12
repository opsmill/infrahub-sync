"""The write-ownership boundary every planned destination dispatch passes through.

An apply executes a reviewed plan against a destination that another writer may also be
holding. The engine cannot answer whether this process still owns that right — the answer
belongs to whatever serializes writers — so it asks, immediately before every operation it
dispatches and once after the last one. There is no default, no `None`, and no no-op
implementation: an apply assembled without a boundary is refused where it is called,
because a boundary that could be absent would be absent exactly when it mattered.

`WriteDispatchTracker` is what one worker's own failure boundary reads: the Boolean that
separates "this failure certainly wrote nothing" from "this failure may have written
something", and the `ApplyRecord` of what the engine actually did. Both are process-local
diagnostic state — never a receipt, a durable row, a state machine, or an authority to
replay anything.

The record lives here because the window that needs it is wider than the engine call. An
apply that returns cleanly can still fail while writing its sidecar, while releasing the
guard, or while committing product success, and every one of those is a failure after a
dispatch. Keeping the record on the scope, rather than on whichever exception happens to
be unwinding, is what lets one exit path report all of them the same way.

`WriteOwnership` deliberately stays two operations wide. It answers one question — does
this process still hold the right to write — and reporting a result is a different
concern; an apply hands its completed record to `WriteDispatchTracker.record_applied`
through a separate sink, not through the interface that authorizes it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Callable

    from infrahub_sync.plan.models import ApplyRecord


class WriteOwnership(Protocol):
    """The proof surface the apply engine requires around its destination dispatches."""

    def before_operation(self) -> None:
        """Prove this process still holds the write right, then record the dispatch.

        Raises whatever the underlying hold raises. A failure here means the operation
        about to be dispatched was not dispatched.
        """

    def after_final_operation(self) -> None:
        """Prove the hold once more, after the last operation and before any success."""


class WriteDispatchTracker:
    """What one worker's failure boundary knows about its own destination writes."""

    def __init__(self) -> None:
        self._dispatch_started = False
        self._applied_record: ApplyRecord | None = None

    @property
    def dispatch_started(self) -> bool:
        """Whether a proven dispatch has started, so a later failure is uncertain."""
        return self._dispatch_started

    @property
    def applied_record(self) -> ApplyRecord | None:
        """What the engine completed, if it returned before anything else failed."""
        return self._applied_record

    def record_dispatch(self) -> None:
        """Record that one destination operation is about to be dispatched."""
        self._dispatch_started = True

    def record_applied(self, record: ApplyRecord) -> None:
        """Keep the completed record so a later failure can still report it."""
        self._applied_record = record


class ProvenWriteOwnership:
    """The engine boundary: prove an external hold, then record the dispatch it allows.

    Proving first is what makes the tracker truthful. A proof that fails leaves the
    tracker untouched, so a hold lost before the first dispatch stays a known
    pre-dispatch failure, while a hold lost before the second dispatch is already
    uncertain because of the first.
    """

    def __init__(self, *, prove: Callable[[], None], tracker: WriteDispatchTracker) -> None:
        self._prove = prove
        self._tracker = tracker

    def before_operation(self) -> None:
        """Prove the hold, then record that a destination operation may start."""
        self._prove()
        self._tracker.record_dispatch()

    def after_final_operation(self) -> None:
        """Prove the hold after the last operation; this alone is not a dispatch."""
        self._prove()
