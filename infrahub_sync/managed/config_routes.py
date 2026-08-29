"""HTTP-only adapter for the shared configuration application service."""

from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends

from infrahub_sync.product_store import configs

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


def configuration_router(routes: ConfigurationRoutes, authenticate: Any) -> APIRouter:
    """Create the seven authenticated configuration resources."""
    router = APIRouter()

    @router.post("/configs", status_code=201)
    def register(body: ConfigMutationRequest, principal: Annotated[Principal, Depends(authenticate)]) -> Any:
        if not principal.administrator:
            raise ManagedAPIError(403, "forbidden", "administrator access is required")
        return routes.register(body.package)

    @router.post("/configs/{config_id}/versions", status_code=201)
    def create_version(
        config_id: str, body: ConfigMutationRequest, principal: Annotated[Principal, Depends(authenticate)]
    ) -> Any:
        if not principal.administrator:
            raise ManagedAPIError(403, "forbidden", "administrator access is required")
        return routes.create_version(config_id, body.package)

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
