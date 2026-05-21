"""Rowcount guardrails.

The previous successful run's rowcounts are kept in
`<run_dir>/last-successful-rowcounts.json` (one canonical copy per pipeline,
updated only when a sync completes successfully). The next run loads the
baseline; if any resource's current count is below the threshold the engine
raises and asks the operator to confirm with `--allow-rowcount-drop`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


class RowcountGuardrailError(RuntimeError):
    """Raised when a resource's rowcount drops below the threshold."""


@dataclass
class RowcountGuardrail:
    previous: dict[str, int]
    drop_threshold: float = 0.5
    allow_drop: bool = False
    triggered: list[str] = field(default_factory=list)

    def check(self, resource: str, *, current: int) -> None:
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
            f"{self.drop_threshold:.2f}). Pass --allow-rowcount-drop to override."
        )
        self.triggered.append(resource)
        logger.error(msg)
        raise RowcountGuardrailError(msg)
