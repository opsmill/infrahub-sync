"""One explicit write-ownership boundary for tests that drive a real apply.

The engine requires a boundary and product code offers no default, so every apply a test
drives has to pass one. This recorder is the plain "the hold is good" answer: it proves
nothing about a real writer, it only records that it was asked, so a case that cares about
the asking can read `proofs` and every other case can ignore it.

Not a test module: no assertions live here.
"""

from __future__ import annotations


class GrantedOwnership:
    """A boundary that always grants, recording each proof it was asked for."""

    def __init__(self) -> None:
        self.proofs: list[str] = []

    def before_operation(self) -> None:
        """Record one pre-dispatch proof."""
        self.proofs.append("before-operation")

    def after_final_operation(self) -> None:
        """Record the closing proof."""
        self.proofs.append("after-final-operation")


def granted_ownership() -> GrantedOwnership:
    """Return a fresh always-granting write-ownership boundary."""
    return GrantedOwnership()
