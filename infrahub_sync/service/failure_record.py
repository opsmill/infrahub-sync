"""The failure record a service stage leaves behind: its evidence and its cause.

A stage that fails ends through `record_failure`, which stores the stage's evidence under
`results["{stage}_failure"]` and the run's cause under `results["failure"]`, the one place
`GET /runs/{run_id}` reads it from. The cause comes from `build_failure_cause` and is built
from named fields only, never from the text of an exception (ADR 0021).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, ClassVar, Literal

from infrahub_sync.configuration.storage import (
    UNSUPPORTED_STORE_MESSAGE,
    UNSUPPORTED_STORE_REASON,
    UnsupportedSyncStoreError,
)
from infrahub_sync.failure_cause import build_failure_cause
from infrahub_sync.plan.models import ApplyRecord
from infrahub_sync.product_store import ExecutionFinishWriteback, ExecutionMergeWriteback, ExecutionWriteback

from .service import PLAN_ARTIFACT_ID

if TYPE_CHECKING:
    from collections.abc import Sequence

    from infrahub_sync.orchestration.flow import RunLogger
    from infrahub_sync.product_store import ProductProjection

Stage = Literal["plan", "verify", "apply", "sync"]


class RegisteredPlanVerificationError(ValueError):
    """A managed plan refusal with a fixed, safe recovery signal for the CLI."""

    # Read by `infrahub_sync.failure_cause`, which cannot import the service flow.
    cause_failure_type: ClassVar[str] = "verification_failed"

    def __init__(self, message: str, recovery_action: Literal["rebuild", "compatible_version"] | None) -> None:
        super().__init__(message)
        self.recovery_action = recovery_action


def failure_evidence(
    stage: Stage,
    exc: Exception,
    *,
    outcome: str,
    record: ApplyRecord | None,
    operations: Sequence[Mapping[str, Any]] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Describe one failure: its evidence, with whatever is known about what it wrote, and its cause.

    A failure raised by the engine carries its own partial record; a failure raised after
    the engine returned — a sidecar write, the guard's release, the product commit — carries
    none, and the scope's record is what that one wrote.

    The cause is built here and only here. It is built before `sanitize_exception_chain`
    replaces the exception, because the replacement keeps none of the types the cause reads.
    `operations` are the saved plan's operations; with the apply record they give the
    cause's `apply_progress`.
    """
    evidence: dict[str, Any] = {
        "stage": stage,
        "outcome": outcome,
        "error_type": type(exc).__name__,
        # The class the wrapper was raised from, for scripts that read it today. The apply
        # path wraps exactly once, so the immediate cause is the one that decided the failure.
        #
        # ADR 0021: nothing that leaves the worker is built from the text of an exception. A
        # class name is an identifier from this process's own taxonomy; the cause next to
        # this evidence is built by `build_failure_cause` from named fields only.
        "cause_type": None if exc.__cause__ is None else type(exc.__cause__).__name__,
    }
    if isinstance(exc, UnsupportedSyncStoreError):
        evidence.update(reason=UNSUPPORTED_STORE_REASON, message=UNSUPPORTED_STORE_MESSAGE)
    if stage == "apply" and isinstance(exc, RegisteredPlanVerificationError) and exc.recovery_action is not None:
        evidence["recovery_action"] = exc.recovery_action
    written = effective_apply_record(exc, record)
    if written is not None:
        evidence.update(written.as_summary_keys())
    cause = build_failure_cause(stage, exc, apply_record=written, operations=operations)
    return evidence, cause.model_dump(mode="json")


def planned_operations(projection: ProductProjection, run_id: str) -> list[Mapping[str, Any]] | None:
    """The operations of the run's saved plan, as its review document holds them, or None.

    The cause reads only each operation's ID, action, kind and identity. A plan that cannot be
    read leaves the cause without `apply_progress` rather than without a cause.
    """
    stored = projection.lookup_artifact(run_id, PLAN_ARTIFACT_ID).value
    if stored is None:
        return None
    try:
        document = json.loads(stored)
    except ValueError:
        return None
    operations = document.get("operations") if isinstance(document, Mapping) else None
    if not isinstance(operations, list):
        return None
    return [operation for operation in operations if isinstance(operation, Mapping)]


def effective_apply_record(exc: Exception, record: ApplyRecord | None) -> ApplyRecord | None:
    """The record that describes this failure: the one the error carries, else the scope's.

    A failure raised by the engine carries its own partial record; one raised after the
    engine returned carries none, and the scope's record is what that one wrote. Resolved in
    one place because the ambiguity verdict and the recorded evidence must agree about what
    was written — reading different records is how a run gets an outcome its own evidence
    contradicts.
    """
    carried = getattr(exc, "apply_record", None)
    return carried if isinstance(carried, ApplyRecord) else record


def record_failure(  # pylint: disable=too-many-arguments,too-many-positional-arguments,too-many-locals
    run_id: str,
    stage: Stage,
    exc: Exception,
    run_logger: RunLogger,
    secrets: Sequence[str],
    projection: ProductProjection,
    flow_run_id: str,
    worker_id: str,
    *,
    dispatch_started: bool,
    record: ApplyRecord | None,
) -> None:
    """Best-effort durable product evidence without masking the worker failure.

    `dispatch_started` is the one thing this boundary cannot re-derive: only the stage
    that ran knows whether it proved its hold and began a destination operation. Before
    that point a failure is an ordinary failed run; after it, nothing can claim the
    destination was left untouched, so the verdict is interrupted/ambiguous and the store
    records that the run requires reconciliation in the same transaction.

    A record that **names a failing operation** is the sharper account, and it overrides
    `dispatch_started` for exactly that case: the write surface knows whether that operation
    reached a mutation, and a refusal it raised before the write leaves nothing to
    reconcile. So a named failing operation is ambiguous only where its reach is unknown
    (S6). Everything else keeps the `dispatch_started` rule — including a **completed**
    record attached to a post-write failure, where the operations did write and the
    interruption came afterwards, in `record_applied`, the sidecar transition, the product
    commit or the final ownership proof.
    """
    try:
        stored = projection.lookup_run(run_id).value
        if stored is None:
            return
        described = effective_apply_record(exc, record)
        if described is not None and described.failed_operation is not None:
            ambiguous = described.failed_operation_wrote is not False
        else:
            ambiguous = dispatch_started
        terminal_state, terminal_outcome = ("interrupted", "ambiguous") if ambiguous else ("failed", "failed")
        operations = planned_operations(projection, run_id) if described is not None and stage != "verify" else None
        evidence, cause = failure_evidence(stage, exc, outcome=terminal_outcome, record=record, operations=operations)
        results: dict[str, Any] = {f"{stage}_failure": evidence}
        if stage == "verify":
            # A failed verify leaves the run's phase unchanged (`planned`), and the run-level
            # `failure` stays null for a run that has not failed or stopped
            # (contracts/sync-api.md): the cause is kept in `verify_failure` only.
            evidence["failure"] = cause
            writeback: ExecutionWriteback = ExecutionMergeWriteback(results=results)
        else:
            partial = {
                key: evidence[key]
                for key in (
                    "applied_operations",
                    "skipped_delete_operations",
                    "skipped_delete_count",
                    "failed_operation",
                    "failed_operation_wrote",
                    "may_have_partially_written",
                )
                if key in evidence
            }
            # The cause replaces any earlier one, such as the stop of a `sync` this apply follows.
            results["failure"] = cause
            writeback = ExecutionFinishWriteback(
                phase=f"{stage}-interrupted" if ambiguous else f"{stage}-failed",
                outcome=terminal_outcome,
                finished_at=datetime.now(timezone.utc),
                summary={**stored.summary, "failed_stage": stage, **partial},
                results={**stored.results, **results},
            )
        projection.commit_claimed_execution(
            run_id,
            flow_run_id,
            worker_id=worker_id,
            terminal_at=datetime.now(timezone.utc),
            terminal_state=terminal_state,
            terminal_outcome=terminal_outcome,
            writeback=writeback,
            secrets=secrets,
        )
    except Exception as persistence_error:  # noqa: BLE001  # pylint: disable=broad-exception-caught
        run_logger.log(
            logging.WARNING,
            "service failure evidence could not be persisted (%s)",
            type(persistence_error).__name__,
        )
