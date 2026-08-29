"""Pure timing policy for managed execution liveness."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

RUN_ADMISSION_TTL_ENV = "INFRAHUB_SYNC_RUN_ADMISSION_TTL_SECONDS"
_POLICY_ERROR = "managed liveness settings are invalid"
_TTL_PATTERN = re.compile(r"^[0-9]+$")


@dataclass(frozen=True, slots=True)
class LivenessPolicy:
    """Validated code-owned timing values for one managed service instance."""

    admission_ttl_seconds: int
    stall_threshold_seconds: float
    cadence_seconds: float

    @classmethod
    def from_environment(cls, *, worker_query_seconds: str = "10") -> LivenessPolicy:
        """Build the policy from exact environment and Prefect setting strings."""
        ttl = os.environ.get(RUN_ADMISSION_TTL_ENV, "300")
        if type(ttl) is not str or _TTL_PATTERN.fullmatch(ttl) is None:  # pylint: disable=unidiomatic-typecheck
            raise ValueError(_POLICY_ERROR)
        ttl_value = int(ttl)
        if not 1 <= ttl_value <= 86400:
            raise ValueError(_POLICY_ERROR)
        if type(worker_query_seconds) is not str:  # pylint: disable=unidiomatic-typecheck
            raise ValueError(_POLICY_ERROR)
        try:
            query = Decimal(worker_query_seconds)
        except InvalidOperation as exc:
            raise ValueError(_POLICY_ERROR) from exc
        if not query.is_finite() or not Decimal(0) < query <= Decimal(3600):
            raise ValueError(_POLICY_ERROR)
        threshold = max(float(query * 3), 30.0)
        return cls(ttl_value, threshold, max(0.25, min(5.0, threshold / 2)))
