"""HTTP-only adapter for the shared configuration application service."""

from datetime import datetime, timezone
from functools import wraps
from hashlib import sha256
from pathlib import Path
from typing import Annotated, Any, Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, Query
from fastapi import Path as APIPath
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from infrahub_sync.plan.canonical import canonical_json_bytes
from infrahub_sync.product_store import (
    AuditEvent,
    MutationReceipt,
    ProductProjection,
    ProductStoreProviderError,
    configs,
    local_product_projection,
)

from .auth import Principal
from .models import ConfigMutationRequest
from .service import ManagedAPIError

_CONFIG_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"
_MAX_REGISTRY_VERSION = 2**63 - 1
_MAX_PAGE_LIMIT = 256


class ConfigurationAPIError(Exception):
    """Fixed public representation of one shared configuration-service refusal."""

    def __init__(self, status: int, family: str, *, reason: str | None = None, proven_pre_effect: bool = False) -> None:
        self.status = status
        self.family = family
        self.reason = reason
        self.proven_pre_effect = proven_pre_effect


def _provider_error_boundary(operation: Any) -> Any:
    """Keep direct receipt and audit provider failures in the configuration vocabulary."""

    @wraps(operation)
    def guarded(*args: Any, **kwargs: Any) -> Any:
        try:
            return operation(*args, **kwargs)
        except ProductStoreProviderError:
            raise ConfigurationAPIError(503, "storage") from None

    return guarded


def _strict_integer(value: str, *, minimum: int, maximum: int) -> int:
    """Parse one bounded API integer without accepting FastAPI's coercions."""
    if not value.isascii() or not value.isdecimal():
        raise ManagedAPIError(422, "request-invalid", "the request does not match the API schema")
    number = int(value)
    if number < minimum or number > maximum:
        raise ManagedAPIError(422, "request-invalid", "the request does not match the API schema")
    return number


class ConfigurationRoutes:
    """Bind configuration operations to this server's durable cache location."""

    def __init__(
        self,
        product_cache_location: Path | None = None,
        *,
        product_projection: ProductProjection | None = None,
        service: Any = configs,
        secrets: tuple[str, ...] = (),
    ) -> None:
        if (product_cache_location is None) == (product_projection is None):
            msg = "provide exactly one product projection or product_cache_location"
            raise ValueError(msg)
        self._location = product_cache_location
        self._projection = product_projection
        self._service = service
        self._secrets = secrets

    def _call(self, operation: Any, **kwargs: Any) -> Any:
        try:
            if self._projection is not None:
                return operation(projection=self._projection, **kwargs)
            return operation(product_cache_location=self._location, **kwargs)
        except self._service.ConfigsError as error:
            error_type = type(error)
            if error_type is self._service.ConfigsRequestError:
                raise ConfigurationAPIError(400, "request", proven_pre_effect=True) from None
            if error_type is self._service.ConfigsValidationError:
                raise ConfigurationAPIError(422, "validation", proven_pre_effect=True) from None
            if error_type is self._service.ConfigsNotFoundError:
                raise ConfigurationAPIError(404, "not-found", reason=error.reason, proven_pre_effect=True) from None
            if error_type is self._service.ConfigsStorageError:
                raise ConfigurationAPIError(503, "storage") from None
            if error_type is self._service.ConfigsInternalError:
                raise ConfigurationAPIError(503, "internal") from None
            raise ConfigurationAPIError(503, "configs") from None

    def register(self, package: dict[str, Any]) -> Any:
        return self._call(self._service.register, package=package)

    def create_version(self, config_id: str, package: dict[str, Any]) -> Any:
        return self._call(self._service.create_version, config_id=config_id, package=package)

    def list_configs(self) -> Any:
        return self._call(self._service.list_configs)

    def get_config(self, config_id: str) -> Any:
        return self._call(self._service.get_config, config_id=config_id)

    def list_versions(self, config_id: str) -> Any:
        return self._call(self._service.list_versions, config_id=config_id)

    def get_version(self, config_id: str, registry_version: int) -> Any:
        return self._call(self._service.get_version, config_id=config_id, registry_version=registry_version)

    def validate(self, config_id: str, registry_version: int, *, offset: int = 0, limit: int = _MAX_PAGE_LIMIT) -> Any:
        report = self._call(self._service.validate, config_id=config_id, registry_version=registry_version)
        findings = report.findings[offset : offset + limit]
        return {
            "config_id": report.config_id,
            "registry_version": report.registry_version,
            "package_checksum": report.package_checksum,
            "destination_schema_fingerprint": report.destination_schema_fingerprint,
            "findings": findings,
            "offset": offset,
            "limit": limit,
            "total_findings": len(report.findings),
            "next_offset": offset + len(findings) if offset + len(findings) < len(report.findings) else None,
        }

    @_provider_error_boundary
    def mutate(
        self,
        *,
        actor: str,
        idempotency_key: str,
        operation: str,
        resource_kind: Literal["configuration", "configuration-registry"],
        resource_id: str,
        package: dict[str, Any],
        reason: str,
    ) -> tuple[int, dict[str, Any]]:
        """Persist one configuration mutation response and replay it exactly."""
        now = datetime.now(timezone.utc)
        receipt = MutationReceipt(
            receipt_id=f"m-{uuid4().hex}",
            actor=actor,
            key_digest=sha256(idempotency_key.encode()).hexdigest(),
            operation=operation,
            request_fingerprint=sha256(
                canonical_json_bytes(
                    {
                        "resource_kind": resource_kind,
                        "resource_id": resource_id,
                        "operation": operation,
                        "package": package,
                        "reason": reason,
                    }
                )
            ).hexdigest(),
            reason=reason,
            resource_kind=resource_kind,
            resource_id=resource_id,
            created_at=now,
            updated_at=now,
        )
        projection = self._store_projection()
        stored, created = projection.reserve_mutation(receipt, secrets=self._secrets)
        if not created:
            if (
                stored.resource_kind != receipt.resource_kind
                or stored.resource_id != receipt.resource_id
                or stored.operation != receipt.operation
                or stored.request_fingerprint != receipt.request_fingerprint
            ):
                self._audit(actor, operation, reason, "refused-idempotency")
                raise ManagedAPIError(
                    409, "idempotency-conflict", "Idempotency-Key was already used by this actor for different content"
                )
            if stored.state == "accepted":
                assert stored.response_status is not None
                assert stored.response_body is not None
                self._audit(actor, operation, reason, "replayed")
                return stored.response_status, stored.response_body
        if not projection.claim_mutation(stored.receipt_id, secrets=self._secrets):
            self._audit(actor, operation, reason, "refused-idempotency-in-progress")
            raise ManagedAPIError(409, "idempotency-in-progress", "the matching request is still being processed")
        try:
            if operation == "register-config":
                result = self.register(package)
                response_status = 201
            else:
                result = self.create_version(resource_id, package)
                response_status = 201 if result.created else 200
        except ConfigurationAPIError as error:
            if error.proven_pre_effect:
                projection.release_mutation(stored.receipt_id, secrets=self._secrets)
            self._audit(actor, operation, reason, "unavailable")
            raise
        response = jsonable_encoder(result)
        completed = projection.complete_mutation(
            stored.receipt_id,
            response_status=response_status,
            response_body=response,
            flow_run_id=None,
            secrets=self._secrets,
        )
        assert completed.response_body is not None
        assert completed.response_status is not None
        self._audit(actor, operation, reason, "accepted")
        return completed.response_status, completed.response_body

    @_provider_error_boundary
    def audit_refusal(self, actor: str, operation: str, reason: str) -> None:
        """Record a refused configuration mutation without reserving it."""
        self._audit(actor, operation, reason, "refused-authorization")

    def _audit(self, actor: str, operation: str, reason: str, outcome: str) -> None:
        self._store_projection().record_audit(
            AuditEvent(
                event_id=f"a-{uuid4().hex}",
                run_id=None,
                actor=actor,
                operation=operation,
                reason=reason,
                outcome=outcome,
                created_at=datetime.now(timezone.utc),
            ),
            secrets=self._secrets,
        )

    def _store_projection(self) -> ProductProjection:
        """Return the injected projection or the explicit local compatibility projection."""
        if self._projection is not None:
            return self._projection
        assert self._location is not None
        return local_product_projection(self._location)


def configuration_router(routes: ConfigurationRoutes, authenticate: Any, idempotency_key: Any) -> APIRouter:
    """Create the seven authenticated configuration resources."""
    router = APIRouter()
    config_id_parameter = APIPath(pattern=_CONFIG_ID_PATTERN)
    registry_version_parameter = APIPath()
    page_offset = Query()
    page_limit = Query()

    @router.post("/configs", status_code=201)
    def register(
        body: ConfigMutationRequest,
        principal: Annotated[Principal, Depends(authenticate)],
        key: Annotated[str, Depends(idempotency_key)],
    ) -> Any:
        if not principal.administrator:
            routes.audit_refusal(principal.actor, "register-config", body.reason)
            raise ManagedAPIError(403, "forbidden", "administrator access is required")
        status, content = routes.mutate(
            actor=principal.actor,
            idempotency_key=key,
            operation="register-config",
            resource_kind="configuration-registry",
            resource_id="configs",
            package=body.package,
            reason=body.reason,
        )
        return JSONResponse(status_code=status, content=content)

    @router.post("/configs/{config_id}/versions", status_code=201)
    def create_version(
        config_id: Annotated[str, config_id_parameter],
        body: ConfigMutationRequest,
        principal: Annotated[Principal, Depends(authenticate)],
        key: Annotated[str, Depends(idempotency_key)],
    ) -> Any:
        if not principal.administrator:
            routes.audit_refusal(principal.actor, "create-config-version", body.reason)
            raise ManagedAPIError(403, "forbidden", "administrator access is required")
        status, content = routes.mutate(
            actor=principal.actor,
            idempotency_key=key,
            operation="create-config-version",
            resource_kind="configuration",
            resource_id=config_id,
            package=body.package,
            reason=body.reason,
        )
        return JSONResponse(status_code=status, content=content)

    @router.get("/configs")
    def list_configs(
        _principal: Annotated[Principal, Depends(authenticate)],
        offset: Annotated[str, page_offset] = "0",
        limit: Annotated[str, page_limit] = str(_MAX_PAGE_LIMIT),
    ) -> Any:
        _strict_integer(offset, minimum=0, maximum=_MAX_REGISTRY_VERSION)
        _strict_integer(limit, minimum=1, maximum=_MAX_PAGE_LIMIT)
        return routes.list_configs()

    @router.get("/configs/{config_id}")
    def get_config(
        config_id: Annotated[str, config_id_parameter], _principal: Annotated[Principal, Depends(authenticate)]
    ) -> Any:
        return routes.get_config(config_id)

    @router.get("/configs/{config_id}/versions")
    def list_versions(
        config_id: Annotated[str, config_id_parameter],
        _principal: Annotated[Principal, Depends(authenticate)],
        offset: Annotated[str, page_offset] = "0",
        limit: Annotated[str, page_limit] = str(_MAX_PAGE_LIMIT),
    ) -> Any:
        _strict_integer(offset, minimum=0, maximum=_MAX_REGISTRY_VERSION)
        _strict_integer(limit, minimum=1, maximum=_MAX_PAGE_LIMIT)
        return routes.list_versions(config_id)

    @router.get("/configs/{config_id}/versions/{registry_version}")
    def get_version(
        config_id: Annotated[str, config_id_parameter],
        registry_version: Annotated[str, registry_version_parameter],
        _principal: Annotated[Principal, Depends(authenticate)],
    ) -> Any:
        return routes.get_version(
            config_id, _strict_integer(registry_version, minimum=1, maximum=_MAX_REGISTRY_VERSION)
        )

    @router.post("/configs/{config_id}/versions/{registry_version}/validate")
    def validate(
        config_id: Annotated[str, config_id_parameter],
        registry_version: Annotated[str, registry_version_parameter],
        _principal: Annotated[Principal, Depends(authenticate)],
        offset: Annotated[str, page_offset] = "0",
        limit: Annotated[str, page_limit] = str(_MAX_PAGE_LIMIT),
    ) -> Any:
        return routes.validate(
            config_id,
            _strict_integer(registry_version, minimum=1, maximum=_MAX_REGISTRY_VERSION),
            offset=_strict_integer(offset, minimum=0, maximum=_MAX_REGISTRY_VERSION),
            limit=_strict_integer(limit, minimum=1, maximum=_MAX_PAGE_LIMIT),
        )

    return router
