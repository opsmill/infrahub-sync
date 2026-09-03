"""Append a durable service execution the way the API does: through its owning receipt.

Every `prefect_executions` row belongs to exactly one still-unresolved mutation receipt,
and a write execution additionally needs the run's write admission to name that receipt.
Service tests that only need a durable link would otherwise have to restate that whole
reservation; this builds it once.

Not a test module: no assertions live here.
"""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from itertools import count
from typing import TYPE_CHECKING

from infrahub_sync.product_store import MutationReceipt

if TYPE_CHECKING:
    from infrahub_sync.product_store import PrefectExecutionLink, ProductProjection

_ORDINAL = count(1)
_WRITE_PURPOSES = ("apply", "sync")


def append_execution(
    projection: ProductProjection,
    run_id: str,
    link: PrefectExecutionLink,
    *,
    allocate_attempt: bool = False,
) -> PrefectExecutionLink:
    """Reserve the receipt that owns this append, then append `link` through it."""
    ordinal = next(_ORDINAL)
    receipt_id = f"m-fixture-{ordinal}"
    now = datetime.now(timezone.utc)
    reserved, _created = projection.reserve_mutation(
        MutationReceipt(
            receipt_id=receipt_id,
            actor="owner",
            key_digest=sha256(f"fixture-key-{ordinal}".encode()).hexdigest(),
            operation=link.purpose,
            target_run_id=run_id,
            request_fingerprint=sha256(f"{link.purpose}:{run_id}:{ordinal}".encode()).hexdigest(),
            reason="service execution fixture",
            resource_id=run_id,
            run_id=run_id,
            prefect_key=sha256(f"prefect:{receipt_id}".encode()).hexdigest(),
            created_at=now,
            updated_at=now,
        ),
        admit_write=link.purpose in _WRITE_PURPOSES,
    )
    return projection.add_prefect_execution(
        run_id, link, receipt_id=reserved.receipt_id, allocate_attempt=allocate_attempt
    )


class GrantingGuardSession:
    """A direct-session double that always grants the configuration write guard.

    Stage-driving tests whose subject is something else — binding, schema, ordering —
    still cross the guard, so they need a session that answers its three statements. What
    the guard does with a session that does not grant is
    `tests/service/test_managed_write_guard.py`'s subject, not theirs.
    """

    def execute(self, query: str, params: object = None) -> _GrantingCursor:  # noqa: PLR6301
        """Answer the acquire, ownership, and release statements the guard issues."""
        _ = params
        if "pg_locks" in query:
            return _GrantingCursor((_BACKEND_PID, True))
        if "pg_advisory_unlock" in query:
            return _GrantingCursor((True, _BACKEND_PID))
        return _GrantingCursor((None,))

    def close(self) -> None:
        """Close the dedicated session."""


class _GrantingCursor:
    def __init__(self, row: tuple[object, ...]) -> None:
        self._row = row

    def fetchone(self) -> tuple[object, ...]:
        return self._row


_BACKEND_PID = 4242


def bind_granting_guard(monkeypatch: object, flow_module: object) -> None:
    """Bind a granting configuration write guard onto one service flow module."""
    monkeypatch.setattr(flow_module, "service_guard_session", GrantingGuardSession)  # ty: ignore[unresolved-attribute]
    monkeypatch.setattr(flow_module, "service_guard_secrets", lambda: ())  # ty: ignore[unresolved-attribute]
