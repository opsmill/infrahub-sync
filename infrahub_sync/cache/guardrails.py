"""The row-count comparison, as a primitive with no state of its own.

`RowcountGuardrail` compares counts a caller hands it against a mapping of previous
counts the same caller supplies, and refuses a per-resource collapse. It reads no file,
loads no baseline, and has no operator flag: the filesystem baseline this once read is
deleted, and the durable replacement is the configuration baseline the managed write path
records in PostgreSQL (`infrahub_sync.product_store`).

Nothing calls this today. It is kept as the comparison a later row-count refusal would
need, so that feature can be built around a fixed placement, a missing-baseline rule, a
failure class, and an operator contract rather than around a rediscovered ratio.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


class RowcountGuardrailError(RuntimeError):
    """Raised when a resource's count falls below `drop_threshold` of its previous count."""


@dataclass
class RowcountGuardrail:
    """Reject per-resource count drops below `drop_threshold` of `previous`.

    Every input is the caller's: `previous` is the mapping it chose to compare against,
    and `allow_drop` is how it says a collapse is expected. A resource absent from
    `previous`, or recorded there as zero, has nothing to compare against and passes.
    """

    previous: dict[str, int]
    drop_threshold: float = 0.5
    allow_drop: bool = False
    triggered: list[str] = field(default_factory=list)

    def check(self, resource: str, *, current: int) -> None:
        """Raise `RowcountGuardrailError` when `current/prior < drop_threshold`."""
        if self.allow_drop:
            return
        prior = self.previous.get(resource)
        if prior is None or prior == 0:
            return
        ratio = current / prior
        if ratio >= self.drop_threshold:
            return
        msg = (
            f"Rowcount guardrail tripped for {resource!r}: dropped from "
            f"{prior} to {current} (ratio {ratio:.2f} < threshold "
            f"{self.drop_threshold:.2f}). Construct this guardrail with allow_drop=True "
            f"to accept an expected drop."
        )
        self.triggered.append(resource)
        logger.error(msg)
        raise RowcountGuardrailError(msg)
