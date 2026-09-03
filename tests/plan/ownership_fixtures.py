"""One explicit write-ownership boundary for tests that drive a real apply.

The engine requires a boundary and product code offers no default, so every apply a test
drives has to pass one. This is the plain "the hold is good" answer, for cases whose
subject is something other than the proving itself; a case that cares about the proving
passes its own recorder.

Not a test module: no assertions live here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from infrahub_sync.plan.models import ApplyRecord


class GrantedOwnership:
    """A write-ownership boundary that always grants."""

    def before_operation(self) -> None:
        """Grant the pre-dispatch proof."""

    def after_final_operation(self) -> None:
        """Grant the closing proof."""

    def record_applied(self, record: ApplyRecord) -> None:
        """Accept the engine's completed record; no case here reads it back."""


def granted_ownership() -> GrantedOwnership:
    """Return an always-granting write-ownership boundary."""
    return GrantedOwnership()
