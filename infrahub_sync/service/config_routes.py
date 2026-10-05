"""HTTP-only adapter for the shared configuration application service."""

from datetime import datetime, timezone
from functools import wraps
from hashlib import sha256
from typing import Annotated, Any, Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, Query
from fastapi import Path as APIPath
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from infrahub_sdk.exceptions import AuthenticationError, BranchNotFoundError
from infrahub_sdk.exceptions import Error as InfrahubSdkError

from infrahub_sync.client.models import (
    ConfigurationSummaryResource,
    ConfigurationVersionResource,
    RegisteredConfigurationResource,
    RegisteredVersionResource,
    ValidationReportResource,
)
from infrahub_sync.configuration.storage import UNSUPPORTED_STORE_REASON
from infrahub_sync.plan.canonical import canonical_json_bytes
from infrahub_sync.platform.client import SERVICE_ACCOUNT_REFUSED
from infrahub_sync.platform.records import ConfigurationDocumentError
from infrahub_sync.product_store import (
    AuditEvent,
    MutationReceipt,
    ProductProjection,
    ProductStoreProviderError,
    configs,
)

from .auth import Principal
from .models import ConfigMutationRequest
from .service import ServiceAPIError

_CONFIG_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"
_MAX_REGISTRY_VERSION = 2**63 - 1
_MAX_PAGE_LIMIT = 256
_SERVICE_ACCOUNT_BRANCH_REFUSED = (
    "Infrahub refused the Sync service account's read of the configuration: the account needs a token "
    "Infrahub accepts, and object:Sync:Configuration:view with allow_all to read a branch other than the default"
)


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


def _strict_integer(value: str, *, minimum: int, maximum: int) -> int:
    """Parse one bounded API integer without accepting FastAPI's coercions."""
    if not value.isascii() or not value.isdecimal():
        raise ServiceAPIError(422, "request-invalid", "the request does not match the API schema")
    number = int(value)
    if number < minimum or number > maximum:
        raise ServiceAPIError(422, "request-invalid", "the request does not match the API schema")
    return number


class ConfigurationRoutes:
    """Bind configuration operations to this server's durable product projection."""

    def __init__(
        self,
        *,
        product_projection: ProductProjection,
        service: Any = configs,
        secrets: tuple[str, ...] = (),
        configurations: Any = None,
    ) -> None:
        self._projection = product_projection
        self._service = service
        self._secrets = secrets
        # Infrahub's configurations when it holds them; reads answer from there, and the
        # register and version routes refuse because users change configurations in Infrahub.
        self._configurations = configurations

    @property
    def in_infrahub(self) -> bool:
        """Whether configurations are created and changed in Infrahub rather than through this API."""
        return self._configurations is not None

    def _call(self, operation: Any, **kwargs: Any) -> Any:
        """Translate configuration service refusals into fixed API classifications."""
        return self._translated(operation, projection=self._configurations or self._projection, **kwargs)

    def _translated(self, operation: Any, **kwargs: Any) -> Any:
        try:
            return operation(**kwargs)
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

    def register(self, package: dict[str, Any]) -> RegisteredConfigurationResource:
        """Register a package and return its configuration and first version."""

        result = self._call(self._service.register, package=package)
        return RegisteredConfigurationResource.model_validate(result, from_attributes=True)

    def create_version(self, config_id: str, package: dict[str, Any]) -> RegisteredVersionResource:
        """Create or retrieve an identical registered configuration version."""

        result = self._call(self._service.create_version, config_id=config_id, package=package)
        return RegisteredVersionResource.model_validate(result, from_attributes=True)

    def list_configs(self) -> tuple[ConfigurationSummaryResource, ...]:
        """Return the complete ordered configuration list."""

        results = self._call(self._service.list_configs)
        return tuple(ConfigurationSummaryResource.model_validate(result, from_attributes=True) for result in results)

    def get_config(self, config_id: str) -> ConfigurationSummaryResource:
        """Return one registered configuration summary."""

        result = self._call(self._service.get_config, config_id=config_id)
        return ConfigurationSummaryResource.model_validate(result, from_attributes=True)

    def list_versions(self, config_id: str) -> tuple[ConfigurationVersionResource, ...]:
        """Return the complete ordered version list for a configuration."""

        results = self._call(self._service.list_versions, config_id=config_id)
        return tuple(ConfigurationVersionResource.model_validate(result, from_attributes=True) for result in results)

    def get_version(self, config_id: str, registry_version: int) -> ConfigurationVersionResource:
        """Return one immutable registered configuration version."""

        result = self._call(self._service.get_version, config_id=config_id, registry_version=registry_version)
        return ConfigurationVersionResource.model_validate(result, from_attributes=True)

    def validate(
        self, config_id: str, registry_version: int, *, offset: int = 0, limit: int = _MAX_PAGE_LIMIT
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

    def on_default_branch(self, branch: str | None) -> bool:
        """Whether a request names Infrahub's default branch, or no branch at all."""
        if branch is None or self._configurations is None:
            return True
        try:
            return branch == self._configurations.default_branch()
        except AuthenticationError:
            raise ServiceAPIError(503, "infrahub-unavailable", SERVICE_ACCOUNT_REFUSED) from None
        except InfrahubSdkError:
            raise ServiceAPIError(503, "infrahub-unavailable", "Infrahub could not be reached") from None

    def _declared_report(self, content: dict[str, Any]) -> tuple[tuple[Any, ...], str]:
        """Every finding in a declared document, and the checksum its version would carry.

        A document that is not a configuration package at all has no findings to list.
        It is refused with its first defect, as a run on the same document is refused.
        """
        try:
            return (
                self._service.validate_declared(package=content, secrets=self._secrets),
                self._service.declared_checksum(package=content),
            )
        except self._service.ConfigsRequestError as error:
            raise ServiceAPIError(422, "configuration-invalid", str(error), secrets=self._secrets) from None

    def validate_on_branch(
        self, config_id: str, branch: str | None, *, offset: int = 0, limit: int = _MAX_PAGE_LIMIT
    ) -> dict[str, Any]:
        """Validate a configuration's document as it stands on a branch; records nothing."""
        if self._configurations is None:
            raise ServiceAPIError(
                409,
                "configurations-not-in-infrahub",
                "this Sync keeps configurations in its own store, not in Infrahub",
            )
        try:
            content = self._configurations.declared_content(config_id, branch)
        except BranchNotFoundError:
            raise ServiceAPIError(404, "branch-not-found", "the branch does not exist in Infrahub") from None
        except ConfigurationDocumentError as error:
            raise ServiceAPIError(422, "configuration-document-invalid", str(error)) from None
        except AuthenticationError:
            # A refused token, or an account that may read the default branch only.
            raise ServiceAPIError(503, "infrahub-unavailable", _SERVICE_ACCOUNT_BRANCH_REFUSED) from None
        except InfrahubSdkError:
            raise ServiceAPIError(503, "infrahub-unavailable", "Infrahub could not be reached") from None
        if content is None:
            raise ConfigurationAPIError(404, "not-found", reason="configuration-not-found", proven_pre_effect=True)
        findings, checksum = self._translated(self._declared_report, content=content)
        page = tuple(configs.redact_finding(finding, self._secrets) for finding in findings[offset : offset + limit])
        return {
            "config_id": config_id,
            "branch": branch,
            "checksum": checksum,
            "findings": [finding.model_dump(mode="json") for finding in page],
            "offset": offset,
            "limit": limit,
            "total_findings": len(findings),
            "next_offset": offset + len(page) if offset + len(page) < len(findings) else None,
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
                raise ServiceAPIError(
                    409, "idempotency-conflict", "Idempotency-Key was already used by this actor for different content"
                )
            if stored.state == "accepted":
                assert stored.response_status is not None
                assert stored.response_body is not None
                self._audit(actor, operation, reason, "replayed")
                return stored.response_status, stored.response_body
        if not projection.claim_mutation(stored.receipt_id, secrets=self._secrets):
            self._audit(actor, operation, reason, "refused-idempotency-in-progress")
            raise ServiceAPIError(409, "idempotency-in-progress", "the matching request is still being processed")
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
        """Return the injected durable projection this server writes receipts and audit to."""
        return self._projection


def _refuse_when_in_infrahub(routes: ConfigurationRoutes) -> None:
    if routes.in_infrahub:
        raise ServiceAPIError(
            410,
            "configurations-in-infrahub",
            "create and change configurations in Infrahub; a run records the version it uses",
        )


def configuration_router(
    routes: ConfigurationRoutes, authenticate: Any, idempotency_key: Any, authorize: Any = None
) -> APIRouter:
    """Create the authenticated configuration resources."""
    router = APIRouter()

    def configuration_viewer(principal: Annotated[Principal, Depends(authenticate)]) -> Principal:
        if authorize is not None:
            authorize(principal, "view", "Configuration")
        return principal

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
        _refuse_when_in_infrahub(routes)
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
        _refuse_when_in_infrahub(routes)
        if not principal.administrator:
            routes.audit_refusal(principal.actor, "create-config-version", body.reason)
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
        _principal: Annotated[Principal, Depends(configuration_viewer)],
        offset: Annotated[str, page_offset] = "0",
        limit: Annotated[str, page_limit] = str(_MAX_PAGE_LIMIT),
    ) -> Any:
        _strict_integer(offset, minimum=0, maximum=_MAX_REGISTRY_VERSION)
        _strict_integer(limit, minimum=1, maximum=_MAX_PAGE_LIMIT)
        return routes.list_configs()

    @router.get("/configs/{config_id}")
    def get_config(
        config_id: Annotated[str, config_id_parameter], _principal: Annotated[Principal, Depends(configuration_viewer)]
    ) -> Any:
        return routes.get_config(config_id)

    @router.get("/configs/{config_id}/versions")
    def list_versions(
        config_id: Annotated[str, config_id_parameter],
        _principal: Annotated[Principal, Depends(configuration_viewer)],
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
        _principal: Annotated[Principal, Depends(configuration_viewer)],
    ) -> Any:
        return routes.get_version(
            config_id, _strict_integer(registry_version, minimum=1, maximum=_MAX_REGISTRY_VERSION)
        )

    @router.post("/configs/{config_id}/versions/{registry_version}/validate")
    def validate(
        config_id: Annotated[str, config_id_parameter],
        registry_version: Annotated[str, registry_version_parameter],
        _principal: Annotated[Principal, Depends(configuration_viewer)],
        offset: Annotated[str, page_offset] = "0",
        limit: Annotated[str, page_limit] = str(_MAX_PAGE_LIMIT),
    ) -> Any:
        return routes.validate(
            config_id,
            _strict_integer(registry_version, minimum=1, maximum=_MAX_REGISTRY_VERSION),
            offset=_strict_integer(offset, minimum=0, maximum=_MAX_REGISTRY_VERSION),
            limit=_strict_integer(limit, minimum=1, maximum=_MAX_PAGE_LIMIT),
        )

    @router.post("/configs/{config_id}/validate")
    def validate_on_branch(
        config_id: Annotated[str, config_id_parameter],
        principal: Annotated[Principal, Depends(configuration_viewer)],
        branch: Annotated[str | None, Query()] = None,
        offset: Annotated[str, page_offset] = "0",
        limit: Annotated[str, page_limit] = str(_MAX_PAGE_LIMIT),
    ) -> Any:
        # Sync reads the branch with its own account, so the caller must be allowed to
        # see configurations on that branch themselves.
        if not routes.on_default_branch(branch) and not principal.allows("view", "Configuration", default_branch=False):
            raise ServiceAPIError(
                403,
                "forbidden",
                "Infrahub permission object:Sync:Configuration:view on branches other than the default is required",
            )
        return routes.validate_on_branch(
            config_id,
            branch,
            offset=_strict_integer(offset, minimum=0, maximum=_MAX_REGISTRY_VERSION),
            limit=_strict_integer(limit, minimum=1, maximum=_MAX_PAGE_LIMIT),
        )

    return router
