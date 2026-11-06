"""HTTP-only adapter for the shared configuration application service."""

from collections.abc import Mapping
from datetime import datetime, timezone
from functools import wraps
from hashlib import sha256
from typing import Annotated, Any, Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, Query
from fastapi import Path as APIPath
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from infrahub_sync.client.models import (
    ConfigurationPage,
    ConfigurationSummaryResource,
    ConfigurationVersionPage,
    ConfigurationVersionResource,
    ConfigurationVersionSummaryResource,
    RegisteredConfigurationResource,
    RegisteredVersionResource,
    ValidationReportResource,
)
from infrahub_sync.configuration.storage import UNSUPPORTED_STORE_REASON
from infrahub_sync.plan.canonical import canonical_json_bytes
from infrahub_sync.product_store import (
    MutationReceipt,
    ProductProjection,
    ProductStoreProviderError,
    configs,
)
from infrahub_sync.product_store.models import MAX_REGISTRY_VERSION

from .auth import Principal
from .events import CONFIG_REGISTERED, CONFIG_VERSION_REGISTERED, config_decision_type, config_event
from .models import ConfigMutationRequest
from .paging import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    MIN_LIMIT,
    FilterValue,
    decode_cursor,
    encode_cursor,
    timestamp_cursor,
    timestamp_key,
    version_key,
)
from .service import ServiceAPIError

CONFIG_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"
# Most findings one validation-report page returns, and the default page size.
_MAX_VALIDATION_FINDINGS_PAGE = 256


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
        except (ConfigurationAPIError, ServiceAPIError):
            raise
        except ProductStoreProviderError:
            raise ConfigurationAPIError(503, "storage") from None
        except Exception:  # noqa: BLE001  # Direct receipt/audit defects retain the fixed configuration family.
            raise ConfigurationAPIError(503, "internal") from None

    return guarded


def parse_bounded_integer(value: str, *, minimum: int, maximum: int) -> int:
    """Parse one bounded API integer without accepting FastAPI's coercions."""
    if not value.isascii() or not value.isdecimal():
        raise ServiceAPIError(422, "request-invalid", "the request does not match the API schema")
    # Refuse a value with more digits than the maximum before ``int()``, which raises
    # ``ValueError`` past Python's integer string-digit limit.
    digits = value.lstrip("0") or "0"
    if len(digits) > len(str(maximum)):
        raise ServiceAPIError(422, "request-invalid", "the request does not match the API schema")
    number = int(digits)
    if number < minimum or number > maximum:
        raise ServiceAPIError(422, "request-invalid", "the request does not match the API schema")
    return number


def parse_page_limit(value: str | None) -> int | None:
    """Parse a list's ``limit`` query value without FastAPI's coercions (``1.0``, ``true``)."""
    return None if value is None else parse_bounded_integer(value, minimum=MIN_LIMIT, maximum=MAX_LIMIT)


class ConfigurationRoutes:
    """Bind configuration operations to this server's durable product projection."""

    def __init__(
        self,
        *,
        product_projection: ProductProjection,
        service: Any = configs,
        secrets: tuple[str, ...] = (),
    ) -> None:
        self._projection = product_projection
        self._service = service
        self._secrets = secrets

    def _call(self, operation: Any, **kwargs: Any) -> Any:
        """Translate configuration service refusals into fixed API classifications."""
        try:
            return operation(projection=self._projection, **kwargs)
        except self._service.ConfigsError as error:
            error_type = type(error)
            if error_type is self._service.ConfigsRequestError:
                raise ConfigurationAPIError(400, "request", proven_pre_effect=True) from None
            if error_type is self._service.ConfigsValidationError:
                reason = UNSUPPORTED_STORE_REASON if error.reason == UNSUPPORTED_STORE_REASON else None
                raise ConfigurationAPIError(422, "validation", reason=reason, proven_pre_effect=True) from None
            if error_type is self._service.ConfigsNotFoundError:
                raise ConfigurationAPIError(404, "not-found", reason=error.reason, proven_pre_effect=True) from None
            if error_type is self._service.ConfigsStorageError:
                raise ConfigurationAPIError(503, "storage") from None
            if error_type is self._service.ConfigsInternalError:
                raise ConfigurationAPIError(503, "internal") from None
            raise ConfigurationAPIError(503, "configs") from None

    def register(
        self, package: dict[str, Any], *, actor: str | None = None, reason: str | None = None
    ) -> RegisteredConfigurationResource:
        """Register a package and return its configuration and first version.

        ``actor`` and ``reason`` are recorded on the first version, with this server's
        secrets redacted.
        """

        result = self._call(self._service.register, package=package, actor=actor, reason=reason, secrets=self._secrets)
        return RegisteredConfigurationResource.model_validate(result, from_attributes=True)

    def create_version(
        self, config_id: str, package: dict[str, Any], *, actor: str | None = None, reason: str | None = None
    ) -> RegisteredVersionResource:
        """Create or retrieve an identical registered configuration version.

        ``actor`` and ``reason`` are recorded on a created version, with this server's
        secrets redacted.
        """

        result = self._call(
            self._service.create_version,
            config_id=config_id,
            package=package,
            actor=actor,
            reason=reason,
            secrets=self._secrets,
        )
        return RegisteredVersionResource.model_validate(result, from_attributes=True)

    def list_configs(
        self, *, name: str | None = None, cursor: str | None = None, limit: int | None = None
    ) -> ConfigurationPage:
        """Return one page of configurations, newest first, optionally only those named ``name``.

        ``limit`` is already bounded by the route (`parse_page_limit`); ``None`` means the default.

        Raises:
            InvalidCursorError: ``cursor`` does not decode or was issued for another name.
        """
        page_size = DEFAULT_LIMIT if limit is None else limit
        filters: dict[str, FilterValue] = {"list": "configs", "name": name}
        after = None if cursor is None else timestamp_key(decode_cursor(cursor, filters))
        page = self._call(self._service.list_configs, name=name, after=after, limit=page_size)
        items = tuple(ConfigurationSummaryResource.model_validate(item, from_attributes=True) for item in page.items)
        last = items[-1] if page.has_more else None
        next_cursor = None if last is None else timestamp_cursor(last.created_at, last.config_id, filters)
        return ConfigurationPage(items=items, next_cursor=next_cursor)

    def get_config(self, config_id: str) -> ConfigurationSummaryResource:
        """Return one registered configuration summary."""

        result = self._call(self._service.get_config, config_id=config_id)
        return ConfigurationSummaryResource.model_validate(result, from_attributes=True)

    def list_versions(
        self, config_id: str, *, cursor: str | None = None, limit: int | None = None
    ) -> ConfigurationVersionPage:
        """Return one page of a configuration's version summaries, newest version first.

        ``limit`` is already bounded by the route (`parse_page_limit`); ``None`` means the default.

        Raises:
            InvalidCursorError: ``cursor`` does not decode or was issued for another
                configuration.
            ConfigurationAPIError: ``404`` for an unknown configuration.
        """
        page_size = DEFAULT_LIMIT if limit is None else limit
        filters: dict[str, FilterValue] = {"list": "versions", "config_id": config_id}
        after = None if cursor is None else version_key(decode_cursor(cursor, filters))
        page = self._call(self._service.list_versions, config_id=config_id, after=after, limit=page_size)
        items = tuple(
            ConfigurationVersionSummaryResource.model_validate(item, from_attributes=True) for item in page.items
        )
        next_cursor = encode_cursor(items[-1].registry_version, filters) if page.has_more else None
        return ConfigurationVersionPage(items=items, next_cursor=next_cursor)

    def get_version(self, config_id: str, registry_version: int) -> ConfigurationVersionResource:
        """Return one immutable registered configuration version."""

        result = self._call(self._service.get_version, config_id=config_id, registry_version=registry_version)
        return ConfigurationVersionResource.model_validate(result, from_attributes=True)

    def validate(
        self, config_id: str, registry_version: int, *, offset: int = 0, limit: int = _MAX_VALIDATION_FINDINGS_PAGE
    ) -> ValidationReportResource:
        """Validate a version and return the requested ordered findings page.

        This server's collected secrets reach the producer, which can replace a declared key
        whole before the diagnostic bounds cut it, and the same set is applied again to every
        finding on the returned page.
        """

        report = self._call(
            self._service.validate, config_id=config_id, registry_version=registry_version, secrets=self._secrets
        )
        findings = tuple(
            configs.redact_finding(finding, self._secrets) for finding in report.findings[offset : offset + limit]
        )
        return ValidationReportResource.model_validate(
            {
                "config_id": report.config_id,
                "registry_version": report.registry_version,
                "package_checksum": report.package_checksum,
                "destination_schema_fingerprint": report.destination_schema_fingerprint,
                "findings": tuple(finding.model_dump(mode="json") for finding in findings),
                "offset": offset,
                "limit": limit,
                "total_findings": len(report.findings),
                "next_offset": offset + len(findings) if offset + len(findings) < len(report.findings) else None,
            }
        )

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
        reason: str | None,
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
                self._audit(actor, operation, reason, "refused-idempotency", *_configuration_of(receipt, None))
                raise ServiceAPIError(
                    409, "idempotency-conflict", "Idempotency-Key was already used by this actor for different content"
                )
            if stored.state == "accepted":
                assert stored.response_status is not None
                assert stored.response_body is not None
                self._audit(actor, operation, reason, "replayed", *_configuration_of(stored, stored.response_body))
                return stored.response_status, stored.response_body
        if not projection.claim_mutation(stored.receipt_id, secrets=self._secrets):
            self._audit(actor, operation, reason, "refused-idempotency-in-progress", *_configuration_of(receipt, None))
            raise ServiceAPIError(409, "idempotency-in-progress", "the matching request is still being processed")
        try:
            if operation == "register-config":
                result = self.register(package, actor=actor, reason=reason)
                response_status = 201
            else:
                result = self.create_version(resource_id, package, actor=actor, reason=reason)
                response_status = 201 if result.created else 200
        except ConfigurationAPIError as error:
            if error.proven_pre_effect:
                projection.release_mutation(stored.receipt_id, secrets=self._secrets)
            self._audit(actor, operation, reason, "unavailable", *_configuration_of(receipt, None))
            raise
        response = jsonable_encoder(result)
        config_id, registry_version = _configuration_of(receipt, response)
        completed = projection.complete_mutation(
            stored.receipt_id,
            response_status=response_status,
            response_body=response,
            flow_run_id=None,
            event=config_event(
                event_type=CONFIG_REGISTERED if operation == "register-config" else CONFIG_VERSION_REGISTERED,
                actor=actor,
                operation=operation,
                reason=reason,
                outcome="accepted",
                config_id=config_id,
                registry_version=registry_version,
            ),
            secrets=self._secrets,
        )
        assert completed.response_body is not None
        assert completed.response_status is not None
        return completed.response_status, completed.response_body

    @_provider_error_boundary
    def audit_refusal(self, actor: str, operation: str, reason: str | None, *, config_id: str | None = None) -> None:
        """Record a refused configuration mutation without reserving it.

        ``config_id`` names the configuration a refused version request was for; a refused
        registration has none yet.
        """
        self._audit(actor, operation, reason, "refused-authorization", config_id)

    def _audit(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        self,
        actor: str,
        operation: str,
        reason: str | None,
        outcome: str,
        config_id: str | None = None,
        registry_version: int | None = None,
    ) -> None:
        """Record the event of a configuration decision that saves nothing else, such as a refusal."""
        self._store_projection().record_audit(
            config_event(
                event_type=config_decision_type(outcome),
                actor=actor,
                operation=operation,
                reason=reason,
                outcome=outcome,
                config_id=config_id,
                registry_version=registry_version,
            ),
            secrets=self._secrets,
        )

    def _store_projection(self) -> ProductProjection:
        """Return the injected durable projection this server writes receipts and audit to."""
        return self._projection


def _configuration_of(receipt: MutationReceipt, response: Mapping[str, Any] | None) -> tuple[str | None, int | None]:
    """Return the configuration and version a configuration decision is about, where known.

    A stored response names both. Without one, a version request names its configuration
    and a registration names none yet.
    """
    version = None if response is None else response.get("version")
    if isinstance(version, Mapping):
        config_id, registry_version = version.get("config_id"), version.get("registry_version")
        if isinstance(config_id, str) and isinstance(registry_version, int):
            return config_id, registry_version
    if receipt.resource_kind == "configuration":
        return receipt.resource_id, None
    return None, None


def configuration_router(routes: ConfigurationRoutes, authenticate: Any, idempotency_key: Any) -> APIRouter:
    """Create the seven authenticated configuration resources."""
    router = APIRouter()
    config_id_parameter = APIPath(pattern=CONFIG_ID_PATTERN)
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
            raise ServiceAPIError(403, "forbidden", "administrator access is required")
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
            routes.audit_refusal(principal.actor, "create-config-version", body.reason, config_id=config_id)
            raise ServiceAPIError(403, "forbidden", "administrator access is required")
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
        name: Annotated[str | None, Query()] = None,
        limit: Annotated[str | None, page_limit] = None,
        cursor: Annotated[str | None, Query()] = None,
    ) -> ConfigurationPage:
        """Return one page of configurations, newest first, optionally only those named ``name``."""
        return routes.list_configs(name=name, cursor=cursor, limit=parse_page_limit(limit))

    @router.get("/configs/{config_id}")
    def get_config(
        config_id: Annotated[str, config_id_parameter], _principal: Annotated[Principal, Depends(authenticate)]
    ) -> Any:
        return routes.get_config(config_id)

    @router.get("/configs/{config_id}/versions")
    def list_versions(
        config_id: Annotated[str, config_id_parameter],
        _principal: Annotated[Principal, Depends(authenticate)],
        limit: Annotated[str | None, page_limit] = None,
        cursor: Annotated[str | None, Query()] = None,
    ) -> ConfigurationVersionPage:
        """Return one page of a configuration's version summaries, newest version first."""
        return routes.list_versions(config_id, cursor=cursor, limit=parse_page_limit(limit))

    @router.get("/configs/{config_id}/versions/{registry_version}")
    def get_version(
        config_id: Annotated[str, config_id_parameter],
        registry_version: Annotated[str, registry_version_parameter],
        _principal: Annotated[Principal, Depends(authenticate)],
    ) -> Any:
        return routes.get_version(
            config_id, parse_bounded_integer(registry_version, minimum=1, maximum=MAX_REGISTRY_VERSION)
        )

    @router.post("/configs/{config_id}/versions/{registry_version}/validate")
    def validate(
        config_id: Annotated[str, config_id_parameter],
        registry_version: Annotated[str, registry_version_parameter],
        _principal: Annotated[Principal, Depends(authenticate)],
        offset: Annotated[str, page_offset] = "0",
        limit: Annotated[str, page_limit] = str(_MAX_VALIDATION_FINDINGS_PAGE),
    ) -> Any:
        return routes.validate(
            config_id,
            parse_bounded_integer(registry_version, minimum=1, maximum=MAX_REGISTRY_VERSION),
            offset=parse_bounded_integer(offset, minimum=0, maximum=MAX_REGISTRY_VERSION),
            limit=parse_bounded_integer(limit, minimum=1, maximum=_MAX_VALIDATION_FINDINGS_PAGE),
        )

    return router
