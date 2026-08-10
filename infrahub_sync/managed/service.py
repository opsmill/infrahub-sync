"""Run-oriented managed API behavior over durable Sync product records."""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from typing import TYPE_CHECKING, Any, NoReturn
from uuid import uuid4

from pydantic import ValidationError

from infrahub_sync.cache.paths import generate_run_id
from infrahub_sync.execution import collect_secret_values, redact, sanitize_exception_chain
from infrahub_sync.plan.canonical import canonical_json_bytes
from infrahub_sync.product_store import (
    AuditEvent,
    DuplicatePrefectExecutionError,
    MutationReceipt,
    PrefectExecutionLink,
    ProductProjection,
    ProductRun,
    WriteAdmissionConflictError,
)

from .models import (
    ApplyRunRequest,
    ArtifactListResource,
    CancelRunRequest,
    CreateRunRequest,
    OrchestrationSummary,
    PlanResource,
    ResultsResource,
    RunResource,
    VerifyRunRequest,
)
from .orchestration import ManagedOrchestration, Observation

if TYPE_CHECKING:
    from .auth import Principal

PLAN_ARTIFACT_ID = "plan-review"
TERMINAL_STATES = frozenset({"completed", "failed", "crashed", "cancelled"})


class ManagedAPIError(Exception):
    """Stable HTTP classification raised by the service boundary."""

    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        *,
        run_id: str | None = None,
        mutation_id: str | None = None,
        secrets: tuple[str, ...] = (),
    ) -> None:
        self.status = status
        self.code = code
        self.run_id = run_id
        self.mutation_id = mutation_id
        self.message = redact(message, secrets)
        super().__init__(self.message)


class ManagedRunService:
    """Direct application service; Prefect owns all execution mechanics."""

    def __init__(
        self,
        projection: ProductProjection,
        orchestration: ManagedOrchestration,
        *,
        secrets: tuple[str, ...] = (),
    ) -> None:
        self._projection = projection
        self._orchestration = orchestration
        self._secrets = tuple(dict.fromkeys((*collect_secret_values(), *secrets)))

    async def create_run(
        self, request: CreateRunRequest, principal: Principal, idempotency_key: str
    ) -> tuple[int, dict[str, Any]]:
        """Reserve one product identity and accept its first managed execution."""
        self._require_non_secret_parameters(
            principal.actor,
            request.operation,
            request.reason,
            sync_name=request.sync_name,
            configuration_reference=request.configuration_reference,
            branch=request.branch,
        )
        if request.operation == "sync" and not request.confirm_writes:
            self._audit(
                None,
                actor=principal.actor,
                operation="sync",
                reason=request.reason,
                outcome="refused-confirmation",
            )
            raise self._error(409, "confirmation-required", "confirm_writes=true is required for sync")

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
        run = ProductRun(
            run_id=run_id,
            operation=request.operation,
            configuration_reference=request.configuration_reference,
            actor=principal.actor,
            started_at=now,
            phase="accepted",
            summary={"sync_name": request.sync_name},
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
            "sync_name": request.sync_name,
            "stage": request.operation,
            "configuration_reference": request.configuration_reference,
            "branch": request.branch,
            "expected_checksum": None,
            "confirm_writes": request.confirm_writes,
        }
        return await self._submit(reserved, parameters, principal, request.reason)

    async def verify_run(
        self, run_id: str, request: VerifyRunRequest, principal: Principal, idempotency_key: str
    ) -> tuple[int, dict[str, Any]]:
        """Accept an independently auditable, read-only verification."""
        run = self._owned_run(run_id, principal, "verify", request.reason)
        body = request.model_dump(mode="json")
        parameters = self._stage_parameters(run, "verify", confirm_writes=False)
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
        existing = self._lookup_existing_receipt(
            run,
            principal,
            idempotency_key,
            operation="apply",
            reason=request.reason,
            body=body,
        )
        if existing is not None:
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
        try:
            receipt = self._reserve_existing(
                run,
                principal,
                idempotency_key,
                operation="apply",
                reason=request.reason,
                body=body,
                admit_write=True,
            )
        except WriteAdmissionConflictError:
            self._audit(
                run_id,
                actor=principal.actor,
                operation="apply",
                reason=request.reason,
                outcome="refused-apply-admission",
            )
            raise self._error(
                409,
                "apply-already-admitted",
                "a write-capable apply stage is already admitted for this Sync run",
                run_id=run_id,
            ) from None
        return await self._resume_or_replay(receipt, parameters, principal, request.reason)

    async def cancel_run(
        self, run_id: str, request: CancelRunRequest, principal: Principal, idempotency_key: str
    ) -> tuple[int, dict[str, Any]]:
        """Request cancellation of only the latest active managed execution."""
        run = self._owned_run(run_id, principal, "cancel", request.reason)
        receipt = self._reserve_existing(
            run,
            principal,
            idempotency_key,
            operation="cancel",
            reason=request.reason,
            body=request.model_dump(mode="json"),
        )
        if receipt.state == "accepted":
            self._audit(run_id, actor=principal.actor, operation="cancel", reason=request.reason, outcome="replayed")
            return self._stored_response(receipt)
        if not run.prefect_executions:
            self._audit(
                run_id,
                actor=principal.actor,
                operation="cancel",
                reason=request.reason,
                outcome="refused-no-execution",
            )
            raise self._error(409, "no-active-execution", "the run has no managed execution to cancel", run_id=run_id)
        link: PrefectExecutionLink | None = None
        unavailable_detail = False
        for candidate in reversed(run.prefect_executions):
            try:
                observed = await self._orchestration.observe(candidate.flow_run_id)
            except Exception as exc:  # pylint: disable=broad-exception-caught  # noqa: BLE001
                self._raise_cancel_unavailable(
                    receipt,
                    principal,
                    request.reason,
                    "the active managed execution cannot be confirmed",
                    exc=exc,
                )
            if not observed.available:
                if observed.reason == "prefect-execution-unavailable":
                    unavailable_detail = True
                    continue
                self._raise_cancel_unavailable(
                    receipt,
                    principal,
                    request.reason,
                    "the active managed execution cannot be confirmed",
                )
            if observed.state in TERMINAL_STATES:
                continue
            link = candidate
            break
        if link is None and unavailable_detail:
            self._raise_cancel_unavailable(
                receipt,
                principal,
                request.reason,
                "the active managed execution cannot be confirmed",
            )
        if link is None:
            self._audit(
                run_id, actor=principal.actor, operation="cancel", reason=request.reason, outcome="refused-terminal"
            )
            raise self._error(409, "execution-terminal", "the managed execution is already terminal", run_id=run_id)
        try:
            cancelled = await self._orchestration.cancel(link.flow_run_id)
        except Exception as exc:  # pylint: disable=broad-exception-caught  # noqa: BLE001
            self._raise_cancel_unavailable(
                receipt,
                principal,
                request.reason,
                "Prefect could not confirm the cancellation request",
                exc=exc,
            )
        if not cancelled.available:
            self._raise_cancel_unavailable(
                receipt,
                principal,
                request.reason,
                "Prefect could not confirm the cancellation request",
            )
        self._projection.observe_prefect_execution(
            run_id,
            link.flow_run_id,
            state=cancelled.state,
            secrets=self._secrets,
        )
        resource = self._resource_with_observations(
            self._required_run(run_id),
            {link.flow_run_id: cancelled},
        )
        body = resource.model_dump(mode="json")
        completed = self._projection.complete_mutation(
            receipt.receipt_id,
            response_status=202,
            response_body=body,
            flow_run_id=link.flow_run_id,
            secrets=self._secrets,
        )
        self._audit(run_id, actor=principal.actor, operation="cancel", reason=request.reason, outcome="accepted")
        return self._stored_response(completed)

    def _raise_cancel_unavailable(
        self,
        receipt: MutationReceipt,
        principal: Principal,
        reason: str,
        message: str,
        *,
        exc: Exception | None = None,
    ) -> NoReturn:
        self._audit(receipt.run_id, actor=principal.actor, operation="cancel", reason=reason, outcome="unavailable")
        error = self._error(
            503,
            "orchestration-unavailable",
            message,
            run_id=receipt.run_id,
            mutation_id=receipt.receipt_id,
        )
        if exc is None:
            raise error
        error.__cause__ = sanitize_exception_chain(exc, self._secrets)
        error.__suppress_context__ = True
        raise error from error.__cause__

    async def get_run(self, run_id: str) -> RunResource:
        """Return retained product state even when Prefect detail expired."""
        run = self._required_run(run_id)
        observations: dict[str, Observation] = {}
        for link in run.prefect_executions:
            observed = await self._orchestration.observe(link.flow_run_id)
            observations[link.flow_run_id] = observed
            if observed.available:
                self._projection.observe_prefect_execution(
                    run_id,
                    link.flow_run_id,
                    state=observed.state,
                    secrets=self._secrets,
                )
        return self._resource_with_observations(self._required_run(run_id), observations)

    def get_plan(self, run_id: str) -> PlanResource:
        """Return the retained review document."""
        self._required_run(run_id)
        return self._plan(run_id)

    def get_results(self, run_id: str) -> ResultsResource:
        """Return retained product results without consulting Prefect."""
        run = self._required_run(run_id)
        return ResultsResource(run_id=run_id, results=run.results)

    def list_artifacts(self, run_id: str) -> ArtifactListResource:
        """List immutable references without reading artifact bodies."""
        run = self._required_run(run_id)
        return ArtifactListResource(run_id=run_id, artifacts=run.artifact_refs)

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
        reason: str,
    ) -> tuple[int, dict[str, Any]]:
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
                f"Prefect could not confirm managed submission ({type(exc).__name__})",
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
        )
        body = resource.model_dump(mode="json")
        completed = self._projection.complete_mutation(
            receipt.receipt_id,
            response_status=202,
            response_body=body,
            flow_run_id=submission.flow_run_id,
            secrets=self._secrets,
        )
        self._audit(
            receipt.run_id,
            actor=principal.actor,
            operation=receipt.operation,
            reason=reason,
            outcome="accepted",
        )
        return self._stored_response(completed)

    def _reserve_existing(
        self,
        run: ProductRun,
        principal: Principal,
        idempotency_key: str,
        *,
        operation: str,
        reason: str,
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
        reason: str,
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

    async def _resume_or_replay(
        self,
        receipt: MutationReceipt,
        parameters: dict[str, object],
        principal: Principal,
        reason: str,
    ) -> tuple[int, dict[str, Any]]:
        if receipt.state == "accepted":
            self._audit(
                receipt.run_id,
                actor=principal.actor,
                operation=receipt.operation,
                reason=reason,
                outcome="replayed",
            )
            return self._stored_response(receipt)
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
        reason: str,
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

    def _owned_run(self, run_id: str, principal: Principal, operation: str, reason: str) -> ProductRun:
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

    def _plan(self, run_id: str) -> PlanResource:
        result = self._projection.lookup_artifact(run_id, PLAN_ARTIFACT_ID)
        if result.value is None:
            if result.reason == "run-not-found":
                raise self._error(404, "run-not-found", "the requested Sync run does not exist", run_id=run_id)
            if result.reason == "artifact-expired":
                raise self._error(410, "artifact-expired", "the retained plan has expired", run_id=run_id)
            raise self._error(503, "plan-unavailable", "the retained plan cannot be retrieved", run_id=run_id)
        try:
            return PlanResource.model_validate_json(result.value)
        except (ValidationError, ValueError):
            raise self._error(503, "plan-unavailable", "the retained plan is invalid", run_id=run_id) from None

    @staticmethod
    def _stage_parameters(
        run: ProductRun,
        stage: str,
        *,
        branch: str | None = None,
        expected_checksum: str | None = None,
        confirm_writes: bool = False,
    ) -> dict[str, object]:
        return {
            "run_id": run.run_id,
            "sync_name": str(run.summary.get("sync_name", run.configuration_reference)),
            "stage": stage,
            "configuration_reference": run.configuration_reference,
            "branch": branch,
            "expected_checksum": expected_checksum,
            "confirm_writes": confirm_writes,
        }

    @staticmethod
    def _stored_response(receipt: MutationReceipt) -> tuple[int, dict[str, Any]]:
        assert receipt.response_status is not None
        assert receipt.response_body is not None
        return receipt.response_status, receipt.response_body

    @staticmethod
    def _resource_with_observations(run: ProductRun, observations: dict[str, Observation]) -> RunResource:
        orchestration = []
        for link in run.prefect_executions:
            observed = observations.get(link.flow_run_id)
            if observed is None:
                orchestration.append(
                    OrchestrationSummary(
                        flow_run_id=link.flow_run_id,
                        purpose=link.purpose,
                        attempt=link.attempt,
                        state=link.last_observed_state,
                        detail_available=False,
                        unavailable_reason="live-detail-not-requested",
                    )
                )
                continue
            orchestration.append(
                OrchestrationSummary(
                    flow_run_id=link.flow_run_id,
                    purpose=link.purpose,
                    attempt=link.attempt,
                    state=observed.state if observed.available else link.last_observed_state,
                    detail_available=observed.available,
                    unavailable_reason=observed.reason,
                )
            )
        return RunResource(run=run, orchestration=tuple(orchestration))

    def _audit(
        self,
        run_id: str | None,
        *,
        actor: str,
        operation: str,
        reason: str,
        outcome: str,
    ) -> None:
        event = AuditEvent(
            event_id=f"a-{uuid4().hex}",
            run_id=run_id,
            actor=actor,
            operation=operation,
            reason=reason,
            outcome=outcome,
            created_at=datetime.now(timezone.utc),
        )
        self._projection.record_audit(event, secrets=self._secrets)

    def record_authentication_refusal(self, operation: str, reason: str) -> None:
        """Persist anonymous authentication refusal evidence without token material."""
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
        reason: str,
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
    ) -> ManagedAPIError:
        return ManagedAPIError(
            status,
            code,
            message,
            run_id=run_id,
            mutation_id=mutation_id,
            secrets=self._secrets,
        )
