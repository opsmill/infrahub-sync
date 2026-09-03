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


def owning_receipt(projection: ProductProjection, run_id: str, *, purpose: str) -> MutationReceipt:
    """Reserve the receipt that may append one execution of `purpose` to `run_id`."""
    ordinal = next(_ORDINAL)
    receipt_id = f"m-fixture-{ordinal}"
    now = datetime.now(timezone.utc)
    reserved, _created = projection.reserve_mutation(
        MutationReceipt(
            receipt_id=receipt_id,
            actor="owner",
            key_digest=sha256(f"fixture-key-{ordinal}".encode()).hexdigest(),
            operation=purpose,
            target_run_id=run_id,
            request_fingerprint=sha256(f"{purpose}:{run_id}:{ordinal}".encode()).hexdigest(),
            reason="service execution fixture",
            resource_id=run_id,
            run_id=run_id,
            prefect_key=sha256(f"prefect:{receipt_id}".encode()).hexdigest(),
            created_at=now,
            updated_at=now,
        ),
        admit_write=purpose in _WRITE_PURPOSES,
    )
    return reserved


def append_execution(
    projection: ProductProjection,
    run_id: str,
    link: PrefectExecutionLink,
    *,
    allocate_attempt: bool = False,
) -> PrefectExecutionLink:
    """Reserve the owning receipt and append `link` as the one execution it may add."""
    receipt = owning_receipt(projection, run_id, purpose=link.purpose)
    return projection.add_prefect_execution(
        run_id, link, receipt_id=receipt.receipt_id, allocate_attempt=allocate_attempt
    )
