"""HTTP-only adapter for the shared configuration application service."""

from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Annotated, Any
from uuid import uuid4

from fastapi import APIRouter, Depends
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from infrahub_sync.product_store import configs
from infrahub_sync.product_store import MutationReceipt, local_product_projection
from infrahub_sync.plan.canonical import canonical_json_bytes

from .auth import Principal
from .models import ConfigMutationRequest
from .service import ManagedAPIError


class ConfigurationAPIError(Exception):
    """Fixed public representation of one shared configuration-service refusal."""

    def __init__(self, status: int, family: str, *, reason: str | None = None) -> None:
        self.status = status
        self.family = family
        self.reason = reason


class ConfigurationRoutes:
    """Bind configuration operations to this server's durable cache location."""

    def __init__(self, product_cache_location: Path, *, service: Any = configs, secrets: tuple[str, ...] = ()) -> None:
        self._location = product_cache_location
        self._service = service
        self._secrets = secrets

    def _call(self, operation: Any, **kwargs: Any) -> Any:
        try:
            return operation(product_cache_location=self._location, **kwargs)
        except self._service.ConfigsRequestError:
            raise ConfigurationAPIError(400, "request") from None
        except self._service.ConfigsValidationError:
            raise ConfigurationAPIError(422, "validation") from None
        except self._service.ConfigsNotFoundError as exc:
            reason = exc.reason
            raise ConfigurationAPIError(404, "not-found", reason=reason) from None
        except self._service.ConfigsStorageError:
            raise ConfigurationAPIError(503, "storage") from None
        except self._service.ConfigsInternalError:
            raise ConfigurationAPIError(503, "internal") from None
        except self._service.ConfigsError:
            raise ConfigurationAPIError(503, "configs") from None
        except AssertionError:
            raise ConfigurationAPIError(503, "internal") from None

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

    def validate(self, config_id: str, registry_version: int) -> Any:
        return self._call(self._service.validate, config_id=config_id, registry_version=registry_version)

    def mutate(
        self,
        *,
        actor: str,
        idempotency_key: str,
        operation: str,
        resource_path: str,
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
                    {"resource": resource_path, "operation": operation, "package": package, "reason": reason}
                )
            ).hexdigest(),
            reason=reason,
            resource="configuration",
            created_at=now,
            updated_at=now,
        )
        projection = local_product_projection(self._location)
        stored, created = projection.reserve_mutation(receipt, secrets=self._secrets)
        if not created:
            if (
                stored.resource != receipt.resource
                or stored.operation != receipt.operation
                or stored.request_fingerprint != receipt.request_fingerprint
            ):
                raise ManagedAPIError(
                    409, "idempotency-conflict", "Idempotency-Key was already used by this actor for different content"
                )
            if stored.state == "accepted":
                assert stored.response_status is not None and stored.response_body is not None
                return stored.response_status, stored.response_body
        if operation == "register-config":
            response = jsonable_encoder(self.register(package))
        else:
            config_id = resource_path.rsplit("/", 2)[1]
            response = jsonable_encoder(self.create_version(config_id, package))
        completed = projection.complete_mutation(
            stored.receipt_id,
            response_status=201,
            response_body=response,
            flow_run_id=None,
            secrets=self._secrets,
        )
        assert completed.response_body is not None and completed.response_status is not None
        return completed.response_status, completed.response_body


def configuration_router(routes: ConfigurationRoutes, authenticate: Any, idempotency_key: Any) -> APIRouter:
    """Create the seven authenticated configuration resources."""
    router = APIRouter()

    @router.post("/configs", status_code=201)
    def register(
        body: ConfigMutationRequest,
        principal: Annotated[Principal, Depends(authenticate)],
        key: Annotated[str, Depends(idempotency_key)],
    ) -> Any:
        if not principal.administrator:
            raise ManagedAPIError(403, "forbidden", "administrator access is required")
        status, content = routes.mutate(
            actor=principal.actor,
            idempotency_key=key,
            operation="register-config",
            resource_path="/configs",
            package=body.package,
            reason=body.reason,
        )
        return JSONResponse(status_code=status, content=content)

    @router.post("/configs/{config_id}/versions", status_code=201)
    def create_version(
        config_id: str,
        body: ConfigMutationRequest,
        principal: Annotated[Principal, Depends(authenticate)],
        key: Annotated[str, Depends(idempotency_key)],
    ) -> Any:
        if not principal.administrator:
            raise ManagedAPIError(403, "forbidden", "administrator access is required")
        status, content = routes.mutate(
            actor=principal.actor,
            idempotency_key=key,
            operation="create-config-version",
            resource_path=f"/configs/{config_id}/versions",
            package=body.package,
            reason=body.reason,
        )
        return JSONResponse(status_code=status, content=content)

    @router.get("/configs")
    def list_configs(_principal: Annotated[Principal, Depends(authenticate)]) -> Any:
        return routes.list_configs()

    @router.get("/configs/{config_id}")
    def get_config(config_id: str, _principal: Annotated[Principal, Depends(authenticate)]) -> Any:
        return routes.get_config(config_id)

    @router.get("/configs/{config_id}/versions")
    def list_versions(config_id: str, _principal: Annotated[Principal, Depends(authenticate)]) -> Any:
        return routes.list_versions(config_id)

    @router.get("/configs/{config_id}/versions/{registry_version}")
    def get_version(
        config_id: str, registry_version: int, _principal: Annotated[Principal, Depends(authenticate)]
    ) -> Any:
        return routes.get_version(config_id, registry_version)

    @router.post("/configs/{config_id}/versions/{registry_version}/validate")
    def validate(config_id: str, registry_version: int, _principal: Annotated[Principal, Depends(authenticate)]) -> Any:
        return routes.validate(config_id, registry_version)

    return router
