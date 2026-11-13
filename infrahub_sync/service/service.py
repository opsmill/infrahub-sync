"""Run-oriented Sync API behavior over durable Sync product records."""

# pylint: disable=too-many-lines
# This bounded status and render-redaction repair takes the module past pylint's
# default 1000-line cap. Splitting it is a separate, larger change.

from __future__ import annotations

import json
import logging
import sys
import threading
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from typing import TYPE_CHECKING, Any, Literal, NoReturn
from uuid import uuid4

from pydantic import ValidationError

from infrahub_sync.cache.paths import generate_run_id
from infrahub_sync.client.models import DEFAULT_LOG_LIMIT, PlanReportPageResource
from infrahub_sync.configuration import ConfigurationPackageParseError, parse_configuration_package
from infrahub_sync.configuration.runtime import effective_destination_branch
from infrahub_sync.configuration.storage import UNSUPPORTED_STORE_MESSAGE, UNSUPPORTED_STORE_REASON
from infrahub_sync.configuration.validation import credential_namespace_finding
from infrahub_sync.execution import collect_secret_values, redact, sanitize_exception_chain
from infrahub_sync.plan.canonical import canonical_json_bytes
from infrahub_sync.product_store import (
    NO_SAVED_PLAN_MESSAGE,
    PLAN_ARTIFACT_ID,
    WRITE_PURPOSES,
    AuditEvent,
    DuplicatePrefectExecutionError,
    EventFilters,
    MutationReceipt,
    PrefectExecutionLink,
    ProductProjection,
    ProductRun,
    RelatedResource,
    RunFilters,
)
from infrahub_sync.product_store.configs import CONFIGURATION_NOT_FOUND_REASON, CONFIGURATION_VERSION_NOT_FOUND_REASON
from infrahub_sync.product_store.models import (
    RUN_ALREADY_CANCELLED_MESSAGE,
    RUN_CANCELLED_MESSAGE,
    RunApproval,
    already_approved_body,
    not_approvable_message,
)

from .events import RUN_CANCELLED, run_decision_type, run_event
from .liveness import CancellationSelectionUnavailableError, select_cancellable_execution
from .models import (
    ApplyRunRequest,
    ApprovedPlanSummaryResource,
    ApprovedRunResource,
    ApproveRunRequest,
    ArtifactListResource,
    CancelRunRequest,
    CreateRunRequest,
    EmittedArtifactListResource,
    EmittedPlanResource,
    EventPage,
    OrchestrationSummary,
    PlanResource,
    ResultsResource,
    ResumeRunRequest,
    RunLogLine,
    RunLogPage,
    RunPage,
    RunResource,
    ServiceStatusResource,
    VerifyRunRequest,
    WorkerStatusResource,
    public_event_page,
    public_run_log_page,
    public_run_page,
    public_run_resource,
)
from .orchestration import (
    Observation,
    OrchestrationUnavailableError,
    PoolStatus,
    ServiceOrchestration,
    normalized_pool_status,
)
from .paging import FilterValue, decode_cursor, timestamp_cursor, timestamp_key, validate_limit
from .prefect_ui import flow_run_url
from .read_progress import stage_reads
from .resume_view import (
    apply_progress_view,
    run_has_saved_plan,
    run_is_resumable,
    run_resumes,
)
from .run_log import page as log_page
from .run_log import sync_lines
from .settings import approval_required

if TYPE_CHECKING:
    from collections.abc import Callable

    from infrahub_sync.configuration import ConfigurationPackage

    from .auth import Principal

logger = logging.getLogger(__name__)
# How many plan operations the in-process cache of plan layouts holds, over all its runs
# (research R14). A tier L plan has about 88,000 operations, and one cached operation takes
# about 200 bytes, so the bound keeps the cache under about 40 MB in every API process.
PLAN_LAYOUT_CACHE_OPERATIONS = 200_000
# The key of the published review document that holds the plan report's lines (spec 020).
# `GET /runs/{run_id}/plan` does not return it; the paged report routes do.
PLAN_REPORT_KEY = "report"
# The `entry` of a plan report line, one per report route.
ReportEntry = Literal["problem", "left_out", "kept"]


def _refusal_code(receipt: MutationReceipt) -> str | None:
    """The error code a stored refusal carries, or `None` for an accepted request."""
    body = receipt.response_body
    error = body.get("error") if isinstance(body, dict) else None
    code = error.get("code") if isinstance(error, dict) else None
    return code if isinstance(code, str) else None


def _reservation_outcome(receipt: MutationReceipt) -> str:
    """Name what an already-answered receipt is: a replay, or this run's stored refusal."""
    assert receipt.response_status is not None  # an accepted receipt always stores its status.
    return "replayed" if receipt.response_status < 400 else "refused-run-execution-conflict"


def _not_resumable_before_plan(run: ProductRun) -> str | None:
    """Why a resume of the run is refused before its plan is read, or `None`.

    A cancelled run, a run with no interrupted write, and a run that saved no plan, such as a
    `sync` whose worker was ended while it was still planning (research R14). The store checks
    the same conditions again on the run's locked row.
    """
    if run.phase == "cancelled":
        return "this Sync run is cancelled"
    if not run.reconciliation_required:
        return "this Sync run has no interrupted write to resume"
    if not run_has_saved_plan(run):
        return NO_SAVED_PLAN_MESSAGE
    return None


def _service_status(snapshot: PoolStatus) -> ServiceStatusResource:
    """Project internal pool evidence into the deliberately small public schema."""
    snapshot = normalized_pool_status(snapshot)
    if not snapshot.detail_available:
        return ServiceStatusResource(
            service="ready",
            worker=WorkerStatusResource(
                state="unavailable", detail_available=False, live_workers=None, queue_depth=None, observed_at=None
            ),
        )
    assert snapshot.queue_depth is not None
    assert snapshot.observed_at is not None
    now = snapshot.observed_at
    live = sum(
        worker.status == "online"
        and worker.last_heartbeat is not None
        and worker.heartbeat_interval_seconds is not None
        and worker.heartbeat_interval_seconds > 0
        and max(0.0, (now - worker.last_heartbeat).total_seconds()) <= max(3 * worker.heartbeat_interval_seconds, 30)
        for worker in snapshot.workers
    )
    state = "no-live-worker" if live == 0 else "busy" if snapshot.queue_depth > 0 else "ready"
    return ServiceStatusResource(
        service="ready",
        worker=WorkerStatusResource(
            state=state,
            detail_available=True,
            live_workers=live,
            queue_depth=snapshot.queue_depth,
            observed_at=snapshot.observed_at,
        ),
    )


class ServiceAPIError(Exception):
    """Stable HTTP classification raised by the service boundary."""

    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        *,
        run_id: str | None = None,
        mutation_id: str | None = None,
        details: dict[str, Any] | None = None,
        secrets: tuple[str, ...] = (),
        left_out: int | None = None,
    ) -> None:
        self.status = status
        self.code = code
        self.run_id = run_id
        self.mutation_id = mutation_id
        # Structured context a refusal carries, such as the existing approval; most have none.
        self.details = details
        self.message = redact(message, secrets)
        # The count of records the plan leaves out, on `left-out-confirmation-required` only.
        self.left_out = left_out
        super().__init__(self.message)


def page_size_or_refuse(limit: int | None, *, secrets: tuple[str, ...] = ()) -> int:
    """Return the requested page size, refusing one outside the shared paging bounds.

    Raises:
        ServiceAPIError: ``422 request-invalid`` for a limit outside 1-200.
    """
    try:
        return validate_limit(limit)
    except ValueError as exc:
        raise ServiceAPIError(422, "request-invalid", str(exc), secrets=secrets) from None


_SCHEMA_MISMATCH_MESSAGE = "the request does not match the API schema"
# The largest event-list ``offset`` accepted: a signed 64-bit integer, which every store's OFFSET takes.
MAX_EVENT_OFFSET = 2**63 - 1
# The messages of a log the API cannot serve (contracts/logs-route.md). Neither names the orchestrator.
LOG_EXPIRED_MESSAGE = "the log of this run has expired"
LOG_UNAVAILABLE_MESSAGE = "the log of this run cannot be read now; retry later"


def parse_event_time(value: str) -> datetime:
    """Parse an event-list ``since`` or ``until`` value: an ISO 8601 time with a timezone.

    A trailing ``Z`` is read as UTC.

    Raises:
        ServiceAPIError: ``422 request-invalid`` for a value that is not such a time.
    """
    text = value[:-1] + "+00:00" if value.endswith(("Z", "z")) else value
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        raise ServiceAPIError(422, "request-invalid", _SCHEMA_MISMATCH_MESSAGE) from None
    if parsed.utcoffset() is None:
        raise ServiceAPIError(422, "request-invalid", _SCHEMA_MISMATCH_MESSAGE)
    return parsed


def _related_resource(value: str) -> RelatedResource:
    """Parse one ``<kind>:<id>`` event-list filter; the kind is the text before the first colon.

    Raises:
        ValueError: ``value`` has no colon, or its kind or ID is not a related resource's.
    """
    kind, separator, identifier = value.partition(":")
    if not separator:
        msg = "a related resource is written <kind>:<id>"
        raise ValueError(msg)
    return RelatedResource.model_validate({"kind": kind, "id": identifier})


PlanLayout = tuple[tuple[str, str, str], ...]


class _PlanLayoutCache:
    """The saved plans' `(operation_id, kind, action)` triples, keyed by run and plan digest (research R14).

    A run publishes its plan once, and a published artifact never changes, so an entry is
    never stale and every API process computes the same one: no invalidation is needed. The
    least recently read run is dropped first once the cache holds more than `max_operations`
    operations; a plan larger than the bound is not cached. A failed read is never cached.
    """

    def __init__(self, max_operations: int) -> None:
        self._max_operations = max_operations
        self._entries: OrderedDict[tuple[str, str], PlanLayout] = OrderedDict()
        self._operations = 0
        # Sync routes run in a thread pool, so two requests can use the cache at once.
        self._lock = threading.Lock()

    def get(self, key: tuple[str, str]) -> PlanLayout | None:
        with self._lock:
            layout = self._entries.get(key)
            if layout is not None:
                self._entries.move_to_end(key)
            return layout

    def put(self, key: tuple[str, str], layout: PlanLayout) -> None:
        if len(layout) > self._max_operations:
            return
        with self._lock:
            if key in self._entries:
                return
            self._entries[key] = layout
            self._operations += len(layout)
            while self._operations > self._max_operations:
                _key, dropped = self._entries.popitem(last=False)
                self._operations -= len(dropped)


class RunService:
    """Direct application service; Prefect owns all execution mechanics."""

    def __init__(
        self,
        projection: ProductProjection,
        orchestration: ServiceOrchestration,
        *,
        secrets: tuple[str, ...] = (),
        cancellation_recovery_seconds: float = 30.0,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        prefect_ui_url: str | None = None,
    ) -> None:
        self._projection = projection
        self._prefect_ui_url = prefect_ui_url
        self._orchestration = orchestration
        self._secrets = tuple(dict.fromkeys((*collect_secret_values(), *secrets)))
        self._cancellation_recovery_seconds = cancellation_recovery_seconds
        self._clock = clock
        self._plan_layouts = _PlanLayoutCache(PLAN_LAYOUT_CACHE_OPERATIONS)

    async def create_run(
        self, request: CreateRunRequest, principal: Principal, idempotency_key: str
    ) -> tuple[int, dict[str, Any]]:
        """Reserve one product identity and accept its first service execution."""
        self._require_non_secret_parameters(
            principal.actor,
            request.operation,
            request.reason,
            config_id=request.config_id,
            branch=request.branch,
        )
        run_id = generate_run_id()
        now = datetime.now(timezone.utc)
        receipt = self._new_receipt(
            actor=principal.actor,
            idempotency_key=idempotency_key,
            operation=request.operation,
            target_run_id=None,
            run_id=run_id,
            body=request.model_dump(mode="json"),
            reason=request.reason,
            now=now,
        )
        existing = self._projection.lookup_mutation(receipt.actor, receipt.key_digest).value
        if existing is not None:
            self._require_matching_receipt(existing, receipt)
            if existing.state == "accepted":
                return await self._resume_or_replay(existing, {}, principal, request.reason)
        package, package_checksum = self._registered_configuration(request.config_id, request.registry_version)
        sync_name = package.configuration.name
        # The worker resolves the branch it plans and writes against through the same function,
        # so the stored branch is the saved plan's destination branch.
        destination_branch = effective_destination_branch(package.configuration.destination.settings, request.branch)
        if request.operation == "sync" and not request.confirm_writes:
            self._audit(
                None,
                actor=principal.actor,
                operation="sync",
                reason=request.reason,
                outcome="refused-confirmation",
                config_id=request.config_id,
                registry_version=request.registry_version,
                branch=destination_branch,
            )
            raise self._error(409, "confirmation-required", "confirm_writes=true is required for sync")
        if request.operation == "sync" and approval_required():
            self._audit(
                None,
                actor=principal.actor,
                operation="sync",
                reason=request.reason,
                outcome="refused-approval-required",
            )
            raise self._error(
                409,
                "approval-required-for-sync",
                "this Sync API requires an approved plan before any write, so sync is refused; "
                "run plan, approve and apply instead",
            )

        run = ProductRun(
            run_id=run_id,
            operation=request.operation,
            configuration_reference=f"{request.config_id}@{request.registry_version}",
            config_id=request.config_id,
            registry_version=request.registry_version,
            package_checksum=package_checksum,
            actor=principal.actor,
            started_at=now,
            phase="accepted",
            summary={"sync_name": sync_name},
            destination_branch=destination_branch,
        )
        reserved, _ = self._projection.reserve_mutation(
            receipt,
            run=run,
            admit_write=request.operation == "sync",
            secrets=self._secrets,
        )
        self._require_matching_receipt(reserved, receipt)
        if reserved.state == "accepted":
            self._audit(
                reserved.run_id,
                actor=principal.actor,
                operation=request.operation,
                reason=request.reason,
                outcome="replayed",
            )
            return self._stored_response(reserved)
        parameters: dict[str, object] = {
            "run_id": reserved.run_id,
            "stage": request.operation,
            "config_id": request.config_id,
            "registry_version": request.registry_version,
            "package_checksum": package_checksum,
            "branch": request.branch,
            "expected_checksum": None,
            "confirm_writes": request.confirm_writes,
            "configuration_name": sync_name,
        }
        return await self._submit(reserved, parameters, principal, request.reason)

    def _registered_configuration(
        self, config_id: str, registry_version: int, *, expected_checksum: str | None = None
    ) -> tuple[ConfigurationPackage, str]:
        """Read the immutable registered package before allocating any run-side state."""
        stored = self._projection.lookup_configuration_version(config_id, registry_version).value
        if stored is None:
            raise self._error(
                404, "configuration-version-not-found", "the requested configuration version does not exist"
            )
        try:
            package = parse_configuration_package(stored.declared_content)
        except ConfigurationPackageParseError:
            raise self._error(
                503, "configuration-version-invalid", "the registered configuration version is invalid"
            ) from None
        if package.checksum() != stored.package_checksum or (
            expected_checksum is not None and expected_checksum != stored.package_checksum
        ):
            raise self._error(503, "configuration-version-invalid", "the registered configuration version is invalid")
        if package.configuration.store is not None:
            raise self._error(422, UNSUPPORTED_STORE_REASON, UNSUPPORTED_STORE_MESSAGE)
        for name, reference in package.credentials.items():
            if (finding := credential_namespace_finding(name, reference, secrets=self._secrets)) is not None:
                raise self._error(
                    422,
                    finding.code,
                    f"{finding.message}; register a new version with prefixed identifiers "
                    "and recreate saved plans against it",
                )
        return package, stored.package_checksum

    async def verify_run(
        self, run_id: str, request: VerifyRunRequest, principal: Principal, idempotency_key: str
    ) -> tuple[int, dict[str, Any]]:
        """Accept an independently auditable, read-only verification."""
        run = self._owned_run(run_id, principal, "verify", request.reason)
        body = request.model_dump(mode="json")
        # The branch the run was created for, so the destination-binding check compares the
        # plan with the destination it was planned against. `None` for a run that predates the
        # stored branch, which keeps the configuration's default, as `apply` without one does.
        parameters = self._stage_parameters(run, "verify", branch=run.destination_branch, confirm_writes=False)
        existing = self._lookup_existing_receipt(
            run,
            principal,
            idempotency_key,
            operation="verify",
            reason=request.reason,
            body=body,
        )
        if existing is not None:
            return await self._resume_or_replay(existing, parameters, principal, request.reason)
        self._require_supported_run_configuration(run)
        if not run.reconciliation_required or run_has_saved_plan(run):
            # A run that requires reconciliation but saved no plan, a `sync` stopped while it
            # was still planning, keeps its write admission, so the reservation refuses it with
            # `run-execution-conflict`, which the audit history records. Reading its plan first
            # would answer 503 instead (research R14).
            self._plan(run_id)
        receipt = self._reserve_existing(
            run,
            principal,
            idempotency_key,
            operation="verify",
            reason=request.reason,
            body=body,
        )
        return await self._resume_or_replay(receipt, parameters, principal, request.reason)

    async def apply_run(
        self, run_id: str, request: ApplyRunRequest, principal: Principal, idempotency_key: str
    ) -> tuple[int, dict[str, Any]]:
        """Accept apply only for the exact reviewed retained plan."""
        run = self._owned_run(run_id, principal, "apply", request.reason)
        body = request.model_dump(mode="json")
        parameters = self._stage_parameters(
            run,
            "apply",
            branch=request.branch,
            expected_checksum=request.expected_checksum,
            confirm_writes=True,
        )
        if request.confirm_left_out:
            # The worker's engine checks the confirmation again (research R10). Sent only
            # when true, so an apply that confirms nothing keeps its parameters of today.
            parameters["confirm_left_out"] = True
        existing = self._lookup_existing_receipt(
            run,
            principal,
            idempotency_key,
            operation="apply",
            reason=request.reason,
            body=body,
        )
        if existing is not None:
            if existing.state != "accepted" and approval_required():
                # A receipt reserved before the setting was turned on, whose submission failed,
                # resumes into a submission: it must pass the approval check first (FR-015).
                self._require_matching_approval(run, request, principal)
            return await self._resume_or_replay(existing, parameters, principal, request.reason)
        self._require_non_secret_parameters(
            principal.actor,
            "apply",
            request.reason,
            run_id=run_id,
            branch=request.branch,
        )
        if not request.confirm_writes:
            self._audit(
                run_id,
                actor=principal.actor,
                operation="apply",
                reason=request.reason,
                outcome="refused-confirmation",
            )
            raise self._error(409, "confirmation-required", "confirm_writes=true is required for apply", run_id=run_id)
        self._refuse_cancelled(run, principal, "apply", request.reason)
        if approval_required():
            self._require_matching_approval(run, request, principal)
        self._require_supported_run_configuration(run)
        plan = self._plan(run_id)
        if not plan.checksum_ok or plan.checksum != request.expected_checksum:
            self._audit(
                run_id,
                actor=principal.actor,
                operation="apply",
                reason=request.reason,
                outcome="refused-checksum",
            )
            raise self._error(
                409,
                "checksum-conflict",
                "expected_checksum does not match the retained reviewed plan",
                run_id=run_id,
            )
        # After the checksum, so the count refers to the plan the person reviewed, and before
        # the reservation, so a refusal takes no write admission (spec 020 FR-010, FR-011).
        left_out = plan.summary.left_out
        if left_out > 0 and not request.confirm_left_out:
            self._audit(
                run_id,
                actor=principal.actor,
                operation="apply",
                reason=request.reason,
                outcome="refused-left-out-not-confirmed",
            )
            raise ServiceAPIError(
                409,
                "left-out-confirmation-required",
                f"this plan leaves out {left_out} records; confirm with confirm_left_out=true",
                run_id=run_id,
                secrets=self._secrets,
                left_out=left_out,
            )
        receipt = self._reserve_existing(
            run,
            principal,
            idempotency_key,
            operation="apply",
            reason=request.reason,
            body=body,
            admit_write=True,
        )
        return await self._resume_or_replay(receipt, parameters, principal, request.reason)

    async def resume_run(  # pylint: disable=too-many-locals
        self, run_id: str, request: ResumeRunRequest, principal: Principal, idempotency_key: str
    ) -> tuple[int, dict[str, Any]]:
        """Accept a resume of an interrupted write from the run's saved position (spec 022).

        The checks are `apply`'s, in the same order: ownership, non-secret parameters,
        `confirm_writes`, a cancelled run, approval, the supported configuration, the checksum
        and the left-out confirmation (FR-011, FR-018). The store then decides, on the run's
        locked row, whether the run is resumable, whether a `verify` passed after its latest
        interruption and whether another resume is unfinished (FR-012, FR-013). Every request,
        accepted or refused, is recorded in the run's audit history and event list with
        operation `resume` and the optional reason (FR-014).

        The interrupted write may be an `apply` or a `sync`: a `sync` run is resumed with the
        checksum of the plan it saved before its apply part (FR-017).
        """
        operation = "resume"
        run = self._owned_run(run_id, principal, operation, request.reason)
        body = request.model_dump(mode="json")
        # The branch the run was planned against, as `verify` uses: the saved plan's destination.
        parameters = self._stage_parameters(
            run,
            operation,
            branch=run.destination_branch,
            expected_checksum=request.expected_checksum,
            confirm_writes=True,
        )
        if request.confirm_left_out:
            parameters["confirm_left_out"] = True
        existing = self._lookup_existing_receipt(
            run, principal, idempotency_key, operation=operation, reason=request.reason, body=body
        )
        if existing is not None:
            if existing.state != "accepted" and approval_required():
                self._require_matching_approval(run, request, principal, operation=operation)
            return await self._resume_or_replay(existing, parameters, principal, request.reason)
        self._require_non_secret_parameters(principal.actor, operation, request.reason, run_id=run_id)
        if not request.confirm_writes:
            self._audit(
                run_id,
                actor=principal.actor,
                operation=operation,
                reason=request.reason,
                outcome="refused-confirmation",
            )
            raise self._error(409, "confirmation-required", "confirm_writes=true is required for resume", run_id=run_id)
        # A resume refuses every run it cannot resume with `run-not-resumable`, and the message
        # says why (FR-013). These checks come before the saved plan is read: a `sync` run that
        # stopped in its plan part wrote nothing and may have saved no plan (FR-017, research
        # R14). The store checks them again on the run's locked row.
        not_resumable = _not_resumable_before_plan(run)
        if not_resumable is not None:
            self._audit(
                run_id,
                actor=principal.actor,
                operation=operation,
                reason=request.reason,
                outcome="refused-run-not-resumable",
            )
            raise self._error(409, "run-not-resumable", not_resumable, run_id=run_id)
        if approval_required():
            self._require_matching_approval(run, request, principal, operation=operation)
        self._require_supported_run_configuration(run)
        try:
            plan = self._plan(run_id)
        except ServiceAPIError:
            # FR-014: every resume request is recorded, including one whose plan cannot be read.
            self._audit(
                run_id,
                actor=principal.actor,
                operation=operation,
                reason=request.reason,
                outcome="refused-plan-unavailable",
            )
            raise
        if not plan.checksum_ok or plan.checksum != request.expected_checksum:
            self._audit(
                run_id, actor=principal.actor, operation=operation, reason=request.reason, outcome="refused-checksum"
            )
            raise self._error(
                409,
                "checksum-conflict",
                f"expected_checksum {request.expected_checksum} does not match the saved plan's checksum "
                f"{plan.checksum}",
                run_id=run_id,
            )
        left_out = plan.summary.left_out
        if left_out > 0 and not request.confirm_left_out:
            self._audit(
                run_id,
                actor=principal.actor,
                operation=operation,
                reason=request.reason,
                outcome="refused-left-out-not-confirmed",
            )
            raise ServiceAPIError(
                409,
                "left-out-confirmation-required",
                f"this plan leaves out {left_out} records; confirm with confirm_left_out=true",
                run_id=run_id,
                secrets=self._secrets,
                left_out=left_out,
            )
        requested = self._new_receipt(
            actor=principal.actor,
            idempotency_key=idempotency_key,
            operation=operation,
            target_run_id=run_id,
            run_id=run_id,
            body=body,
            reason=request.reason,
            now=datetime.now(timezone.utc),
        )
        reserved, created = self._projection.reserve_resume(run_id, requested, secrets=self._secrets)
        self._require_matching_receipt(reserved, requested)
        refusal = _refusal_code(reserved) if reserved.state == "accepted" else None
        if created and refusal is not None:
            # The store answered the request with its refusal; the event records which one.
            self._audit(
                run_id, actor=principal.actor, operation=operation, reason=request.reason, outcome=f"refused-{refusal}"
            )
            return self._stored_response(reserved)
        return await self._resume_or_replay(reserved, parameters, principal, request.reason)

    def _refuse_cancelled(self, run: ProductRun, principal: Principal, operation: str, reason: str | None) -> None:
        """Refuse to approve or apply a run in phase ``cancelled``, before any receipt is reserved.

        Raises:
            ServiceAPIError: ``409 run-cancelled``.
        """
        if run.phase != "cancelled":
            return
        self._audit(run.run_id, actor=principal.actor, operation=operation, reason=reason, outcome="refused-cancelled")
        raise self._error(409, "run-cancelled", RUN_CANCELLED_MESSAGE, run_id=run.run_id)

    def _require_matching_approval(
        self,
        run: ProductRun,
        request: ApplyRunRequest | ResumeRunRequest,
        principal: Principal,
        *,
        operation: str = "apply",
    ) -> None:
        """Refuse an ``apply`` or a ``resume`` whose run has no approval, or an approval of another checksum.

        Called only when ``INFRAHUB_SYNC_REQUIRE_APPROVAL`` is on, before the receipt is reserved,
        so a refusal submits nothing.

        Raises:
            ServiceAPIError: ``409 approval-required`` or ``409 approval-checksum-mismatch``.
        """
        approval = run.approval
        if approval is not None and approval.plan_checksum == request.expected_checksum:
            return
        self._audit(
            run.run_id,
            actor=principal.actor,
            operation=operation,
            reason=request.reason,
            outcome="refused-approval-required",
        )
        if approval is None:
            raise self._error(
                409,
                "approval-required",
                f"this Sync API requires an approved plan before {operation}; approve the plan with "
                f"`infrahub-sync approve {run.run_id} --expected-checksum {request.expected_checksum}`",
                run_id=run.run_id,
            )
        raise self._error(
            409,
            "approval-checksum-mismatch",
            f"expected_checksum {request.expected_checksum} does not match the approved checksum "
            f"{approval.plan_checksum}",
            run_id=run.run_id,
        )

    async def approve_run(
        self, run_id: str, request: ApproveRunRequest, principal: Principal, idempotency_key: str
    ) -> tuple[int, dict[str, Any]]:
        """Record the one approval of a run's saved plan, bound to the reviewed checksum.

        The service answers in the request: no flow run is submitted. The checks run in the
        order of the HTTP contract, and the approval is stored in the same store transaction
        that completes the request's receipt, so a retry with the same key replays the body.
        """
        run = self._owned_run(run_id, principal, "approve", request.reason)
        body = request.model_dump(mode="json")
        existing = self._lookup_existing_receipt(
            run,
            principal,
            idempotency_key,
            operation="approve",
            reason=request.reason,
            body=body,
        )
        if existing is not None and existing.state == "accepted":
            self._audit(run_id, actor=principal.actor, operation="approve", reason=request.reason, outcome="replayed")
            return self._stored_response(existing)
        self._refuse_cancelled(run, principal, "approve", request.reason)
        self._require_approvable(run, principal, request.reason)
        try:
            plan = self._plan(run_id)
        except ServiceAPIError:
            # FR-008: a refused approval is audited too, including a plan that cannot be read.
            self._audit(
                run_id,
                actor=principal.actor,
                operation="approve",
                reason=request.reason,
                outcome="refused-plan-unavailable",
            )
            raise
        self._require_approvable_plan(run, plan, request, principal)
        receipt = existing or self._reserve_existing(
            run,
            principal,
            idempotency_key,
            operation="approve",
            reason=request.reason,
            body=body,
        )
        if receipt.state == "accepted":
            self._audit(run_id, actor=principal.actor, operation="approve", reason=request.reason, outcome="replayed")
            return self._stored_response(receipt)
        # One clock reading stamps the approval and its accepted event.
        approved_at = self._clock()
        approval = RunApproval(
            approved_by=principal.actor,
            approved_at=approved_at,
            plan_checksum=plan.checksum,
            reason=request.reason,
        )
        resource = self._resource_with_observations(
            self._redacted_links(run.model_copy(update={"approval": approval})), {}
        )
        by_action = plan.summary.by_action
        response = ApprovedRunResource(
            run=resource.run,
            orchestration=resource.orchestration,
            plan_summary=ApprovedPlanSummaryResource(
                creates=by_action.get("create", 0),
                updates=by_action.get("update", 0),
                deletes_recorded=plan.summary.deletes_not_executed,
            ),
        ).model_dump(mode="json")
        try:
            # The accepted event is written in the approval's transaction (spec 017, FR-014).
            accepted = self._event(
                run_id,
                actor=principal.actor,
                operation="approve",
                reason=request.reason,
                outcome="accepted",
                run=run,
                created_at=approved_at,
            )
            stored = self._projection.record_approval(
                run_id, approval, receipt, response, events=(accepted,), secrets=self._secrets
            )
        except ValueError as refusal:
            return self._replay_answered(receipt, principal, request.reason, refusal)
        completed = self._projection.lookup_mutation(receipt.actor, receipt.key_digest).value
        assert completed is not None  # record_approval completed this receipt or raised.
        status, content = self._stored_response(completed)
        if stored:
            return status, content
        if content["error"]["code"] == "already-approved":
            outcome = "refused-already-approved"
        elif content["error"]["code"] == "run-cancelled":
            outcome = "refused-cancelled"
        else:
            outcome = "refused-not-approvable"
        self._audit(run_id, actor=principal.actor, operation="approve", reason=request.reason, outcome=outcome)
        return status, content

    def _require_approvable(self, run: ProductRun, principal: Principal, reason: str | None) -> None:
        """Refuse a run that is still running a stage, or that is neither planned nor resumable.

        A resumable run is one whose write stopped part-way (spec 022): approving it lets a site
        that requires approval resume it, for example a `sync` run, which is never approved
        before its write (FR-019). Checked before the plan lookup, so a run still planning is
        not reported as a run whose plan is missing.
        """
        active = next((link for link in reversed(run.prefect_executions) if link.terminal_at is None), None)
        if active is None and (run.phase == "planned" or run_is_resumable(run)):
            return
        self._audit(
            run.run_id, actor=principal.actor, operation="approve", reason=reason, outcome="refused-not-approvable"
        )
        raise self._error(
            409,
            "run-not-approvable",
            not_approvable_message(run.phase, None if active is None else active.purpose),
            run_id=run.run_id,
        )

    def _require_approvable_plan(
        self, run: ProductRun, plan: PlanResource, request: ApproveRunRequest, principal: Principal
    ) -> None:
        """Refuse an unverified plan, a checksum other than the plan's, and a second approval."""
        if not plan.checksum_ok:
            self._audit(
                run.run_id,
                actor=principal.actor,
                operation="approve",
                reason=request.reason,
                outcome="refused-plan-unverified",
            )
            raise self._error(
                409,
                "plan-checksum-unverified",
                "the saved plan's checksum was not verified when it was published; create and review a new plan",
                run_id=run.run_id,
            )
        if request.expected_checksum != plan.checksum:
            self._audit(
                run.run_id,
                actor=principal.actor,
                operation="approve",
                reason=request.reason,
                outcome="refused-checksum",
            )
            raise self._error(
                409,
                "checksum-mismatch",
                f"expected_checksum {request.expected_checksum} does not match the saved plan's checksum "
                f"{plan.checksum}",
                run_id=run.run_id,
            )
        if run.approval is not None:
            self._audit(
                run.run_id,
                actor=principal.actor,
                operation="approve",
                reason=request.reason,
                outcome="refused-already-approved",
            )
            error = already_approved_body(run.approval, run_id=run.run_id, mutation_id=None)["error"]
            raise self._error(409, error["code"], error["message"], run_id=run.run_id, details=error["details"])

    async def cancel_run(  # noqa: PLR0911  # pylint: disable=too-many-branches,too-many-return-statements,too-many-statements
        self, run_id: str, request: CancelRunRequest, principal: Principal, idempotency_key: str
    ) -> tuple[int, dict[str, Any]]:
        """Cancel the latest active service execution, or withdraw a finished plan.

        A run with an active execution has that execution cancelled through Prefect (``202``). A
        run in phase ``planned`` with no active execution is withdrawn: its phase becomes
        ``cancelled`` in one store transaction and nothing is sent to Prefect (``200``). A run
        already cancelled, or in any other phase with nothing running, is refused (``409``).
        """
        run = self._owned_run(run_id, principal, "cancel", request.reason)
        body = request.model_dump(mode="json")
        receipt = self._lookup_existing_receipt(
            run,
            principal,
            idempotency_key,
            operation="cancel",
            reason=request.reason,
            body=body,
        )
        if receipt is not None and receipt.state == "accepted":
            self._audit(run_id, actor=principal.actor, operation="cancel", reason=request.reason, outcome="replayed")
            return self._stored_response(receipt)
        if not run.prefect_executions:
            # A run with no execution record follows the same phase rules as one whose
            # executions all ended: approve accepts such a planned run, so cancel withdraws it.
            if run.phase == "planned":
                return self._withdraw_planned_run(run, request, principal, idempotency_key, receipt)
            if run.phase == "cancelled":
                self._refuse_already_cancelled(run_id, principal, request.reason)
            self._audit(
                run_id,
                actor=principal.actor,
                operation="cancel",
                reason=request.reason,
                outcome="refused-no-execution",
            )
            raise self._error(409, "no-active-execution", "the run has no service execution to cancel", run_id=run_id)
        try:
            link = await select_cancellable_execution(
                run,
                None if receipt is None else receipt.receipt_id,
                self._orchestration,
            )
        except CancellationSelectionUnavailableError:
            self._raise_cancel_unavailable(
                receipt, principal, request.reason, "the active service execution cannot be confirmed", run_id=run_id
            )
        except Exception as exc:  # pylint: disable=broad-exception-caught  # noqa: BLE001
            self._raise_cancel_unavailable(
                receipt,
                principal,
                request.reason,
                "the active service execution cannot be confirmed",
                run_id=run_id,
                exc=exc,
            )
        if link is None:
            if run.phase == "planned":
                return self._withdraw_planned_run(run, request, principal, idempotency_key, receipt)
            if run.phase == "cancelled":
                self._refuse_already_cancelled(run_id, principal, request.reason)
            self._audit(
                run_id, actor=principal.actor, operation="cancel", reason=request.reason, outcome="refused-terminal"
            )
            raise self._error(409, "execution-terminal", "the service execution is already terminal", run_id=run_id)
        if receipt is None:
            receipt = self._reserve_existing(
                run,
                principal,
                idempotency_key,
                operation="cancel",
                reason=request.reason,
                body=body,
            )
            if receipt.state == "accepted":
                self._audit(
                    run_id,
                    actor=principal.actor,
                    operation="cancel",
                    reason=request.reason,
                    outcome="replayed",
                )
                return self._stored_response(receipt)
        decision_at = self._clock()
        if link.cancellation_recovery_deadline_at is not None and decision_at >= link.cancellation_recovery_deadline_at:
            self._projection.expire_execution_cancellation(run_id, link.flow_run_id, terminal_at=decision_at)
            expired = self._projection.lookup_mutation(receipt.actor, receipt.key_digest).value
            if expired is not None and expired.state == "accepted":
                self._audit(
                    run_id,
                    actor=principal.actor,
                    operation="cancel",
                    reason=request.reason,
                    outcome="replayed",
                )
                return self._stored_response(expired)
            raise self._error(
                409,
                "execution-terminal",
                "the service execution is already terminal",
                run_id=run_id,
                mutation_id=receipt.receipt_id,
            )
        if receipt.state == "reserved" and not self._projection.claim_mutation(
            receipt.receipt_id, secrets=self._secrets
        ):
            completed = self._projection.lookup_mutation(receipt.actor, receipt.key_digest).value
            if completed is not None and completed.state == "accepted":
                self._audit(
                    run_id,
                    actor=principal.actor,
                    operation="cancel",
                    reason=request.reason,
                    outcome="replayed",
                )
                return self._stored_response(completed)
            self._raise_cancel_unavailable(
                receipt,
                principal,
                request.reason,
                "the cancellation request is already being processed",
            )
        requested_at = link.cancellation_requested_at or self._clock()
        recovery_deadline_at = link.cancellation_recovery_deadline_at or (
            requested_at + timedelta(seconds=self._cancellation_recovery_seconds)
        )
        requested = self._projection.request_execution_cancellation(
            run_id,
            link.flow_run_id,
            requested_at=requested_at,
            recovery_deadline_at=recovery_deadline_at,
            recovery_seconds=self._cancellation_recovery_seconds,
            expected_latest_position=len(run.prefect_executions) - 1,
            receipt_id=receipt.receipt_id,
            secrets=self._secrets,
        )
        if not requested:
            completed = self._projection.complete_mutation(
                receipt.receipt_id,
                response_status=409,
                response_body=self._error_body(
                    409,
                    "execution-terminal",
                    "the service execution is already terminal",
                    run_id=run_id,
                    mutation_id=receipt.receipt_id,
                ),
                flow_run_id=link.flow_run_id,
                event=self._event(
                    run_id,
                    actor=principal.actor,
                    operation="cancel",
                    reason=request.reason,
                    outcome="refused-terminal",
                    run=run,
                ),
                secrets=self._secrets,
            )
            return self._stored_response(completed)
        try:
            cancelled = await self._orchestration.cancel(link.flow_run_id)
        except Exception as exc:  # pylint: disable=broad-exception-caught  # noqa: BLE001
            self._raise_cancel_unavailable(
                receipt,
                principal,
                request.reason,
                "the orchestrator could not confirm the cancellation request",
                exc=exc,
            )
        if not cancelled.acknowledged:
            self._raise_cancel_unavailable(
                receipt,
                principal,
                request.reason,
                "the orchestrator could not confirm the cancellation request",
            )
        accepted_observation = Observation(available=True, state="cancelling")
        resource = self._resource_with_observations(
            self._required_run(run_id),
            {link.flow_run_id: accepted_observation},
        )
        body = resource.model_dump(mode="json")
        # A late acknowledgement, or one for a claimed write, is stored as a 409 or 503 refusal;
        # its event is saved in that refusal's transaction (FR-014).
        refusal_events = {
            status: self._event(
                run_id, actor=principal.actor, operation="cancel", reason=request.reason, outcome=outcome, run=run
            )
            for status, outcome in ((409, "refused-terminal"), (503, "unavailable"))
        }
        acknowledged = self._projection.acknowledge_execution_cancellation(
            run_id,
            link.flow_run_id,
            acknowledged_at=self._clock(),
            response_status=202,
            response_body=body,
            event=self._event(
                run_id, actor=principal.actor, operation="cancel", reason=request.reason, outcome="accepted", run=run
            ),
            refusal_events=refusal_events,
            secrets=self._secrets,
        )
        completed = self._projection.lookup_mutation(receipt.actor, receipt.key_digest).value
        if completed is None:
            raise self._error(
                409,
                "execution-terminal",
                "the service execution is already terminal",
                run_id=run_id,
                mutation_id=receipt.receipt_id,
            )
        if not acknowledged:
            if completed.state != "accepted":
                raise self._error(
                    409,
                    "execution-terminal",
                    "the service execution is already terminal",
                    run_id=run_id,
                    mutation_id=receipt.receipt_id,
                )
            stored_ids = {event.event_id for event in self._projection.audit_events(run_id)}
            if not any(event.event_id in stored_ids for event in refusal_events.values()):
                # Another writer (the worker's result or the recovery deadline) answered the
                # request first, so its refusal has no event yet.
                self._audit(
                    run_id,
                    actor=principal.actor,
                    operation="cancel",
                    reason=request.reason,
                    outcome="refused-terminal" if completed.response_status == 409 else "unavailable",
                )
        # An acknowledged cancellation wrote its accepted event with the 202 it stored.
        return self._stored_response(completed)

    def _refuse_already_cancelled(self, run_id: str, principal: Principal, reason: str | None) -> NoReturn:
        """Audit and refuse a cancel request for a run that is already cancelled."""
        self._audit(run_id, actor=principal.actor, operation="cancel", reason=reason, outcome="refused-cancelled")
        raise self._error(409, "run-already-cancelled", RUN_ALREADY_CANCELLED_MESSAGE, run_id=run_id)

    def _withdraw_planned_run(
        self,
        run: ProductRun,
        request: CancelRunRequest,
        principal: Principal,
        idempotency_key: str,
        receipt: MutationReceipt | None,
    ) -> tuple[int, dict[str, Any]]:
        """Cancel a run whose plan finished and that has nothing running: the plan is withdrawn.

        Nothing is sent to Prefect. The store re-checks the run under its write-first row lock
        and completes the ``cancel`` receipt in the same transaction, so a retry with the same
        key replays the stored response and a concurrent apply or approval cannot also succeed.
        """
        if receipt is None:
            receipt = self._reserve_existing(
                run,
                principal,
                idempotency_key,
                operation="cancel",
                reason=request.reason,
                body=request.model_dump(mode="json"),
            )
            if receipt.state == "accepted":
                self._audit(
                    run.run_id, actor=principal.actor, operation="cancel", reason=request.reason, outcome="replayed"
                )
                return self._stored_response(receipt)
        # The request's accepted event and the run's `run.cancelled` end event are written in
        # the withdrawal's transaction (spec 017, FR-014). No Prefect acknowledgement follows a
        # withdrawal, so the Sync API records the end event itself. The run ends after the
        # decision is taken, so the event list shows the request before the run's end.
        accepted = self._event(
            run.run_id,
            actor=principal.actor,
            operation="cancel",
            reason=request.reason,
            outcome="accepted-withdrawn",
            run=run,
        )
        cancelled_at = self._clock()
        withdrawn = run.model_copy(update={"phase": "cancelled", "outcome": "cancelled", "finished_at": cancelled_at})
        response = self._resource_with_observations(self._redacted_links(withdrawn), {}).model_dump(mode="json")
        events = (
            accepted,
            run_event(
                run.run_id,
                run,
                event_type=RUN_CANCELLED,
                actor=principal.actor,
                operation="cancel",
                reason=request.reason,
                outcome="cancelled",
                created_at=cancelled_at,
            ),
        )
        try:
            stored = self._projection.cancel_planned_run(
                run.run_id, receipt, response, cancelled_at=cancelled_at, events=events, secrets=self._secrets
            )
        except ValueError as refusal:
            return self._replay_answered(receipt, principal, request.reason, refusal)
        completed = self._projection.lookup_mutation(receipt.actor, receipt.key_digest).value
        assert completed is not None  # cancel_planned_run completed this receipt or raised.
        status, content = self._stored_response(completed)
        if stored:
            return status, content
        outcome = {
            "run-already-cancelled": "refused-cancelled",
            "execution-terminal": "refused-terminal",
        }.get(content["error"]["code"], "refused-run-execution-conflict")
        self._audit(run.run_id, actor=principal.actor, operation="cancel", reason=request.reason, outcome=outcome)
        return status, content

    def _replay_answered(
        self, receipt: MutationReceipt, principal: Principal, reason: str | None, refusal: ValueError
    ) -> tuple[int, dict[str, Any]]:
        """Replay a receipt that another request with the same key answered first.

        The store refuses to complete a receipt that is no longer awaiting completion. For an
        approval or a withdrawal, which the service answers in the request, that happens when a
        concurrent retry with the same key completed it first: this request returns that answer
        instead of failing.

        Raises:
            ValueError: ``refusal``, when the receipt is still not answered: the store refused
                for another reason.
        """
        completed = self._projection.lookup_mutation(receipt.actor, receipt.key_digest).value
        if completed is None or completed.state != "accepted":
            raise refusal
        self._audit(
            receipt.run_id, actor=principal.actor, operation=receipt.operation, reason=reason, outcome="replayed"
        )
        return self._stored_response(completed)

    def _raise_cancel_unavailable(
        self,
        receipt: MutationReceipt | None,
        principal: Principal,
        reason: str | None,
        message: str,
        *,
        run_id: str | None = None,
        exc: Exception | None = None,
    ) -> NoReturn:
        target_run_id = receipt.run_id if receipt is not None else run_id
        mutation_id = receipt.receipt_id if receipt is not None else None
        self._audit(target_run_id, actor=principal.actor, operation="cancel", reason=reason, outcome="unavailable")
        error = self._error(
            503,
            "orchestration-unavailable",
            message,
            run_id=target_run_id,
            mutation_id=mutation_id,
        )
        if exc is None:
            raise error
        error.__cause__ = sanitize_exception_chain(exc, self._secrets)
        error.__suppress_context__ = True
        raise error from error.__cause__

    async def get_run(self, run_id: str, observations: dict[str, Observation] | None = None) -> RunResource:
        """Return retained product state even when Prefect detail expired."""
        collected = dict(observations or {})
        run = self._required_run(run_id)
        wrote = False
        for link in run.prefect_executions:
            # Already observed by this request, or terminal and therefore immutable: no observation needed.
            if link.flow_run_id in collected or link.terminal_at is not None:
                continue
            observed = await self._orchestration.observe(link.flow_run_id)
            collected[link.flow_run_id] = observed
            if observed.available:
                self._projection.observe_prefect_execution(
                    run_id, link.flow_run_id, state=observed.state, secrets=self._secrets
                )
                wrote = True
        rendered = self._required_run(run_id) if wrote else run
        return self._resource_with_observations(self._redacted_links(rendered), collected)

    async def status(self, work_pool_name: str) -> ServiceStatusResource:
        """Return lifecycle-safe pool state without exposing provider identifiers."""
        snapshot = await self._orchestration.pool_status(work_pool_name, datetime.now(timezone.utc))
        return _service_status(snapshot)

    def list_runs(
        self,
        *,
        config_id: str | None = None,
        registry_version: int | None = None,
        cursor: str | None = None,
        limit: int | None = None,
    ) -> RunPage:
        """Return one page of runs, newest first, optionally for one configuration or version.

        Raises:
            ServiceAPIError: ``422 request-invalid`` for a version without a configuration or a
                limit outside 1-200; ``404 configuration-not-found`` or
                ``404 configuration-version-not-found`` for an unknown filter value.
            InvalidCursorError: ``cursor`` does not decode or was issued for other filters.
        """
        if registry_version is not None and config_id is None:
            raise self._error(422, "request-invalid", "registry_version requires config_id")
        page_size = page_size_or_refuse(limit, secrets=self._secrets)
        if config_id is not None:
            self._require_configuration_filter(config_id, registry_version)
        filters: dict[str, FilterValue] = {
            "list": "runs",
            "config_id": config_id,
            "registry_version": registry_version,
        }
        after = None if cursor is None else timestamp_key(decode_cursor(cursor, filters))
        page = self._projection.list_runs(
            RunFilters(config_id=config_id, registry_version=registry_version), after=after, limit=page_size
        )
        last = page.items[-1] if page.has_more else None
        next_cursor = None if last is None else timestamp_cursor(last.started_at, last.run_id, filters)
        return public_run_page(page.items, next_cursor)

    def list_events(  # pylint: disable=too-many-arguments
        self,
        *,
        run_id: str | None = None,
        config_id: str | None = None,
        event_types: tuple[str, ...] = (),
        related: tuple[str, ...] = (),
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> EventPage:
        """Return one page of typed events, oldest first (spec 017, contracts/events-route.md).

        ``related`` holds ``<kind>:<id>`` values; an event must name every one of them. An
        unknown run or configuration matches no event rather than being refused.

        Raises:
            ServiceAPIError: ``422 request-invalid`` for a value outside the contract: a limit
                outside 1-200, a negative offset, a malformed identifier, event type or related
                resource, or a time without a timezone.
        """
        page_size = page_size_or_refuse(limit, secrets=self._secrets)
        if offset < 0:
            raise self._error(422, "request-invalid", _SCHEMA_MISMATCH_MESSAGE)
        try:
            filters = EventFilters(
                run_id=run_id,
                config_id=config_id,
                event_types=event_types,
                related=tuple(_related_resource(value) for value in related),
                since=since,
                until=until,
            )
        except ValueError:
            # The message names no value: the query string is the caller's, not the server's.
            raise self._error(422, "request-invalid", _SCHEMA_MISMATCH_MESSAGE) from None
        page = self._projection.list_events(filters, limit=page_size, offset=offset)
        return public_event_page(page.items, limit=page_size, offset=offset, has_more=page.has_more)

    async def read_run_logs(
        self, run_id: str, *, stage: str | None = None, limit: int = DEFAULT_LOG_LIMIT, offset: int = 0
    ) -> RunLogPage:
        """Return one page of the lines Sync's own code wrote during a run, oldest first.

        The lines of every flow run of the run, or of ``stage``'s flow runs, are read, the
        orchestration engine's lines are left out, and ``offset`` counts Sync lines only, so a
        poller that passes the number of lines it already holds receives only newer ones
        (spec 017, FR-004, FR-005). ``stage``, ``limit`` and ``offset`` are already bounded by
        the route.

        Raises:
            ServiceAPIError: ``404 run-not-found``; ``410 log-expired`` when the orchestrator
                no longer holds a flow run the record names (FR-010); ``503 log-unavailable``
                when the orchestrator cannot be reached (FR-013).
        """
        run = self._required_run(run_id)
        entries = tuple(link for link in run.prefect_executions if stage is None or link.purpose == stage)
        lines: tuple[RunLogLine, ...] = ()
        if entries:
            try:
                read = await self._orchestration.read_logs(tuple(link.flow_run_id for link in entries))
            except OrchestrationUnavailableError as exc:
                # The class name tells a timeout from a refusal; the address and provider stay out.
                logger.warning(
                    "the log of run %s could not be read from the orchestrator (%s)",
                    run_id,
                    exc.error_class or type(exc).__name__,
                )
                raise self._error(503, "log-unavailable", LOG_UNAVAILABLE_MESSAGE, run_id=run_id) from None
            if read.expired:
                raise self._error(410, "log-expired", LOG_EXPIRED_MESSAGE, run_id=run_id)
            # Redacted before the cap, inside `sync_lines`.
            lines = sync_lines(entries, read.logs, secrets=self._secrets)
        items, has_more = log_page(lines, offset, limit)
        return public_run_log_page(run_id, items, limit=limit, offset=offset, has_more=has_more, run_phase=run.phase)

    def _require_configuration_filter(self, config_id: str, registry_version: int | None) -> None:
        """Refuse a run-list filter naming a configuration or version that was never registered."""
        if not self._projection.lookup_configuration(config_id).available:
            raise self._error(404, CONFIGURATION_NOT_FOUND_REASON, "the requested configuration does not exist")
        if registry_version is not None and not self._projection.configuration_version_exists(
            config_id, registry_version
        ):
            raise self._error(
                404, CONFIGURATION_VERSION_NOT_FOUND_REASON, "the requested configuration version does not exist"
            )

    def get_plan(self, run_id: str) -> PlanResource:
        """Return the retained review document."""
        self._required_run(run_id)
        return self._plan(run_id)

    def get_plan_report(  # pylint: disable=too-many-arguments  # the route's filters, all keyword-only
        self,
        run_id: str,
        *,
        entry: ReportEntry,
        offset: int,
        limit: int,
        problem_type: str | None = None,
        kind: str | None = None,
        expected_checksum: str | None = None,
    ) -> PlanReportPageResource:
        """Return one page of the problems, left-out records or kept records of the retained plan (spec 020).

        The entries are the lines of the plan's `report.jsonl` whose `entry` is `entry`,
        without that key, in the report's order, filtered by `problem_type` and `kind`. A
        plan published without a report (format 2 or 3) has no entry.
        """
        self._required_run(run_id)
        document = self._plan_document(run_id)
        checksum = str(document.get("checksum") or "")
        if expected_checksum is not None and expected_checksum != checksum:
            raise self._error(409, "plan-changed", "the retained plan has another checksum", run_id=run_id)
        lines = document.get(PLAN_REPORT_KEY) or []
        if not isinstance(lines, list):
            raise self._error(503, "plan-unavailable", "the retained plan is invalid", run_id=run_id)
        selected = [
            {key: value for key, value in line.items() if key != "entry"}
            for line in lines
            if isinstance(line, dict)
            and line.get("entry") == entry
            and (problem_type is None or line.get("problem_type") == problem_type)
            and (kind is None or (line.get("record") or {}).get("kind") == kind)
        ]
        end = offset + limit
        return PlanReportPageResource(
            run_id=run_id,
            plan_checksum=checksum,
            entries=tuple(selected[offset:end]),
            offset=offset,
            limit=limit,
            total=len(selected),
            next_offset=end if end < len(selected) else None,
        )

    def get_results(self, run_id: str) -> ResultsResource:
        """Return retained product results without consulting Prefect."""
        run = self._required_run(run_id)
        return ResultsResource(run_id=run_id, results=run.results)

    def list_artifacts(self, run_id: str) -> ArtifactListResource:
        """List immutable references without reading artifact bodies.

        Built from store records, so it goes through the bounded twin for the same
        reason the run resource does: a durable field this contract does not declare
        must not reach an operator by being copied through.
        """
        run = self._required_run(run_id)
        artifacts = tuple(reference.model_dump(mode="json") for reference in run.artifact_refs)
        return EmittedArtifactListResource.model_validate({"run_id": run_id, "artifacts": artifacts})

    def get_artifact(self, run_id: str, artifact_id: str) -> tuple[bytes, str, str]:
        """Return verified artifact bytes, media type, and recorded digest."""
        run = self._required_run(run_id)
        reference = next((item for item in run.artifact_refs if item.artifact_id == artifact_id), None)
        if reference is None:
            raise self._error(404, "artifact-not-found", "the requested artifact is not retained", run_id=run_id)
        result = self._projection.lookup_artifact(run_id, artifact_id)
        if result.value is not None:
            return result.value, reference.media_type, reference.digest
        if result.reason == "artifact-expired":
            raise self._error(410, "artifact-expired", "the requested artifact has expired", run_id=run_id)
        raise self._error(503, "artifact-unavailable", "the retained artifact cannot be retrieved", run_id=run_id)

    async def _submit(
        self,
        receipt: MutationReceipt,
        parameters: dict[str, object],
        principal: Principal,
        reason: str | None,
    ) -> tuple[int, dict[str, Any]]:
        assert receipt.resource_kind == "run"
        assert receipt.run_id is not None
        assert receipt.prefect_key is not None
        try:
            submission = await self._orchestration.submit(parameters, idempotency_key=receipt.prefect_key)
        except Exception as exc:  # noqa: BLE001 - remote boundary is translated and cause-sanitized
            self._audit(
                receipt.run_id,
                actor=principal.actor,
                operation=receipt.operation,
                reason=reason,
                outcome="unavailable",
            )
            error = self._error(
                503,
                "orchestration-unavailable",
                f"the orchestrator could not confirm service submission ({type(exc).__name__})",
                run_id=receipt.run_id,
                mutation_id=receipt.receipt_id,
            )
            error.__cause__ = sanitize_exception_chain(exc, self._secrets)
            error.__suppress_context__ = True
            raise error from error.__cause__
        link = PrefectExecutionLink(
            flow_run_id=submission.flow_run_id,
            purpose=receipt.operation,
            attempt=1,
            last_observed_state=submission.state,
            last_observed_at=datetime.now(timezone.utc),
        )
        try:
            self._projection.add_prefect_execution(
                receipt.run_id,
                link,
                receipt_id=receipt.receipt_id,
                allocate_attempt=True,
                secrets=self._secrets,
            )
        except DuplicatePrefectExecutionError:
            self._projection.observe_prefect_execution(
                receipt.run_id,
                submission.flow_run_id,
                state=submission.state,
                secrets=self._secrets,
            )
        resource = self._resource_with_observations(
            self._required_run(receipt.run_id),
            {submission.flow_run_id: Observation(available=True, state=submission.state)},
            # Not yet completed, so the store cannot find this receipt by its flow run.
            pending=(submission.flow_run_id, receipt),
        )
        body = resource.model_dump(mode="json")
        completed = self._projection.complete_mutation(
            receipt.receipt_id,
            response_status=202,
            response_body=body,
            flow_run_id=submission.flow_run_id,
            event=self._event(
                receipt.run_id,
                actor=principal.actor,
                operation=receipt.operation,
                reason=reason,
                outcome="accepted",
            ),
            secrets=self._secrets,
        )
        return self._stored_response(completed)

    def _reserve_existing(
        self,
        run: ProductRun,
        principal: Principal,
        idempotency_key: str,
        *,
        operation: str,
        reason: str | None,
        body: dict[str, Any],
        admit_write: bool = False,
    ) -> MutationReceipt:
        receipt = self._new_receipt(
            actor=principal.actor,
            idempotency_key=idempotency_key,
            operation=operation,
            target_run_id=run.run_id,
            run_id=run.run_id,
            body=body,
            reason=reason,
            now=datetime.now(timezone.utc),
        )
        reserved, _ = self._projection.reserve_mutation(
            receipt,
            admit_write=admit_write,
            secrets=self._secrets,
        )
        self._require_matching_receipt(reserved, receipt)
        return reserved

    def _lookup_existing_receipt(
        self,
        run: ProductRun,
        principal: Principal,
        idempotency_key: str,
        *,
        operation: str,
        reason: str | None,
        body: dict[str, Any],
    ) -> MutationReceipt | None:
        requested = self._new_receipt(
            actor=principal.actor,
            idempotency_key=idempotency_key,
            operation=operation,
            target_run_id=run.run_id,
            run_id=run.run_id,
            body=body,
            reason=reason,
            now=datetime.now(timezone.utc),
        )
        existing = self._projection.lookup_mutation(requested.actor, requested.key_digest).value
        if existing is None:
            return None
        self._require_matching_receipt(existing, requested)
        return existing

    def _require_supported_run_configuration(self, run: ProductRun) -> None:
        """Validate the immutable binding before submitting another execution."""
        binding = run.configuration_binding
        if binding is not None:
            self._registered_configuration(binding[0], binding[1], expected_checksum=binding[2])

    async def _resume_or_replay(
        self,
        receipt: MutationReceipt,
        parameters: dict[str, object],
        principal: Principal,
        reason: str | None,
    ) -> tuple[int, dict[str, Any]]:
        """Replay completed receipts and revalidate configurations before submission."""
        if receipt.state == "accepted":
            refusal = _refusal_code(receipt) if receipt.operation == "resume" else None
            self._audit(
                receipt.run_id,
                actor=principal.actor,
                operation=receipt.operation,
                reason=reason,
                # A resume's stored refusal names its own code; see `reserve_resume`.
                outcome=_reservation_outcome(receipt) if refusal is None else f"refused-{refusal}",
            )
            return self._stored_response(receipt)
        assert receipt.run_id is not None
        self._require_supported_run_configuration(self._required_run(receipt.run_id))
        return await self._submit(receipt, parameters, principal, reason)

    @staticmethod
    def _new_receipt(
        *,
        actor: str,
        idempotency_key: str,
        operation: str,
        target_run_id: str | None,
        run_id: str,
        body: dict[str, Any],
        reason: str | None,
        now: datetime,
    ) -> MutationReceipt:
        key_digest = sha256(idempotency_key.encode()).hexdigest()
        fingerprint = sha256(
            canonical_json_bytes({"operation": operation, "target_run_id": target_run_id, "body": body})
        ).hexdigest()
        receipt_id = f"m-{uuid4().hex}"
        prefect_key = sha256(f"infrahub-sync:{receipt_id}".encode()).hexdigest()
        return MutationReceipt(
            receipt_id=receipt_id,
            actor=actor,
            key_digest=key_digest,
            operation=operation,
            target_run_id=target_run_id,
            request_fingerprint=fingerprint,
            reason=reason,
            resource_id=run_id,
            run_id=run_id,
            prefect_key=prefect_key,
            created_at=now,
            updated_at=now,
        )

    def _require_matching_receipt(self, stored: MutationReceipt, requested: MutationReceipt) -> None:
        binding = (
            "operation",
            "target_run_id",
            "request_fingerprint",
        )
        if any(getattr(stored, field) != getattr(requested, field) for field in binding):
            self._audit(
                stored.run_id,
                actor=requested.actor,
                operation=requested.operation,
                reason=requested.reason,
                outcome="refused-idempotency",
            )
            raise self._error(
                409,
                "idempotency-conflict",
                "Idempotency-Key was already used by this actor for different content",
                run_id=stored.run_id,
                mutation_id=stored.receipt_id,
            )

    def _owned_run(self, run_id: str, principal: Principal, operation: str, reason: str | None) -> ProductRun:
        run = self._required_run(run_id)
        if not principal.administrator and run.actor != principal.actor:
            self._audit(
                run_id,
                actor=principal.actor,
                operation=operation,
                reason=reason,
                outcome="refused-authorization",
            )
            raise self._error(
                403, "forbidden", "only the initiating actor or an administrator may mutate this run", run_id=run_id
            )
        return run

    def _required_run(self, run_id: str) -> ProductRun:
        result = self._projection.lookup_run(run_id)
        if result.value is None:
            raise self._error(404, "run-not-found", "the requested Sync run does not exist", run_id=run_id)
        return result.value

    def _plan_bytes(self, run_id: str) -> bytes:
        result = self._projection.lookup_artifact(run_id, PLAN_ARTIFACT_ID)
        if result.value is None:
            if result.reason == "run-not-found":
                raise self._error(404, "run-not-found", "the requested Sync run does not exist", run_id=run_id)
            if result.reason == "artifact-expired":
                raise self._error(410, "artifact-expired", "the retained plan has expired", run_id=run_id)
            raise self._error(503, "plan-unavailable", "the retained plan cannot be retrieved", run_id=run_id)
        return result.value

    def _plan(self, run_id: str) -> PlanResource:
        # The bounded model ignores the published document's report lines (`PLAN_REPORT_KEY`),
        # which only the paged report routes return.
        try:
            return EmittedPlanResource.model_validate_json(self._plan_bytes(run_id))
        except (ValidationError, ValueError):
            raise self._error(503, "plan-unavailable", "the retained plan is invalid", run_id=run_id) from None

    def _plan_document(self, run_id: str) -> dict[str, Any]:
        """The published review document as a mapping, checked as `_plan` checks it."""
        data = self._plan_bytes(run_id)
        try:
            EmittedPlanResource.model_validate_json(data)
            document = json.loads(data)
        except (ValidationError, ValueError):
            raise self._error(503, "plan-unavailable", "the retained plan is invalid", run_id=run_id) from None
        if not isinstance(document, dict):
            raise self._error(503, "plan-unavailable", "the retained plan is invalid", run_id=run_id)
        return document

    @staticmethod
    def _stage_parameters(
        run: ProductRun,
        stage: str,
        *,
        branch: str | None = None,
        expected_checksum: str | None = None,
        confirm_writes: bool = False,
    ) -> dict[str, object]:
        parameters: dict[str, object] = {
            "run_id": run.run_id,
            "stage": stage,
            "branch": branch,
            "expected_checksum": expected_checksum,
            "confirm_writes": confirm_writes,
        }
        configuration_name = run.summary.get("sync_name")
        if isinstance(configuration_name, str) and configuration_name:
            parameters["configuration_name"] = configuration_name
        binding = run.configuration_binding
        if binding is not None:
            parameters.update(config_id=binding[0], registry_version=binding[1], package_checksum=binding[2])
        return parameters

    @staticmethod
    def _stored_response(receipt: MutationReceipt) -> tuple[int, dict[str, Any]]:
        assert receipt.response_status is not None
        assert receipt.response_body is not None
        return receipt.response_status, receipt.response_body

    @staticmethod
    def _error_body(
        status: int,
        code: str,
        message: str,
        *,
        run_id: str | None = None,
        mutation_id: str | None = None,
    ) -> dict[str, Any]:
        return {
            "error": {
                "code": code,
                "message": message,
                "status": status,
                "run_id": run_id,
                "mutation_id": mutation_id,
            }
        }

    def _redacted_links(self, run: ProductRun) -> ProductRun:
        """Retained observation text is provider-supplied, so redact it at this boundary."""
        links = tuple(
            link
            if link.last_observed_state is None
            else link.model_copy(update={"last_observed_state": redact(link.last_observed_state, self._secrets)})
            for link in run.prefect_executions
        )
        return run.model_copy(update={"prefect_executions": links})

    def _plan_operations(self, run: ProductRun) -> PlanLayout | None:
        """The saved plan's `(operation_id, kind, action)` triples in stored order, or `None` when unreadable.

        Read only for a run with a write execution and a published plan. The triples are read
        from the plan once per run and plan digest, then from an in-process cache, so a client
        that polls the run does not read the whole plan on every request (research R14). A plan
        that expired or cannot be read leaves the progress view without per-kind counts rather
        than failing the read of the run, and is logged with its error class and code only.
        """
        if not any(link.purpose in WRITE_PURPOSES for link in run.prefect_executions):
            return None
        reference = next((ref for ref in run.artifact_refs if ref.artifact_id == PLAN_ARTIFACT_ID), None)
        if reference is None:
            return None
        key = (run.run_id, reference.digest)
        # An expired plan bypasses the cache, with the rule the artifact store applies, so every
        # process stops reporting per-kind counts once it expired; the read below then fails.
        expired = reference.expires_at is not None and reference.expires_at <= datetime.now(timezone.utc)
        cached = None if expired else self._plan_layouts.get(key)
        if cached is not None:
            return cached
        try:
            plan = self._plan(run.run_id)
        except ServiceAPIError as exc:
            # The class and the fixed error code only: the message can name the provider.
            logger.warning(
                "the saved plan of run %s could not be read for its progress view (%s: %s)",
                run.run_id,
                type(exc).__name__,
                exc.code,
            )
            return None
        layout = tuple(
            (operation.operation_id, sys.intern(operation.kind), sys.intern(operation.action))
            for operation in plan.operations
        )
        self._plan_layouts.put(key, layout)
        return layout

    def _resume_receipt(
        self, run: ProductRun, link: PrefectExecutionLink, pending: tuple[str, MutationReceipt] | None
    ) -> MutationReceipt | None:
        """The request that started one resume execution, or `None` when it cannot be read."""
        if pending is not None and pending[0] == link.flow_run_id:
            return pending[1]
        return self._projection.lookup_mutation_for_execution(run.run_id, link.flow_run_id, "resume").value

    def _resource_with_observations(
        self,
        run: ProductRun,
        observations: dict[str, Observation],
        *,
        pending: tuple[str, MutationReceipt] | None = None,
    ) -> RunResource:
        """Build the run resource, with its apply progress view and resume history (spec 022).

        `pending` is the flow run ID and receipt of a request whose execution was just added and
        whose receipt is not completed yet.
        """
        orchestration = []
        for link in run.prefect_executions:
            observed = observations.get(link.flow_run_id)
            # Live and retained observation text is provider-supplied, so no summary is built from it raw.
            retained = None if link.last_observed_state is None else redact(link.last_observed_state, self._secrets)
            # A finished execution reports its verdict as its state: a live or retained observation can
            # predate the verdict, and a completed execution would otherwise read `Running`. The verdict
            # is a fixed enum, returned raw as terminal_state too, so it is not redacted.
            verdict = link.terminal_state
            link_url = None if self._prefect_ui_url is None else flow_run_url(self._prefect_ui_url, link.flow_run_id)
            if observed is None:
                orchestration.append(
                    OrchestrationSummary(
                        flow_run_id=link.flow_run_id,
                        purpose=link.purpose,
                        attempt=link.attempt,
                        state=retained if verdict is None else verdict,
                        detail_available=False,
                        unavailable_reason="live-detail-not-requested",
                        submitted_at=link.submitted_at,
                        claimed_at=link.claimed_at,
                        stalled_at=link.stalled_at,
                        cancellation_requested_at=link.cancellation_requested_at,
                        cancellation_recovery_deadline_at=link.cancellation_recovery_deadline_at,
                        cancellation_acknowledged_at=link.cancellation_acknowledged_at,
                        terminal_at=link.terminal_at,
                        terminal_state=link.terminal_state,
                        terminal_outcome=link.terminal_outcome,
                        prefect_flow_run_url=link_url,
                        reads=stage_reads(link, run_id=run.run_id, prefect_state=link.last_observed_state),
                    )
                )
                continue
            live = None if observed.state is None else redact(observed.state, self._secrets)
            reason = None if observed.reason is None else redact(observed.reason, self._secrets)
            orchestration.append(
                OrchestrationSummary(
                    flow_run_id=link.flow_run_id,
                    purpose=link.purpose,
                    attempt=link.attempt,
                    state=verdict if verdict is not None else (live if observed.available else retained),
                    detail_available=observed.available,
                    unavailable_reason=reason,
                    submitted_at=link.submitted_at,
                    claimed_at=link.claimed_at,
                    stalled_at=link.stalled_at,
                    cancellation_requested_at=link.cancellation_requested_at,
                    cancellation_recovery_deadline_at=link.cancellation_recovery_deadline_at,
                    cancellation_acknowledged_at=link.cancellation_acknowledged_at,
                    terminal_at=link.terminal_at,
                    terminal_state=link.terminal_state,
                    terminal_outcome=link.terminal_outcome,
                    prefect_flow_run_url=link_url,
                    reads=stage_reads(
                        link,
                        run_id=run.run_id,
                        prefect_state=observed.state if observed.available else link.last_observed_state,
                    ),
                )
            )
        operations = self._plan_operations(run)
        return RunResource(
            run=public_run_resource(run),
            orchestration=tuple(orchestration),
            apply_progress=apply_progress_view(run, operations),
            resumes=run_resumes(run, lambda link: self._resume_receipt(run, link, pending), operations),
        )

    def _event(
        self,
        run_id: str | None,
        *,
        actor: str,
        operation: str,
        reason: str | None,
        outcome: str,
        config_id: str | None = None,
        registry_version: int | None = None,
        branch: str | None = None,
        run: ProductRun | None = None,
        created_at: datetime | None = None,
    ) -> AuditEvent:
        """Build the typed event of one request decision.

        The type comes from the outcome. The configuration, version and branch come from the
        stored run when there is one, else from the arguments, for a request refused before
        its run exists. A caller that already holds the run passes it as ``run``; those three
        values never change during a run, so the run is not read again. A caller that stamps
        a stored record with the same decision passes that time as ``created_at``; otherwise
        the event reads the clock.
        """
        if run is None and run_id is not None:
            run = self._projection.lookup_run(run_id).value
        return run_event(
            run_id,
            run,
            event_type=run_decision_type(outcome),
            actor=actor,
            operation=operation,
            reason=reason,
            outcome=outcome,
            config_id=config_id,
            registry_version=registry_version,
            branch=branch,
            created_at=self._clock() if created_at is None else created_at,
        )

    def _audit(
        self,
        run_id: str | None,
        *,
        actor: str,
        operation: str,
        reason: str | None,
        outcome: str,
        config_id: str | None = None,
        registry_version: int | None = None,
        branch: str | None = None,
    ) -> None:
        """Record the event of a decision that saves nothing else, such as a refusal or a replay."""
        event = self._event(
            run_id,
            actor=actor,
            operation=operation,
            reason=reason,
            outcome=outcome,
            config_id=config_id,
            registry_version=registry_version,
            branch=branch,
        )
        self._projection.record_audit(event, secrets=self._secrets)

    def record_authentication_refusal(self, operation: str, reason: str) -> None:
        """Persist anonymous authentication refusal evidence without token material or an event type."""
        self._audit(
            None,
            actor="unauthenticated",
            operation=operation,
            reason=reason or "not-provided",
            outcome="refused-authentication",
        )

    def _require_non_secret_parameters(
        self,
        actor: str,
        operation: str,
        reason: str | None,
        *,
        run_id: str | None = None,
        **parameters: str | None,
    ) -> None:
        for name, value in parameters.items():
            if value is not None and redact(value, self._secrets) != value:
                self._audit(
                    run_id,
                    actor=actor,
                    operation=operation,
                    reason=reason,
                    outcome="refused-secret-parameter",
                )
                raise self._error(
                    422,
                    "secret-parameter-refused",
                    f"{name} must be a non-secret reference",
                    run_id=run_id,
                )

    def _error(
        self,
        status: int,
        code: str,
        message: str,
        *,
        run_id: str | None = None,
        mutation_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> ServiceAPIError:
        return ServiceAPIError(
            status,
            code,
            message,
            run_id=run_id,
            mutation_id=mutation_id,
            details=details,
            secrets=self._secrets,
        )
